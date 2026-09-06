from __future__ import annotations
from fastapi import APIRouter, HTTPException

from app.marketing_api import _public_asset_pack, _public_campaign, _public_manual_post, doctor as marketing_doctor, list_asset_packs
from app.sales_api import doctor as sales_doctor
from core.marketing_store import list_campaigns, list_events as list_marketing_events, list_manual_posts
from core.sales_store import list_events as list_sales_events, list_interactions, list_leads, summary
from core.state import get_active_project
from pydantic import BaseModel

from agents.coding_orchestrator import execute_task, apply_task
from core.ops_store import get_task, list_tasks, get_incident, list_incidents
from services.monitor import check_service, check_all

router = APIRouter(prefix="/company", tags=["company-ops"])

class CodingTaskRequest(BaseModel):
    workspace: str
    request: str
    model: str | None = None

@router.post("/coding/tasks")
def create_coding_task(body: CodingTaskRequest):
    return execute_task(body.workspace, body.request, source="founder", model_override=body.model)

@router.get("/coding/tasks")
def coding_tasks(limit: int = 30):
    return {"tasks": list_tasks(limit)}

@router.get("/coding/tasks/{task_id}")
def coding_task(task_id: str):
    task = get_task(task_id)
    if not task:
        raise HTTPException(404, "task not found")
    return task

@router.post("/coding/tasks/{task_id}/apply")
def coding_apply(task_id: str):
    try:
        return apply_task(task_id)
    except ValueError as exc:
        raise HTTPException(409, str(exc))

@router.get("/incidents")
def incidents(limit: int = 30):
    return {"incidents": list_incidents(limit)}

@router.get("/incidents/{incident_id}")
def incident(incident_id: str):
    item = get_incident(incident_id)
    if not item:
        raise HTTPException(404, "incident not found")
    return item

@router.post("/monitor/run")
def monitor_all():
    return {"results": check_all()}

@router.post("/monitor/run/{service}")
def monitor_service(service: str):
    try:
        return check_service(service)
    except ValueError as exc:
        raise HTTPException(404, str(exc))


def _history_summary(payload: dict) -> str:
    if not isinstance(payload, dict):
        return ""
    for key in ("topic", "direction", "reason", "subject", "draft_id", "resume_stage", "status"):
        value = payload.get(key)
        if value:
            return str(value)[:200]
    return ""


def _operations_history(campaigns: list[dict], leads: list[dict]) -> list[dict]:
    lead_labels = {item["id"]: item.get("full_name") or item.get("company") or item.get("email") or item["id"] for item in leads}
    history = []
    for campaign in campaigns:
        for event in list_marketing_events(campaign["id"])[-8:]:
            history.append({
                "domain": "marketing",
                "entity_id": campaign["id"],
                "entity_label": campaign.get("topic") or campaign["id"],
                "event": event["event"],
                "payload": event.get("payload") or {},
                "summary": _history_summary(event.get("payload") or {}),
                "created_at": event["created_at"],
            })
    for event in list_sales_events(limit=40):
        history.append({
            "domain": "sales",
            "entity_id": event.get("lead_id"),
            "entity_label": lead_labels.get(event.get("lead_id"), event.get("lead_id") or "sales"),
            "event": event["event"],
            "payload": event.get("payload") or {},
            "summary": _history_summary(event.get("payload") or {}),
            "created_at": event["created_at"],
        })
    for interaction in list_interactions(limit=30):
        history.append({
            "domain": "sales",
            "entity_id": interaction.get("lead_id"),
            "entity_label": lead_labels.get(interaction.get("lead_id"), interaction.get("lead_id") or "sales"),
            "event": f"interaction.{interaction.get('direction')}.{interaction.get('kind')}",
            "payload": interaction.get("metadata") or {},
            "summary": (interaction.get("subject") or interaction.get("body") or "")[:200],
            "created_at": interaction["created_at"],
        })
    history.sort(key=lambda item: item.get("created_at") or "", reverse=True)
    return history[:80]


@router.get("/operations/overview")
def operations_overview():
    active_project = get_active_project()
    project_id = active_project["id"] if active_project else None
    campaigns = list_campaigns(limit=12, project_id=project_id)
    manual_posts = list_manual_posts(limit=24, project_id=project_id)
    asset_packs = list_asset_packs(project_slug=active_project["slug"], limit=24) if active_project else []
    leads = list_leads(limit=24)
    return {
        "active_project": active_project,
        "marketing_doctor": marketing_doctor(),
        "sales_doctor": sales_doctor(),
        "marketing_campaigns": [_public_campaign(item) for item in campaigns],
        "sales_summary": summary(),
        "sales_leads": leads,
        "manual_posts": [_public_manual_post(item) for item in manual_posts],
        "asset_packs": [_public_asset_pack(item) for item in asset_packs],
        "history": _operations_history(campaigns, leads),
    }
