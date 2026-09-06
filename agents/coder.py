from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv
from pydantic import SecretStr

from openhands.sdk import Agent, Conversation, LLM, Tool
from openhands.tools.file_editor import FileEditorTool
from openhands.tools.task_tracker import TaskTrackerTool
from openhands.tools.terminal import TerminalTool

load_dotenv()

ROOT = Path(__file__).parent.parent
WORKSPACE_CONFIG = ROOT / "config" / "code_workspaces.json"
CONVERSATIONS_DIR = ROOT / "data" / "coding_conversations"
REPAIR_CONVERSATIONS_DIR = ROOT / "data" / "coding_repairs"
CHAT_INDEX = ROOT / "data" / "coding_chats.json"
TASKS_DIR = ROOT / "data" / "coding_tasks"
CHECKPOINTS_DIR = ROOT / "data" / "coding_checkpoints"
for path in (CONVERSATIONS_DIR, REPAIR_CONVERSATIONS_DIR, TASKS_DIR, CHECKPOINTS_DIR, CHAT_INDEX.parent):
    path.mkdir(parents=True, exist_ok=True)

DEFAULT_MODEL = os.getenv("CODING_DEFAULT_MODEL", "auto/best-coding")
CONTEXT_COMPACT_AFTER = int(os.getenv("CODING_COMPACT_AFTER_TASKS", "6"))
MAX_REPAIR_CYCLES = int(os.getenv("CODING_MAX_REPAIR_CYCLES", "3"))

CATEGORY_MODEL_MAP = {
    "quick_edit": "auto/best-coding-fast",
    "normal_code": "auto/best-coding",
    "deep_debug": "auto/best-reasoning",
    "architecture": "auto/best-reasoning",
    "review": "auto/best-reasoning",
    "global_repair": "auto/best-reasoning",
}

EXCLUDED_PREFIXES = ("node_modules/", "dist/", "build/", ".git/")
EXCLUDED_NAMES = {"package-lock.json", "pnpm-lock.yaml", "yarn.lock"}
PROTECTED_REPAIR_PREFIXES = ("supabase/migrations/", "migrations/")
PROTECTED_REPAIR_NAMES = {".env", ".env.local", ".env.production", "wrangler.toml"}
VALIDATION_FILE_RE = re.compile(r"(?P<path>(?:[A-Za-z0-9_@.-]+/)+[A-Za-z0-9_@.-]+\.(?:ts|tsx|js|jsx|mjs|cjs|py|json|yaml|yml|toml|css|scss|html|sql))(?::|\()(?P<line>\d+)?")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError:
        return default


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False))


def load_workspaces() -> dict:
    return _read_json(WORKSPACE_CONFIG, {})


def get_workspace(alias: str) -> dict:
    workspaces = load_workspaces()
    if alias not in workspaces:
        raise ValueError(f"Unknown coding workspace: {alias}")
    workspace = dict(workspaces[alias])
    path = Path(workspace["path"]).expanduser().resolve()
    if not path.exists() or not path.is_dir():
        raise ValueError(f"Workspace path is invalid: {path}")
    workspace["alias"] = alias
    workspace["path"] = str(path)
    return workspace


def load_chat_index() -> dict:
    return _read_json(CHAT_INDEX, {})


def save_chat_index(data: dict) -> None:
    _write_json(CHAT_INDEX, data)


def _migrate_chat(chat: dict) -> tuple[dict, bool]:
    changed = False
    if not chat.get("conversation_id"):
        chat["conversation_id"] = chat["id"]
        changed = True
    if "default_model" not in chat:
        old_model = chat.get("model")
        chat["default_model"] = old_model if old_model and old_model != "default" else "auto"
        changed = True
    if "default_category" not in chat:
        chat["default_category"] = "auto"
        changed = True
    if "task_count_since_compaction" not in chat:
        chat["task_count_since_compaction"] = 0
        changed = True
    if "compaction_generation" not in chat:
        chat["compaction_generation"] = 0
        changed = True
    if chat.get("model") == "default":
        chat["model"] = DEFAULT_MODEL
        changed = True
    return chat, changed


