"""Pydantic request/response models (validation lives here, not in the routers)."""
from datetime import date, datetime
from typing import Annotated, Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

ProjectStatus = Literal["active", "archived"]
StoryStatus = Literal["backlog", "in_progress", "done"]
TaskStatus = Literal["todo", "in_progress", "done"]
Priority = Literal["low", "medium", "high"]

Title = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
EMAIL_RE = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ---- auth / users ----
class RegisterIn(BaseModel):
    email: str = Field(pattern=EMAIL_RE, max_length=254)
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]
    password: str = Field(min_length=8, max_length=128)


class LoginIn(BaseModel):
    email: str = Field(max_length=254)
    password: str = Field(max_length=128)


class UserOut(ORM):
    id: int
    email: str
    name: str


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut


# ---- tasks ----
class TaskCreate(BaseModel):
    title: Title
    description: str = Field("", max_length=5000)
    assignee_id: Optional[int] = None
    due_date: Optional[date] = None


class TaskUpdate(BaseModel):
    title: Optional[Title] = None
    description: Optional[str] = Field(None, max_length=5000)
    status: Optional[TaskStatus] = None
    assignee_id: Optional[int] = None
    due_date: Optional[date] = None
    position: Optional[int] = Field(None, ge=0)


class TaskOut(ORM):
    id: int
    story_id: int
    title: str
    description: str
    status: TaskStatus
    assignee_id: Optional[int]
    due_date: Optional[date]
    position: int
    created_at: datetime
    updated_at: datetime
    completed_at: Optional[datetime]


# ---- stories ----
class StoryCreate(BaseModel):
    title: Title
    description: str = Field("", max_length=5000)
    priority: Priority = "medium"
    points: Optional[int] = Field(None, ge=0, le=100)
    assignee_id: Optional[int] = None


class StoryUpdate(BaseModel):
    title: Optional[Title] = None
    description: Optional[str] = Field(None, max_length=5000)
    status: Optional[StoryStatus] = None
    priority: Optional[Priority] = None
    points: Optional[int] = Field(None, ge=0, le=100)
    assignee_id: Optional[int] = None
    position: Optional[int] = Field(None, ge=0)


class StoryOut(ORM):
    id: int
    project_id: int
    title: str
    description: str
    status: StoryStatus
    priority: Priority
    points: Optional[int]
    assignee_id: Optional[int]
    position: int
    task_count: int
    done_task_count: int
    created_at: datetime
    updated_at: datetime


class StoryDetail(StoryOut):
    tasks: list[TaskOut]


# ---- projects ----
class ProjectCreate(BaseModel):
    name: Name
    description: str = Field("", max_length=2000)


class ProjectUpdate(BaseModel):
    name: Optional[Name] = None
    description: Optional[str] = Field(None, max_length=2000)
    status: Optional[ProjectStatus] = None


class ProjectOut(ORM):
    id: int
    name: str
    description: str
    status: ProjectStatus
    owner_id: int
    story_count: int
    task_count: int
    done_task_count: int
    progress: int
    created_at: datetime
    updated_at: datetime


class ProjectDetail(ProjectOut):
    stories: list[StoryDetail]


# ---- notifications & jobs ----
class NotificationOut(ORM):
    id: int
    type: str
    message: str
    task_id: Optional[int]
    read: bool
    created_at: datetime
    delivered_at: Optional[datetime]


class NotificationList(BaseModel):
    unread: int
    items: list[NotificationOut]


class JobOut(BaseModel):
    id: int
    type: str
    status: Literal["pending", "running", "succeeded", "dead"]
    attempts: int
    max_attempts: int
    run_at: datetime
    last_error: Optional[str]
    result: Optional[Any]
    project_id: Optional[int]
    created_at: datetime
    updated_at: datetime
