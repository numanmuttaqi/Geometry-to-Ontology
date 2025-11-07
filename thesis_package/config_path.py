from __future__ import annotations

import json
import os
from pathlib import Path

# Locate repo root 
ROOT = Path(__file__).resolve().parent.parent

# Old layout (kept)
DATA        = ROOT / "data"
OUTPUT      = ROOT / "output"
PLOT_DIR    = OUTPUT / "resplan_plot"
PLOT_LABEL_DIR = OUTPUT / "resplan_plotlabel"
JSON_DIR    = OUTPUT / "resplan_json"
PKL_PATH    = DATA / "ResPlan.pkl"

NEW_INPUT   = DATA / "input"
NEW_DERIVED = DATA / "derived"
NEW_OUT     = DATA / "out"
ONTOLOGY    = ROOT / "ontology"
RULES       = ROOT / "rules"


# Optional settings.json override (kept)
settings_path = ROOT / "settings.json"
if settings_path.exists():
    settings = json.loads(settings_path.read_text())
    for key, value in settings.get("path", {}).items():
        globals()[key.upper()] = Path(value)

# Environment overrides (CI friendly)
NEW_INPUT   = Path(os.getenv("RESPLAN_INPUT", NEW_INPUT))
NEW_DERIVED = Path(os.getenv("RESPLAN_DERIVED", NEW_DERIVED))
NEW_OUT     = Path(os.getenv("RESPLAN_OUT", NEW_OUT))


# Ensure directories exist
for path in (
    OUTPUT,
    PLOT_DIR,
    PLOT_LABEL_DIR,
    JSON_DIR,
    NEW_INPUT,
    NEW_DERIVED,
    NEW_OUT,
    ONTOLOGY,
    RULES,
):
    path.mkdir(parents=True, exist_ok=True)