from datetime import date, timedelta

from sqlalchemy import select

from app import config, worker
from app.database import SessionLocal
from app.models import Job, Notification


def drain():
    while worker.run_one():
        pass


def mk_project(client, h, name="Apollo"):
    return client.post("/api/projects", json={"name": name}, headers=h).json()["id"]


def mk_story(client, h, pid, **kw):
    return client.post(f"/api/projects/{pid}/stories", json={"title": "Login page", **kw}, headers=h).json()["id"]


# ------------------------------------------------------------------ auth
def test_requires_auth(client):
    assert client.get("/api/projects").status_code == 401
    assert client.get("/api/projects", headers={"Authorization": "Bearer junk"}).status_code == 401


def test_register_login_validation(client):
    bad = client.post("/api/auth/register", json={"email": "nope", "name": "X", "password": "short"})
    assert bad.status_code == 422
    client.post("/api/auth/register", json={"email": "dup@example.com", "name": "D", "password": "password123"})
    dup = client.post("/api/auth/register", json={"email": "DUP@example.com", "name": "D", "password": "password123"})
    assert dup.status_code == 409
    wrong = client.post("/api/auth/login", json={"email": "dup@example.com", "password": "wrong-password"})
    assert wrong.status_code == 401


# ------------------------------------------------------------------ hierarchy
def test_hierarchy_crud_and_cascade(client, alice):
    h = alice["h"]
    pid = mk_project(client, h)
    sid = mk_story(client, h, pid, priority="high", points=5)
    tid = client.post(f"/api/stories/{sid}/tasks", json={"title": "Build form"}, headers=h).json()["id"]

    tree = client.get(f"/api/projects/{pid}", headers=h).json()
    assert tree["story_count"] == 1 and tree["task_count"] == 1
    assert tree["stories"][0]["tasks"][0]["id"] == tid

    r = client.patch(f"/api/tasks/{tid}", json={"status": "done"}, headers=h)
    assert r.json()["completed_at"] is not None
    assert client.get(f"/api/projects/{pid}", headers=h).json()["progress"] == 100

    assert client.delete(f"/api/projects/{pid}", headers=h).status_code == 204
    assert client.get(f"/api/stories/{sid}", headers=h).status_code == 404   # cascaded
    assert client.get(f"/api/tasks/{tid}", headers=h).status_code == 404     # cascaded


def test_only_owner_can_delete_project(client, alice, bob):
    pid = mk_project(client, alice["h"])
    assert client.delete(f"/api/projects/{pid}", headers=bob["h"]).status_code == 403


def test_story_done_rule_and_auto_progress(client, alice):
    h = alice["h"]
    pid = mk_project(client, h)
    sid = mk_story(client, h, pid)
    tid = client.post(f"/api/stories/{sid}/tasks", json={"title": "T"}, headers=h).json()["id"]

    assert client.patch(f"/api/stories/{sid}", json={"status": "done"}, headers=h).status_code == 409
    client.patch(f"/api/tasks/{tid}", json={"status": "in_progress"}, headers=h)
    assert client.get(f"/api/stories/{sid}", headers=h).json()["status"] == "in_progress"  # auto-advanced
    client.patch(f"/api/tasks/{tid}", json={"status": "done"}, headers=h)
    assert client.patch(f"/api/stories/{sid}", json={"status": "done"}, headers=h).status_code == 200


def test_filters_validation_and_archive(client, alice):
    h = alice["h"]
    pid = mk_project(client, h)
    mk_story(client, h, pid, priority="high")
    mk_story(client, h, pid, priority="low")
    assert len(client.get(f"/api/projects/{pid}/stories?priority=high", headers=h).json()) == 1
    assert client.post(f"/api/projects/{pid}/stories", json={"title": "x", "priority": "urgent"}, headers=h).status_code == 422
    assert client.post(f"/api/projects/{pid}/stories", json={"title": "x", "assignee_id": 9999}, headers=h).status_code == 422
    client.patch(f"/api/projects/{pid}", json={"status": "archived"}, headers=h)
    assert client.post(f"/api/projects/{pid}/stories", json={"title": "x"}, headers=h).status_code == 409


