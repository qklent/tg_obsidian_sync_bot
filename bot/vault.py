import logging
from datetime import datetime, timezone
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

logger = logging.getLogger(__name__)

TEMPLATE_DIR = Path(__file__).resolve().parent
TEMPLATE_NAME = "note_template.md.j2"


class VaultWriter:
    def __init__(self, repo_path: str, attachments_dir: str = "images"):
        self.repo_path = Path(repo_path)
        self.attachments_dir = attachments_dir
        self._env = Environment(
            loader=FileSystemLoader(str(TEMPLATE_DIR)),
            keep_trailing_newline=True,
        )
        self._template = self._env.get_template(TEMPLATE_NAME)

    def write_note(
        self, folder: str, filename: str, title: str, content: str, tags: list[str]
    ) -> Path:
        """Write a markdown note to the vault. Returns the path of the created file."""
        # Ensure .md extension
        if not filename.endswith(".md"):
            filename = filename + ".md"

        folder_path = self.repo_path / folder
        folder_path.mkdir(parents=True, exist_ok=True)

        file_path = folder_path / filename

        # Handle filename collision
        if file_path.exists():
            stem = file_path.stem
            suffix = file_path.suffix
            counter = 1
            while file_path.exists():
                file_path = folder_path / f"{stem}-{counter}{suffix}"
                counter += 1

        rendered = self._template.render(
            tags=tags,
            title=title,
            content=content,
            created=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        )

        file_path.write_text(rendered, encoding="utf-8")
        logger.info("Wrote note: %s", file_path.relative_to(self.repo_path))
        return file_path

    def save_attachment(self, file_bytes: bytes, filename: str) -> str:
        """Save an attachment file and return the vault-relative path."""
        attach_dir = self.repo_path / self.attachments_dir
        attach_dir.mkdir(parents=True, exist_ok=True)

        file_path = attach_dir / filename

        # Handle collision
        if file_path.exists():
            stem = file_path.stem
            suffix = file_path.suffix
            counter = 1
            while file_path.exists():
                file_path = attach_dir / f"{stem}-{counter}{suffix}"
                counter += 1

        file_path.write_bytes(file_bytes)
        logger.info("Saved attachment: %s", file_path.relative_to(self.repo_path))
        return f"{self.attachments_dir}/{file_path.name}"
