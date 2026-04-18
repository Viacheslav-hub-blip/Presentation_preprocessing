# Presentation Preprocessing

## Описание проекта

`presentation_preprocessing` — это FastAPI-сервис для подготовки презентаций к RAG-сценарию. Он принимает `PPTX`, при необходимости принимает соответствующий `PDF`, извлекает текст и визуальный контекст со слайдов, строит итоговые описания и всегда сохраняет результат в две PostgreSQL-структуры:

- в реляционные таблицы с полным текстом и чанками;
- в векторное хранилище PostgreSQL/pgvector для последующего семантического поиска.

Если запись в PostgreSQL/pgvector невозможна, загрузка считается неуспешной. Для этого проекта векторное хранилище не является опцией: оно входит в основную бизнес-логику.

## Структура проекта

### Корень проекта

- `run_api.py`
  Точка входа для запуска FastAPI через Uvicorn. Импортирует приложение из `src.app.main` и поднимает сервер с настройками из `src/project_config.py`.

- `README.md`
  Документация по архитектуре проекта, пайплайну обработки презентаций и FastAPI-ручкам.

- `pyproject.toml`
  Базовая конфигурация Python-проекта и список объявленных зависимостей.

- `uv.lock`
  Lock-файл зависимостей для `uv`.

- `.python-version`
  Версия Python, на которую ориентируется локальное окружение.

### Каталог `src`

- `src/project_config.py`
  Центральный файл настроек приложения. Здесь задаются параметры FastAPI, директории, строки подключения к реляционной и векторной PostgreSQL, а также конфигурация VLM-клиента.

- `src/llm_model.py`
  Единая точка создания LangChain-объектов. В этом файле должны быть инициализированы:
  - `TEXT_MODEL` — текстовая LLM для обработки содержания слайдов;
  - `EMBEDDINGS_MODEL` — модель эмбеддингов для записи документов в pgvector.

- `src/vlm_client.py`
  Клиент визуальной модели. Используется для OCR по изображению слайда и для получения структурированного описания визуальной части презентации.

### Каталог `src/app`

- `src/app/main.py`
  Создаёт объект FastAPI и подключает общий API-роутер.

- `src/app/api/router.py`
  Общий роутер API. Подключает набор ручек для работы с презентациями.

- `src/app/api/dependencies.py`
  Слой FastAPI-зависимостей. Здесь создаётся `PresentationService` с уже собранным конфигом и моделями.

- `src/app/api/endpoints/presentations.py`
  Все HTTP-ручки проекта:
  - загрузка презентации;
  - получение списка презентаций;
  - удаление презентации.

- `src/app/core/config.py`
  Преобразует настройки из `src/project_config.py` и объекты из `src/llm_model.py` в типизированные объекты приложения:
  - `AppConfig`;
  - `ModelRegistry`.

  Также здесь выполняются обязательные проверки:
  - задана ли реляционная PostgreSQL;
  - задана ли векторная PostgreSQL;
  - подключены ли `TEXT_MODEL` и `EMBEDDINGS_MODEL`.

- `src/app/services/presentation_service.py`
  Главный сервисный слой API. Отвечает за полный orchestration-сценарий:
  - валидацию входных файлов;
  - сохранение входных файлов;
  - запуск пайплайна обработки;
  - запись результата в реляционную PostgreSQL;
  - запись результата в PostgreSQL/pgvector;
  - откат при сбое;
  - удаление презентации из всех хранилищ.

- `src/app/services/processor.py`
  Основной пайплайн обработки одной презентации. Координирует извлечение данных из файлов, обработку каждого слайда через LLM/VLM и сбор итогового результата.

- `src/app/services/file_extractors.py`
  Утилиты для извлечения текста из `PPTX`, `PDF` и вспомогательных представлений слайдов.

- `src/app/services/image_renderers.py`
  Подготовка изображений слайдов. Если `PDF` передан, изображения берутся из него; если нет, слайды экспортируются из PowerPoint.

- `src/app/prompts/prompts.py`
  Набор всех промптов для LLM и VLM: нормализация текста, OCR, визуальное описание, финальное описание слайда и summary по презентации.

- `src/app/models/processing.py`
  Внутренние dataclass-модели результатов обработки презентации и отдельных слайдов.

- `src/app/schemas/presentation.py`
  Pydantic-схемы HTTP-ответов FastAPI.

- `src/app/db/storage.py`
  Слой доступа к данным. Содержит:
  - dataclass-конфиги БД;
  - модели записей презентаций и чанков;
  - создание таблиц;
  - операции чтения и удаления;
  - синхронизацию с реляционной PostgreSQL;
  - синхронизацию с PostgreSQL/pgvector.

### Служебные `__init__.py`

