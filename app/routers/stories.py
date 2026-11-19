from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from ..database import get_db
from ..deps import current_user
from ..models import Story, User
from ..schemas import Priority, StoryCreate, StoryDetail, StoryOut, StoryStatus, StoryUpdate
from ..services import (apply_updates, ensure_project_active, ensure_user_exists, get_project, get_story,
                        next_position)

router = APIRouter(tags=["User stories"])


@router.get("/api/projects/{project_id}/stories", response_model=list[StoryOut])
def list_stories(project_id: int, status: Optional[StoryStatus] = None, priority: Optional[Priority] = None,
                 assignee_id: Optional[int] = None, db: Session = Depends(get_db),
                 _: User = Depends(current_user)):
    """List a project's stories, optionally filtered by status / priority / assignee."""
    get_project(db, project_id)
    stmt = (select(Story).where(Story.project_id == project_id).options(selectinload(Story.tasks))
            .order_by(Story.position, Story.id))
    if status:
        stmt = stmt.where(Story.status == status)
    if priority:
        stmt = stmt.where(Story.priority == priority)
    if assignee_id is not None:
        stmt = stmt.where(Story.assignee_id == assignee_id)
    return db.scalars(stmt).all()


@router.post("/api/projects/{project_id}/stories", response_model=StoryDetail, status_code=201)
def create_story(project_id: int, body: StoryCreate, db: Session = Depends(get_db),
                 _: User = Depends(current_user)):
    ensure_project_active(db, project_id)
    ensure_user_exists(db, body.assignee_id)
    story = Story(project_id=project_id, position=next_position(db, Story, project_id=project_id),
                  **body.model_dump())
    db.add(story)
    db.commit()
    return get_story(db, story.id)


@router.get("/api/stories/{story_id}", response_model=StoryDetail)
def read_story(story_id: int, db: Session = Depends(get_db), _: User = Depends(current_user)):
    return get_story(db, story_id)


@router.patch("/api/stories/{story_id}", response_model=StoryDetail)
def update_story(story_id: int, body: StoryUpdate, db: Session = Depends(get_db),
                 _: User = Depends(current_user)):
    story = get_story(db, story_id)
    ensure_project_active(db, story.project_id)
    data = body.model_dump(exclude_unset=True)
    ensure_user_exists(db, data.get("assignee_id"))
    # Business rule: a story can only be "done" when every task under it is done.
    if data.get("status") == "done":
        open_tasks = [t for t in story.tasks if t.status != "done"]
        if open_tasks:
            raise HTTPException(409, f"Cannot mark story as done: {len(open_tasks)} task(s) still open")
    apply_updates(story, data)
    db.commit()
    return get_story(db, story_id)


@router.delete("/api/stories/{story_id}", status_code=204)
def delete_story(story_id: int, db: Session = Depends(get_db), _: User = Depends(current_user)):
    story = get_story(db, story_id)
    ensure_project_active(db, story.project_id)
    db.delete(story)
    db.commit()
