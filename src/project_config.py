"""
Что содержит: централизованные настройки API, путей хранения, баз данных, VLM и внешних моделей.
За что отвечает: за единое место конфигурации, из которого приложение получает параметры запуска и интеграций.
Где используется: импортируется точкой входа `run_api.py`, модулем `src.app.core.config` и через него всем сервисным слоем.
"""

from __future__ import annotations

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"


# === API SETTINGS ===
API_TITLE = "Presentation Preprocessing API"
API_DESCRIPTION = "API для загрузки, просмотра и удаления обработанных презентаций."
API_VERSION = "0.1.0"
APP_HOST = "127.0.0.1"
APP_PORT = 8000
APP_RELOAD = False
PRESENTATION_UPLOAD_DIR = DATA_DIR / "uploads"


# === REPORT SETTINGS ===
REPORT_NAME = ""
PRESENTATION_ID = None
MAX_CONCURRENCY = 4


# === RELATIONAL DB SETTINGS ===
RELATIONAL_CONNECTION_STRING = ""
PRESENTATIONS_TABLE = "presentation_text_dev"
CHUNKS_TABLE = "chunk_text_dev"


# === VECTOR DB SETTINGS ===
VECTOR_CONNECTION_STRING = ""
VECTOR_TABLE = "reports_rag_2_test"
VECTOR_SCHEMA = "user"
VECTOR_ID_COLUMN = "langchain_id"
VECTOR_SIZE = 2560
VECTOR_METADATA_COLUMNS = (
    "unique_id",
    "report_name",
    "presentation_id",
    "type",
    "slide_number",
    "chunk_number",
    "total_chunks",
)


# === VLM SETTINGS ===
VLM_BASE_URL = "http://progress1122.csp.omega.sbrf.ru:11000/v1"
VLM_MODEL_NAME = "Qwen3-VL-8B-Instruct"
VLM_API_KEY = "EMPTY"
VLM_TIMEOUT = 3600
VLM_MAX_TOKENS = 4096

