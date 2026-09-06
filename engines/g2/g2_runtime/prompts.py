from __future__ import annotations

import hashlib

from .models import Slide


NEGATIVE_RULES = (
    "no text, no letters, no numbers, no logos, no watermark, no UI, "
    "no futuristic hologram, no neon glow, no distorted documents, no visible personal data"
)


def image_prompt(slide: Slide) -> str:
    composition = {
        "cover": "subject in the lower half with generous clean negative space above",
        "evidence": "clear documentary composition with room for a comparison overlay",
        "operational_complexity": "multiple real operational roles or objects arranged with visual separation",
        "failure_point": "one precise operational handoff shown clearly without exaggerated drama",
    }.get(slide.purpose, "clean editorial composition")
    prompt = (
        f"Realistic documentary editorial photograph for an East African freight operations campaign. "
        f"Scene: {slide.visual_concept.strip()} {composition}. Kigali or East African commercial logistics context "
        f"where relevant, natural light, authentic people and equipment, restrained deep navy cinematic color grade, "
        f"high detail, portrait 4:5 background plate. {NEGATIVE_RULES}."
    )
    return " ".join(prompt.split())


def prompt_sha256(prompt: str) -> str:
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()


def validate_background_prompt(prompt: str) -> None:
    lower = prompt.lower()
    for required in ("background plate", "no text", "no logos", "no watermark", "portrait 4:5"):
        if required not in lower:
            raise ValueError(f"image prompt missing policy clause: {required}")
    if "company dashboard" in lower or "render the headline" in lower:
        raise ValueError("image model cannot generate product UI or slide typography")
