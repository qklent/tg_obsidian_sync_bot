"""Parse and manipulate Obsidian Kanban board.md files.

The Kanban plugin stores boards as markdown with lane headings (## emoji Title)
and card items (- [[link|label]]). A settings block at the end is preserved.
"""

import logging
import re
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

# Maps logical status names to the lane headings used in board.md
STATUS_TO_LANE: dict[str, str] = {
    "planning": "📋 Planning",
    "todo": "📥 Todo",
    "in-progress": "⚙️ In Progress",
    "review": "👀 Review",
    "failed": "❌ Failed",
    "done": "✅ Done",
}

LANE_TO_STATUS: dict[str, str] = {v: k for k, v in STATUS_TO_LANE.items()}

_SETTINGS_PATTERN = re.compile(r"(%% kanban:settings\n.*?\n%%)", re.DOTALL)


@dataclass
class KanbanCard:
    """A single card on the board."""
    link: str    # e.g. "projects/disorder_fix/T001-setup-env"
    label: str   # e.g. "Setup environment"

    def to_markdown(self) -> str:
        return f"- [[{self.link}|{self.label}]]"


@dataclass
class KanbanLane:
    heading: str          # e.g. "📋 Planning"
    cards: list[KanbanCard]


class KanbanBoard:
    """In-memory representation of a board.md file."""

    def __init__(self, lanes: list[KanbanLane], settings_block: str | None = None) -> None:
        self.lanes = lanes
        self.settings_block = settings_block
        self._lane_map: dict[str, KanbanLane] = {lane.heading: lane for lane in lanes}

    @classmethod
    def read(cls, path: Path) -> "KanbanBoard":
        """Parse a board.md file into a KanbanBoard."""
        text = path.read_text(encoding="utf-8")
        return cls.parse(text)

    @classmethod
    def parse(cls, text: str) -> "KanbanBoard":
        """Parse board markdown text into a KanbanBoard."""
        # Extract settings block
        settings_block: str | None = None
        settings_match = _SETTINGS_PATTERN.search(text)
        if settings_match:
            settings_block = settings_match.group(1)
            text = text[:settings_match.start()].rstrip() + "\n"

        # Strip frontmatter
        body = text
        if body.startswith("---"):
            end = body.find("---", 3)
            if end != -1:
                body = body[end + 3:].lstrip("\n")

        lanes: list[KanbanLane] = []
        current_heading: str | None = None
        current_cards: list[KanbanCard] = []

        for line in body.splitlines():
            if line.startswith("## "):
                if current_heading is not None:
                    lanes.append(KanbanLane(heading=current_heading, cards=current_cards))
                current_heading = line[3:].strip()
                current_cards = []
            elif line.startswith("- [[") and current_heading is not None:
                card = _parse_card_line(line)
                if card:
                    current_cards.append(card)

        if current_heading is not None:
            lanes.append(KanbanLane(heading=current_heading, cards=current_cards))

        return cls(lanes, settings_block)

    def write(self, path: Path) -> None:
        """Write the board back to a file."""
        path.write_text(self.to_markdown(), encoding="utf-8")

    def to_markdown(self) -> str:
        """Serialize the board to markdown."""
        parts = ["---\nkanban-plugin: basic\n---\n"]

        for lane in self.lanes:
            parts.append(f"\n## {lane.heading}\n")
            for card in lane.cards:
                parts.append(card.to_markdown())
            parts.append("")  # blank line after cards

        if self.settings_block:
            parts.append(self.settings_block)
            parts.append("")

        return "\n".join(parts)

    def add_card(self, status: str, card: KanbanCard) -> None:
        """Add a card to the lane matching the given status."""
        heading = STATUS_TO_LANE.get(status)
        if heading is None:
            raise ValueError(f"Unknown status: {status!r}. Valid: {list(STATUS_TO_LANE)}")
        lane = self._lane_map.get(heading)
        if lane is None:
            lane = KanbanLane(heading=heading, cards=[])
            self.lanes.append(lane)
            self._lane_map[heading] = lane
        lane.cards.append(card)

    def move_card(self, link: str, new_status: str) -> bool:
        """Move a card identified by its link to a new lane. Returns True if found."""
        new_heading = STATUS_TO_LANE.get(new_status)
        if new_heading is None:
            raise ValueError(f"Unknown status: {new_status!r}. Valid: {list(STATUS_TO_LANE)}")

        # Find and remove the card from its current lane
        card: KanbanCard | None = None
        for lane in self.lanes:
            for i, c in enumerate(lane.cards):
                if c.link == link:
                    card = lane.cards.pop(i)
                    break
            if card:
                break

        if card is None:
            return False

        # Add to new lane
        dest = self._lane_map.get(new_heading)
        if dest is None:
            dest = KanbanLane(heading=new_heading, cards=[])
            self.lanes.append(dest)
            self._lane_map[new_heading] = dest
        dest.cards.append(card)
        return True

    def remove_card(self, link: str) -> bool:
        """Remove a card by link. Returns True if found."""
        for lane in self.lanes:
            for i, c in enumerate(lane.cards):
                if c.link == link:
                    lane.cards.pop(i)
                    return True
        return False

    def find_card(self, link: str) -> tuple[str, KanbanCard] | None:
        """Find a card by link. Returns (status, card) or None."""
        for lane in self.lanes:
            for c in lane.cards:
                if c.link == link:
                    status = LANE_TO_STATUS.get(lane.heading, lane.heading)
                    return status, c
        return None


def _parse_card_line(line: str) -> KanbanCard | None:
    """Parse a line like '- [[link|label]]' into a KanbanCard."""
    match = re.match(r"^- \[\[([^|]+)\|([^\]]+)\]\]", line.strip())
    if not match:
        # Try without alias: - [[link]]
        match = re.match(r"^- \[\[([^\]]+)\]\]", line.strip())
        if match:
            link = match.group(1)
            return KanbanCard(link=link, label=link.split("/")[-1])
        return None
    return KanbanCard(link=match.group(1), label=match.group(2))
