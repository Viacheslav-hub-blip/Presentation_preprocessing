"""
Что содержит: dataclass-конфиги приложения и моделей, а также функции сборки настроек и VLM-клиента.
За что отвечает: за преобразование значений из `src.project_config` в объекты, удобные для сервисного слоя.
Где используется: вызывается в `src.app.main`, `src.app.api.dependencies` и сервисах, которым нужны настройки и модели.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import src.llm_model as llm_model
import src.project_config as settings
from src.app.db.storage import RelationalDBConfig, VectorDBConfig
from src.vlm_client import QwenVLMClient, QwenVLMConfig


@dataclass(slots=True)
class AppConfig:
    """Хранит основные настройки приложения, собранные из `project_config.py`."""

    upload_dir: Path
    max_concurrency: int
    relational_db: RelationalDBConfig
    vector_db: VectorDBConfig
    api_title: str
    api_description: str
    api_version: str


@dataclass(slots=True)
class ModelRegistry:
    """Хранит подключенные внешние модели, которые нужны сервисам."""

    text_model: object
    embeddings_model: object


def get_app_config() -> AppConfig:
    """Собирает конфигурацию приложения из настроек проекта."""
    upload_dir = Path(settings.PRESENTATION_UPLOAD_DIR).resolve()
    relational_connection_string = settings.RELATIONAL_CONNECTION_STRING
    if not relational_connection_string:
        raise RuntimeError(
            "Не задан `RELATIONAL_CONNECTION_STRING`. Заполни его в `src/project_config.py`."
        )

    relational_db = RelationalDBConfig(
        connection_string=relational_connection_string,
        presentations_table=settings.PRESENTATIONS_TABLE,
        chunks_table=settings.CHUNKS_TABLE,
    )

    vector_connection_string = settings.VECTOR_CONNECTION_STRING
    vector_table = settings.VECTOR_TABLE
    if not vector_connection_string:
        raise RuntimeError(
            "Не задан `VECTOR_CONNECTION_STRING`. Для RAG-пайплайна заполни его в `src/project_config.py`."
        )
    if not vector_table:
        raise RuntimeError(
            "Не задан `VECTOR_TABLE`. Для RAG-пайплайна заполни его в `src/project_config.py`."
        )

    vector_db = VectorDBConfig(
        connection_string=vector_connection_string,
        table_name=vector_table,
        schema_name=settings.VECTOR_SCHEMA,
        id_column=settings.VECTOR_ID_COLUMN,
        vector_size=settings.VECTOR_SIZE,
        metadata_columns=settings.VECTOR_METADATA_COLUMNS,
    )

    return AppConfig(
        upload_dir=upload_dir,
        max_concurrency=settings.MAX_CONCURRENCY,
        relational_db=relational_db,
        vector_db=vector_db,
        api_title=settings.API_TITLE,
        api_description=settings.API_DESCRIPTION,
        api_version=settings.API_VERSION,
    )


def get_model_registry() -> ModelRegistry:
    """Возвращает реестр подключенных моделей для сервисного слоя."""
    if llm_model.TEXT_MODEL is None:
        raise RuntimeError(
            "В `src/llm_model.py` не задан `TEXT_MODEL`. API не сможет обрабатывать презентации, пока модель не подключена."
        )
    if llm_model.EMBEDDINGS_MODEL is None:
        raise RuntimeError(
            "В `src/llm_model.py` не задан `EMBEDDINGS_MODEL`. Для RAG-пайплайна нужна обязательная запись в векторную PostgreSQL."
        )
    return ModelRegistry(
        text_model=llm_model.TEXT_MODEL,
        embeddings_model=llm_model.EMBEDDINGS_MODEL,
    )


def build_vision_model() -> QwenVLMClient | None:
    """Пытается создать VLM-клиент и возвращает `None`, если это не удалось."""
    try:
        return QwenVLMClient(
            QwenVLMConfig(
                base_url=settings.VLM_BASE_URL,
                model_name=settings.VLM_MODEL_NAME,
                api_key=settings.VLM_API_KEY,
                timeout=settings.VLM_TIMEOUT,
                max_tokens=settings.VLM_MAX_TOKENS,
            )
        )
    except Exception:
        return None
