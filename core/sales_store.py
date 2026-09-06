from __future__ import annotations

import json
import sqlite3
import uuid
from typing import Any

from core.state import DB_PATH, now_iso


PIPELINE_STAGES = {
    "new", "enriched", "qualified", "draft_ready", "approved",
    "contacted", "scheduled", "replied", "won", "lost", "suppressed",
}


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DB_PATH, timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA foreign_keys=ON")
    return connection


def init_sales_db() -> None:
    with connect() as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS sales_leads (
                id TEXT PRIMARY KEY,
                email TEXT UNIQUE,
                full_name TEXT,
                job_title TEXT,
                company TEXT,
                company_domain TEXT,
                phone TEXT,
                country TEXT,
                source TEXT NOT NULL,
                source_detail TEXT,
                campaign_id TEXT,
                post_id TEXT,
                utm_source TEXT,
                utm_medium TEXT,
                utm_campaign TEXT,
                message TEXT,
                consent INTEGER NOT NULL DEFAULT 0,
                verification_status TEXT,
                hunter_score INTEGER,
                lead_score INTEGER NOT NULL DEFAULT 0,
                stage TEXT NOT NULL DEFAULT 'new',
                suppression_reason TEXT,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS sales_drafts (
                id TEXT PRIMARY KEY,
                lead_id TEXT NOT NULL,
                channel TEXT NOT NULL,
                kind TEXT NOT NULL DEFAULT 'first_outreach',
                subject TEXT NOT NULL,
                body TEXT NOT NULL,
                in_reply_to TEXT,
                status TEXT NOT NULL DEFAULT 'draft',
                approved_at TEXT,
                sent_at TEXT,
                provider_message_id TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(lead_id) REFERENCES sales_leads(id)
            );

            CREATE TABLE IF NOT EXISTS sales_interactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                lead_id TEXT NOT NULL,
                direction TEXT NOT NULL,
                channel TEXT NOT NULL,
                kind TEXT NOT NULL,
                subject TEXT,
                body TEXT,
                provider_message_id TEXT,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                FOREIGN KEY(lead_id) REFERENCES sales_leads(id)
            );

            CREATE TABLE IF NOT EXISTS sales_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                lead_id TEXT,
                event TEXT NOT NULL,
                payload_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS sales_company_profiles (
                domain TEXT PRIMARY KEY,
                company_name TEXT,
                provider TEXT NOT NULL,
                status TEXT NOT NULL,
                summary_json TEXT NOT NULL DEFAULT '{}',
                raw_json TEXT NOT NULL DEFAULT '{}',
                fetched_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE UNIQUE INDEX IF NOT EXISTS sales_interaction_provider_uid
            ON sales_interactions(provider_message_id)
            WHERE provider_message_id IS NOT NULL;
            """
        )


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _clean(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _decode(row: sqlite3.Row | None) -> dict | None:
    if row is None:
        return None
    value = dict(row)
    value["metadata"] = json.loads(value.pop("metadata_json") or "{}")
    value["consent"] = bool(value["consent"])
    value["contact_profile"] = contact_profile_for_lead(value)
    return value


def _merge_metadata(existing: dict[str, Any] | None, patch: dict[str, Any]) -> dict[str, Any]:
    merged = dict(existing or {})
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge_metadata(merged.get(key), value)
        else:
            merged[key] = value
    return merged


def _decode_interaction_row(row: sqlite3.Row | None) -> dict | None:
    if row is None:
        return None
    item = dict(row)
    item["metadata"] = json.loads(item.get("metadata_json") or "{}")
    return item


def _decode_event_row(row: sqlite3.Row | None) -> dict | None:
    if row is None:
        return None
    item = dict(row)
    item["payload"] = json.loads(item.pop("payload_json") or "{}")
    return item


def _attach_lead_relations(connection: sqlite3.Connection, lead: dict, *, drafts_limit: int = 5, interactions_limit: int = 5, events_limit: int = 6) -> dict:
    lead_id = lead["id"]
    drafts = connection.execute(
        "SELECT * FROM sales_drafts WHERE lead_id = ? ORDER BY created_at DESC LIMIT ?",
        (lead_id, drafts_limit),
    ).fetchall()
    interactions = connection.execute(
        "SELECT * FROM sales_interactions WHERE lead_id = ? ORDER BY created_at DESC LIMIT ?",
        (lead_id, interactions_limit),
    ).fetchall()
    recent_events = connection.execute(
        "SELECT id, lead_id, event, payload_json, created_at FROM sales_events WHERE lead_id = ? ORDER BY id DESC LIMIT ?",
        (lead_id, events_limit),
    ).fetchall()
    lead["drafts"] = [dict(item) for item in drafts]
    lead["interactions"] = [decoded for item in interactions if (decoded := _decode_interaction_row(item))]
    lead["latest_interaction"] = lead["interactions"][0] if lead["interactions"] else None
    lead["recent_events"] = [decoded for item in recent_events if (decoded := _decode_event_row(item))]
    lead["company_profile"] = get_company_profile(lead.get("company_domain"))
    return lead


def contact_profile_for_lead(lead: dict | None) -> dict[str, Any]:
    lead = lead or {}
    metadata = lead.get("metadata") if isinstance(lead.get("metadata"), dict) else {}
    resolution = metadata.get("contact_resolution") if isinstance(metadata.get("contact_resolution"), dict) else {}
    email = _clean(lead.get("email"))
    phone = _clean(lead.get("phone"))
    whatsapp_candidate = _clean(resolution.get("whatsapp_candidate")) or phone
    verification_status = _clean(lead.get("verification_status")) or _clean((resolution.get("details") or {}).get("verification_status"))
    email_ready = bool(email)
    whatsapp_ready = bool(whatsapp_candidate)
    ready_for_outreach = email_ready or whatsapp_ready
    return {
        "provider": _clean(resolution.get("provider")),
        "status": _clean(resolution.get("status")) or ("resolved" if ready_for_outreach else "missing"),
        "resolved_at": _clean(resolution.get("resolved_at")),
        "email": email,
        "phone": phone,
        "whatsapp_candidate": whatsapp_candidate,
        "email_found": bool(email),
        "phone_found": bool(phone),
        "email_ready": email_ready,
        "whatsapp_ready": whatsapp_ready,
        "ready_for_outreach": ready_for_outreach,
        "needs_generic_fallback": not ready_for_outreach,
        "verification_status": verification_status,
    }


def _company_profile_decode(row: sqlite3.Row | None) -> dict | None:
    if row is None:
        return None
    value = dict(row)
    value["summary"] = json.loads(value.pop("summary_json") or "{}")
    value["raw"] = json.loads(value.pop("raw_json") or "{}")
    return value


def normalize_company_domain(value: Any) -> str | None:
    text = _clean(value)
    if not text:
        return None
    normalized = text.lower()
    for prefix in ("https://", "http://"):
        if normalized.startswith(prefix):
            normalized = normalized[len(prefix):]
            break
    normalized = normalized.split("/", 1)[0].split("?", 1)[0].strip()
    if normalized.startswith("www."):
        normalized = normalized[4:]
    return normalized or None


def get_company_profile(domain: str | None) -> dict | None:
    normalized = normalize_company_domain(domain)
    if not normalized:
        return None
    with connect() as connection:
        row = connection.execute("SELECT * FROM sales_company_profiles WHERE domain = ?", (normalized,)).fetchone()
    return _company_profile_decode(row)


def upsert_company_profile(
    domain: str,
    *,
    provider: str,
    status: str,
    company_name: str | None = None,
    summary: dict[str, Any] | None = None,
    raw: dict[str, Any] | None = None,
    fetched_at: str | None = None,
) -> dict:
    normalized = normalize_company_domain(domain)
    if not normalized:
        raise ValueError("company domain is required")
    timestamp = now_iso()
    with connect() as connection:
        existing = connection.execute("SELECT * FROM sales_company_profiles WHERE domain = ?", (normalized,)).fetchone()
        if existing:
            connection.execute(
                """
                UPDATE sales_company_profiles
                SET company_name = ?, provider = ?, status = ?, summary_json = ?, raw_json = ?, fetched_at = ?, updated_at = ?
                WHERE domain = ?
                """,
                (
                    _clean(company_name),
                    provider,
                    status,
                    json.dumps(summary or {}, ensure_ascii=False),
                    json.dumps(raw or {}, ensure_ascii=False),
                    fetched_at or timestamp,
                    timestamp,
                    normalized,
                ),
            )
        else:
            connection.execute(
                """
                INSERT INTO sales_company_profiles (domain, company_name, provider, status, summary_json, raw_json, fetched_at, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    normalized,
                    _clean(company_name),
                    provider,
                    status,
                    json.dumps(summary or {}, ensure_ascii=False),
                    json.dumps(raw or {}, ensure_ascii=False),
                    fetched_at or timestamp,
                    timestamp,
                    timestamp,
                ),
            )
        row = connection.execute("SELECT * FROM sales_company_profiles WHERE domain = ?", (normalized,)).fetchone()
    decoded = _company_profile_decode(row)
    assert decoded
    return decoded


def score_lead(lead: dict) -> int:
    score = 0
    source = (lead.get("source") or "").lower()
    email = (lead.get("email") or "").lower()
    title = (lead.get("job_title") or "").lower()
    country = (lead.get("country") or "").lower()
    message = (lead.get("message") or "").lower()
    if source in {"website", "post_form", "social_form", "referral"}:
        score += 35
    if lead.get("consent"):
        score += 10
    if email and not email.endswith(("@gmail.com", "@outlook.com", "@yahoo.com", "@hotmail.com")):
        score += 10
    if any(word in title for word in ("director", "manager", "head", "founder", "owner")):
        score += 15
    if any(word in f"{title} {message}" for word in ("logistics", "freight", "cargo", "customs", "clearing", "operations")):
        score += 15
    if any(word in country for word in ("rwanda", "kenya", "uganda", "tanzania", "burundi", "congo")):
        score += 10
    if lead.get("verification_status") == "valid":
        score += 10
    elif lead.get("verification_status") in {"invalid", "disposable"}:
        score -= 40
    if lead.get("phone"):
        score += 5
    return max(0, min(score, 100))


def upsert_lead(payload: dict) -> tuple[dict, bool]:
    email = _clean(payload.get("email"))
    email = email.lower() if email else None
    timestamp = now_iso()
    with connect() as connection:
        existing = None
        if email:
            existing = connection.execute(
                "SELECT * FROM sales_leads WHERE email = ?", (email,)
            ).fetchone()
        if existing:
            lead_id = existing["id"]
            updates = {
                key: _clean(payload.get(key))
                for key in (
                    "full_name", "job_title", "company", "company_domain", "phone",
                    "country", "source_detail", "campaign_id", "post_id", "utm_source",
                    "utm_medium", "utm_campaign", "message",
                )
                if _clean(payload.get(key))
            }
            if payload.get("consent"):
                updates["consent"] = 1
            if _clean(payload.get("verification_status")):
                updates["verification_status"] = _clean(payload.get("verification_status"))
            if payload.get("hunter_score") is not None:
                updates["hunter_score"] = int(payload["hunter_score"])
            if updates:
                updates["updated_at"] = timestamp
                assignments = ", ".join(f"{key} = ?" for key in updates)
                connection.execute(
                    f"UPDATE sales_leads SET {assignments} WHERE id = ?",
                    (*updates.values(), lead_id),
                )
            created = False
        else:
            lead_id = _id("lead")
            connection.execute(
                """
                INSERT INTO sales_leads (
                    id, email, full_name, job_title, company, company_domain, phone,
                    country, source, source_detail, campaign_id, post_id, utm_source,
                    utm_medium, utm_campaign, message, consent, verification_status,
                    hunter_score, metadata_json,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    lead_id, email, _clean(payload.get("full_name")), _clean(payload.get("job_title")),
                    _clean(payload.get("company")), _clean(payload.get("company_domain")),
                    _clean(payload.get("phone")), _clean(payload.get("country")),
                    _clean(payload.get("source")) or "manual", _clean(payload.get("source_detail")),
                    _clean(payload.get("campaign_id")), _clean(payload.get("post_id")),
                    _clean(payload.get("utm_source")), _clean(payload.get("utm_medium")),
                    _clean(payload.get("utm_campaign")), _clean(payload.get("message")),
                    int(bool(payload.get("consent"))), _clean(payload.get("verification_status")),
                    int(payload.get("hunter_score") or 0), json.dumps(payload.get("metadata") or {}),
                    timestamp, timestamp,
                ),
            )
            created = True
        row = connection.execute("SELECT * FROM sales_leads WHERE id = ?", (lead_id,)).fetchone()
        lead = _decode(row)
        assert lead
        lead_score = score_lead(lead)
        stage = "qualified" if lead_score >= 60 else ("enriched" if lead.get("verification_status") else lead["stage"])
        if lead["stage"] != "suppressed":
            connection.execute(
                "UPDATE sales_leads SET lead_score = ?, stage = ?, updated_at = ? WHERE id = ?",
                (lead_score, stage, timestamp, lead_id),
            )
        connection.execute(
            "INSERT INTO sales_events (lead_id, event, payload_json, created_at) VALUES (?, ?, ?, ?)",
            (lead_id, "lead.created" if created else "lead.updated", json.dumps({"source": payload.get("source")}), timestamp),
        )
        row = connection.execute("SELECT * FROM sales_leads WHERE id = ?", (lead_id,)).fetchone()
    return _decode(row), created  # type: ignore[return-value]


def get_lead(lead_id: str) -> dict | None:
    with connect() as connection:
        row = connection.execute("SELECT * FROM sales_leads WHERE id = ?", (lead_id,)).fetchone()
        lead = _decode(row)
        if lead:
            lead = _attach_lead_relations(connection, lead, drafts_limit=20, interactions_limit=20, events_limit=12)
        return lead


def get_lead_by_email(email: str | None) -> dict | None:
    normalized = _clean(email)
    normalized = normalized.lower() if normalized else None
    if not normalized:
        return None
    with connect() as connection:
        row = connection.execute("SELECT id FROM sales_leads WHERE email = ?", (normalized,)).fetchone()
    if not row:
        return None
    return get_lead(row["id"])


def list_leads(limit: int = 50, stage: str | None = None) -> list[dict]:
    query = "SELECT * FROM sales_leads"
    values: list[Any] = []
    if stage:
        query += " WHERE stage = ?"
        values.append(stage)
    query += " ORDER BY lead_score DESC, created_at DESC LIMIT ?"
    values.append(max(1, min(limit, 200)))
    with connect() as connection:
        rows = connection.execute(query, values).fetchall()
        result = []
        for row in rows:
            lead = _decode(row)
            if not lead:
                continue
            result.append(_attach_lead_relations(connection, lead))
    return result


def merge_lead_metadata(lead_id: str, patch: dict[str, Any]) -> dict:
    timestamp = now_iso()
    with connect() as connection:
        row = connection.execute("SELECT * FROM sales_leads WHERE id = ?", (lead_id,)).fetchone()
        lead = _decode(row)
        if not lead:
            raise ValueError("lead not found")
        metadata = _merge_metadata(lead.get("metadata") or {}, patch)
        connection.execute(
            "UPDATE sales_leads SET metadata_json = ?, updated_at = ? WHERE id = ?",
            (json.dumps(metadata, ensure_ascii=False), timestamp, lead_id),
        )
        row = connection.execute("SELECT * FROM sales_leads WHERE id = ?", (lead_id,)).fetchone()
    updated = _decode(row)
    assert updated
    return updated


def record_contact_resolution(
    lead_id: str,
    *,
    provider: str,
    email: str | None = None,
    phone: str | None = None,
    whatsapp_candidate: str | None = None,
    status: str,
    details: dict[str, Any] | None = None,
) -> dict:
    timestamp = now_iso()
    email_value = _clean(email)
    email_value = email_value.lower() if email_value else None
    phone_value = _clean(phone)
    whatsapp_value = _clean(whatsapp_candidate)
    with connect() as connection:
        row = connection.execute("SELECT * FROM sales_leads WHERE id = ?", (lead_id,)).fetchone()
        lead = _decode(row)
        if not lead:
            raise ValueError("lead not found")
        resolved_email = email_value or _clean(lead.get("email"))
        resolved_phone = phone_value or _clean(lead.get("phone"))
        resolved_whatsapp = whatsapp_value or resolved_phone
        metadata = _merge_metadata(lead.get("metadata") or {}, {
            "contact_resolution": {
                "provider": provider,
                "status": status,
                "resolved_at": timestamp,
                "email": resolved_email,
                "phone": resolved_phone,
                "email_found": bool(resolved_email),
                "phone_found": bool(resolved_phone),
                "email_ready": bool(resolved_email),
                "whatsapp_candidate": resolved_whatsapp,
                "whatsapp_ready": bool(resolved_whatsapp),
                "ready_for_outreach": bool(resolved_email or resolved_whatsapp),
                "needs_generic_fallback": not bool(resolved_email or resolved_whatsapp),
                "details": details or {},
            }
        })
        updates = {
            "metadata_json": json.dumps(metadata, ensure_ascii=False),
            "updated_at": timestamp,
        }
        if email_value and not lead.get("email"):
            updates["email"] = email_value
        elif email_value and lead.get("email") != email_value:
            updates["email"] = email_value
        if phone_value and not lead.get("phone"):
            updates["phone"] = phone_value
        elif phone_value and lead.get("phone") != phone_value:
            updates["phone"] = phone_value
        assignments = ", ".join(f"{key} = ?" for key in updates)
        connection.execute(
            f"UPDATE sales_leads SET {assignments} WHERE id = ?",
            (*updates.values(), lead_id),
        )
        row = connection.execute("SELECT * FROM sales_leads WHERE id = ?", (lead_id,)).fetchone()
        updated = _decode(row)
        assert updated
        lead_score = score_lead(updated)
        stage = updated["stage"]
        if stage != "suppressed":
            if lead_score >= 60:
                stage = "qualified"
            elif updated.get("email") or updated.get("phone") or updated.get("verification_status"):
                stage = "enriched"
            connection.execute(
                "UPDATE sales_leads SET lead_score = ?, stage = ?, updated_at = ? WHERE id = ?",
                (lead_score, stage, timestamp, lead_id),
            )
            row = connection.execute("SELECT * FROM sales_leads WHERE id = ?", (lead_id,)).fetchone()
            updated = _decode(row)
            assert updated
    return updated


def update_lead(lead_id: str, **fields: Any) -> None:
    allowed = {
        "email", "full_name", "job_title", "company", "company_domain", "phone", "country",
        "verification_status", "hunter_score", "lead_score", "stage", "suppression_reason",
    }
    unknown = set(fields) - allowed
    if unknown:
        raise ValueError(f"unsupported lead fields: {sorted(unknown)}")
    if "stage" in fields and fields["stage"] not in PIPELINE_STAGES:
        raise ValueError("invalid sales stage")
    if "email" in fields:
        fields["email"] = _clean(fields["email"])
        fields["email"] = fields["email"].lower() if fields["email"] else None
    fields["updated_at"] = now_iso()
    assignments = ", ".join(f"{key} = ?" for key in fields)
    with connect() as connection:
        connection.execute(
            f"UPDATE sales_leads SET {assignments} WHERE id = ?", (*fields.values(), lead_id)
        )


def suppress_lead(lead_id: str, reason: str) -> None:
    update_lead(lead_id, stage="suppressed", suppression_reason=reason)
    add_event(lead_id, "lead.suppressed", {"reason": reason})


def create_draft(
    lead_id: str,
    subject: str,
    body: str,
    channel: str = "email",
    kind: str = "first_outreach",
    in_reply_to: str | None = None,
) -> dict:
    draft_id = _id("draft")
    timestamp = now_iso()
    with connect() as connection:
        connection.execute(
            """
            INSERT INTO sales_drafts (id, lead_id, channel, kind, subject, body, in_reply_to, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (draft_id, lead_id, channel, kind, subject.strip(), body.strip(), in_reply_to, timestamp, timestamp),
        )
        connection.execute(
            "UPDATE sales_leads SET stage = 'draft_ready', updated_at = ? WHERE id = ? AND stage != 'suppressed'",
            (timestamp, lead_id),
        )
        row = connection.execute("SELECT * FROM sales_drafts WHERE id = ?", (draft_id,)).fetchone()
    add_event(lead_id, "outreach.drafted", {"draft_id": draft_id})
    return dict(row)


def get_draft(draft_id: str) -> dict | None:
    with connect() as connection:
        row = connection.execute("SELECT * FROM sales_drafts WHERE id = ?", (draft_id,)).fetchone()
    return dict(row) if row else None


def get_draft_by_provider_message_id(provider_message_id: str | None) -> dict | None:
    message_id = _clean(provider_message_id)
    if not message_id:
        return None
    with connect() as connection:
        row = connection.execute("SELECT * FROM sales_drafts WHERE provider_message_id = ?", (message_id,)).fetchone()
    return dict(row) if row else None


def update_draft(draft_id: str, *, subject: str | None = None, body: str | None = None) -> dict:
    draft = get_draft(draft_id)
    if not draft:
        raise ValueError("draft not found")
    if draft["status"] not in {"draft", "approved"}:
        raise ValueError("sent or sending drafts cannot be edited")
    updates = {}
    if subject is not None:
        cleaned = subject.strip()
        if len(cleaned) < 3:
            raise ValueError("draft subject must be at least 3 characters")
        updates["subject"] = cleaned[:200]
    if body is not None:
        cleaned = body.strip()
        if len(cleaned) < 20:
            raise ValueError("draft body must be at least 20 characters")
        updates["body"] = cleaned[:6000]
    if not updates:
        raise ValueError("subject or body is required")
    timestamp = now_iso()
    updates["updated_at"] = timestamp
    assignments = ", ".join(f"{key} = ?" for key in updates)
    with connect() as connection:
        connection.execute(
            f"UPDATE sales_drafts SET {assignments} WHERE id = ?",
            (*updates.values(), draft_id),
        )
    add_event(draft["lead_id"], "outreach.draft_updated", {"draft_id": draft_id, "fields": sorted(set(updates) - {"updated_at"})})
    updated = get_draft(draft_id)
    assert updated
    return updated


def approve_draft(draft_id: str) -> dict:
    draft = get_draft(draft_id)
    if not draft or draft["status"] != "draft":
        raise ValueError("draft is not awaiting approval")
    timestamp = now_iso()
    with connect() as connection:
        connection.execute(
            "UPDATE sales_drafts SET status = 'approved', approved_at = ?, updated_at = ? WHERE id = ?",
            (timestamp, timestamp, draft_id),
        )
        connection.execute(
            "UPDATE sales_leads SET stage = 'approved', updated_at = ? WHERE id = ? AND stage != 'suppressed'",
            (timestamp, draft["lead_id"]),
        )
    add_event(draft["lead_id"], "founder.outreach_approved", {"draft_id": draft_id})
    return get_draft(draft_id)  # type: ignore[return-value]


def claim_draft_for_send(draft_id: str) -> dict:
    timestamp = now_iso()
    with connect() as connection:
        cursor = connection.execute(
            "UPDATE sales_drafts SET status = 'sending', updated_at = ? WHERE id = ? AND status = 'approved'",
            (timestamp, draft_id),
        )
        if cursor.rowcount != 1:
            raise ValueError("founder approval is required or this draft is already sending")
        row = connection.execute("SELECT * FROM sales_drafts WHERE id = ?", (draft_id,)).fetchone()
    return dict(row)


def release_send_claim(draft_id: str) -> None:
    with connect() as connection:
        connection.execute(
            "UPDATE sales_drafts SET status = 'approved', updated_at = ? WHERE id = ? AND status = 'sending'",
            (now_iso(), draft_id),
        )


def mark_sent(draft_id: str, provider_message_id: str, metadata: dict | None = None) -> None:
    draft = get_draft(draft_id)
    if not draft:
        raise ValueError("draft not found")
    timestamp = now_iso()
    with connect() as connection:
        connection.execute(
            "UPDATE sales_drafts SET status = 'sent', sent_at = ?, provider_message_id = ?, updated_at = ? WHERE id = ?",
            (timestamp, provider_message_id, timestamp, draft_id),
        )
        connection.execute(
            "UPDATE sales_leads SET stage = 'contacted', updated_at = ? WHERE id = ?",
            (timestamp, draft["lead_id"]),
        )
    interaction_metadata = dict(metadata or {})
    interaction_metadata.setdefault("sent_at", timestamp)
    add_interaction(draft["lead_id"], "outbound", "email", "outreach", draft["subject"], draft["body"], provider_message_id, metadata=interaction_metadata)
    add_event(draft["lead_id"], "outreach.sent", {"draft_id": draft_id, "provider_message_id": provider_message_id, **interaction_metadata})


def add_interaction(lead_id: str, direction: str, channel: str, kind: str, subject: str | None, body: str | None, provider_message_id: str | None = None, metadata: dict | None = None) -> bool:
    with connect() as connection:
        cursor = connection.execute(
            """
            INSERT OR IGNORE INTO sales_interactions (lead_id, direction, channel, kind, subject, body, provider_message_id, metadata_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (lead_id, direction, channel, kind, subject, body, provider_message_id, json.dumps(metadata or {}), now_iso()),
        )
        return cursor.rowcount == 1


def mark_replied(lead_id: str, subject: str, body: str, provider_message_id: str | None = None, metadata: dict | None = None) -> None:
    if add_interaction(lead_id, "inbound", "email", "reply", subject, body, provider_message_id, metadata=metadata):
        update_lead(lead_id, stage="replied")
        add_event(lead_id, "lead.replied", {"subject": subject})


def mark_meeting_scheduled(lead_id: str, scheduled_for: str | None = None, note: str | None = None) -> dict:
    lead = get_lead(lead_id)
    if not lead:
        raise ValueError("lead not found")
    timestamp = now_iso()
    meeting = {
        "status": "scheduled",
        "scheduled_for": _clean(scheduled_for),
        "note": _clean(note),
        "updated_at": timestamp,
        "source": "manual",
    }
    merge_lead_metadata(lead_id, {"meeting": meeting})
    update_lead(lead_id, stage="scheduled")
    add_event(lead_id, "meeting.scheduled", {key: value for key, value in meeting.items() if value is not None})
    refreshed = get_lead(lead_id)
    assert refreshed
    return refreshed


def add_event(lead_id: str | None, event: str, payload: dict) -> None:
    with connect() as connection:
        connection.execute(
            "INSERT INTO sales_events (lead_id, event, payload_json, created_at) VALUES (?, ?, ?, ?)",
            (lead_id, event, json.dumps(payload, ensure_ascii=False), now_iso()),
        )


def count_company_profile_attempts() -> int:
    with connect() as connection:
        row = connection.execute(
            "SELECT COUNT(*) FROM sales_events WHERE event IN ('company_profile.resolved', 'company_profile.failed')"
        ).fetchone()
    return int(row[0] or 0)


def list_events(limit: int = 50, lead_id: str | None = None) -> list[dict]:
    query = "SELECT id, lead_id, event, payload_json, created_at FROM sales_events"
    values: list[Any] = []
    if lead_id:
        query += " WHERE lead_id = ?"
        values.append(lead_id)
    query += " ORDER BY id DESC LIMIT ?"
    values.append(max(1, min(limit, 200)))
    with connect() as connection:
        rows = connection.execute(query, values).fetchall()
    return [decoded for row in rows if (decoded := _decode_event_row(row))]


def list_interactions(limit: int = 50, lead_id: str | None = None) -> list[dict]:
    query = "SELECT id, lead_id, direction, channel, kind, subject, body, provider_message_id, metadata_json, created_at FROM sales_interactions"
    values: list[Any] = []
    if lead_id:
        query += " WHERE lead_id = ?"
        values.append(lead_id)
    query += " ORDER BY id DESC LIMIT ?"
    values.append(max(1, min(limit, 200)))
    with connect() as connection:
        rows = connection.execute(query, values).fetchall()
    return [decoded for row in rows if (decoded := _decode_interaction_row(row))]


def summary() -> dict:
    with connect() as connection:
        total = connection.execute("SELECT COUNT(*) FROM sales_leads").fetchone()[0]
        rows = connection.execute("SELECT stage, COUNT(*) AS count FROM sales_leads GROUP BY stage").fetchall()
        source_rows = connection.execute("SELECT source, COUNT(*) AS count FROM sales_leads GROUP BY source").fetchall()
    return {
        "total": total,
        "by_stage": {row["stage"]: row["count"] for row in rows},
        "by_source": {row["source"]: row["count"] for row in source_rows},
    }


init_sales_db()
