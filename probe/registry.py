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
