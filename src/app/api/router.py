"""
Что содержит: корневой FastAPI-роутер приложения и подключение роутера презентаций.
За что отвечает: за сборку общего API-маршрутизатора из endpoint-модулей.
Где используется: импортируется в `src.app.main` и включается в объект FastAPI-приложения.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.endpoints.presentations import router as presentations_router


api_router = APIRouter()
api_router.include_router(presentations_router)
