import asyncio
import logging
import os
import re
import subprocess
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

CONFLICT_RESOLUTION_TIMEOUT = 30 * 60  # 30 minutes


@dataclass
class ConflictBlock:
    id: str
    file_path: str
    block_index: int
    current: str   # HEAD (local) content
    incoming: str  # remote content


@dataclass
class PendingMerge:
    blocks: list[ConflictBlock]
    _pending_ids: set[str] = field(init=False)
    _resolutions: dict[str, str] = field(default_factory=dict, init=False)
    _done_event: asyncio.Event = field(default_factory=asyncio.Event, init=False)

    def __post_init__(self):
        self._pending_ids = {b.id for b in self.blocks}

    def resolve(self, block_id: str, resolution: str) -> bool:
        """Record a resolution ('current' or 'incoming'). Returns False if already resolved."""
        if block_id not in self._pending_ids:
            return False
        self._resolutions[block_id] = resolution
        self._pending_ids.discard(block_id)
        if not self._pending_ids:
            self._done_event.set()
        return True

    def get_resolution(self, block_id: str) -> str:
        return self._resolutions.get(block_id, "current")

    async def wait_for_resolution(self, timeout: float = CONFLICT_RESOLUTION_TIMEOUT) -> bool:
        """Wait until all blocks are resolved. Returns False on timeout."""
        try:
            await asyncio.wait_for(self._done_event.wait(), timeout=timeout)
            return True
        except asyncio.TimeoutError:
            return False


