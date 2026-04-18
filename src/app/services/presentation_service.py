"""
Что содержит: сервисный класс `PresentationService` с логикой загрузки, списка и удаления презентаций.
За что отвечает: за orchestration всего сценария API: валидацию файлов, вызов пайплайна обработки, запись в БД и откаты.
Где используется: создается в `src.app.api.dependencies` и вызывается endpoint'ами из `src.app.api.endpoints.presentations`.
"""

from __future__ import annotations

from pathlib import Path
from uuid import UUID, uuid4

from fastapi import HTTPException, UploadFile

from src.app.core.config import AppConfig, ModelRegistry, build_vision_model
from src.app.db.storage import (
    PresentationRecord,
    SlideChunkRecord,
    create_relational_tables,
    create_vector_store,
    delete_presentation,
    delete_presentation_from_vector_db,
    select_chunks,
    select_presentation_list,
    select_presentations,
    sync_presentation_to_relational_db,
    sync_presentation_to_vector_db,
)
from src.app.prompts.prompts import get_processing_prompts
from src.app.schemas.presentation import (
    PresentationDeleteResponse,
    PresentationListItemResponse,
    PresentationListResponse,
    PresentationUploadResponse,
)
from src.app.services.processor import build_storage_records, process_presentation


class PresentationService:
    """Инкапсулирует сценарии загрузки, просмотра и удаления презентаций."""

    def __init__(self, config: AppConfig, models: ModelRegistry):
        """Сохраняет конфигурацию приложения и подключенные модели."""
        self._config = config
        self._models = models

    async def upload_presentation(
        self,
        *,
        pptx_file: UploadFile,
        pdf_file: UploadFile | None,
        additional_info: str,
        report_name: str | None,
        presentation_id: str | None,
    ) -> PresentationUploadResponse:
        """Сохраняет загруженные файлы, обрабатывает презентацию и пишет результат в обе базы данных."""
        create_relational_tables(self._config.relational_db)
        normalized_presentation_id = self._normalize_presentation_id(presentation_id)

        pptx_filename = self._validate_uploaded_filename(
            upload=pptx_file,
            expected_suffix=".pptx",
            missing_name_detail="У загружаемого PPTX-файла должно быть имя.",
            invalid_suffix_detail="Поле `pptx_file` должно содержать файл .pptx.",
        )
        pdf_filename = ""
        if pdf_file is not None:
            pdf_filename = self._validate_uploaded_filename(
                upload=pdf_file,
                expected_suffix=".pdf",
                missing_name_detail="У загружаемого PDF-файла должно быть имя.",
                invalid_suffix_detail="Поле `pdf_file` должно содержать файл .pdf.",
            )

        stored_pptx_path = self._config.upload_dir / f"{uuid4()}_{pptx_filename}"
        stored_pdf_path = self._config.upload_dir / f"{uuid4()}_{pdf_filename}" if pdf_file is not None else None

        existing_presentations = select_presentations(
            self._config.relational_db,
            presentation_id=normalized_presentation_id,
            limit=1,
        )
        if existing_presentations:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Презентация с id `{normalized_presentation_id}` уже существует. "
                    "Передай другой `presentation_id` или не передавай его вовсе."
                ),
            )

        presentation_record: PresentationRecord | None = None
        chunk_records: list[SlideChunkRecord] = []

        try:
            await self._save_upload_file(pptx_file, stored_pptx_path)
            if pdf_file is not None and stored_pdf_path is not None:
                await self._save_upload_file(pdf_file, stored_pdf_path)

            processing_result = await process_presentation(
                report_name=report_name or Path(pptx_filename).stem,
                text_model=self._models.text_model,
                vision_model=build_vision_model(),
                prompts=get_processing_prompts(),
                presentation_id=normalized_presentation_id,
                max_concurrency=self._config.max_concurrency,
                export_slide_images_if_missing=stored_pdf_path is None,
                additional_context=additional_info,
                pptx_path=str(stored_pptx_path),
                pdf_path=str(stored_pdf_path) if stored_pdf_path is not None else None,
            )
            presentation_record, chunk_records = build_storage_records(
                processing_result,
                link_on_file=str(stored_pptx_path),
            )

            sync_presentation_to_relational_db(
                self._config.relational_db,
                presentation_record,
                chunk_records,
            )
            await self._sync_to_vector_db(presentation_record, chunk_records)
        except HTTPException:
            await self._rollback_failed_upload(
                stored_pptx_path=stored_pptx_path,
                stored_pdf_path=stored_pdf_path,
                presentation_record=presentation_record,
                chunk_records=chunk_records,
            )
            raise
        except (ImportError, ValueError) as exc:
            await self._rollback_failed_upload(
                stored_pptx_path=stored_pptx_path,
                stored_pdf_path=stored_pdf_path,
                presentation_record=presentation_record,
                chunk_records=chunk_records,
            )
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            await self._rollback_failed_upload(
                stored_pptx_path=stored_pptx_path,
                stored_pdf_path=stored_pdf_path,
                presentation_record=presentation_record,
                chunk_records=chunk_records,
            )
            raise HTTPException(status_code=500, detail=str(exc)) from exc

        return PresentationUploadResponse(
            presentation_id=processing_result.presentation_id,
            report_name=processing_result.report_name,
            source_file_name=pptx_filename,
            pdf_file_name=pdf_filename or None,
            slides_count=len(processing_result.slides),
            additional_info_applied=bool(additional_info.strip()),
            image_source="pdf" if stored_pdf_path is not None else "pptx_export",
            vector_synced=True,
        )

    def list_presentations(self, *, limit: int) -> PresentationListResponse:
        """Возвращает ограниченный список сохранённых презентаций."""
        if limit <= 0:
            raise HTTPException(status_code=400, detail="Параметр `limit` должен быть больше нуля.")

        create_relational_tables(self._config.relational_db)
        items = select_presentation_list(self._config.relational_db, limit=limit)
        return PresentationListResponse(
            items=[
                PresentationListItemResponse(
                    presentation_id=item.presentation_id,
                    report_name=item.report_name,
                    link_on_file=item.link_on_file,
                )
                for item in items
            ]
        )

    async def remove_presentation(self, presentation_id: str) -> PresentationDeleteResponse:
        """Удаляет презентацию из реляционной БД, векторной PostgreSQL и файлового хранилища."""
        create_relational_tables(self._config.relational_db)
        normalized_presentation_id = self._normalize_presentation_id(presentation_id)

        presentations = select_presentations(
            self._config.relational_db,
            presentation_id=normalized_presentation_id,
            limit=1,
        )
        if not presentations:
            raise HTTPException(status_code=404, detail="Презентация не найдена.")

        presentation = presentations[0]
        chunks = select_chunks(self._config.relational_db, presentation_id=normalized_presentation_id)

        source_file_backup = self._backup_managed_source_file(presentation.link_on_file)
        relational_deleted = False
        source_file_deleted = False
        vector_deleted = False

        try:
            vector_store = await create_vector_store(
                self._config.vector_db,
                embedding_service=self._models.embeddings_model,
                initialize_table=False,
            )
            await delete_presentation_from_vector_db(
                vector_store,
                normalized_presentation_id,
                chunks,
            )
            vector_deleted = True

            deleted_presentations = delete_presentation(self._config.relational_db, normalized_presentation_id)
            relational_deleted = deleted_presentations > 0
            source_file_deleted = self._delete_managed_source_file(presentation.link_on_file)
        except Exception as exc:
            rollback_errors = await self._rollback_removed_presentation(
                presentation=presentation,
                chunks=chunks,
                source_file_backup=source_file_backup,
                relational_deleted=relational_deleted,
                vector_deleted=vector_deleted,
                source_file_deleted=source_file_deleted,
            )
            if rollback_errors:
                detail = (
                    "Не удалось удалить презентацию атомарно. "
                    f"Ошибки отката: {'; '.join(rollback_errors)}"
                )
                raise HTTPException(status_code=500, detail=detail) from exc
            raise HTTPException(status_code=500, detail=str(exc)) from exc

        return PresentationDeleteResponse(
            presentation_id=normalized_presentation_id,
            deleted_presentations=deleted_presentations,
            deleted_chunks=len(chunks),
            source_file_deleted=source_file_deleted,
            vector_deleted=vector_deleted,
        )

    def _normalize_presentation_id(self, presentation_id: str | None) -> str:
        """Проверяет и нормализует идентификатор презентации в формат UUID-строки."""
        if not presentation_id:
            return str(uuid4())
        try:
            return str(UUID(presentation_id))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="`presentation_id` должен быть валидным UUID.") from exc

    def _validate_uploaded_filename(
        self,
        *,
        upload: UploadFile,
        expected_suffix: str,
        missing_name_detail: str,
        invalid_suffix_detail: str,
    ) -> str:
        """Проверяет имя загруженного файла и его ожидаемое расширение."""
        filename = Path(upload.filename or "").name
        if not filename:
            raise HTTPException(status_code=400, detail=missing_name_detail)
        if Path(filename).suffix.lower() != expected_suffix:
            raise HTTPException(status_code=400, detail=invalid_suffix_detail)
        return filename

    async def _save_upload_file(self, upload: UploadFile, destination: Path) -> None:
        """Сохраняет загруженный файл на диск потоково, без чтения целиком в память."""
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("wb") as output_file:
            while True:
                chunk = await upload.read(1024 * 1024)
                if not chunk:
                    break
                output_file.write(chunk)
        await upload.close()

    async def _rollback_failed_upload(
        self,
        *,
        stored_pptx_path: Path,
        stored_pdf_path: Path | None,
        presentation_record: PresentationRecord | None,
        chunk_records: list[SlideChunkRecord],
    ) -> None:
        """Удаляет частично сохранённые данные, если обработка загрузки завершилась ошибкой."""
        if presentation_record is not None:
            await self._delete_presentation_from_vector_db(
                presentation_record.presentation_id,
                chunk_records,
            )
            delete_presentation(self._config.relational_db, presentation_record.presentation_id)

        stored_pptx_path.unlink(missing_ok=True)
        if stored_pdf_path is not None:
            stored_pdf_path.unlink(missing_ok=True)

    def _is_managed_upload(self, file_path: str) -> bool:
        """Проверяет, что путь указывает на файл из управляемой директории загрузок."""
        if not file_path:
            return False
        try:
            resolved_path = Path(file_path).resolve()
            resolved_path.relative_to(self._config.upload_dir)
            return True
        except (OSError, ValueError):
            return False

    def _delete_managed_source_file(self, file_path: str) -> bool:
        """Удаляет исходный файл только если он находится в директории загрузок приложения."""
        if not self._is_managed_upload(file_path):
            return False
        path = Path(file_path)
        if not path.exists():
            return False
        path.unlink()
        return True

    def _backup_managed_source_file(self, file_path: str) -> bytes | None:
        """Читает содержимое управляемого файла для возможного отката удаления."""
        if not self._is_managed_upload(file_path):
            return None
        path = Path(file_path)
        if not path.exists():
            return None
        return path.read_bytes()

    def _restore_managed_source_file(self, file_path: str, backup: bytes | None) -> None:
        """Восстанавливает ранее сохранённую резервную копию управляемого файла."""
        if backup is None or not self._is_managed_upload(file_path):
            return
        path = Path(file_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(backup)

    async def _delete_presentation_from_vector_db(
        self,
        presentation_id: str,
        chunk_records: list[SlideChunkRecord],
    ) -> None:
        """Удаляет презентацию и её чанки из векторной PostgreSQL."""
        vector_store = await create_vector_store(
            self._config.vector_db,
            embedding_service=self._models.embeddings_model,
            initialize_table=False,
        )
        await delete_presentation_from_vector_db(
            vector_store,
            presentation_id,
            chunk_records,
        )

    async def _sync_to_vector_db(
        self,
        presentation_record: PresentationRecord,
        chunk_records: list[SlideChunkRecord],
    ) -> None:
        """Синхронизирует презентацию с векторной PostgreSQL как обязательной частью RAG-пайплайна."""
        vector_store = await create_vector_store(
            self._config.vector_db,
            embedding_service=self._models.embeddings_model,
            initialize_table=True,
        )
        await sync_presentation_to_vector_db(vector_store, presentation_record, chunk_records)

    async def _rollback_removed_presentation(
        self,
        *,
        presentation: PresentationRecord,
        chunks: list[SlideChunkRecord],
        source_file_backup: bytes | None,
        relational_deleted: bool,
        vector_deleted: bool,
        source_file_deleted: bool,
    ) -> list[str]:
        """Пробует откатить удаление презентации и собирает ошибки отката, если они были."""
        rollback_errors: list[str] = []

        if relational_deleted:
            try:
                sync_presentation_to_relational_db(
                    self._config.relational_db,
                    presentation,
                    chunks,
                )
            except Exception as exc:
                rollback_errors.append(f"postgres restore failed: {exc}")

        if vector_deleted:
            try:
                vector_store = await create_vector_store(
                    self._config.vector_db,
                    embedding_service=self._models.embeddings_model,
                    initialize_table=False,
                )
                await sync_presentation_to_vector_db(vector_store, presentation, chunks)
            except Exception as exc:
                rollback_errors.append(f"vector restore failed: {exc}")

        if source_file_deleted:
            try:
                self._restore_managed_source_file(presentation.link_on_file, source_file_backup)
            except Exception as exc:
                rollback_errors.append(f"file restore failed: {exc}")

        return rollback_errors
