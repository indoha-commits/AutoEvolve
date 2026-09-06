from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageOps

from .storyboard import MixedScene

VIDEO_SUFFIXES = {".mp4", ".mov", ".m4v", ".webm"}


def _background(asset: str | Path | None, size: tuple[int, int]) -> Image.Image:
    """Create a restrained full-frame visual; typography is added only as subtitles."""
    width, height = size
    if not asset:
        raise ValueError("non-outro scene requires explicit approved media; visual fallback is disabled")
    image = Image.open(asset).convert("RGB")
    image = ImageOps.fit(image, size, Image.Resampling.LANCZOS, centering=(0.5, 0.48))
    image = ImageEnhance.Color(image).enhance(0.82)
    image = ImageEnhance.Contrast(image).enhance(1.04)

    # Only the bottom subtitle-safe region is shaded. The image remains the scene.
    shade = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(shade)
    shade_start = int(height * 0.65)
    for y in range(shade_start, height):
        progress = (y - shade_start) / (height - shade_start)
        draw.line((0, y, width, y), fill=(2, 7, 17, int(20 + 150 * progress)))
    return Image.alpha_composite(image.convert("RGBA"), shade).convert("RGB")


def _premade_outro(path: str | Path, size: tuple[int, int]) -> Image.Image:
    source = Image.open(path).convert("RGB")
    backdrop = ImageOps.fit(source, size, Image.Resampling.LANCZOS).filter(ImageFilter.GaussianBlur(28))
    backdrop = ImageEnhance.Brightness(backdrop).enhance(0.38)
    foreground = source.copy()
    foreground.thumbnail(size, Image.Resampling.LANCZOS)
    x = (size[0] - foreground.width) // 2
    y = (size[1] - foreground.height) // 2
    backdrop.paste(foreground, (x, y))
    return backdrop


def render_layers(
    scene: MixedScene,
    background_asset: str | Path | None,
    outro_path: str | Path,
    output: str | Path,
    review: bool,
    size: tuple[int, int] = (1080, 1920),
    showcase_path: str | Path | None = None,
) -> dict:
    """Render full-frame media with no persistent logo; use a premade outro unchanged."""
    root = Path(output)
    root.mkdir(parents=True, exist_ok=True)
    effective_asset = showcase_path if scene.purpose == "control_system" else background_asset
    if scene.purpose == "control_system":
        if not showcase_path:
            raise ValueError("control-system scene requires a configured showcase asset")
    is_video = bool(effective_asset and Path(effective_asset).suffix.lower() in VIDEO_SUFFIXES)
    if is_video:
        # Preserve moving media for FFmpeg instead of decoding it as a still.
        bg = None
        background_path = Path(effective_asset)
    elif scene.purpose == "outcome_cta":
        bg = _premade_outro(outro_path, size)
    else:
        bg = _background(effective_asset, size)
    # Preserve the resized source without another lossy generation. The scene
    # encoder uses inter-frame compression, so a lossless still does not require
    # duplicating PNG data for every frame.
    if bg is not None:
        background_path = root / f"scene_{scene.number:02d}_background.png"
        bg.save(background_path, "PNG", optimize=True)

    layer = Image.new("RGBA", size, (0, 0, 0, 0))

    if review:
        draw = ImageDraw.Draw(layer)
        width, _ = size
        draw.rounded_rectangle((width - 274, 55, width - 56, 105), 14, fill=(2, 7, 17, 160))
        draw.text((width - 238, 69), "REVIEW CUT", fill=(255, 255, 255, 210))

    overlay_path = root / f"scene_{scene.number:02d}_overlay.png"
    layer.save(overlay_path, "PNG", optimize=True)
    preview_path = None
    if bg is not None:
        preview = Image.alpha_composite(bg.convert("RGBA"), layer)
        preview_path = root / f"scene_{scene.number:02d}_preview.png"
        preview.save(preview_path, "PNG", optimize=True)
    return {
        "background": str(background_path),
        "background_kind": "video" if is_video else "image",
        "overlay": str(overlay_path),
        "preview": str(preview_path) if preview_path else None,
    }
