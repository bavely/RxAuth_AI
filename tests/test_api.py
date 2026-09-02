"""Tests for the HTTP surface, the job runner, and object storage."""

from __future__ import annotations

import io
import os
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from rxauth_ai.api import create_app, require_auth_configured
from rxauth_ai.config import settings_from_env
from rxauth_ai.jobs import JobRunner, JobStatus
from rxauth_ai.persistence import create_all
from rxauth_ai.persistence.tables import Base
from rxauth_ai.storage import LocalObjectStore, StorageError, build_object_store, document_key

_ROOT = Path(__file__).resolve().parents[1]
_CASE_DIR = _ROOT / "data" / "cases" / "PA-CASE-001"


@pytest.fixture
def settings(tmp_path):
    url = os.environ.get("RXAUTH_TEST_DATABASE_URL") or f"sqlite:///{tmp_path / 'api.db'}"
    return settings_from_env(
        environment="local",
        database_url=url,
        artifacts_dir=tmp_path / "artifacts",
        local_storage_dir=tmp_path / "objects",
        policy_dir=_ROOT / "data" / "policies",
        job_workers=1,
    )


@pytest.fixture
def client(settings):
    """No monkeypatching: `create_app(settings)` is self-contained by design."""
    engine = create_engine(settings.database_url, future=True)
    Base.metadata.drop_all(engine)
    create_all(engine)
    engine.dispose()

    app = create_app(settings)
    with TestClient(app) as test_client:
        test_client.app_state = app.state
        yield test_client

    app.state.job_runner.shutdown()
    engine = create_engine(settings.database_url, future=True)
    Base.metadata.drop_all(engine)
    engine.dispose()


def _new_case(client, case_id: str = "PA-CASE-001") -> dict:
    return client.post(
        "/cases",
        json={
            "case_id": case_id,
            "patient_synthetic_id": "SYNTH-0001",
            "payer": "Example Health Plan",
            "medication": "Drug A",
            "indication": "Example Condition",
            "pa_required": True,
        },
    ).json()


# --- Health and safety boundaries ------------------------------------------


def test_health_reports_what_is_actually_wired(client):
    body = client.get("/health").json()

    assert body["status"] == "ok"
    assert body["database"] is True
    assert body["storage"] == "local"


def test_the_api_refuses_to_start_unauthenticated_outside_local():
    """Auth is Stage 3; until it exists this must not reach a deployment."""
    for environment in ("staging", "production"):
        with pytest.raises(RuntimeError, match="no authentication"):
            require_auth_configured(
                settings_from_env(environment=environment, s3_bucket="some-bucket")
            )


def test_the_openapi_description_states_the_system_does_not_decide(client):
    body = client.get("/openapi.json").json()

    description = body["info"]["description"].casefold()
    assert "does not decide" in description
    assert "human reviewer" in description


# --- The flow ---------------------------------------------------------------


def test_a_case_is_created_from_its_declared_facts(client, settings):
    body = _new_case(client)

    assert body["case_id"] == "PA-CASE-001"
    assert (Path(settings.artifacts_dir) / "cases" / "PA-CASE-001" / "case.json").is_file()


def test_creating_a_case_that_infers_pa_required_is_rejected(client):
    response = client.post(
        "/cases",
        json={
            "case_id": "PA-BAD",
            "patient_synthetic_id": "S",
            "payer": "P",
            "medication": "M",
            "indication": "I",
        },
    )

    assert response.status_code == 422


def test_a_document_is_stored_and_its_digest_returned(client, settings):
    _new_case(client)

    response = client.post(
        "/cases/PA-CASE-001/documents",
        files={"file": ("01_pa_request.txt", b"Diagnosis: Example Condition", "text/plain")},
    )

    body = response.json()
    assert response.status_code == 201
    assert body["storage_key"].endswith("01_pa_request.txt")
    assert body["size_bytes"] == len(b"Diagnosis: Example Condition")
    assert len(body["sha256"]) == 64
    assert build_object_store(settings).exists(body["storage_key"])


def test_uploading_to_an_unknown_case_is_a_404(client):
    response = client.post("/cases/NOPE/documents", files={"file": ("a.txt", b"x", "text/plain")})

    assert response.status_code == 404


