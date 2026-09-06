from __future__ import annotations

import json
import sqlite3
import uuid
from typing import Any

from core.state import DB_PATH, now_iso


TERMINAL_CAMPAIGN_STATES = {"variants_ready", "drafted", "failed", "needs_campaign_review"}


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DB_PATH, timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA foreign_keys=ON")
    return connection


def init_marketing_db() -> None:
    with connect() as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS marketing_campaigns (
                id TEXT PRIMARY KEY,
                project_id INTEGER NOT NULL,
                task_id INTEGER NOT NULL,
                request TEXT NOT NULL,
                objective TEXT NOT NULL,
                buyer TEXT NOT NULL,
                topic TEXT NOT NULL,
                social_platforms_json TEXT NOT NULL,
                video_platform TEXT NOT NULL,
                voice_mode TEXT NOT NULL DEFAULT 'tts',
                voice_transcript TEXT,
                voice_handoff_path TEXT,
                voice_recording_path TEXT,
                status TEXT NOT NULL,
                current_stage TEXT NOT NULL,
                g1_campaign_id TEXT,
                g1_output_path TEXT,
                platform_script_path TEXT,
                media_search_path TEXT,
                asset_root TEXT,
                asset_manifest_path TEXT,
                selected_variant_id TEXT,
                g3_handoff_path TEXT,
                g3_status TEXT NOT NULL DEFAULT 'not_requested',
                g3_result_json TEXT,
                error TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(project_id) REFERENCES projects(id),
                FOREIGN KEY(task_id) REFERENCES tasks(id)
            );

            CREATE TABLE IF NOT EXISTS marketing_variants (
                id TEXT PRIMARY KEY,
                campaign_id TEXT NOT NULL,
                platform TEXT NOT NULL,
                voice TEXT NOT NULL,
                speed REAL NOT NULL,
                status TEXT NOT NULL,
                video_path TEXT,
                manifest_path TEXT,
                sha256 TEXT,
                duration_seconds REAL,
                error TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(campaign_id) REFERENCES marketing_campaigns(id)
            );

            CREATE TABLE IF NOT EXISTS marketing_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                campaign_id TEXT NOT NULL,
                event TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(campaign_id) REFERENCES marketing_campaigns(id)
            );

            CREATE TABLE IF NOT EXISTS marketing_manual_posts (
                id TEXT PRIMARY KEY,
                project_id INTEGER NOT NULL,
                campaign_id TEXT,
                platform TEXT NOT NULL,
                post_id TEXT NOT NULL UNIQUE,
                title TEXT,
                creative TEXT,
                hook TEXT,
                offer TEXT,
                cta TEXT,
                destination_url TEXT NOT NULL,
                tracked_url TEXT NOT NULL,
                source_detail TEXT,
                asset_dir TEXT,
                assets_json TEXT NOT NULL DEFAULT '[]',
                metadata_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(project_id) REFERENCES projects(id),
                FOREIGN KEY(campaign_id) REFERENCES marketing_campaigns(id)
            );
            """
        )
        columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(marketing_campaigns)").fetchall()
        }
        if "g1_approved_at" not in columns:
            connection.execute("ALTER TABLE marketing_campaigns ADD COLUMN g1_approved_at TEXT")
        if "g1_revision_instruction" not in columns:
            connection.execute("ALTER TABLE marketing_campaigns ADD COLUMN g1_revision_instruction TEXT")
        if "voice_mode" not in columns:
            connection.execute("ALTER TABLE marketing_campaigns ADD COLUMN voice_mode TEXT NOT NULL DEFAULT 'tts'")
        if "voice_transcript" not in columns:
            connection.execute("ALTER TABLE marketing_campaigns ADD COLUMN voice_transcript TEXT")
        if "voice_handoff_path" not in columns:
            connection.execute("ALTER TABLE marketing_campaigns ADD COLUMN voice_handoff_path TEXT")
        if "voice_recording_path" not in columns:
            connection.execute("ALTER TABLE marketing_campaigns ADD COLUMN voice_recording_path TEXT")


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _clean(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _decode_campaign(row: sqlite3.Row | None) -> dict | None:
    if row is None:
        return None
    value = dict(row)
    value["social_platforms"] = json.loads(value.pop("social_platforms_json"))
    value["g3_result"] = json.loads(value["g3_result_json"]) if value.get("g3_result_json") else None
    value.pop("g3_result_json", None)
    return value


def _decode_manual_post(row: sqlite3.Row | None) -> dict | None:
    if row is None:
        return None
    value = dict(row)
    value["assets"] = json.loads(value.pop("assets_json") or "[]")
    value["metadata"] = json.loads(value.pop("metadata_json") or "{}")
    return value


def create_campaign(
    *,
    project_id: int,
    task_id: int,
    request: str,
    objective: str,
    buyer: str,
    topic: str,
    social_platforms: list[str],
    video_platform: str,
    voice_mode: str = "tts",
    voice_transcript: str | None = None,
) -> dict:
    campaign_id = _id("mkt")
    timestamp = now_iso()
    with connect() as connection:
        connection.execute(
            """
            INSERT INTO marketing_campaigns (
                id, project_id, task_id, request, objective, buyer, topic,
                social_platforms_json, video_platform, voice_mode, voice_transcript, status, current_stage,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'queued', 'growth_intake', ?, ?)
            """,
            (
                campaign_id, project_id, task_id, request, objective, buyer, topic,
                json.dumps(social_platforms), video_platform, voice_mode, _clean(voice_transcript), timestamp, timestamp,
            ),
        )
        add_event(campaign_id, "campaign.queued", {"topic": topic}, connection=connection)
        row = connection.execute("SELECT * FROM marketing_campaigns WHERE id = ?", (campaign_id,)).fetchone()
    return _decode_campaign(row)  # type: ignore[return-value]


def update_campaign(campaign_id: str, **fields: Any) -> None:
    allowed = {
        "objective", "buyer", "topic", "status", "current_stage", "g1_campaign_id", "g1_output_path",
        "platform_script_path", "media_search_path", "asset_root",
        "asset_manifest_path", "selected_variant_id", "g3_handoff_path",
        "g3_status", "g3_result_json", "g1_approved_at",
        "g1_revision_instruction", "voice_mode", "voice_transcript",
        "voice_handoff_path", "voice_recording_path", "error",
    }
    unknown = set(fields) - allowed
    if unknown:
        raise ValueError(f"unsupported campaign fields: {sorted(unknown)}")
    fields["updated_at"] = now_iso()
    assignments = ", ".join(f"{name} = ?" for name in fields)
    with connect() as connection:
        connection.execute(
            f"UPDATE marketing_campaigns SET {assignments} WHERE id = ?",
            (*fields.values(), campaign_id),
        )


def get_campaign(campaign_id: str, *, include_variants: bool = True) -> dict | None:
    with connect() as connection:
        row = connection.execute("SELECT * FROM marketing_campaigns WHERE id = ?", (campaign_id,)).fetchone()
        value = _decode_campaign(row)
        if value and include_variants:
            rows = connection.execute(
                "SELECT * FROM marketing_variants WHERE campaign_id = ? ORDER BY created_at, id",
                (campaign_id,),
            ).fetchall()
            value["variants"] = [dict(item) for item in rows]
        return value


def list_campaigns(limit: int = 20, project_id: int | None = None) -> list[dict]:
    query = "SELECT * FROM marketing_campaigns"
    parameters: list[Any] = []
    if project_id is not None:
        query += " WHERE project_id = ?"
        parameters.append(project_id)
    query += " ORDER BY created_at DESC LIMIT ?"
    parameters.append(max(1, min(limit, 100)))
    with connect() as connection:
        rows = connection.execute(query, parameters).fetchall()
    values = []
    for row in rows:
        item = _decode_campaign(row)
        if item:
            item["variants"] = get_campaign(item["id"])["variants"]
            values.append(item)
    return values


def create_variant(campaign_id: str, platform: str, voice: str, speed: float) -> dict:
    variant_id = _id("var")
    timestamp = now_iso()
    with connect() as connection:
        connection.execute(
            """
            INSERT INTO marketing_variants (
                id, campaign_id, platform, voice, speed, status, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, 'queued', ?, ?)
            """,
            (variant_id, campaign_id, platform, voice, speed, timestamp, timestamp),
        )
        row = connection.execute("SELECT * FROM marketing_variants WHERE id = ?", (variant_id,)).fetchone()
    return dict(row)


def update_variant(variant_id: str, **fields: Any) -> None:
    allowed = {"status", "video_path", "manifest_path", "sha256", "duration_seconds", "error"}
    unknown = set(fields) - allowed
    if unknown:
        raise ValueError(f"unsupported variant fields: {sorted(unknown)}")
    fields["updated_at"] = now_iso()
    assignments = ", ".join(f"{name} = ?" for name in fields)
    with connect() as connection:
        connection.execute(
            f"UPDATE marketing_variants SET {assignments} WHERE id = ?",
            (*fields.values(), variant_id),
        )


def get_variant(variant_id: str) -> dict | None:
    with connect() as connection:
        row = connection.execute("SELECT * FROM marketing_variants WHERE id = ?", (variant_id,)).fetchone()
    return dict(row) if row else None


def clear_variants(campaign_id: str) -> int:
    with connect() as connection:
        cursor = connection.execute(
            "DELETE FROM marketing_variants WHERE campaign_id = ?",
            (campaign_id,),
        )
        return cursor.rowcount


def add_event(
    campaign_id: str,
    event: str,
    payload: dict,
    *,
    connection: sqlite3.Connection | None = None,
) -> None:
    owns_connection = connection is None
    connection = connection or connect()
    try:
        connection.execute(
            "INSERT INTO marketing_events (campaign_id, event, payload_json, created_at) VALUES (?, ?, ?, ?)",
            (campaign_id, event, json.dumps(payload, ensure_ascii=False), now_iso()),
        )
        if owns_connection:
            connection.commit()
    finally:
        if owns_connection:
            connection.close()


def list_events(campaign_id: str) -> list[dict]:
    with connect() as connection:
        rows = connection.execute(
            "SELECT id, event, payload_json, created_at FROM marketing_events WHERE campaign_id = ? ORDER BY id",
            (campaign_id,),
        ).fetchall()
    return [
        {
            "id": row["id"],
            "event": row["event"],
            "payload": json.loads(row["payload_json"]),
            "created_at": row["created_at"],
        }
        for row in rows
    ]


init_marketing_db()


def create_manual_post(
    *,
    project_id: int,
    platform: str,
    post_id: str,
    destination_url: str,
    tracked_url: str,
    campaign_id: str | None = None,
    title: str | None = None,
    creative: str | None = None,
    hook: str | None = None,
    offer: str | None = None,
    cta: str | None = None,
    source_detail: str | None = None,
    asset_dir: str | None = None,
    assets: list[dict[str, Any]] | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict:
    manual_id = _id("post")
    timestamp = now_iso()
    with connect() as connection:
        connection.execute(
            """
            INSERT INTO marketing_manual_posts (
                id, project_id, campaign_id, platform, post_id, title, creative, hook, offer, cta,
                destination_url, tracked_url, source_detail, asset_dir, assets_json, metadata_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                manual_id, project_id, _clean(campaign_id), platform, post_id, _clean(title), _clean(creative), _clean(hook), _clean(offer), _clean(cta),
                destination_url, tracked_url, _clean(source_detail), _clean(asset_dir),
                json.dumps(assets or [], ensure_ascii=False), json.dumps(metadata or {}, ensure_ascii=False), timestamp, timestamp,
            ),
        )
        row = connection.execute("SELECT * FROM marketing_manual_posts WHERE id = ?", (manual_id,)).fetchone()
    decoded = _decode_manual_post(row)
    assert decoded
    return decoded


