from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).parent.parent
WORKSPACE_ROOT = Path(os.getenv("COMPANY_CORE_WORKSPACE_ROOT", ROOT)).expanduser().resolve()


def _path(name: str, default: str) -> Path:
    return Path(os.getenv(name, default)).expanduser().resolve()


@dataclass(frozen=True)
class MarketingConfig:
    g1_root: Path
    g1_bin: Path
    g1_env_file: Path | None
    g2_root: Path
    g2_bin: Path
    g2_env_file: Path | None
    g2_showcase: Path
    g2_outro: Path
    g3_root: Path
    g3_bin: Path
    g3_env_file: Path | None
    voices: tuple[str, ...]
    speed: float
    media_deadline: float
    media_limit: int
    stage_timeout: int

    @classmethod
    def load(cls) -> "MarketingConfig":
        g1_root = _path("MARKETING_G1_ROOT", str(WORKSPACE_ROOT / "engines/g1"))
        g2_root = _path("MARKETING_G2_ROOT", str(WORKSPACE_ROOT / "engines/g2"))
        g3_root = _path("MARKETING_G3_ROOT", str(WORKSPACE_ROOT / "engines/g3"))
        voice_text = os.getenv(
            "MARKETING_VARIANT_VOICES",
            "en-US-AndrewNeural,en-US-EmmaNeural,en-US-BrianNeural,en-GB-SoniaNeural",
        )
        voices = tuple(value.strip() for value in voice_text.split(",") if value.strip())[:6]
        if not voices:
            raise RuntimeError("MARKETING_VARIANT_VOICES must contain at least one voice")
        return cls(
            g1_root=g1_root,
            g1_bin=_path("MARKETING_G1_BIN", str(g1_root / ".venv/bin/company-core-g1")),
            g1_env_file=_optional_path("MARKETING_G1_ENV_FILE", g1_root / ".env"),
            g2_root=g2_root,
            g2_bin=_path("MARKETING_G2_BIN", str(g2_root / ".venv/bin/company-core-g2")),
            g2_env_file=_optional_path("MARKETING_G2_ENV_FILE", g2_root / ".env"),
            g2_showcase=_path(
                "MARKETING_G2_SHOWCASE",
                str(g2_root / "assets/references/replace-with-your-showcase.mp4"),
            ),
            g2_outro=_path(
                "MARKETING_G2_OUTRO",
                str(g2_root / "assets/references/replace-with-your-outro.png"),
            ),
            g3_root=g3_root,
            g3_bin=_path("MARKETING_G3_BIN", str(g3_root / ".venv/bin/company-core-g3")),
            g3_env_file=_optional_path("MARKETING_G3_ENV_FILE", g3_root / "config/g3.env"),
            voices=voices,
            speed=float(os.getenv("MARKETING_VOICE_SPEED", "1.04")),
            media_deadline=float(os.getenv("MARKETING_MEDIA_DEADLINE", "18")),
            media_limit=int(os.getenv("MARKETING_MEDIA_LIMIT", "8")),
            stage_timeout=int(os.getenv("MARKETING_STAGE_TIMEOUT", "900")),
        )

    def diagnostics(self) -> dict:
        paths = {
            "g1_bin": self.g1_bin,
            "g2_bin": self.g2_bin,
            "g2_showcase": self.g2_showcase,
            "g2_outro": self.g2_outro,
            "g3_bin": self.g3_bin,
        }
        return {
            name: {
                "ok": path.is_file() and (not name.endswith("_bin") or os.access(path, os.X_OK)),
                "path": str(path),
                "executable": os.access(path, os.X_OK) if name.endswith("_bin") and path.exists() else None,
            }
            for name, path in paths.items()
        }


def _optional_path(name: str, default: Path) -> Path | None:
    raw = os.getenv(name)
    if raw is not None and not raw.strip():
        return None
    return Path(raw).expanduser().resolve() if raw else default.resolve()
