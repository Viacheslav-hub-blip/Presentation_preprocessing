import sys
import uuid
import json
import urllib3
import asyncio
import functools
from fastmcp import FastMCP
from datetime import datetime
from pathlib import Path
from dataclasses import dataclass
from contextlib import contextmanager
from typing import Optional, Any, Literal

from sqlalchemy import create_engine
from sqlalchemy.sql import text as sqlalchemy_text

from langchain_core.documents import Document
from langchain_core.prompts import ChatPromptTemplate
from langchain_community.retrievers import BM25Retriever
from langchain_classic.retrievers import EnsembleRetriever
from langchain_postgres import PGEngine, PGVectorStore

from sber_kitai_sdk_langchain.system_chat_model import KitaiSystemChatModel
from sber_kitai_sdk_langchain.system_embedding_chat_model import KitaiSystemEmbeddings
from sber_kitai_sdk_py.generated.api_client import ApiClient
from sber_kitai_sdk_py.generated.configuration import Configuration

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))

import src.project_config as settings
try:
    from prompts import PROMPT_RERANKING as prompt_reranking
except ImportError:
    from rag_tool.prompts import PROMPT_RERANKING as prompt_reranking

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ----------------Настраиваемые переменные--------------
RAG_TOOL_PORT = 8115
DB_POOL_SIZE = 15
DB_MAX_OVERFLOW = 5  # дополнительные при пиковой нагрузке
DB_POOL_TIMEOUT = 30  # секунд ожидания свободного соединения

CONNECTION_STRING = settings.RELATIONAL_CONNECTION_STRING
CONNECTION_STRING_PG = settings.VECTOR_CONNECTION_STRING
PRESENTATIONS_TABLE = settings.PRESENTATIONS_TABLE
CHUNKS_TABLE = settings.CHUNKS_TABLE
VECTOR_TABLE = settings.VECTOR_TABLE
VECTOR_SCHEMA = settings.VECTOR_SCHEMA
VECTOR_ID_COLUMN = settings.VECTOR_ID_COLUMN
VECTOR_METADATA_COLUMNS = settings.VECTOR_METADATA_COLUMNS

# ----------------Настраиваемые переменные--------------

SESSION_ID = str(uuid.uuid4())
DB_SEMAPHORE = asyncio.Semaphore(DB_POOL_SIZE + DB_MAX_OVERFLOW)
LLM_SEMAPHORE = asyncio.Semaphore(10)


