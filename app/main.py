import logging
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from app.config import get_settings, validate_settings
from app.csrf import CSRFMiddleware
from app.db import database_is_ready
from app.logging_conf import setup_logging
from app.rate_limit import limiter
from app.routers.auth import router as auth_router
from app.routers.docs import router as docs_router
from app.routers.memberships import router as memberships_router
from app.routers.sources import router as sources_router
from app.security import SecurityHeadersMiddleware

logger = logging.getLogger(__name__)


def create_app() -> FastAPI:
    settings = get_settings()
    validate_settings(settings)
    setup_logging(settings.log_level)

    app = FastAPI(title=settings.app_name, debug=settings.debug)
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

    app.add_middleware(CSRFMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,  # Support cookies/authentication headers
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(SecurityHeadersMiddleware)

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request, exc) -> JSONResponse:
        current = get_settings()
        if current.debug:
            return JSONResponse(status_code=500, content={"detail": str(exc)})
        return JSONResponse(status_code=500, content={"detail": "internal server error"})

    @app.on_event("startup")
    async def startup_event():
        """Validate and create storage directories on startup.

        The local disk snapshot dir is only needed for the ``local`` backend;
        the ``cloudinary`` backend requires DOCVERSION_CLOUDINARY_URL (enforced
        by validate_settings in production) and manages its own storage. The git
        export working dir is always local — git needs a real checkout.
        """
        settings = get_settings()

        # Validate snapshot storage directory (local backend only)
        if settings.storage_backend != "cloudinary":
            snapshot_dir = Path(settings.snapshot_storage_dir).resolve()
            try:
                snapshot_dir.mkdir(parents=True, exist_ok=True)
                logger.info(f"Snapshot storage directory: {snapshot_dir}")
            except PermissionError as e:
                logger.error(f"Permission denied creating snapshot directory {snapshot_dir}: {e}")
                raise
            except OSError as e:
                logger.error(f"Failed to create snapshot directory {snapshot_dir}: {e}")
                raise

        # Validate git export directory
        git_dir = Path(settings.git_export_base_dir).resolve()
        try:
            git_dir.mkdir(parents=True, exist_ok=True)
            logger.info(f"Git export directory: {git_dir}")
        except PermissionError as e:
            logger.error(f"Permission denied creating git export directory {git_dir}: {e}")
            raise
        except OSError as e:
            logger.error(f"Failed to create git export directory {git_dir}: {e}")
            raise

    app.include_router(auth_router)
    app.include_router(memberships_router)
    app.include_router(sources_router)
    app.include_router(docs_router)

    @app.get("/")
    def root() -> dict[str, str]:
        # environment deliberately omitted: minor but avoidable info disclosure
        return {
            "app": settings.app_name,
        }

    @app.get("/health")
    async def health() -> dict[str, str]:
        try:
            await database_is_ready()
        except Exception:
            raise HTTPException(status_code=503, detail="database unavailable")
        return {"status": "ok", "database": "ok"}

    return app


app = create_app()