Файлы `__init__.py` внутри `src/app/...` помечают каталоги как Python-пакеты и прямой прикладной логики почти не содержат.

## Логика обработки презентации

Ниже описан основной сценарий, который выполняется в `POST /presentations`.

1. Клиент отправляет `pptx_file` и при необходимости `pdf_file`.
2. FastAPI передаёт запрос в `PresentationService.upload_presentation(...)`.
3. Сервис:
   - проверяет корректность имён и расширений;
   - нормализует или генерирует `presentation_id`;
   - сохраняет загруженные файлы на диск;
   - убеждается, что презентации с таким `presentation_id` ещё нет в реляционной БД.
4. Затем вызывается `process_presentation(...)` из [src/app/services/processor.py](C:/Users/Slav4ik/PycharmProjects/presentation_preprocessing/src/app/services/processor.py).
5. Внутри пайплайна обработки:
   - из `PPTX` извлекается текст по слайдам;
   - для каждого слайда подготавливается изображение;
   - если есть `PDF`, изображения берутся из PDF-страниц;
   - если `PDF` нет, сервис экспортирует изображения из PowerPoint;
   - слайды обрабатываются параллельно с ограничением `MAX_CONCURRENCY`.
6. Для каждого слайда выполняется несколько этапов:
   - текстовая LLM нормализует и структурирует текстовое содержимое;
   - VLM делает OCR по изображению;
   - VLM описывает визуальную структуру слайда;
   - текстовая LLM объединяет текстовый и визуальный контекст в финальное семантическое описание.
7. После обработки всех слайдов формируется:
   - `PresentationProcessingResult` для всей презентации;
   - `PresentationRecord` для таблицы презентаций;
   - набор `SlideChunkRecord` для чанков, пригодных для RAG-поиска.
8. Данные записываются в реляционную PostgreSQL.
9. Затем эти же данные обязательно записываются в PostgreSQL/pgvector:
   - один векторный документ уровня всей презентации;
   - по одному векторному документу на каждый чанк.
10. Если на любом шаге возникает ошибка, сервис выполняет откат:
   - удаляет частично записанные записи из БД;
   - удаляет уже загруженные файлы;
   - откатывает запись в векторное хранилище, если она уже началась.

## Что сохраняется в базе данных

### Реляционная PostgreSQL

Используются две основные таблицы.

- Таблица презентаций хранит:
  - `id`;
  - `report_name`;
  - `text`;
  - `summary`;
  - `link_on_file`.

- Таблица чанков хранит:
  - `presentation_id`;
  - `slide_sequence_number`;
  - `chunk_number`;
  - `source_slide_text`;
  - `chunk_summary`.

### PostgreSQL/pgvector

Векторное хранилище получает:

- один документ верхнего уровня по всей презентации;
- по одному документу на каждый чанк.

Для каждого векторного документа записываются метаданные:

- `unique_id`;
- `presentation_id`;
- `report_name`;
- `type`;
- `slide_number`;
- `chunk_number`;
- `total_chunks`.

Эти данные используются для RAG-поиска, фильтрации результатов и обратной привязки найденного чанка к исходной презентации и конкретному слайду.

## FastAPI API

Все ручки находятся под префиксом `/presentations`.

### `POST /presentations`

Загружает презентацию, обрабатывает её и сохраняет результат и в обычную PostgreSQL, и в PostgreSQL/pgvector.

#### Тип запроса

`multipart/form-data`

#### Аргументы запроса

- `pptx_file: UploadFile`
  Обязательный `PPTX`-файл презентации.

- `pdf_file: UploadFile | None`
  Необязательный `PDF` той же презентации. Если он передан, изображения слайдов будут извлекаться из него.

- `additional_info: str`
  Необязательный дополнительный контекст для модели. По умолчанию используется пустая строка.

- `report_name: str | None`
  Необязательное имя отчёта. Если поле не передано, сервис использует имя `PPTX`-файла без расширения.

- `presentation_id: str | None`
  Необязательный UUID презентации. Если не передан, сервис создаёт его сам.

#### Возвращаемый тип

```python
class PresentationUploadResponse(BaseModel):
    presentation_id: str
    report_name: str
    source_file_name: str
    pdf_file_name: str | None = None
    slides_count: int
    additional_info_applied: bool
    image_source: str
    vector_synced: bool
```

#### Смысл полей ответа

- `presentation_id` — UUID обработанной презентации.
- `report_name` — итоговое имя отчёта.
- `source_file_name` — имя исходного `PPTX`.
- `pdf_file_name` — имя `PDF`, если он был передан.
- `slides_count` — количество обработанных слайдов.
- `additional_info_applied` — был ли реально использован дополнительный контекст.
- `image_source` — источник изображений: `pdf` или `pptx_export`.
- `vector_synced` — признак того, что запись в векторную PostgreSQL завершилась успешно. В текущей логике успешный ответ этого эндпоинта означает, что значение всегда `True`.

