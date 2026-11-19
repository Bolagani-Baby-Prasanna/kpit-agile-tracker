import json
import time
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import config
from ..database import get_db
from ..deps import current_user
from ..models import Job, User
from ..schemas import JobOut
from ..services import get_project
from ..worker import enqueue

router = APIRouter(tags=["Background jobs & reports"])


def _out(job: Job) -> JobOut:
    return JobOut(id=job.id, type=job.type, status=job.status, attempts=job.attempts,
                  max_attempts=job.max_attempts, run_at=job.run_at, last_error=job.last_error,
                  result=json.loads(job.result) if job.result else None, project_id=job.project_id,
                  created_at=job.created_at, updated_at=job.updated_at)


@router.post("/api/projects/{project_id}/reports", response_model=JobOut, status_code=202)
def request_report(project_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    """Queue a project status report. Returns immediately (202); poll GET /api/jobs/{id}."""
    get_project(db, project_id)
    job = enqueue(db, "project_report", project_id=project_id, created_by=user.id)
    db.commit()
    return _out(job)


@router.get("/api/projects/{project_id}/reports", response_model=list[JobOut])
def list_reports(project_id: int, db: Session = Depends(get_db), _: User = Depends(current_user)):
    get_project(db, project_id)
    jobs = db.scalars(select(Job).where(Job.project_id == project_id, Job.type == "project_report")
                      .order_by(Job.id.desc()).limit(20)).all()
    return [_out(j) for j in jobs]


@router.get("/api/jobs", response_model=list[JobOut])
def list_my_jobs(status: Optional[str] = None, db: Session = Depends(get_db), user: User = Depends(current_user)):
    """Jobs I triggered - handy for inspecting retries and dead-lettered jobs."""
    stmt = select(Job).where(Job.created_by == user.id).order_by(Job.id.desc()).limit(50)
    if status:
        stmt = stmt.where(Job.status == status)
    return [_out(j) for j in db.scalars(stmt)]


@router.get("/api/jobs/{job_id}", response_model=JobOut)
def get_job(job_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    job = db.get(Job, job_id)
    # Reports are team-visible (projects are shared); other jobs only to whoever triggered them.
    if job is None or (job.created_by != user.id and job.type != "project_report"):
        raise HTTPException(404, "Job not found")
    return _out(job)


@router.post("/api/jobs/due-scan", response_model=JobOut, status_code=202)
def trigger_due_scan(db: Session = Depends(get_db), user: User = Depends(current_user)):
    """Run the due-date reminder scan now (it also runs automatically every hour)."""
    job = enqueue(db, "due_soon_scan", unique_key=f"due-scan:manual:{time.time_ns()}", created_by=user.id)
    db.commit()
    return _out(job)
