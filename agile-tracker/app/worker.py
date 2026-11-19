"""Background job system: durable DB-backed queue + in-process async worker.

Guarantees (see README "Async workflow"):
  * at-least-once execution  -> handlers are idempotent (dedupe keys)
  * retries with exponential backoff + jitter, then a dead-letter state ("dead")
  * crash recovery           -> jobs stuck in "running" are re-queued
  * atomic claiming          -> safe even if several workers run
"""
import asyncio
import json
import logging
import random
import time
from datetime import date, timedelta
from typing import Any, Callable, Optional

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from . import config
from .database import SessionLocal
from .models import Job, Notification, Project, Story, Task, User, utcnow

log = logging.getLogger("worker")


class PermanentError(Exception):
    """Raise from a handler when retrying cannot help (goes straight to 'dead')."""


# --------------------------------------------------------------------------- enqueue
def enqueue(db: Session, job_type: str, payload: Optional[dict] = None, *, project_id: Optional[int] = None,
            created_by: Optional[int] = None, unique_key: Optional[str] = None, delay: float = 0) -> Job:
    """Add a job to the caller's transaction (caller commits)."""
    job = Job(
        type=job_type, payload=json.dumps(payload or {}), project_id=project_id, created_by=created_by,
        unique_key=unique_key, max_attempts=config.JOB_MAX_ATTEMPTS,
        run_at=utcnow() + timedelta(seconds=delay),
    )
    db.add(job)
    db.flush()
    return job


def enqueue_unique(job_type: str, unique_key: str, payload: Optional[dict] = None) -> Optional[int]:
    """Idempotent enqueue (used by the scheduler). Returns job id, or None if it already exists."""
    with SessionLocal() as db:
        if db.scalar(select(Job.id).where(Job.unique_key == unique_key)):
            return None
        try:
            job = enqueue(db, job_type, payload, unique_key=unique_key)
            db.commit()
            return job.id
        except IntegrityError:  # another worker won the race
            db.rollback()
            return None


# --------------------------------------------------------------------------- handlers
def deliver(notification: Notification) -> None:
    """Pretend to send an e-mail. Fails randomly when DELIVERY_FAILURE_RATE > 0 (demo of retries)."""
    if random.random() < config.DELIVERY_FAILURE_RATE:
        raise ConnectionError("simulated e-mail provider outage")
    log.info("e-mail sent to user %s: %s", notification.user_id, notification.message)


def handle_notify_assignment(db: Session, job: Job) -> dict:
    p = json.loads(job.payload)
    task = db.get(Task, p["task_id"])
    if task is None or task.assignee_id != p["user_id"]:
        return {"skipped": "task deleted or re-assigned before the job ran"}

    key = f"assign:job:{job.id}"  # same key on every retry -> never duplicated
    note = db.scalar(select(Notification).where(Notification.dedupe_key == key))
    if note is None:
        actor = db.get(User, p.get("actor_id"))
        who = actor.name if actor else "Someone"
        note = Notification(user_id=p["user_id"], type="task_assigned", task_id=task.id, dedupe_key=key,
                            message=f"{who} assigned you the task \"{task.title}\"")
        db.add(note)
        db.commit()  # in-app notification is saved even if the e-mail step fails below

    if note.delivered_at is None:
        deliver(note)  # may raise -> job is retried; only this step is repeated
        note.delivered_at = utcnow()
        db.commit()
    return {"notification_id": note.id}


def handle_due_scan(db: Session, job: Job) -> dict:
    today = date.today()
    horizon = today + timedelta(days=1)
    tasks = db.scalars(
        select(Task).where(Task.status != "done", Task.assignee_id.is_not(None),
                           Task.due_date.is_not(None), Task.due_date <= horizon)
    ).all()
    created = 0
    for t in tasks:
        key = f"due:{t.id}:{t.due_date.isoformat()}:{t.assignee_id}"
        if db.scalar(select(Notification.id).where(Notification.dedupe_key == key)):
            continue
        when = "is overdue" if t.due_date < today else ("is due today" if t.due_date == today else "is due tomorrow")
        db.add(Notification(user_id=t.assignee_id, type="task_due", task_id=t.id, dedupe_key=key,
                            message=f"Task \"{t.title}\" {when} ({t.due_date.isoformat()})"))
        created += 1
    db.commit()
    return {"scanned": len(tasks), "notifications_created": created}


