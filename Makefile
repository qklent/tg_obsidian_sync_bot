NOTES_DIR := /home/qklent/programming/Notes/Notes/tg_sync_bot

.PHONY: clarify implement implement-parallel watch pipeline status retry sync-board update-codemap

clarify:
	@echo "Tasks in planning:" && grep -l "^status: planning" $(NOTES_DIR)/*.md 2>/dev/null || echo "  (none)"
	@read -p "Task file: " f && ./scripts/clarify-task.sh $$f

implement:
	./scripts/run-task-pipeline.sh

implement-parallel:
	./scripts/run-parallel-pipeline.sh

watch:
	./scripts/watch-tasks.sh

retry:
	claude "/project:retry-failed"

sync-board:
	./scripts/sync-board.sh

update-codemap:
	claude "/project:update-codemap"

status:
	@echo "Planning:" && grep -l "^status: planning" $(NOTES_DIR)/*.md 2>/dev/null || echo "  none"
	@echo "Todo:" && grep -l "^status: todo" $(NOTES_DIR)/*.md 2>/dev/null || echo "  none"
	@echo "In Progress:" && grep -l "^status: in-progress" $(NOTES_DIR)/*.md 2>/dev/null || echo "  none"
	@echo "Review:" && grep -l "^status: review" $(NOTES_DIR)/*.md 2>/dev/null || echo "  none"
	@echo "Failed:" && grep -l "^status: failed" $(NOTES_DIR)/*.md 2>/dev/null || echo "  none"
	@echo "Done:" && grep -l "^status: done" $(NOTES_DIR)/done/*.md 2>/dev/null || echo "  none"
