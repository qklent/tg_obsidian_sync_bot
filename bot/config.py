import os
import re
from pathlib import Path

import yaml


CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"


def _resolve_env_vars(value: str) -> str:
    """Replace ${ENV_VAR} placeholders with actual environment variable values."""

    def replacer(match: re.Match) -> str:
        var_name = match.group(1)
        env_val = os.environ.get(var_name)
        if env_val is None:
            raise ValueError(f"Environment variable {var_name} is not set")
        return env_val

    return re.sub(r"\$\{(\w+)\}", replacer, value)


def _resolve_recursive(obj):
    """Walk a nested dict/list and resolve env vars in all string values."""
    if isinstance(obj, str):
        return _resolve_env_vars(obj)
    if isinstance(obj, dict):
        return {k: _resolve_recursive(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_resolve_recursive(item) for item in obj]
    return obj


def load_settings(path: Path | None = None) -> dict:
    path = path or CONFIG_DIR / "settings.yaml"
    with open(path) as f:
        raw = yaml.safe_load(f)
    return _resolve_recursive(raw)


def load_vault_structure(path: Path | None = None) -> dict:
    path = path or CONFIG_DIR / "vault_structure.yaml"
    with open(path) as f:
        return yaml.safe_load(f)
