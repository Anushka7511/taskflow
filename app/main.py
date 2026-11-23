import logging

from fastapi import Depends, FastAPI
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.exceptions import register_exception_handlers
from app.database.session import get_db
from app.routers import auth, memberships, projects, tasks

DESCRIPTION = """
Multi-user task management API (projects, role-based membership, tasks).

**How to authenticate in Swagger UI:** call `POST /auth/register`, then `POST /auth/login`,
click **Authorize** and paste the `access_token`.

All errors share one shape: `{"error": {"code": "...", "message": "...", "details": ...}}`.
"""

TAGS = [
    {"name": "Authentication", "description": "Register, log in, current user"},
    {"name": "Projects", "description": "Create and manage projects you belong to"},
    {"name": "Memberships", "description": "Invite users and manage project roles (RBAC)"},
    {"name": "Tasks", "description": "Task CRUD, workflow, filtering, pagination, optimistic locking"},
    {"name": "System", "description": "Health check"},
]


def create_app() -> FastAPI:
    settings = get_settings()
    logging.basicConfig(
        level=settings.log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    app = FastAPI(
        title=f"{settings.app_name} API",
        version="1.0.0",
        description=DESCRIPTION,
        openapi_tags=TAGS,
    )
    register_exception_handlers(app)

    app.include_router(auth.router)
    app.include_router(projects.router)
    app.include_router(memberships.router)
    app.include_router(tasks.router)

    @app.get("/health", tags=["System"], summary="Liveness + database check")
    def health(db: Session = Depends(get_db)):
        try:
            db.execute(text("SELECT 1"))
        except Exception:
            logging.getLogger("taskflow").exception("Health check: database unreachable")
            return JSONResponse(status_code=503, content={"status": "unavailable", "database": "down"})
        return {"status": "ok", "database": "up"}

    return app


app = create_app()
