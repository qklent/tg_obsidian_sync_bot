import asyncio
import logging

logger = logging.getLogger(__name__)


class GitSync:
    def __init__(self, repo_path: str, debounce_seconds: int = 30):
        self.repo_path = repo_path
        self.debounce_seconds = debounce_seconds
        self._dirty = False
        self._lock = asyncio.Lock()
        self._note_count = 0

    def mark_dirty(self):
        self._dirty = True
        self._note_count += 1

    async def sync_loop(self):
        """Run as background task. Periodically commit+push if dirty."""
        while True:
            await asyncio.sleep(self.debounce_seconds)
            if self._dirty:
                async with self._lock:
                    if not self._dirty:
                        continue
                    count = self._note_count
                    self._dirty = False
                    self._note_count = 0
                    await self._run_git(count)

    async def _run_git(self, note_count: int):
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
                logger.error("git commit failed: %s", stderr.decode())
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
                logger.error("git push failed: %s", stderr.decode())
                return

            logger.info("Git sync complete: %s", msg)

        except Exception:
            logger.exception("Git sync error")
