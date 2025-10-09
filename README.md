
# AA3 Package (split from AA_3.ipynb)

## Structure
- `imports.py`    — all imports grouped together so modules can import from here
- `config.py`     — constants, paths, hyperparameters
- `io.py`         — file I/O: read/write json/csv/pickle, loaders/savers
- `geometry.py`   — geometric ops (Shapely/GeoPandas): buffer, union, intersection, splits
- `graph.py`      — NetworkX graph build/analysis of plan relationships
- `visualize.py`  — Matplotlib drawing, labeling, legend handling
- `processing.py` — core pipeline: normalize, extract_*, split_*, pack_* and helpers
- `ml.py`         — any model training/eval code (if present)
- `misc.py`       — leftover cells not matched by heuristics
- `main.py`       — example entry point

## How this was built
The notebook's code cells were scanned and categorized via simple keyword heuristics
(e.g., `plot` → `visualize.py`, `networkx` → `graph.py`, `shapely` → `geometry.py`,
`json/pickle/read_*` → `io.py`, function definitions/pipeline verbs → `processing.py`).
Order inside each file follows the original cell order to preserve behavior.

## Next steps (recommended cleanup)
1. Move **shared imports** from each module back into the module file where they’re used or
   centralize in `imports.py` and import symbols from there (e.g., `from .imports import os, json, nx, plt`).
2. Build a **pipeline** function in `processing.py` (e.g., `run_pipeline(cfg)`).
3. Consolidate duplicate helpers and rename functions to stable, descriptive names.
4. Add **type hints** and docstrings for public functions.
5. Write **unit tests** (e.g., in `tests/`) and a small sample dataset in `data/`.
6. Replace **notebook-only artifacts** (display/inline) with return values logged by `visualize.py`.

## Usage
```bash
# Example usage inside a script or REPL
from aa3_package import processing, config
# processing.run_pipeline(config)
```
