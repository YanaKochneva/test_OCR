from __future__ import annotations

import json
import queue
import shutil
from pathlib import Path

from fastapi import FastAPI, File, Form, Header, HTTPException, UploadFile
from contextlib import asynccontextmanager
from fastapi.responses import FileResponse, PlainTextResponse

from docpipe.api.jobs import JobManager
from docpipe.config import AppConfig


def create_app(config: AppConfig | None = None) -> FastAPI:
    cfg = config or AppConfig()
    cfg.api.work_dir.mkdir(parents=True, exist_ok=True)
    manager = JobManager(cfg)
    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        manager.start()
        try:
            yield
        finally:
            manager.stop()

    app = FastAPI(title="DocPipe API", version="0.1.0", lifespan=lifespan)
    app.state.manager = manager
    app.state.config = cfg

    def authorize(api_key: str | None) -> None:
        if cfg.api.api_key and api_key != cfg.api.api_key:
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

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/readyz")
    def readyz() -> dict[str, str]:
        if not manager.ready:
            raise HTTPException(status_code=503, detail="Модели ещё не загружены")
        return {"status": "ready"}

    @app.get("/metrics")
    def metrics() -> PlainTextResponse:
        lines = ["# TYPE docpipe_jobs_total counter"]
        for name, value in manager.metrics.items():
            lines.append(f"docpipe_{name} {value}")
        return PlainTextResponse("\n".join(lines) + "\n", media_type="text/plain; version=0.0.4")

    @app.post("/v1/jobs", status_code=202)
    async def create_job(
        file: UploadFile = File(...),
        options: str | None = Form(default=None),
        x_api_key: str | None = Header(default=None),
    ) -> dict:
        authorize(x_api_key)
        opts = parse_options(options)
        name = validate_upload(file)
        engine = str(opts.get("engine", cfg.api.default_engine))
        if engine not in {"fake", "docling", "ppstructure", "paddleocr_vl"}:
            raise HTTPException(status_code=400, detail=f"Неизвестный engine: {engine}")
        outputs = opts.get("outputs", ["md", "html", "json"])
        if not isinstance(outputs, list) or not all(isinstance(x, str) for x in outputs):
            raise HTTPException(status_code=400, detail="outputs должен быть списком строк")
        tmp_root = cfg.api.work_dir / "uploads"
        tmp_root.mkdir(exist_ok=True)
        upload_path = tmp_root / f"{__import__('uuid').uuid4().hex}_{name}"
        size = 0
        try:
            with upload_path.open("wb") as dst:
                while chunk := await file.read(1024 * 1024):
                    size += len(chunk)
                    if size > cfg.limits.max_file_mb * 1024 * 1024:
                        raise HTTPException(status_code=413, detail="Файл превышает лимит размера")
                    dst.write(chunk)
            try:
                job = manager.submit(upload_path, engine, outputs)
            except queue.Full as exc:
                raise HTTPException(status_code=429, detail="Очередь заполнена") from exc
            return {"job_id": job.id, "status": job.status}
        except HTTPException:
            upload_path.unlink(missing_ok=True)
            raise
        except Exception as exc:
            upload_path.unlink(missing_ok=True)
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @app.get("/v1/jobs/{job_id}")
    def get_job(job_id: str, x_api_key: str | None = Header(default=None)) -> dict:
        authorize(x_api_key)
        job = manager.get(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Задание не найдено")
        return manager.status(job)

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
    def get_file(job_id: str, path: str, x_api_key: str | None = Header(default=None)) -> FileResponse:
        authorize(x_api_key)
        job = manager.get(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Задание не найдено")
        root = (job.work_dir / "output").resolve()
        target = (root / path).resolve()
        if root not in target.parents and target != root:
            raise HTTPException(status_code=400, detail="Недопустимый путь")
        if not target.is_file():
            raise HTTPException(status_code=404, detail="Файл не найден")
        return FileResponse(target)

    @app.post("/v1/parse")
    async def parse_sync(
        file: UploadFile = File(...),
        options: str | None = Form(default=None),
        x_api_key: str | None = Header(default=None),
    ) -> FileResponse:
        authorize(x_api_key)
        opts = parse_options(options)
        if cfg.api.sync_max_pages <= 0:
            raise HTTPException(status_code=503, detail="Синхронный режим отключён")
        # Синхронный endpoint использует ту же очередь и ждёт завершения.
        name = validate_upload(file)
        tmp_root = cfg.api.work_dir / "sync"
        tmp_root.mkdir(exist_ok=True)
        path = tmp_root / f"{__import__('uuid').uuid4().hex}_{name}"
        with path.open("wb") as dst:
            shutil.copyfileobj(file.file, dst)
        try:
            import pypdfium2 as pdfium
            suffix = path.suffix.lower()
            page_count = 1
            if suffix == ".pdf":
                with pdfium.PdfDocument(str(path)) as pdf:
                    page_count = len(pdf)
            if page_count > cfg.api.sync_max_pages:
                raise HTTPException(status_code=413, detail="Документ превышает лимит синхронного API")
            engine = str(opts.get("engine", cfg.api.default_engine))
            if engine not in {"fake", "docling", "ppstructure", "paddleocr_vl"}:
                raise HTTPException(status_code=400, detail=f"Неизвестный engine: {engine}")
            outputs = opts.get("outputs", ["md", "html", "json"])
            job = manager.submit(path, engine, outputs)
            # Ожидание ограничено конфигом, чтобы endpoint не зависал бесконечно.
            import time
            deadline = time.monotonic() + cfg.api.sync_timeout_seconds
            while time.monotonic() < deadline and job.status in {"queued", "running"}:
                time.sleep(0.05)
            if job.status != "completed" or not job.result_zip:
                raise HTTPException(status_code=504, detail=job.error or "Обработка не завершилась вовремя")
            return FileResponse(job.result_zip, media_type="application/zip", filename=f"{job.id}.zip")
        finally:
            path.unlink(missing_ok=True)

    @app.delete("/v1/jobs/{job_id}", status_code=204)
    def delete_job(job_id: str, x_api_key: str | None = Header(default=None)) -> None:
        authorize(x_api_key)
        if not manager.delete(job_id):
            raise HTTPException(status_code=404, detail="Задание не найдено")

    return app


app = create_app()
