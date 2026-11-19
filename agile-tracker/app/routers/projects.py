from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from ..database import get_db
from ..deps import current_user
from ..models import Project, Story, User
from ..schemas import ProjectCreate, ProjectDetail, ProjectOut, ProjectStatus, ProjectUpdate
from ..services import apply_updates, get_project

router = APIRouter(prefix="/api/projects", tags=["Projects"])


@router.get("", response_model=list[ProjectOut])
def list_projects(status: Optional[ProjectStatus] = None, db: Session = Depends(get_db),
                  _: User = Depends(current_user)):
    stmt = (select(Project).options(selectinload(Project.stories).selectinload(Story.tasks))
            .order_by(Project.created_at.desc(), Project.id.desc()))
    if status:
        stmt = stmt.where(Project.status == status)
    return db.scalars(stmt).all()


@router.post("", response_model=ProjectOut, status_code=201)
def create_project(body: ProjectCreate, db: Session = Depends(get_db), user: User = Depends(current_user)):
    project = Project(name=body.name, description=body.description, owner_id=user.id)
    db.add(project)
    db.commit()
    return get_project(db, project.id, with_tree=True)


@router.get("/{project_id}", response_model=ProjectDetail)
def get_project_tree(project_id: int, db: Session = Depends(get_db), _: User = Depends(current_user)):
    """The whole hierarchy in one call: Project -> Stories -> Tasks."""
    return get_project(db, project_id, with_tree=True)


@router.patch("/{project_id}", response_model=ProjectOut)
def update_project(project_id: int, body: ProjectUpdate, db: Session = Depends(get_db),
                   _: User = Depends(current_user)):
    project = get_project(db, project_id)
    apply_updates(project, body.model_dump(exclude_unset=True))
    db.commit()
    return get_project(db, project_id, with_tree=True)


@router.delete("/{project_id}", status_code=204)
def delete_project(project_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    project = get_project(db, project_id)
    if project.owner_id != user.id:
        raise HTTPException(403, "Only the project owner can delete a project")
    db.delete(project)  # stories + tasks removed by ON DELETE CASCADE
    db.commit()