def get_manual_post(post_record_id: str) -> dict | None:
    with connect() as connection:
        row = connection.execute("SELECT * FROM marketing_manual_posts WHERE id = ?", (post_record_id,)).fetchone()
    return _decode_manual_post(row)


def list_manual_posts(limit: int = 50, project_id: int | None = None) -> list[dict]:
    query = "SELECT * FROM marketing_manual_posts"
    params: list[Any] = []
    if project_id is not None:
        query += " WHERE project_id = ?"
        params.append(project_id)
    query += " ORDER BY created_at DESC LIMIT ?"
    params.append(max(1, min(limit, 200)))
    with connect() as connection:
        rows = connection.execute(query, params).fetchall()
    return [decoded for row in rows if (decoded := _decode_manual_post(row))]


def update_manual_post(post_record_id: str, **fields: Any) -> dict:
    allowed = {
        "campaign_id", "platform", "post_id", "title", "creative", "hook", "offer", "cta",
        "destination_url", "tracked_url", "source_detail", "asset_dir", "assets_json", "metadata_json",
    }
    unknown = set(fields) - allowed
    if unknown:
        raise ValueError(f"unsupported manual post fields: {sorted(unknown)}")
    fields = {key: value for key, value in fields.items() if value is not None}
    fields["updated_at"] = now_iso()
    assignments = ", ".join(f"{name} = ?" for name in fields)
    with connect() as connection:
        connection.execute(
            f"UPDATE marketing_manual_posts SET {assignments} WHERE id = ?",
            (*fields.values(), post_record_id),
        )
        row = connection.execute("SELECT * FROM marketing_manual_posts WHERE id = ?", (post_record_id,)).fetchone()
    decoded = _decode_manual_post(row)
    if not decoded:
        raise ValueError("manual post not found")
    return decoded
