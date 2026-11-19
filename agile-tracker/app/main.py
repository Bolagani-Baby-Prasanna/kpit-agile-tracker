import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware

from . import config
from .database import Base, engine
from .routers import auth, jobs, notifications, projects, stories, tasks
from .worker import worker_loop

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
STATIC = Path(__file__).parent / "static"

DESCRIPTION = """
Agile project tracker for small teams. **Project -> User Story -> Task**, with a background job
system (assignment notifications, due-date reminders, report generation).

Authenticate: `POST /api/auth/login`, then click **Authorize** and paste the `access_token`.
"""


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(engine)  # fine for SQLite/demo; use Alembic migrations in production
    stop = asyncio.Event()
    task = asyncio.create_task(worker_loop(stop)) if config.WORKER_ENABLED else None
    yield
    stop.set()
    if task:
        await task


class SecurityHeaders(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        if not request.url.path.startswith(("/docs", "/redoc")):  # Swagger UI needs its CDN assets
            response.headers.setdefault(
                "Content-Security-Policy",
                "default-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'self'",
            )
        return response


app = FastAPI(title="Sprintboard API", version="1.0.0", description=DESCRIPTION, lifespan=lifespan)
app.add_middleware(SecurityHeaders)

for module in (auth, projects, stories, tasks, notifications, jobs):
    app.include_router(module.router)

app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/api/health", tags=["Meta"])
def health():
    return {"status": "ok", "worker": config.WORKER_ENABLED}
