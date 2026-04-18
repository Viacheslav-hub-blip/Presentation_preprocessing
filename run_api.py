"""
Что содержит: точку входа для запуска FastAPI-приложения через Uvicorn и подготовку `sys.path` для импорта модулей из `src`.
За что отвечает: за локальный старт API-сервиса с хостом, портом и режимом reload из конфигурации проекта.
Где используется: запускается вручную как основной файл проекта при старте сервера FastAPI.
"""

from __future__ import annotations

import sys
from pathlib import Path
import src.project_config as settings

PROJECT_ROOT = Path(__file__).resolve().parent
SRC_DIR = PROJECT_ROOT / "src"

if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))


def main() -> None:
    """Запускает FastAPI-приложение через Uvicorn с настройками из конфигурации."""
    try:
        import uvicorn
    except ImportError as exc:
        raise RuntimeError(
            "Не найден пакет `uvicorn`. Установи его в виртуальное окружение проекта."
        ) from exc

    try:
        from src.app.main import app
    except RuntimeError as exc:
        if "python-multipart" in str(exc):
            raise RuntimeError(
                "Не найден пакет `python-multipart`. Он нужен FastAPI для загрузки PPTX/PDF через form-data."
            ) from exc
        raise

    uvicorn.run(
        app,
        host=settings.APP_HOST,
        port=settings.APP_PORT,
        reload=settings.APP_RELOAD,
    )


if __name__ == "__main__":
    main()
