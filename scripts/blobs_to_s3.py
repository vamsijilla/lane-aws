"""Copy every stored file from the local folder into the S3 bucket the settings name.

    cd backend && uv run python ../scripts/blobs_to_s3.py      (make blobs-to-s3)

Keys stay the same (the content's sha256), so the database needs no change. Creates the bucket
if it does not exist. Files already in the bucket are skipped; each copy is checked by hash.
"""

import sys

from lane.config import get_settings
from lane.storage.blobs import BlobStore, S3BlobStore, blob_store, sha256_hex


def main() -> int:
    settings = get_settings()
    target = blob_store(settings)
    if not isinstance(target, S3BlobStore):
        print("Set LANE_BLOB_BACKEND=s3 and the LANE_S3_* settings first.")
        return 2
    existing = {b.get("Name") for b in target.client.list_buckets().get("Buckets", [])}
    if target.bucket not in existing:
        target.client.create_bucket(Bucket=target.bucket)
        print(f"Created bucket {target.bucket}")

    local = BlobStore(settings.blob_dir)
    copied = skipped = 0
    for path in sorted(p for p in settings.blob_dir.rglob("*") if p.is_file()):
        if path.name.startswith(".tmp-"):
            continue
        data = path.read_bytes()
        key = local.key_for(sha256_hex(data))
        if key != str(path.relative_to(settings.blob_dir)):
            print(f"Skipped {path.name}: its name is not its content's hash")
            continue
        stored = target.put(data)
        copied, skipped = copied + stored.created, skipped + (not stored.created)
        if target.get(key) != data:
            print(f"Copy of {key} does not match")
            return 1
    print(
        f"Copied {copied} files to s3://{target.bucket}/{target.prefix} ({skipped} already there)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
