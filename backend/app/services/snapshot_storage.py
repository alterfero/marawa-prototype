"""Storage adapters for Time Machine snapshot archives.

The service is deliberately storage-agnostic: development defaults to a local
directory while Railway uses its S3-compatible Bucket API.
"""

from __future__ import annotations

from pathlib import Path, PurePosixPath
from tempfile import NamedTemporaryFile
from typing import Protocol

from app.core.config import Settings


class SnapshotStorageError(RuntimeError):
    """Raised when a snapshot archive cannot be persisted or read."""


class SnapshotStore(Protocol):
    def put(self, key: str, content: bytes) -> None: ...

    def get(self, key: str) -> bytes: ...

    def delete(self, key: str) -> None: ...


def _safe_relative_key(key: str) -> PurePosixPath:
    path = PurePosixPath(key)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise SnapshotStorageError("Snapshot storage key is invalid.")
    return path


class FilesystemSnapshotStore:
    def __init__(self, root: Path) -> None:
        self.root = root

    def _path_for(self, key: str) -> Path:
        return self.root.joinpath(*_safe_relative_key(key).parts)

    def put(self, key: str, content: bytes) -> None:
        target = self._path_for(key)
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            with NamedTemporaryFile(dir=target.parent, delete=False) as temporary_file:
                temporary_file.write(content)
                temporary_path = Path(temporary_file.name)
            temporary_path.replace(target)
        except OSError as exc:
            raise SnapshotStorageError("Could not write the snapshot archive.") from exc

    def get(self, key: str) -> bytes:
        try:
            return self._path_for(key).read_bytes()
        except OSError as exc:
            raise SnapshotStorageError("The snapshot archive is unavailable.") from exc

    def delete(self, key: str) -> None:
        try:
            self._path_for(key).unlink(missing_ok=True)
        except OSError as exc:
            raise SnapshotStorageError("Could not delete an expired snapshot archive.") from exc


class S3SnapshotStore:
    def __init__(
        self,
        *,
        endpoint_url: str,
        bucket: str,
        access_key_id: str,
        secret_access_key: str,
        region: str,
        url_style: str,
    ) -> None:
        try:
            import boto3
            from botocore.config import Config
            from botocore.exceptions import BotoCoreError, ClientError
        except ImportError as exc:  # pragma: no cover - dependency is installed in deployed images
            raise SnapshotStorageError(
                "S3 snapshot storage needs the boto3 dependency. Rebuild the application image after installing it."
            ) from exc

        self.bucket = bucket
        self._boto_errors = (BotoCoreError, ClientError)
        self.client = boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            aws_access_key_id=access_key_id,
            aws_secret_access_key=secret_access_key,
            region_name=region,
            config=Config(s3={"addressing_style": url_style}),
        )

    def put(self, key: str, content: bytes) -> None:
        try:
            self.client.put_object(
                Bucket=self.bucket,
                Key=key,
                Body=content,
                ContentType="application/gzip",
            )
        except self._boto_errors as exc:
            raise SnapshotStorageError("Could not upload the snapshot archive to object storage.") from exc

    def get(self, key: str) -> bytes:
        try:
            response = self.client.get_object(Bucket=self.bucket, Key=key)
            return response["Body"].read()
        except self._boto_errors as exc:
            raise SnapshotStorageError("The snapshot archive is unavailable from object storage.") from exc

    def delete(self, key: str) -> None:
        try:
            self.client.delete_object(Bucket=self.bucket, Key=key)
        except self._boto_errors as exc:
            raise SnapshotStorageError("Could not delete an expired snapshot archive from object storage.") from exc


def build_snapshot_store(settings: Settings) -> SnapshotStore:
    if settings.snapshot_storage_backend == "filesystem":
        return FilesystemSnapshotStore(settings.data_dir / "snapshots")

    if settings.snapshot_storage_backend != "s3":
        raise SnapshotStorageError(
            "SNAPSHOT_STORAGE_BACKEND must be either `filesystem` for local development or `s3` for Railway."
        )

    required = {
        "endpoint": settings.snapshot_s3_endpoint,
        "bucket": settings.snapshot_s3_bucket,
        "access key": settings.snapshot_s3_access_key_id,
        "secret key": settings.snapshot_s3_secret_access_key,
    }
    missing = [label for label, value in required.items() if not value]
    if missing:
        raise SnapshotStorageError(
            "S3 snapshot storage is missing Railway Bucket configuration: " + ", ".join(missing) + "."
        )

    return S3SnapshotStore(
        endpoint_url=settings.snapshot_s3_endpoint or "",
        bucket=settings.snapshot_s3_bucket or "",
        access_key_id=settings.snapshot_s3_access_key_id or "",
        secret_access_key=settings.snapshot_s3_secret_access_key or "",
        region=settings.snapshot_s3_region,
        url_style=settings.snapshot_s3_url_style,
    )
