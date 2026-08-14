import logging
import subprocess
from pathlib import Path

from app.config import get_settings
from app.models.doc import Doc

logger = logging.getLogger(__name__)


class GitExportError(Exception):
    pass


def _run_git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        capture_output=True,
        text=True,
        cwd=str(cwd),
        check=False,
    )
    if result.returncode != 0:
        raise GitExportError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout.strip()


def _ensure_repo(export_dir: Path, remote_url: str | None) -> None:
    if not (export_dir / ".git").exists():
        _run_git(export_dir, "init", "-q")
        _run_git(export_dir, "config", "user.email", "docversion@local")
        _run_git(export_dir, "config", "user.name", "DocVersion")
        if remote_url:
            _run_git(export_dir, "remote", "add", "origin", remote_url)


def export_doc_to_git(
    doc: Doc,
    org_slug: str,
    base_dir: str | Path | None = None,
    remote_url: str | None = None,
) -> str | None:
    """Mirror doc.current_content_md to a per-org git export and commit/push.

    Returns the resulting commit SHA, or None if the doc has git export
    disabled. Callers must wrap this in try/except — a failure here is logged
    but must never roll back the authoritative DB publish.
    """
    if not doc.git_export_enabled:
        return None

    settings = get_settings()
    base = Path(base_dir) if base_dir else Path(settings.git_export_base_dir)
    export_dir = base / org_slug
    export_dir.mkdir(parents=True, exist_ok=True)

    file_path = export_dir / f"{doc.slug}.md"
    file_path.write_text(doc.current_content_md, encoding="utf-8")

    resolved_remote = remote_url or doc.git_export_path
    _ensure_repo(export_dir, resolved_remote)
    _run_git(export_dir, "add", file_path.name)
    _run_git(export_dir, "commit", "-q", "-m", f"docs: update {doc.slug}")
    if resolved_remote:
        _run_git(export_dir, "push", "origin", "HEAD")

    sha = _run_git(export_dir, "rev-parse", "HEAD")
    doc.last_git_export_commit = sha
    logger.info("exported doc %s to git export (%s)", doc.slug, sha)
    return sha
