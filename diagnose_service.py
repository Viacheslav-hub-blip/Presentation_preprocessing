"""
Что содержит: диагностический сценарий для проверки файлов, моделей, VLM и обеих PostgreSQL-подсистем.
За что отвечает: за пошаговую проверку основных точек отказа перед загрузкой презентации в API.
Где используется: запускается вручную рядом с проектом, когда нужно понять, на каком этапе сервис падает с HTTP 500.
"""

from __future__ import annotations

import asyncio
import json
import traceback
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen

import src.llm_model as llm_model
import src.project_config as settings


# Заполни пути, если хочешь проверить конкретные входные файлы.
PPTX_PATH = Path(r"C:\path\to\presentation.pptx")
PDF_PATH: Path | None = None

# Если не хочешь делать сетевой запрос к VLM, поставь False.
CHECK_VLM_HTTP = True

# Если не хочешь делать реальное создание vector store, поставь False.
CHECK_VECTOR_STORE = True


def print_header(title: str) -> None:
    """Печатает заголовок отдельного диагностического шага."""
    print()
    print("=" * 80)
    print(title)
    print("=" * 80)


def print_ok(message: str) -> None:
    """Печатает сообщение об успешном прохождении шага."""
    print(f"[OK] {message}")


def print_fail(message: str) -> None:
    """Печатает сообщение об ошибке шага."""
    print(f"[FAIL] {message}")


def run_check(title: str, check) -> bool:
    """Запускает отдельную проверку и печатает её результат."""
    print_header(title)
    try:
        check()
        return True
    except Exception as exc:
        print_fail(f"{type(exc).__name__}: {exc}")
        print(traceback.format_exc())
        return False


def check_basic_config() -> None:
    """Проверяет, что обязательные настройки проекта заполнены."""
    required_fields = {
        "RELATIONAL_CONNECTION_STRING": settings.RELATIONAL_CONNECTION_STRING,
        "VECTOR_CONNECTION_STRING": settings.VECTOR_CONNECTION_STRING,
        "VECTOR_TABLE": settings.VECTOR_TABLE,
        "VLM_BASE_URL": settings.VLM_BASE_URL,
        "VLM_MODEL_NAME": settings.VLM_MODEL_NAME,
    }
    missing = [name for name, value in required_fields.items() if not value]
    if missing:
        raise RuntimeError(f"Не заполнены обязательные настройки: {', '.join(missing)}")

    print_ok(f"RELATIONAL_CONNECTION_STRING = {settings.RELATIONAL_CONNECTION_STRING}")
    print_ok(f"VECTOR_CONNECTION_STRING = {settings.VECTOR_CONNECTION_STRING}")
    print_ok(f"VECTOR_TABLE = {settings.VECTOR_TABLE}")
    print_ok(f"VECTOR_SCHEMA = {settings.VECTOR_SCHEMA}")
    print_ok(f"VLM_BASE_URL = {settings.VLM_BASE_URL}")
    print_ok(f"VLM_MODEL_NAME = {settings.VLM_MODEL_NAME}")


def check_model_objects() -> None:
    """Проверяет, что в `llm_model.py` созданы текстовая модель и эмбеддинги."""
    if llm_model.TEXT_MODEL is None:
        raise RuntimeError("В `src/llm_model.py` не создан `TEXT_MODEL`.")
    if llm_model.EMBEDDINGS_MODEL is None:
        raise RuntimeError("В `src/llm_model.py` не создан `EMBEDDINGS_MODEL`.")

    print_ok(f"TEXT_MODEL = {type(llm_model.TEXT_MODEL).__name__}")
    print_ok(f"EMBEDDINGS_MODEL = {type(llm_model.EMBEDDINGS_MODEL).__name__}")


def check_relational_db() -> None:
    """Проверяет, что реляционная PostgreSQL доступна через sync SQLAlchemy."""
    from sqlalchemy import create_engine, text

    engine = create_engine(settings.RELATIONAL_CONNECTION_STRING)
    try:
        with engine.begin() as connection:
            result = connection.execute(text("SELECT 1 AS value"))
            row = result.fetchone()
    finally:
        engine.dispose()

    if row is None or row[0] != 1:
        raise RuntimeError("Тестовый запрос к реляционной БД вернул неожиданный результат.")
    print_ok("Реляционная PostgreSQL доступна.")


def check_vector_db_connection() -> None:
    """Проверяет формат строки подключения к векторной PostgreSQL."""
    if "+asyncpg" in settings.VECTOR_CONNECTION_STRING:
        print_ok(
            "VECTOR_CONNECTION_STRING использует asyncpg. "
            "Прямую sync-проверку пропускаем, реальное подключение проверит шаг создания vector store."
        )
        return

    from sqlalchemy import create_engine, text

    engine = create_engine(settings.VECTOR_CONNECTION_STRING)
    try:
        with engine.begin() as connection:
            connection.execute(text("SELECT 1"))
    finally:
        engine.dispose()

    print_ok("PostgreSQL для vector store доступна.")


