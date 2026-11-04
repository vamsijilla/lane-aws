"""Read zip archives defensively. Members are returned in memory and stored by hash, never
written to disk by name, so a hostile path cannot escape. Each member that breaks a limit is
returned as rejected with a reason; the rest of the archive still comes through.
"""

import io
import stat
import zipfile
from dataclasses import dataclass
from pathlib import PurePosixPath

IGNORED_PREFIXES = ("__MACOSX/",)
IGNORED_NAMES = frozenset({".DS_Store", "Thumbs.db", "desktop.ini"})


@dataclass(frozen=True)
class ArchiveLimits:
    max_members: int
    max_member_bytes: int
    max_total_bytes: int
    max_compression_ratio: float


@dataclass(frozen=True)
class ArchiveMember:
    path: str
    data: bytes | None
    rejected: str | None = None

    @property
    def ok(self) -> bool:
        return self.data is not None


@dataclass(frozen=True)
class ArchiveListing:
    members: list[ArchiveMember]
    error: str | None = None


def read_zip(data: bytes, limits: ArchiveLimits) -> ArchiveListing:
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        return ArchiveListing([], error="Archive is corrupt or not a zip file")

    with zf:
        infos = [i for i in zf.infolist() if not i.is_dir()]
        if len(infos) > limits.max_members:
            return ArchiveListing(
                [], error=f"Archive has {len(infos)} files, over the limit of {limits.max_members}"
            )
        members: list[ArchiveMember] = []
        total = 0
        for info in infos:
            member, size = _read_member(zf, info, limits, budget=limits.max_total_bytes - total)
            total += size
            members.append(member)
        return ArchiveListing(members)


def _read_member(
    zf: zipfile.ZipFile, info: zipfile.ZipInfo, limits: ArchiveLimits, *, budget: int
) -> tuple[ArchiveMember, int]:
    name = info.filename

    def reject(reason: str) -> tuple[ArchiveMember, int]:
        return ArchiveMember(name, None, reason), 0

    if name.startswith(IGNORED_PREFIXES) or PurePosixPath(name).name in IGNORED_NAMES:
        return reject("System metadata file, ignored")
    if _unsafe_path(name):
        return reject("Unsafe path inside archive")
    if info.flag_bits & 0x1:
        return reject("File is password protected")
    if stat.S_ISLNK(info.external_attr >> 16):
        return reject("Symbolic link inside archive")
    if info.file_size > limits.max_member_bytes:
        return reject(f"File is {info.file_size:,} bytes, over the per-file limit")
    if info.file_size > budget:
        return reject("Archive expands beyond the total size limit")
    if info.compress_size and info.file_size / info.compress_size > limits.max_compression_ratio:
        return reject("Compression ratio is suspiciously high (possible zip bomb)")

    # Never trust the declared size: read at most the cap plus one byte.
    cap = min(limits.max_member_bytes, budget)
    try:
        with zf.open(info) as fh:
            content = fh.read(cap + 1)
    except (zipfile.BadZipFile, RuntimeError, OSError, EOFError) as exc:
        return reject(f"File could not be read from the archive ({exc.__class__.__name__})")
    if len(content) > cap:
        return reject("File expands beyond its declared size")
    return ArchiveMember(name, content), len(content)


def _unsafe_path(name: str) -> bool:
    normalized = name.replace("\\", "/")
    if normalized.startswith("/") or (len(normalized) > 1 and normalized[1] == ":"):
        return True
    return any(part == ".." for part in normalized.split("/"))
