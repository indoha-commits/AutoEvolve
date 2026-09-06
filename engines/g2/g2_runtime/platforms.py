from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict


PlatformName = Literal["youtube", "shorts", "tiktok"]


class PlatformProfile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: PlatformName
    width: int
    height: int
    fps: int
    duration_seconds: int
    narrative_goal: str
    opening_rule: str
    pacing: str
    subtitle_preset: str
    outro_seconds: float

    @property
    def resolution(self) -> str:
        return f"{self.width}x{self.height}"


PROFILES: dict[PlatformName, PlatformProfile] = {
    "youtube": PlatformProfile(
        name="youtube",
        width=1920,
        height=1080,
        fps=30,
        duration_seconds=120,
        narrative_goal="explain the operational problem with enough context to build trust",
        opening_rule="establish the stakes before compressing the argument",
        pacing="measured; 7-14 second visual beats",
        subtitle_preset="youtube_lower_third",
        outro_seconds=5.0,
    ),
    "shorts": PlatformProfile(
        name="shorts",
        width=1080,
        height=1920,
        fps=30,
        duration_seconds=45,
        narrative_goal="deliver one complete insight with a strong first-second hook",
        opening_rule="open with tension or contrast; no introduction",
        pacing="compressed; 3-7 second visual beats",
        subtitle_preset="vertical_safe",
        outro_seconds=2.5,
    ),
    "tiktok": PlatformProfile(
        name="tiktok",
        width=1080,
        height=1920,
        fps=30,
        duration_seconds=30,
        narrative_goal="feel native and conversational while revealing one useful operational truth",
        opening_rule="start mid-thought with a human observation, then create an open loop",
        pacing="conversational; fast visual changes without sounding like an advertisement",
        subtitle_preset="tiktok_center_safe",
        outro_seconds=1.5,
    ),
}


def get_profile(name: PlatformName) -> PlatformProfile:
    return PROFILES[name]
