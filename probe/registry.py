from __future__ import annotations

import importlib.util
import json
import tomllib
from pathlib import Path
from types import ModuleType


def repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def load_aliases() -> list[str]:
    data = tomllib.loads((repo_root() / "models.toml").read_text())
    return list(data["aliases"])


def load_registry() -> list[dict]:
    data = tomllib.loads((repo_root() / "checks" / "registry.toml").read_text())
    return list(data["checks"])


def load_check_module(check_id: str) -> ModuleType:
    path = repo_root() / "checks" / check_id / "check.py"
    spec = importlib.util.spec_from_file_location(f"checks.{check_id}", path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

def intelligence_models() -> dict:
    """data/intelligence.json 'models' dict ({slug: {iq, ...}}), or {}."""
    try:
        payload = json.loads((repo_root() / "data" / "intelligence.json").read_text())
    except (OSError, ValueError):
        return {}
    models = payload.get("models") if isinstance(payload, dict) else None
    return models if isinstance(models, dict) else {}

def aa_slug(route: str) -> str | None:
    """Route -> AA slug: models.toml [aa] first, else derive from the model name.

    Derived form: normalize dots in the model part to dashes and match it
    against data/intelligence.json's known slugs, so candidate routes get IQ
    data without a hand-edited mapping (cbcn/glm-5.3-flash -> glm-5-3-flash).
    Single copy — site/rundata.aa_slug delegates here.
    """
    data = tomllib.loads((repo_root() / "models.toml").read_text())
    mapped = (data.get("aa") or {}).get(route)
    if mapped:
        return mapped
    candidate = route.split("/", 1)[-1].lower().replace(".", "-")
    if candidate in intelligence_models():
        return candidate
    return None
