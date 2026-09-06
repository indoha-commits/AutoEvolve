from __future__ import annotations
import json, os, subprocess, time, uuid
from datetime import datetime, timezone
from pathlib import Path
import httpx

from core.ops_store import upsert_incident
from agents.coding_orchestrator import execute_task, apply_task, load_workspace, validations

ROOT = Path(__file__).resolve().parents[1]
SERVICES_CONFIG = ROOT / "config" / "services.json"

def now_iso():
    return datetime.now(timezone.utc).isoformat()

def load_services():
    if not SERVICES_CONFIG.exists():
        return {}
    return json.loads(SERVICES_CONFIG.read_text())

def shell(cmd: str, timeout=60):
    p = subprocess.run(cmd, shell=True, text=True, capture_output=True, timeout=timeout)
    return {"command":cmd,"ok":p.returncode==0,"returncode":p.returncode,
            "stdout":p.stdout[-10000:],"stderr":p.stderr[-10000:]}

def health(service: dict) -> dict:
    url = service.get("health_url")
    if not url:
        return {"ok": False, "error":"no health_url"}
    try:
        r = httpx.get(url, timeout=service.get("health_timeout", 8))
        expected = service.get("expected_status", 200)
        return {"ok": r.status_code == expected, "status_code": r.status_code, "body": r.text[:1000]}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}

def evidence(service: dict, h: dict) -> dict:
    commands = service.get("diagnostic_commands", [])
    return {
        "health": h,
        "diagnostics": [shell(c, 60) for c in commands],
        "captured_at": now_iso(),
    }

def _looks_code_related(ev: dict, service: dict) -> bool:
    text = json.dumps(ev).lower()
    patterns = [p.lower() for p in service.get(
        "code_error_patterns",
        ["traceback","keyerror","typeerror","referenceerror","syntaxerror","module not found"]
    )]
    return any(p in text for p in patterns)

def handle_failure(name: str, service: dict, h: dict) -> dict:
    incident_id = "inc_" + datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:6]
    inc = {
        "id": incident_id, "service": name,
        "severity": service.get("severity","medium"),
        "status": "diagnosing",
        "trigger": f"health check failed: {h}",
        "created_at": now_iso(),
    }
    ev = evidence(service, h)
    inc["evidence"] = ev
    upsert_incident(inc)

    # Tier 0: deterministic recovery first.
    recovery = service.get("recovery_command")
    if recovery:
        rec = shell(recovery, service.get("recovery_timeout",60))
        time.sleep(service.get("post_recovery_wait",2))
        after = health(service)
        inc["evidence"]["deterministic_recovery"] = rec
        inc["evidence"]["health_after_recovery"] = after
        if rec["ok"] and after["ok"]:
            inc["status"] = "resolved"
            inc["resolution"] = {"tier":0,"action":recovery}
            upsert_incident(inc)
            return inc

    # Tier 1/2: AI diagnosis + isolated code candidate.
    if service.get("workspace") and (_looks_code_related(ev, service) or service.get("ai_repair_on_unknown", True)):
        inc["status"] = "repairing"
        upsert_incident(inc)
        request = f"""
INCIDENT {incident_id} for service {name}.

Health failure:
{json.dumps(h)}

Evidence:
{json.dumps(ev)[:16000]}

Diagnose the root cause and make the smallest safe project-local repair.
Do not deploy or change production configuration. Produce an isolated candidate
that passes the workspace validation commands.
""".strip()
        task = execute_task(
            service["workspace"], request,
            source="monitor", incident=inc
        )
        inc["repair_task_id"] = task["id"]

        if task["status"] == "ready_for_review":
            # Canary is deterministic and optional.
            canary = [shell(c, 180) for c in service.get("canary_commands", [])]
            canary_ok = all(c["ok"] for c in canary)
            inc["evidence"]["canary"] = canary

            if canary_ok and service.get("allow_auto_promote", False) and task.get("risk","low") == "low":
                applied = apply_task(task["id"])
                if applied["status"] == "applied":
                    workspace = load_workspace(service["workspace"])
                    post = validations(workspace, workspace["path"])
                    post_ok = all(x["ok"] for x in post)
                    inc["evidence"]["post_apply_validation"] = post
                    if post_ok:
                        inc["status"] = "resolved"
                        inc["resolution"] = {
                            "tier":2,
                            "task_id":task["id"],
                            "action":"isolated patch validated and auto-applied"
                        }
                    else:
                        inc["status"] = "needs_approval"
                        inc["resolution"] = {"reason":"post-apply validation failed; manual rollback/review required"}
                else:
                    inc["status"] = "needs_approval"
                    inc["resolution"] = {"reason":"patch apply conflict"}
            else:
                inc["status"] = "candidate_ready"
                inc["resolution"] = {
                    "task_id": task["id"],
                    "canary_ok": canary_ok,
                    "auto_promote": service.get("allow_auto_promote", False),
                }
        else:
            inc["status"] = "needs_approval"
            inc["resolution"] = {"reason":"AI candidate did not validate", "task_id":task["id"]}

    else:
        inc["status"] = "needs_approval"
        inc["resolution"] = {"reason":"no safe deterministic or scoped AI repair path"}

    upsert_incident(inc)
    return inc

def check_service(name: str) -> dict:
    services = load_services()
    if name not in services:
        raise ValueError(f"unknown service {name}")
    service = services[name]
    h = health(service)
    if h["ok"]:
        return {"service":name,"status":"healthy","health":h}
    return handle_failure(name, service, h)

def check_all() -> list[dict]:
    return [check_service(name) for name in load_services()]
