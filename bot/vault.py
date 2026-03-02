import logging
from datetime import datetime, timezone
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, Template

logger = logging.getLogger(__name__)

TEMPLATE_DIR = Path(__file__).resolve().parent
TEMPLATE_NAME = "note_template.md.j2"
KANBAN_FOLDER_PREFIX = "tg_sync_bot"
KANBAN_VAULT_TEMPLATE = "tg_sync_bot/_template.md"


class VaultWriter:
    def __init__(self, repo_path: str, attachments_dir: str = "images"):
        self.repo_path = Path(repo_path)
        self.attachments_dir = attachments_dir
        self._env = Environment(
            loader=FileSystemLoader(str(TEMPLATE_DIR)),
            keep_trailing_newline=True,
        )
        self._template = self._env.get_template(TEMPLATE_NAME)

    def _load_kanban_template(self) -> Template:
        """Load the kanban note template from the vault at write time."""
        template_path = self.repo_path / KANBAN_VAULT_TEMPLATE
        if not template_path.exists():
            raise FileNotFoundError(
                f"Kanban template not found in vault: {template_path}. "
                f"Expected a Jinja2 template at {KANBAN_VAULT_TEMPLATE}."
            )
        env = Environment(keep_trailing_newline=True)
        return env.from_string(template_path.read_text(encoding="utf-8"))

    def write_note(
        self,
        folder: str,
        filename: str,
        title: str,
        content: str,
        tags: list[str],
        status: str | None = None,
        priority: str | None = None,
        clarification_needed: bool | None = None,
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

        is_kanban = folder.startswith(KANBAN_FOLDER_PREFIX)
        template = self._load_kanban_template() if is_kanban else self._template
        render_kwargs: dict = dict(
            tags=tags,
            title=title,
            content=content,
            created=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        )
        if is_kanban:
            render_kwargs["status"] = status or "planning"
            render_kwargs["priority"] = priority or "medium"
            render_kwargs["clarification_needed"] = (
                clarification_needed if clarification_needed is not None else True
            )
        rendered = template.render(**render_kwargs)

        file_path.write_text(rendered, encoding="utf-8")
        logger.info("Wrote note: %s", file_path.relative_to(self.repo_path))
        return file_path

    def list_inbox_notes(self, inbox_folder: str = "inbox") -> list[Path]:
        """Return all .md files in the inbox folder."""
        inbox_path = self.repo_path / inbox_folder
        if not inbox_path.exists():
            return []
        return sorted(inbox_path.glob("*.md"))

    def move_note(self, src_path: Path, dest_folder: str) -> Path:
        """Move a note to a different vault folder. Returns the new path."""
        dest_dir = self.repo_path / dest_folder
        dest_dir.mkdir(parents=True, exist_ok=True)

        dest_path = dest_dir / src_path.name
        if dest_path.exists():
            stem = dest_path.stem
            suffix = dest_path.suffix
            counter = 1
            while dest_path.exists():
                dest_path = dest_dir / f"{stem}-{counter}{suffix}"
                counter += 1

        src_path.rename(dest_path)
        logger.info("Moved note: %s → %s", src_path, dest_path)
        return dest_path

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
