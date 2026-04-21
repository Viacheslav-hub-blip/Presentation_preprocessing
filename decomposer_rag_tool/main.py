import sys
import ast
import uuid
import json
import urllib3
import asyncio
import operator
import functools
from fastmcp import FastMCP
from datetime import datetime
from typing import TypedDict, Annotated, Any

from langgraph.graph import StateGraph, END
from langchain.messages import HumanMessage, SystemMessage
from langchain_mcp_adapters.client import MultiServerMCPClient

from sber_kitai_sdk_langchain.system_chat_model import KitaiSystemChatModel
from sber_kitai_sdk_langchain.system_embedding_chat_model import KitaiSystemEmbeddings
from sber_kitai_sdk_py.generated.api_client import ApiClient
from sber_kitai_sdk_py.generated.configuration import Configuration

try:
    from prompts import (
        PROMPT_DECOMPOSE_QUERY as prompt_decompose_query,
        PROMPT_FOR_FINAL_RESPONSE as prompt_for_final_res,
        PROMPT_SELECT_RELEVANT_CONTEXT as prompt_select_relevant_context,
    )
except ImportError:
    from decomposer_rag_tool.prompts import (
        PROMPT_DECOMPOSE_QUERY as prompt_decompose_query,
        PROMPT_FOR_FINAL_RESPONSE as prompt_for_final_res,
        PROMPT_SELECT_RELEVANT_CONTEXT as prompt_select_relevant_context,
    )

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ---------Настраиваемые переменные-------
RAG_TOOL = 8115
DECOMPOSER_RUN_PORT = 8117
MCP_TOOLS_URL = f"http://127.0.0.1:{RAG_TOOL}/mcp"

SESSION_ID = str(uuid.uuid4())
# ---------Настраиваемые переменные-------

# ---------Инициализация GigaChat-------
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

# ---------Инициализация GigaChat-------


# ---------Prompt запросы-------




# ---------Prompt запросы-------


# ---------Подключение к mcp инструменту-------
async def make_client(server_config: dict) -> MultiServerMCPClient:
    multi_client = MultiServerMCPClient(server_config)
    return multi_client


async def load_tools(server_config: dict) -> list:
    """Получает инструменты от MCP-сервиса. Если сервис недоступен — бросает ConnectionError."""

    client = await make_client(server_config)
    try:
        tools = await client.get_tools()
    except Exception as e:
        raise ConnectionError(f"Не удалось получить инструменты от сервиса: {e}")

    _langchain_tools = tools
    return _langchain_tools


MCP_TOOL_CONFIG = {
    "graphics-tools": {
        "transport": "streamable_http",
        "url": MCP_TOOLS_URL,
    }
}

_langchain_tools: list = None


async def load_agent_tools() -> list:
    """Получает инструменты от MCP-сервиса. Если сервис недоступен — бросает ConnectionError."""
    global _langchain_tools, MCP_TOOL_CONFIG
    if _langchain_tools is not None:
        return _langchain_tools

    _langchain_tools = await load_tools(MCP_TOOL_CONFIG)
    return _langchain_tools


# ---------Подключение к mcp инструменту-------


# --------- Вспомогательные функции-------
def _deduplicate_seacrh_results(search_results: list):
    ''' Удаляет  дубликаты документов в полученных ответах'''
    seen = set()
    result = []

    for item in search_results:
        contents = item.get('retrieved_content', [])
        metas = item.get('documents_metadata', [])

        new_contents = []
        new_metas = []

        for i in range(min(len(contents), len(metas))):
            meta = metas[i]
            pres_id = meta.get('presentation_id')
            seq_num = meta.get('sequence_number') or meta.get('slide_number')

            key = (str(pres_id), str(seq_num))
            if key not in seen:
                seen.add(key)
                new_contents.append(contents[i])
                new_metas.append(meta)
        result.append({
            'retrieved_content': new_contents,
            'documents_metadata': new_metas
        })
    return result


def _flatten_deduplicated_results(deduplicated_results: list):
    '''Преобразует вложенный список в плоский'''
    flat_docs = []
    for search_res in deduplicated_results:
        contents = search_res.get("retrieved_content", [])
        metadatas = search_res.get("documents_metadata", [])
        for content, meta in zip(contents, metadatas):
            flat_docs.append({
                "retrieved_content": content,
                "documents_metadata": meta
            })
    return flat_docs


def _format_results_for_relevance_selection(results_flatten: list[dict]) -> str:
    fragments = []
    for idx, item in enumerate(results_flatten):
        meta = item.get("documents_metadata") or {}
        slide_number = meta.get("sequence_number") or meta.get("slide_number") or ""
        report_name = meta.get("report_name") or ""
        fragments.append(
            f"Fragment ID: {idx}\n"
            f"Report: {report_name}\n"
            f"Slide: {slide_number}\n"
            f"Content: {item.get('retrieved_content', '')}\n---"
        )
    return "\n".join(fragments)


def _parse_selected_fragment_ids(raw_response: str, max_index: int) -> list[int]:
    try:
        parsed = ast.literal_eval(raw_response.strip())
    except (SyntaxError, ValueError):
        return []
    if not isinstance(parsed, list):
        return []

    selected_ids = []
    for item in parsed:
        if isinstance(item, int) and 0 <= item <= max_index and item not in selected_ids:
            selected_ids.append(item)
    return selected_ids


