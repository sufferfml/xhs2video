import asyncio
from contextlib import asynccontextmanager
from io import BytesIO
from pathlib import Path
import subprocess

import httpx
import pytest
from PIL import Image

from xhs_video_mcp.config import Config, config
from xhs_video_mcp.safety import public_url, validate_url
from xhs_video_mcp.video_maker import _resolve_image_durations, _run_ffmpeg, create_video
from xhs_video_mcp.video_plan_workflow import _resolve_download_dir as plan_dir
from xhs_video_mcp.video_render_workflow import _resolve_download_dir as render_dir, _resolve_selected_images
from xhs_video_mcp.xhs_parser import XHSParser, XHSPost
from xhs_video_mcp import image_workflow


@pytest.fixture(autouse=True)
def private_dirs(tmp_path, monkeypatch):
    for name in ('bgm_dir', 'output_dir', 'temp_dir'):
        monkeypatch.setattr(config, name, tmp_path / name)


@pytest.mark.parametrize('url', [
    'http://www.xiaohongshu.com/explore/demo',
    'https://xiaohongshu.com.evil.example/explore/demo',
    'https://evil.example/?url=https://www.xiaohongshu.com/explore/demo',
    'https://user@www.xiaohongshu.com/explore/demo',
    'https://127.0.0.1/explore/demo', 'file:///etc/passwd',
    'https://www.xiaohongshu.com:444/explore/demo',
    'https://www.xiaohongshu.com/\nexplore/demo',
])
def test_untrusted_navigation_is_rejected(url):
    with pytest.raises(ValueError):
        validate_url(url, post=True)


def test_valid_hosts_and_signature_redaction():
    url = 'https://www.xiaohongshu.com/explore/demo?xsec_token=EXAMPLE_SIGNATURE#private'
    assert validate_url(url, post=True) == url
    assert public_url(url) == 'https://www.xiaohongshu.com/explore/demo'
    assert validate_url('https://sns-img-bd.xhscdn.com/demo')
    with pytest.raises(ValueError):
        validate_url('https://xhscdn.com.evil.example/image.png')


def test_config_has_no_import_time_write(tmp_path):
    target = tmp_path / 'not-created'
    Config(bgm_dir=target/'bgm', output_dir=target/'out', temp_dir=target/'tmp')
    assert not target.exists()


@pytest.mark.parametrize('resolve', [plan_dir, render_dir])
@pytest.mark.parametrize('selection', [None, Path('latest')])
def test_ambiguous_jobs_rejected(resolve, selection):
    with pytest.raises(ValueError, match='explicit'):
        resolve(selection)


def test_plan_cannot_select_file_outside_job(tmp_path):
    job = tmp_path / 'job'; job.mkdir()
    inside = job / 'image_000.png'; Image.new('RGB', (20, 20)).save(inside)
    outside = tmp_path / 'image_001.png'; Image.new('RGB', (20, 20)).save(outside)
    with pytest.raises(ValueError, match='inside'):
        _resolve_selected_images({'selected_image_paths': [str(outside)]}, job)
    (job / 'image_001.png').symlink_to(outside)
    with pytest.raises(ValueError, match='inside'):
        _resolve_selected_images({'selected_indices': [2]}, job)


@pytest.mark.parametrize('duration', [0, -1, float('nan'), float('inf'), 13])
def test_invalid_durations_rejected(duration):
    with pytest.raises(ValueError):
        _resolve_image_durations(1, duration, None, 3)


def test_output_filename_cannot_escape_directory(tmp_path):
    image = tmp_path/'image.png'; Image.new('RGB', (20, 20)).save(image)
    with pytest.raises(ValueError, match='output_filename'):
        create_video([image], output_filename='../escape')


def test_ffmpeg_has_deadline(monkeypatch):
    def timeout(*args, **kwargs):
        assert kwargs['timeout'] == config.ffmpeg_timeout
        raise subprocess.TimeoutExpired(args[0], kwargs['timeout'])
    monkeypatch.setattr(subprocess, 'run', timeout)
    with pytest.raises(RuntimeError, match='time limit'):
        _run_ffmpeg(['ffmpeg'])


async def test_invalid_url_rejected_before_browser_start(monkeypatch):
    parser = XHSParser()
    async def unexpected_browser():
        pytest.fail('Browser must not launch for invalid URLs')
    monkeypatch.setattr(parser, '_init_browser', unexpected_browser)
    with pytest.raises(ValueError):
        await parser.parse('https://localhost/private')


def fake_http(monkeypatch, response):
    class Client:
        def __init__(self, **kwargs):
            assert kwargs['follow_redirects'] is False
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        @asynccontextmanager
        async def stream(self, method, url):
            yield response
    monkeypatch.setattr(httpx, 'AsyncClient', Client)


async def test_oversized_download_rejected_before_write(tmp_path, monkeypatch):
    parser = XHSParser(); parser.download_dir = tmp_path
    monkeypatch.setattr(config, 'max_image_bytes', 4)
    response = httpx.Response(200, headers={'content-type': 'image/png'}, content=b'12345',
                              request=httpx.Request('GET', 'https://ci.xiaohongshu.com/demo'))
    fake_http(monkeypatch, response)
    with pytest.raises(ValueError, match='byte limit'):
        await parser._download_images(['https://ci.xiaohongshu.com/demo'], 'demo')
    assert not list(tmp_path.iterdir())


async def test_redirect_not_followed_and_no_signed_url_in_logs(tmp_path, monkeypatch, caplog, capsys):
    parser = XHSParser(); parser.download_dir = tmp_path
    url = 'https://ci.xiaohongshu.com/demo?xsec_token=EXAMPLE_SIGNATURE'
    response = httpx.Response(302, headers={'location': 'https://localhost/private'},
                             request=httpx.Request('GET', url))
    fake_http(monkeypatch, response)
    assert await parser._download_images([url], 'demo') == []
    assert 'EXAMPLE_SIGNATURE' not in caplog.text
    assert capsys.readouterr().out == ''


async def test_generated_image_download(tmp_path, monkeypatch):
    parser = XHSParser(); parser.download_dir = tmp_path
    data = BytesIO(); Image.new('RGB', (800, 800)).save(data, format='PNG')
    response = httpx.Response(200, headers={'content-type': 'image/png'}, content=data.getvalue(),
                             request=httpx.Request('GET', 'https://ci.xiaohongshu.com/demo'))
    fake_http(monkeypatch, response)
    images = await parser._download_images(['https://ci.xiaohongshu.com/demo'], 'demo')
    assert len(images) == 1
    with Image.open(images[0]) as image:
        assert image.size == (800, 800)


async def test_cleanup_only_own_job(tmp_path, monkeypatch):
    own = tmp_path/'own'; own.mkdir()
    other = tmp_path/'other'; other.mkdir()
    image = own/'image_000.png'; Image.new('RGB', (20, 20)).save(image)
    marker = other/'keep.txt'; marker.write_text('another task')
    async def parse(_url):
        return XHSPost('samepost', 'demo', ['https://ci.xiaohongshu.com/demo'], [image], own)
    monkeypatch.setattr(image_workflow, 'parse_xhs_url', parse)
    result = await image_workflow.run_workflow('https://www.xiaohongshu.com/explore/demo')
    assert result['ok']
    assert not own.exists()
    assert marker.exists()
    assert Path(result['image_paths'][0]).exists()
