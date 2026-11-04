"""Content-addressed blob store: the local filesystem, or S3 (MinIO locally).

Both keep one contract keyed by sha256: put returns the key, identical bytes land on the same
key, writes are atomic, nothing is rewritten. `blob_store(settings)` picks one by
`blob_backend`.
"""

import base64
import hashlib
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from mypy_boto3_s3 import S3Client

    from lane.config import Settings


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@dataclass(frozen=True)
class StoredBlob:
    sha256: str
    size: int
    storage_key: str
    created: bool


class BlobStore:
    def __init__(self, root: Path) -> None:
        self.root = root

    def _path(self, key: str) -> Path:
        return self.root / key

    @staticmethod
    def key_for(digest: str) -> str:
        return f"{digest[:2]}/{digest[2:4]}/{digest}"

    def put(self, data: bytes) -> StoredBlob:
        digest = sha256_hex(data)
        key = self.key_for(digest)
        path = self._path(key)
        if path.exists():
            return StoredBlob(digest, len(data), key, created=False)
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
                fh.flush()
                os.fsync(fh.fileno())
            Path(tmp).replace(path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
        return StoredBlob(digest, len(data), key, created=True)

    def get(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    def exists(self, key: str) -> bool:
        return self._path(key).exists()


class S3BlobStore(BlobStore):
    """The same contract on S3 (or MinIO locally): the key is the content's sha256, identical
    bytes land on one object, an object is never rewritten. Server-side encryption is requested
    on every write when `sse` is set (AES256 or aws:kms)."""

    def __init__(self, client: "S3Client", bucket: str, *, prefix: str = "", sse: str = "") -> None:
        super().__init__(Path(f"s3://{bucket}/{prefix}"))
        self.client = client
        self.bucket = bucket
        self.prefix = prefix
        self.sse = sse

    def _key(self, key: str) -> str:
        return f"{self.prefix}{key}"

    def put(self, data: bytes) -> StoredBlob:
        digest = sha256_hex(data)
        key = self.key_for(digest)
        if self.exists(key):
            return StoredBlob(digest, len(data), key, created=False)
        extra: dict[str, Any] = {"ServerSideEncryption": self.sse} if self.sse else {}
        self.client.put_object(
            Bucket=self.bucket, Key=self._key(key), Body=data, ChecksumSHA256=_b64(digest), **extra
        )
        return StoredBlob(digest, len(data), key, created=True)

    def get(self, key: str) -> bytes:
        body = self.client.get_object(Bucket=self.bucket, Key=self._key(key))["Body"]
        return bytes(body.read())

    def exists(self, key: str) -> bool:
        from botocore.exceptions import ClientError

        try:
            self.client.head_object(Bucket=self.bucket, Key=self._key(key))
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code")
            if code in ("404", "NoSuchKey", "NotFound"):
                return False
            raise
        return True


def _b64(hex_digest: str) -> str:
    return base64.b64encode(bytes.fromhex(hex_digest)).decode()


def blob_store(settings: "Settings") -> BlobStore:
    """The store the settings name: the local folder, or an S3 bucket."""
    if settings.blob_backend == "local":
        return BlobStore(settings.blob_dir)
    import boto3

    # Empty keys: the standard AWS credential chain (an instance or task role in production)
    key_id = settings.s3_access_key_id or None
    secret = settings.s3_secret_access_key.get_secret_value() or None
    # The stubs type only the services installed (S3); the other overloads read as unknown
    client: S3Client = boto3.client(  # pyright: ignore[reportUnknownMemberType]
        "s3",
        region_name=settings.s3_region,
        endpoint_url=settings.s3_endpoint_url or None,
        aws_access_key_id=key_id,
        aws_secret_access_key=secret,
    )
    return S3BlobStore(client, settings.s3_bucket, prefix=settings.s3_prefix, sse=settings.s3_sse)
