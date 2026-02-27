import hashlib
import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Awaitable

import faiss
import numpy as np
from openai import AsyncOpenAI

logger = logging.getLogger(__name__)

_SKIP_DIRS = {".obsidian", ".tg_sync_cache", ".git", "images"}
_FRONTMATTER_RE = re.compile(r"^---\s*\n.*?\n---\s*\n", re.DOTALL)
_BATCH_SIZE = 50
_PROGRESS_INTERVAL = 10


@dataclass
class NoteInfo:
    path: Path
    content: str
    content_hash: str


@dataclass
class DuplicatePair:
    path_a: str
    path_b: str
    similarity: float
    preview_a: str
    preview_b: str


class Deduplicator:
    def __init__(
        self,
        vault_path: str,
        openai_client: AsyncOpenAI,
        model: str,
        cache_path: str,
        similarity_threshold: float = 0.90,
    ):
        self.vault_path = Path(vault_path)
        self.client = openai_client
        self.model = model
        self.cache_path = Path(cache_path)
        self.similarity_threshold = similarity_threshold
        self._cache: dict[str, list[float]] = {}
        self._load_cache()

    def _load_cache(self):
        if self.cache_path.is_file():
            try:
                self._cache = json.loads(self.cache_path.read_text())
            except (json.JSONDecodeError, OSError):
                logger.warning("Corrupt embedding cache, starting fresh")
                self._cache = {}

    def _save_cache(self):
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        self.cache_path.write_text(json.dumps(self._cache))

    def _collect_notes(self) -> list[NoteInfo]:
        notes = []
        for md_path in self.vault_path.rglob("*.md"):
            rel_parts = md_path.relative_to(self.vault_path).parts
            if any(part in _SKIP_DIRS for part in rel_parts):
                continue
            try:
                raw = md_path.read_text(encoding="utf-8")
            except OSError:
                continue
            body = _strip_frontmatter(raw)
            if not body.strip():
                continue
            content_hash = hashlib.md5(body.encode()).hexdigest()
            notes.append(NoteInfo(path=md_path, content=body, content_hash=content_hash))
        return notes

    async def _update_embeddings(
        self,
        notes: list[NoteInfo],
        progress_cb: Callable[[int, int], Awaitable[None]] | None,
    ):
        new_notes = [n for n in notes if n.content_hash not in self._cache]
        total = len(new_notes)
        if total == 0:
            return

        done = 0
        for i in range(0, total, _BATCH_SIZE):
            batch = new_notes[i : i + _BATCH_SIZE]
            texts = [n.content[:8000] for n in batch]  # truncate to stay within token limits
            try:
                response = await self.client.embeddings.create(
                    model=self.model, input=texts
                )
                for note, emb_data in zip(batch, response.data):
                    self._cache[note.content_hash] = emb_data.embedding
            except Exception:
                logger.exception("Embedding API call failed for batch %d", i)
                raise

            done += len(batch)
            if progress_cb and (done % _PROGRESS_INTERVAL < _BATCH_SIZE or done == total):
                await progress_cb(done, total)

        # Prune stale cache entries
        live_hashes = {n.content_hash for n in notes}
        stale = [h for h in self._cache if h not in live_hashes]
        for h in stale:
            del self._cache[h]

        self._save_cache()

    def _find_duplicates(self, notes: list[NoteInfo]) -> list[DuplicatePair]:
        if len(notes) < 2:
            return []

        # Build embedding matrix
        hashes = [n.content_hash for n in notes]
        embeddings = []
        valid_indices = []
        for i, h in enumerate(hashes):
            if h in self._cache:
                embeddings.append(self._cache[h])
                valid_indices.append(i)

        if len(valid_indices) < 2:
            return []

        matrix = np.array(embeddings, dtype=np.float32)
        faiss.normalize_L2(matrix)

        k = min(10, len(valid_indices))
        index = faiss.IndexFlatIP(matrix.shape[1])
        index.add(matrix)
        similarities, indices = index.search(matrix, k)

        seen_pairs: set[tuple[str, str]] = set()
        pairs: list[DuplicatePair] = []

        for row_idx in range(len(valid_indices)):
            note_i = notes[valid_indices[row_idx]]
            for col_idx in range(k):
                neighbor = indices[row_idx][col_idx]
                sim = float(similarities[row_idx][col_idx])

                if neighbor == row_idx or sim < self.similarity_threshold:
                    continue

                note_j = notes[valid_indices[neighbor]]
                pair_key = tuple(sorted([str(note_i.path), str(note_j.path)]))
                if pair_key in seen_pairs:
                    continue
                seen_pairs.add(pair_key)

                pairs.append(DuplicatePair(
                    path_a=str(note_i.path),
                    path_b=str(note_j.path),
                    similarity=sim,
                    preview_a=note_i.content[:300],
                    preview_b=note_j.content[:300],
                ))

        pairs.sort(key=lambda p: p.similarity, reverse=True)
        return pairs

    async def scan(
        self, progress_cb: Callable[[int, int], Awaitable[None]] | None = None
    ) -> list[DuplicatePair]:
        notes = self._collect_notes()
        logger.info("Collected %d notes for dedup scan", len(notes))
        await self._update_embeddings(notes, progress_cb)
        return self._find_duplicates(notes)

    def delete_note(self, path: str):
        p = Path(path)
        if p.exists():
            p.unlink()
            logger.info("Deleted duplicate note: %s", path)


def _strip_frontmatter(text: str) -> str:
    return _FRONTMATTER_RE.sub("", text, count=1)
