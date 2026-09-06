import json

import os
from pathlib import Path
from typing import Literal, Optional

from pydantic import BaseModel
from pydantic_ai import Agent

from agents.researcher import run_research
from agents.growth import prepare_campaign_brief

from core.models import cloud_model

from core.projects import (
    ensure_project,
    save_research,
)

from core.state import (
    init_db,
    get_active_project,
    create_task,
    update_task_status,
    complete_task,
    fail_task,
)
from core.marketing_store import create_campaign
from services.marketing_worker import spawn as spawn_marketing_worker


PROMPT = (
    Path(__file__).parent.parent
    / "prompts"
    / "founder.md"
).read_text()


class FounderDecision(BaseModel):
    action: Literal[
        "answer",
        "research",
        "future_coding",
        "future_marketing",
        "future_sales",
    ]

    reasoning: str

    task: str

    response: str

    project_name: Optional[str] = None

    use_active_project: bool = False

    priority: Literal[
        "low",
        "normal",
        "high",
    ] = "normal"

    requires_approval: bool = False


founder_agent = Agent(
    cloud_model("fast"),
    instructions=PROMPT,
    output_type=FounderDecision,
)


init_db()


async def run_founder(
    message: str,
):
    active_project = (
        get_active_project()
    )

    project_context = (
        json.dumps(
            active_project,
            indent=2,
        )
        if active_project
        else "No active project."
    )

    founder_prompt = f"""
Founder request:

{message}

CURRENT ACTIVE PROJECT:

{project_context}

Rules:

- If the request clearly continues the active project,
  set use_active_project=true.

- If this is a genuinely new initiative,
  set use_active_project=false and provide a concise
  project_name.

- project_name should describe the initiative itself.

GOOD:
Document Data Analysis

BAD:
Document Data Analysis Research Report

BAD:
Research About Document Data Analysis

Do not create a new project merely because the founder
asks another question about an existing project.
"""

    decision_result = (
        await founder_agent.run(
            founder_prompt
        )
    )

    decision = (
        decision_result.output
    )

    project_record = None
    project_path = None

    if (
        decision.use_active_project
        and active_project
    ):
        project_record = active_project

        project_path = (
            Path(__file__).parent.parent
            / "projects"
            / active_project["slug"]
        )

    elif decision.project_name:
        (
            project_record,
            project_path,
        ) = ensure_project(
            decision.project_name
        )

    if decision.action == "research":

        # Research should belong to a project.
        #
        # If the Founder failed to select one,
        # create a reasonable project from the task.
        if project_record is None:
            fallback_name = (
                decision.project_name
                or "General Research"
            )

            (
                project_record,
                project_path,
            ) = ensure_project(
                fallback_name
            )

        task = create_task(
            project_id=project_record["id"],
            agent="research",
            task_type="research",
            input_text=decision.task,
            priority=decision.priority,
            requires_approval=False,
        )

        update_task_status(
            task["id"],
            "running",
        )

        try:
            report = await run_research(
                decision.task
            )

            artifact = save_research(
                project_path,
                report.model_dump(),
            )

            task_output = json.dumps(
                {
                    "artifact": str(
                        artifact
                    ),
                    "decision": (
                        report.decision
                    ),
                    "summary": (
                        report.executive_summary
                    ),
                },
                ensure_ascii=False,
            )

            complete_task(
                task["id"],
                task_output,
            )

            return {
                "type": "research",

                "project": {
                    "id": project_record["id"],
                    "name": project_record["name"],
                    "slug": project_record["slug"],
                    "path": str(
                        project_path
                    ),
                },

                "task": {
                    "id": task["id"],
                    "agent": "research",
                    "status": "done",
                    "priority": (
                        decision.priority
                    ),
                },

                "artifact": str(
                    artifact
                ),

                "founder": (
                    decision.model_dump()
                ),

                "research": (
                    report.model_dump()
                ),
            }

        except Exception as exc:
            fail_task(
                task["id"],
                f"{type(exc).__name__}: {exc}",
            )

            raise

    if decision.action == "future_marketing":
        if project_record is None:
            (
                project_record,
                project_path,
            ) = ensure_project(
                decision.project_name
                or (active_project or {}).get("name")
                or os.getenv("COMPANY_NAME", "Company Core")
            )

        brief = await prepare_campaign_brief(decision.task or message)
        task = create_task(
            project_id=project_record["id"],
            agent="growth",
            task_type="campaign",
            input_text=brief.execution_summary,
            priority=decision.priority,
            requires_approval=False,
        )
        campaign = create_campaign(
            project_id=project_record["id"],
            task_id=task["id"],
            request=message,
            objective=brief.objective,
            buyer=brief.buyer,
            topic=brief.topic,
            social_platforms=brief.social_platforms,
            video_platform=brief.video_platform,
        )
        spawn_marketing_worker(campaign["id"])
        return {
            "type": "marketing_queued",
            "project": project_record,
            "campaign": {
                "id": campaign["id"],
                "status": "queued",
                "objective": brief.objective,
                "buyer": brief.buyer,
                "topic": brief.topic,
                "social_platforms": brief.social_platforms,
                "video_platform": brief.video_platform,
            },
            "founder": decision.model_dump(),
            "message": (
                "Campaign accepted. Growth, Media, and rendering now run in the "
                "background. Video variants will appear in the campaign review panel."
            ),
        }

    return {
        "type": decision.action,

        "project": (
            project_record
            if project_record
            else active_project
        ),

        "founder": (
            decision.model_dump()
        ),
    }
