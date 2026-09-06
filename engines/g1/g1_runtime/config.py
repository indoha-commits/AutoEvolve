from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    root: Path
    knowledge_dir: Path
    data_dir: Path
    output_dir: Path
    searxng_base_url: str
    omniroute_base_url: str
    coding_api_key: str
    reasoning_model: str
    writing_model: str
    fallback_model: str

    @classmethod
    def load(cls, root: str | Path | None = None) -> "Settings":
        root = Path(root or os.getenv("COMPANY_CORE_G1_ROOT") or Path.cwd()).resolve()
        return cls(
            root=root,
            knowledge_dir=Path(os.getenv("COMPANY_CORE_G1_KNOWLEDGE", root / "knowledge")),
            data_dir=Path(os.getenv("COMPANY_CORE_G1_DATA", root / "data")),
            output_dir=Path(os.getenv("COMPANY_CORE_G1_OUTPUTS", root / "outputs")),
            searxng_base_url=os.getenv("SEARXNG_BASE_URL", "http://127.0.0.1:8080").rstrip("/"),
            omniroute_base_url=os.getenv("OMNIROUTE_BASE_URL", "http://127.0.0.1:20128/v1").rstrip("/"),
            coding_api_key=os.getenv("CODING_API_KEY", ""),
            reasoning_model=os.getenv("G1_REASONING_MODEL", "auto/best-reasoning"),
            # Route labels are configurable, but final quality never trusts a
            # combo label or underlying provider model. Python closes the draft.
            writing_model=os.getenv("G1_WRITING_MODEL", "auto/best-chat"),
            fallback_model=os.getenv("G1_FALLBACK_MODEL", "auto/best-reasoning"),
        )

    def knowledge(self, filename: str):
        with open(self.knowledge_dir / filename, encoding="utf-8") as stream:
            return json.load(stream)
