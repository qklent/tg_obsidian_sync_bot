import json

from loguru import logger
from openai import AsyncOpenAI


def _folders_to_yaml(folders: list[dict], indent: int = 0) -> str:
    """Convert the folder tree into a readable YAML-like string for the prompt."""
    lines = []
    for folder in folders:
        prefix = "  " * indent
        lines.append(f"{prefix}- {folder['path']}: {folder.get('description', '')}")
        if "children" in folder:
            lines.extend(_folders_to_yaml(folder["children"], indent + 1).splitlines())
    return "\n".join(lines)


def build_prompt(message_text: str, vault_structure: dict) -> str:
    folders_yaml = _folders_to_yaml(vault_structure["folders"])
    tags_csv = ", ".join(vault_structure.get("tags", []))

    return f"""You are a note classifier for an Obsidian vault.

FOLDERS (with descriptions):
{folders_yaml}

AVAILABLE TAGS:
{tags_csv}

Given the user's message below, respond with ONLY valid JSON:
{{
  "folder": "exact/folder/path from the list above",
  "filename": "short-kebab-case-name",
  "tags": ["tag1", "tag2"],
  "title": "Human readable title",
  "content": "cleaned up / formatted version of the message in markdown"
}}

Rules:
- If the message doesn't fit any folder, use "inbox"
- filename must be filesystem-safe, kebab-case, max 60 chars
- Pick 1-4 tags that are most relevant
- content: preserve the original meaning, fix formatting, add markdown structure if appropriate
- If the message is a forwarded post or article, add a "source" line at the top of content
- If the message contains fetched link content, use it to create a meaningful title and summary. Include a brief summary of the linked content in the note.
- If folder is "tg_sync_bot", also include these additional fields in the JSON:
    "status": one of "planning" | "in_progress" | "done" | "blocked"
    "priority": one of "low" | "medium" | "high"
    "clarification_needed": true or false
- Respond ONLY with the JSON object, no other text

USER MESSAGE:
{message_text}"""


class LLMClassifier:
    def __init__(self, api_key: str, model: str):
        self.client = AsyncOpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key=api_key,
        )
        self.model = model

    async def classify(self, message_text: str, vault_structure: dict) -> dict:
        prompt = build_prompt(message_text, vault_structure)

        response = await self.client.chat.completions.create(
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
        )

        if response.usage:
            logger.bind(
                tokens_prompt=response.usage.prompt_tokens,
                tokens_completion=response.usage.completion_tokens,
                tokens_total=response.usage.total_tokens,
            ).info("llm_call")

        raw = response.choices[0].message.content.strip()

        # Strip markdown code fences if the model wraps its response
        if raw.startswith("```"):
            # Remove opening fence (```json or ```)
            raw = raw.split("\n", 1)[1] if "\n" in raw else raw[3:]
            if raw.endswith("```"):
                raw = raw[:-3]
            raw = raw.strip()

        try:
            result = json.loads(raw)
        except json.JSONDecodeError:
            logger.error("LLM returned invalid JSON: %s", raw)
            raise

        # Validate required fields
        for key in ("folder", "filename", "tags", "title", "content"):
            if key not in result:
                raise ValueError(f"LLM response missing required field: {key}")

        return result

    async def query(self, question: str, context_notes: list[tuple[str, str]]) -> str:
        """Answer a question using relevant notes as context.

        Args:
            question: The user's question.
            context_notes: List of (relative_path, body_text) for relevant notes.

        Returns:
            The LLM's synthesized answer.
        """
        notes_block = "\n\n".join(
            f"--- {path} ---\n{body[:3000]}"
            for path, body in context_notes
        )

        prompt = f"""You are a knowledge assistant for an Obsidian vault.
Answer the user's question based ONLY on the notes provided below.
If the notes don't contain enough information, say so.
Be concise. Use markdown formatting.

NOTES:
{notes_block}

QUESTION:
{question}"""

        response = await self.client.chat.completions.create(
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
        )

        if response.usage:
            logger.bind(
                tokens_prompt=response.usage.prompt_tokens,
                tokens_completion=response.usage.completion_tokens,
            ).info("llm_query_call")

        return response.choices[0].message.content.strip()

    async def suggest_wikilinks(
        self,
        new_note_title: str,
        new_note_content: str,
        related_notes: list[tuple[str, str]],
    ) -> list[str]:
        """Suggest existing note filenames to link from the new note.

        Args:
            new_note_title: Title of the newly created note.
            new_note_content: Body of the new note.
            related_notes: List of (filename_stem, body_text) for candidate notes.

        Returns:
            List of filename stems that should be linked.
        """
        if not related_notes:
            return []

        candidates = "\n".join(
            f"- {stem}: {body[:200]}" for stem, body in related_notes
        )

        prompt = f"""You are a knowledge-linking assistant for an Obsidian vault.

Given a NEW note and a list of EXISTING notes, pick which existing notes are genuinely related and should be cross-referenced.

NEW NOTE TITLE: {new_note_title}
NEW NOTE CONTENT:
{new_note_content[:2000]}

CANDIDATE NOTES:
{candidates}

Return ONLY a JSON array of filenames (stems) that are truly related. Example: ["note-one", "note-two"]
If none are related, return an empty array: []
Be selective — only pick notes with a clear topical connection."""

        response = await self.client.chat.completions.create(
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
        )

        raw = response.choices[0].message.content.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1] if "\n" in raw else raw[3:]
            if raw.endswith("```"):
                raw = raw[:-3]
            raw = raw.strip()

        try:
            result = json.loads(raw)
            if isinstance(result, list):
                return [str(s) for s in result]
        except json.JSONDecodeError:
            logger.warning("LLM returned invalid JSON for wikilinks: %s", raw)

        return []
