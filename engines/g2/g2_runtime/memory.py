from __future__ import annotations

import json
import os
from pathlib import Path

from .fingerprint import hash_distance


class AssetMemory:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.data = {"version": 1, "assets": []}
        if self.path.is_file():
            self.data = json.loads(self.path.read_text(encoding="utf-8"))

    def duplicate(self, sha256: str, perceptual_hash: str, threshold: int = 5) -> dict | None:
        for item in self.data["assets"]:
            if item["sha256"] == sha256 or hash_distance(item["perceptual_hash"], perceptual_hash) <= threshold:
                return item
        return None

    def add(self, record: dict) -> None:
        if self.duplicate(record["sha256"], record["perceptual_hash"]):
            raise ValueError("asset is already present in media memory")
        self.data["assets"].append(record)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(json.dumps(self.data, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, self.path)
