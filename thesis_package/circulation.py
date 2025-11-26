"""Derive circulation information (entry, reachable rooms, door graph) for plans."""

from __future__ import annotations

from collections import defaultdict, deque
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from .relations import as_id, normalize_relation_ids

OUTSIDE_ID = "OUT-0000"


def _collect_room_ids(plan: Dict[str, Any]) -> Set[str]:
    inst = plan.get("instances", {})
    rooms = inst.get("room", {}) or {}
    room_ids: Set[str] = set()
    for records in rooms.values():
        for rec in records or []:
            room_id = rec.get("id")
            if isinstance(room_id, str):
                room_ids.add(room_id)
    return room_ids


def _build_adjacency(entry_node: str, passages: Iterable[Dict[str, Any]]) -> Tuple[Dict[str, Set[str]], List[List[str]]]:
    adjacency: Dict[str, Set[str]] = defaultdict(set)
    door_edges: List[List[str]] = []

    for passage in passages:
        door_id = passage.get("door")
        rooms = passage.get("rooms") or []
        if not door_id or not rooms:
            continue

        door_type = passage.get("door_type")
        interior_rooms = [room for room in rooms if isinstance(room, str) and room != OUTSIDE_ID]

        if door_type == "front_door":
            if interior_rooms:
                room_id = interior_rooms[0]
                adjacency[entry_node].add(room_id)
                adjacency[room_id].add(entry_node)
            continue

        if len(interior_rooms) != 2:
            continue

        a, b = interior_rooms
        if a == b:
            continue
        adjacency[a].add(b)
        adjacency[b].add(a)
        door_edges.append([a, b, door_id])

    return adjacency, door_edges


def _bfs_paths(entry_node: str, adjacency: Dict[str, Set[str]]) -> Dict[str, List[str]]:
    paths: Dict[str, List[str]] = {entry_node: [entry_node]}
    queue: deque[str] = deque([entry_node])
    visited: Set[str] = {entry_node}

    while queue:
        node = queue.popleft()
        for neighbor in sorted(adjacency.get(node, ())):
            if neighbor in visited:
                continue
            visited.add(neighbor)
            paths[neighbor] = paths[node] + [neighbor]
            queue.append(neighbor)
    return paths


def build_circulation(plan: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Create a circulation summary from plan instances and relations."""
    structural = plan.get("instances", {}).get("structural", {}) or {}
    front_records = structural.get("front_door") or []

    entry_id: Optional[str] = None
    for record in front_records:
        entry_id = as_id(record)
        if entry_id:
            break
    if not entry_id:
        return None

    entry_node = f"front_door_{entry_id}"

    relations = plan.get("graph", {}).get("relations") or {}
    relations = normalize_relation_ids(relations)
    passages = relations.get("connected_via_door") or []
    if not passages:
        return None

    adjacency, door_edges = _build_adjacency(entry_node, passages)
    if entry_node not in adjacency:
        # No room connects to the front door; nothing useful to report.
        return None

    room_ids = _collect_room_ids(plan)
    paths = _bfs_paths(entry_node, adjacency)

    reachable_rooms: List[str] = []
    reachability_paths: Dict[str, List[str]] = {}
    for node in paths:
        if node == entry_node or node not in room_ids:
            continue
        reachable_rooms.append(node)
        reachability_paths[node] = paths[node]

    result: Dict[str, Any] = {
        "entry_node": entry_node,
        "room_nodes": reachable_rooms,
        "door_edges": door_edges,
        "reachability_paths": reachability_paths,
    }
    return result


__all__ = ["build_circulation"]