def test_a_run_is_accepted_then_polled_then_read(client, settings):
    """The whole flow over HTTP: 202, poll, fetch the stored run."""
    _new_case(client)
    case_dir = Path(settings.artifacts_dir) / "cases" / "PA-CASE-001"
    for source in sorted(_CASE_DIR.glob("*.txt")):
        shutil.copyfile(source, case_dir / source.name)

    accepted = client.post("/cases/PA-CASE-001/runs")
    assert accepted.status_code == 202
    job_id = accepted.json()["job_id"]
    assert accepted.json()["status"] in {JobStatus.QUEUED.value, JobStatus.RUNNING.value}

    client.app_state.job_runner.wait()

    job = client.get(f"/jobs/{job_id}").json()
    assert job["status"] == JobStatus.SUCCEEDED.value, job
    run_id = job["result"]["run_id"]

    payload = client.get(f"/runs/{run_id}").json()
    assert payload["readiness"]["case_id"] == "PA-CASE-001"
    assert payload["readiness"]["criteria_total"] == 6
    assert payload["draft_groundedness"]["passed"] is True

    listed = client.get("/cases/PA-CASE-001/runs").json()
    assert [run["run_id"] for run in listed["runs"]] == [run_id]


def test_polling_an_unknown_job_explains_that_jobs_are_in_memory(client):
    body = client.get("/jobs/deadbeef").json()

    assert "do not survive a restart" in body["detail"]


def test_a_reviewer_decision_is_recorded_against_a_stored_run(client, settings):
    _new_case(client)
    case_dir = Path(settings.artifacts_dir) / "cases" / "PA-CASE-001"
    for source in sorted(_CASE_DIR.glob("*.txt")):
        shutil.copyfile(source, case_dir / source.name)
    job_id = client.post("/cases/PA-CASE-001/runs").json()["job_id"]

    client.app_state.job_runner.wait()
    run_id = client.get(f"/jobs/{job_id}").json()["result"]["run_id"]

    created = client.post(
        f"/runs/{run_id}/decisions",
        json={
            "criterion_id": "C1",
            "reviewer_id": "reviewer-01",
            "action": "corrected",
            "corrected_result": "HUMAN_REVIEW_REQUIRED",
            "note": "Two documents disagree about the start date.",
        },
    )

    assert created.status_code == 201
    decisions = client.get("/cases/PA-CASE-001/decisions").json()["decisions"]
    assert decisions[0]["corrected_result"] == "HUMAN_REVIEW_REQUIRED"
    # The version the correction was filed against travels with it.
    assert decisions[0]["matcher_version"] == "evidence-match-v2"


def test_a_correction_that_changes_nothing_is_rejected_with_422(client, settings):
    _new_case(client)
    case_dir = Path(settings.artifacts_dir) / "cases" / "PA-CASE-001"
    for source in sorted(_CASE_DIR.glob("*.txt")):
        shutil.copyfile(source, case_dir / source.name)
    job_id = client.post("/cases/PA-CASE-001/runs").json()["job_id"]

    client.app_state.job_runner.wait()
    run_id = client.get(f"/jobs/{job_id}").json()["result"]["run_id"]

    response = client.post(
        f"/runs/{run_id}/decisions",
        json={"criterion_id": "C1", "reviewer_id": "r", "action": "corrected"},
    )

    assert response.status_code == 422
    assert "what the answer should have been" in response.json()["detail"]


def test_a_decision_about_an_unknown_criterion_is_a_404(client, settings):
    _new_case(client)
    case_dir = Path(settings.artifacts_dir) / "cases" / "PA-CASE-001"
    for source in sorted(_CASE_DIR.glob("*.txt")):
        shutil.copyfile(source, case_dir / source.name)
    job_id = client.post("/cases/PA-CASE-001/runs").json()["job_id"]

    client.app_state.job_runner.wait()
    run_id = client.get(f"/jobs/{job_id}").json()["result"]["run_id"]

    response = client.post(
        f"/runs/{run_id}/decisions",
        json={"criterion_id": "C-NOPE", "reviewer_id": "r", "action": "accepted"},
    )

    assert response.status_code == 404


