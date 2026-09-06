from __future__ import annotations
import json, os, re, subprocess
from pathlib import Path
from typing import Any

ERROR_RE = re.compile(r"(?P<file>[\w@./-]+\.(?:ts|tsx|js|jsx|py))(?::|\()(?P<line>\d+)?")

def run(cmd: str, cwd: str, timeout: int = 120) -> dict[str, Any]:
    try:
        p = subprocess.run(cmd, cwd=cwd, shell=True, text=True, capture_output=True, timeout=timeout)
        return {"command": cmd, "ok": p.returncode == 0, "returncode": p.returncode,
                "stdout": p.stdout[-12000:], "stderr": p.stderr[-12000:]}
    except subprocess.TimeoutExpired:
        return {"command": cmd, "ok": False, "returncode": None, "stdout": "", "stderr": "timeout"}

def validation_error_count(results: list[dict]) -> int:
    text = "\n".join((r.get("stdout","") + "\n" + r.get("stderr","")) for r in results if not r.get("ok"))
    # TS errors are usually one "error TSxxxx" per diagnostic.
    ts = len(re.findall(r"\berror TS\d+:", text))
    if ts:
        return ts
    return sum(1 for r in results if not r.get("ok"))

def error_files(results: list[dict]) -> list[str]:
    files = set()
    for r in results:
        text = r.get("stdout","") + "\n" + r.get("stderr","")
        for m in ERROR_RE.finditer(text):
            files.add(m.group("file"))
    return sorted(files)

def file_excerpt(repo: str, relative: str, line: int | None = None, radius: int = 30) -> str:
    p = Path(repo) / relative
    if not p.exists() or not p.is_file():
        return ""
    try:
        lines = p.read_text(errors="ignore").splitlines()
    except Exception:
        return ""
    if line:
        start = max(0, line-radius-1); end = min(len(lines), line+radius)
    else:
        start, end = 0, min(len(lines), 120)
    return "\n".join(f"{i+1:>5} {lines[i]}" for i in range(start, end))

def collect_repo_map(repo: str, task: str, validation: list[dict] | None = None) -> dict:
    validation = validation or []
    status = run("git status --short", repo, 30)
    log = run("git --no-pager log -5 --oneline", repo, 30)
    files = run("git ls-files | head -n 350", repo, 30)
    package = run("cat package.json 2>/dev/null || true", repo, 30)
    tsconfig = run("cat tsconfig.json 2>/dev/null || true", repo, 30)

    diagnostics = []
    combined = "\n".join(r.get("stdout","") + "\n" + r.get("stderr","") for r in validation)
    seen = set()
    for m in ERROR_RE.finditer(combined):
        rel = m.group("file")
        if rel in seen: 
            continue
        seen.add(rel)
        diagnostics.append({
            "file": rel,
            "line": int(m.group("line")) if m.group("line") else None,
            "excerpt": file_excerpt(repo, rel, int(m.group("line")) if m.group("line") else None)
        })
        if len(diagnostics) >= 12:
            break

    return {
        "task": task,
        "git_status": status["stdout"],
        "recent_commits": log["stdout"],
        "files": files["stdout"],
        "package_json": package["stdout"][-6000:],
        "tsconfig": tsconfig["stdout"][-5000:],
        "validation_error_count": validation_error_count(validation),
        "validation_error_files": error_files(validation),
        "diagnostics": diagnostics,
    }
