from __future__ import annotations

import sqlite3
from pathlib import Path


class Ledger:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path) as db:
            db.execute("""
                CREATE TABLE IF NOT EXISTS drafts (
                    idempotency_key TEXT PRIMARY KEY,
                    campaign_id TEXT NOT NULL,
                    platform TEXT NOT NULL,
                    post_id TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
            """)

    def existing(self, key: str) -> str | None:
        with sqlite3.connect(self.path) as db:
            row = db.execute("SELECT post_id FROM drafts WHERE idempotency_key = ?", (key,)).fetchone()
        return row[0] if row else None

    def record(self, key: str, campaign_id: str, platform: str, post_id: str) -> None:
        with sqlite3.connect(self.path) as db:
            db.execute(
                "INSERT INTO drafts(idempotency_key,campaign_id,platform,post_id) VALUES(?,?,?,?)",
                (key, campaign_id, platform, post_id),
            )

