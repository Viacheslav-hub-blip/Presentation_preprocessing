"""
Что содержит: функции очистки и разбора ответов LLM/VLM моделей.
За что отвечает: за снятие markdown-оберток, разбор JSON/Python-like строк и извлечение текстовых полей.
Где используется: в `src.app.services.processor` при обработке слайдов и в `src.app.db.storage` перед записью в vector store.
"""

from __future__ import annotations

import ast
import json
import re
from typing import Any


def strip_markdown_json_block(text: str | None) -> str:
    """Убирает markdown-обертку вокруг JSON-ответа модели, если она есть."""
    if not text:
        return ""

    cleaned_text = text.strip()
    fenced_match = re.fullmatch(
        r"```(?:json)?\s*(.*?)\s*```",
        cleaned_text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if fenced_match:
        return fenced_match.group(1).strip()
    return cleaned_text


def extract_json_object_text(text: str) -> str:
    """Возвращает текст первого JSON-объекта из ответа модели, если он там есть."""
    start_index = text.find("{")
    end_index = text.rfind("}")
    if start_index == -1 or end_index == -1 or end_index <= start_index:
        return text
    return text[start_index : end_index + 1]


def parse_structured_text(text: str) -> Any:
    """Пробует безопасно разобрать строку как JSON или Python literal."""
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    try:
        return ast.literal_eval(text)
    except (SyntaxError, ValueError):
        return None


def extract_quoted_field_from_text(text: str, field_name: str) -> str | None:
    """Достает строковое поле из почти-JSON, если строгий парсинг не сработал."""
    pattern = rf'"{re.escape(field_name)}"\s*:\s*"(.*)"\s*}}\s*$'
    match = re.search(pattern, text, flags=re.DOTALL)
    if not match:
        return None

    field_value = match.group(1)
    try:
        return json.loads(f'"{field_value}"')
    except json.JSONDecodeError:
        return (
            field_value.replace(r"\"", '"')
            .replace(r"\n", "\n")
            .replace(r"\t", "\t")
            .strip()
        )


def parse_model_json_response(response_text: str | None) -> Any:
    """Разбирает JSON-ответ модели, включая случай, когда JSON был возвращен строкой внутри JSON."""
    candidate = strip_markdown_json_block(response_text)
    for _ in range(3):
        parsed_response = parse_structured_text(candidate)
        if parsed_response is None:
            json_object_text = extract_json_object_text(candidate)
            if json_object_text == candidate:
                return None
            parsed_response = parse_structured_text(json_object_text)
            if parsed_response is None:
                return None

        if isinstance(parsed_response, str):
            nested_candidate = strip_markdown_json_block(parsed_response)
            if nested_candidate == candidate:
                return parsed_response
            candidate = nested_candidate
            continue

        return parsed_response

    return None


def extract_text_field_from_model_response(response_text: str | None, field_name: str) -> str:
    """Достает строковое поле из JSON-ответа модели или возвращает очищенный исходный ответ."""
    cleaned_response = strip_markdown_json_block(response_text)
    parsed_response = parse_model_json_response(cleaned_response)
    for _ in range(3):
        if not isinstance(parsed_response, dict):
            break

        field_value = parsed_response.get(field_name)
        if isinstance(field_value, str) and field_value.strip():
            nested_response = parse_model_json_response(field_value)
            if isinstance(nested_response, dict):
                parsed_response = nested_response
                continue
            return field_value.strip()
        break

    regex_value = extract_quoted_field_from_text(cleaned_response, field_name)
    if regex_value:
        return regex_value.strip()
    return cleaned_response


def extract_summary_from_model_response(response_text: str | None) -> str:
    """Достает поле `summary` из JSON-ответа модели и возвращает чистый текст."""
    return extract_text_field_from_model_response(response_text, "summary")


def extract_structured_text_from_model_response(response_text: str | None) -> str:
    """Преобразует JSON semantic splitter в человекочитаемый структурированный текст."""
    parsed_response = parse_model_json_response(response_text)
    if not isinstance(parsed_response, dict):
        return strip_markdown_json_block(response_text)

    parts: list[str] = []
    fragments = parsed_response.get("fragments")
    if isinstance(fragments, list):
        parts.extend(str(fragment).strip() for fragment in fragments if str(fragment).strip())

    notes = parsed_response.get("notes")
    if isinstance(notes, str) and notes.strip() and notes.strip().lower() != "null":
        parts.append(f"Примечание: {notes.strip()}")

    if parts:
        return "\n".join(parts)
    return strip_markdown_json_block(response_text)
