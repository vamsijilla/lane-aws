from pathlib import Path

from lane.storage.blobs import BlobStore, sha256_hex


def test_put_is_content_addressed_and_idempotent(tmp_path: Path) -> None:
    store = BlobStore(tmp_path)
    first = store.put(b"hello")
    second = store.put(b"hello")
    assert first.created and not second.created
    assert first.sha256 == second.sha256 == sha256_hex(b"hello")
    assert first.storage_key == f"{first.sha256[:2]}/{first.sha256[2:4]}/{first.sha256}"
    assert store.get(first.storage_key) == b"hello"


def test_put_leaves_no_temp_files(tmp_path: Path) -> None:
    store = BlobStore(tmp_path)
    store.put(b"a")
    store.put(b"b")
    assert not [p for p in tmp_path.rglob(".tmp-*")]