async def select_relevant_results_with_llm(question: str, results_flatten: list[dict]) -> list[dict]:
    if len(results_flatten) <= 1:
        return results_flatten

    fragments = _format_results_for_relevance_selection(results_flatten)
    response = await GigaChat_Max.ainvoke(
        prompt_select_relevant_context.format(
            user_input=question,
            retrieved_fragments=fragments,
        )
    )
    selected_ids = _parse_selected_fragment_ids(response.content, len(results_flatten) - 1)
    if not selected_ids:
        return results_flatten[:1]
    return [results_flatten[idx] for idx in selected_ids[:5]]


# --------- Вспомогательные функции-------


# ---------Описание Pipeline-------
class AgentState(TypedDict):
    original_question: str  # входной запрос
    sub_queries: list[str]  # список запросов после декомпозиции
    retrieved_context: Annotated[list[str], operator.add]  # накопленный контекст
    docs_metadata: list[dict]
    final_answer: str


async def decomposer_node(state: AgentState):
    question = state["original_question"]
    messages = [
        SystemMessage(prompt_decompose_query),
        HumanMessage(question)
    ]
    response = await GigaChat_Max.ainvoke(messages)
    result = eval(response.content)
    return {"sub_queries": result}


async def retriever_node(state: AgentState):
    docs_metadata, texts = [], []
    queries = state["sub_queries"]
    tools = await load_agent_tools()
    search_in_vec_tool = [t for t in tools if t.name == "search_information_in_vectore_store"][0]

    async def call_mcp_tool(query: str) -> dict:
        result = await search_in_vec_tool.ainvoke({"query": query})
        try:
            return eval(result)
        except:
            print("## Ошибка ##")
            print(result)
            return {}

    tasks = [call_mcp_tool(q) for q in queries]
    results = await asyncio.gather(*tasks)
    results_flatten = _flatten_deduplicated_results(_deduplicate_seacrh_results(results))
    try:
        results_flatten = await select_relevant_results_with_llm(state["original_question"], results_flatten)
    except Exception as e:
        print("## Ошибка фильтра релевантности ##", e)

    for res in results_flatten:
        texts.append(res["retrieved_content"])
        docs_metadata.append(res["documents_metadata"])
    combined_text = [
        (
            f"Запрос к векторной базе: {queries[min(idx, len(queries) - 1)]}\n \n "
            f"Номер слайда: {m.get('sequence_number') or m.get('slide_number', '')} "
            f"Результат поиска: {r} "
        )
        for idx, (r, m) in enumerate(zip(texts, docs_metadata))
    ]
    return {"retrieved_context": combined_text, "docs_metadata": docs_metadata}


async def generate_result_answer(state: AgentState):
    question = state["original_question"]
    context = "\n\n".join(state["retrieved_context"])

    context_with_source_links = (context
                                 + "## Используемые источники##:"
                                 + "\n".join(
                [f"Номер слайда: {item.get('sequence_number') or item.get('slide_number', '')} Название отчета: {item.get('report_name', '')}" for item in
                 state["docs_metadata"]])
                                 + "##Доступные ссылки на источники (презентации), ОБЯЗАТЕЛЬНО К УКАЗАНИЮ##:"
                                 + "\n".join(set([
                                                     f"Название документа: {(item.get('report_name') or '').split('.')[0]} Ссылка на источник: {item.get('link_on_file')}"
                                                     for item in state["docs_metadata"] if item.get('link_on_file')]))
                                 )

    print("Контекст: ", context_with_source_links)
    reponse = await GigaChat_Max.ainvoke(
        prompt_for_final_res.format(user_input=question, finded_text=context_with_source_links))
    return {"final_answer": reponse.content}


# ---------Описание Pipeline-------

# ---------Инициализация графа-------
workflow = StateGraph(AgentState)
workflow.add_node("decompose", decomposer_node),
workflow.add_node("retrieve", retriever_node),
workflow.add_node("group_context", generate_result_answer)

workflow.set_entry_point("decompose")
workflow.add_edge("decompose", "retrieve")
workflow.add_edge("retrieve", "group_context")
workflow.add_edge("group_context", END)

app = workflow.compile()
# ---------Инициализация графа-------

# ---------Создание сервера-------
mcp_agent = FastMCP("RAG Tool")


@mcp_agent.tool()
async def search_in_vec_store_agent(query: str) -> dict:
    '''
    Инструмент для поиска исторических показателей / информации компании на основе отчетов, презентаций и другой документации.
    Используй этот инструмент, когда тебя интересуют фактические значения, которые использовались в документах

    Примеры запросов на которые может ответить инструмент:
         - каким было значение эффективности в 2025 году?
         - что есть в компании про AI
         - Сколько партнеров у компании
    и другие подобные запросы

    Не используй этот инструмент для получения актуальной информации или информации, когда может быстро меняться
    Args:
    query  - запрос пользоватея
    '''

    print("Запрос пользователя:", query)
    print(GigaChat_Max.invoke("hi"))

    try:

        final_state = await app.ainvoke({"original_question": query})
        print("Ответ:", final_state["final_answer"])
        print('-' * 10)
        return {"status": 200, "answer": final_state["final_answer"],
                "source_markdown_content": final_state["retrieved_context"], "metadata": final_state["docs_metadata"]}
    except Exception as e:
        return {"status": 500, "answer": f"Сервис сейчас недоступен: {str(e)}", "source_markdown_content": "",
                "metadata": [{}]}


if __name__ == "__main__":
    mcp_agent.run(transport="http", port=DECOMPOSER_RUN_PORT)
