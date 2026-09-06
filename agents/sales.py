from __future__ import annotations

import os
from typing import Literal

from pydantic import BaseModel, Field
from pydantic_ai import Agent

from core.models import cloud_model


class OutreachDraft(BaseModel):
    subject: str = Field(min_length=3, max_length=90)
    body: str = Field(min_length=30, max_length=1400)
    rationale: str = Field(min_length=3, max_length=300)
    call_to_action: Literal["reply", "walkthrough"] = "reply"


class SalesCommand(BaseModel):
    action: Literal["status", "prospect_domain", "sync_inbound"] = "status"
    domain: str | None = None
    limit: int = Field(default=5, ge=1, le=10)


COMPANY_NAME = os.getenv("COMPANY_NAME", "Company Core").strip() or "Company Core"
COMPANY_DESCRIPTION = (
    os.getenv(
        "COMPANY_PRODUCT_DESCRIPTION",
        "the configured company's products and services",
    ).strip()
    or "the configured company's products and services"
)


sales_writer = Agent(
    cloud_model("fast"),
    instructions=(
        f"You draft concise, specific B2B outreach for {COMPANY_NAME}: {COMPANY_DESCRIPTION}. "
        "Use only the supplied "
        "lead facts and company context. Never invent familiarity, metrics, customers, or product capabilities. "
        "If company_context is present, use at most one grounded operational observation from it. "
        "Prefer direct contact outreach over generic copy. Use plain English and one low-pressure CTA. "
        "Do not use fake urgency, tracking claims, or manipulative language. Keep the email under 150 words."
    ),
    output_type=OutreachDraft,
)

sales_router = Agent(
    cloud_model("fast"),
    instructions=(
        "Convert a founder Sales request into one bounded action. Use prospect_domain only when "
        "a specific company domain is supplied. Use sync_inbound for requests to fetch website "
        "or campaign form submissions. Otherwise return status. Never infer or invent a domain."
    ),
    output_type=SalesCommand,
)


async def draft_outreach(lead: dict) -> OutreachDraft:
    safe = {
        key: lead.get(key)
        for key in (
            "full_name", "job_title", "company", "company_domain", "country",
            "source", "source_detail", "campaign_id", "message", "lead_score",
            "contact_profile", "company_context",
        )
    }
    latest = lead.get("latest_interaction") or {}
    if latest.get("direction") == "inbound":
        safe["latest_reply"] = {
            "subject": latest.get("subject"),
            "body": (latest.get("body") or "")[:3000],
        }
    result = await sales_writer.run(
        "Draft an email for this lead. If latest_reply is present, answer it directly and preserve "
        "the conversation context. Otherwise, if the lead is inbound, acknowledge their "
        "request directly. If company_context is present, ground one observation in its operational focus, "
        "specialties, or pain points. If sourced through Hunter, open with a relevant operational hypothesis "
        "and make it clear this is an introduction. Lead facts:\n" + repr(safe)
    )
    return result.output


async def prepare_sales_command(request: str) -> SalesCommand:
    result = await sales_router.run(request)
    return result.output
