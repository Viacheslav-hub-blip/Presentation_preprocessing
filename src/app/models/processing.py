"""
Что содержит: dataclass-модели результатов обработки одного слайда и всей презентации.
За что отвечает: за хранение промежуточных и итоговых данных пайплайна до сохранения в базы и выдачи наружу.
Где используется: импортируется в `src.app.services.processor` при сборке результата обработки.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class SlideProcessingResult:
    """Хранит результат полной обработки одного слайда."""

    slide_number: int
    original_text: str
    slide_image_path: str
    llm_structured_text: str
    vlm_transcribed_text: str
    vlm_visual_description: str
    final_slide_description: str


@dataclass(slots=True)
class PresentationProcessingResult:
    """Хранит итог обработки всей презентации целиком."""

    presentation_id: str
    report_name: str
    source_pptx_path: str
    additional_context: str = ""
    slides: list[SlideProcessingResult] = field(default_factory=list)

    @property
    def full_text(self) -> str:
        """Собирает исходный текст всех слайдов в одну строку."""
        return "\n\n".join(slide.original_text for slide in self.slides if slide.original_text)

    @property
    def final_summary(self) -> str:
        """Формирует итоговое описание презентации из описаний слайдов и допконтекста."""
        base_summary = "\n\n".join(
            f"### СЛАЙД {slide.slide_number}\n{slide.final_slide_description}"
            for slide in self.slides
            if slide.final_slide_description
        )
        additional_context = self.additional_context.strip()
        if not additional_context:
            return base_summary
        if not base_summary:
            return additional_context
        return f"{base_summary}\n\n### ДОПОЛНИТЕЛЬНАЯ ИНФОРМАЦИЯ\n{additional_context}"
