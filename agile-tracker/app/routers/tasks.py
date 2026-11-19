from typing import Optional

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import current_user
from ..models import Task, User, utcnow
from ..schemas import TaskCreate, TaskOut, TaskStatus, TaskUpdate
from ..services import (apply_updates, ensure_project_active, ensure_user_exists, get_story, get_task,
                        next_position, notify_if_newly_assigned)

router = APIRouter(tags=["Tasks"])


@router.get("/api/tasks/mine", response_model=list[TaskOut])
def my_tasks(include_done: bool = False, db: Session = Depends(get_db), user: User = Depends(current_user)):
    """Everything assigned to me, across all projects (soonest due date first)."""
    stmt = select(Task).where(Task.assignee_id == user.id)
    if not include_done:
        stmt = stmt.where(Task.status != "done")
    return db.scalars(stmt.order_by(Task.due_date.is_(None), Task.due_date, Task.id)).all()


@router.get("/api/stories/{story_id}/tasks", response_model=list[TaskOut])
def list_tasks(story_id: int, status: Optional[TaskStatus] = None, assignee_id: Optional[int] = None,
               db: Session = Depends(get_db), _: User = Depends(current_user)):
    get_story(db, story_id)
    stmt = select(Task).where(Task.story_id == story_id).order_by(Task.position, Task.id)
    if status:
        stmt = stmt.where(Task.status == status)
    if assignee_id is not None:
        stmt = stmt.where(Task.assignee_id == assignee_id)
    return db.scalars(stmt).all()


@router.post("/api/stories/{story_id}/tasks", response_model=TaskOut, status_code=201)
def create_task(story_id: int, body: TaskCreate, db: Session = Depends(get_db), user: User = Depends(current_user)):
    story = get_story(db, story_id)
    ensure_project_active(db, story.project_id)
    ensure_user_exists(db, body.assignee_id)
    task = Task(story_id=story_id, position=next_position(db, Task, story_id=story_id), **body.model_dump())
    db.add(task)
    db.flush()
    notify_if_newly_assigned(db, task, None, user)  # job row committed atomically with the task
    db.commit()
    return task


@router.get("/api/tasks/{task_id}", response_model=TaskOut)
def read_task(task_id: int, db: Session = Depends(get_db), _: User = Depends(current_user)):
    return get_task(db, task_id)


@router.patch("/api/tasks/{task_id}", response_model=TaskOut)
def update_task(task_id: int, body: TaskUpdate, db: Session = Depends(get_db), user: User = Depends(current_user)):
    task = get_task(db, task_id)
    story = get_story(db, task.story_id)
    ensure_project_active(db, story.project_id)
    data = body.model_dump(exclude_unset=True)
    ensure_user_exists(db, data.get("assignee_id"))

    previous_assignee = task.assignee_id
    if "status" in data and data["status"] != task.status:
        task.completed_at = utcnow() if data["status"] == "done" else None
        # Workflow rule: starting work on a task pulls a backlog story into "in_progress".
        if data["status"] in ("in_progress", "done") and story.status == "backlog":
            story.status = "in_progress"
    apply_updates(task, data)
    notify_if_newly_assigned(db, task, previous_assignee, user)
    db.commit()
    return task


@router.delete("/api/tasks/{task_id}", status_code=204)
def delete_task(task_id: int, db: Session = Depends(get_db), _: User = Depends(current_user)):
    task = get_task(db, task_id)
    ensure_project_active(db, get_story(db, task.story_id).project_id)
    db.delete(task)
    db.commit()