def create_chat(workspace_alias: str, name: str, default_model: str = "auto", default_category: str = "auto") -> dict:
    workspace = get_workspace(workspace_alias)
    chat_id = str(uuid.uuid4())
    chat = {
        "id": chat_id,
        "name": name,
        "workspace_alias": workspace_alias,
        "workspace_name": workspace["name"],
        "workspace_path": workspace["path"],
        "conversation_id": chat_id,
        "default_model": default_model,
        "default_category": default_category,
        "model": DEFAULT_MODEL,
        "task_count_since_compaction": 0,
        "compaction_generation": 0,
        "created_at": now_iso(),
    }
    chats = load_chat_index()
    chats[chat_id] = chat
    save_chat_index(chats)
    return chat


def get_chat(chat_id: str) -> dict:
    chats = load_chat_index()
    if chat_id not in chats:
        raise ValueError(f"Unknown coding chat: {chat_id}")
    chat, changed = _migrate_chat(dict(chats[chat_id]))
    if changed:
        chats[chat_id] = chat
        save_chat_index(chats)
    return chat


def update_chat(chat_id: str, **changes: Any) -> dict:
    chats = load_chat_index()
    if chat_id not in chats:
        raise ValueError(f"Unknown coding chat: {chat_id}")
    chat, _ = _migrate_chat(dict(chats[chat_id]))
    chat.update(changes)
    chats[chat_id] = chat
    save_chat_index(chats)
    return chat


def list_chats() -> list[dict]:
    chats = load_chat_index()
    out, dirty = [], False
    for chat_id, raw in chats.items():
        chat, changed = _migrate_chat(dict(raw))
        chats[chat_id] = chat
        out.append(chat)
        dirty = dirty or changed
    if dirty:
        save_chat_index(chats)
    return out


def list_omniroute_models() -> list[str]:
    base_url = os.getenv("CODING_BASE_URL", "http://127.0.0.1:20128/v1").rstrip("/")
    api_key = os.environ["CODING_API_KEY"]
    response = httpx.get(f"{base_url}/models", headers={"Authorization": f"Bearer {api_key}"}, timeout=20)
    response.raise_for_status()
    return sorted({item["id"] for item in response.json().get("data", []) if item.get("id")})


def search_omniroute_models(query: str) -> list[str]:
    q = query.strip().lower()
    models = list_omniroute_models()
    return models if not q else [m for m in models if q in m.lower()]


def openhands_model_name(model_id: str) -> str:
    return model_id if model_id.startswith("openai/") else f"openai/{model_id}"


def classify_task(message: str) -> str:
    text = message.lower()
    if any(k in text for k in ("architecture", "system design", "migration plan", "security model", "threat model")):
        return "architecture"
    if any(k in text for k in ("review", "audit", "inspect the diff", "code review", "regression analysis", "analyze only", "do not edit")):
        return "review"
    if any(k in text for k in ("debug", "root cause", "typecheck", "build failure", "failing test", "race condition", "cross-file", "why is", "trace")):
        return "deep_debug"
    if any(k in text for k in ("css", "spacing", "copy", "label", "tiny", "small ui", "rename", "one-line", "one line")):
        return "quick_edit"
    return "normal_code"


def resolve_category(chat: dict, message: str, override: str | None) -> str:
    if override and override != "auto":
        return override
    default_category = chat.get("default_category", "auto")
    return default_category if default_category != "auto" else classify_task(message)


def resolve_model(chat: dict, category: str, override: str | None) -> str:
    if override and override != "auto":
        return override
    default_model = chat.get("default_model", "auto")
    return default_model if default_model != "auto" else CATEGORY_MODEL_MAP.get(category, DEFAULT_MODEL)


def build_coding_agent(model_id: str) -> Agent:
    llm = LLM(
        model=openhands_model_name(model_id),
        api_key=SecretStr(os.environ["CODING_API_KEY"]),
        base_url=os.getenv("CODING_BASE_URL", "http://127.0.0.1:20128/v1"),
        usage_id="coding-agent",
    )
    return Agent(llm=llm, tools=[Tool(name=TerminalTool.name), Tool(name=FileEditorTool.name), Tool(name=TaskTrackerTool.name)])


