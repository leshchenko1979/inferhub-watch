from __future__ import annotations

import importlib.util
import tomllib
from pathlib import Path
from types import ModuleType


def repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def load_aliases() -> list[str]:
    data = tomllib.loads((repo_root() / "models.toml").read_text())
    return list(data["aliases"])


def load_candidates(path: Path | None = None) -> list[dict]:
    """Candidate groups [{model, routes}] from candidates.toml.

    Missing or empty file → [] (board-only run). Groups without a model or
    without usable routes are skipped.
    """
    path = path or repo_root() / "candidates.toml"
    try:
        data = tomllib.loads(path.read_text())
    except (OSError, ValueError):
        return []
    groups = []
    for entry in data.get("candidate") or []:
        model = str(entry.get("model") or "").strip()
        routes = [str(r).strip() for r in entry.get("routes") or [] if str(r).strip()]
        if model and routes:
            groups.append({"model": model, "routes": routes})
    return groups


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
