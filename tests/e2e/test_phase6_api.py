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


def test_ui_is_served_from_api(tmp_path):
    cfg = AppConfig(api={"work_dir": tmp_path, "default_engine": "fake"})
    response = TestClient(create_app(cfg)).get("/")
    assert response.status_code == 200
    assert "Распознайте документ" in response.text


def test_production_api_rejects_fake_engine(tmp_path):
    cfg = AppConfig(api={"work_dir": tmp_path, "default_engine": "docling"})
    response = TestClient(create_app(cfg)).post(
        "/v1/jobs",
        files={"file": ("input.pdf", pdf_bytes(), "application/pdf")},
        data={"options": '{"engine":"fake"}'},
    )
    assert response.status_code == 400


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
        import zipfile
        from io import BytesIO
        with zipfile.ZipFile(BytesIO(result.content)) as archive:
            names = set(archive.namelist())
        assert {
            "document.json",
            "document.md",
            "document.html",
            "document-flow.pdf",
            "document-searchable.pdf",
            "verification.json",
            "metrics.json",
        } <= names


def test_api_key_is_enforced_for_job_endpoints(tmp_path):
    api_key = "x" * 32
    cfg = AppConfig(api={"work_dir": tmp_path, "default_engine": "fake", "api_key": api_key, "require_api_key": True})
    app = create_app(cfg)
    with TestClient(app) as client:
        no_key = client.post("/v1/jobs", files={"file": ("input.pdf", pdf_bytes(), "application/pdf")})
        assert no_key.status_code == 401
        assert client.get("/metrics").status_code == 401
        assert client.get("/metrics", headers={"x-api-key": api_key}).status_code == 200
        accepted = client.post(
            "/v1/jobs",
            files={"file": ("input.pdf", pdf_bytes(), "application/pdf")},
            headers={"x-api-key": api_key},
        )
        assert accepted.status_code == 202, accepted.text


def test_sync_endpoint_enforces_upload_limit(tmp_path):
    cfg = AppConfig(
        api={"work_dir": tmp_path, "default_engine": "fake"},
        limits={"max_file_mb": 1},
    )
    app = create_app(cfg)
    payload = b"%PDF-" + b"x" * (1024 * 1024 + 1)
    with TestClient(app) as client:
        response = client.post(
            "/v1/parse",
            files={"file": ("large.pdf", payload, "application/pdf")},
        )
    assert response.status_code == 413


def test_sync_endpoint_rejects_malformed_pdf(tmp_path):
    cfg = AppConfig(api={"work_dir": tmp_path, "default_engine": "fake"})
    app = create_app(cfg)
    with TestClient(app) as client:
        response = client.post(
            "/v1/parse",
            files={"file": ("broken.pdf", b"not a pdf", "application/pdf")},
        )
    assert response.status_code == 422


def test_api_rejects_unsupported_output_before_queueing(tmp_path):
    cfg = AppConfig(api={"work_dir": tmp_path, "default_engine": "fake"})
    app = create_app(cfg)
    with TestClient(app) as client:
        response = client.post(
            "/v1/jobs",
            files={"file": ("input.pdf", pdf_bytes(), "application/pdf")},
            data={"options": '{"outputs":["shell"]}'},
        )
    assert response.status_code == 400


def test_web_does_not_require_previously_configured_key(tmp_path):
    cfg = AppConfig(api={"work_dir": tmp_path, "default_engine": "fake", "api_key": "x" * 32})
    with TestClient(create_app(cfg)) as client:
        response = client.get("/")
        assert response.status_code == 200
        assert 'id="api-key"' not in response.text
        assert "X-API-Key" not in response.text
        assert client.get("/metrics").status_code == 200
        accepted = client.post("/v1/jobs", files={"file": ("input.pdf", pdf_bytes(), "application/pdf")})
        assert accepted.status_code == 202, accepted.text
        assert client.get(f"/v1/jobs/{accepted.json()['job_id']}").status_code == 200
