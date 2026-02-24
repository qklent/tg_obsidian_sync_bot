import json
import logging

import yaml
from openai import AsyncOpenAI

logger = logging.getLogger(__name__)


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
