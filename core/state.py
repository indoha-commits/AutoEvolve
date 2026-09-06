import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


DB_PATH = (
    Path(__file__).parent.parent
    / "data"
    / "company.db"
)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    return conn


def init_db() -> None:
    with connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS projects (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                slug TEXT NOT NULL UNIQUE,
                status TEXT NOT NULL DEFAULT 'idea',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id INTEGER,
                agent TEXT NOT NULL,
                task_type TEXT NOT NULL,
                priority TEXT NOT NULL DEFAULT 'normal',
                status TEXT NOT NULL DEFAULT 'queued',
                input TEXT,
                output TEXT,
                error TEXT,
                requires_approval INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,

                FOREIGN KEY(project_id)
                    REFERENCES projects(id)
            );

            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT
            );
            """
        )


def create_project(
    name: str,
    slug: str,
) -> dict:
    timestamp = now_iso()

    with connect() as conn:
        existing = conn.execute(
            """
            SELECT *
            FROM projects
            WHERE slug = ?
            """,
            (slug,),
        ).fetchone()

        if existing:
            return dict(existing)

        cursor = conn.execute(
            """
            INSERT INTO projects (
                name,
                slug,
                status,
                created_at,
                updated_at
            )
            VALUES (?, ?, 'idea', ?, ?)
            """,
            (
                name,
                slug,
                timestamp,
                timestamp,
            ),
        )

        project_id = cursor.lastrowid

        row = conn.execute(
            """
            SELECT *
            FROM projects
            WHERE id = ?
            """,
            (project_id,),
        ).fetchone()

        return dict(row)


def get_project_by_slug(
    slug: str,
) -> Optional[dict]:
    with connect() as conn:
        row = conn.execute(
            """
            SELECT *
            FROM projects
            WHERE slug = ?
            """,
            (slug,),
        ).fetchone()

        return dict(row) if row else None


def get_project(
    project_id: int,
) -> Optional[dict]:
    with connect() as conn:
        row = conn.execute(
            """
            SELECT *
            FROM projects
            WHERE id = ?
            """,
            (project_id,),
        ).fetchone()

        return dict(row) if row else None


def set_active_project(
    project_id: int,
) -> None:
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO settings (
                key,
                value
            )
            VALUES (
                'active_project_id',
                ?
            )
            ON CONFLICT(key)
            DO UPDATE SET
                value = excluded.value
            """,
            (str(project_id),),
        )


def get_active_project() -> Optional[dict]:
    with connect() as conn:
        setting = conn.execute(
            """
            SELECT value
            FROM settings
            WHERE key = 'active_project_id'
            """
        ).fetchone()

        if not setting:
            return None

        project = conn.execute(
            """
            SELECT *
            FROM projects
            WHERE id = ?
            """,
            (int(setting["value"]),),
        ).fetchone()

        return dict(project) if project else None


def create_task(
    *,
    project_id: Optional[int],
    agent: str,
    task_type: str,
    input_text: str,
    priority: str = "normal",
    requires_approval: bool = False,
) -> dict:
    timestamp = now_iso()

    with connect() as conn:
        cursor = conn.execute(
            """
            INSERT INTO tasks (
                project_id,
                agent,
                task_type,
                priority,
                status,
                input,
                requires_approval,
                created_at,
                updated_at
            )
            VALUES (?, ?, ?, ?, 'queued', ?, ?, ?, ?)
            """,
            (
                project_id,
                agent,
                task_type,
                priority,
                input_text,
                int(requires_approval),
                timestamp,
                timestamp,
            ),
        )

        task_id = cursor.lastrowid

        row = conn.execute(
            """
            SELECT *
            FROM tasks
            WHERE id = ?
            """,
            (task_id,),
        ).fetchone()

        return dict(row)


def update_task_status(
    task_id: int,
    status: str,
) -> None:
    with connect() as conn:
        conn.execute(
            """
            UPDATE tasks
            SET
                status = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (
                status,
                now_iso(),
                task_id,
            ),
        )


def complete_task(
    task_id: int,
    output: str,
) -> None:
    with connect() as conn:
        conn.execute(
            """
            UPDATE tasks
            SET
                status = 'done',
                output = ?,
                error = NULL,
                updated_at = ?
            WHERE id = ?
            """,
            (
                output,
                now_iso(),
                task_id,
            ),
        )


def fail_task(
    task_id: int,
    error: str,
) -> None:
    with connect() as conn:
        conn.execute(
            """
            UPDATE tasks
            SET
                status = 'failed',
                error = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (
                error,
                now_iso(),
                task_id,
            ),
        )


def get_recent_tasks(
    limit: int = 20,
) -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT
                tasks.*,
                projects.name AS project_name,
                projects.slug AS project_slug
            FROM tasks
            LEFT JOIN projects
                ON projects.id = tasks.project_id
            ORDER BY tasks.id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()

        return [
            dict(row)
            for row in rows
        ]
