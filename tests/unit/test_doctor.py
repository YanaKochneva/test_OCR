from docpipe.config import AppConfig
from docpipe.doctor import run_doctor


def test_doctor_warns_when_api_auth_is_not_configured(tmp_path):
    report = run_doctor(AppConfig(api={"work_dir": tmp_path}))
    auth = next(check for check in report["checks"] if check["check"] == "api_auth")

    assert auth["status"] == "WARN"
    assert "unauthenticated" in auth["detail"]


def test_doctor_reports_configured_api_key(tmp_path):
    report = run_doctor(AppConfig(api={"work_dir": tmp_path, "api_key": "x" * 32}))
    auth = next(check for check in report["checks"] if check["check"] == "api_auth")

    assert auth["status"] == "PASS"
