import io
import zipfile

from lane.intake.archive import ArchiveLimits, read_zip
from tests.factories import PDF, make_zip

LIMITS = ArchiveLimits(
    max_members=10, max_member_bytes=1_000_000, max_total_bytes=2_000_000, max_compression_ratio=200
)


def _zip_with_info(info: zipfile.ZipInfo, data: bytes) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(info, data)
    return buf.getvalue()


def _rejections(data: bytes, limits: ArchiveLimits = LIMITS) -> dict[str, str | None]:
    return {m.path: m.rejected for m in read_zip(data, limits).members}


def test_reads_normal_members_and_skips_directories() -> None:
    listing = read_zip(make_zip({"pack/a.pdf": PDF, "pack/b.txt": b"note"}), LIMITS)
    assert listing.error is None
    assert [(m.path, m.ok) for m in listing.members] == [("pack/a.pdf", True), ("pack/b.txt", True)]
    assert listing.members[0].data == PDF


def test_rejects_path_traversal_and_absolute_paths() -> None:
    result = _rejections(make_zip({"../../etc/passwd": b"x", "/abs.txt": b"y", "C:/w.txt": b"z"}))
    assert all(reason == "Unsafe path inside archive" for reason in result.values())


def test_ignores_system_metadata() -> None:
    result = _rejections(make_zip({"__MACOSX/._a.pdf": b"x", "dir/.DS_Store": b"y"}))
    assert all(reason and "System metadata" in reason for reason in result.values())


def test_rejects_symlinks() -> None:
    info = zipfile.ZipInfo("link")
    info.external_attr = 0o120777 << 16
    assert _rejections(_zip_with_info(info, b"/etc/passwd")) == {
        "link": "Symbolic link inside archive"
    }


def test_rejects_encrypted_members() -> None:
    raw = bytearray(make_zip({"secret.pdf": PDF}, compression=zipfile.ZIP_STORED))
    # Set the "encrypted" general-purpose flag in the central directory entry.
    central = raw.find(b"PK\x01\x02")
    raw[central + 8] |= 0x1
    assert _rejections(bytes(raw)) == {"secret.pdf": "File is password protected"}


def test_rejects_zip_bombs_by_ratio() -> None:
    bomb = make_zip({"zeros.bin": b"\x00" * 900_000})
    reason = _rejections(bomb)["zeros.bin"]
    assert reason and "zip bomb" in reason


def test_rejects_members_over_size_and_total_limits() -> None:
    limits = ArchiveLimits(10, max_member_bytes=100, max_total_bytes=150, max_compression_ratio=1e9)
    data = make_zip({"big.bin": b"a" * 101, "ok1.bin": b"b" * 100, "ok2.bin": b"c" * 100})
    result = _rejections(data, limits)
    assert result["big.bin"] and "per-file limit" in result["big.bin"]
    assert result["ok1.bin"] is None
    assert result["ok2.bin"] and "total size" in result["ok2.bin"]


def test_rejects_archives_with_too_many_members() -> None:
    data = make_zip({f"f{i}.txt": b"x" for i in range(11)})
    listing = read_zip(data, LIMITS)
    assert listing.members == []
    assert listing.error and "over the limit" in listing.error


def test_reports_corrupt_archives() -> None:
    listing = read_zip(b"PK\x03\x04 not really a zip", LIMITS)
    assert listing.error == "Archive is corrupt or not a zip file"
