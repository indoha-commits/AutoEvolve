from __future__ import annotations

import argparse
import json
import subprocess
import sys
import traceback
from pathlib import Path
from typing import Any

from core.marketing_config import MarketingConfig
from core.state import now_iso
from services.marketing_worker import _run


ROOT = Path(__file__).parent.parent.resolve()
PROJECTS_ROOT = (ROOT / "projects").resolve()


def _record_path(project_slug: str, pack_id: str) -> Path:
    return PROJECTS_ROOT / project_slug / "marketing" / "asset_packs" / pack_id / "asset_pack.json"


def _read_record(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise RuntimeError(f"asset pack record not found: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def _write_record(path: Path, record: dict[str, Any], **updates: Any) -> dict[str, Any]:
    value = {**record, **updates, "updated_at": now_iso()}
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(path)
    return value


def run_asset_pack(project_slug: str, pack_id: str) -> None:
    from services.asset_scene_planner import plan_script_scenes

    record_path = _record_path(project_slug, pack_id)
    root = record_path.parent
    record = _read_record(record_path)
    config = MarketingConfig.load()
    script_path = Path(record["source_script_path"])
    plan_path = Path(record["package_path"])
    search_path = Path(record["search_path"])
    asset_root = Path(record["asset_root"])

    record = _write_record(record_path, record, status="planning", error=None)
    plan = plan_script_scenes(script_path.read_text(encoding="utf-8"))
    plan_path.write_text(json.dumps(plan, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    record = _write_record(
        record_path,
        record,
        status="searching",
        scene_count=len(plan["scenes"]),
        scenes=plan["scenes"],
        planning_source=plan["source"],
        planner_error=plan["planner_error"],
        input_word_count=plan["word_count"],
        input_paragraph_count=plan["paragraph_count"],
        target_duration_seconds=plan["target_duration_seconds"],
        duration_source=plan["duration_source"],
        target_scene_count=plan["target_scene_count"],
        returned_scene_count=plan["returned_scene_count"],
    )
    _run(
        [
            str(config.g2_bin), "search-scene-images", str(plan_path),
            "--output", str(search_path),
            "--deadline", str(min(config.media_deadline, 8.0)),
            "--limit", str(min(config.media_limit, 4)),
        ],
        cwd=config.g2_root,
        env_file=config.g2_env_file,
        timeout=config.stage_timeout,
        log_path=root / "logs" / "g2_search.log",
    )
    search_result = json.loads(search_path.read_text(encoding="utf-8"))
    selected_count = sum(bool(scene.get("selected")) for scene in search_result.get("scenes", []))
    if selected_count == 0:
        raise RuntimeError("Pexels returned no matching background images")
    record = _write_record(record_path, record, status="downloading")
    _run(
        [
            str(config.g2_bin), "acquire-media", str(search_path),
            "--output", str(asset_root), "--workers", "3", "--timeout", "20",
        ],
        cwd=config.g2_root,
        env_file=config.g2_env_file,
        timeout=config.stage_timeout,
        log_path=root / "logs" / "g2_acquire.log",
    )
    manifest_path = asset_root / "asset_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.is_file() else []
    downloaded_count = len(manifest)
    if downloaded_count == 0:
        raise RuntimeError("Pexels images were selected but none could be downloaded")
    _write_record(
        record_path,
        record,
        status="ready" if downloaded_count == len(plan["scenes"]) else "partial",
        downloaded_count=downloaded_count,
        missing_count=max(0, len(plan["scenes"]) - downloaded_count),
        error=None,
    )


def spawn_asset_pack(project_slug: str, pack_id: str) -> None:
    subprocess.Popen(
        [sys.executable, "-m", "services.asset_pack_worker", project_slug, pack_id],
        cwd=ROOT,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("project_slug")
    parser.add_argument("pack_id")
    args = parser.parse_args()
    try:
        run_asset_pack(args.project_slug, args.pack_id)
        return 0
    except Exception as exc:
        path = _record_path(args.project_slug, args.pack_id)
        try:
            record = _read_record(path)
            _write_record(
                path,
                record,
                status="failed",
                error=f"{type(exc).__name__}: {exc}",
                traceback=traceback.format_exc(limit=8),
            )
        except Exception:
            pass
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