def handle_project_report(db: Session, job: Job) -> dict:
    project = db.scalar(select(Project).where(Project.id == job.project_id)
                        .options(selectinload(Project.stories).selectinload(Story.tasks)))
    if project is None:
        raise PermanentError("project no longer exists")
    names = {u.id: u.name for u in db.scalars(select(User))}
    tasks = [t for s in project.stories for t in s.tasks]
    today = date.today()

    def count(items, attr, values):
        return {v: sum(1 for i in items if getattr(i, attr) == v) for v in values}

    workload: dict[str, dict] = {}
    for t in tasks:
        who = names.get(t.assignee_id, "Unassigned")
        row = workload.setdefault(who, {"user": who, "open_tasks": 0, "done_tasks": 0})
        row["done_tasks" if t.status == "done" else "open_tasks"] += 1

    points_total = sum(s.points or 0 for s in project.stories)
    points_done = sum(s.points or 0 for s in project.stories if s.status == "done")
    return {
        "project": {"id": project.id, "name": project.name, "status": project.status},
        "generated_at": utcnow().isoformat(timespec="seconds") + "Z",
        "completion_percent": project.progress,
        "stories": {"total": len(project.stories),
                    "by_status": count(project.stories, "status", ["backlog", "in_progress", "done"])},
        "tasks": {"total": len(tasks), "by_status": count(tasks, "status", ["todo", "in_progress", "done"]),
                  "unassigned": sum(1 for t in tasks if t.assignee_id is None)},
        "story_points": {"total": points_total, "done": points_done},
        "overdue_tasks": [{"id": t.id, "title": t.title, "due_date": t.due_date.isoformat(),
                           "assignee": names.get(t.assignee_id, "Unassigned")}
                          for t in tasks if t.due_date and t.due_date < today and t.status != "done"],
        "workload": sorted(workload.values(), key=lambda r: -r["open_tasks"]),
    }


HANDLERS: dict[str, Callable[[Session, Job], Any]] = {
    "notify_assignment": handle_notify_assignment,
    "due_soon_scan": handle_due_scan,
    "project_report": handle_project_report,
}


# --------------------------------------------------------------------------- engine
def claim_next(db: Session) -> Optional[Job]:
    """Atomically claim the oldest due job (UPDATE ... WHERE status='pending' is the lock)."""
    now = utcnow()
    candidate = db.scalar(select(Job.id).where(Job.status == "pending", Job.run_at <= now)
                          .order_by(Job.run_at, Job.id).limit(1))
    if candidate is None:
        return None
    res = db.execute(update(Job).where(Job.id == candidate, Job.status == "pending")
                     .values(status="running", locked_at=now, attempts=Job.attempts + 1, updated_at=now))
    db.commit()
    return db.get(Job, candidate) if res.rowcount == 1 else None


def _record_failure(db: Session, job_id: int, exc: Exception) -> None:
    job = db.get(Job, job_id)
    job.last_error = f"{type(exc).__name__}: {exc}"[:500]
    job.locked_at = None
    if isinstance(exc, PermanentError) or job.attempts >= job.max_attempts:
        job.status = "dead"  # dead-letter: kept for inspection, never retried automatically
        log.error("job %s (%s) is dead after %s attempt(s): %s", job.id, job.type, job.attempts, job.last_error)
    else:
        backoff = config.JOB_BACKOFF_BASE_SECONDS * 2 ** (job.attempts - 1)       # 2s, 4s, 8s ...
        backoff += random.uniform(0, config.JOB_BACKOFF_BASE_SECONDS / 2)          # jitter
        job.status = "pending"
        job.run_at = utcnow() + timedelta(seconds=backoff)
        log.warning("job %s (%s) failed (attempt %s/%s), retry in %.1fs: %s",
                    job.id, job.type, job.attempts, job.max_attempts, backoff, job.last_error)
    db.commit()


def run_one() -> bool:
    """Claim and execute a single job. Returns False when the queue has nothing due."""
    with SessionLocal() as db:
        job = claim_next(db)
        if job is None:
            return False
        job_id = job.id
        try:
            handler = HANDLERS.get(job.type)
            if handler is None:
                raise PermanentError(f"unknown job type {job.type!r}")
            result = handler(db, job)
            job = db.get(Job, job_id)
            job.status, job.locked_at, job.last_error = "succeeded", None, None
            job.result = json.dumps(result) if result is not None else None
            db.commit()
        except Exception as exc:  # noqa: BLE001 - any handler error goes through retry policy
            db.rollback()
            _record_failure(db, job_id, exc)
        return True


def maintenance() -> None:
    """Crash recovery + periodic scheduling. Cheap and idempotent."""
    cutoff = utcnow() - timedelta(seconds=config.STALE_LOCK_SECONDS)
    with SessionLocal() as db:
        db.execute(update(Job).where(Job.status == "running", Job.locked_at < cutoff)
                   .values(status="pending", locked_at=None))
        db.commit()
    window = int(time.time() // config.DUE_SCAN_INTERVAL_SECONDS)
    enqueue_unique("due_soon_scan", f"due-scan:{window}")


async def worker_loop(stop: asyncio.Event) -> None:
    log.info("background worker started")
    last_maintenance = 0.0
    while not stop.is_set():
        did_work = False
        try:
            if time.monotonic() - last_maintenance > 30:
                await asyncio.to_thread(maintenance)
                last_maintenance = time.monotonic()
            did_work = await asyncio.to_thread(run_one)  # DB work off the event loop
        except Exception:  # noqa: BLE001 - the loop must never die
            log.exception("worker tick failed")
        if not did_work:
            try:
                await asyncio.wait_for(stop.wait(), timeout=config.WORKER_POLL_SECONDS)
            except asyncio.TimeoutError:
                pass
    log.info("background worker stopped")
