from __future__ import annotations

import json
import queue
import shutil
import threading
import time
import uuid
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from docpipe.config import AppConfig
from docpipe.engines import create_engine
from docpipe.engines.base import LayoutOcrEngine
from docpipe.pipeline import parse
from docpipe.renderers import (
    render_docx,
    render_html,
    render_markdown,
    render_pdf_flow,
    render_pdf_positional,
    render_pdf_searchable,
)

ProgressCallback = Callable[[int, int], None]


@dataclass
class Job:
    id: str
    input_path: Path
    work_dir: Path
    engine: str
    outputs: list[str]
    status: str = "queued"
    progress: int = 0
    pages_total: int = 0
    error: str | None = None
    created_at: float = field(default_factory=time.time)
    started_at: float | None = None
    finished_at: float | None = None
    result_zip: Path | None = None
    document_json: Path | None = None


class JobManager:
    """Очередь обработки в памяти с ограниченным числом рабочих потоков."""

    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self._queue: queue.Queue[Job] = queue.Queue(maxsize=config.api.max_queue)
        self._jobs: dict[str, Job] = {}
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._workers: list[threading.Thread] = []
        self._janitor: threading.Thread | None = None
        self._engines: dict[str, LayoutOcrEngine] = {}
        self._engine_locks: dict[str, threading.Lock] = {}
        self.metrics = {
            "jobs_submitted_total": 0,
            "jobs_completed_total": 0,
            "jobs_failed_total": 0,
            "jobs_rejected_total": 0,
        }
        self.ready = False

    def start(self) -> None:
        """Запустить workers и загрузить настроенный движок до первого запроса."""
        default_engine = self.config.api.default_engine
        self._get_engine(default_engine)
        self.ready = True
        for i in range(self.config.api.workers):
            t = threading.Thread(target=self._worker, name=f"docpipe-worker-{i}", daemon=True)
            t.start()
            self._workers.append(t)
        self._janitor = threading.Thread(target=self._janitor_loop, name="docpipe-janitor", daemon=True)
        self._janitor.start()

    def stop(self) -> None:
        self._stop.set()
        for t in self._workers:
            t.join(timeout=2)
        if self._janitor:
            self._janitor.join(timeout=2)
        self.ready = False

    def _get_engine(self, name: str) -> LayoutOcrEngine:
        with self._lock:
            engine = self._engines.get(name)
            if engine is None:
                engine = create_engine(name)
                engine.load()
                self._engines[name] = engine
                self._engine_locks[name] = threading.Lock()
            return engine

    def submit(self, input_path: Path, engine: str, outputs: list[str]) -> Job:
        job_id = uuid.uuid4().hex
        work_dir = self.config.api.work_dir / job_id
        work_dir.mkdir(parents=True, exist_ok=False)
        job = Job(job_id, input_path, work_dir, engine, outputs)
        with self._lock:
            if len(self._jobs) >= self.config.api.max_jobs:
                shutil.rmtree(work_dir, ignore_errors=True)
                self.metrics["jobs_rejected_total"] += 1
                raise queue.Full("Очередь и хранилище заданий заполнены")
            self._jobs[job_id] = job
        try:
            self._queue.put_nowait(job)
        except queue.Full:
            with self._lock:
                self._jobs.pop(job_id, None)
                self.metrics["jobs_rejected_total"] += 1
            shutil.rmtree(work_dir, ignore_errors=True)
            raise
        self.metrics["jobs_submitted_total"] += 1
        return job

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def delete(self, job_id: str) -> bool:
        with self._lock:
            job = self._jobs.pop(job_id, None)
        if job is None:
            return False
        shutil.rmtree(job.work_dir, ignore_errors=True)
        return True

    def cleanup_ttl(self) -> int:
        now = time.time()
        removed = 0
        with self._lock:
            jobs = list(self._jobs.values())
        for job in jobs:
            if job.finished_at and now - job.finished_at > self.config.api.result_ttl_seconds:
                if self.delete(job.id):
                    removed += 1
        return removed

    def _janitor_loop(self) -> None:
        interval = min(60, max(5, self.config.api.result_ttl_seconds // 4))
        while not self._stop.wait(interval):
            self.cleanup_ttl()

    def _worker(self) -> None:
        while not self._stop.is_set():
            try:
                job = self._queue.get(timeout=0.2)
            except queue.Empty:
                continue
            try:
                self._run(job)
            finally:
                self._queue.task_done()

    def _run(self, job: Job) -> None:
        job.status = "running"
        job.started_at = time.time()
        try:
            engine = self._get_engine(job.engine)
            # Один движок не запускаем одновременно из нескольких worker-потоков.
            with self._engine_locks[job.engine]:
                doc = parse(
                    job.input_path,
                    self.config,
                    engine=job.engine,
                    out_dir=job.work_dir,
                    engine_instance=engine,
                    progress_callback=lambda current, total: self._progress(job, current, total),
                )
            out = job.work_dir / "output"
            out.mkdir(exist_ok=True)
            json_path = out / "document.json"
            json_path.write_text(doc.model_dump_json(indent=2), encoding="utf-8")
            job.document_json = json_path
            self._render_outputs(doc, job, out)
            zip_path = job.work_dir / f"{job.id}.zip"
            with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
                for p in out.rglob("*"):
                    if p.is_file():
                        zf.write(p, p.relative_to(out).as_posix())
            job.result_zip = zip_path
            job.progress = 100
            job.status = "completed"
            self.metrics["jobs_completed_total"] += 1
        except Exception as exc:
            job.status = "failed"
            job.error = str(exc)
            self.metrics["jobs_failed_total"] += 1
        finally:
            job.finished_at = time.time()
            job.input_path.unlink(missing_ok=True)

    @staticmethod
    def _progress(job: Job, current: int, total: int) -> None:
        job.pages_total = total
        job.progress = int(current * 100 / total) if total else 0

    def _render_outputs(self, doc, job: Job, out: Path) -> None:
        outputs = set(job.outputs)
        if "md" in outputs:
            render_markdown(doc, out / "document.md")
        if "html" in outputs:
            render_html(doc, out / "document.html")
        if "pdf-flow" in outputs:
            render_pdf_flow(doc, out / "document-flow.pdf", out)
        if "pdf-searchable" in outputs:
            render_pdf_searchable(doc, job.input_path, out / "document-searchable.pdf")
        if "pdf-positional" in outputs:
            render_pdf_positional(doc, out / "document-positional.pdf", out)
        if "docx" in outputs:
            render_docx(doc, out / "document.docx", out)

    def status(self, job: Job) -> dict:
        return {
            "job_id": job.id,
            "status": job.status,
            "progress": job.progress,
            "pages_total": job.pages_total,
            "error": job.error,
            "created_at": job.created_at,
            "started_at": job.started_at,
            "finished_at": job.finished_at,
        }
