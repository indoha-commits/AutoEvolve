from __future__ import annotations

import os
import sys
from pathlib import Path


def record_draft(package: dict) -> dict:
    source_value = os.getenv("COMPANY_M0_SOURCE")
    root_value = os.getenv("COMPANY_M0_ROOT")
    if not source_value or not root_value:
        return {"status": "skipped", "reason": "M0 source/root not configured"}
    source, root = Path(source_value), Path(root_value)
    if not (source / "company_runtime").exists() or not (root / "config").exists():
        return {"status": "skipped", "reason": "M0 source/root path is invalid"}
    sys.path.insert(0, str(source))
    from company_runtime.models import ActionRequest, Capability
    from company_runtime.runtime import Runtime
    runtime = Runtime.from_config_dir(root / "config", root / "data" / "company_m0.sqlite3")
    task_id = runtime.create_task("growth", "company-core", f"Create campaign {package['campaign_id']}")
    action = runtime.request_action(ActionRequest(task_id, "growth", Capability.CREATE_DRAFT, "mock",
                                                   "create_campaign_package", f"campaign:{package['campaign_id']}",
                                                   {"status": package["status"]}))
    return {"status": action["status"], "task_id": task_id, "action_id": action["action_id"]}
