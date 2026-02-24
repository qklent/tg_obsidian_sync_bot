import asyncio
import logging
from collections.abc import Awaitable, Callable

logger = logging.getLogger(__name__)


class GitSync:
    def __init__(self, repo_path: str, debounce_seconds: int = 30):
        self.repo_path = repo_path
        self.debounce_seconds = debounce_seconds
        self._dirty = False
        self._lock = asyncio.Lock()
        self._note_count = 0
        self._error_callbacks: list[Callable[[str], Awaitable[None]]] = []

    def mark_dirty(self, on_error: Callable[[str], Awaitable[None]] | None = None):
        self._dirty = True
        self._note_count += 1
        if on_error is not None:
            self._error_callbacks.append(on_error)

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

    async def _notify_error(self, message: str, callbacks: list[Callable[[str], Awaitable[None]]]):
        for cb in callbacks:
            try:
                await cb(message)
            except Exception:
                logger.exception("Failed to send git error notification")

    async def _run_git(self, note_count: int, error_callbacks: list[Callable[[str], Awaitable[None]]]):
        msg = f"telegram: add {note_count} note{'s' if note_count != 1 else ''}"
        try:
            proc = await asyncio.create_subprocess_exec(
                "git",
                "add",
                "-A",
                cwd=self.repo_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await proc.communicate()

            # Check if there's anything to commit
            proc = await asyncio.create_subprocess_exec(
                "git",
                "diff",
                "--cached",
                "--quiet",
                cwd=self.repo_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await proc.communicate()
            if proc.returncode == 0:
                logger.info("Nothing to commit, skipping")
                return

            proc = await asyncio.create_subprocess_exec(
                "git",
                "commit",
                "-m",
                msg,
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

            proc = await asyncio.create_subprocess_exec(
                "git",
                "push",
                cwd=self.repo_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()
            if proc.returncode != 0:
                err = stderr.decode().strip()
                logger.error("git push failed: %s", err)
                await self._notify_error(f"Git sync failed (push): `{err}`", error_callbacks)
                return

            logger.info("Git sync complete: %s", msg)

        except Exception:
            logger.exception("Git sync error")
