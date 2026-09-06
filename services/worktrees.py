from __future__ import annotations
import shutil, subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKTREES = ROOT / "data" / "coding_worktrees"
PATCHES = ROOT / "data" / "coding_patches"

def run(cmd: str, cwd: str, timeout: int = 120):
    return subprocess.run(cmd, cwd=cwd, shell=True, text=True, capture_output=True, timeout=timeout)

def create(repo: str, task_id: str) -> dict:
    WORKTREES.mkdir(parents=True, exist_ok=True)
    path = WORKTREES / task_id
    branch = f"agent/{task_id}"
    base = run("git rev-parse HEAD", repo, 30)
    if base.returncode != 0:
        raise RuntimeError(base.stderr)
    base_commit = base.stdout.strip()

    if path.exists():
        shutil.rmtree(path, ignore_errors=True)

    p = run(f"git worktree add -b {branch} {str(path)!r} {base_commit}", repo, 120)
    if p.returncode != 0:
        raise RuntimeError(f"worktree create failed: {p.stderr}")
    return {"path": str(path), "branch": branch, "base_commit": base_commit}

def make_patch(worktree: str, task_id: str) -> str:
    PATCHES.mkdir(parents=True, exist_ok=True)
    patch = PATCHES / f"{task_id}.patch"
    p = run("git --no-pager diff --binary", worktree, 60)
    patch.write_text(p.stdout)
    return str(patch)

def changed_files(worktree: str) -> list[str]:
    p = run("git status --short", worktree, 30)
    out = []
    for line in p.stdout.splitlines():
        if len(line) >= 4:
            out.append(line[3:].strip())
    return sorted(set(out))

def discard(repo: str, task_id: str, branch: str) -> None:
    path = WORKTREES / task_id
    if path.exists():
        run(f"git worktree remove --force {str(path)!r}", repo, 120)
    run(f"git branch -D {branch}", repo, 60)

def apply_patch(repo: str, patch_path: str) -> dict:
    # Do not overwrite a dirty file silently. git apply --3way either applies
    # or returns a conflict/error.
    p = run(f"git apply --3way {patch_path!r}", repo, 120)
    return {"ok": p.returncode == 0, "stdout": p.stdout, "stderr": p.stderr}
