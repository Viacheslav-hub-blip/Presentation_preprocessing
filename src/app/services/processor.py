"""
Что содержит: функции вызова текстовой модели, обработки отдельных слайдов и сборки итоговых записей для хранения.
За что отвечает: за основной пайплайн анализа презентации, который объединяет текст, изображения, LLM и VLM.
Где используется: вызывается из `src.app.services.presentation_service` при загрузке и обработке презентации.
"""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import Any, Sequence
from uuid import UUID, uuid4

from src.app.db.storage import PresentationRecord, SlideChunkRecord
from src.app.prompts.prompts import ProcessingPrompts
from src.app.models.processing import PresentationProcessingResult, SlideProcessingResult
from src.app.services.file_extractors import load_markdown_slides, load_pdf_slides, load_pptx_slides
from src.app.services.image_renderers import export_slide_images, render_pdf_page_images, resolve_slide_images
from src.vlm_client import QwenVLMClient


async def invoke_text_model(model: Any, prompt: str, retries: int = 5, delay_seconds: float = 1.0) -> str:
    """Вызывает текстовую модель с повторными попытками при временных сбоях."""
    last_error: Exception | None = None
    for attempt in range(retries):
        try:
            response = await model.ainvoke(prompt)
            return str(response.content)
        except Exception as error:
            last_error = error
            if attempt == retries - 1:
                break
            await asyncio.sleep(delay_seconds)
    assert last_error is not None
    raise last_error


def strip_markdown_json_block(text: str) -> str:
    """Убирает markdown-обёртку вокруг JSON-ответа модели, если она есть."""
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


def extract_summary_from_model_response(response_text: str) -> str:
    """Достаёт поле `summary` из JSON-ответа модели и возвращает чистый текст."""
    cleaned_text = strip_markdown_json_block(response_text)
    try:
        parsed_response = json.loads(cleaned_text)
    except json.JSONDecodeError:
        json_object_text = extract_json_object_text(cleaned_text)
        try:
            parsed_response = json.loads(json_object_text)
        except json.JSONDecodeError:
            return cleaned_text

    if not isinstance(parsed_response, dict):
        return cleaned_text

    summary = parsed_response.get("summary")
    if not isinstance(summary, str) or not summary.strip():
        return cleaned_text

    return summary.strip()


async def process_slide(
    *,
    slide_text: str,
    slide_index: int,
    slide_image_path: str | Path | None,
    report_name: str,
    text_model: Any,
    vision_model: QwenVLMClient | None,
    prompts: ProcessingPrompts,
    is_prestructured: bool = False,
) -> SlideProcessingResult:
    """Обрабатывает один слайд и собирает по нему полный набор текстовых и визуальных данных."""
    if is_prestructured:
        llm_structured_task = asyncio.sleep(0, result=slide_text)
    else:
        llm_structured_task = invoke_text_model(
            text_model,
            prompts.PROMPT_SEMANTIC_TEXT_SPLITTER.format(
                report_name=report_name,
                slide_number=slide_index + 1,
                text=slide_text,
                slide_text=slide_text,
            ),
        )

    if slide_image_path and vision_model is not None:
        vlm_transcription_task = vision_model.atranscribe_slide(
            slide_image_path,
            system_prompt=prompts.PROMPT_VLM_TRANSCRIBE_SLIDE,
            user_prompt=prompts.PROMPT_VLM_TRANSCRIBE_SLIDE_USER,
        )
        vlm_visuals_task = vision_model.adescribe_slide_visuals(
            slide_image_path,
            system_prompt=prompts.PROMPT_VLM_DESCRIBE_IMAGES,
            user_prompt=prompts.PROMPT_VLM_DESCRIBE_IMAGES_USER,
        )
        llm_structured_text, vlm_transcribed_text, vlm_visual_description = await asyncio.gather(
            llm_structured_task,
            vlm_transcription_task,
            vlm_visuals_task,
        )
    else:
        llm_structured_text = await llm_structured_task
        vlm_transcribed_text = ""
        vlm_visual_description = ""

    raw_final_slide_description = await invoke_text_model(
        text_model,
        prompts.PROMPT_DENSE_SLIDE_SUMMARY.format(
            report_name=report_name,
            slide_number=slide_index + 1,
            text=slide_text,
            slide_text=slide_text,
            original_text=slide_text,
            pptx_extracted_text=slide_text,
            llm_structured_text=llm_structured_text,
            vlm_transcribed_text=vlm_transcribed_text,
            vlm_visual_description=vlm_visual_description,
        ),
    )
    final_slide_description = extract_summary_from_model_response(raw_final_slide_description)

    return SlideProcessingResult(
        slide_number=slide_index + 1,
        original_text=slide_text,
        slide_image_path=str(slide_image_path or ""),
        llm_structured_text=llm_structured_text,
        vlm_transcribed_text=vlm_transcribed_text,
        vlm_visual_description=vlm_visual_description,
        final_slide_description=final_slide_description,
    )


