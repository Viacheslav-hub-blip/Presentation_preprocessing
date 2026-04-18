"""
Что содержит: HTTP-обработчики для загрузки, просмотра списка и удаления презентаций.
За что отвечает: за внешний REST-интерфейс работы с презентациями на уровне FastAPI.
Где используется: подключается в `src.app.api.router` и вызывает `PresentationService` через dependency injection.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, UploadFile

from app.api.dependencies import get_presentation_service
from app.schemas.presentation import (
    PresentationDeleteResponse,
    PresentationListResponse,
    PresentationUploadResponse,
)
from app.services.presentation_service import PresentationService


router = APIRouter(prefix="/presentations", tags=["presentations"])


@router.post("", response_model=PresentationUploadResponse)
async def upload_presentation(
    pptx_file: UploadFile = File(...),
    pdf_file: UploadFile | None = File(None),
    additional_info: str = Form(""),
    report_name: str | None = Form(None),
    presentation_id: str | None = Form(None),
    service: PresentationService = Depends(get_presentation_service),
):
    """Принимает загруженную презентацию и запускает её обработку."""
    return await service.upload_presentation(
        pptx_file=pptx_file,
        pdf_file=pdf_file,
        additional_info=additional_info,
        report_name=report_name,
        presentation_id=presentation_id,
    )


@router.get("", response_model=PresentationListResponse)
async def list_presentations(
    limit: int,
    service: PresentationService = Depends(get_presentation_service),
):
    """Возвращает список ранее обработанных презентаций."""
    return service.list_presentations(limit=limit)


@router.delete("/{presentation_id}", response_model=PresentationDeleteResponse)
async def remove_presentation(
    presentation_id: str,
    service: PresentationService = Depends(get_presentation_service),
):
    """Удаляет презентацию и связанные с ней данные из хранилищ."""
    return await service.remove_presentation(presentation_id)
