"""Input/output helpers for Geometry-to-Ontology pipelines."""

from __future__ import annotations

import json
import pickle
from pathlib import Path
from typing import Any, Iterable

from .config import JSON_DIR, PKL_PATH, PLOT_DIR, PLOT_LABEL_DIR


def load_data(path: str | Path = PKL_PATH) -> Any:
    """Load the raw ResPlan pickle dataset."""
    path = Path(path)
    with path.open("rb") as fh:
        return pickle.load(fh)


def save_json(data: Any, path: str | Path) -> Path:
    """Write JSON data with UTF-8 encoding and pretty formatting."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
    return path


def ensure_output_dirs(extra: Iterable[Path] | None = None) -> None:
    """Make sure standard output directories exist before writing artefacts."""
    directories = [JSON_DIR, PLOT_DIR, PLOT_LABEL_DIR]
    if extra:
        directories.extend(Path(d) for d in extra)
    for directory in directories:
        directory.mkdir(parents=True, exist_ok=True)


__all__ = ["load_data", "save_json", "ensure_output_dirs"]
