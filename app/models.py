"""SQLAlchemy models. Hierarchy: Project -> Story -> Task (ON DELETE CASCADE)."""
from datetime import date, datetime, timezone
from typing import Optional

from sqlalchemy import CheckConstraint, Date, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


def utcnow() -> datetime:
    """Naive UTC timestamp (SQLite has no timezone type; we always store UTC)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(254), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(80))
    password_hash: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class Project(Base):
    __tablename__ = "projects"
    __table_args__ = (CheckConstraint("status IN ('active','archived')", name="ck_project_status"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default="active")
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), index=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)

    stories: Mapped[list["Story"]] = relationship(
        back_populates="project", cascade="all, delete-orphan", passive_deletes=True,
        order_by=lambda: [Story.position, Story.id],
    )

    # Derived values (stories/tasks are eager-loaded by the routers that serialise these).
    @property
    def story_count(self) -> int:
        return len(self.stories)

    @property
    def task_count(self) -> int:
        return sum(len(s.tasks) for s in self.stories)

    @property
    def done_task_count(self) -> int:
        return sum(s.done_task_count for s in self.stories)

    @property
    def progress(self) -> int:
        return round(100 * self.done_task_count / self.task_count) if self.task_count else 0


class Story(Base):
    __tablename__ = "stories"
    __table_args__ = (
        CheckConstraint("status IN ('backlog','in_progress','done')", name="ck_story_status"),
        CheckConstraint("priority IN ('low','medium','high')", name="ck_story_priority"),
        Index("ix_stories_project_status", "project_id", "status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default="backlog")
    priority: Mapped[str] = mapped_column(String(10), default="medium")
    points: Mapped[Optional[int]] = mapped_column(default=None)
    assignee_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True, default=None
    )
    position: Mapped[int] = mapped_column(default=0)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)

    project: Mapped[Project] = relationship(back_populates="stories")
    tasks: Mapped[list["Task"]] = relationship(
        back_populates="story", cascade="all, delete-orphan", passive_deletes=True,
        order_by=lambda: [Task.position, Task.id],
    )

    @property
    def task_count(self) -> int:
        return len(self.tasks)

    @property
    def done_task_count(self) -> int:
        return sum(1 for t in self.tasks if t.status == "done")


class Task(Base):
    __tablename__ = "tasks"
    __table_args__ = (
        CheckConstraint("status IN ('todo','in_progress','done')", name="ck_task_status"),
        Index("ix_tasks_story_status", "story_id", "status"),
        Index("ix_tasks_due", "due_date"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    story_id: Mapped[int] = mapped_column(ForeignKey("stories.id", ondelete="CASCADE"))
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default="todo")
    assignee_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True, default=None
    )
    due_date: Mapped[Optional[date]] = mapped_column(Date, default=None)
    position: Mapped[int] = mapped_column(default=0)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)
    completed_at: Mapped[Optional[datetime]] = mapped_column(default=None)

    story: Mapped[Story] = relationship(back_populates="tasks")


class Notification(Base):
    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    type: Mapped[str] = mapped_column(String(30))  # task_assigned | task_due
    message: Mapped[str] = mapped_column(String(300))
    task_id: Mapped[Optional[int]] = mapped_column(ForeignKey("tasks.id", ondelete="SET NULL"), default=None)
    # Idempotency key: a retried job can never create a second copy of the same notification.
    dedupe_key: Mapped[Optional[str]] = mapped_column(String(120), unique=True, default=None)
    read: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    delivered_at: Mapped[Optional[datetime]] = mapped_column(default=None)  # "e-mail" sent


class Job(Base):
    """Durable background-job queue (a tiny transactional outbox)."""

    __tablename__ = "jobs"
    __table_args__ = (
        CheckConstraint("status IN ('pending','running','succeeded','dead')", name="ck_job_status"),
        Index("ix_jobs_status_run_at", "status", "run_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    type: Mapped[str] = mapped_column(String(40))
    payload: Mapped[str] = mapped_column(Text, default="{}")  # JSON
    status: Mapped[str] = mapped_column(String(20), default="pending")
    attempts: Mapped[int] = mapped_column(default=0)
    max_attempts: Mapped[int] = mapped_column(default=4)
    run_at: Mapped[datetime] = mapped_column(default=utcnow)  # earliest time it may run (backoff)
    locked_at: Mapped[Optional[datetime]] = mapped_column(default=None)
    last_error: Mapped[Optional[str]] = mapped_column(String(500), default=None)
    result: Mapped[Optional[str]] = mapped_column(Text, default=None)  # JSON
    unique_key: Mapped[Optional[str]] = mapped_column(String(120), unique=True, default=None)
    project_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True, default=None
    )
    created_by: Mapped[Optional[int]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), default=None
    )
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)
