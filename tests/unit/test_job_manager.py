import pytest

from docpipe.api.jobs import JobManager
from docpipe.config import AppConfig


def test_shutdown_rejects_new_work_and_cleans_queued_jobs(tmp_path):
    config = AppConfig(api={"work_dir": tmp_path, "default_engine": "fake", "max_queue": 2})
    manager = JobManager(config)
    manager.ready = True
    source = tmp_path / "input.pdf"
    source.write_bytes(b"input")
    job = manager.submit(source, "fake", ["md"])

    manager.stop()

    assert job.status == "cancelled"
    assert not source.exists()
    assert not job.work_dir.exists()
    with pytest.raises(RuntimeError, match="not accepting"):
        manager.submit(tmp_path / "other.pdf", "fake", ["md"])
