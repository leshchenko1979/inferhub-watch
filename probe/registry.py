from __future__ import annotations

import importlib.util
import json
import os
import tomllib
from pathlib import Path
from types import ModuleType


def repo_root() -> Path:
    return Path(__file__).resolve().parent.parent

def atomic_write_text(path: Path, text: str) -> None:
    """Write text via tmp-file + rename: a reader never sees a half-written
    JSON file (the 02:00Z cron regenerates data while pages are being read).
    Parent dirs are created as needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text)
    os.replace(tmp, path)


# mtime-keyed content cache: key = (path, mtime_ns, size), so a file
# rewritten in the same process gets a fresh read and a stable file is
# parsed once. Only for read-only payloads — callers that mutate what
# they load must bypass it (see market.load_proven / record_proven).
_CONTENT_CACHE: dict[tuple[str, int, int], object] = {}

def _cached_read(path: Path, fallback: object, parse):
    """Parse `path` once per (path, mtime, size); `fallback` when absent/bad."""
    try:
        st = path.stat()
    except OSError:
        return fallback
    key = (str(path), st.st_mtime_ns, st.st_size)
    hit = _CONTENT_CACHE.get(key)
    if hit is None:
        try:
            hit = parse(path.read_text())
        except (OSError, ValueError):
            hit = fallback
        _CONTENT_CACHE[key] = hit
    return hit


def _load_models_toml() -> dict:
    return _cached_read(repo_root() / "models.toml", {}, tomllib.loads)


def load_aliases() -> list[str]:
    data = _load_models_toml()
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

def intelligence_models(root: Path | None = None) -> dict:
    """data/intelligence.json 'models' dict ({slug: {iq, ...}}), or {}.

    Cached per (path, mtime, size); `root` is injectable for tests.
    """
    path = (root or repo_root()) / "data" / "intelligence.json"
    payload = _cached_read(path, None, json.loads)
    if not isinstance(payload, dict):
        return {}
    models = payload.get("models")
    return models if isinstance(models, dict) else {}

def aa_slug(route: str) -> str | None:
    """Route -> AA slug: models.toml [aa] first, else derive from the model name.

    Derived form: normalize dots in the model part to dashes and match it
    against data/intelligence.json's known slugs, so candidate routes get IQ
    data without a hand-edited mapping (cbcn/glm-5.3-flash -> glm-5-3-flash).
    Single copy — site/rundata.aa_slug delegates here.
    """
    mapped = (_load_models_toml().get("aa") or {}).get(route)
    if mapped:
        return mapped
    candidate = route.split("/", 1)[-1].lower().replace(".", "-")
    if candidate in intelligence_models():
        return candidate
    return None
