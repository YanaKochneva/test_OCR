from io import BytesIO
import time

from fastapi.testclient import TestClient
from reportlab.pdfgen import canvas

from docpipe.api.app import create_app
from docpipe.config import AppConfig


def pdf_bytes() -> bytes:
    buf = BytesIO()
    c = canvas.Canvas(buf, pagesize=(300, 300))
    c.drawString(30, 250, "Тестовый документ")
    c.save()
    return buf.getvalue()


def test_health_and_ready(tmp_path):
    cfg = AppConfig(api={"work_dir": tmp_path, "default_engine": "fake", "workers": 1})
    app = create_app(cfg)
    with TestClient(app) as client:
        assert client.get("/healthz").status_code == 200
        assert client.get("/readyz").status_code == 200
        assert client.get("/metrics").status_code == 200


def test_job_lifecycle(tmp_path):
    cfg = AppConfig(api={"work_dir": tmp_path, "default_engine": "fake", "workers": 1, "max_queue": 2})
    app = create_app(cfg)
    with TestClient(app) as client:
        r = client.post("/v1/jobs", files={"file": ("input.pdf", pdf_bytes(), "application/pdf")}, data={"options": '{"outputs":["md","json"]}'})
        assert r.status_code == 202, r.text
        job_id = r.json()["job_id"]
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            status = client.get(f"/v1/jobs/{job_id}")
            assert status.status_code == 200
            if status.json()["status"] in {"completed", "failed"}:
                break
            time.sleep(0.05)
        assert status.json()["status"] == "completed", status.json()
        result = client.get(f"/v1/jobs/{job_id}/result")
        assert result.status_code == 200
        assert result.headers["content-type"].startswith("application/zip")
