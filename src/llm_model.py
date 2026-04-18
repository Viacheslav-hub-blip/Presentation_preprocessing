"""
Что содержит: инициализацию текстовой LLM-модели и модели эмбеддингов, которые используются приложением.
За что отвечает: за отдельную точку подключения LangChain-объектов без смешивания этого кода с общими настройками проекта.
Где используется: импортируется в `src.app.core.config`, откуда модели передаются в сервисный слой приложения.
"""

from __future__ import annotations


# Создай здесь экземпляры своих LangChain-объектов.
# TEXT_MODEL должен поддерживать метод `ainvoke(...)` или совместимый с ним интерфейс.
# EMBEDDINGS_MODEL должен быть совместим с кодом записи в векторную БД.
#
# Пример:
# from langchain_openai import ChatOpenAI, OpenAIEmbeddings
# TEXT_MODEL = ChatOpenAI(model="gpt-4.1-mini", temperature=0)
# EMBEDDINGS_MODEL = OpenAIEmbeddings(model="text-embedding-3-large")

TEXT_MODEL = None
EMBEDDINGS_MODEL = None
