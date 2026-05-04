from __future__ import annotations

from pathlib import Path

from PIL import Image


ROOT = Path(__file__).resolve().parent
FRAME_ORDER = [
    "01-korestack.png",
    "02-koreagent.png",
    "03-korechat-list.png",
    "04-korechat-detail.png",
    "05-koredata-home.png",
    "06-koredata-feeds.png",
    "07-koredata-library.png",
    "08-koredata-book.png",
    "09-koredata-reference.png",
    "10-koredata-article.png",
    "11-koredata-rag.png",
    "12-koredocs-file.png",
    "13-koredocs-doc.png",
    "14-koredocs-sheet.png",
    "15-korecode.png",
    "16-korecomms-home.png",
    "17-korecomms-compose.png",
    "18-korecomms-connections.png",
    "19-korecomms-activity.png",
]
OUTPUT       = ROOT / "kore-suite-headline.gif"
POSTER       = ROOT / "kore-suite-headline-poster.png"
DWELL_MS     = 3140      # time each full image is shown
FADE_STEPS   = 8         # number of blend frames in the crossfade
FADE_STEP_MS = 40        # ms per blend frame (~25 fps)


def _to_rgb(img: Image.Image) -> Image.Image:
    return img.convert("RGB")


def _to_palette(img: Image.Image) -> Image.Image:
    return img.convert("P", palette=Image.Palette.ADAPTIVE, colors=256)


def _blend_frames(a: Image.Image, b: Image.Image, steps: int) -> list[Image.Image]:
    """Return `steps` crossfade frames blending from a to b (exclusive of endpoints)."""
    out = []
    for i in range(1, steps + 1):
        alpha = i / (steps + 1)
        blended = Image.blend(a, b, alpha)
        out.append(_to_palette(blended))
    return out


def build() -> None:
    # Load all source images as RGB (consistent size: use first image dimensions)
    sources: list[Image.Image] = []
    for filename in FRAME_ORDER:
        p = ROOT / filename
        if not p.exists():
            raise FileNotFoundError(f"Missing frame: {p}")
        sources.append(_to_rgb(Image.open(p)))

    if not sources:
        raise RuntimeError("No frames available")

    # Resize all to match first image size (they should already match)
    target_size = sources[0].size
    sources = [img.resize(target_size, Image.LANCZOS) if img.size != target_size else img
               for img in sources]

    # Build frame list: dwell frame + fade transition to next
    frames: list[Image.Image] = []
    durations: list[int] = []

    for i, src in enumerate(sources):
        # Dwell on this image
        frames.append(_to_palette(src))
        durations.append(DWELL_MS)

        # Crossfade to next (wrap around to first at the end)
        nxt = sources[(i + 1) % len(sources)]
        for blend in _blend_frames(src, nxt, FADE_STEPS):
            frames.append(blend)
            durations.append(FADE_STEP_MS)

    # Save poster (first full frame as RGB)
    sources[0].save(POSTER)

    # Save GIF
    frames[0].save(
        OUTPUT,
        save_all=True,
        append_images=frames[1:],
        duration=durations,
        loop=0,
        optimize=False,
        disposal=2,
    )
    print(f"Saved {OUTPUT}  ({len(frames)} frames, {len(sources)} slides)")


if __name__ == "__main__":
    build()