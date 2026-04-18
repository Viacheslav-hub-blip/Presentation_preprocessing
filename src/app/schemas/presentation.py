"""
Что содержит: Pydantic-модели ответов API для списка, загрузки и удаления презентаций.
За что отвечает: за контракт HTTP-ответов, который возвращает FastAPI-клиентам сервис презентаций.
Где используется: импортируется в `src.app.api.endpoints.presentations` и частично в сервисе `PresentationService`.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class PresentationListItemResponse(BaseModel):
    presentation_id: str
    report_name: str
    link_on_file: str


class PresentationListResponse(BaseModel):
    items: list[PresentationListItemResponse] = Field(default_factory=list)


class PresentationUploadResponse(BaseModel):
    presentation_id: str
    report_name: str
    source_file_name: str
    pdf_file_name: str | None = None
    slides_count: int
    additional_info_applied: bool
    image_source: str
    vector_synced: bool


class PresentationDeleteResponse(BaseModel):
    presentation_id: str
    deleted_presentations: int
    deleted_chunks: int
    source_file_deleted: bool
    vector_deleted: bool