# ------------------------------------------------------------------ async workflow
def test_assignment_creates_notification_via_worker(client, alice, bob):
    h = alice["h"]
    pid = mk_project(client, h)
    sid = mk_story(client, h, pid)
    client.post(f"/api/stories/{sid}/tasks", json={"title": "Review PR", "assignee_id": bob["id"]}, headers=h)

    # Nothing happens synchronously: the request only queued a job.
    assert client.get("/api/notifications", headers=bob["h"]).json()["unread"] == 0
    drain()
    data = client.get("/api/notifications", headers=bob["h"]).json()
    assert data["unread"] == 1 and "Review PR" in data["items"][0]["message"]
    assert data["items"][0]["delivered_at"] is not None

    # Self-assignment does not notify; mark-read works and is scoped to the owner.
    nid = data["items"][0]["id"]
    assert client.post(f"/api/notifications/{nid}/read", headers=alice["h"]).status_code == 404
    client.post(f"/api/notifications/{nid}/read", headers=bob["h"])
    assert client.get("/api/notifications", headers=bob["h"]).json()["unread"] == 0


def test_retry_then_success_without_duplicates(client, alice, bob, monkeypatch):
    h = alice["h"]
    sid = mk_story(client, h, mk_project(client, h))
    client.post(f"/api/stories/{sid}/tasks", json={"title": "Flaky mail", "assignee_id": bob["id"]}, headers=h)

    monkeypatch.setattr(config, "DELIVERY_FAILURE_RATE", 1.0)   # provider down
    assert worker.run_one() is True
    with SessionLocal() as db:
        job = db.scalars(select(Job).where(Job.type == "notify_assignment").order_by(Job.id.desc())).first()
        assert job.status == "pending" and job.attempts == 1 and "outage" in job.last_error
        jid = job.id

    monkeypatch.setattr(config, "DELIVERY_FAILURE_RATE", 0.0)   # provider recovers
    drain()
    with SessionLocal() as db:
        job = db.get(Job, jid)
        assert job.status == "succeeded" and job.attempts == 2
        notes = db.scalars(select(Notification).where(Notification.dedupe_key == f"assign:job:{jid}")).all()
        assert len(notes) == 1 and notes[0].delivered_at is not None     # idempotent: exactly one


def test_job_goes_dead_after_max_attempts(client, alice, bob, monkeypatch):
    h = alice["h"]
    sid = mk_story(client, h, mk_project(client, h))
    client.post(f"/api/stories/{sid}/tasks", json={"title": "Doomed", "assignee_id": bob["id"]}, headers=h)
    monkeypatch.setattr(config, "DELIVERY_FAILURE_RATE", 1.0)
    drain()
    dead = client.get("/api/jobs?status=dead", headers=h).json()
    assert dead and dead[0]["attempts"] == dead[0]["max_attempts"]


def test_report_job_lifecycle(client, alice):
    h = alice["h"]
    pid = mk_project(client, h)
    sid = mk_story(client, h, pid, points=3)
    client.post(f"/api/stories/{sid}/tasks", json={"title": "A"}, headers=h)
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    client.post(f"/api/stories/{sid}/tasks", json={"title": "B", "due_date": yesterday}, headers=h)

    r = client.post(f"/api/projects/{pid}/reports", headers=h)
    assert r.status_code == 202 and r.json()["status"] == "pending"
    jid = r.json()["id"]
    drain()
    job = client.get(f"/api/jobs/{jid}", headers=h).json()
    assert job["status"] == "succeeded"
    assert job["result"]["tasks"]["total"] == 2 and len(job["result"]["overdue_tasks"]) == 1


def test_due_scan_is_idempotent(client, alice):
    h = alice["h"]
    sid = mk_story(client, h, mk_project(client, h))
    client.post(f"/api/stories/{sid}/tasks",
                json={"title": "Late", "assignee_id": alice["id"], "due_date": date.today().isoformat()}, headers=h)
    for _ in range(2):
        client.post("/api/jobs/due-scan", headers=h)
        drain()
    items = client.get("/api/notifications", headers=h).json()["items"]
    assert sum(1 for n in items if n["type"] == "task_due" and "Late" in n["message"]) == 1
