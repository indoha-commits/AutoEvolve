from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from .platforms import PlatformName


class PlatformScript(BaseModel):
    """G1-owned editorial adaptation consumed by G2 without rewriting its claims."""

    model_config = ConfigDict(extra="forbid")

    campaign_id: str
    platform: PlatformName
    purpose: str
    hook: str
    scene_narration: dict[str, str] = Field(min_length=5)


def load_platform_script(path: str | Path, campaign_id: str, platform: PlatformName) -> PlatformScript:
    script = PlatformScript.model_validate(json.loads(Path(path).read_text(encoding="utf-8")))
    if script.campaign_id != campaign_id:
        raise ValueError("platform script campaign does not match the G1 package")
    if script.platform != platform:
        raise ValueError("platform script does not match the requested output profile")
    return script
