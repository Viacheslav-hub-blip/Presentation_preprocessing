"""
Что содержит: функции зависимостей FastAPI для создания `PresentationService` с конфигурацией и моделями.
За что отвечает: за внедрение зависимостей в endpoint-обработчики без ручной сборки сервиса в каждом маршруте.
Где используется: импортируется в `src.app.api.endpoints.presentations` через `Depends(...)`.
"""

from __future__ import annotations

from app.core.config import get_app_config, get_model_registry
from app.services.presentation_service import PresentationService


def get_presentation_service() -> PresentationService:
    """Собирает и возвращает сервис для работы с презентациями."""
    return PresentationService(
        config=get_app_config(),
        models=get_model_registry(),
    )
