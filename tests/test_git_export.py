import subprocess
from pathlib import Path

import pytest

from app.models.doc import Doc
from app.publish.git_export import GitExportError, export_doc_to_git


def _make_doc(git_export_enabled: bool = True, git_export_path: str | None = None) -> Doc:
    return Doc(
        org_id=1,
        source_id=1,
        title="Pet Store",
        slug="pet-store",
        current_content_md="# Pet Store\n\nUpdated content.",
        version=2,
        git_export_enabled=git_export_enabled,
        git_export_path=git_export_path,
    )


def _init_bare_remote(tmp_path: Path) -> Path:
    remote = tmp_path / "remote.git"
    remote.mkdir()
    subprocess.run(["git", "init", "--bare", "-q", str(remote)], check=True)
    return remote


def test_git_export_writes_file_under_org_slug_path(tmp_path) -> None:
    doc = _make_doc()
    export_doc_to_git(doc, "acme", base_dir=tmp_path)

    written = tmp_path / "acme" / "pet-store.md"
    assert written.exists()
    assert written.read_text() == "# Pet Store\n\nUpdated content."


def test_git_export_updates_last_git_export_commit(tmp_path) -> None:
    doc = _make_doc()
    sha = export_doc_to_git(doc, "acme", base_dir=tmp_path)

    assert sha is not None
    assert len(sha) == 40
    assert doc.last_git_export_commit == sha


def test_git_export_pushes_to_configured_remote(tmp_path) -> None:
    remote = _init_bare_remote(tmp_path)
    doc = _make_doc(git_export_path=str(remote))
    export_doc_to_git(doc, "acme", base_dir=tmp_path / "exports")

    branch = subprocess.run(
        ["git", "--git-dir", str(remote), "branch", "--format=%(refname:short)"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert branch  # a branch exists on the remote after push


def test_git_export_skipped_when_disabled(tmp_path) -> None:
    doc = _make_doc(git_export_enabled=False)
    sha = export_doc_to_git(doc, "acme", base_dir=tmp_path)

    assert sha is None
    assert not (tmp_path / "acme").exists()


def test_git_export_raises_on_failure(tmp_path) -> None:
    doc = _make_doc()
    target = tmp_path / "acme" / "pet-store.md"
    target.parent.mkdir(parents=True)
    target.write_text("existing")
    target.chmod(0o400)

    # .git dir cannot be created if the path is unwritable
    export_dir = tmp_path / "acme"
    export_dir.chmod(0o500)
    try:
        with pytest.raises((GitExportError, OSError)):
            export_doc_to_git(doc, "acme", base_dir=tmp_path)
    finally:
        export_dir.chmod(0o700)
