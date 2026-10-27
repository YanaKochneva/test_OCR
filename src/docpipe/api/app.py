from __future__ import annotations

import hmac
import json
import queue
import shutil
import tempfile
import time
import uuid
import zipfile
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, Form, Header, HTTPException, Request, Response, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from starlette.background import BackgroundTask

from docpipe.api.contracts import AgentDocument, JobStatus
from docpipe.api.jobs import JobManager
from docpipe.config import AppConfig

_ALLOWED_ENGINES = {"docling"}
_ALLOWED_OUTPUTS = {"json", "md", "html", "pdf-flow", "pdf-searchable", "pdf-positional", "docx"}


def create_app(config: AppConfig | None = None) -> FastAPI:
    cfg = config or AppConfig()
    manager = JobManager(cfg)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        cfg.api.work_dir.mkdir(parents=True, exist_ok=True)
        manager.start()
        try:
            yield
        finally:
            manager.stop()

    app = FastAPI(title="DocPipe API", version="0.1.0", lifespan=lifespan)
    app.state.manager = manager
    app.state.config = cfg

    @app.exception_handler(HTTPException)
    async def http_error(_request: Request, exc: HTTPException):
        codes = {
            400: "invalid_request",
            401: "unauthorized",
            404: "not_found",
            409: "result_not_ready",
            413: "limit_exceeded",
            415: "unsupported_format",
            422: "processing_failed",
            429: "queue_full",
            503: "service_unavailable",
            504: "timeout",
        }
        return JSONResponse(
            status_code=exc.status_code,
            headers=exc.headers,
            content={
                "detail": exc.detail,
                "error": {
                    "code": codes.get(exc.status_code, "server_error"),
                    "message": exc.detail,
                },
            },
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error(_request: Request, exc: RequestValidationError):
        from fastapi.encoders import jsonable_encoder

        detail = jsonable_encoder(exc.errors())
        return JSONResponse(
            status_code=422,
            content={
                "detail": detail,
                "error": {"code": "invalid_request", "message": "Invalid request parameters"},
            },
        )

    def authorize(api_key: str | None) -> None:
        if cfg.api.require_api_key and (
            not cfg.api.api_key or not hmac.compare_digest(api_key or "", cfg.api.api_key)
        ):
            raise HTTPException(status_code=401, detail="Неверный API-ключ")

    def parse_options(raw: str | None) -> dict:
        if not raw:
            return {}
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=400, detail=f"options должен быть JSON: {exc}") from exc
        if not isinstance(value, dict):
            raise HTTPException(status_code=400, detail="options должен быть JSON-объектом")
        return value

    def validate_upload(upload: UploadFile) -> str:
        name = Path(upload.filename or "input.bin").name
        suffix = Path(name).suffix.lower()
        if suffix not in {".pdf", ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp"}:
            raise HTTPException(status_code=415, detail="Неподдерживаемый формат входного файла")
        return name

    def validate_job_options(opts: dict) -> tuple[str, list[str]]:
        engine = str(opts.get("engine", cfg.api.default_engine))
        if engine not in _ALLOWED_ENGINES:
            raise HTTPException(status_code=400, detail="unknown engine")
        outputs = opts.get("outputs", ["md", "html", "json"])
        if (
            not isinstance(outputs, list)
            or not outputs
            or not all(isinstance(item, str) for item in outputs)
        ):
            raise HTTPException(status_code=400, detail="outputs must be a list of strings")
        unsupported = sorted(set(outputs) - _ALLOWED_OUTPUTS)
        if unsupported:
            raise HTTPException(status_code=400, detail=f"unsupported outputs: {unsupported}")
        if not cfg.render.docx_enabled:
            outputs = [item for item in outputs if item != "docx"] or ["pdf-positional"]
        return engine, outputs

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/readyz")
    def readyz() -> dict[str, str]:
        if not manager.ready:
            raise HTTPException(status_code=503, detail="Модели ещё не загружены")
        return {"status": "ready"}

    @app.post("/v1/jobs", status_code=202, response_model=JobStatus)
    async def create_job(
        response: Response,
        file: UploadFile = File(...),
        options: str | None = Form(default=None),
        request_id: str | None = Form(default=None, min_length=1, max_length=200),
        x_api_key: str | None = Header(default=None),
    ) -> dict:
        authorize(x_api_key)
        opts = parse_options(options)
        name = validate_upload(file)
        engine, outputs = validate_job_options(opts)
        tmp_root = cfg.api.work_dir / "uploads"
        tmp_root.mkdir(exist_ok=True)
        upload_path = tmp_root / f"{uuid.uuid4().hex}_{name}"
        size = 0
        try:
            with upload_path.open("wb") as dst:
                while chunk := await file.read(1024 * 1024):
                    size += len(chunk)
                    if size > cfg.limits.max_file_mb * 1024 * 1024:
                        raise HTTPException(status_code=413, detail="Файл превышает лимит размера")
                    dst.write(chunk)
            if size == 0:
                raise HTTPException(status_code=400, detail="uploaded file is empty")
            try:
                job = manager.submit(upload_path, engine, outputs, request_id=request_id)
            except queue.Full as exc:
                raise HTTPException(status_code=429, detail="Очередь заполнена") from exc
            response.headers["Location"] = f"/v1/jobs/{job.id}"
            response.headers["Retry-After"] = "2"
            return manager.status(job)
        except HTTPException:
            upload_path.unlink(missing_ok=True)
            raise
        except Exception as exc:
            upload_path.unlink(missing_ok=True)
            raise HTTPException(status_code=500, detail="Unable to submit job") from exc

    @app.get("/v1/jobs/{job_id}", response_model=JobStatus)
    def get_job(job_id: str, x_api_key: str | None = Header(default=None)) -> dict:
        authorize(x_api_key)
        job = manager.get(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Задание не найдено")
        return manager.status(job)

    @app.get("/v1/jobs/{job_id}/document", response_model=AgentDocument)
    def get_document(job_id: str, x_api_key: str | None = Header(default=None)) -> dict:
        authorize(x_api_key)
        job = manager.get(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Задание не найдено")
        if job.status != "completed" or not job.document_json:
            raise HTTPException(status_code=409, detail="Результат ещё не готов")
        from docpipe.ir import Document

        document = Document.model_validate_json(job.document_json.read_text(encoding="utf-8"))
        pages = []
        for page in document.pages:
            blocks = []
            for block in sorted(page.blocks, key=lambda item: item.order):
                if block.table:
                    rows: dict[int, list] = {}
                    for cell in sorted(block.table.cells, key=lambda item: (item.row, item.col)):
                        rows.setdefault(cell.row, []).append(cell.text)
                    blocks.append("\n".join("\t".join(cells) for cells in rows.values()))
                elif block.text:
                    blocks.append(block.text)
            pages.append("\n\n".join(blocks))
        return {
            "job_id": job.id,
            "request_id": job.request_id,
            "review_required": job.review_required,
            "text": "\n\n".join(pages),
            "document": document,
        }

    @app.get("/v1/jobs/{job_id}/result")
    def get_result(job_id: str, x_api_key: str | None = Header(default=None)) -> FileResponse:
        authorize(x_api_key)
        job = manager.get(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Задание не найдено")
        if job.status != "completed" or not job.result_zip:
            raise HTTPException(status_code=409, detail="Результат ещё не готов")
        return FileResponse(job.result_zip, media_type="application/zip", filename=f"{job_id}.zip")

    @app.get("/v1/jobs/{job_id}/files/{path:path}")
    def get_file(
        job_id: str, path: str, x_api_key: str | None = Header(default=None)
    ) -> FileResponse:
        authorize(x_api_key)
        job = manager.get(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Задание не найдено")
        if job.status != "completed":
            raise HTTPException(status_code=409, detail="Result is not ready")
        root = (job.work_dir / "output").resolve()
        target = (root / path).resolve()
        if root not in target.parents and target != root:
            raise HTTPException(status_code=400, detail="Недопустимый путь")
        if not target.is_file():
            raise HTTPException(status_code=404, detail="Файл не найден")
        return FileResponse(target)

    def run_sync(
        file: UploadFile,
        options: str | None,
        x_api_key: str | None,
        timeout: float | None = None,
    ) -> FileResponse:
        authorize(x_api_key)
        opts = parse_options(options)
        if cfg.api.sync_max_pages <= 0:
            raise HTTPException(status_code=503, detail="Синхронный режим отключён")
        if not manager.ready:
            raise HTTPException(status_code=503, detail="Service is not ready")
        # Синхронный endpoint использует ту же очередь и ждёт завершения.
        name = validate_upload(file)
        engine, outputs = validate_job_options(opts)
        tmp_root = cfg.api.work_dir / "sync"
        tmp_root.mkdir(exist_ok=True)
        path = tmp_root / f"{uuid.uuid4().hex}_{name}"
        size = 0
        try:
            with path.open("wb") as dst:
                while chunk := file.file.read(1024 * 1024):
                    size += len(chunk)
                    if size > cfg.limits.max_file_mb * 1024 * 1024:
                        raise HTTPException(
                            status_code=413, detail="upload exceeds configured size limit"
                        )
                    dst.write(chunk)
        except Exception:
            path.unlink(missing_ok=True)
            raise
        if size == 0:
            path.unlink(missing_ok=True)
            raise HTTPException(status_code=400, detail="uploaded file is empty")
        job = None
        try:
            import pypdfium2 as pdfium

            suffix = path.suffix.lower()
            page_count = 1
            if suffix == ".pdf":
                try:
                    with pdfium.PdfDocument(str(path)) as pdf:
                        page_count = len(pdf)
                except Exception as exc:
                    raise HTTPException(status_code=422, detail="uploaded PDF is invalid") from exc
            else:
                from PIL import Image

                try:
                    with Image.open(path) as image:
                        page_count = getattr(image, "n_frames", 1)
                except Exception as exc:
                    raise HTTPException(
                        status_code=422, detail="Uploaded image is invalid"
                    ) from exc
            if page_count > min(cfg.api.sync_max_pages, cfg.limits.max_pages):
                raise HTTPException(
                    status_code=413, detail="Документ превышает лимит синхронного API"
                )
            try:
                job = manager.submit(path, engine, outputs)
            except queue.Full as exc:
                raise HTTPException(status_code=429, detail="Queue is full") from exc
            except RuntimeError as exc:
                raise HTTPException(status_code=503, detail="Service is shutting down") from exc
            # Ожидание ограничено конфигом, чтобы endpoint не зависал бесконечно.
            deadline = time.monotonic() + (
                cfg.api.sync_timeout_seconds if timeout is None else timeout
            )
            while time.monotonic() < deadline and job.status in {"queued", "running"}:
                time.sleep(0.05)
            if job.status == "failed":
                raise HTTPException(status_code=422, detail=job.error)
            if job.status != "completed" or not job.result_zip:
                raise HTTPException(
                    status_code=504,
                    detail={"message": job.error or "Processing timed out", "job_id": job.id},
                )
            return FileResponse(
                job.result_zip,
                media_type="application/zip",
                filename=f"{job.id}.zip",
                headers={"X-Job-ID": job.id, "X-Review-Required": str(job.review_required).lower()},
            )
        finally:
            # A timed-out request must not remove an input the worker still needs.
            if job is None or job.status not in {"queued", "running"}:
                path.unlink(missing_ok=True)

    zip_response = {
        200: {
            "description": "Recognized document package",
            "content": {"application/zip": {"schema": {"type": "string", "format": "binary"}}},
        }
    }

    @app.post(
        "/v1/parse",
        response_class=FileResponse,
        responses=zip_response,
        summary="Recognize one document and return its complete output package",
    )
    def parse_sync(
        file: UploadFile = File(...),
        options: str | None = Form(default=None),
        x_api_key: str | None = Header(default=None),
    ) -> FileResponse:
        return run_sync(file, options, x_api_key)

    @app.post(
        "/v1/recognize",
        response_class=FileResponse,
        responses=zip_response,
        summary="Recognize documents and return a single ZIP with a manifest",
    )
    def recognize(
        files: list[UploadFile] = File(..., description="One or more documents"),
        options: str | None = Form(default=None),
        x_api_key: str | None = Header(default=None),
    ) -> FileResponse:
        authorize(x_api_key)
        validate_job_options(parse_options(options))
        if not files or len(files) > cfg.api.max_batch_files:
            raise HTTPException(status_code=413, detail="Too many documents in request")
        names = [validate_upload(upload) for upload in files]
        total_bytes = 0
        for upload in files:
            # Measure the spooled stream as well as enforcing per-file limits.
            upload.file.seek(0, 2)
            size = upload.file.tell()
            upload.file.seek(0)
            if size == 0:
                raise HTTPException(status_code=400, detail="Uploaded file is empty")
            if size > cfg.limits.max_file_mb * 1024 * 1024:
                raise HTTPException(status_code=413, detail="Document exceeds size limit")
            total_bytes += size
        if total_bytes > cfg.api.max_batch_mb * 1024 * 1024:
            raise HTTPException(status_code=413, detail="Batch exceeds size limit")
        if not manager.ready:
            raise HTTPException(status_code=503, detail="Service is not ready")

        batch_id = uuid.uuid4().hex
        temporary = Path(tempfile.mkdtemp(prefix="docpipe-batch-"))
        archive_path = temporary / "results.zip"
        documents = []
        deadline = time.monotonic() + cfg.api.sync_timeout_seconds
        try:
            with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as archive:
                for index, (upload, name) in enumerate(zip(files, names), start=1):
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise HTTPException(
                            status_code=504,
                            detail={
                                "message": "Batch timed out",
                                "batch_id": batch_id,
                                "documents": documents,
                            },
                        )
                    entry = {"source_filename": name, "index": index}
                    try:
                        result = run_sync(upload, options, x_api_key, timeout=remaining)
                    except HTTPException as exc:
                        entry.update(status="failed", http_status=exc.status_code, error=exc.detail)
                        documents.append(entry)
                        if exc.status_code in {429, 503, 504}:
                            raise HTTPException(
                                status_code=exc.status_code,
                                detail={
                                    "message": "Batch processing interrupted",
                                    "batch_id": batch_id,
                                    "documents": documents,
                                },
                            ) from exc
                        continue
                    folder = f"document_{index:03d}"
                    entry.update(
                        status="completed",
                        job_id=result.headers["x-job-id"],
                        review_required=result.headers["x-review-required"] == "true",
                        directory=folder,
                    )
                    with zipfile.ZipFile(result.path) as source:
                        for member in source.infolist():
                            if not member.is_dir():
                                with (
                                    source.open(member) as src,
                                    archive.open(f"{folder}/{member.filename}", "w") as dst,
                                ):
                                    shutil.copyfileobj(src, dst, length=1024 * 1024)
                    documents.append(entry)
                completed = sum(entry["status"] == "completed" for entry in documents)
                if not completed:
                    raise HTTPException(
                        status_code=422,
                        detail={
                            "message": "No documents could be recognized",
                            "documents": documents,
                        },
                    )
                manifest = {
                    "batch_id": batch_id,
                    "status": "completed" if completed == len(files) else "partial",
                    "documents": documents,
                }
                archive.writestr(
                    "manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2)
                )
            return FileResponse(
                archive_path,
                media_type="application/zip",
                filename=f"{batch_id}.zip",
                headers={"X-Batch-ID": batch_id, "X-Batch-Status": manifest["status"]},
                background=BackgroundTask(shutil.rmtree, temporary, ignore_errors=True),
            )
        except BaseException:
            shutil.rmtree(temporary, ignore_errors=True)
            raise

    @app.delete("/v1/jobs/{job_id}", status_code=204)
    def delete_job(job_id: str, x_api_key: str | None = Header(default=None)) -> None:
        authorize(x_api_key)
        existing = manager.get(job_id)
        if existing is not None and existing.status == "running":
            raise HTTPException(status_code=409, detail="job is currently running")
        if not manager.delete(job_id):
            raise HTTPException(status_code=404, detail="Задание не найдено")

    return app


app = create_app()
