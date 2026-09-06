from __future__ import annotations
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "company_ops.db"

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db() -> None:
    with connect() as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS coding_tasks (
            id TEXT PRIMARY KEY,
            workspace TEXT NOT NULL,
            request TEXT NOT NULL,
            source TEXT NOT NULL DEFAULT 'founder',
            category TEXT,
            model TEXT,
            status TEXT NOT NULL,
            risk TEXT NOT NULL DEFAULT 'low',
            worktree_path TEXT,
            branch TEXT,
            base_commit TEXT,
            result_json TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS incidents (
            id TEXT PRIMARY KEY,
            service TEXT NOT NULL,
            severity TEXT NOT NULL,
            status TEXT NOT NULL,
            trigger TEXT NOT NULL,
            evidence_json TEXT,
            repair_task_id TEXT,
            resolution_json TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        """)

def upsert_task(task: dict[str, Any]) -> None:
    init_db()
    now = now_iso()
    payload = json.dumps(task, ensure_ascii=False)
    with connect() as db:
        db.execute("""
        INSERT INTO coding_tasks
        (id, workspace, request, source, category, model, status, risk,
         worktree_path, branch, base_commit, result_json, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
          category=excluded.category,
          model=excluded.model,
          status=excluded.status,
          risk=excluded.risk,
          worktree_path=excluded.worktree_path,
          branch=excluded.branch,
          base_commit=excluded.base_commit,
          result_json=excluded.result_json,
          updated_at=excluded.updated_at
        """, (
            task["id"], task["workspace"], task["request"],
            task.get("source","founder"), task.get("category"),
            task.get("model"), task["status"], task.get("risk","low"),
            task.get("worktree_path"), task.get("branch"),
            task.get("base_commit"), payload,
            task.get("created_at", now), now
        ))

def get_task(task_id: str) -> dict[str, Any] | None:
    init_db()
    with connect() as db:
        row = db.execute("SELECT result_json FROM coding_tasks WHERE id=?", (task_id,)).fetchone()
    return json.loads(row["result_json"]) if row else None

def list_tasks(limit: int = 50) -> list[dict[str, Any]]:
    init_db()
    with connect() as db:
        rows = db.execute(
            "SELECT result_json FROM coding_tasks ORDER BY created_at DESC LIMIT ?",
            (limit,)
        ).fetchall()
    return [json.loads(r["result_json"]) for r in rows]

def upsert_incident(incident: dict[str, Any]) -> None:
    init_db()
    now = now_iso()
    with connect() as db:
        db.execute("""
        INSERT INTO incidents
        (id, service, severity, status, trigger, evidence_json,
         repair_task_id, resolution_json, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
          status=excluded.status,
          evidence_json=excluded.evidence_json,
          repair_task_id=excluded.repair_task_id,
          resolution_json=excluded.resolution_json,
          updated_at=excluded.updated_at
        """, (
            incident["id"], incident["service"], incident["severity"],
            incident["status"], incident["trigger"],
            json.dumps(incident.get("evidence",{}), ensure_ascii=False),
            incident.get("repair_task_id"),
            json.dumps(incident.get("resolution",{}), ensure_ascii=False),
            incident.get("created_at", now), now
        ))

def get_incident(incident_id: str) -> dict[str, Any] | None:
    init_db()
    with connect() as db:
        row = db.execute("SELECT * FROM incidents WHERE id=?", (incident_id,)).fetchone()
    if not row:
        return None
    return {
        "id": row["id"], "service": row["service"], "severity": row["severity"],
        "status": row["status"], "trigger": row["trigger"],
        "evidence": json.loads(row["evidence_json"] or "{}"),
        "repair_task_id": row["repair_task_id"],
        "resolution": json.loads(row["resolution_json"] or "{}"),
        "created_at": row["created_at"], "updated_at": row["updated_at"],
    }

def list_incidents(limit: int = 50) -> list[dict[str, Any]]:
    init_db()
    with connect() as db:
        rows = db.execute("SELECT id FROM incidents ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
    return [get_incident(r["id"]) for r in rows]
