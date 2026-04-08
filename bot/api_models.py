"""Request and response dataclasses for the HTTP API."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class TaskCreateRequest:
    project: str
    task_id: str
    title: str
    description: str = ""
    status: str = "todo"
    priority: str = "medium"
    tags: list[str] = field(default_factory=list)


@dataclass
class TaskUpdateRequest:
    project: str
    task_id: str
    new_status: str
    comment: str = ""


@dataclass
class ContentSyncRequest:
    project: str
    changelog_content: str = ""
    changes_content: str = ""


@dataclass
class ApiResponse:
    ok: bool
    data: dict | None = None
    error: str | None = None

    def to_dict(self) -> dict:
        d: dict = {"ok": self.ok}
        if self.data is not None:
            d["data"] = self.data
        if self.error is not None:
            d["error"] = self.error
        return d
