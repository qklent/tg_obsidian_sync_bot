"""Business logic for syncing project tasks, changelogs, and changes to the vault.

Orchestrates kanban board updates, note creation, and git dirty marking.
"""

import logging
import re
from datetime import datetime, timezone
from pathlib import Path

from bot.api_models import ContentSyncRequest, TaskCreateRequest, TaskUpdateRequest
from bot.kanban import KanbanBoard, KanbanCard

logger = logging.getLogger(__name__)


def _slugify(text: str) -> str:
    """Convert text to a filename-safe slug."""
    slug = text.lower().strip()
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    return slug.strip("-")


class ProjectSync:
    """Handles project task and content synchronization to the Obsidian vault."""

    def __init__(
        self,
        vault_path: str,
        board_path: str,
        projects_dir: str,
    ) -> None:
        self.vault_path = Path(vault_path)
        self.board_path = self.vault_path / board_path
        self.projects_dir = projects_dir

    def _task_note_path(self, project: str, task_id: str, title: str) -> Path:
        """Build the path for a task note file."""
        slug = _slugify(title)
        filename = f"{task_id}-{slug}.md"
        return self.vault_path / self.projects_dir / project / filename

    def _task_link(self, project: str, task_id: str, title: str) -> str:
        """Build the vault-relative wikilink path (without .md)."""
        slug = _slugify(title)
        filename = f"{task_id}-{slug}"
        return f"{self.projects_dir}/{project}/{filename}"

    def create_task(self, req: TaskCreateRequest) -> Path:
        """Create a task note and add a card to the kanban board.

        Returns the path of the created note.
        """
        note_path = self._task_note_path(req.project, req.task_id, req.title)
        note_path.parent.mkdir(parents=True, exist_ok=True)

        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        tags_yaml = "\n".join(f"  - {t}" for t in req.tags) if req.tags else "  - task"
        content = (
            f"---\n"
            f"status: {req.status}\n"
            f"priority: {req.priority}\n"
            f"project: {req.project}\n"
            f"task_id: {req.task_id}\n"
            f"created: {now}\n"
            f"tags:\n{tags_yaml}\n"
            f"---\n\n"
            f"# {req.title}\n\n"
            f"{req.description}\n"
        )
        note_path.write_text(content, encoding="utf-8")
        logger.info("Created task note: %s", note_path.relative_to(self.vault_path))

        # Update kanban board
        link = self._task_link(req.project, req.task_id, req.title)
        card = KanbanCard(link=link, label=req.title)
        self._update_board(lambda board: board.add_card(req.status, card))

        return note_path

    def update_task(self, req: TaskUpdateRequest) -> Path | None:
        """Update a task's status in its note frontmatter and move its kanban card.

        Returns the note path if found, None otherwise.
        """
        project_dir = self.vault_path / self.projects_dir / req.project
        if not project_dir.exists():
            logger.warning("Project directory not found: %s", project_dir)
            return None

        # Find the task note by task_id prefix
        note_path: Path | None = None
        for md in project_dir.glob("*.md"):
            if md.name.startswith(f"{req.task_id}-"):
                note_path = md
                break

        if note_path is None:
            logger.warning("Task note not found for %s in %s", req.task_id, req.project)
            return None

        # Update frontmatter status
        text = note_path.read_text(encoding="utf-8")
        text = re.sub(r"^status:\s*\S+", f"status: {req.new_status}", text, count=1, flags=re.MULTILINE)

        # Append comment if provided
        if req.comment:
            now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
            text += f"\n---\n**Update ({now}):** {req.comment}\n"

        note_path.write_text(text, encoding="utf-8")
        logger.info("Updated task %s status to %s", req.task_id, req.new_status)

        # Move kanban card
        link_stem = note_path.stem  # e.g. T001-setup-env
        link = f"{self.projects_dir}/{req.project}/{link_stem}"
        self._update_board(lambda board: board.move_card(link, req.new_status))

        return note_path

    def sync_changelog(self, req: ContentSyncRequest) -> Path:
        """Mirror changelog content to the vault. Returns the written path."""
        return self._sync_file(req.project, "CHANGELOG.md", req.changelog_content)

    def sync_changes(self, req: ContentSyncRequest) -> Path:
        """Mirror changes content to the vault. Returns the written path."""
        return self._sync_file(req.project, "CHANGES.md", req.changes_content)

    def _sync_file(self, project: str, filename: str, content: str) -> Path:
        """Write content to a project file in the vault."""
        file_path = self.vault_path / self.projects_dir / project / filename
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(content, encoding="utf-8")
        logger.info("Synced %s for project %s", filename, project)
        return file_path

    def _update_board(self, operation: object) -> None:
        """Read the board, apply an operation, write it back.

        Creates the board with default lanes if it doesn't exist.
        """
        if self.board_path.exists():
            board = KanbanBoard.read(self.board_path)
        else:
            from bot.kanban import STATUS_TO_LANE, KanbanLane
            lanes = [KanbanLane(heading=h, cards=[]) for h in STATUS_TO_LANE.values()]
            board = KanbanBoard(
                lanes=lanes,
                settings_block='%% kanban:settings\n{"kanban-plugin":"basic","lane-width":250,"show-checkboxes":false}\n%%',
            )
            self.board_path.parent.mkdir(parents=True, exist_ok=True)

        operation(board)  # type: ignore[operator]
        board.write(self.board_path)