class GitSync:
    def __init__(
        self,
        repo_path: str,
        debounce_seconds: int = 30,
        pull_interval_seconds: int = 60,
    ):
        self.repo_path = repo_path
        self.debounce_seconds = debounce_seconds
        self.pull_interval_seconds = pull_interval_seconds
        self._dirty = False
        self._lock = asyncio.Lock()
        self._note_count = 0
        self._error_callbacks: list[Callable[[str], Awaitable[None]]] = []
        self._on_conflict: Callable[[PendingMerge], Awaitable[None]] | None = None
        self._current_merge: PendingMerge | None = None

    def set_conflict_handler(self, handler: Callable[[PendingMerge], Awaitable[None]]):
        self._on_conflict = handler

    def mark_dirty(self, on_error: Callable[[str], Awaitable[None]] | None = None):
        self._dirty = True
        self._note_count += 1
        if on_error is not None:
            self._error_callbacks.append(on_error)

    async def resolve_conflict_block(self, block_id: str, resolution: str) -> bool:
        """Called by the bot when the user picks a resolution for a conflict block."""
        if self._current_merge is None:
            return False
        return self._current_merge.resolve(block_id, resolution)

    async def sync_loop(self):
        """Run as background task. Periodically commit+push if dirty."""
        while True:
            await asyncio.sleep(self.debounce_seconds)
            if self._dirty:
                async with self._lock:
                    if not self._dirty:
                        continue
                    count = self._note_count
                    callbacks = self._error_callbacks.copy()
                    self._dirty = False
                    self._note_count = 0
                    self._error_callbacks.clear()
                    await self._run_git(count, callbacks)

    async def pull_loop(self):
        """Run as background task. Periodically pull from remote."""
        while True:
            await asyncio.sleep(self.pull_interval_seconds)
            async with self._lock:
                await self._run_pull()

    def _get_auth_remote_url(self) -> str:
        """Return the origin remote URL with GITHUB_TOKEN embedded for dulwich."""
        result = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            cwd=self.repo_path, capture_output=True, text=True, check=True,
        )
        remote_url = result.stdout.strip()
        github_token = os.environ.get("GITHUB_TOKEN", "")
        if github_token and "https://" in remote_url:
            remote_url = remote_url.replace("https://", f"https://x-access-token:{github_token}@")
        return remote_url

    def _dulwich_fetch(self) -> None:
        """Fetch from remote using dulwich (pure Python).

        dulwich implements the git smart-HTTP protocol directly in Python so it
        never needs to fork() a git-remote-https subprocess – which is exactly
        what causes the 'cannot fork() for remote-https' error inside Docker
        containers that have a tight PID limit.
        """
        from dulwich import porcelain
        porcelain.fetch(self.repo_path, remote_location=self._get_auth_remote_url())

    def _dulwich_push(self) -> None:
        """Push to remote using dulwich (pure Python, same fork-free reason as fetch)."""
        from dulwich import porcelain
        from dulwich.repo import Repo
        remote_url = self._get_auth_remote_url()
        with Repo(self.repo_path) as repo:
            head_ref = repo.refs.get_symrefs().get(b"HEAD", b"refs/heads/main")
        porcelain.push(self.repo_path, remote_location=remote_url, refspecs=[head_ref])

    async def _run_pull(self) -> bool:
        """Fetch via dulwich then merge locally. Returns True on success."""
        loop = asyncio.get_running_loop()
        try:
            await loop.run_in_executor(None, self._dulwich_fetch)
        except Exception as e:
            logger.error("Git pull failed (fetch): %s", e)
            return False

        try:
            proc = await asyncio.create_subprocess_exec(
                "git", "merge", "FETCH_HEAD",
                cwd=self.repo_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()

            if proc.returncode == 0:
                logger.info("Git pull: %s", stdout.decode().strip() or "already up to date")
                return True

            out = stdout.decode()
            if "CONFLICT" in out:
                logger.warning("Git pull produced conflicts, awaiting user resolution")
                return await self._handle_conflicts()

            logger.error("Git pull failed (merge): %s", stderr.decode().strip())
            return False

        except Exception:
            logger.exception("Git pull error")
            return False

    async def _handle_conflicts(self) -> bool:
        """Parse conflict markers, notify the user via Telegram, wait for resolution, apply it."""
        proc = await asyncio.create_subprocess_exec(
            "git", "diff", "--name-only", "--diff-filter=U",
            cwd=self.repo_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        conflicting_files = [f for f in stdout.decode().strip().splitlines() if f]

        if not conflicting_files:
            return await self._complete_merge()

        blocks: list[ConflictBlock] = []
        for file_path in conflicting_files:
            full_path = Path(self.repo_path) / file_path
            try:
                content = full_path.read_text()
                blocks.extend(self._parse_conflicts(content, file_path))
            except Exception:
                logger.exception("Failed to parse conflicts in %s", file_path)

        if not blocks:
            logger.error("Conflict files found but no parseable blocks; aborting merge")
            await self._abort_merge()
            return False

        pending = PendingMerge(blocks=blocks)
        self._current_merge = pending

        if self._on_conflict:
            await self._on_conflict(pending)

        resolved = await pending.wait_for_resolution()
        self._current_merge = None

        if not resolved:
            logger.warning("Conflict resolution timed out, aborting merge")
            await self._abort_merge()
            return False

        await self._apply_resolutions(pending)
        return await self._complete_merge()

    def _parse_conflicts(self, content: str, file_path: str) -> list[ConflictBlock]:
        pattern = re.compile(
            r"<<<<<<< HEAD\n(.*?)=======\n(.*?)>>>>>>> [^\n]+",
            re.DOTALL,
        )
        return [
            ConflictBlock(
                id=uuid.uuid4().hex[:12],
                file_path=file_path,
                block_index=i,
                current=match.group(1),
                incoming=match.group(2),
            )
            for i, match in enumerate(pattern.finditer(content))
        ]

    async def _apply_resolutions(self, pending: PendingMerge):
        by_file: dict[str, list[ConflictBlock]] = {}
        for block in pending.blocks:
            by_file.setdefault(block.file_path, []).append(block)

        pattern = re.compile(
            r"<<<<<<< HEAD\n(.*?)=======\n(.*?)>>>>>>> [^\n]+\n?",
            re.DOTALL,
        )
        for file_path, blocks in by_file.items():
            full_path = Path(self.repo_path) / file_path
            try:
                content = full_path.read_text()
                matches = list(pattern.finditer(content))

                # Apply in reverse order so earlier match positions stay valid
                for i, match in enumerate(reversed(matches)):
                    idx = len(matches) - 1 - i
                    block = blocks[idx]
                    chosen = (
                        block.current
                        if pending.get_resolution(block.id) == "current"
                        else block.incoming
                    )
                    content = content[: match.start()] + chosen + content[match.end() :]

                full_path.write_text(content)

                proc = await asyncio.create_subprocess_exec(
                    "git", "add", file_path,
                    cwd=self.repo_path,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                await proc.communicate()
            except Exception:
                logger.exception("Failed to apply resolution for %s", file_path)

    async def _complete_merge(self) -> bool:
        proc = await asyncio.create_subprocess_exec(
            "git", "commit", "--no-edit",
            cwd=self.repo_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        if proc.returncode != 0:
            err = stderr.decode().strip()
            if "nothing to commit" not in err:
                logger.error("Merge commit failed: %s", err)
                return False
        return True

    async def _abort_merge(self):
        proc = await asyncio.create_subprocess_exec(
            "git", "merge", "--abort",
            cwd=self.repo_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await proc.communicate()

    async def _notify_error(self, message: str, callbacks: list[Callable[[str], Awaitable[None]]]):
        for cb in callbacks:
            try:
                await cb(message)
            except Exception:
                logger.exception("Failed to send git error notification")

    async def push_now(self) -> tuple[bool, str]:
        """Manually trigger a push of any committed but unpushed local commits.

        Pulls first to avoid rejection, then pushes. Does not stage or commit
        anything new — use this to flush commits that stacked up after a
        previous automatic push failure.

        Returns (success, message).
        """
        async with self._lock:
            pull_ok = await self._run_pull()
            if not pull_ok:
                return False, "Git pull failed — cannot push."

            loop = asyncio.get_running_loop()
            try:
                await loop.run_in_executor(None, self._dulwich_push)
            except Exception as e:
                err = str(e)
                logger.error("manual push failed: %s", err)
                return False, f"Push failed: `{err}`"

            logger.info("Manual push complete")
            return True, "Pushed successfully."

    async def _run_git(self, note_count: int, error_callbacks: list[Callable[[str], Awaitable[None]]]):
        msg = f"telegram: add {note_count} note{'s' if note_count != 1 else ''}"
        try:
            # Pull first so our push won't be rejected
            pull_ok = await self._run_pull()
            if not pull_ok:
                await self._notify_error("Git pull failed, skipping push", error_callbacks)
                return

            proc = await asyncio.create_subprocess_exec(
                "git", "add", "-A",
                cwd=self.repo_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await proc.communicate()

            proc = await asyncio.create_subprocess_exec(
                "git", "diff", "--cached", "--quiet",
                cwd=self.repo_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await proc.communicate()
            if proc.returncode == 0:
                logger.info("Nothing to commit, skipping")
                return

            proc = await asyncio.create_subprocess_exec(
                "git", "commit", "-m", msg,
                cwd=self.repo_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()
            if proc.returncode != 0:
                err = stderr.decode().strip()
                logger.error("git commit failed: %s", err)
                await self._notify_error(f"Git sync failed (commit): `{err}`", error_callbacks)
                return

            loop = asyncio.get_running_loop()
            try:
                await loop.run_in_executor(None, self._dulwich_push)
            except Exception as e:
                err = str(e)
                logger.error("git push failed: %s", err)
                await self._notify_error(f"Git sync failed (push): `{err}`", error_callbacks)
                return

            logger.info("Git sync complete: %s", msg)

        except Exception:
            logger.exception("Git sync error")
