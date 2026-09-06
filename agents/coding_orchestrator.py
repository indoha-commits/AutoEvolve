from __future__ import annotations
import json, os, re, subprocess, uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import SecretStr
from openhands.sdk import Agent, Conversation, LLM, Tool
from openhands.tools.file_editor import FileEditorTool
from openhands.tools.terminal import TerminalTool
from openhands.tools.task_tracker import TaskTrackerTool

try:
    from openhands.sdk.context.condenser import LLMSummarizingCondenser
except Exception:
    LLMSummarizingCondenser = None

from core.ops_store import upsert_task, get_task
from tools.omniroute_client import complete_json
from tools.repo_intelligence import collect_repo_map, run, validation_error_count, error_files
from services import worktrees

ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_CONFIG = ROOT / "config" / "code_workspaces.json"
CONVERSATIONS = ROOT / "data" / "coding_v05_conversations"
CONVERSATIONS.mkdir(parents=True, exist_ok=True)

MODEL_MAP = {
    "explore": os.getenv("CODING_MODEL_EXPLORE", "auto/best-coding-fast"),
    "architect": os.getenv("CODING_MODEL_ARCHITECT", "auto/best-reasoning"),
    "edit": os.getenv("CODING_MODEL_EDIT", "auto/best-coding"),
    "repair": os.getenv("CODING_MODEL_REPAIR", "auto/best-reasoning"),
}

def now_iso():
    return datetime.now(timezone.utc).isoformat()

def load_workspace(alias: str) -> dict:
    data = json.loads(WORKSPACE_CONFIG.read_text())
    if alias not in data:
        raise ValueError(f"unknown workspace {alias}")
    w = dict(data[alias])
    w["alias"] = alias
    w["path"] = str(Path(w["path"]).expanduser().resolve())
    return w

def validations(workspace: dict, cwd: str) -> list[dict]:
    commands = workspace.get("validation", [])
    if isinstance(commands, str):
        commands = [commands]
    return [run(cmd, cwd, 300) for cmd in commands]

def _openhands_agent(model: str) -> Agent:
    llm = LLM(
        model=model if model.startswith("openai/") else f"openai/{model}",
        api_key=SecretStr(os.environ["CODING_API_KEY"]),
        base_url=os.getenv("CODING_BASE_URL","http://127.0.0.1:20128/v1"),
        usage_id="company-coding-v05",
    )
    kwargs = {}
    if LLMSummarizingCondenser is not None:
        kwargs["condenser"] = LLMSummarizingCondenser(llm=llm, max_size=160, keep_first=4)
    return Agent(
        llm=llm,
        tools=[Tool(name=TerminalTool.name), Tool(name=FileEditorTool.name), Tool(name=TaskTrackerTool.name)],
        **kwargs
    )

def architect_plan(task: str, repo_map: dict, incident: dict | None = None) -> dict:
    system = """You are the read-only Architect for a coding system.
Return ONLY JSON. Diagnose root cause before proposing edits.
Do not invent files. Prefer the smallest causally justified patch.
If evidence is insufficient, set decision=need_more_evidence.
Schema:
{
 "decision":"edit|need_more_evidence|no_change",
 "root_cause":"...",
 "confidence":0.0,
 "files_to_inspect":[],
 "files_allowed_to_edit":[],
 "steps":[],
 "risk":"low|medium|high",
 "validation_focus":[]
}"""
    payload = {"task": task, "repo_map": repo_map, "incident": incident or {}}
    return complete_json(MODEL_MAP["architect"], system, json.dumps(payload, ensure_ascii=False))

def editor_prompt(task: str, plan: dict, repo_map: dict, repair: bool = False) -> str:
    allowed = plan.get("files_allowed_to_edit", [])
    return f"""
You are the {'bounded Repair Editor' if repair else 'Build Editor'}.

TASK:
{task}

ARCHITECT PLAN:
{json.dumps(plan, indent=2)}

EVIDENCE:
{json.dumps(repo_map, indent=2)[:18000]}

RULES:
- Work only in this git worktree.
- Do not push, merge, deploy, commit, change credentials, production DB/schema, or external services.
- Never edit files outside the architect's allowed edit list unless a validation error directly proves one additional file is necessary.
- Never disable type checking/tests or add broad `any` casts to silence errors.
- Never change tsconfig/package/vite config unless the architect explicitly listed that file and explained why.
- Use terminal commands for grep/find/search; FileEditor is only for view/create/str_replace/insert/undo_edit.
- Verify file paths exist before editing.
- Make the smallest correct patch and stop.
""".strip()

def run_editor(worktree: str, task_id: str, task: str, plan: dict, repo_map: dict, model: str, repair=False):
    conv = Conversation(
        agent=_openhands_agent(model),
        workspace=worktree,
        persistence_dir=str(CONVERSATIONS),
        conversation_id=uuid.uuid4(),
    )
    conv.send_message(editor_prompt(task, plan, repo_map, repair=repair))
    conv.run()

def _blast_radius_guard(before_errors: int, after_errors: int, changed: list[str]) -> tuple[bool,str]:
    if before_errors <= 0:
        return True, ""
    high = {"tsconfig.json","package.json","vite.config.ts","vite.config.js","pyproject.toml"}
    if high.intersection(changed) and after_errors > before_errors:
        return False, "high-blast-radius config change increased validation errors"
    if after_errors > max(before_errors + 3, int(before_errors * 1.25)):
        return False, f"validation errors grew from {before_errors} to {after_errors}"
    return True, ""

