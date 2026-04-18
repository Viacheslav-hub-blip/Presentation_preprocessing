"""
Что содержит: простой CLI-скрипт для проверки FastAPI-сервиса через загрузку PPTX и PDF.
За что отвечает: за отправку multipart/form-data запроса в `POST /presentations` без внешних зависимостей.
Где используется: запускается вручную рядом с проектом, когда нужно быстро проверить работу поднятого API.
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import uuid
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def build_multipart_body(
    *,
    fields: dict[str, str],
    files: dict[str, Path],
    boundary: str,
) -> bytes:
    """Собирает тело multipart/form-data из текстовых полей и файлов."""
    line_break = b"\r\n"
    body_parts: list[bytes] = []

    for field_name, field_value in fields.items():
        body_parts.append(f"--{boundary}".encode("utf-8"))
        body_parts.append(
            f'Content-Disposition: form-data; name="{field_name}"'.encode("utf-8")
        )
        body_parts.append(b"")
        body_parts.append(field_value.encode("utf-8"))

    for field_name, file_path in files.items():
        content_type = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
        body_parts.append(f"--{boundary}".encode("utf-8"))
        body_parts.append(
            (
                f'Content-Disposition: form-data; name="{field_name}"; '
                f'filename="{file_path.name}"'
            ).encode("utf-8")
        )
        body_parts.append(f"Content-Type: {content_type}".encode("utf-8"))
        body_parts.append(b"")
        body_parts.append(file_path.read_bytes())

    body_parts.append(f"--{boundary}--".encode("utf-8"))
    body_parts.append(b"")
    return line_break.join(body_parts)


def upload_presentation(
    *,
    base_url: str,
    pptx_path: Path,
    pdf_path: Path | None,
    additional_info: str,
    report_name: str | None,
    presentation_id: str | None,
    timeout_seconds: int,
) -> tuple[int, str]:
    """Отправляет презентацию в FastAPI-сервис и возвращает HTTP-статус и тело ответа."""
    boundary = f"----presentation-upload-{uuid.uuid4().hex}"
    fields = {
        "additional_info": additional_info,
    }
    if report_name:
        fields["report_name"] = report_name
    if presentation_id:
        fields["presentation_id"] = presentation_id

    files = {
        "pptx_file": pptx_path,
    }
    if pdf_path is not None:
        files["pdf_file"] = pdf_path

    body = build_multipart_body(fields=fields, files=files, boundary=boundary)
    request = Request(
        url=f"{base_url.rstrip('/')}/presentations",
        data=body,
        headers={
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Content-Length": str(len(body)),
        },
        method="POST",
    )

    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            return response.getcode(), response.read().decode("utf-8")
    except HTTPError as error:
        return error.code, error.read().decode("utf-8", errors="replace")
    except URLError as error:
        raise RuntimeError(
            f"Не удалось подключиться к сервису по адресу `{base_url}`: {error.reason}"
        ) from error


def parse_args() -> argparse.Namespace:
    """Разбирает аргументы командной строки."""
    parser = argparse.ArgumentParser(
        description="Проверка FastAPI-сервиса загрузкой презентации."
    )
    parser.add_argument(
        "--base-url",
        default="http://127.0.0.1:8000",
        help="Базовый адрес FastAPI-сервиса. По умолчанию: http://127.0.0.1:8000",
    )
    parser.add_argument(
        "--pptx",
        required=True,
        help="Путь к PPTX-файлу презентации.",
    )
    parser.add_argument(
        "--pdf",
        help="Путь к PDF-файлу той же презентации. Необязательный аргумент.",
    )
    parser.add_argument(
        "--additional-info",
        default="",
        help="Дополнительный контекст, который будет передан в API.",
    )
    parser.add_argument(
        "--report-name",
        help="Имя отчёта. Если не указать, сервис возьмёт имя PPTX-файла.",
    )
    parser.add_argument(
        "--presentation-id",
        help="UUID презентации. Если не указать, сервис сгенерирует его сам.",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=300,
        help="Таймаут HTTP-запроса в секундах. По умолчанию: 300.",
    )
    return parser.parse_args()


def validate_paths(pptx_path: Path, pdf_path: Path | None) -> None:
    """Проверяет, что переданные файлы существуют и имеют ожидаемые расширения."""
    if not pptx_path.exists() or not pptx_path.is_file():
        raise FileNotFoundError(f"Не найден PPTX-файл: {pptx_path}")
    if pptx_path.suffix.lower() != ".pptx":
        raise ValueError(f"Файл `{pptx_path}` должен иметь расширение `.pptx`.")

    if pdf_path is None:
        return
    if not pdf_path.exists() or not pdf_path.is_file():
        raise FileNotFoundError(f"Не найден PDF-файл: {pdf_path}")
    if pdf_path.suffix.lower() != ".pdf":
        raise ValueError(f"Файл `{pdf_path}` должен иметь расширение `.pdf`.")


def main() -> None:
    """Запускает проверочную загрузку презентации в FastAPI-сервис."""
    args = parse_args()
    pptx_path = Path(args.pptx).expanduser().resolve()
    pdf_path = Path(args.pdf).expanduser().resolve() if args.pdf else None

    validate_paths(pptx_path, pdf_path)

    status_code, response_text = upload_presentation(
        base_url=args.base_url,
        pptx_path=pptx_path,
        pdf_path=pdf_path,
        additional_info=args.additional_info,
        report_name=args.report_name,
        presentation_id=args.presentation_id,
        timeout_seconds=args.timeout,
    )

    print(f"HTTP {status_code}")
    try:
        print(json.dumps(json.loads(response_text), ensure_ascii=False, indent=2))
    except json.JSONDecodeError:
        print(response_text)


if __name__ == "__main__":
    main()
