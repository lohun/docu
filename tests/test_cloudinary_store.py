from io import BytesIO

import pytest

from app.storage.cloudinary_store import CloudinaryStore


@pytest.fixture(autouse=True)
def _mock_cloudinary(monkeypatch):
    """Point the cloudinary SDK module functions at in-memory fakes.

    The global cloudinary config is shared across tests, so isolate each store
    instance behind a unique credential set.
    """
    cloudinary_uploads: list[tuple[str, dict]] = []
    cloudinary_deletes: list[tuple[str, dict]] = []
    download_urls: dict[str, str] = {}
    list_resources: list[dict] = []

    def fake_upload(file, **options):
        cloudinary_uploads.append((file, options))
        return {"public_id": options.get("public_id", ""), "secure_url": "https://cdn/asset"}

    def fake_destroy(public_id, **options):
        cloudinary_deletes.append((public_id, options))
        return {"result": "ok"}

    def fake_private_download_url(public_id, fmt, **options):
        key = (public_id, fmt, options.get("resource_type"), options.get("type"))
        url = download_urls.get(key, "https://api.cloudinary.com/private")
        download_urls[key] = url
        return url

    def fake_resources_by_asset_folder(folder, **options):
        return {"resources": list_resources, "next_cursor": None}

    import cloudinary.utils  # noqa: F401

    import cloudinary.api
    import cloudinary.uploader

    monkeypatch.setattr(cloudinary.uploader, "upload", fake_upload)
    monkeypatch.setattr(cloudinary.uploader, "destroy", fake_destroy)
    monkeypatch.setattr(cloudinary.utils, "private_download_url", fake_private_download_url)
    monkeypatch.setattr(cloudinary.api, "resources_by_asset_folder", fake_resources_by_asset_folder)

    state = {
        "uploads": cloudinary_uploads,
        "deletes": cloudinary_deletes,
        "download_urls": download_urls,
        "list_resources": list_resources,
    }
    yield state


def _store() -> CloudinaryStore:
    return CloudinaryStore("cloudinary://api_key:api_secret@mycloud")


def test_write_raw_uploads_as_private_raw_asset(_mock_cloudinary) -> None:
    store = _store()
    ref = store.write_raw(7, b"payload")
    assert ref == "snapshots/7.raw"

    _, options = _mock_cloudinary["uploads"][0]
    assert options["resource_type"] == "raw"
    assert options["type"] == "private"
    assert options["public_id"] == "snapshots/7.raw"
    assert options["overwrite"] is False
    assert isinstance(options["filename"], str)


def test_write_screenshot_is_image_asset_without_extension(_mock_cloudinary) -> None:
    store = _store()
    ref = store.write_raw(7, b"\x89PNG", suffix="png")
    assert ref == "snapshots/7.png"

    _, options = _mock_cloudinary["uploads"][0]
    assert options["resource_type"] == "image"
    assert options["public_id"] == "snapshots/7"
    assert options["type"] == "private"


def test_read_raw_fetches_private_download_url(_mock_cloudinary, monkeypatch) -> None:
    store = _store()

    class _FakeResponse:
        content = b"fetched"

        def raise_for_status(self) -> None:
            return None

    monkeypatch.setattr("app.storage.cloudinary_store.httpx.Client", lambda timeout: type(
        "FakeClient", (), {"__enter__": lambda self: self, "__exit__": lambda *a: None, "get": lambda self, url: _FakeResponse()}
    )())
    data = store.read_raw("snapshots/7.raw")
    assert data == b"fetched"
    assert ("snapshots/7.raw", "raw", "raw", "private") in _mock_cloudinary["download_urls"]


def test_read_screenshot_uses_image_resource_type(_mock_cloudinary, monkeypatch) -> None:
    store = _store()

    class _FakeResponse:
        content = b"img"

        def raise_for_status(self) -> None:
            return None

    monkeypatch.setattr("app.storage.cloudinary_store.httpx.Client", lambda timeout: type(
        "FakeClient", (), {"__enter__": lambda self: self, "__exit__": lambda *a: None, "get": lambda self, url: _FakeResponse()}
    )())
    data = store.read_raw("snapshots/7.png")
    assert data == b"img"
    assert ("snapshots/7", "png", "image", "private") in _mock_cloudinary["download_urls"]


def test_delete_raw_uses_canonical_public_id(_mock_cloudinary) -> None:
    store = _store()
    store.delete_raw("snapshots/7.raw")
    public_id, options = _mock_cloudinary["deletes"][0]
    assert public_id == "snapshots/7.raw"
    assert options["resource_type"] == "raw"
    assert options["type"] == "private"
    assert options["invalidate"] is True

    store.delete_raw("snapshots/8.png")
    public_id, options = _mock_cloudinary["deletes"][1]
    assert public_id == "snapshots/8"
    assert options["resource_type"] == "image"


def test_list_refs_reconstructs_png_refs_for_images(_mock_cloudinary) -> None:
    import cloudinary.api

    def fake_list(folder, **options):
        if options["resource_type"] == "raw":
            return {"resources": [{"public_id": "1.raw"}], "next_cursor": None}
        return {"resources": [{"public_id": "2"}], "next_cursor": None}

    import cloudinary.utils  # noqa: F401

    cloudinary.api.resources_by_asset_folder = fake_list

    store = _store()
    assert sorted(store.list_refs()) == ["snapshots/1.raw", "snapshots/2.png"]


def test_parse_cloudinary_url_rejects_bad_scheme() -> None:
    from app.storage.cloudinary_store import _parse_cloudinary_url

    with pytest.raises(ValueError):
        _parse_cloudinary_url("https://mycloud")

    parsed = _parse_cloudinary_url("cloudinary://my_key:my_secret@mycloud")
    assert parsed == {"cloud_name": "mycloud", "api_key": "my_key", "api_secret": "my_secret"}


def test_get_snapshot_store_factory_backend_selection(monkeypatch) -> None:
    from app.config import get_settings
    from app.storage import get_snapshot_store
    from app.storage.cloudinary_store import CloudinaryStore
    from app.storage.snapshot_store import SnapshotStore

    monkeypatch.setenv("DOCVERSION_STORAGE_BACKEND", "local")
    monkeypatch.setenv("DOCVERSION_SNAPSHOT_STORAGE_DIR", "/tmp/nonexistent-dir-test")
    get_settings.cache_clear()
    assert isinstance(get_snapshot_store(), SnapshotStore)
    get_settings.cache_clear()

    monkeypatch.setenv("DOCVERSION_STORAGE_BACKEND", "cloudinary")
    monkeypatch.setenv("DOCVERSION_CLOUDINARY_URL", "cloudinary://k:s@mycloud")
    get_settings.cache_clear()
    assert isinstance(get_snapshot_store(), CloudinaryStore)
    get_settings.cache_clear()