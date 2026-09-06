from __future__ import annotations

import hashlib
from pathlib import Path

from PIL import Image, ImageStat


def file_sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def perceptual_hash(path: str | Path) -> str:
    with Image.open(path) as image:
        gray = image.convert("L").resize((9, 8), Image.Resampling.LANCZOS)
        pixels = list(gray.get_flattened_data()) if hasattr(gray, "get_flattened_data") else list(gray.getdata())
    bits = []
    for row in range(8):
        start = row * 9
        bits.extend(pixels[start + col] > pixels[start + col + 1] for col in range(8))
    value = sum(int(bit) << index for index, bit in enumerate(bits))
    return f"{value:016x}"


def hash_distance(left: str, right: str) -> int:
    return (int(left, 16) ^ int(right, 16)).bit_count()


def image_quality(path: str | Path) -> dict:
    with Image.open(path) as image:
        image.load()
        width, height = image.size
        variation = sum(ImageStat.Stat(image.convert("L").resize((128, 128))).var) ** 0.5
    return {
        "width": width,
        "height": height,
        "portrait": height > width,
        "variation": round(variation, 2),
        "ok": width >= 640 and height >= 800 and height > width and variation >= 18,
    }