def build_conversation(workspace_path: str, model_id: str, conversation_id: str, persistence_dir: Path) -> Conversation:
    return Conversation(
        agent=build_coding_agent(model_id),
        workspace=workspace_path,
        persistence_dir=str(persistence_dir),
        conversation_id=uuid.UUID(conversation_id),
    )


def run_command(command: str, cwd: str, timeout: int = 180) -> dict:
    try:
        result = subprocess.run(command, cwd=cwd, shell=True, text=True, capture_output=True, timeout=timeout)
        return {"command": command, "ok": result.returncode == 0, "returncode": result.returncode, "stdout": result.stdout.strip(), "stderr": result.stderr.strip()}
    except subprocess.TimeoutExpired:
        return {"command": command, "ok": False, "returncode": None, "stdout": "", "stderr": f"Command timed out after {timeout}s"}


def repo_files(repo: str) -> list[str]:
    result = run_command("git ls-files --cached --others --exclude-standard", repo, 30)
    return [line.strip() for line in result["stdout"].splitlines() if line.strip()] if result["ok"] else []


def git_status(repo: str) -> str:
    return run_command("git status --short", repo, 30)["stdout"]


def _status_files(status: str) -> list[str]:
    files = []
    for line in status.splitlines():
        if not line.strip():
            continue
        path = line[3:].strip() if len(line) > 3 else line.strip()
        if " -> " in path:
            path = path.split(" -> ", 1)[1].strip()
        files.append(path)
    return sorted(set(files))


def _quote_paths(files: list[str]) -> str:
    import shlex
    return " ".join(shlex.quote(f) for f in files)


def git_diff_stat_for_files(repo: str, files: list[str]) -> str:
    return "" if not files else run_command(f"git --no-pager diff --stat -- {_quote_paths(files)}", repo, 30)["stdout"]


def git_diff_for_files(repo: str, files: list[str]) -> str:
    return "" if not files else run_command(f"git --no-pager diff -- {_quote_paths(files)}", repo, 30)["stdout"]


def sha256_file(path: Path) -> str | None:
    try:
        if not path.exists() or not path.is_file():
            return None
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except OSError:
        return None


def snapshot_repo(repo: str) -> dict:
    root = Path(repo)
    hashes = {}
    for relative in repo_files(repo):
        path = root / relative
        if path.is_file() and path.stat().st_size <= 20 * 1024 * 1024:
            hashes[relative] = sha256_file(path)
    status = git_status(repo)
    return {"status": status, "dirty_files": _status_files(status), "hashes": hashes}


def changed_since_snapshot(repo: str, before: dict) -> list[str]:
    root = Path(repo)
    before_hashes = before.get("hashes", {})
    current_files = set(repo_files(repo))
    changed = []
    for relative in sorted(set(before_hashes) | current_files):
        previous = before_hashes.get(relative)
        path = root / relative
        current = sha256_file(path) if relative in current_files and path.exists() and path.is_file() and path.stat().st_size <= 20 * 1024 * 1024 else None
        if previous != current:
            changed.append(relative)
    return changed


def _checkpoint_dir(task_id: str) -> Path:
    return CHECKPOINTS_DIR / task_id


def create_checkpoint(task_id: str, repo: str) -> dict:
    checkpoint = _checkpoint_dir(task_id)
    files_dir = checkpoint / "files"
    files_dir.mkdir(parents=True, exist_ok=True)
    manifest = {"task_id": task_id, "repo": repo, "created_at": now_iso(), "files": {}}
    root = Path(repo)
    total_bytes, max_total = 0, 150 * 1024 * 1024
    known_files = sorted(repo_files(repo))
    for relative in known_files:
        path = root / relative
        if any(relative.startswith(p) for p in EXCLUDED_PREFIXES) or Path(relative).name in EXCLUDED_NAMES:
            continue
        if not path.exists() or not path.is_file():
            continue
        size = path.stat().st_size
        if size > 10 * 1024 * 1024 or total_bytes + size > max_total:
            continue
        dst = files_dir / relative
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, dst)
        total_bytes += size
        manifest["files"][relative] = {"existed": True, "hash": sha256_file(path), "size": size}
    manifest["known_files"] = known_files
    _write_json(checkpoint / "manifest.json", manifest)
    return manifest


