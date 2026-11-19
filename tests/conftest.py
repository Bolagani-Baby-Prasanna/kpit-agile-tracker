import os
import tempfile

# Must be set BEFORE the app is imported.
_tmp = tempfile.mkdtemp()
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp}/test.db"
os.environ["WORKER_ENABLED"] = "0"          # tests drive the worker manually via run_one()
os.environ["SECRET_KEY"] = "test-secret"
os.environ["JOB_BACKOFF_BASE_SECONDS"] = "0"  # retries are instantly due in tests

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.security import login_limiter


@pytest.fixture(scope="session")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture(autouse=True)
def _reset_limiter():
    login_limiter.hits.clear()


def make_user(client, name, email=None):
    email = email or f"{name.lower()}@example.com"
    client.post("/api/auth/register", json={"email": email, "name": name, "password": "password123"})
    r = client.post("/api/auth/login", json={"email": email, "password": "password123"})
    assert r.status_code == 200, r.text
    data = r.json()
    return {"id": data["user"]["id"], "h": {"Authorization": f"Bearer {data['access_token']}"}}


@pytest.fixture()
def alice(client):
    return make_user(client, "Alice")


@pytest.fixture()
def bob(client):
    return make_user(client, "Bob")
