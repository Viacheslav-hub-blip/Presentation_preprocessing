"""
Что содержит: функции рендеринга изображений страниц PDF, экспорта изображений слайдов PowerPoint и выбора готовых картинок.
За что отвечает: за подготовку визуальных данных, которые затем отправляются в VLM для анализа содержимого слайдов.
Где используется: импортируется в `src.app.services.processor` для получения изображений слайдов.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Optional, Sequence


def render_pdf_page_images(
    pdf_path: str | Path,
    output_dir: str | Path | None = None,
) -> list[str]:
    """Рендерит страницы PDF в изображения и возвращает пути к ним."""
    try:
        import fitz
    except ImportError as exc:
        raise ImportError("Для получения изображений из PDF требуется пакет `PyMuPDF`.") from exc

    output_path = Path(output_dir) if output_dir else Path(tempfile.mkdtemp(prefix="pdf_pages_"))
    output_path.mkdir(parents=True, exist_ok=True)

    document = fitz.open(str(pdf_path))
    image_paths: list[str] = []
    try:
        for page_index, page in enumerate(document, start=1):
            pixmap = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
            image_path = output_path / f"page_{page_index:04d}.png"
            pixmap.save(str(image_path))
            image_paths.append(str(image_path))
    finally:
        document.close()
    return image_paths


def export_slide_images(
    pptx_path: str | Path,
    output_dir: str | Path | None = None,
) -> list[str]:
    """Экспортирует слайды PowerPoint в PNG-изображения."""
    output_path = Path(output_dir) if output_dir else Path(tempfile.mkdtemp(prefix="pptx_slides_"))
    output_path.mkdir(parents=True, exist_ok=True)

    try:
        import pythoncom
        import win32com.client
    except ImportError as exc:
        raise ImportError("Для экспорта слайдов требуется автоматизация PowerPoint через `pywin32`.") from exc

    pythoncom.CoInitialize()
    app = win32com.client.Dispatch("PowerPoint.Application")
    app.Visible = 1
    deck = None
    try:
        deck = app.Presentations.Open(str(Path(pptx_path).resolve()), WithWindow=False)
        deck.SaveAs(str(output_path.resolve()), 18)
    finally:
        if deck is not None:
            deck.Close()
        app.Quit()
        pythoncom.CoUninitialize()

    return [str(path) for path in sorted(output_path.glob("Slide*.PNG"), key=_slide_image_sort_key)]


def resolve_slide_images(
    pptx_path: str | Path | None,
    *,
    slide_image_paths: Optional[Sequence[str | Path]] = None,
    slide_images_dir: str | Path | None = None,
    export_if_missing: bool = True,
) -> list[str]:
    """Возвращает готовые изображения слайдов или создаёт их при необходимости."""
    if slide_image_paths:
        return [str(Path(path)) for path in slide_image_paths]
    if slide_images_dir:
        directory = Path(slide_images_dir)
        images = sorted(
            [path for path in directory.iterdir() if path.suffix.lower() in {".png", ".jpg", ".jpeg"}],
            key=_slide_image_sort_key,
        )
        return [str(path) for path in images]
    if export_if_missing and pptx_path is not None:
        return export_slide_images(pptx_path)
    raise ValueError(
        "Не переданы изображения слайдов для обработки через VLM. "
        "Если используется markdown без pptx, нужно передать `slide_image_paths` или `slide_images_dir`."
    )


def _slide_image_sort_key(path: Path) -> int:
    """Возвращает числовой ключ сортировки по номеру слайда в имени файла."""
    digits = "".join(ch for ch in path.stem if ch.isdigit())
    return int(digits) if digits else 0
