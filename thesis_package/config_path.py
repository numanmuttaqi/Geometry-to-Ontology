from __future__ import annotations

import json
from pathlib import Path

# Locate repo root 
ROOT = Path(__file__).resolve().parent.parent

# Old layout (kept)
DATA            = ROOT / "data"
OUTPUT          = ROOT / "output"
PLOT_DIR        = OUTPUT / "resplan_plot"
PLOT_LABEL_DIR  = OUTPUT / "resplan_plotlabel"
JSON_DIR        = OUTPUT / "resplan_json"
PKL_PATH        = DATA / "ResPlan.pkl"
ONTOLOGY        = ROOT / "ontology"


# Optional settings.json override (kept)
settings_path = ROOT / "settings.json"
if settings_path.exists():
    settings = json.loads(settings_path.read_text())
    for key, value in settings.get("path", {}).items():
        globals()[key.upper()] = Path(value)

# Ensure directories exist
for path in (
    OUTPUT,
    PLOT_DIR,
    PLOT_LABEL_DIR,
    JSON_DIR,
    ONTOLOGY,
):
    path.mkdir(parents=True, exist_ok=True)
