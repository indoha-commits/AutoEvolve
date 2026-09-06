from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterator


def now() -> str:
    return datetime.now(UTC).isoformat()


class CampaignStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def init(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            conn.executescript("""
            CREATE TABLE IF NOT EXISTS campaigns(
              id TEXT PRIMARY KEY, status TEXT NOT NULL, buyer TEXT NOT NULL,
              narrative TEXT NOT NULL, objective TEXT NOT NULL, hook TEXT NOT NULL,
              package_json TEXT NOT NULL, created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS model_runs(
              id INTEGER PRIMARY KEY AUTOINCREMENT, campaign_id TEXT, stage TEXT NOT NULL,
              model TEXT NOT NULL, attempt INTEGER NOT NULL, status TEXT NOT NULL,
              latency_ms INTEGER NOT NULL, usage_json TEXT, error TEXT, created_at TEXT NOT NULL,
              actual_model TEXT
            );
            CREATE TABLE IF NOT EXISTS audit_events(
              id INTEGER PRIMARY KEY AUTOINCREMENT, campaign_id TEXT, event TEXT NOT NULL,
              payload_json TEXT NOT NULL, created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_campaign_created ON campaigns(created_at);
            """)
            columns = {row[1] for row in conn.execute("PRAGMA table_info(model_runs)")}
            if "actual_model" not in columns:
                conn.execute("ALTER TABLE model_runs ADD COLUMN actual_model TEXT")

    def save(self, package: dict, hook: str) -> None:
        with self.connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO campaigns VALUES(?,?,?,?,?,?,?,?)",
                (package["campaign_id"], package["status"], package["buyer"], package["narrative"],
                 package["objective"], hook, json.dumps(package), now()),
            )

    def recent(self, limit: int = 30) -> list[dict]:
        with self.connect() as conn:
            return [dict(row) for row in conn.execute("SELECT * FROM campaigns ORDER BY created_at DESC LIMIT ?", (limit,))]

    def usable_memory(self, limit: int = 30) -> list[dict]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT id,buyer,narrative,objective,hook,created_at FROM campaigns "
                "WHERE status='ready_for_media' ORDER BY created_at DESC LIMIT ?", (limit,),
            )
            return [dict(row) for row in rows]

    def failure_lessons(self, limit: int = 30) -> list[dict]:
        counts: dict[str, int] = {}
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT package_json FROM campaigns WHERE status='needs_founder_review' "
                "ORDER BY created_at DESC LIMIT ?", (limit,),
            )
            for row in rows:
                package = json.loads(row[0])
                for warning in package.get("quality", {}).get("warnings", []):
                    lesson = self._warning_class(warning)
                    if lesson is None:
                        continue
                    counts[lesson] = counts.get(lesson, 0) + 1
                for _term in package.get("quality", {}).get("forbidden_terms", []):
                    lesson = "forbidden or restricted product claims"
                    counts[lesson] = counts.get(lesson, 0) + 1
        return [{"avoid": lesson, "occurrences": count}
                for lesson, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:12]]

    @staticmethod
    def _warning_class(warning: str) -> str | None:
        value = warning.lower()
        if "ungrounded claim language" in value or "causal or outcome language" in value:
            return "causal or outcome claims without sentence-level evidence"
        if "image query" in value:
            return "abstract or non-searchable stock image queries"
        if "similarity" in value:
            return "repeated copy across slides or recent campaigns"
        if "trial cta" in value or "trial offer" in value:
            return "trial CTA outside a trial objective"
        if "generic" in value:
            return "generic SaaS copy and learn-more language"
        return None

    def get(self, campaign_id: str) -> dict | None:
        with self.connect() as conn:
            row = conn.execute("SELECT package_json FROM campaigns WHERE id=?", (campaign_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def model_run(self, campaign_id: str | None, stage: str, model: str, attempt: int,
                  status: str, latency_ms: int, usage: dict | None = None, error: str | None = None,
                  actual_model: str | None = None) -> None:
        with self.connect() as conn:
            conn.execute("INSERT INTO model_runs(campaign_id,stage,model,attempt,status,latency_ms,usage_json,error,created_at,actual_model) VALUES(?,?,?,?,?,?,?,?,?,?)",
                         (campaign_id, stage, model, attempt, status, latency_ms, json.dumps(usage or {}), error, now(), actual_model))

    def recent_model_runs(self, limit: int = 30) -> list[dict]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT campaign_id,stage,model,actual_model,attempt,status,latency_ms,usage_json,error,created_at "
                "FROM model_runs ORDER BY id DESC LIMIT ?", (limit,),
            )
            output = []
            for row in rows:
                item = dict(row)
                item["usage"] = json.loads(item.pop("usage_json") or "{}")
                output.append(item)
            return output

    def audit(self, event: str, payload: dict, campaign_id: str | None = None) -> None:
        with self.connect() as conn:
            conn.execute("INSERT INTO audit_events(campaign_id,event,payload_json,created_at) VALUES(?,?,?,?)",
                         (campaign_id, event, json.dumps(payload), now()))
