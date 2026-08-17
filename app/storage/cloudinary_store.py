import logging
from io import BytesIO
from pathlib import Path
from urllib.parse import urlparse

import cloudinary
import cloudinary.api
import cloudinary.uploader
import cloudinary.utils
import httpx

logger = logging.getLogger(__name__)

_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".ico", ".tiff", ".avif"}


def _parse_cloudinary_url(url: str) -> dict[str, str]:
    """Parse a ``cloudinary://<api_key>:<api_secret>@<cloud_name>`` URL.

    Format matches the API environment variable used in the Cloudinary Python
    quick start (https://cloudinary.com/documentation/python_quickstart).
    """
    parsed = urlparse(url)
    if parsed.scheme != "cloudinary":
        raise ValueError(
            "DOCVERSION_CLOUDINARY_URL must start with 'cloudinary://'"
        )
    if not parsed.hostname or not parsed.username or not parsed.password:
        raise ValueError(
            "DOCVERSION_CLOUDINARY_URL must be "
            "'cloudinary://<api_key>:<api_secret>@<cloud_name>'"
        )
    return {
        "cloud_name": parsed.hostname,
        "api_key": parsed.username,
        "api_secret": parsed.password,
    }


def _is_image_ref(ref: str) -> bool:
    return Path(ref).suffix.lower() in _IMAGE_SUFFIXES


class CloudinaryStore:
    """Snapshot blob storage backed by Cloudinary with private delivery.

    Mirrors the ``SnapshotStore`` (local disk) interface so diff engines and the
    scheduler are backend-agnostic. Stored refs are Cloudinary public IDs:

    - raw content:   ``snapshots/<id>.raw``  (resource_type ``raw``)
    - screenshots:   ``snapshots/<id>.png``  (resource_type ``image``)

    Cloudinary stores image public IDs without the file extension, while raw
    public IDs must include it — refs keep the extension for readability and the
    canonical public ID is derived on read/delete.
    """

    _FOLDER = "snapshots"

    def __init__(self, cloudinary_url: str) -> None:
        creds = _parse_cloudinary_url(cloudinary_url)
        cloudinary.config(
            cloud_name=creds["cloud_name"],
            api_key=creds["api_key"],
            api_secret=creds["api_secret"],
            secure=True,
        )
        self.cloud_name = creds["cloud_name"]

    @staticmethod
    def _public_id(snapshot_id: int, suffix: str) -> str:
        return f"{CloudinaryStore._FOLDER}/{snapshot_id}.{suffix}"

    @staticmethod
    def _canonical_public_id(ref: str) -> str:
        """Image assets are stored by Cloudinary without their extension."""
        if not _is_image_ref(ref):
            return ref
        return ref[: -len(Path(ref).suffix)]

    @staticmethod
    def _format(ref: str) -> str:
        return Path(ref).suffix.lstrip(".")

    def write_raw(self, snapshot_id: int, content: bytes, suffix: str = "raw") -> str:
        ref = self._public_id(snapshot_id, suffix)
        resource_type = "image" if _is_image_ref(ref) else "raw"
        cloudinary.uploader.upload(
            BytesIO(content),
            resource_type=resource_type,
            type="private",
            public_id=self._canonical_public_id(ref),
            unique_filename=False,
            overwrite=False,
            filename=f"{snapshot_id}.{suffix}",
        )
        logger.info("uploaded snapshot %s to Cloudinary (%s/%s)", snapshot_id, resource_type, ref)
        return ref

    def read_raw(self, raw_storage_ref: str) -> bytes:
        resource_type = "image" if _is_image_ref(raw_storage_ref) else "raw"
        url = cloudinary.utils.private_download_url(
            self._canonical_public_id(raw_storage_ref),
            self._format(raw_storage_ref),
            resource_type=resource_type,
            type="private",
        )
        with httpx.Client(timeout=30.0) as client:
            response = client.get(url)
            response.raise_for_status()
            return response.content

    def delete_raw(self, raw_storage_ref: str) -> None:
        resource_type = "image" if _is_image_ref(raw_storage_ref) else "raw"
        cloudinary.uploader.destroy(
            self._canonical_public_id(raw_storage_ref),
            resource_type=resource_type,
            type="private",
            invalidate=True,
        )
        logger.info("deleted snapshot asset %s from Cloudinary", raw_storage_ref)

    def write_public_md(self, org_slug: str, doc_slug: str, content: str) -> str:
        """Mirror an exported doc as a public raw asset (deliverable via CDN).

        Returns the secure CDN delivery URL. Only the exported markdown mirror is
        public — snapshot blobs remain private.
        """
        public_id = f"git-exports/{org_slug}/{doc_slug}.md"
        result = cloudinary.uploader.upload(
            BytesIO(content.encode("utf-8")),
            resource_type="raw",
            type="upload",
            public_id=public_id,
            unique_filename=False,
            overwrite=True,
            filename=f"{doc_slug}.md",
        )
        return result["secure_url"]

    def list_refs(self) -> list[str]:
        """List every snapshot ref currently in Cloudinary (for orphan cleanup).

        Screenshot (image) public IDs lack an extension, so the canonical ref is
        reconstructed as ``<public_id>.png`` to match what write_raw stores.
        """
        refs: list[str] = []
        for resource_type in ("raw", "image"):
            cursor: str | None = None
            while True:
                params: dict[str, str] = {"type": "private", "resource_type": resource_type}
                if cursor:
                    params["next_cursor"] = cursor
                result = cloudinary.api.resources_by_asset_folder(self._FOLDER, **params)
                for res in result.get("resources", []):
                    public_id = res["public_id"]
                    if not public_id.startswith(self._FOLDER + "/"):
                        public_id = f"{self._FOLDER}/{public_id}"
                    if resource_type == "image":
                        public_id += ".png"
                    refs.append(public_id)
                cursor = result.get("next_cursor")
                if not cursor:
                    break
        return refs