def finalize_checkpoint(task_id: str, repo: str, changed_files: list[str]) -> None:
    checkpoint = _checkpoint_dir(task_id)
    manifest_path = checkpoint / "manifest.json"
    manifest = _read_json(manifest_path, {})
    root = Path(repo)
    manifest["changed_files"] = changed_files
    manifest["post_hashes"] = {relative: sha256_file(root / relative) for relative in changed_files}
    manifest["finished_at"] = now_iso()
    _write_json(manifest_path, manifest)


def task_file(task_id: str) -> Path:
    return TASKS_DIR / f"{task_id}.json"


def undo_task(task_id: str) -> dict:
    task = _read_json(task_file(task_id), None)
    if not task:
        raise ValueError(f"Unknown coding task: {task_id}")
    repo = task["workspace_path"]
    root = Path(repo)
    checkpoint = _checkpoint_dir(task_id)
    manifest = _read_json(checkpoint / "manifest.json", None)
    if not manifest:
        raise ValueError(f"No checkpoint found for task: {task_id}")
    changed_files = manifest.get("changed_files", [])
    post_hashes = manifest.get("post_hashes", {})
    conflicts = [r for r in changed_files if sha256_file(root / r) != post_hashes.get(r)]
    if conflicts:
        return {"ok": False, "status": "undo_blocked_newer_changes", "task_id": task_id, "conflicts": conflicts}
    known_before = set(manifest.get("known_files", []))
    restored, deleted = [], []
    for relative in changed_files:
        src, dst = checkpoint / "files" / relative, root / relative
        if relative in known_before and src.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            restored.append(relative)
        elif relative not in known_before and dst.exists():
            dst.unlink()
            deleted.append(relative)
    task["undo"] = {"at": now_iso(), "restored": restored, "deleted": deleted}
    task["status"] = "undone"
    _write_json(task_file(task_id), task)
    return {"ok": True, "status": "undone", "task_id": task_id, "restored": restored, "deleted": deleted}


def validation_commands(workspace: dict) -> list[str]:
    configured = workspace.get("validation", [])
    if isinstance(configured, str):
        return [configured]
    return [str(c) for c in configured if c] if isinstance(configured, list) else []


def run_validations(workspace: dict) -> list[dict]:
    return [run_command(command, workspace["path"], timeout=300) for command in validation_commands(workspace)]


def validation_passed(results: list[dict]) -> bool:
    return all(item.get("ok") for item in results)


def validation_failure_text(results: list[dict]) -> str:
    parts = []
    for item in results:
        if item.get("ok"):
            continue
        parts.append(f"$ {item['command']}\nSTDOUT:\n{item.get('stdout','')[-8000:]}\nSTDERR:\n{item.get('stderr','')[-8000:]}")
    return "\n\n".join(parts)


def validation_error_files(results: list[dict]) -> list[str]:
    files = set()
    for item in results:
        text = f"{item.get('stdout','')}\n{item.get('stderr','')}"
        for match in VALIDATION_FILE_RE.finditer(text):
            files.add(match.group("path"))
    return sorted(files)


def coding_system_context(chat: dict, task: str, category: str, model_id: str, compact_memory: str = "") -> str:
    memory_block = f"\nCOMPACTED PRIOR CONTEXT:\n{compact_memory}\n" if compact_memory else ""
    return f"""
You are the Coding Agent for the company.

CURRENT CODING CHAT: {chat['name']}
WORKSPACE: {chat['workspace_name']}
REPOSITORY: {chat['workspace_path']}
TASK CATEGORY: {category}
MODEL ROUTE: {model_id}
{memory_block}
USER REQUEST:
{task}

OPERATING RULES:
- inspect relevant files before editing
- understand existing architecture before changing it
- keep requested changes scoped to the request
- edit original files directly
- preserve existing unrelated user modifications
- verify assumptions before claiming a bug is fixed
- do not claim success solely because a file edit succeeded
- prefer the smallest correct change

REPOSITORY EXPLORATION RULES:
Do not inspect node_modules/, dist/, build/, .git internals, package-lock.json,
yarn.lock, pnpm-lock.yaml, generated binaries or large generated assets unless required.

AUTOMATICALLY ALLOWED:
- read/search project files
- inspect git status/diff
- edit project files
- run tests/build/typecheck
- install normal project-local dependencies
- inspect logs

DO NOT DO WITHOUT EXPLICIT APPROVAL:
- git push or merge
- production deploy
- production database migration
- production secret changes
- destructive deletion of important data
- Cloudflare production configuration changes
- Supabase production schema changes

If the user clearly asks you to fix or implement something, make the code changes.
Do not merely propose a solution unless the user requested analysis only.
""".strip()


