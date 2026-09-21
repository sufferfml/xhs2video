"""Generate original, offline sample cards; no downloaded media or logos."""
import argparse
from pathlib import Path
from PIL import Image, ImageDraw


def generate(output: Path) -> list[Path]:
    output.mkdir(parents=True, exist_ok=True)
    paths = []
    for index, color in enumerate(((35, 65, 90), (185, 65, 70))):
        image = Image.new("RGB", (1080, 1920), color)
        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle((100, 300, 980, 1620), radius=60, outline="white", width=12)
        draw.ellipse((300, 600, 780, 1080), fill=(240, 210, 100))
        draw.text((420, 1200), f"DEMO {index + 1}", fill="white", font_size=56)
        path = output / f"image_{index:03d}.png"
        image.save(path)
        paths.append(path)
    return paths


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=Path("output/demo"))
    args = parser.parse_args()
    generate(args.output_dir)
