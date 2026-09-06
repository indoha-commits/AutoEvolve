import json
import re
from datetime import datetime, timezone
from pathlib import Path

from core.state import (
    create_project as create_project_record,
    get_project_by_slug,
    set_active_project,
)


PROJECTS_ROOT = (
    Path(__file__).parent.parent
    / "projects"
)


def now_iso() -> str:
    return datetime.now(
        timezone.utc
    ).isoformat()


def slugify(text: str) -> str:
    value = text.lower().strip()

    value = re.sub(
        r"[^a-z0-9]+",
        "-",
        value,
    )

    return value.strip("-")[:60]


def ensure_project(
    name: str,
) -> tuple[dict, Path]:
    """
    Create or reuse a project.

    Returns:
        database project record
        filesystem project path
    """

    slug = slugify(name)

    existing = get_project_by_slug(
        slug
    )

    if existing:
        project_record = existing

    else:
        project_record = (
            create_project_record(
                name=name,
                slug=slug,
            )
        )

    project_path = (
        PROJECTS_ROOT / slug
    )

    for folder in [
        "research",
        "product",
        "code",
        "marketing",
        "sales",
        "media",
        "logs",
    ]:
        (
            project_path / folder
        ).mkdir(
            parents=True,
            exist_ok=True,
        )

    metadata_file = (
        project_path / "project.json"
    )

    if not metadata_file.exists():
        metadata_file.write_text(
            json.dumps(
                {
                    "id": project_record["id"],
                    "name": project_record["name"],
                    "slug": project_record["slug"],
                    "status": project_record["status"],
                    "created_at": project_record[
                        "created_at"
                    ],
                },
                indent=2,
            )
        )

    set_active_project(
        project_record["id"]
    )

    return (
        project_record,
        project_path,
    )


def save_research(
    project_path: Path,
    report: dict,
) -> Path:

    timestamp = datetime.now(
        timezone.utc
    ).strftime(
        "%Y%m%d-%H%M%S"
    )

    destination = (
        project_path
        / "research"
        / f"research-{timestamp}.json"
    )

    data = json.dumps(
        report,
        indent=2,
        ensure_ascii=False,
    )

    destination.write_text(data)

    latest = (
        project_path
        / "research"
        / "latest.json"
    )

    latest.write_text(data)

    return destination
