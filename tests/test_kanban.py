"""Unit tests for bot/kanban.py — Kanban board parser and manipulator."""

import textwrap
from pathlib import Path

import pytest

from bot.kanban import KanbanBoard, KanbanCard, KanbanLane

SAMPLE_BOARD = textwrap.dedent("""\
    ---
    kanban-plugin: basic
    ---

    ## 📋 Planning

    - [[projects/disorder_fix/T001-setup-env|Setup environment]]

    ## 📥 Todo

    - [[projects/disorder_fix/T002-implement|Implement feature]]
    - [[projects/disorder_fix/T003-docs|Write docs]]

    ## ⚙️ In Progress


    ## 👀 Review


    ## ❌ Failed


    ## ✅ Done

    - [[projects/disorder_fix/T000-init|Init project]]

    %% kanban:settings
    {"kanban-plugin":"basic","lane-width":250,"show-checkboxes":false}
    %%
""")


class TestParse:
    def test_parse_lanes(self):
        board = KanbanBoard.parse(SAMPLE_BOARD)
        headings = [lane.heading for lane in board.lanes]
        assert headings == [
            "📋 Planning", "📥 Todo", "⚙️ In Progress",
            "👀 Review", "❌ Failed", "✅ Done",
        ]

    def test_parse_cards(self):
        board = KanbanBoard.parse(SAMPLE_BOARD)
        todo = next(l for l in board.lanes if l.heading == "📥 Todo")
        assert len(todo.cards) == 2
        assert todo.cards[0].link == "projects/disorder_fix/T002-implement"
        assert todo.cards[0].label == "Implement feature"

    def test_parse_preserves_settings(self):
        board = KanbanBoard.parse(SAMPLE_BOARD)
        assert board.settings_block is not None
        assert "lane-width" in board.settings_block

    def test_parse_empty_lanes(self):
        board = KanbanBoard.parse(SAMPLE_BOARD)
        in_progress = next(l for l in board.lanes if l.heading == "⚙️ In Progress")
        assert len(in_progress.cards) == 0


class TestAddCard:
    def test_add_card_to_existing_lane(self):
        board = KanbanBoard.parse(SAMPLE_BOARD)
        card = KanbanCard(link="projects/foo/T099-test", label="Test task")
        board.add_card("todo", card)

        todo = next(l for l in board.lanes if l.heading == "📥 Todo")
        assert len(todo.cards) == 3
        assert todo.cards[-1].link == "projects/foo/T099-test"

    def test_add_card_to_empty_lane(self):
        board = KanbanBoard.parse(SAMPLE_BOARD)
        card = KanbanCard(link="projects/foo/T050-review", label="Review task")
        board.add_card("in-progress", card)

        lane = next(l for l in board.lanes if l.heading == "⚙️ In Progress")
        assert len(lane.cards) == 1

    def test_add_card_unknown_status_raises(self):
        board = KanbanBoard.parse(SAMPLE_BOARD)
        card = KanbanCard(link="x", label="x")
        with pytest.raises(ValueError, match="Unknown status"):
            board.add_card("invalid-status", card)


class TestMoveCard:
    def test_move_card_between_lanes(self):
        board = KanbanBoard.parse(SAMPLE_BOARD)
        moved = board.move_card("projects/disorder_fix/T002-implement", "in-progress")
        assert moved is True

        todo = next(l for l in board.lanes if l.heading == "📥 Todo")
        assert len(todo.cards) == 1  # was 2, now 1

        ip = next(l for l in board.lanes if l.heading == "⚙️ In Progress")
        assert len(ip.cards) == 1
        assert ip.cards[0].label == "Implement feature"

    def test_move_card_not_found(self):
        board = KanbanBoard.parse(SAMPLE_BOARD)
        moved = board.move_card("nonexistent/link", "done")
        assert moved is False

    def test_move_card_unknown_status_raises(self):
        board = KanbanBoard.parse(SAMPLE_BOARD)
        with pytest.raises(ValueError):
            board.move_card("projects/disorder_fix/T002-implement", "bogus")


class TestRemoveCard:
    def test_remove_existing_card(self):
        board = KanbanBoard.parse(SAMPLE_BOARD)
        removed = board.remove_card("projects/disorder_fix/T002-implement")
        assert removed is True

        todo = next(l for l in board.lanes if l.heading == "📥 Todo")
        assert len(todo.cards) == 1

    def test_remove_nonexistent_card(self):
        board = KanbanBoard.parse(SAMPLE_BOARD)
        removed = board.remove_card("nonexistent")
        assert removed is False


class TestFindCard:
    def test_find_existing(self):
        board = KanbanBoard.parse(SAMPLE_BOARD)
        result = board.find_card("projects/disorder_fix/T001-setup-env")
        assert result is not None
        status, card = result
        assert status == "planning"
        assert card.label == "Setup environment"

    def test_find_nonexistent(self):
        board = KanbanBoard.parse(SAMPLE_BOARD)
        assert board.find_card("nonexistent") is None


class TestRoundTrip:
    def test_parse_and_serialize(self):
        board = KanbanBoard.parse(SAMPLE_BOARD)
        output = board.to_markdown()

        # Re-parse to verify structural equivalence
        board2 = KanbanBoard.parse(output)
        assert len(board2.lanes) == len(board.lanes)
        for lane1, lane2 in zip(board.lanes, board2.lanes):
            assert lane1.heading == lane2.heading
            assert len(lane1.cards) == len(lane2.cards)
            for c1, c2 in zip(lane1.cards, lane2.cards):
                assert c1.link == c2.link
                assert c1.label == c2.label

    def test_roundtrip_preserves_settings(self):
        board = KanbanBoard.parse(SAMPLE_BOARD)
        output = board.to_markdown()
        assert "kanban:settings" in output
        assert "lane-width" in output

    def test_write_and_read_file(self, tmp_path: Path):
        board = KanbanBoard.parse(SAMPLE_BOARD)
        board.add_card("review", KanbanCard(link="test/T1", label="Test"))

        file_path = tmp_path / "board.md"
        board.write(file_path)

        board2 = KanbanBoard.read(file_path)
        review = next(l for l in board2.lanes if l.heading == "👀 Review")
        assert len(review.cards) == 1
        assert review.cards[0].link == "test/T1"