def execute_task(
    workspace_alias: str,
    request: str,
    *,
    source: str = "founder",
    incident: dict | None = None,
    model_override: str | None = None,
) -> dict:
    workspace = load_workspace(workspace_alias)
    task_id = "code_" + datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:6]
    task = {
        "id": task_id, "workspace": workspace_alias, "request": request,
        "source": source, "status": "diagnosing", "risk": "low",
        "created_at": now_iso(), "repair_history": [],
    }
    upsert_task(task)

    wt = worktrees.create(workspace["path"], task_id)
    task.update({
        "worktree_path": wt["path"], "branch": wt["branch"],
        "base_commit": wt["base_commit"],
    })
    upsert_task(task)

    pre_validation = validations(workspace, wt["path"])
    repo_map = collect_repo_map(wt["path"], request, pre_validation)
    plan = architect_plan(request, repo_map, incident)
    task["architect_plan"] = plan
    task["risk"] = plan.get("risk","medium")

    if plan.get("decision") == "need_more_evidence":
        task["status"] = "needs_review"
        task["reason"] = "architect requested more evidence"
        upsert_task(task)
        return task
    if plan.get("decision") == "no_change":
        task["status"] = "verified_no_changes"
        upsert_task(task)
        return task

    task["status"] = "editing"
    upsert_task(task)
    run_editor(
        wt["path"], task_id, request, plan, repo_map,
        model_override or MODEL_MAP["edit"], repair=False
    )

    after = validations(workspace, wt["path"])
    before_count = validation_error_count(pre_validation)
    after_count = validation_error_count(after)
    changed = worktrees.changed_files(wt["path"])

    safe, reason = _blast_radius_guard(before_count, after_count, changed)
    if not safe:
        task["status"] = "rolled_back_bad_patch"
        task["reason"] = reason
        task["validation"] = after
        task["changed_files"] = changed
        upsert_task(task)
        worktrees.discard(workspace["path"], task_id, wt["branch"])
        return task

    # At most 2 bounded repair transactions. Controller owns the loop.
    cycle = 0
    while not all(x["ok"] for x in after) and cycle < 2:
        cycle += 1
        before_cycle = after
        before_cycle_count = validation_error_count(before_cycle)
        repo_map = collect_repo_map(wt["path"], request, before_cycle)
        repair_plan = architect_plan(
            "Repair ONLY the validation failures after the requested patch. " + request,
            repo_map, incident
        )
        if repair_plan.get("decision") != "edit":
            break

        # Snapshot worktree diff so this cycle can be reverted without touching
        # the main checkout.
        patch_before = run("git --no-pager diff --binary", wt["path"], 60)["stdout"]
        run_editor(
            wt["path"], task_id, request, repair_plan, repo_map,
            MODEL_MAP["repair"], repair=True
        )
        candidate = validations(workspace, wt["path"])
        candidate_count = validation_error_count(candidate)
        cycle_changed = worktrees.changed_files(wt["path"])
        safe, reason = _blast_radius_guard(before_cycle_count, candidate_count, cycle_changed)

        hist = {
            "cycle": cycle,
            "before_error_count": before_cycle_count,
            "after_error_count": candidate_count,
            "changed_files": cycle_changed,
            "accepted": safe and candidate_count <= before_cycle_count,
            "reason": reason,
        }

        if not hist["accepted"]:
            # Reset only the worktree to task base then re-apply pre-cycle diff.
            run("git reset --hard HEAD && git clean -fd", wt["path"], 60)
            if patch_before.strip():
                tmp = Path(wt["path"]) / ".company-precycle.patch"
                tmp.write_text(patch_before)
                run(f"git apply {str(tmp)!r}", wt["path"], 60)
                tmp.unlink(missing_ok=True)
            task["repair_history"].append(hist)
            break

        after = candidate
        task["repair_history"].append(hist)

        if candidate_count == before_cycle_count:
            break

    changed = worktrees.changed_files(wt["path"])
    task["validation"] = after
    task["validation_error_count"] = validation_error_count(after)
    task["validation_error_files"] = error_files(after)
    task["changed_files"] = changed
    task["patch_path"] = worktrees.make_patch(wt["path"], task_id)
    task["model"] = model_override or MODEL_MAP["edit"]
    task["category"] = "incident_repair" if incident else "coding"

    if all(x["ok"] for x in after):
        task["status"] = "ready_for_review"
    else:
        task["status"] = "needs_review"

    upsert_task(task)
    return task

def apply_task(task_id: str) -> dict:
    task = get_task(task_id)
    if not task:
        raise ValueError("unknown task")
    if task.get("status") not in {"ready_for_review","verified_candidate"}:
        raise ValueError(f"task not applicable from status {task.get('status')}")
    workspace = load_workspace(task["workspace"])
    result = worktrees.apply_patch(workspace["path"], task["patch_path"])
    task["apply_result"] = result
    task["status"] = "applied" if result["ok"] else "apply_conflict"
    upsert_task(task)
    return task