def repair_prompt(task_id: str, original_request: str, validation_results: list[dict], previous_changed_files: list[str], cycle: int) -> str:
    return f"""
You are running GLOBAL REPAIR cycle {cycle} for coding task {task_id}.

ORIGINAL USER REQUEST:
{original_request}

CURRENT TASK-OWNED FILES:
{json.dumps(previous_changed_files, indent=2)}

VALIDATION ERROR FILES:
{json.dumps(validation_error_files(validation_results), indent=2)}

VALIDATION OUTPUT:
{validation_failure_text(validation_results)}

Investigate the project-local validation failures and directly repair safe,
unambiguous code defects. The controller will rerun validation.

AUTO-FIX IS ALLOWED FOR missing/renamed local imports and symbols, stale local
references after refactors, unambiguous type errors, local component interface
mismatches, obvious project-local build/test regressions, and lint/format errors.

Do not modify production database/schema/migrations, secrets/.env, Cloudflare
production configuration, external service configuration, destructive business
logic, or ambiguous product behavior. Do not hide errors, disable type checking,
add broad any casts, skip tests, or weaken validation merely to pass.
""".strip()


def recent_task_records(chat_id: str, limit: int = 6) -> list[dict]:
    records = []
    for path in TASKS_DIR.glob("*.json"):
        item = _read_json(path, None)
        if item and item.get("chat_id") == chat_id:
            records.append(item)
    records.sort(key=lambda x: x.get("created_at", ""))
    return records[-limit:]


def build_compact_memory(chat: dict) -> str:
    lines = [f"Workspace: {chat['workspace_name']}", "Recent coding task outcomes:"]
    for item in recent_task_records(chat["id"], 6):
        lines.append(f"- {item.get('category')} | {item.get('status')} | request={item.get('request','')[:220]!r} | requested_changes={item.get('requested_changed_files', [])} | repair_changes={item.get('repair_changed_files', [])} | validation={'PASS' if item.get('validation_passed') else 'FAIL'}")
    agents_md = Path(chat["workspace_path"]) / "AGENTS.md"
    if agents_md.exists():
        try:
            lines.append("\nRepository memory from AGENTS.md:\n" + agents_md.read_text(errors="ignore")[:6000])
        except OSError:
            pass
    return "\n".join(lines)


def maybe_compact_chat(chat: dict) -> tuple[dict, str]:
    if int(chat.get("task_count_since_compaction", 0)) < CONTEXT_COMPACT_AFTER:
        return chat, ""
    compact_memory = build_compact_memory(chat)
    chat = update_chat(
        chat["id"],
        conversation_id=str(uuid.uuid4()),
        task_count_since_compaction=0,
        compaction_generation=int(chat.get("compaction_generation", 0)) + 1,
        last_compacted_at=now_iso(),
    )
    return chat, compact_memory


def _is_protected_repair_file(relative: str) -> bool:
    return Path(relative).name in PROTECTED_REPAIR_NAMES or any(relative.startswith(prefix) for prefix in PROTECTED_REPAIR_PREFIXES)


def restore_file_from_task_checkpoint(task_id: str, relative: str, repo: str) -> None:
    checkpoint = _checkpoint_dir(task_id)
    manifest = _read_json(checkpoint / "manifest.json", {})
    known_before = set(manifest.get("known_files", []))
    src, dst = checkpoint / "files" / relative, Path(repo) / relative
    if relative in known_before and src.exists():
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
    elif relative not in known_before and dst.exists():
        dst.unlink()


