#!/usr/bin/env python3
"""One-off migration: upload existing local snapshot blobs to Cloudinary.

Reads every rows' local ``raw_storage_ref`` path (and the sibling ``<id>.png``
screenshot if present), uploads it to the private Cloudinary store, and rewrites
``raw_storage_ref`` / ``screenshot_storage_ref`` to the Cloudinary refs.

Safe to re-run: rows already pointing at ``snapshots/<id>.raw`` are skipped.

Usage (uses the app's .env via DOCVERSION_ENV_FILE):
    DOCVERSION_STORAGE_BACKEND=cloudinary \
    DOCVERSION_CLOUDINARY_URL=cloudinary://k:s@cloud \
    .venv/bin/python scripts/migrate_snapshots_to_cloudinary.py [--commit]
Pass --commit to persist changes; otherwise it only reports what it would do.
"""

import argparse
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import get_settings
from app.db_urls import with_sslmode
from app.models.snapshot import Snapshot
from app.storage.cloudinary_store import CloudinaryStore

PREVIEW_TAG = "snapshots/"


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--commit", action="store_true", help="persist DB changes")
    args = parser.parse_args()

    settings = get_settings()
    if not settings.cloudinary_url:
        raise SystemExit("DOCVERSION_CLOUDINARY_URL is required")
    store = CloudinaryStore(settings.cloudinary_url)

    engine = create_async_engine(
        with_sslmode(settings.database_url, settings.database_sslmode)
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)

    updated_raw = 0
    updated_screenshot = 0

    async with factory() as session:
        snapshots = list((await session.scalars(select(Snapshot))).all())
        for snapshot in snapshots:
            if snapshot.raw_storage_ref.startswith(PREVIEW_TAG):
                continue

            local_path = Path(snapshot.raw_storage_ref)
            if not local_path.exists():
                print(f"skip {snapshot.id}: missing file {snapshot.raw_storage_ref}")
                continue

            content = local_path.read_bytes()
            new_ref = store.write_raw(snapshot.id, content)
            print(f"raw {snapshot.id}: {snapshot.raw_storage_ref} -> {new_ref}")
            snapshot.raw_storage_ref = new_ref
            updated_raw += 1

            png = local_path.with_suffix(".png")
            if png.exists():
                new_png_ref = store.write_raw(snapshot.id, png.read_bytes(), suffix="png")
                print(f"png {snapshot.id}: {png} -> {new_png_ref}")
                snapshot.screenshot_storage_ref = new_png_ref
                updated_screenshot += 1

        if args.commit:
            await session.commit()
        else:
            await session.rollback()

    print(
        f"would-commit={args.commit} "
        f"raw_updated={updated_raw} screenshots_updated={updated_screenshot}"
    )


if __name__ == "__main__":
    import anyio

    anyio.run(main)