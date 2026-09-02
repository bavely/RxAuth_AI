"""Tests for the HTTP surface, the job runner, and object storage."""

from __future__ import annotations

import io
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from rxauth_ai.api import create_app, require_auth_configured
from rxauth_ai.auth import AuthenticationError, Principal
from rxauth_ai.config import ConfigurationError, settings_from_env
from rxauth_ai.jobs import JobStatus
from rxauth_ai.persistence import (
    create_all,
    list_uploaded_documents,
    load_case_record,
    session_scope,
)
from rxauth_ai.persistence.tables import Base
from rxauth_ai.storage import LocalObjectStore, StorageError, build_object_store, document_key

_ROOT = Path(__file__).resolve().parents[1]
_CASE_DIR = _ROOT / "data" / "cases" / "PA-CASE-001"


class TokenAuthenticator:
    def __init__(self, principals: dict[str, Principal]) -> None:
        self.principals = principals

    def authenticate(self, token: str | None) -> Principal:
        if token is None or token not in self.principals:
            raise AuthenticationError()
        return self.principals[token]


@pytest.fixture
def settings(tmp_path):
    url = os.environ.get("RXAUTH_TEST_DATABASE_URL") or f"sqlite:///{tmp_path / 'api.db'}"
    return settings_from_env(
        environment="local",
        database_url=url,
        artifacts_dir=tmp_path / "artifacts",
        local_storage_dir=tmp_path / "objects",
        policy_dir=_ROOT / "data" / "policies",
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

    engine = create_engine(settings.database_url, future=True)
    Base.metadata.drop_all(engine)
    engine.dispose()


@pytest.fixture
def authenticated_client(settings):
    engine = create_engine(settings.database_url, future=True)
    Base.metadata.drop_all(engine)
    create_all(engine)
    engine.dispose()

    authenticator = TokenAuthenticator(
        {
            "writer-a": Principal(
                subject="writer-a", organization_id="org-a", roles={"case:write"}
            ),
            "reader-a": Principal(subject="reader-a", organization_id="org-a", roles={"case:read"}),
            "writer-b": Principal(
                subject="writer-b", organization_id="org-b", roles={"case:write"}
            ),
            "reviewer-a": Principal(
                subject="reviewer-a", organization_id="org-a", roles={"case:review"}
            ),
        }
    )
    app = create_app(settings, authenticator=authenticator)
    with TestClient(app) as test_client:
        test_client.app_state = app.state
        yield test_client

    engine = create_engine(settings.database_url, future=True)
    Base.metadata.drop_all(engine)
    engine.dispose()


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


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


def _upload_case_documents(client) -> None:
    for source in sorted(_CASE_DIR.glob("*.txt")):
        response = client.post(
            "/cases/PA-CASE-001/documents",
            files={"file": (source.name, source.read_bytes(), "text/plain")},
        )
        assert response.status_code == 201, response.json()


# --- Health and safety boundaries ------------------------------------------


def test_health_reports_what_is_actually_wired(client):
    body = client.get("/health").json()

    assert body["status"] == "ok"
    assert body["database"] is True
    assert body["storage"] == "local"


def test_the_api_refuses_to_start_unauthenticated_outside_local():
    """A deployed service must have a complete OIDC verifier configuration."""
    for environment in ("staging", "production"):
        with pytest.raises(ConfigurationError, match="AUTH_ENABLED"):
            settings_from_env(environment=environment, s3_bucket="some-bucket")

    configured = settings_from_env(
        environment="staging",
        database_url="postgresql+psycopg://rxauth:secret@db.example.test/rxauth",
        s3_bucket="some-bucket",
        auth_enabled=True,
        auth_issuer="https://identity.example.test/",
        auth_audience="rxauth-api",
        auth_jwks_url="https://identity.example.test/.well-known/jwks.json",
        job_retry_initial_seconds=1800,
        job_retry_max_seconds=3600,
        job_lease_seconds=3600,
    )
    require_auth_configured(configured)


def test_the_openapi_description_states_the_system_does_not_decide(client):
    body = client.get("/openapi.json").json()

    description = body["info"]["description"].casefold()
    assert "does not decide" in description
    assert "human reviewer" in description


def test_protected_endpoints_require_a_valid_bearer_token(authenticated_client):
    response = authenticated_client.post(
        "/cases",
        json={
            "case_id": "PA-1",
            "patient_synthetic_id": "S",
            "payer": "P",
            "medication": "M",
            "indication": "I",
            "pa_required": True,
        },
    )

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


def test_roles_are_enforced_before_a_case_is_created(authenticated_client):
    response = authenticated_client.post(
        "/cases",
        headers=_bearer("reader-a"),
        json={
            "case_id": "PA-1",
            "patient_synthetic_id": "S",
            "payer": "P",
            "medication": "M",
            "indication": "I",
            "pa_required": True,
        },
    )

    assert response.status_code == 403


def test_the_same_case_id_is_isolated_between_organizations(authenticated_client, settings):
    payload = {
        "case_id": "PA-SHARED",
        "patient_synthetic_id": "S",
        "payer": "P",
        "medication": "M",
        "indication": "I",
        "pa_required": True,
    }

    assert (
        authenticated_client.post("/cases", headers=_bearer("writer-a"), json=payload).status_code
        == 201
    )
    assert (
        authenticated_client.post("/cases", headers=_bearer("writer-b"), json=payload).status_code
        == 201
    )

    root = Path(settings.artifacts_dir) / "cases"
    assert (root / "org-a" / "PA-SHARED" / "case.json").is_file()
    assert (root / "org-b" / "PA-SHARED" / "case.json").is_file()


def test_a_job_identifier_does_not_cross_the_organization_boundary(authenticated_client):
    job = authenticated_client.app_state.job_runner.submit(
        "test",
        {"ok": True},
        organization_id="org-a",
    )

    hidden = authenticated_client.get(f"/jobs/{job.id}", headers=_bearer("writer-b"))
    visible = authenticated_client.get(f"/jobs/{job.id}", headers=_bearer("reader-a"))

    assert hidden.status_code == 404
    assert visible.status_code == 200


# --- The flow ---------------------------------------------------------------


def test_a_case_is_created_from_its_declared_facts(client, settings):
    body = _new_case(client)

    assert body["case_id"] == "PA-CASE-001"
    assert (
        Path(settings.artifacts_dir) / "cases" / "local" / "PA-CASE-001" / "case.json"
    ).is_file()
    with session_scope(client.app_state.engine) as session:
        record = load_case_record(session, organization_id="local", case_id="PA-CASE-001")
    assert record.manifest["patient_synthetic_id"] == "SYNTH-0001"


def test_a_case_id_cannot_escape_its_organization_directory(client, settings):
    response = client.post(
        "/cases",
        json={
            "case_id": "..",
            "patient_synthetic_id": "S",
            "payer": "P",
            "medication": "M",
            "indication": "I",
            "pa_required": True,
        },
    )

    assert response.status_code == 422
    assert not (Path(settings.artifacts_dir) / "cases" / "local" / "case.json").exists()


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
    with session_scope(client.app_state.engine) as session:
        documents = list_uploaded_documents(session, organization_id="local", case_id="PA-CASE-001")
    assert documents[0].storage_key == body["storage_key"]
    assert documents[0].media_type == "text/plain"
    assert documents[0].retain_until.year == datetime.now(timezone.utc).year + 10


def test_uploading_to_an_unknown_case_is_a_404(client):
    response = client.post("/cases/NOPE/documents", files={"file": ("a.txt", b"x", "text/plain")})

    assert response.status_code == 404


def test_the_per_case_document_limit_returns_413(settings):
    limited = settings.model_copy(update={"upload_max_documents_per_case": 1})
    engine = create_engine(limited.database_url, future=True)
    Base.metadata.drop_all(engine)
    create_all(engine)
    engine.dispose()

    with TestClient(create_app(limited)) as client:
        _new_case(client)
        first = client.post(
            "/cases/PA-CASE-001/documents",
            files={"file": ("one.txt", b"one", "text/plain")},
        )
        second = client.post(
            "/cases/PA-CASE-001/documents",
            files={"file": ("two.txt", b"two", "text/plain")},
        )

    assert first.status_code == 201
    assert second.status_code == 413


def test_the_aggregate_case_byte_limit_returns_413(settings):
    limited = settings.model_copy(
        update={
            "upload_max_file_bytes": 1024,
            "upload_max_case_bytes": 1200,
        }
    )
    engine = create_engine(limited.database_url, future=True)
    Base.metadata.drop_all(engine)
    create_all(engine)
    engine.dispose()

    with TestClient(create_app(limited)) as client:
        _new_case(client)
        first = client.post(
            "/cases/PA-CASE-001/documents",
            files={"file": ("one.txt", b"x" * 800, "text/plain")},
        )
        second = client.post(
            "/cases/PA-CASE-001/documents",
            files={"file": ("two.txt", b"x" * 500, "text/plain")},
        )

    assert first.status_code == 201
    assert second.status_code == 413


def test_the_request_body_limit_rejects_before_multipart_staging(settings):
    limited = settings.model_copy(
        update={
            "upload_max_file_bytes": 1024,
            "upload_multipart_overhead_bytes": 64 * 1024,
        }
    )
    engine = create_engine(limited.database_url, future=True)
    Base.metadata.drop_all(engine)
    create_all(engine)
    engine.dispose()

    with TestClient(create_app(limited)) as client:
        _new_case(client)
        response = client.post(
            "/cases/PA-CASE-001/documents",
            files={"file": ("large.txt", b"x" * (70 * 1024), "text/plain")},
        )

    assert response.status_code == 413
    assert "body limit" in response.json()["detail"]


def test_a_run_is_accepted_then_polled_then_read(client, settings):
    """The whole flow over HTTP: 202, poll, fetch the stored run."""
    _new_case(client)
    _upload_case_documents(client)

    accepted = client.post("/cases/PA-CASE-001/runs")
    assert accepted.status_code == 202
    job_id = accepted.json()["job_id"]
    assert accepted.json()["status"] in {JobStatus.QUEUED.value, JobStatus.RUNNING.value}

    assert client.app_state.job_worker.run_once() is True

    job = client.get(f"/jobs/{job_id}").json()
    assert job["status"] == JobStatus.SUCCEEDED.value, job
    run_id = job["result"]["run_id"]

    payload = client.get(f"/runs/{run_id}").json()
    assert payload["readiness"]["case_id"] == "PA-CASE-001"
    assert payload["readiness"]["criteria_total"] == 6
    assert payload["draft_groundedness"]["passed"] is True

    listed = client.get("/cases/PA-CASE-001/runs").json()
    assert [run["run_id"] for run in listed["runs"]] == [run_id]


def test_case_documents_and_an_accepted_job_survive_an_api_restart(settings):
    engine = create_engine(settings.database_url, future=True)
    Base.metadata.drop_all(engine)
    create_all(engine)
    engine.dispose()

    first_app = create_app(settings)
    with TestClient(first_app) as first:
        _new_case(first)
        _upload_case_documents(first)
        job_id = first.post("/cases/PA-CASE-001/runs").json()["job_id"]

    working_copy = Path(settings.artifacts_dir) / "cases" / "local" / "PA-CASE-001"
    shutil.rmtree(working_copy)

    restarted_app = create_app(settings)
    with TestClient(restarted_app) as restarted:
        before = restarted.get(f"/jobs/{job_id}").json()
        assert before["status"] == JobStatus.QUEUED.value
        assert restarted_app.state.job_worker.run_once() is True
        after = restarted.get(f"/jobs/{job_id}").json()
        assert after["status"] == JobStatus.SUCCEEDED.value
        assert restarted.get(f"/runs/{after['result']['run_id']}").status_code == 200

    engine = create_engine(settings.database_url, future=True)
    Base.metadata.drop_all(engine)
    engine.dispose()


def test_polling_an_unknown_job_explains_the_durable_retention_boundary(client):
    body = client.get("/jobs/deadbeef").json()

    assert "Durable jobs" in body["detail"]


def test_a_reviewer_decision_is_recorded_against_a_stored_run(client, settings):
    _new_case(client)
    _upload_case_documents(client)
    job_id = client.post("/cases/PA-CASE-001/runs").json()["job_id"]

    assert client.app_state.job_worker.run_once() is True
    run_id = client.get(f"/jobs/{job_id}").json()["result"]["run_id"]

    created = client.post(
        f"/runs/{run_id}/decisions",
        json={
            "criterion_id": "C1",
            "action": "corrected",
            "corrected_result": "HUMAN_REVIEW_REQUIRED",
            "note": "Two documents disagree about the start date.",
        },
    )

    assert created.status_code == 201
    decisions = client.get("/cases/PA-CASE-001/decisions").json()["decisions"]
    assert decisions[0]["corrected_result"] == "HUMAN_REVIEW_REQUIRED"
    assert decisions[0]["reviewer_id"] == "local-developer"
    # The version the correction was filed against travels with it.
    assert decisions[0]["matcher_version"] == "evidence-match-v2"


def test_a_correction_that_changes_nothing_is_rejected_with_422(client, settings):
    _new_case(client)
    _upload_case_documents(client)
    job_id = client.post("/cases/PA-CASE-001/runs").json()["job_id"]

    assert client.app_state.job_worker.run_once() is True
    run_id = client.get(f"/jobs/{job_id}").json()["result"]["run_id"]

    response = client.post(
        f"/runs/{run_id}/decisions",
        json={"criterion_id": "C1", "action": "corrected"},
    )

    assert response.status_code == 422
    assert "what the answer should have been" in response.json()["detail"]


def test_a_decision_about_an_unknown_criterion_is_a_404(client, settings):
    _new_case(client)
    _upload_case_documents(client)
    job_id = client.post("/cases/PA-CASE-001/runs").json()["job_id"]

    assert client.app_state.job_worker.run_once() is True
    run_id = client.get(f"/jobs/{job_id}").json()["result"]["run_id"]

    response = client.post(
        f"/runs/{run_id}/decisions",
        json={"criterion_id": "C-NOPE", "action": "accepted"},
    )

    assert response.status_code == 404


# --- Storage ----------------------------------------------------------------


def test_the_local_store_round_trips_with_a_digest(tmp_path):
    store = LocalObjectStore(tmp_path / "objects")
    key = document_key("PA-1", "D1", "note.txt", organization_id="org-a")

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
    first = document_key("PA-1", "D1", "01_pa_request.txt", organization_id="org-a")
    second = document_key("PA-2", "D1", "01_pa_request.txt", organization_id="org-a")

    assert first != second


def test_a_filename_with_a_path_in_it_is_reduced_to_its_name():
    key = document_key("PA-1", "D1", "../../../etc/passwd", organization_id="org-a")

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


def test_s3_uploads_apply_the_configured_object_retention():
    from datetime import datetime, timezone

    from rxauth_ai.storage import S3ObjectStore

    calls: list[dict] = []

    class FakeClient:
        def put_object(self, **kwargs):
            calls.append(kwargs)

    retain_until = datetime(2036, 9, 2, tzinfo=timezone.utc)
    store = S3ObjectStore("rxauth-docs", object_lock_mode="COMPLIANCE", client=FakeClient())
    store.put(
        "cases/org-a/PA-1/D1/note.txt",
        io.BytesIO(b"hello"),
        retain_until=retain_until,
    )

    assert calls[0]["ObjectLockMode"] == "COMPLIANCE"
    assert calls[0]["ObjectLockRetainUntilDate"] == retain_until
