"""Small domain helpers shared by the routers (business rules live here)."""
from typing import Any, Optional

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from .models import Project, Story, Task, User
from .worker import enqueue

NULLABLE_FIELDS = {"assignee_id", "points", "due_date"}  # PATCH with null clears these


def apply_updates(obj: Any, data: dict) -> None:
    """Apply a partial update. Explicit null only clears nullable fields."""
    for key, value in data.items():
        if value is None and key not in NULLABLE_FIELDS:
            continue
        setattr(obj, key, value)


def ensure_user_exists(db: Session, user_id: Optional[int]) -> None:
    if user_id is not None and db.get(User, user_id) is None:
        raise HTTPException(422, f"assignee_id {user_id} does not exist")


def get_project(db: Session, project_id: int, *, with_tree: bool = False) -> Project:
    stmt = select(Project).where(Project.id == project_id)
    if with_tree:
        stmt = stmt.options(selectinload(Project.stories).selectinload(Story.tasks))
    project = db.scalar(stmt)
    if project is None:
        raise HTTPException(404, "Project not found")
    return project


def get_story(db: Session, story_id: int) -> Story:
    story = db.scalar(select(Story).where(Story.id == story_id).options(selectinload(Story.tasks)))
    if story is None:
        raise HTTPException(404, "Story not found")
    return story


def get_task(db: Session, task_id: int) -> Task:
    task = db.get(Task, task_id)
    if task is None:
        raise HTTPException(404, "Task not found")
    return task


def ensure_project_active(db: Session, project_id: int) -> Project:
    project = get_project(db, project_id)
    if project.status == "archived":
        raise HTTPException(409, "Project is archived; un-archive it to add or change work items")
    return project


def next_position(db: Session, model, **filters) -> int:
    stmt = select(func.coalesce(func.max(model.position), -1) + 1)
    for column, value in filters.items():
        stmt = stmt.where(getattr(model, column) == value)
    return db.scalar(stmt)


def notify_if_newly_assigned(db: Session, task: Task, previous_assignee: Optional[int], actor: User) -> None:
    """Outbox pattern: the job row is written in the SAME transaction as the change."""
    if task.assignee_id and task.assignee_id != previous_assignee and task.assignee_id != actor.id:
        enqueue(
            db, "notify_assignment",
            {"task_id": task.id, "user_id": task.assignee_id, "actor_id": actor.id},
            created_by=actor.id,
        )
