"""Object storage for uploaded case documents (roadmap Stage 2).

An uploaded document is the patient record. In a real deployment it is PHI,
which means it belongs in encrypted object storage with a retention policy and
an audit trail — not in a database row and not on a container's ephemeral
filesystem. S3 is the deployment target.

**Credentials are never fields here.** boto3 resolves them from the standard
chain: environment variables, shared config, or an instance role. A secret in a
settings object is a secret one `repr()` away from a log line, and this project
has spent real effort keeping patient text out of logs.

`LocalObjectStore` exists so a developer can run the stack without a bucket and
so tests never touch real storage. `Settings` refuses to fall back to it in
`staging` or `production`, so the convenience cannot become the deployment.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import BinaryIO, Optional, Protocol

from .config import Settings, get_settings


class StorageError(RuntimeError):
    """Raised when an object cannot be stored or retrieved."""


@dataclass(frozen=True)
class StoredObject:
    """Where something was put, and enough to prove it is unchanged."""

    key: str
    size_bytes: int
    sha256: str


class ObjectStore(Protocol):
    """The slice of object storage this application needs."""

    def put(
        self, key: str, stream: BinaryIO, *, retain_until: Optional[datetime] = None
    ) -> StoredObject: ...

    def get(self, key: str, destination: Path) -> Path: ...

    def exists(self, key: str) -> bool: ...

    def delete(self, key: str) -> None: ...


def document_key(
    case_id: str,
    document_id: str,
    filename: str,
    *,
    organization_id: str,
    prefix: str = "cases",
) -> str:
    """Where one document lives.

    Keyed by case and document rather than by filename alone, because two cases
    routinely contain a `01_pa_request.txt` and one overwriting the other would
    be silent and unrecoverable.
    """
    safe = Path(filename).name
    return f"{prefix}/{organization_id}/{case_id}/{document_id}/{safe}"


def _digest(source: BinaryIO, destination: BinaryIO) -> tuple[int, str]:
    """Copy while hashing, so neither pass has to re-read the stream."""
    digest = hashlib.sha256()
    size = 0
    while chunk := source.read(1024 * 1024):
        digest.update(chunk)
        destination.write(chunk)
        size += len(chunk)
    return size, digest.hexdigest()


class LocalObjectStore:
    """Filesystem-backed store for local development and tests."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def _path(self, key: str) -> Path:
        candidate = (self.root / key).resolve()
        root = self.root.resolve()
        # A key arriving from an upload is untrusted input; `../` in one must
        # not be able to write outside the store.
        if not candidate.is_relative_to(root):
            raise StorageError(f"Refusing to write outside the object store: {key!r}")
        return candidate

    def put(
        self, key: str, stream: BinaryIO, *, retain_until: Optional[datetime] = None
    ) -> StoredObject:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.parent / f".{uuid.uuid4().hex}.upload"
        try:
            with temporary.open("xb") as handle:
                size, digest = _digest(stream, handle)
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
        return StoredObject(key=key, size_bytes=size, sha256=digest)

    def get(self, key: str, destination: Path) -> Path:
        path = self._path(key)
        if not path.is_file():
            raise StorageError(f"No object at {key!r}.")
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, destination)
        return destination

    def exists(self, key: str) -> bool:
        return self._path(key).is_file()

    def delete(self, key: str) -> None:
        path = self._path(key)
        if path.is_file():
            path.unlink()


class S3ObjectStore:
    """S3 (or an S3-compatible endpoint), with server-side encryption on.

    `ServerSideEncryption=AES256` is set on every upload rather than left to a
    bucket policy. A bucket policy is the right belt; this is the braces, and
    it fails visibly if the bucket forbids it rather than silently storing PHI
    unencrypted.
    """

    def __init__(
        self,
        bucket: str,
        *,
        region: Optional[str] = None,
        endpoint_url: Optional[str] = None,
        object_lock_mode: Optional[str] = None,
        client: Optional[object] = None,
    ) -> None:
        self.bucket = bucket
        self.object_lock_mode = object_lock_mode
        if client is not None:
            self._client = client
            return
        try:
            import boto3
        except ImportError as exc:  # pragma: no cover - exercised only without the extra
            raise StorageError(
                "S3 storage needs the service dependencies: uv sync --extra service"
            ) from exc
        self._client = boto3.client("s3", region_name=region, endpoint_url=endpoint_url)

    def put(
        self, key: str, stream: BinaryIO, *, retain_until: Optional[datetime] = None
    ) -> StoredObject:
        try:
            start = stream.tell()
            digest = hashlib.sha256()
            size = 0
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
                size += len(chunk)
            stream.seek(start)
        except (AttributeError, OSError) as exc:
            raise StorageError("S3 uploads require a seekable staged stream.") from exc
        try:
            request = {
                "Bucket": self.bucket,
                "Key": key,
                "Body": stream,
                "ServerSideEncryption": "AES256",
            }
            if retain_until is not None and self.object_lock_mode is not None:
                request["ObjectLockMode"] = self.object_lock_mode
                request["ObjectLockRetainUntilDate"] = retain_until
            self._client.put_object(**request)
        except Exception as exc:
            raise StorageError(f"Could not store {key!r} in s3://{self.bucket}: {exc}") from exc
        return StoredObject(key=key, size_bytes=size, sha256=digest.hexdigest())

    def get(self, key: str, destination: Path) -> Path:
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._client.download_file(self.bucket, key, str(destination))
        except Exception as exc:
            raise StorageError(f"Could not read {key!r} from s3://{self.bucket}: {exc}") from exc
        return destination

    def exists(self, key: str) -> bool:
        try:
            self._client.head_object(Bucket=self.bucket, Key=key)
        except Exception:
            return False
        return True

    def delete(self, key: str) -> None:
        try:
            self._client.delete_object(Bucket=self.bucket, Key=key)
        except Exception as exc:
            raise StorageError(f"Could not delete {key!r} from s3://{self.bucket}: {exc}") from exc


def build_object_store(settings: Optional[Settings] = None) -> ObjectStore:
    """S3 when a bucket is configured, local disk otherwise.

    `Settings` refuses the local branch in `staging` and `production`, so this
    cannot quietly become the deployment's storage.
    """
    active = settings or get_settings()
    if active.s3_bucket:
        return S3ObjectStore(
            active.s3_bucket,
            region=active.s3_region,
            endpoint_url=active.s3_endpoint_url,
            object_lock_mode=active.s3_object_lock_mode,
        )
    return LocalObjectStore(active.local_storage_dir)
