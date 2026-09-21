"""Offline CLI rendering and a real MCP stdio handshake, with generated inputs."""
import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

from PIL import Image
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def check_mcp(env: dict[str, str], cwd: Path) -> None:
    params = StdioServerParameters(command=sys.executable,
        args=['-m', 'xhs_video_mcp.server'], env=env, cwd=str(cwd))
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            assert {'get_video_config', 'list_bgm_files', 'create_video_from_xhs'} <= {t.name for t in tools.tools}
            result = await session.call_tool('get_video_config', {})
            assert not result.isError
            payload = json.loads(result.content[0].text)
            assert payload['resolution'] == '1080x1920'
            assert Path(payload['output_dir']).resolve() == Path(env['XHS_OUTPUT_DIR']).resolve()


def main() -> None:
    with tempfile.TemporaryDirectory(prefix='xhs-smoke-') as directory:
        root = Path(directory)
        env = dict(os.environ)
        env.pop('PYTHONPATH', None)
        for key, name in [('XHS_OUTPUT_DIR', 'output'), ('XHS_BGM_DIR', 'bgm'), ('XHS_TEMP_DIR', 'tmp')]:
            env[key] = str(root/name)
        paths = []
        for index, color in enumerate(['navy', 'coral']):
            path = root/f'image_{index:03d}.png'
            Image.new('RGB', (1080, 1920), color).save(path)
            paths.append(str(path))
        result = subprocess.run([sys.executable, '-m', 'xhs_video_mcp.local_workflow', *paths,
                                 '--duration', '1'], cwd=root, env=env, check=True,
                                text=True, capture_output=True, timeout=90)
        payload = json.loads(result.stdout)
        assert payload['ok'] and payload['images_count'] == 2
        probe = subprocess.run(['ffprobe', '-v', 'error', '-select_streams', 'v:0',
            '-show_entries', 'stream=codec_name,width,height:format=duration', '-of', 'json',
            payload['video_path']], check=True, text=True, capture_output=True, timeout=15)
        metadata = json.loads(probe.stdout)
        assert metadata['streams'][0]['width'] == 1080
        assert metadata['streams'][0]['height'] == 1920
        assert metadata['streams'][0]['codec_name'] == 'h264'
        assert abs(float(metadata['format']['duration']) - 1.5) < 0.05
        asyncio.run(asyncio.wait_for(check_mcp(env, root), timeout=30))
        print('PASS: offline CLI -> H.264 1080x1920; MCP initialize/list_tools/call_tool over stdio')


if __name__ == '__main__':
    main()
