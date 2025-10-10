"""Project-level configuration and high-level export helpers."""

from __future__ import annotations

import json
import pickle
from pathlib import Path
from typing import Any, Dict

import matplotlib.pyplot as plt
import resplan_utils as R

from .constants import ROOM_KEYS, STRUCT_KEYS

# --- directories and canonical paths ---
ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
OUTPUT = ROOT / "output"
PLOT_DIR = OUTPUT / "resplan_plot"
PLOT_LABEL_DIR = OUTPUT / "resplan_plotlabel"
JSON_DIR = OUTPUT / "resplan_json"
PKL_PATH = DATA / "ResPlan.pkl"

for directory in (OUTPUT, PLOT_DIR, PLOT_LABEL_DIR, JSON_DIR):
    directory.mkdir(parents=True, exist_ok=True)


def assemble_json(plan: Dict[str, Any], idx: int, json_path: Path, plot_path: Path) -> Dict[str, Any]:
    """Assemble the enriched JSON artefact for a single plan."""
    from .plan_utils import extract_layers, extract_metadata, extract_room_instances, split_walls
    from .graph import export_graph, relabel_rooms_with_subtype_prefixes_inplace

    normalized = R.normalize_keys(plan.copy())
    rooms = extract_room_instances(normalized)
    structural = split_walls(normalized)
    layers = extract_layers(normalized)
    metadata = extract_metadata(
        normalized,
        plan_idx=idx,
        json_relpath=str(json_path),
        plot_relpath=str(plot_path),
    )

    temp_plan = {"instances": {"room": rooms}, "graph": {"relations": {}}}
    relabel_rooms_with_subtype_prefixes_inplace(temp_plan)
    rooms = temp_plan["instances"]["room"]

    graph = export_graph(normalized, rooms, structural)

    room_counts = {key: len(rooms[key]) for key in ROOM_KEYS}
    struct_counts = {key: len(structural[key]) for key in STRUCT_KEYS}
    relationship_summary = {
        "total_relationships": len(graph["edges"]),
        "adjacency_count": sum(1 for edge in graph["edges"] if edge["type"] == "adjacent"),
        "door_connections": sum(1 for edge in graph["edges"] if edge["type"] == "connected_via_door"),
        "bounded_by_count": sum(1 for edge in graph["edges"] if edge["type"] == "bounded_by"),
        "hosts_opening_count": sum(1 for edge in graph["edges"] if edge["type"] == "hosts_opening"),
    }

    summary = metadata.setdefault("summary", {})
    summary.update(
        {
            "rooms_total": sum(room_counts.values()),
            "room_counts": room_counts,
            "structural_counts": struct_counts,
            "relationship_summary": relationship_summary,
        }
    )

    return {
        "metadata": metadata,
        "instances": {"room": rooms, "structural": structural},
        "geom": layers,
        "graph": graph,
    }


def export_one(idx: int, plan: Dict[str, Any]) -> Path:
    """Export a plan to JSON and companion plot, returning the JSON path."""
    json_path = JSON_DIR / f"plan_{idx:05d}.json"
    plot_path = PLOT_DIR / f"plan_{idx:05d}.png"

    artefact = assemble_json(plan, idx, json_path, plot_path)
    from .graph import relabel_rooms_with_subtype_prefixes_inplace

    relabel_rooms_with_subtype_prefixes_inplace(artefact)

    with json_path.open("w", encoding="utf-8") as fh:
        json.dump(artefact, fh, ensure_ascii=False, indent=2)

    axis = R.plot_plan(plan, title=f"Plan #{idx}")
    figure = axis.get_figure()
    figure.savefig(plot_path, bbox_inches="tight", dpi=150)
    plt.close(figure)
    return json_path


if __name__ == "__main__":
    from .visualize import plot_plan_json

    with PKL_PATH.open("rb") as fh:
        plans = pickle.load(fh)

    print(f"Total plans: {len(plans)}")
    failed_indices = []

    for idx, plan_raw in enumerate(plans):
        try:
            plan = R.normalize_keys(plan_raw.copy())
            json_path = export_one(idx, plan)

            labeled_plot_dir = PLOT_LABEL_DIR
            labeled_plot_path = labeled_plot_dir / f"plan_{idx:05d}_ids.png"

            axis = plot_plan_json(json_path, show_ids=True)
            figure = axis.get_figure() if hasattr(axis, "get_figure") else plt.gcf()
            figure.savefig(labeled_plot_path, dpi=200, bbox_inches="tight")
            plt.close(figure)
        except Exception as exc:  # pragma: no cover - diagnostic path
            failed_indices.append(idx)
            print(f"\nFailed at index {idx}: {exc}")

    if failed_indices:
        print(f"\nFailed indices: {failed_indices}")
