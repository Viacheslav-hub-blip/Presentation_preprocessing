"""
Что содержит: конфиг клиента Qwen VLM, кодирование изображений в base64 и методы обращения к OpenAI-совместимому VLM API.
За что отвечает: за работу с визуальной моделью, которая распознает текст на слайдах и описывает визуальное содержимое.
Где используется: подключается в `src.app.core.config` и `src.app.services.processor` для обработки изображений слайдов.
"""

from __future__ import annotations

"""Модуль клиента VLM-модели Qwen.

В этом файле расположены:
- dataclass-конфиг клиента VLM;
- функция кодирования изображения в base64;
- клиент для вызова VLM по OpenAI-совместимому API;
- методы для распознавания текста со слайда и описания визуальной части слайда.
"""

import asyncio
import base64
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional


@dataclass(slots=True)
class QwenVLMConfig:
    """Параметры подключения и ограничения для вызова Qwen VLM."""

    base_url: str
    model_name: str = "Qwen3-VL-8B-Instruct"
    api_key: str = "EMPTY"
    timeout: int = 3600
    max_tokens: int = 4096


def encode_image(image_path: str | Path) -> str:
    """Кодирует локальное изображение в base64 для отправки в API."""

    with Path(image_path).open("rb") as image_file:
        return base64.b64encode(image_file.read()).decode("utf-8")


class QwenVLMClient:
    """Клиент для вызова VLM-модели по OpenAI-совместимому API."""

    def __init__(self, config: QwenVLMConfig):
        """Инициализирует клиент и подготавливает внутреннее API-подключение."""
        self._config = config
        self._client = self._build_client(config)

    @staticmethod
    def _build_client(config: QwenVLMConfig) -> Any:
        """Создает объект OpenAI-клиента для дальнейших запросов к VLM."""

        try:
            from openai import OpenAI
        except ImportError as exc:
            raise ImportError("Для работы клиента Qwen VLM требуется пакет `openai`.") from exc

        return OpenAI(
            api_key=config.api_key,
            base_url=config.base_url,
            timeout=config.timeout,
        )

    def generate_from_image(
        self,
        *,
        image_path: str | Path,
        system_prompt: Optional[str] = None,
        user_prompt: str = "Обработай изображение",
        max_tokens: Optional[int] = None,
    ) -> str:
        """Синхронно отправляет изображение в VLM и возвращает текстовый ответ."""

        base64_image = encode_image(image_path)
        messages = []

        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})

        messages.append(
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/jpeg;base64,{base64_image}",
                        },
                    },
                    {
                        "type": "text",
                        "text": user_prompt,
                    },
                ],
            }
        )

        response = self._client.chat.completions.create(
            model=self._config.model_name,
            messages=messages,
            max_tokens=max_tokens or self._config.max_tokens,
        )
        return response.choices[0].message.content or ""

    async def agenerate_from_image(
        self,
        *,
        image_path: str | Path,
        system_prompt: Optional[str] = None,
        user_prompt: str = "Обработай изображение",
        max_tokens: Optional[int] = None,
    ) -> str:
        """Асинхронная обертка над синхронным вызовом VLM."""

        return await asyncio.to_thread(
            self.generate_from_image,
            image_path=image_path,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            max_tokens=max_tokens,
        )

    async def atranscribe_slide(
        self,
        image_path: str | Path,
        *,
        system_prompt: str,
        user_prompt: str = "Полностью перенеси информацию со слайда.",
    ) -> str:
        """Просит VLM полностью перенести содержимое со слайда."""

        return await self.agenerate_from_image(
            image_path=image_path,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
        )

    async def adescribe_slide_visuals(
        self,
        image_path: str | Path,
        *,
        system_prompt: str,
        user_prompt: str = "Опиши изображения на слайде, фон и визуальные элементы.",
    ) -> str:
        """Просит VLM описать фон, изображения и визуальные элементы слайда."""

        return await self.agenerate_from_image(
            image_path=image_path,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
        )


__all__ = [
    "QwenVLMClient",
    "QwenVLMConfig",
    "encode_image",
]
