from __future__ import annotations

from collections.abc import Callable

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from tcg_pipeline.api.auth import SupabaseJWTVerifier, TokenVerifier
from tcg_pipeline.api.routers import auth, coverage, evidence, health, projects, research, review
from tcg_pipeline.settings import Settings, get_settings

ReadinessCheck = Callable[[], None]


def create_app(
    *,
    settings: Settings | None = None,
    jwt_verifier: TokenVerifier | None = None,
    readiness_check: ReadinessCheck | None = None,
) -> FastAPI:
    app_settings = settings or get_settings()
    app = FastAPI(
        title="TCG Pipeline Write API",
        version="0.1.0",
        docs_url="/docs" if app_settings.app_env != "production" else None,
        redoc_url="/redoc" if app_settings.app_env != "production" else None,
    )
    app.state.settings = app_settings
    app.state.jwt_verifier = jwt_verifier or SupabaseJWTVerifier(app_settings)
    if readiness_check is not None:
        app.state.readiness_check = readiness_check

    cors_origins = _parse_csv(app_settings.api_cors_origins)
    if cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=cors_origins,
            allow_credentials=False,
            allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
            allow_headers=["Authorization", "Content-Type"],
        )

    app.include_router(health.router)
    app.include_router(auth.router)
    app.include_router(projects.router)
    app.include_router(review.router)
    app.include_router(coverage.router)
    app.include_router(evidence.router)
    app.include_router(research.router)

    @app.get("/")
    def root() -> dict[str, str]:
        return {"service": "tcg-pipeline-write-api", "status": "ok"}

    return app


def _parse_csv(value: str | None) -> list[str]:
    return [item.strip() for item in (value or "").split(",") if item.strip()]


app = create_app()
