"""
@file main.py
@brief FastAPI application entrypoint.
"""

from __future__ import annotations

from fastapi import FastAPI

from app.config import settings
from app.routes import router as api_router


def create_app() -> FastAPI:
    """
    @brief App factory.
    @return FastAPI instance.
    """
    app = FastAPI(title=settings.app_name)
    app.include_router(api_router)
    return app


app = create_app()