### `GET /presentations`

Возвращает список ранее сохранённых презентаций.

#### Query-параметры

- `limit: int`
  Максимальное количество записей в ответе. Значение должно быть больше нуля.

#### Возвращаемый тип

```python
class PresentationListItemResponse(BaseModel):
    presentation_id: str
    report_name: str
    link_on_file: str


class PresentationListResponse(BaseModel):
    items: list[PresentationListItemResponse]
```

#### Смысл полей ответа

- `items` — список найденных презентаций.
- `presentation_id` — UUID презентации.
- `report_name` — имя отчёта.
- `link_on_file` — путь к сохранённому исходному файлу.

### `DELETE /presentations/{presentation_id}`

Удаляет презентацию из реляционной PostgreSQL, из PostgreSQL/pgvector и, если файл находится в управляемой директории загрузки, удаляет его с диска.

#### Path-параметры

- `presentation_id: str`
  UUID удаляемой презентации.

#### Возвращаемый тип

```python
class PresentationDeleteResponse(BaseModel):
    presentation_id: str
    deleted_presentations: int
    deleted_chunks: int
    source_file_deleted: bool
    vector_deleted: bool
```

#### Смысл полей ответа

- `presentation_id` — UUID удалённой презентации.
- `deleted_presentations` — количество удалённых записей уровня презентации в реляционной БД.
- `deleted_chunks` — количество удалённых чанков.
- `source_file_deleted` — был ли удалён исходный файл с диска.
- `vector_deleted` — были ли удалены документы из векторной PostgreSQL.

## Основные ошибки API

- `400 Bad Request`
  Возвращается при некорректных входных данных:
  - неправильное расширение файла;
  - пустое имя файла;
  - невалидный `presentation_id`;
  - `limit <= 0`;
  - ошибки в обработке входных данных.

- `404 Not Found`
  Возвращается, если удаляемая презентация не найдена.

- `409 Conflict`
  Возвращается, если презентация с тем же `presentation_id` уже существует.

- `500 Internal Server Error`
  Возвращается при внутренних ошибках:
  - ошибка реляционной PostgreSQL;
  - ошибка PostgreSQL/pgvector;
  - ошибка модели;
  - ошибка отката;
  - отсутствует обязательный `TEXT_MODEL` или `EMBEDDINGS_MODEL`;
  - не заданы обязательные настройки векторной PostgreSQL.

## Где настраиваются модели

### `src/llm_model.py`

В этом файле обязательно должны быть созданы два LangChain-объекта:

```python
TEXT_MODEL = ...
EMBEDDINGS_MODEL = ...
```

Пример:

```python
from langchain_openai import ChatOpenAI, OpenAIEmbeddings

TEXT_MODEL = ChatOpenAI(
    model="gpt-4.1-mini",
    temperature=0,
)

EMBEDDINGS_MODEL = OpenAIEmbeddings(
    model="text-embedding-3-large",
)
```

### `src/project_config.py`

Этот файл должен содержать:

- настройки FastAPI;
- путь для загружаемых файлов;
- строку подключения к реляционной PostgreSQL;
- строку подключения к PostgreSQL/pgvector;
- имя таблицы презентаций;
- имя таблицы чанков;
- имя таблицы векторного хранилища;
- параметры VLM-клиента.

Для данного проекта параметры векторной БД обязательны. Без `VECTOR_CONNECTION_STRING` и `VECTOR_TABLE` приложение не должно считаться корректно настроенным.

## Запуск проекта

Минимальный сценарий запуска:

1. Создать и активировать виртуальное окружение.
2. Установить зависимости проекта.
3. Заполнить [src/project_config.py](C:/Users/Slav4ik/PycharmProjects/presentation_preprocessing/src/project_config.py).
4. Создать `TEXT_MODEL` и `EMBEDDINGS_MODEL` в [src/llm_model.py](C:/Users/Slav4ik/PycharmProjects/presentation_preprocessing/src/llm_model.py).
5. Запустить сервер:

```bash
python run_api.py
```

## Что важно помнить

- Если `PDF` не передан, изображения слайдов будут экспортироваться из PowerPoint.
- Проект рассчитан на RAG, поэтому запись в PostgreSQL/pgvector является обязательной частью успешной загрузки.
- В `pyproject.toml` может быть перечислена не вся фактически используемая инфраструктура. По коду также нужны библиотеки вроде `fastapi`, `sqlalchemy`, `python-pptx`, `pypdf`, `PyMuPDF`, `pywin32`, `openai`, `langchain-core` и `langchain-postgres`.
- Каталоги `.venv`, `.idea` и `__pycache__` не относятся к прикладной логике сервиса.