def send_coding_message(chat_id: str, message: str, *, category_override: str | None = None, model_override: str | None = None) -> dict:
    chat = get_chat(chat_id)
    workspace = get_workspace(chat["workspace_alias"])
    chat, compact_memory = maybe_compact_chat(chat)
    category = resolve_category(chat, message, category_override)
    model_id = resolve_model(chat, category, model_override)
    task_id = f"code_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"

    before = snapshot_repo(workspace["path"])
    create_checkpoint(task_id, workspace["path"])

    task_record = {
        "task_id": task_id,
        "chat_id": chat_id,
        "chat_name": chat["name"],
        "workspace_alias": workspace["alias"],
        "workspace_name": workspace["name"],
        "workspace_path": workspace["path"],
        "request": message,
        "category": category,
        "model": model_id,
        "created_at": now_iso(),
        "status": "running",
        "preexisting_dirty_files": before["dirty_files"],
        "requested_changed_files": [],
        "repair_changed_files": [],
        "repair_history": [],
        "validation": [],
        "validation_passed": False,
        "compaction_generation": chat.get("compaction_generation", 0),
    }
    _write_json(task_file(task_id), task_record)

    conversation = build_conversation(workspace["path"], model_id, chat["conversation_id"], CONVERSATIONS_DIR)
    conversation.send_message(coding_system_context(chat, message, category, model_id, compact_memory))
    conversation.run()

    requested_changed = changed_since_snapshot(workspace["path"], before)
    task_record["requested_changed_files"] = requested_changed
    validations = run_validations(workspace)
    task_record["validation"] = validations

    all_task_changed = set(requested_changed)
    repair_changed = set()
    cycle = 0

    while validations and not validation_passed(validations) and cycle < MAX_REPAIR_CYCLES:
        cycle += 1
        repair_before = snapshot_repo(workspace["path"])
        repair_model = CATEGORY_MODEL_MAP["global_repair"]
        repair_conversation = build_conversation(workspace["path"], repair_model, str(uuid.uuid4()), REPAIR_CONVERSATIONS_DIR)
        repair_conversation.send_message(repair_prompt(task_id, message, validations, sorted(all_task_changed), cycle))
        repair_conversation.run()

        cycle_changed = changed_since_snapshot(workspace["path"], repair_before)
        blocked, allowed = [], []
        for relative in cycle_changed:
            if _is_protected_repair_file(relative):
                restore_file_from_task_checkpoint(task_id, relative, workspace["path"])
                blocked.append(relative)
            else:
                allowed.append(relative)
                repair_changed.add(relative)
                all_task_changed.add(relative)

        before_validation = validations
        validations = run_validations(workspace)
        task_record["repair_history"].append({
            "cycle": cycle,
            "model": repair_model,
            "changed_files": allowed,
            "blocked_protected_files": blocked,
            "error_files_before": validation_error_files(before_validation),
            "validation_after": validations,
            "passed": validation_passed(validations),
        })
        task_record["validation"] = validations
        _write_json(task_file(task_id), task_record)
        if not allowed and not blocked:
            break

    final_changed = sorted(all_task_changed)
    final_pass = validation_passed(validations) if validations else True
    task_record["repair_changed_files"] = sorted(repair_changed - set(requested_changed))
    task_record["all_task_changed_files"] = final_changed
    task_record["validation"] = validations
    task_record["validation_passed"] = final_pass
    task_record["finished_at"] = now_iso()
    task_record["repair_cycles"] = cycle
    task_record["status"] = ("verified_with_changes" if final_changed else "verified_no_changes") if final_pass else "needs_review"
    task_record["current_status"] = git_status(workspace["path"])
    task_record["agent_diff_stat"] = git_diff_stat_for_files(workspace["path"], final_changed)
    task_record["agent_diff"] = git_diff_for_files(workspace["path"], final_changed)

    finalize_checkpoint(task_id, workspace["path"], final_changed)
    _write_json(task_file(task_id), task_record)
    update_chat(chat_id, task_count_since_compaction=int(chat.get("task_count_since_compaction", 0)) + 1, last_task_id=task_id, last_task_at=now_iso())
    return task_record


def get_task(task_id: str) -> dict:
    task = _read_json(task_file(task_id), None)
    if not task:
        raise ValueError(f"Unknown coding task: {task_id}")
    return task


def get_last_task(chat_id: str) -> dict | None:
    chat = get_chat(chat_id)
    task_id = chat.get("last_task_id")
    return get_task(task_id) if task_id else None
