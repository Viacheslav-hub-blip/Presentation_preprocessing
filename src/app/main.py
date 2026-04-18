"""
Что содержит: фабрику FastAPI-приложения и готовый объект `app` с подключенным роутером API.
За что отвечает: за сборку веб-приложения из конфигурации и маршрутов перед запуском сервера.
Где используется: импортируется из `run_api.py` и является ASGI-входом для FastAPI/Uvicorn.
"""

from __future__ import annotations

from fastapi import FastAPI

from src.app.api.router import api_router
from src.app.core.config import get_app_config


def create_app() -> FastAPI:
    """Создаёт и настраивает экземпляр FastAPI-приложения."""
    config = get_app_config()
    application = FastAPI(
        title=config.api_title,
        description=config.api_description,
        version=config.api_version,
    )
    application.include_router(api_router)
    return application


app = create_app()
