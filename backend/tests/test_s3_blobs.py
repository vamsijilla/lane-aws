"""The S3 store keeps the local store's contract (moto stands in for S3 and MinIO)."""

from collections.abc import Iterator

import boto3
import pytest
from moto import mock_aws

from lane.config import get_settings
from lane.storage.blobs import BlobStore, S3BlobStore, blob_store, sha256_hex

BUCKET = "lane-test"


@pytest.fixture
def s3(monkeypatch: pytest.MonkeyPatch) -> Iterator[S3BlobStore]:
    monkeypatch.setenv("LANE_BLOB_BACKEND", "s3")
    monkeypatch.setenv("LANE_S3_BUCKET", BUCKET)
    monkeypatch.setenv("LANE_S3_ACCESS_KEY_ID", "test")
    monkeypatch.setenv("LANE_S3_SECRET_ACCESS_KEY", "test")
    get_settings.cache_clear()
    with mock_aws():
        boto3.client("s3", region_name="us-east-1").create_bucket(Bucket=BUCKET)  # pyright: ignore[reportUnknownMemberType]
        store = blob_store(get_settings())
        assert isinstance(store, S3BlobStore)
        yield store
    get_settings.cache_clear()


def test_the_settings_pick_the_store() -> None:
    assert type(blob_store(get_settings())) is BlobStore  # local by default


def test_bytes_are_stored_once_under_their_hash(s3: S3BlobStore) -> None:
    data = b"%PDF-1.7 a loss run"
    first = s3.put(data)
    again = s3.put(data)

    assert first.sha256 == sha256_hex(data) and first.created
    assert again.storage_key == first.storage_key and not again.created
    assert s3.get(first.storage_key) == data
    assert s3.exists(first.storage_key)
    assert not s3.exists(BlobStore.key_for(sha256_hex(b"something else")))
    listed = s3.client.list_objects_v2(Bucket=BUCKET).get("Contents", [])
    assert [o.get("Key") for o in listed] == [f"blobs/{first.storage_key}"]


def test_a_missing_object_is_an_error_on_read(s3: S3BlobStore) -> None:
    with pytest.raises(s3.client.exceptions.NoSuchKey):
        s3.get(BlobStore.key_for(sha256_hex(b"never stored")))
