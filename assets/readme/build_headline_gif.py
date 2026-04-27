from __future__ import annotations

from pathlib import Path

from PIL import Image


ROOT = Path(__file__).resolve().parent
FRAME_ORDER = [
    "01-korestack.png",
    "02-koreagent.png",
    "03-koreconversation.png",
    "04-koredata.png",
    "05-koredocs-tabs.png",
    "06-koredocs-doc.png",
    "07-koredocs-sheet.png",
    "08-koredocs-diag.png",
    "06-korecomms.png",
]
OUTPUT = ROOT / "kore-suite-headline.gif"
POSTER = ROOT / "kore-suite-headline-poster.png"


def _prepare(image: Image.Image) -> Image.Image:
    return image.convert("P", palette=Image.Palette.ADAPTIVE)


def build() -> None:
    frames = []
    for filename in FRAME_ORDER:
        image_path = ROOT / filename
        if not image_path.exists():
            raise FileNotFoundError(f"Missing frame: {image_path}")
        with Image.open(image_path) as source:
            frames.append(_prepare(source))

    if not frames:
        raise RuntimeError("No frames available")

    frames[0].save(POSTER)
    frames[0].save(
        OUTPUT,
        save_all=True,
        append_images=frames[1:],
        duration=2000,
        loop=0,
        optimize=True,
        disposal=2,
    )


if __name__ == "__main__":
    build()