"""
Что содержит: модели записей БД, конфиги подключений и функции CRUD/синхронизации для PostgreSQL и векторного хранилища.
За что отвечает: за создание таблиц, чтение, запись, обновление и удаление данных презентаций и поисковых документов.
Где используется: вызывается из `src.app.core.config`, `src.app.services.presentation_service` и `src.app.services.processor`.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Iterable, Optional, Protocol, Sequence
from uuid import NAMESPACE_URL, UUID, uuid5

DEFAULT_PRESENTATIONS_TABLE = "presentation_text_dev"
DEFAULT_CHUNKS_TABLE = "chunk_text_dev"


@dataclass(slots=True)
class RelationalDBConfig:
    connection_string: str
    presentations_table: str = DEFAULT_PRESENTATIONS_TABLE
    chunks_table: str = DEFAULT_CHUNKS_TABLE


@dataclass(slots=True)
class VectorDBConfig:
    connection_string: str
    table_name: str
    schema_name: str = "public"
    id_column: str = "langchain_id"
    vector_size: int = 2560
    metadata_columns: tuple[str, ...] = (
        "unique_id",
        "report_name",
        "presentation_id",
        "type",
        "slide_number",
        "chunk_number",
        "total_chunks",
    )


@dataclass(slots=True)
class PresentationRecord:
    id: UUID | str
    report_name: str
    text: str
    summary: Optional[str] = None
    link_on_file: str = ""

    @property
    def presentation_id(self) -> str:
        """Возвращает идентификатор презентации в строковом виде."""
        return str(self.id)


@dataclass(slots=True)
class SlideChunkRecord:
    presentation_id: UUID | str
    slide_sequence_number: int
    chunk_number: int
    source_slide_text: str
    chunk_summary: Optional[str] = None

    @property
    def normalized_presentation_id(self) -> str:
        """Возвращает идентификатор презентации чанка в строковом виде."""
        return str(self.presentation_id)


@dataclass(slots=True)
class PresentationListItem:
    id: UUID | str
    report_name: str
    link_on_file: str = ""

    @property
    def presentation_id(self) -> str:
        """Возвращает идентификатор элемента списка в строковом виде."""
        return str(self.id)


@dataclass(slots=True)
class QueryExecutionResult:
    rows: list[dict[str, Any]]
    rowcount: int


class DatabaseConnection(Protocol):
    def execute(self, query: str, params: Optional[dict[str, Any]] = None) -> QueryExecutionResult:
        """Выполняет SQL-запрос и возвращает строки результата вместе с rowcount."""
        ...


class SQLAlchemyConnection:
    def __init__(self, connection: Any, sql_text: Any):
        """Оборачивает SQLAlchemy-соединение в единый интерфейс выполнения запросов."""
        self._connection = connection
        self._sql_text = sql_text

    def execute(self, query: str, params: Optional[dict[str, Any]] = None) -> QueryExecutionResult:
        """Выполняет запрос через SQLAlchemy и нормализует формат результата."""
        result = self._connection.execute(self._sql_text(query), params or {})
        rows = [dict(row._mapping) for row in result] if result.returns_rows else []
        return QueryExecutionResult(rows=rows, rowcount=result.rowcount or 0)


def _insert_presentation_on_connection(db: DatabaseConnection, config: RelationalDBConfig, record: PresentationRecord) -> None:
    """Добавляет запись презентации в реляционную БД через переданное соединение."""
    db.execute(
        f"""
        INSERT INTO {config.presentations_table} (
            id, report_name, text, summary, link_on_file
        )
        VALUES (:id, :report_name, :text, :summary, :link_on_file);
        """,
        {
            "id": record.presentation_id,
            "report_name": record.report_name,
            "text": record.text,
            "summary": record.summary,
            "link_on_file": record.link_on_file,
        },
    )


def _upsert_presentation_on_connection(db: DatabaseConnection, config: RelationalDBConfig, record: PresentationRecord) -> None:
    """Создаёт или обновляет запись презентации через переданное соединение."""
    db.execute(
        f"""
        INSERT INTO {config.presentations_table} (
            id, report_name, text, summary, link_on_file
        )
        VALUES (:id, :report_name, :text, :summary, :link_on_file)
        ON CONFLICT (id) DO UPDATE SET
            report_name = EXCLUDED.report_name,
            text = EXCLUDED.text,
            summary = EXCLUDED.summary,
            link_on_file = EXCLUDED.link_on_file;
        """,
        {
            "id": record.presentation_id,
            "report_name": record.report_name,
            "text": record.text,
            "summary": record.summary,
            "link_on_file": record.link_on_file,
        },
    )


def _insert_chunk_on_connection(db: DatabaseConnection, config: RelationalDBConfig, record: SlideChunkRecord) -> None:
    """Добавляет один чанк слайда в реляционную БД через переданное соединение."""
    db.execute(
        f"""
        INSERT INTO {config.chunks_table} (
            presentation_id, slide_sequence_number, chunk_number, source_slide_text, chunk_summary
        )
        VALUES (
            :presentation_id, :slide_sequence_number, :chunk_number, :source_slide_text, :chunk_summary
        );
        """,
        {
            "presentation_id": record.normalized_presentation_id,
            "slide_sequence_number": record.slide_sequence_number,
            "chunk_number": record.chunk_number,
            "source_slide_text": record.source_slide_text,
            "chunk_summary": record.chunk_summary,
        },
    )


def _upsert_chunk_on_connection(db: DatabaseConnection, config: RelationalDBConfig, record: SlideChunkRecord) -> None:
    """Создаёт или обновляет один чанк слайда через переданное соединение."""
    db.execute(
        f"""
        INSERT INTO {config.chunks_table} (
            presentation_id, slide_sequence_number, chunk_number, source_slide_text, chunk_summary
        )
        VALUES (
            :presentation_id, :slide_sequence_number, :chunk_number, :source_slide_text, :chunk_summary
        )
        ON CONFLICT (presentation_id, slide_sequence_number, chunk_number) DO UPDATE SET
            source_slide_text = EXCLUDED.source_slide_text,
            chunk_summary = EXCLUDED.chunk_summary;
        """,
        {
            "presentation_id": record.normalized_presentation_id,
            "slide_sequence_number": record.slide_sequence_number,
            "chunk_number": record.chunk_number,
            "source_slide_text": record.source_slide_text,
            "chunk_summary": record.chunk_summary,
        },
    )


def _delete_chunks_on_connection(
    db: DatabaseConnection,
    config: RelationalDBConfig,
    *,
    presentation_id: UUID | str | None = None,
    slide_sequence_number: int | None = None,
    chunk_number: int | None = None,
) -> int:
    """Удаляет чанки по фильтрам через переданное соединение и возвращает число удалённых строк."""
    filters: list[str] = []
    params: dict[str, Any] = {}
    if presentation_id is not None:
        filters.append("presentation_id = :presentation_id")
        params["presentation_id"] = str(presentation_id)
    if slide_sequence_number is not None:
        filters.append("slide_sequence_number = :slide_sequence_number")
        params["slide_sequence_number"] = slide_sequence_number
    if chunk_number is not None:
        filters.append("chunk_number = :chunk_number")
        params["chunk_number"] = chunk_number
    if not filters:
        raise ValueError("Р”Р»СЏ СѓРґР°Р»РµРЅРёСЏ С‡Р°РЅРєРѕРІ РЅСѓР¶РЅРѕ СѓРєР°Р·Р°С‚СЊ С…РѕС‚СЏ Р±С‹ РѕРґРёРЅ С„РёР»СЊС‚СЂ, С‡С‚РѕР±С‹ РЅРµ РѕС‡РёСЃС‚РёС‚СЊ РІСЃСЋ С‚Р°Р±Р»РёС†Сѓ.")
    result = db.execute(
        f"DELETE FROM {config.chunks_table} WHERE {' AND '.join(filters)};",
        params,
    )
    return result.rowcount


def _import_sqlalchemy() -> tuple[Any, Any]:
    """Импортирует зависимости SQLAlchemy и возвращает фабрики, нужные этому модулю."""
    try:
        from sqlalchemy import create_engine
        from sqlalchemy.sql import text as sqlalchemy_text
    except ImportError as exc:
        raise ImportError("Для работы с обычной БД требуется пакет `sqlalchemy`.") from exc
    return create_engine, sqlalchemy_text


def _import_vector_dependencies() -> tuple[Any, Any, Any]:
    """Импортирует зависимости для работы с векторным хранилищем."""
    try:
        from langchain_core.documents import Document
        from langchain_postgres import PGEngine, PGVectorStore
    except ImportError as exc:
        raise ImportError(
            "Для работы с векторной БД требуются пакеты `langchain-core` и `langchain-postgres`."
        ) from exc
    return Document, PGEngine, PGVectorStore


@contextmanager
def get_db_connection(connection_string: str):
    """Открывает SQLAlchemy-соединение и гарантированно закрывает его после использования."""
    create_engine, sqlalchemy_text = _import_sqlalchemy()
    engine = create_engine(connection_string)
    try:
        with engine.begin() as connection:
            yield SQLAlchemyConnection(connection, sqlalchemy_text)
    finally:
        engine.dispose()


def create_relational_tables(config: RelationalDBConfig) -> None:
    """Создаёт таблицы презентаций и чанков, если их ещё нет в БД."""
    with get_db_connection(config.connection_string) as db:
        db.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {config.presentations_table} (
                id UUID PRIMARY KEY,
                report_name TEXT NOT NULL,
                text TEXT NOT NULL,
                summary TEXT,
                link_on_file TEXT DEFAULT ''
            );
            """
        )
        db.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {config.chunks_table} (
                presentation_id UUID NOT NULL,
                slide_sequence_number INTEGER NOT NULL,
                chunk_number INTEGER NOT NULL,
                source_slide_text TEXT NOT NULL,
                chunk_summary TEXT,
                PRIMARY KEY (presentation_id, slide_sequence_number, chunk_number),
                FOREIGN KEY (presentation_id)
                    REFERENCES {config.presentations_table}(id)
                    ON DELETE CASCADE
            );
            """
        )


def insert_presentation(config: RelationalDBConfig, record: PresentationRecord) -> None:
    """Добавляет новую презентацию в реляционную БД."""
    with get_db_connection(config.connection_string) as db:
        _insert_presentation_on_connection(db, config, record)


def upsert_presentation(config: RelationalDBConfig, record: PresentationRecord) -> None:
    """Создаёт или обновляет запись презентации в реляционной БД."""
    with get_db_connection(config.connection_string) as db:
        _upsert_presentation_on_connection(db, config, record)


def select_presentations(
    config: RelationalDBConfig,
    *,
    presentation_id: UUID | str | None = None,
    report_name: str | None = None,
    limit: int | None = None,
) -> list[PresentationRecord]:
    """Читает презентации из реляционной БД с необязательными фильтрами."""
    query = f"""
        SELECT id, report_name, text, summary, link_on_file
        FROM {config.presentations_table}
    """
    filters: list[str] = []
    params: dict[str, Any] = {}
    if presentation_id is not None:
        filters.append("id = :presentation_id")
        params["presentation_id"] = str(presentation_id)
    if report_name is not None:
        filters.append("report_name = :report_name")
        params["report_name"] = report_name
    if filters:
        query += " WHERE " + " AND ".join(filters)
    query += " ORDER BY report_name"
    if limit is not None:
        query += " LIMIT :limit"
        params["limit"] = limit
    with get_db_connection(config.connection_string) as db:
        result = db.execute(query, params)
    return [
        PresentationRecord(
            id=row["id"],
            report_name=row["report_name"],
            text=row["text"],
            summary=row.get("summary"),
            link_on_file=row.get("link_on_file") or "",
        )
        for row in result.rows
    ]


def select_presentation_list(
    config: RelationalDBConfig,
    *,
    limit: int | None = None,
) -> list[PresentationListItem]:
    """Возвращает облегчённый список презентаций для отображения в API."""
    query = f"""
        SELECT id, report_name, link_on_file
        FROM {config.presentations_table}
        ORDER BY report_name
    """
    params: dict[str, Any] = {}
    if limit is not None:
        query += " LIMIT :limit"
        params["limit"] = limit
    with get_db_connection(config.connection_string) as db:
        result = db.execute(query, params)
    return [
        PresentationListItem(
            id=row["id"],
            report_name=row["report_name"],
            link_on_file=row.get("link_on_file") or "",
        )
        for row in result.rows
    ]


def update_presentation(
    config: RelationalDBConfig,
    presentation_id: UUID | str,
    *,
    report_name: str | None = None,
    text: str | None = None,
    summary: str | None = None,
    link_on_file: str | None = None,
) -> int:
    """Обновляет выбранные поля презентации и возвращает число затронутых строк."""
    fields = {
        "report_name": report_name,
        "text": text,
        "summary": summary,
        "link_on_file": link_on_file,
    }
    assignments = [f"{column} = :{column}" for column, value in fields.items() if value is not None]
    if not assignments:
        return 0
    params = {column: value for column, value in fields.items() if value is not None}
    params["presentation_id"] = str(presentation_id)
    with get_db_connection(config.connection_string) as db:
        result = db.execute(
            f"""
            UPDATE {config.presentations_table}
            SET {", ".join(assignments)}
            WHERE id = :presentation_id;
            """,
            params,
        )
    return result.rowcount


def delete_presentation(config: RelationalDBConfig, presentation_id: UUID | str) -> int:
    """Удаляет презентацию и её чанки из реляционной БД."""
    with get_db_connection(config.connection_string) as db:
        db.execute(
            f"DELETE FROM {config.chunks_table} WHERE presentation_id = :presentation_id;",
            {"presentation_id": str(presentation_id)},
        )
        result = db.execute(
            f"DELETE FROM {config.presentations_table} WHERE id = :presentation_id;",
            {"presentation_id": str(presentation_id)},
        )
    return result.rowcount


def insert_chunk(config: RelationalDBConfig, record: SlideChunkRecord) -> None:
    """Добавляет один чанк слайда в реляционную БД."""
    with get_db_connection(config.connection_string) as db:
        _insert_chunk_on_connection(db, config, record)


def upsert_chunk(config: RelationalDBConfig, record: SlideChunkRecord) -> None:
    """Создаёт или обновляет один чанк слайда в реляционной БД."""
    with get_db_connection(config.connection_string) as db:
        _upsert_chunk_on_connection(db, config, record)


def select_chunks(
    config: RelationalDBConfig,
    *,
    presentation_id: UUID | str | None = None,
    slide_sequence_number: int | None = None,
    chunk_number: int | None = None,
) -> list[SlideChunkRecord]:
    """Читает чанки слайдов из БД по переданным фильтрам."""
    query = f"""
        SELECT presentation_id, slide_sequence_number, chunk_number, source_slide_text, chunk_summary
        FROM {config.chunks_table}
    """
    filters: list[str] = []
    params: dict[str, Any] = {}
    if presentation_id is not None:
        filters.append("presentation_id = :presentation_id")
        params["presentation_id"] = str(presentation_id)
    if slide_sequence_number is not None:
        filters.append("slide_sequence_number = :slide_sequence_number")
        params["slide_sequence_number"] = slide_sequence_number
    if chunk_number is not None:
        filters.append("chunk_number = :chunk_number")
        params["chunk_number"] = chunk_number
    if filters:
        query += " WHERE " + " AND ".join(filters)
    query += " ORDER BY presentation_id, slide_sequence_number, chunk_number"
    with get_db_connection(config.connection_string) as db:
        result = db.execute(query, params)
    return [
        SlideChunkRecord(
            presentation_id=row["presentation_id"],
            slide_sequence_number=row["slide_sequence_number"],
            chunk_number=row["chunk_number"],
            source_slide_text=row["source_slide_text"],
            chunk_summary=row.get("chunk_summary"),
        )
        for row in result.rows
    ]


def update_chunk(
    config: RelationalDBConfig,
    presentation_id: UUID | str,
    slide_sequence_number: int,
    chunk_number: int,
    *,
    source_slide_text: str | None = None,
    chunk_summary: str | None = None,
) -> int:
    """Обновляет выбранные поля чанка и возвращает число затронутых строк."""
    fields = {"source_slide_text": source_slide_text, "chunk_summary": chunk_summary}
    assignments = [f"{column} = :{column}" for column, value in fields.items() if value is not None]
    if not assignments:
        return 0
    params = {column: value for column, value in fields.items() if value is not None}
    params.update(
        {
            "presentation_id": str(presentation_id),
            "slide_sequence_number": slide_sequence_number,
            "chunk_number": chunk_number,
        }
    )
    with get_db_connection(config.connection_string) as db:
        result = db.execute(
            f"""
            UPDATE {config.chunks_table}
            SET {", ".join(assignments)}
            WHERE presentation_id = :presentation_id
              AND slide_sequence_number = :slide_sequence_number
              AND chunk_number = :chunk_number;
            """,
            params,
        )
    return result.rowcount


def delete_chunks(
    config: RelationalDBConfig,
    *,
    presentation_id: UUID | str | None = None,
    slide_sequence_number: int | None = None,
    chunk_number: int | None = None,
) -> int:
    """Удаляет чанки по фильтрам и возвращает число удалённых строк."""
    filters: list[str] = []
    params: dict[str, Any] = {}
    if presentation_id is not None:
        filters.append("presentation_id = :presentation_id")
        params["presentation_id"] = str(presentation_id)
    if slide_sequence_number is not None:
        filters.append("slide_sequence_number = :slide_sequence_number")
        params["slide_sequence_number"] = slide_sequence_number
    if chunk_number is not None:
        filters.append("chunk_number = :chunk_number")
        params["chunk_number"] = chunk_number
    if not filters:
        raise ValueError("Для удаления чанков нужно указать хотя бы один фильтр, чтобы не очистить всю таблицу.")
    with get_db_connection(config.connection_string) as db:
        result = db.execute(
            f"DELETE FROM {config.chunks_table} WHERE {' AND '.join(filters)};",
            params,
        )
    return result.rowcount


def replace_presentation_chunks(
    config: RelationalDBConfig,
    presentation_id: UUID | str,
    chunks: Sequence[SlideChunkRecord],
) -> None:
    """Полностью заменяет набор чанков для одной презентации."""
    delete_chunks(config, presentation_id=presentation_id)
    for chunk in chunks:
        upsert_chunk(config, chunk)


def sync_presentation_to_relational_db(
    config: RelationalDBConfig,
    presentation: PresentationRecord,
    chunks: Sequence[SlideChunkRecord],
) -> None:
    """Синхронизирует презентацию и её чанки с реляционной БД."""
    upsert_presentation(config, presentation)
    replace_presentation_chunks(config, presentation.presentation_id, chunks)


async def create_vector_store(
    config: VectorDBConfig,
    embedding_service: Any,
    *,
    initialize_table: bool = False,
) -> Any:
    """Создаёт объект векторного хранилища и при необходимости инициализирует таблицу."""
    _, pg_engine_cls, pg_vector_store_cls = _import_vector_dependencies()
    pg_engine = pg_engine_cls.from_connection_string(url=config.connection_string)
    if initialize_table:
        try:
            from langchain_postgres import Column
        except ImportError as exc:
            raise ImportError(
                "Для инициализации таблицы векторной БД нужен пакет `langchain-postgres` с поддержкой `Column`."
            ) from exc
        metadata_columns = [Column(column_name, "TEXT") for column_name in config.metadata_columns]
        await pg_engine.ainit_vectorstore_table(
            table_name=config.table_name,
            vector_size=config.vector_size,
            schema_name=config.schema_name,
            id_column=config.id_column,
            metadata_columns=metadata_columns,
        )
    return await pg_vector_store_cls.create(
        engine=pg_engine,
        table_name=config.table_name,
        schema_name=config.schema_name,
        embedding_service=embedding_service,
        metadata_columns=list(config.metadata_columns),
        id_column=config.id_column,
    )


def build_report_vector_document(record: PresentationRecord) -> Any:
    """Собирает векторный документ верхнего уровня для всей презентации."""
    document_cls, _, _ = _import_vector_dependencies()
    return document_cls(
        id=record.presentation_id,
        page_content=record.summary or "",
        metadata={
            "unique_id": f"{record.presentation_id}+report",
            "presentation_id": record.presentation_id,
            "report_name": record.report_name,
            "type": "report",
        },
    )


def build_chunk_document_id(
    *,
    presentation_id: UUID | str,
    slide_sequence_number: int,
    chunk_number: int,
) -> str:
    """Строит стабильный идентификатор документа для чанка слайда."""
    seed = f"{presentation_id}:{slide_sequence_number}:{chunk_number}"
    return str(uuid5(NAMESPACE_URL, seed))


def build_chunk_vector_document(report_name: str, chunk: SlideChunkRecord, *, total_chunks: int) -> Any:
    """Собирает векторный документ для одного чанка слайда."""
    document_cls, _, _ = _import_vector_dependencies()
    chunk_id = build_chunk_document_id(
        presentation_id=chunk.normalized_presentation_id,
        slide_sequence_number=chunk.slide_sequence_number,
        chunk_number=chunk.chunk_number,
    )
    return document_cls(
        id=chunk_id,
        page_content=chunk.chunk_summary or "",
        metadata={
            "unique_id": (
                f"{chunk.normalized_presentation_id}+slide{chunk.slide_sequence_number}"
                f"+chunk{chunk.chunk_number}"
            ),
            "presentation_id": chunk.normalized_presentation_id,
            "report_name": report_name,
            "slide_number": chunk.slide_sequence_number,
            "chunk_number": chunk.chunk_number,
            "total_chunks": total_chunks,
            "type": "slide_chunk",
        },
    )


async def add_vector_documents(vector_store: Any, documents: Iterable[Any]) -> Any:
    """Добавляет документы в векторное хранилище."""
    document_list = list(documents)
    if not document_list:
        return []
    return await vector_store.aadd_documents(document_list)


async def delete_vector_documents(vector_store: Any, document_ids: Sequence[str]) -> Any:
    """Удаляет документы из векторного хранилища по их идентификаторам."""
    if not document_ids:
        return False
    return await vector_store.adelete(ids=list(document_ids))


async def update_vector_documents(vector_store: Any, documents: Iterable[Any]) -> Any:
    """Обновляет документы в векторном хранилище через удаление и повторную загрузку."""
    document_list = list(documents)
    document_ids = [document.id for document in document_list if getattr(document, "id", None)]
    if document_ids:
        await delete_vector_documents(vector_store, document_ids)
    if not document_list:
        return []
    return await vector_store.aadd_documents(document_list)


async def select_vector_documents(
    vector_store: Any,
    query: str,
    *,
    k: int = 4,
    filters: Optional[dict[str, Any]] = None,
) -> Any:
    """Выполняет similarity search в векторном хранилище."""
    search_kwargs: dict[str, Any] = {"query": query, "k": k}
    if filters:
        search_kwargs["filter"] = filters
    return await vector_store.asimilarity_search(**search_kwargs)


async def delete_presentation_from_vector_db(
    vector_store: Any,
    presentation_id: UUID | str,
    chunks: Sequence[SlideChunkRecord],
) -> Any:
    """Удаляет из векторной БД презентацию и все её чанки."""
    document_ids = [str(presentation_id)]
    document_ids.extend(
        build_chunk_document_id(
            presentation_id=chunk.normalized_presentation_id,
            slide_sequence_number=chunk.slide_sequence_number,
            chunk_number=chunk.chunk_number,
        )
        for chunk in chunks
    )
    return await delete_vector_documents(vector_store, document_ids)


async def sync_presentation_to_vector_db(
    vector_store: Any,
    presentation: PresentationRecord,
    chunks: Sequence[SlideChunkRecord],
) -> Any:
    """Синхронизирует презентацию и её чанки с векторным хранилищем."""
    documents = [build_report_vector_document(presentation)]
    total_chunks = len(chunks)
    for chunk in chunks:
        documents.append(
            build_chunk_vector_document(
                report_name=presentation.report_name,
                chunk=chunk,
                total_chunks=total_chunks,
            )
        )
    return await update_vector_documents(vector_store, documents)