async def _check_vector_store_async() -> None:
    """Проверяет, что langchain-postgres может создать объект vector store."""
    from src.app.db.storage import VectorDBConfig, create_vector_store

    config = VectorDBConfig(
        connection_string=settings.VECTOR_CONNECTION_STRING,
        table_name=settings.VECTOR_TABLE,
        schema_name=settings.VECTOR_SCHEMA,
        id_column=settings.VECTOR_ID_COLUMN,
        vector_size=settings.VECTOR_SIZE,
        metadata_columns=settings.VECTOR_METADATA_COLUMNS,
    )
    vector_store = await create_vector_store(
        config,
        embedding_service=llm_model.EMBEDDINGS_MODEL,
        initialize_table=True,
    )
    if vector_store is None:
        raise RuntimeError("create_vector_store(...) вернул None.")
    print_ok(f"Vector store создан: {type(vector_store).__name__}")


def check_vector_store() -> None:
    """Проверяет создание объекта vector store через `langchain-postgres`."""
    asyncio.run(_check_vector_store_async())


def check_pptx_file() -> None:
    """Проверяет, что PPTX-файл существует и читается библиотекой `python-pptx`."""
    from src.app.services.file_extractors import load_pptx_slides

    if not PPTX_PATH.exists():
        raise FileNotFoundError(f"PPTX-файл не найден: {PPTX_PATH}")
    slides = load_pptx_slides(PPTX_PATH)
    print_ok(f"PPTX открыт успешно. Найдено слайдов: {len(slides)}")


def check_pdf_file() -> None:
    """Проверяет, что PDF-файл существует, читается и рендерится в изображения."""
    from src.app.services.file_extractors import load_pdf_slides
    from src.app.services.image_renderers import render_pdf_page_images

    if PDF_PATH is None:
        raise RuntimeError("PDF_PATH = None. Укажи путь к PDF, чтобы проверить эту ветку.")
    if not PDF_PATH.exists():
        raise FileNotFoundError(f"PDF-файл не найден: {PDF_PATH}")

    slide_texts = load_pdf_slides(PDF_PATH)
    image_paths = render_pdf_page_images(PDF_PATH)
    print_ok(f"PDF открыт успешно. Найдено страниц: {len(slide_texts)}")
    print_ok(f"PDF успешно отрендерен в изображения. Получено файлов: {len(image_paths)}")
    if image_paths:
        print_ok(f"Первое изображение: {image_paths[0]}")


def check_vlm_http() -> None:
    """Проверяет, отвечает ли OpenAI-совместимый VLM endpoint на запрос `/models`."""
    models_url = settings.VLM_BASE_URL.rstrip("/") + "/models"
    request = Request(models_url, headers={"Authorization": f"Bearer {settings.VLM_API_KEY}"})
    try:
        with urlopen(request, timeout=30) as response:
            payload = response.read().decode("utf-8", errors="replace")
            print_ok(f"VLM endpoint отвечает. HTTP {response.status}")
            try:
                parsed = json.loads(payload)
                if isinstance(parsed, dict):
                    print_ok(f"Ключи ответа: {', '.join(parsed.keys())}")
            except json.JSONDecodeError:
                print_ok("Ответ VLM получен, но он не является JSON.")
    except URLError as exc:
        raise RuntimeError(f"Не удалось обратиться к VLM endpoint `{models_url}`: {exc}") from exc


def main() -> None:
    """Запускает все диагностические проверки по очереди."""
    results: list[tuple[str, bool]] = []

    results.append(("Базовая конфигурация", run_check("Базовая конфигурация", check_basic_config)))
    results.append(("LangChain-модели", run_check("LangChain-модели", check_model_objects)))
    results.append(("Реляционная PostgreSQL", run_check("Реляционная PostgreSQL", check_relational_db)))
    results.append(("Vector PostgreSQL", run_check("Vector PostgreSQL", check_vector_db_connection)))

    if CHECK_VECTOR_STORE:
        results.append(("Создание vector store", run_check("Создание vector store", check_vector_store)))

    if PPTX_PATH:
        results.append(("Проверка PPTX", run_check("Проверка PPTX", check_pptx_file)))

    if PDF_PATH is not None:
        results.append(("Проверка PDF", run_check("Проверка PDF", check_pdf_file)))

    if CHECK_VLM_HTTP:
        results.append(("Проверка VLM endpoint", run_check("Проверка VLM endpoint", check_vlm_http)))

    print_header("Итог")
    for title, is_ok in results:
        status = "OK" if is_ok else "FAIL"
        print(f"{status:>4} | {title}")


if __name__ == "__main__":
    main()
