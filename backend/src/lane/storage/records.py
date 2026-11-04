"""Store bytes and keep the matching Blob row, in one call."""

from sqlalchemy.orm import Session

from lane.db.models import Blob
from lane.intake.sniff import sniff
from lane.storage.blobs import BlobStore, sha256_hex


def ensure_blob(
    session: Session,
    store: BlobStore,
    data: bytes,
    *,
    kind: str | None = None,
    mime: str | None = None,
) -> Blob:
    """Return the Blob for these bytes, writing them and the row only if new.

    kind and mime override sniffing for derived artifacts we produce ourselves (JSON layouts).
    """
    digest = sha256_hex(data)
    blob = session.get(Blob, digest)
    if blob is not None:
        return blob
    stored = store.put(data)
    sniffed = sniff(data)
    blob = Blob(
        sha256=stored.sha256,
        size=stored.size,
        kind=kind or sniffed.kind.value,
        mime=mime or sniffed.mime,
        storage_key=stored.storage_key,
    )
    session.add(blob)
    session.flush()
    return blob