async def process_presentation(
    *,
    pptx_path: str | Path,
    pdf_path: str | Path | None = None,
    report_name: str | None,
    text_model: Any,
    vision_model: QwenVLMClient | None,
    prompts: ProcessingPrompts,
    presentation_id: UUID | str | None = None,
    slide_image_paths: Sequence[str | Path] | None = None,
    slide_images_dir: str | Path | None = None,
    max_concurrency: int = 4,
    export_slide_images_if_missing: bool = True,
    additional_context: str | None = None,
) -> PresentationProcessingResult:
    """Обрабатывает всю презентацию по слайдам и возвращает общий результат пайплайна."""
    source_path = Path(pptx_path)
    slides = load_pptx_slides(source_path)

    if slide_image_paths or slide_images_dir:
        images = resolve_slide_images(
            None,
            slide_image_paths=slide_image_paths,
            slide_images_dir=slide_images_dir,
            export_if_missing=False,
        )
    elif pdf_path is not None:
        images = render_pdf_page_images(pdf_path)
    else:
        images = resolve_slide_images(
            source_path,
            slide_image_paths=slide_image_paths,
            slide_images_dir=slide_images_dir,
            export_if_missing=export_slide_images_if_missing,
        )

    if images and len(images) != len(slides):
        raise ValueError(
            f"РљРѕР»РёС‡РµСЃС‚РІРѕ СЃР»Р°Р№РґРѕРІ РІ PPTX РЅРµ СЃРѕРІРїР°РґР°РµС‚ СЃ РєРѕР»РёС‡РµСЃС‚РІРѕРј РёР·РѕР±СЂР°Р¶РµРЅРёР№ РґР»СЏ VLM: "
            f"PPTX СЃРѕРґРµСЂР¶РёС‚ {len(slides)} СЃР»Р°Р№РґРѕРІ, Р° РёР·РѕР±СЂР°Р¶РµРЅРёР№ РЅР°Р№РґРµРЅРѕ {len(images)}."
        )

    effective_images: list[str | None] = list(images) if images else [None] * len(slides)
    semaphore = asyncio.Semaphore(max_concurrency)
    effective_report_name = report_name or source_path.stem
    normalized_presentation_id = str(presentation_id or uuid4())

    async def _process_single(slide_text: str, slide_index: int, image_path: str | None) -> SlideProcessingResult:
        """Ограничивает параллелизм и запускает обработку одного слайда."""
        async with semaphore:
            return await process_slide(
                slide_text=slide_text,
                slide_index=slide_index,
                slide_image_path=image_path,
                report_name=effective_report_name,
                text_model=text_model,
                vision_model=vision_model,
                prompts=prompts,
                is_prestructured=False,
            )

    tasks = [
        _process_single(slide_text, slide_index, image_path)
        for slide_index, (slide_text, image_path) in enumerate(zip(slides, effective_images))
    ]
    slide_results = await asyncio.gather(*tasks)

    return PresentationProcessingResult(
        presentation_id=normalized_presentation_id,
        report_name=effective_report_name,
        source_pptx_path=str(source_path),
        additional_context=(additional_context or "").strip(),
        slides=sorted(slide_results, key=lambda slide: slide.slide_number),
    )


def build_storage_records(
    processing_result: PresentationProcessingResult,
    *,
    link_on_file: str = "",
) -> tuple[PresentationRecord, list[SlideChunkRecord]]:
    """Преобразует результат обработки в записи для реляционной и векторной БД."""
    presentation_record = PresentationRecord(
        id=processing_result.presentation_id,
        report_name=processing_result.report_name,
        text=processing_result.full_text,
        summary=processing_result.final_summary,
        link_on_file=link_on_file or processing_result.source_pptx_path,
    )
    chunk_records = [
        SlideChunkRecord(
            presentation_id=processing_result.presentation_id,
            slide_sequence_number=slide.slide_number,
            chunk_number=1,
            source_slide_text=_combine_slide_sources(
                processing_result=processing_result,
                slide=slide,
                chunk_number=1,
                total_chunks=1,
            ),
            chunk_summary=slide.final_slide_description,
        )
        for slide in processing_result.slides
    ]
    return presentation_record, chunk_records


def _combine_slide_sources(
    *,
    processing_result: PresentationProcessingResult,
    slide: SlideProcessingResult,
    chunk_number: int,
    total_chunks: int,
) -> str:
    """Склеивает все источники данных по слайду в один текст для хранения."""
    metadata_lines = [
        "CHUNK_METADATA:",
        f"unique_id={processing_result.presentation_id}+slide{slide.slide_number}+chunk{chunk_number}",
        f"presentation_id={processing_result.presentation_id}",
        f"slide_number={slide.slide_number}",
        f"chunk_number={chunk_number}",
        f"total_chunks={total_chunks}",
        "type=slide_chunk",
        f"report_name={processing_result.report_name}",
    ]
    parts = [
        "\n".join(metadata_lines),
        f"РЎР»Р°Р№Рґ {slide.slide_number}",
        "PPTX_EXTRACTED_TEXT:",
        slide.original_text,
        "LLM_STRUCTURED_TEXT:",
        slide.llm_structured_text,
        "VLM_TRANSCRIBED_TEXT:",
        slide.vlm_transcribed_text,
        "VLM_VISUAL_DESCRIPTION:",
        slide.vlm_visual_description,
    ]
    return "\n".join(part for part in parts if part)


__all__ = [
    "build_storage_records",
    "export_slide_images",
    "invoke_text_model",
    "load_markdown_slides",
    "load_pdf_slides",
    "load_pptx_slides",
    "process_presentation",
    "process_slide",
    "render_pdf_page_images",
    "resolve_slide_images",
]
