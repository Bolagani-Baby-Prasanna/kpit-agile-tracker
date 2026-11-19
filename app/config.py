"""Central configuration, read from environment variables (12-factor style)."""
import logging
import os
import secrets

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./agile.db")

SECRET_KEY = os.getenv("SECRET_KEY") or ""
SECRET_KEY_IS_EPHEMERAL = not SECRET_KEY
if SECRET_KEY_IS_EPHEMERAL:
    # Safe default for local dev: a random key per process (tokens die on restart).
    SECRET_KEY = secrets.token_urlsafe(32)
    logging.getLogger("app").warning(
        "SECRET_KEY not set - using a random key; logins reset on every restart."
    )

TOKEN_TTL_MINUTES = int(os.getenv("TOKEN_TTL_MINUTES", "480"))

WORKER_ENABLED = os.getenv("WORKER_ENABLED", "1") == "1"
WORKER_POLL_SECONDS = float(os.getenv("WORKER_POLL_SECONDS", "1"))
JOB_MAX_ATTEMPTS = int(os.getenv("JOB_MAX_ATTEMPTS", "4"))
JOB_BACKOFF_BASE_SECONDS = float(os.getenv("JOB_BACKOFF_BASE_SECONDS", "2"))
STALE_LOCK_SECONDS = int(os.getenv("STALE_LOCK_SECONDS", "300"))
DUE_SCAN_INTERVAL_SECONDS = int(os.getenv("DUE_SCAN_INTERVAL_SECONDS", "3600"))

# Simulated flaky e-mail provider, used to demonstrate retry/backoff.
DELIVERY_FAILURE_RATE = float(os.getenv("DELIVERY_FAILURE_RATE", "0"))