def _quote_identifier(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _qualified_table_name(table_name: str, schema_name: str | None = None) -> str:
    if schema_name:
        return f"{_quote_identifier(schema_name)}.{_quote_identifier(table_name)}"
    return _quote_identifier(table_name)


PRESENTATIONS_TABLE_SQL = _qualified_table_name(PRESENTATIONS_TABLE)
CHUNKS_TABLE_SQL = _qualified_table_name(CHUNKS_TABLE)
VECTOR_TABLE_SQL = _qualified_table_name(VECTOR_TABLE, VECTOR_SCHEMA)

# ----------------Подключение к GigaChat--------------
KITAI_HOST_SDK = "https://cspkitaicore.prom-369-370-mlb.ocp-geo.ocp.omega.sbrf.ru"
KITAI_HOST = "https://cspkitaicore.prom-369-370-mlb.ocp-geo.ocp.omega.sbrf.ru/api/v1"
CERT_FILE_PATH = "cert.pem"
CERT_KEY_FILE_PATH = "key.pem"
SYSTEM_NAME = "lab"
MODULE_NAME = "lab_antifraud_edge"

config = Configuration(host=KITAI_HOST_SDK)
config.cert_file = CERT_FILE_PATH
config.key_file = CERT_KEY_FILE_PATH
config.verify_ssl = False
api_client = ApiClient(config)

GigaChat_Max = KitaiSystemChatModel(api_client=api_client,
                                    system_name=SYSTEM_NAME,
                                    module_name=MODULE_NAME,
                                    model_name="GigaChat-2-Max",
                                    polling_retries=500,
                                    polling_delay_in_sec=2,
                                    polling_start_delay_in_sec=2,
                                    temperature=0.05,
                                    profanity_check=False,
                                    verbose=True,
                                    polling_timeout_in_sec=180
                                    )

EmbeddingsGigaR = KitaiSystemEmbeddings(api_client=api_client,
                                        system_name=SYSTEM_NAME,
                                        module_name=MODULE_NAME,
                                        model_name="EmbeddingsGigaR",
                                        polling_retries=500,
                                        polling_delay_in_sec=5,
                                        polling_start_delay_in_sec=5,
                                        temperature=0.05,
                                        profanity_check=False)
# ----------------Подключение к GigaChat--------------

# ----------------Подключение к базам--------------
engine = create_engine(
    CONNECTION_STRING,
    pool_size=DB_POOL_SIZE,
    max_overflow=DB_MAX_OVERFLOW,
    pool_timeout=DB_POOL_TIMEOUT,
    pool_recycle=3600,
    pool_pre_ping=True,
    pool_use_lifo=True,
    echo=False
)

pg_vector_engine = PGEngine.from_connection_string(url=CONNECTION_STRING_PG)

vectorstore = asyncio.run(PGVectorStore.create(
    engine=pg_vector_engine,
    table_name=VECTOR_TABLE,
    schema_name=VECTOR_SCHEMA,
    embedding_service=EmbeddingsGigaR,
    metadata_columns=list(VECTOR_METADATA_COLUMNS),
    id_column=VECTOR_ID_COLUMN
))


# ----------------Подключение к базам--------------

# ----------------Логирование--------------
async def _save_log(record: dict) -> None:
    """Асинхронная запись лога"""
    await asyncio.to_thread(_sync_save_log, record)


def _sync_save_log(record: dict) -> None:
    """Синхронная запись лога в файл."""
    with open("logs.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")


def log(func):
    """
    Декоратор для логирования вызовов функций.
    Исправлена критическая ошибка с сигнатурой (было: wrapper(args, *kwargs)).
    """

    @functools.wraps(func)
    async def wrapper(args, *kwargs):
        req_time = datetime.now()
        success = False
        response_data: Any = None

        try:
            result = await func(args, *kwargs)
            response_data = result
            success = True
            return result
        except Exception as e:
            response_data = f"ERROR: {str(e)}"
            raise
        finally:
            resp_time = datetime.now()
            duration_ms = (resp_time - req_time).total_seconds() * 1000
            record = {
                "session_id": SESSION_ID,
                "func_name": func.__name__,
                "request": {"args": args, "kwargs": kwargs},
                "request_time": req_time.isoformat(),
                "response": str(response_data)[:1000],
                "response_time": resp_time.isoformat(),
                "duration_ms": duration_ms,
                "success": success
            }
            asyncio.create_task(_save_log(record))

    return wrapper


# ----------------Логирование--------------

# ----------------Работа с реляционной базой--------------
@contextmanager
def get_db_connection():
    conn = engine.connect()
    try:
        yield conn
        if conn.in_transaction():
            conn.commit()
    except Exception:
        if conn.in_transaction():
            conn.rollback()
    finally:
        conn.close()


def _sync_db_execute(query: str, params: Optional[dict] = None) -> list:
    """
    Синхронное выполнение запроса к БД.
    """
    with get_db_connection() as conn:
        result = conn.execute(sqlalchemy_text(query), params or {})
        return result.fetchall() if result.returns_rows else []


async def db_execute(query: str, params: Optional[dict] = None) -> list:
    """
    Асинхронная обёртка для выполнения запроса с защитой семафором.
    """
    async with DB_SEMAPHORE:
        return await asyncio.to_thread(_sync_db_execute, query, params)


def select_chunk_by_pres_id_and_slide_num(presentation_id: str, slide_num: int):
    """Получение чанков по презентации и номеру слайда """
    query = f'''
        SELECT
        c.presentation_id,
        c.slide_sequence_number,
        c.chunk_number,
        c.source_slide_text,
        c.chunk_summary,
        p.report_name, 
        p.link_on_file
        FROM {CHUNKS_TABLE_SQL} c
        JOIN {PRESENTATIONS_TABLE_SQL} p ON c.presentation_id = p.id
        WHERE c.presentation_id = :presentation_id
        AND c.slide_sequence_number = :sequence_number
        ORDER BY c.chunk_number ASC
    '''
    rows = _sync_db_execute(query, {
        "presentation_id": str(presentation_id),
        "sequence_number": slide_num
    })

    return rows


def select_presentation_by_id(presentation_id: str):
    """Получение презентации по ID"""
    query = f'''
        SELECT id, report_name, text, summary, link_on_file
        FROM {PRESENTATIONS_TABLE_SQL}
        WHERE id = :id
    '''
    rows = _sync_db_execute(query, {"id": str(presentation_id)})
    return rows


# ----------------Работа с реляционной базой--------------


# ----------------Вспомогательные функции--------------
def _preload_bm25_documents() -> list[Document]:
    """Загружает документы для BM25 ОДИН РАЗ при старте приложения."""
    docs = []
    metadata_columns_sql = ", ".join(_quote_identifier(column) for column in VECTOR_METADATA_COLUMNS)
    with get_db_connection() as conn:
        result = conn.execute(sqlalchemy_text(f"SELECT content, {metadata_columns_sql} FROM {VECTOR_TABLE_SQL}"))
        for row in result:
            row_data = dict(row._mapping)
            metadata = {column: row_data.get(column) for column in VECTOR_METADATA_COLUMNS}
            docs.append(Document(page_content=row_data.get("content") or "", metadata=metadata))
    return docs


BM25_DOCS_CACHE = _preload_bm25_documents()


def _blocking_search_logic(query: str,
                           search_type: Literal["similarity", "mmr"],
                           k: int = 10,
                           fetch_k: int = None,
                           lambda_mult: float = None,
                           bm25_weight: float = 0.55,
                           vec_weight: float = 0.45

                           ) -> list[Document]:
    """
    Загружает документы для BM25 и выполняет ансамблевый поиск.
    """
    if not BM25_DOCS_CACHE:
        return []

    bm25_retriever = BM25Retriever.from_documents(BM25_DOCS_CACHE)

    if search_type == "similarity":
        vector_retriever = vectorstore.as_retriever()
    elif search_type == "mmr":
        vector_retriever = vectorstore.as_retriever(
            search_type="mmr", search_kwargs={
                "k": k,
                "fetch_k": fetch_k,
                "lambda_mult": lambda_mult
            }
        )

    ensemble_retriever = EnsembleRetriever(
        retrievers=[bm25_retriever, vector_retriever],
        weights=[bm25_weight, vec_weight]
    )

    results = ensemble_retriever.invoke(query, search_kwargs={"k": k})
    return results


# ----------------Вспомогательные функции--------------


async def search_in_vectore_store(query: str,
                                  search_type: Literal["similarity", "mmr"],
                                  k: int = 10,
                                  fetch_k: int = None,
                                  lambda_mult: float = None,
                                  bm25_weight: float = 0.55,
                                  vec_weight: float = 0.45) -> list[Document]:
    """
    Асинхронный поиск в векторном хранилище с защитой семафором.
    """
    async with DB_SEMAPHORE:
        return await asyncio.to_thread(_blocking_search_logic,
                                       query,
                                       search_type,
                                       k,
                                       fetch_k,
                                       lambda_mult,
                                       bm25_weight,
                                       vec_weight)


async def get_neighboring_slides(slides: list[Document]) -> list[list[Document]]:
    """Возвращает спиок списков соседних слайдов

    args:
    slides  - список слайдов
    returns:
    list[list[Document]] - список списков соседних документов для каждого слайда включая сам слайд
    """
    neighboring_slides = []
    CHUNK_FIELDS = ["presentation_id", "slide_sequence_number", "chunk_number", "source_slide_text", "chunk_summary",
                    "report_name", "link_on_file"]
    for doc in slides:
        seq_num = int(doc.metadata["slide_number"])
        pres_id = doc.metadata["presentation_id"]

        neighboring_nums = [n for n in range(seq_num - 1, seq_num + 2) if n > 0]

        tasks = [
            asyncio.to_thread(
                select_chunk_by_pres_id_and_slide_num,
                pres_id,
                n_num
            )
            for n_num in neighboring_nums
        ]
        results = await asyncio.gather(*tasks)

        for row_list in results:
            if not row_list:
                continue

            slide_chunks = {}
            for row in row_list:
                row_dict = dict(zip(CHUNK_FIELDS, row))
                slide_seq = row_dict["slide_sequence_number"]

                if slide_seq not in slide_chunks:
                    slide_chunks[slide_seq] = {
                        "presentation_id": row_dict["presentation_id"],
                        "sequence_number": slide_seq,
                        "slide_number": slide_seq,
                        "source_slide_text": row_dict["source_slide_text"],
                        "report_name": row_dict["report_name"],
                        "link_on_file": row_dict["link_on_file"],
                        "chunk_summaries": []
                    }
                slide_chunks[slide_seq]["chunk_summaries"].append(row_dict["chunk_summary"])

            doc_group: list[Document] = []
            for slide_seq, slide_data in slide_chunks.items():
                combined_summary = ' '.join(slide_data["chunk_summaries"])
                new_metadata = doc.metadata.copy()
                new_metadata.update({
                    "presentation_id": slide_data["presentation_id"],
                    "sequence_number": slide_data["sequence_number"],
                    "slide_number": slide_data["slide_number"],
                    "text": slide_data["source_slide_text"],
                    "summary": combined_summary,
                    "report_name": slide_data["report_name"],
                    "link_on_file": slide_data["link_on_file"],
                })

                doc_group.append(
                    Document(
                        page_content=combined_summary,
                        metadata=new_metadata
                    )
                )

            neighboring_slides.append(doc_group)
    return neighboring_slides


@log
async def rerank_docs_with_llm(user_query: str, documents: list[Document]) -> list[Document]:
    '''
    Функция реранжирования документов с помощью LLM
    args:
    user_query  - запрос пользователя
    documents  - список релевантых документов

    returns:
    list[Document]  - реранжированный список наиболее релевантных документов
    '''

    docs_texts, doc_map = [], {}
    for idx, doc in enumerate(documents):
        try:
            n = doc.metadata.get("report_name")
            print("BEFORE RERANKED", n, doc.metadata.get("sequence_number") or doc.metadata.get("slide_number"))
            docs_texts.append(f"Document ID: {idx} \n Content: {doc.page_content} \n---\n")
            doc_map[idx] = doc
        except:
            pass
    docs_texts_str = "".join(docs_texts)

    chain = ChatPromptTemplate.from_template(prompt_reranking) | GigaChat_Max
    res_llm = await chain.ainvoke(
        {
            "user_input": user_query,
            "texts_for_reranking": docs_texts_str
        })
    try:
        list_ = eval(res_llm.content)
    except:
        print("## Ошибка ##", res_llm.content)
        list_ = []

    print("after reranked", list_)
    result_list = []
    for d in list_:
        result_list.append(doc_map[d])
    return result_list


def _filter_unique_slides_by_pres_id_seq_num(slides: list[Document]) -> list[Document]:
    '''Вовзаращет список уникальных документов'''
    seen = set()
    result = []
    for slide in slides:
        key = (
            slide.metadata.get("presentation_id"),
            slide.metadata.get("sequence_number") or slide.metadata.get("slide_number"),
        )
        if key not in seen:
            seen.add(key)
            result.append(slide)
    return result


def _enrich_report_documents(reports: list[Document]) -> list[Document]:
    for report in reports:
        presentation_id = report.metadata.get("presentation_id")
        if not presentation_id:
            continue
        rows = select_presentation_by_id(str(presentation_id))
        if not rows:
            continue
        row = dict(rows[0]._mapping)
        report.metadata.update(
            {
                "text": row.get("text") or row.get("summary") or report.page_content,
                "link_on_file": row.get("link_on_file") or "",
                "sequence_number": report.metadata.get("sequence_number") or "",
            }
        )
    return reports


async def _preparing_documents_for_reranking(documents: list[Document]) -> list[Document]:
    vec_bm_search_result_slides = [vc_r for vc_r in documents if vc_r.metadata.get("type") == "slide_chunk"]
    vec_bm_search_result_reports = [vc_r for vc_r in documents if vc_r.metadata.get("type") == "report"]
    neighboring_slides: list[list[Document]] = await get_neighboring_slides(vec_bm_search_result_slides)
    neighboring_slides_uniques = _filter_unique_slides_by_pres_id_seq_num(
        [doc for group in neighboring_slides for doc in group])
    return neighboring_slides_uniques + _enrich_report_documents(vec_bm_search_result_reports)


mcp = FastMCP("RAG Service")


@dataclass
class SearchedInform:
    retrieved_content: list[str]
    documents_metadata: list[dict]


@log
@mcp.tool(exclude_args=["search_type", "k", "fetch_k", "lambda_mult", "bm25_weight", "vec_weight"])
async def search_information_in_vectore_store(
        query: str,
        search_type: Literal["similarity", "mmr"] = "similarity",
        k: int = 10,
        fetch_k: int = 50,
        lambda_mult: float = 0.5,
        bm25_weight: float = 0.55,
        vec_weight: float = 0.45

) -> SearchedInform:
    import time
    t0 = time.perf_counter()
    """
        Инструмент для поиска информации в векторной базе знаний.

        Args:
        query: Запрос пользователя для поиска

        Returns:
        SearchedInform: Результат поиска с контентом, метаданными и опциональным ответом LLM
    """
    print("ВЫЗОВ ИНСТРУМЕНТА", query)
    vec_bm_search_result = await search_in_vectore_store(query, search_type, k,
                                                         fetch_k, lambda_mult, bm25_weight, vec_weight)
    if not vec_bm_search_result:
        return SearchedInform(
            retrieved_content=["Не найдено релевантных документов"],
            documents_metadata=[{"error": "empty_result"}],
        )

    try:
        all_docs = await _preparing_documents_for_reranking(vec_bm_search_result)
    except Exception as e:
        print('Ошибка search_information_in_vectore_store', e)
        return SearchedInform(
            retrieved_content=["Не удалось обработать документы"],
            documents_metadata=[{"error": "empty_result"}],
        )

    try:
        reranked_documents = await rerank_docs_with_llm(query, all_docs)
    except Exception:
        reranked_documents = [vec_bm_search_result[0]]

    source_markdown_texts_with_pres_name = []
    for r_d in reranked_documents:
        print("Документ", r_d)
        try:
            r_d.metadata["sequence_number"] = r_d.metadata.get("sequence_number") or r_d.metadata.get("slide_number", "")
            source_text = r_d.metadata.get("text") or r_d.page_content
            source_markdown_texts_with_pres_name.append(
                f"Название документа: {r_d.metadata['report_name'].split('.')[0]} \n\n Текст:                             {source_text}")
            r_d.metadata.pop("text", None)
        except Exception as e:
            print('Ошибка search_information_in_vectore_store: ', e)
            print('Ошибка search_information_in_vectore_store:', r_d)
    print('ВРЕМЯ ВЫПОЛНЕНИЯ ПОИСКА: ', time.perf_counter() - t0, "ЗАПРОС:", query)

    return SearchedInform(
        retrieved_content=source_markdown_texts_with_pres_name,
        documents_metadata=[r_d.metadata for r_d in reranked_documents],
    )


if __name__ == "__main__":
    mcp.run(transport="http", port=RAG_TOOL_PORT)