# --- Jobs -------------------------------------------------------------------


def test_a_failing_job_records_its_error_rather_than_vanishing():
    runner = JobRunner(workers=1)

    def explode() -> dict:
        raise FileNotFoundError("classifier artifact missing")

    job = runner.submit("case_run", explode, case_id="PA-1")
    runner.wait()

    finished = runner.get(job.id)
    assert finished.status is JobStatus.FAILED
    assert finished.error_type == "FileNotFoundError"
    assert "classifier artifact missing" in finished.error
    runner.shutdown()


def test_a_succeeding_job_keeps_its_result():
    runner = JobRunner(workers=2)

    job = runner.submit("case_run", lambda: {"run_id": "abc"}, case_id="PA-1")
    runner.wait()

    assert runner.get(job.id).status is JobStatus.SUCCEEDED
    assert runner.get(job.id).result == {"run_id": "abc"}
    runner.shutdown()


def test_the_job_table_evicts_finished_jobs_but_never_running_ones():
    """A poller seeing 404 for work still in flight is worse than forgetting."""
    runner = JobRunner(workers=1, retention=3)

    jobs = [runner.submit("noop", lambda: {"ok": True}) for _ in range(10)]
    runner.wait()

    remembered = [job for job in jobs if runner.get(job.id) is not None]
    assert len(remembered) <= 3, "the bound must hold after a burst, not only at submit time"
    assert all(runner.get(job.id).is_finished for job in remembered)
    runner.shutdown()


# --- Storage ----------------------------------------------------------------


def test_the_local_store_round_trips_with_a_digest(tmp_path):
    store = LocalObjectStore(tmp_path / "objects")
    key = document_key("PA-1", "D1", "note.txt")

    stored = store.put(key, io.BytesIO(b"Assessment: Example Condition"))
    destination = store.get(key, tmp_path / "out" / "note.txt")

    assert store.exists(key)
    assert destination.read_bytes() == b"Assessment: Example Condition"
    assert stored.size_bytes == 29
    assert len(stored.sha256) == 64


def test_a_traversing_key_cannot_escape_the_store(tmp_path):
    """An upload filename is untrusted input."""
    store = LocalObjectStore(tmp_path / "objects")

    with pytest.raises(StorageError, match="Refusing to write outside"):
        store.put("../../escaped.txt", io.BytesIO(b"x"))


def test_document_keys_separate_cases_that_share_a_filename():
    first = document_key("PA-1", "D1", "01_pa_request.txt")
    second = document_key("PA-2", "D1", "01_pa_request.txt")

    assert first != second


def test_a_filename_with_a_path_in_it_is_reduced_to_its_name():
    key = document_key("PA-1", "D1", "../../../etc/passwd")

    assert key.endswith("/passwd")
    assert ".." not in key


def test_reading_an_absent_object_says_so(tmp_path):
    store = LocalObjectStore(tmp_path / "objects")

    with pytest.raises(StorageError, match="No object at"):
        store.get("cases/PA-1/D1/missing.txt", tmp_path / "out.txt")


def test_s3_is_selected_when_a_bucket_is_configured(monkeypatch, tmp_path):
    from rxauth_ai.storage import S3ObjectStore

    monkeypatch.setattr(
        "rxauth_ai.storage.S3ObjectStore.__init__",
        lambda self, bucket, **kwargs: setattr(self, "bucket", bucket),
    )
    store = build_object_store(
        settings_from_env(s3_bucket="rxauth-docs", local_storage_dir=tmp_path)
    )

    assert isinstance(store, S3ObjectStore)


def test_s3_uploads_are_server_side_encrypted(tmp_path):
    """Set per object, not left to a bucket policy — belt as well as braces."""
    from rxauth_ai.storage import S3ObjectStore

    calls: list[dict] = []

    class FakeClient:
        def put_object(self, **kwargs):
            calls.append(kwargs)

    store = S3ObjectStore("rxauth-docs", client=FakeClient())
    store.put("cases/PA-1/D1/note.txt", io.BytesIO(b"hello"))

    assert calls[0]["ServerSideEncryption"] == "AES256"
    assert calls[0]["Bucket"] == "rxauth-docs"
