from __future__ import annotations

import hashlib
import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps

from .ingest import package_sha256
from .models import AssetRecord, G1Campaign
from .policy import validate_asset

W, H = 1080, 1350
BG = "#020711"
PANEL = "#091426"
WHITE = "#F8FAFF"
MUTED = "#CAD4E3"
INDIGO = "#6268FF"
MAGENTA = "#D72C77"


def _font(size: int, bold: bool = False):
    names = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSansCondensed-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
    ]
    for name in names:
        if Path(name).is_file():
            return ImageFont.truetype(name, size=size)
    return ImageFont.load_default()


def _wrap(draw: ImageDraw.ImageDraw, text: str, font, width: int) -> list[str]:
    lines, current = [], ""
    for word in text.split():
        candidate = f"{current} {word}".strip()
        if draw.textbbox((0, 0), candidate, font=font)[2] <= width:
            current = candidate
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def _fit_headline(draw: ImageDraw.ImageDraw, text: str, width: int, max_lines: int = 3):
    for size in range(74, 39, -2):
        font = _font(size, True)
        lines = _wrap(draw, text, font, width)
        if len(lines) <= max_lines:
            return font, lines
    return _font(40, True), _wrap(draw, text, _font(40, True), width)[:max_lines]


def _cover_crop(image: Image.Image, box: tuple[int, int, int, int]) -> Image.Image:
    width, height = box[2] - box[0], box[3] - box[1]
    return ImageOps.fit(image.convert("RGB"), (width, height), method=Image.Resampling.LANCZOS)


def _placeholder(size: tuple[int, int], strategy: str) -> Image.Image:
    image = Image.new("RGB", size, "#101D31")
    draw = ImageDraw.Draw(image)
    for y in range(0, size[1], 34):
        shade = 24 + int(22 * y / max(size[1], 1))
        draw.rectangle((0, y, size[0], y + 34), fill=(4, 12 + shade // 3, shade))
    if strategy == "document_composite":
        for index, x in enumerate((80, 300, 520)):
            draw.rounded_rectangle((x, 70 + index * 25, x + 270, size[1] - 65), 14, fill="#E9E4DA", outline="#FFFFFF", width=3)
            draw.line((x + 35, 150 + index * 25, x + 225, 150 + index * 25), fill="#213049", width=5)
            draw.line((x + 35, 200 + index * 25, x + 190, 200 + index * 25), fill="#60708B", width=4)
    elif strategy == "route_diagram":
        points = [(80, size[1] - 110), (300, size[1] - 210), (540, 250), (size[0] - 80, 100)]
        draw.line(points, fill=INDIGO, width=10, joint="curve")
        for i, point in enumerate(points):
            draw.ellipse((point[0]-14, point[1]-14, point[0]+14, point[1]+14), fill=MAGENTA if i not in (0, len(points)-1) else INDIGO, outline=WHITE, width=2)
    elif strategy == "product_ui":
        draw.rounded_rectangle((60, 70, size[0]-60, size[1]-70), 24, fill="#071022", outline=INDIGO, width=4)
        draw.rectangle((95, 120, size[0]-95, 185), fill="#101D35")
        for y in (250, 340, 430):
            draw.rounded_rectangle((100, y, size[0]-100, y+55), 10, outline="#54617B", width=2)
    return image


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def render_carousel(
    campaign: G1Campaign,
    output_dir: str | Path,
    logo_path: str | Path,
    asset_root: str | Path | None = None,
    asset_records: list[AssetRecord] | None = None,
    proof_placeholders: bool = False,
) -> dict:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    logo = Image.open(logo_path).convert("RGBA")
    logo.thumbnail((310, 118), Image.Resampling.LANCZOS)
    records = {record.slide_number: record for record in (asset_records or [])}
    rendered = []

    for slide in campaign.slides:
        canvas = Image.new("RGB", (W, H), BG)
        draw = ImageDraw.Draw(canvas)
        slide_logo = logo.copy()
        canvas.paste(slide_logo, (52, 45), slide_logo.getchannel("A"))
        font, lines = _fit_headline(draw, slide.headline, 950)
        y = 190
        for line in lines:
            draw.text((52, y), line, font=font, fill=WHITE)
            y += font.size + 4
        draw.line((52, y + 18, 365, y + 18), fill=INDIGO, width=4)
        body_font = _font(29)
        for line in _wrap(draw, slide.body, body_font, 950)[:3]:
            draw.text((52, y + 55), line, font=body_font, fill=MUTED)
            y += 39

        media_box = (40, max(575, y + 95), 1040, 1240)
        record = records.get(slide.number)
        if record and asset_root:
            media = _cover_crop(Image.open(validate_asset(record, Path(asset_root), campaign)), media_box)
        elif slide.asset_strategy == "branded_end_card":
            media = _placeholder((media_box[2]-media_box[0], media_box[3]-media_box[1]), "route_diagram")
        elif proof_placeholders:
            media = _placeholder((media_box[2]-media_box[0], media_box[3]-media_box[1]), slide.asset_strategy)
        else:
            raise ValueError(f"slide {slide.number} requires a validated asset (or --proof-placeholders)")
        media = ImageEnhance.Contrast(media).enhance(1.08).filter(ImageFilter.GaussianBlur(0.15))
        mask = Image.new("L", media.size, 0)
        ImageDraw.Draw(mask).rounded_rectangle((0, 0, media.width, media.height), 26, fill=255)
        canvas.paste(media, media_box[:2], mask)
        draw.rounded_rectangle(media_box, 26, outline="#263651", width=3)
        draw.line((90, 1275, 890, 1275), fill=INDIGO, width=7)
        for x in (250, 545, 835):
            draw.ellipse((x-12, 1263, x+12, 1287), fill=MAGENTA, outline=WHITE, width=2)
        draw.text((930, 1258), f"{slide.number:02d}", font=_font(23, True), fill=WHITE)
        path = output / f"slide_{slide.number:02d}.png"
        canvas.save(path, "PNG", optimize=True)
        rendered.append({"slide": slide.number, "path": path.name, "sha256": _sha(path), "width": W, "height": H})

    manifest = {
        "media_type": "carousel",
        "campaign_id": campaign.campaign_id,
        "source_package_sha256": package_sha256(campaign),
        "proof_only": proof_placeholders,
        "publish_allowed": False,
        "files": rendered,
        "status": "proof_rendered" if proof_placeholders else "ready_for_review",
    }
    manifest_path = output / "render_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest
