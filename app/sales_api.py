from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, EmailStr, Field, field_validator

from core.sales_store import (
    approve_draft, get_lead, list_leads, mark_meeting_scheduled, summary, suppress_lead,
    update_draft, upsert_lead,
)
from services.company_enrich import CompanyEnrichError
from services.hunter import HunterClient
from services.prospeo import ProspeoError
from services.lusha import LushaError
from services.sales_service import (
    build_draft, enrich_lead, import_domain, ingest_inbound_email, ingest_resend_event, maybe_auto_contact_lead,
    research_lusha_suggestions,
    research_market_leads, research_prospeo_suggestions, resolve_company_profile, resolve_contact, preview_draft, send_approved, verify_resend_webhook,
)


router = APIRouter(prefix="/company/sales", tags=["sales"])
action_router = APIRouter(prefix="/company/sales", tags=["sales-actions"])
intake_router = APIRouter(prefix="/integrations", tags=["integrations"])


class LeadIntake(BaseModel):
    email: EmailStr | None = None
    full_name: str | None = Field(default=None, max_length=160)
    job_title: str | None = Field(default=None, max_length=160)
    company: str | None = Field(default=None, max_length=200)
    company_domain: str | None = Field(default=None, max_length=253)
    phone: str | None = Field(default=None, max_length=60)
    country: str | None = Field(default=None, max_length=100)
    source: str = Field(default="website", max_length=60)
    source_detail: str | None = Field(default=None, max_length=200)
    campaign_id: str | None = Field(default=None, max_length=100)
    post_id: str | None = Field(default=None, max_length=100)
    utm_source: str | None = Field(default=None, max_length=100)
    utm_medium: str | None = Field(default=None, max_length=100)
    utm_campaign: str | None = Field(default=None, max_length=160)
    message: str | None = Field(default=None, max_length=4000)
    consent: bool = False
    metadata: dict = Field(default_factory=dict)

    @field_validator(
        "full_name",
        "job_title",
        "company",
        "company_domain",
        "phone",
        "country",
        "source",
        "source_detail",
        "campaign_id",
        "post_id",
        "utm_source",
        "utm_medium",
        "utm_campaign",
        "message",
        mode="before",
    )
    @classmethod
    def _blank_strings_to_none(cls, value: object) -> object:
        if value is None:
            return None
        if isinstance(value, str):
            cleaned = value.strip()
            return cleaned or None
        return value


class ProspectRequest(BaseModel):
    domain: str = Field(min_length=3, max_length=253)
    limit: int = Field(default=5, ge=1, le=10)
    provider: str = Field(default="hunter", min_length=3, max_length=20)


class LeadResearchRequest(BaseModel):
    industry: str = Field(min_length=2, max_length=160)
    location: str | None = Field(default=None, max_length=120)
    limit_per_provider: int = Field(default=5, ge=1, le=5)


class LeadResearchSuggestionsRequest(BaseModel):
    provider: str = Field(default="prospeo", min_length=3, max_length=20)
    kind: str = Field(min_length=3, max_length=40)
    query: str = Field(min_length=1, max_length=120)


class DraftUpdatePayload(BaseModel):
    subject: str | None = Field(default=None, min_length=3, max_length=200)
    body: str | None = Field(default=None, min_length=20, max_length=6000)


class MeetingScheduledPayload(BaseModel):
    scheduled_for: str | None = Field(default=None, max_length=120)
    note: str | None = Field(default=None, max_length=400)

    @field_validator("scheduled_for", "note", mode="before")
    @classmethod
    def _clean_optional_text(cls, value: object) -> object:
        if value is None:
            return None
        if isinstance(value, str):
            cleaned = value.strip()
            return cleaned or None
        return value


class InboundEmailPayload(BaseModel):
    from_email: str = Field(min_length=3, max_length=320)
    to_email: str | None = Field(default=None, max_length=320)
    subject: str | None = Field(default=None, max_length=500)
    text: str | None = Field(default=None, max_length=10000)
    html: str | None = Field(default=None, max_length=20000)
    message_id: str | None = Field(default=None, max_length=500)
    in_reply_to: str | None = Field(default=None, max_length=500)
    references: list[str] | str | None = None
    received_at: str | None = Field(default=None, max_length=100)
    headers: dict = Field(default_factory=dict)
    source: str = Field(default="cloudflare_email_routing", max_length=80)


class TallyWebhookPayload(BaseModel):
    eventId: str | None = None
    eventType: str | None = None
    createdAt: str | None = None
    data: dict = Field(default_factory=dict)


def _check_secret(actual: str | None, env_name: str, error_status: int, message: str) -> None:
    expected = os.getenv(env_name, "")
    if not expected or not actual or not hmac.compare_digest(actual, expected):
        raise HTTPException(error_status, message)


def _check_intake_secret(value: str | None) -> None:
    _check_secret(value, "SALES_INTAKE_SECRET", 401, "invalid lead intake credential")


def _check_email_webhook_secret(value: str | None) -> None:
    _check_secret(value, "SALES_EMAIL_WEBHOOK_SECRET", 401, "invalid sales email webhook credential")


def _verify_tally_signature(raw_payload: str, signature: str | None) -> None:
    expected_secret = os.getenv("TALLY_WEBHOOK_SECRET", "").strip()
    if not expected_secret:
        return
    if not signature:
        raise HTTPException(401, "missing Tally signature")
    digest = hmac.new(
        expected_secret.encode("utf-8"),
        raw_payload.encode("utf-8"),
        hashlib.sha256,
    ).digest()
    calculated = base64.b64encode(digest).decode("utf-8")
    if not hmac.compare_digest(signature, calculated):
        raise HTTPException(401, "invalid Tally signature")


def _normalize_tally_field_name(value: str | None) -> str:
    return "".join(ch.lower() if ch.isalnum() else "_" for ch in (value or "")).strip("_")


def _string_value(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip()
        return text or None
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if isinstance(item, str):
                text = item.strip()
                if text:
                    parts.append(text)
            elif isinstance(item, dict):
                text = str(item.get("text") or item.get("label") or item.get("value") or "").strip()
                if text:
                    parts.append(text)
        return ", ".join(parts) or None
    return None


def _bool_value(value) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on", "checked"}
    return False


def _tally_lookup(fields: list[dict]) -> dict[str, object]:
    lookup: dict[str, object] = {}
    for field in fields:
        key = _normalize_tally_field_name(field.get("key"))
        label = _normalize_tally_field_name(field.get("label"))
        value = field.get("value")
        if key:
            lookup[key] = value
        if label:
            lookup[label] = value
    return lookup


def _first_value(lookup: dict[str, object], *names: str) -> object | None:
    for name in names:
        normalized = _normalize_tally_field_name(name)
        if normalized in lookup:
            return lookup[normalized]
    return None


def _lead_intake_from_tally(payload: TallyWebhookPayload) -> LeadIntake:
    if (payload.eventType or "").strip().upper() not in {"FORM_RESPONSE", ""}:
        raise HTTPException(422, "unsupported Tally event type")
    data = payload.data or {}
    fields = data.get("fields") or []
    if not isinstance(fields, list):
        raise HTTPException(422, "invalid Tally fields payload")
    lookup = _tally_lookup(fields)
    metadata = {
        "tally_event_id": payload.eventId,
        "tally_event_type": payload.eventType,
        "tally_created_at": payload.createdAt,
        "tally_response_id": data.get("responseId"),
        "tally_submission_id": data.get("submissionId"),
        "tally_respondent_id": data.get("respondentId"),
        "tally_form_id": data.get("formId"),
        "tally_form_name": data.get("formName"),
        "tally_submission_pdf_url": data.get("submissionPdfUrl"),
        "tally_submission_preview_url": data.get("submissionPreviewUrl"),
    }
    company_domain = _string_value(_first_value(lookup, "company_domain", "domain", "website", "company website"))
    return LeadIntake(
        email=_string_value(_first_value(lookup, "email", "work_email", "business_email", "email_address")),
        full_name=_string_value(_first_value(lookup, "full_name", "name", "your_name")),
        job_title=_string_value(_first_value(lookup, "job_title", "role", "title")),
        company=_string_value(_first_value(lookup, "company", "company_name", "organization")),
        company_domain=company_domain,
        phone=_string_value(_first_value(lookup, "phone", "phone_number", "whatsapp", "whatsapp_number")),
        country=_string_value(_first_value(lookup, "country", "location", "market")),
        source=_string_value(_first_value(lookup, "source")) or "popup_offer",
        source_detail=_string_value(_first_value(lookup, "source_detail", "offer", "quiz_type")) or (data.get("formName") or "tally_popup"),
        campaign_id=_string_value(_first_value(lookup, "campaign_id")),
        post_id=_string_value(_first_value(lookup, "post_id")),
        utm_source=_string_value(_first_value(lookup, "utm_source")),
        utm_medium=_string_value(_first_value(lookup, "utm_medium")),
        utm_campaign=_string_value(_first_value(lookup, "utm_campaign")),
        message=_string_value(_first_value(lookup, "message", "notes", "details", "biggest_issue")),
        consent=_bool_value(_first_value(lookup, "consent", "privacy_consent", "marketing_consent")),
        metadata={k: v for k, v in metadata.items() if v is not None},
    )


def verify_founder_action(x_founder_action_token: str | None = Header(default=None)) -> None:
    _check_secret(
        x_founder_action_token,
        "SALES_ACTION_TOKEN",
        403,
        "founder action token is missing or invalid",
    )


def _raise_lusha_gateway_error(exc: LushaError, prefix: str) -> None:
    if exc.status == 403:
        raise HTTPException(
            502,
            f"{prefix}: Lusha blocked this request upstream. The dropdown/input is valid; this needs a provider-side access fix or a fallback provider.",
        ) from exc
    raise HTTPException(502, f"{prefix}: {exc}") from exc


@intake_router.post("/leads/website")
def website_intake(
    payload: LeadIntake,
    x_company_core_lead_secret: str | None = Header(
        default=None,
        alias="X-Company-Core-Lead-Secret",
    ),
):
    _check_intake_secret(x_company_core_lead_secret)
    if not payload.email and not payload.phone:
        raise HTTPException(422, "email or phone is required")
    lead, created = upsert_lead(payload.model_dump())
    auto_contact = maybe_auto_contact_lead(lead["id"], created=created)
    return {"ok": True, "lead_id": lead["id"], "created": created, "stage": lead["stage"], "auto_contact": auto_contact}


@intake_router.post("/leads/tally")
async def tally_intake(
    request: Request,
    x_company_core_lead_secret: str | None = Header(
        default=None,
        alias="X-Company-Core-Lead-Secret",
    ),
    tally_signature: str | None = Header(default=None),
):
    _check_intake_secret(x_company_core_lead_secret)
    raw_payload = await request.body()
    text_payload = raw_payload.decode("utf-8")
    _verify_tally_signature(text_payload, tally_signature)
    try:
        payload = TallyWebhookPayload.model_validate(json.loads(text_payload))
    except json.JSONDecodeError as exc:
        raise HTTPException(422, "invalid Tally JSON payload") from exc
    lead_payload = _lead_intake_from_tally(payload)
    if not lead_payload.email and not lead_payload.phone:
        raise HTTPException(422, "email or phone is required")
    lead, created = upsert_lead(lead_payload.model_dump())
    auto_contact = maybe_auto_contact_lead(lead["id"], created=created)
    return {
        "ok": True,
        "provider": "tally",
        "lead_id": lead["id"],
        "created": created,
        "stage": lead["stage"],
        "auto_contact": auto_contact,
    }


@intake_router.post("/email/sales")
def inbound_email(payload: InboundEmailPayload, x_sales_email_webhook_secret: str | None = Header(default=None)):
    _check_email_webhook_secret(x_sales_email_webhook_secret)
    try:
        return ingest_inbound_email(payload.model_dump())
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@intake_router.post("/resend/sales")
async def resend_sales_webhook(request: Request):
    raw_payload = await request.body()
    try:
        verified = verify_resend_webhook(raw_payload.decode("utf-8"), dict(request.headers))
        return ingest_resend_event(verified)
    except ValueError as exc:
        raise HTTPException(401, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(502, str(exc)) from exc


@router.get("/doctor")
def doctor():
    checks = {
        "hunter": bool(os.getenv("HUNTER_API_KEY")),
        "apollo": bool(os.getenv("APOLLO_API_KEY")),
        "prospeo": bool(os.getenv("PROSPEO_API_KEY")),
        "lusha": bool(os.getenv("LUSHA_API_KEY")),
        "company_enrich": bool(os.getenv("CE_API_KEY") or os.getenv("PDL_API_KEY")),
        "tally_api": bool(os.getenv("TALLY_API_KEY")),
        "tally_webhook_secret": bool(os.getenv("TALLY_WEBHOOK_SECRET")),
        "lead_intake": bool(os.getenv("SALES_INTAKE_SECRET")),
        "founder_action_token": bool(os.getenv("SALES_ACTION_TOKEN")),
        "outbound_email": bool(os.getenv("SALES_RESEND_API_KEY") and os.getenv("SALES_FROM_EMAIL") and os.getenv("SALES_RESEND_DOMAIN")),
        "inbound_replies": bool((os.getenv("SALES_EMAIL_WEBHOOK_SECRET") or os.getenv("SALES_RESEND_WEBHOOK_SECRET")) and os.getenv("SALES_REPLY_TO_EMAIL")),
    }
    return {"ok": all(checks.values()), "checks": checks, "approval_required": True, "bulk_send": False}


@router.get("/leads")
def leads(limit: int = 50, stage: str | None = None):
    return {"summary": summary(), "leads": list_leads(limit, stage)}


@router.get("/leads/{lead_id}")
def lead(lead_id: str):
    value = get_lead(lead_id)
    if not value:
        raise HTTPException(404, "lead not found")
    return value


@action_router.post("/leads", dependencies=[Depends(verify_founder_action)])
def manual_lead(payload: LeadIntake):
    lead, created = upsert_lead({**payload.model_dump(), "source": payload.source or "manual"})
    return {"lead": lead, "created": created}


@action_router.post("/prospect/domain", dependencies=[Depends(verify_founder_action)])
def prospect_domain(payload: ProspectRequest):
    try:
        provider = payload.provider.lower().strip()
        return {
            "ok": True,
            "provider": provider,
            "results": import_domain(payload.domain.lower().strip(), payload.limit, provider),
        }
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(502, f"prospecting failed: {exc}") from exc


@action_router.post("/research/suggestions", dependencies=[Depends(verify_founder_action)])
def research_suggestions(payload: LeadResearchSuggestionsRequest):
    query = payload.query.strip()
    kind = payload.kind.strip().lower()
    provider = payload.provider.strip().lower()
    try:
        if provider == "lusha":
            suggestions = research_lusha_suggestions(kind=kind, query=query)
        elif provider == "prospeo":
            suggestions = research_prospeo_suggestions(kind=kind, query=query)
        else:
            raise HTTPException(422, "unsupported research provider")
        return {"ok": True, "provider": provider, "kind": kind, "options": suggestions}
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except ProspeoError as exc:
        raise HTTPException(502, f"prospeo suggestions failed: {exc}") from exc
    except LushaError as exc:
        _raise_lusha_gateway_error(exc, "lusha suggestions failed")


@action_router.post("/research/leads", dependencies=[Depends(verify_founder_action)])
def research_leads(payload: LeadResearchRequest):
    try:
        summary = research_market_leads(
            industry=payload.industry,
            location=payload.location,
            limit_per_provider=payload.limit_per_provider,
        )
        return {
            "ok": True,
            "provider": "multi",
            "results": summary["results"],
            "providers": summary["providers"],
            "warnings": summary["warnings"],
            "mode": summary.get("mode", "company_first"),
            "contact_enrichment": summary.get("contact_enrichment", "on_demand"),
            "requested": summary["requested"],
        }
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(502, f"lead research failed: {exc}") from exc


@action_router.post("/leads/{lead_id}/resolve-company", dependencies=[Depends(verify_founder_action)])
def resolve_lead_company(lead_id: str, provider: str = "auto", force: bool = False):
    try:
        return resolve_company_profile(lead_id, provider=provider, force=force)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    except CompanyEnrichError as exc:
        raise HTTPException(502, f"company profile resolution failed: {exc}") from exc
    except Exception as exc:
        raise HTTPException(502, f"company profile resolution failed: {exc}") from exc


@action_router.post("/leads/{lead_id}/resolve-contact", dependencies=[Depends(verify_founder_action)])
def resolve_lead_contact(lead_id: str, provider: str = "prospeo", force: bool = False):
    try:
        return resolve_contact(lead_id, provider=provider, force=force)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(502, f"contact resolution failed: {exc}") from exc


@action_router.post("/leads/{lead_id}/enrich", dependencies=[Depends(verify_founder_action)])
def enrich(lead_id: str, provider: str = "hunter"):
    try:
        return enrich_lead(lead_id, provider=provider)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(502, f"enrichment failed: {exc}") from exc


@action_router.post("/leads/{lead_id}/draft-preview", dependencies=[Depends(verify_founder_action)])
async def draft_preview(lead_id: str):
    try:
        return await preview_draft(lead_id)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(502, f"draft preview failed: {exc}") from exc


@action_router.post("/leads/{lead_id}/draft", dependencies=[Depends(verify_founder_action)])
async def draft(lead_id: str):
    try:
        return await build_draft(lead_id)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@action_router.post("/drafts/{draft_id}/update", dependencies=[Depends(verify_founder_action)])
def update_saved_draft(draft_id: str, payload: DraftUpdatePayload):
    try:
        return update_draft(draft_id, subject=payload.subject, body=payload.body)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@action_router.post("/drafts/{draft_id}/approve", dependencies=[Depends(verify_founder_action)])
def approve(draft_id: str):
    try:
        return approve_draft(draft_id)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@action_router.post("/drafts/{draft_id}/send", dependencies=[Depends(verify_founder_action)])
def send(draft_id: str):
    try:
        return send_approved(draft_id)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(502, f"email send failed: {exc}") from exc


@action_router.post("/leads/{lead_id}/schedule-meeting", dependencies=[Depends(verify_founder_action)])
def schedule_meeting(lead_id: str, payload: MeetingScheduledPayload):
    try:
        lead = mark_meeting_scheduled(lead_id, scheduled_for=payload.scheduled_for, note=payload.note)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    when = lead.get("metadata", {}).get("meeting", {}).get("scheduled_for")
    message = f"Meeting marked as scheduled for {when}." if when else "Meeting marked as scheduled."
    return {"ok": True, "lead_id": lead_id, "stage": lead["stage"], "lead": lead, "message": message}


@action_router.post("/leads/{lead_id}/suppress", dependencies=[Depends(verify_founder_action)])
def suppress(lead_id: str, reason: str = "founder_request"):
    if not get_lead(lead_id):
        raise HTTPException(404, "lead not found")
    suppress_lead(lead_id, reason)
    return {"ok": True, "lead_id": lead_id, "stage": "suppressed"}



@router.get("/hunter/account")
def hunter_account():
    try:
        data = HunterClient().account()
        return {"ok": True, "calls": data.get("calls"), "reset_date": data.get("reset_date")}
    except Exception as exc:
        raise HTTPException(502, f"Hunter account check failed: {exc}") from exc
