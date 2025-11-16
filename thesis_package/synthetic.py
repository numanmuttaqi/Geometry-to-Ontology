"""Utilities for creating imperfect variants of exported floor plans."""

from __future__ import annotations

import copy
import json
import random
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from .circulation import build_circulation

STRUCT_CATEGORIES = ("interior_wall","exterior_wall", "door", "window", "front_door")


def load_plan(path: Path) -> Dict:
    """Load a JSON plan from disk."""
    with Path(path).open("r", encoding="utf-8") as handle:
        return json.load(handle)


def save_plan(plan: Dict, path: Path) -> None:
    """Save a JSON plan to disk."""
    Path(path).write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")


def generate_variants(plan: Mapping, *, rng: Optional[random.Random] = None) -> Dict[str, Dict]:
    """
    Return four imperfect variants of the given plan.

    Variants:
      - drop_interior_wall : remove 1-3 interior walls
      - drop_door          : remove 1-3 doors
      - drop_window        : remove 1-3 windows
      - drop_all_structural: remove 1-2 items from each structural category
    """
    rng = rng or random.Random()
    return {
        "drop_interior_wall": remove_structural_elements(
            plan, {"interior_wall": (1, 3)}, rng=rng
        ),
        "drop_door": remove_structural_elements(
            plan, {"door": (1, 3)}, rng=rng
        ),
        "drop_window": remove_structural_elements(
            plan, {"window": (1, 3)}, rng=rng
        ),
        "drop_all_structural": remove_structural_elements(
            plan,
            {
                cat: (1, 2)
                for cat in STRUCT_CATEGORIES
                if cat != "exterior_wall"
            },
            rng=rng,
        ),
    }


def remove_structural_elements(
    plan: Mapping,
    removal_spec: Mapping[str, Tuple[int, int]],
    *,
    rng: Optional[random.Random] = None,
) -> Dict:
    """
    Return a new plan where structural elements in `removal_spec` have been dropped.

    removal_spec example:
        {"interior_wall": (1, 3), "door": (2, 4)}
    """
    rng = rng or random.Random()
    mutated = copy.deepcopy(plan)

    removed_ids: Dict[str, List[str]] = {}
    structural = mutated.get("instances", {}).get("structural", {})

    for category, (min_count, max_count) in removal_spec.items():
        items = structural.get(category) or []
        ids = _choose_ids_to_drop(items, min_count, max_count, rng)
        if not ids:
            continue
        removed_ids[category] = ids
        structural[category] = [inst for inst in items if inst.get("id") not in ids]

    if not removed_ids:
        return mutated

    _purge_graph(mutated, removed_ids)
    _rebuild_circulation(mutated)
    _update_summary_counts(mutated)
    return mutated


def _choose_ids_to_drop(
    items: Sequence[Mapping],
    min_count: int,
    max_count: int,
    rng: random.Random,
    *,
    min_remaining: int = 3, # ensure at least this many remain
) -> List[str]:
    if not items:
        return []
    min_count = max(0, min_count)
    max_count = max(min_count, max_count)
    available = [item.get("id") for item in items if isinstance(item, Mapping)]
    available = [item_id for item_id in available if item_id]
    if not available:
        return []

    total = len(available)
    if total <= min_remaining:
        return []

    max_drop = min(max_count, total - min_remaining)
    if max_drop <= 0:
        return []
    min_drop = min(min_count, max_drop)
    if min_drop > max_drop:
        min_drop = max_drop
    if min_drop <= 0:
        drop_count = max_drop
    else:
        drop_count = rng.randint(min_drop, max_drop)
    if drop_count <= 0:
        return []
    drop_count = min(drop_count, total - min_remaining)
    if drop_count <= 0:
        return []

    return rng.sample(available, drop_count) if drop_count else []


def _purge_graph(plan: Mapping, removed: Mapping[str, Iterable[str]]) -> None:
    graph = plan.get("graph") or {}
    relations = graph.get("relations") or {}

    removed_walls = set(removed.get("interior_wall", []))
    removed_walls.update(removed.get("exterior_wall", []))
    removed_doors = set(removed.get("door", []))
    removed_windows = set(removed.get("window", []))

    # graph.edges
    edges = graph.get("edges") or []
    graph["edges"] = [
        edge
        for edge in edges
        if not _edge_uses_removed(edge, removed_walls, removed_doors, removed_windows)
    ]

    # bounded_by
    bounded = relations.get("bounded_by")
    if isinstance(bounded, dict):
        edges_list = bounded.get("edges", [])
        new_edges = [
            e for e in edges_list if e.get("wall") not in removed_walls
        ]
        bounded["edges"] = new_edges
        bounded["per_room"] = _rebuild_bounded_by_per_room(new_edges)
    elif isinstance(bounded, list):
        relations["bounded_by"] = [
            e for e in bounded if e.get("wall") not in removed_walls
        ]

    # hosts_opening
    hosts = relations.get("hosts_opening") or []
    relations["hosts_opening"] = [
        e
        for e in hosts
        if e.get("opening") not in removed_doors | removed_windows
        and e.get("wall") not in removed_walls
    ]

    # connected_via_door
    connections = relations.get("connected_via_door") or []
    relations["connected_via_door"] = [
        e for e in connections if e.get("door") not in removed_doors
    ]

    # Update relation summary in metadata if present
    summary = plan.get("metadata", {}).get("summary", {})
    if isinstance(summary, dict):
        rel_summary = summary.get("relationship_summary")
        if isinstance(rel_summary, dict):
            rel_summary["total_relationships"] = len(graph.get("edges", []))
            rel_summary["bounded_by_count"] = len(
                bounded.get("edges", []) if isinstance(bounded, dict) else relations.get("bounded_by", [])
            )
            rel_summary["hosts_opening_count"] = len(relations.get("hosts_opening", []))
            rel_summary["door_connections"] = len(relations.get("connected_via_door", []))


def _edge_uses_removed(edge: Mapping, walls: set, doors: set, windows: set) -> bool:
    if edge.get("type") == "bounded_by":
        return edge.get("target") in walls
    if edge.get("type") == "connected_via_door":
        return edge.get("properties", {}).get("door") in doors
    if edge.get("type") == "hosts_opening":
        opening = edge.get("target")
        return opening in doors or opening in windows
    return False


def _rebuild_bounded_by_per_room(edges: Iterable[Mapping]) -> List[Dict]:
    per_room: Dict[str, Dict[str, float]] = {}
    for edge in edges:
        room_id = edge.get("room")
        wall_id = edge.get("wall")
        length = edge.get("length_m") or edge.get("length") or 0.0
        if not room_id or not wall_id:
            continue
        room_data = per_room.setdefault(room_id, {"walls": [], "lengths": []})
        room_data["walls"].append(wall_id)
        room_data["lengths"].append(float(length))

    result = []
    for room_id, data in per_room.items():
        result.append(
            {
                "room": room_id,
                "walls": sorted(set(data["walls"])),
                "length_total": round(sum(data["lengths"]), 2),
            }
        )
    return result


def _update_summary_counts(plan: Mapping) -> None:
    structural = plan.get("instances", {}).get("structural", {})
    summary = plan.get("metadata", {}).get("summary")
    if not isinstance(summary, dict):
        return
    counts = {cat: len(structural.get(cat, []) or []) for cat in STRUCT_CATEGORIES}
    summary["structural_counts"] = counts


def _rebuild_circulation(plan: Mapping) -> None:
    if not isinstance(plan, dict):
        return
    circulation = build_circulation(plan)
    if isinstance(circulation, dict) and circulation:
        plan["circulation"] = circulation
    else:
        plan.pop("circulation", None)
