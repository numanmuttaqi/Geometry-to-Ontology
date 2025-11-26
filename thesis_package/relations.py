"""Utilities for normalising and deriving plan relationship data."""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Dict, Iterable, List, Optional

from shapely.geometry import shape

import resplan_utils as R

def get_relations_dict(plan: Dict[str, Any], *, create: bool = False, promote: bool = True) -> Dict[str, Any]:
    """
    Return the relations dictionary stored on the plan, preferring the top-level key.

    When `promote` is True (default), any legacy storage under ``plan["graph"]["relations"]``
    gets moved to the top level to keep the JSON structure consistent. When `create`
    is True, the function ensures that ``plan["relations"]`` exists and is a dict.
    """
    if not isinstance(plan, dict):
        return {}

    relations = plan.get("relations")
    if isinstance(relations, dict):
        return relations

    graph = plan.get("graph")
    nested: Optional[Dict[str, Any]] = None
    if isinstance(graph, dict):
        candidate = graph.get("relations")
        if isinstance(candidate, dict):
            nested = candidate

    if nested is not None:
        if promote or create:
            plan["relations"] = nested
            if isinstance(graph, dict):
                graph.pop("relations", None)
        return nested

    if create:
        relations = {}
        plan["relations"] = relations
        if isinstance(graph, dict):
            graph.pop("relations", None)
        return relations

    return {}


def as_id(value: Any) -> Optional[str]:
    """Return an identifier string from a dict/list entry."""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return value.get("id")
    return None


def geometry_from_record(record: Any):
    """Best-effort conversion of a record to a shapely geometry."""
    geom = None
    if isinstance(record, dict):
        geom = record.get("geom") or record.get("geometry")
        if isinstance(geom, dict) and "type" in geom:
            return shape(geom)
    try:
        return R.to_shape(record)
    except Exception:
        return None


def _normalize_entries(entries: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    clean_entries: List[Dict[str, Any]] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        normalized_entry: Dict[str, Any] = {}
        for slot, value in entry.items():
            if slot in {"room", "wall", "opening", "from", "to", "door", "through_wall"}:
                identifier = as_id(value)
                if identifier is not None:
                    normalized_entry[slot] = identifier
            else:
                normalized_entry[slot] = value
        clean_entries.append(normalized_entry)
    return clean_entries


def normalize_relation_ids(relations: Dict[str, Any]) -> Dict[str, Any]:
    """Replace nested dict entries with their identifier values."""
    normalized: Dict[str, Any] = {}
    for key, entries in (relations or {}).items():
        if isinstance(entries, dict) and "edges" in entries:
            normalized[key] = dict(entries)
            normalized[key]["edges"] = _normalize_entries(entries.get("edges", []))
            continue
        if not isinstance(entries, list):
            normalized[key] = entries
            continue
        normalized[key] = _normalize_entries(entries)
    return normalized


def bounded_by_per_room(relations: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Summaries of bounded_by edges per room."""
    accumulator: Dict[str, Dict[str, Any]] = defaultdict(lambda: {"walls": set(), "by_wall_len": defaultdict(float)})

    for edge in relations.get("bounded_by", []):
        room_id = edge.get("room")
        wall_id = edge.get("wall")
        length = float(edge.get("length", 0.0) or 0.0)
        if not room_id or not wall_id:
            continue
        accumulator[room_id]["walls"].add(wall_id)
        accumulator[room_id]["by_wall_len"][wall_id] += length

    summaries: List[Dict[str, Any]] = []
    for room_id, data in accumulator.items():
        by_wall = [
            {"wall": wall_id, "length": round(data["by_wall_len"][wall_id], 6)}
            for wall_id in sorted(data["by_wall_len"])
        ]
        summaries.append(
            {
                "room": room_id,
                "walls": sorted(data["walls"]),
                "length_total": round(sum(item["length"] for item in by_wall), 6),
            }
        )
    summaries.sort(key=lambda item: item["room"])
    return summaries


def _build_lookups(plan: Dict[str, Any]):
    """Prepare lookup dictionaries for walls, rooms, and openings."""
    instances = plan.get("instances", {})
    structural = instances.get("structural", {}) or {}
    rooms_by_type = instances.get("room", {}) or {}

    wall_type: Dict[str, str] = {}
    for wall in structural.get("interior_wall", []) or []:
        wall_id = as_id(wall if isinstance(wall, str) else wall.get("id"))
        if wall_id:
            wall_type[wall_id] = "interior_wall"
    for wall in structural.get("exterior_wall", []) or []:
        wall_id = as_id(wall if isinstance(wall, str) else wall.get("id"))
        if wall_id:
            wall_type[wall_id] = "exterior_wall"

    room_type: Dict[str, str] = {}
    room_geom: Dict[str, Any] = {}
    for subtype, records in rooms_by_type.items():
        for record in records or []:
            room_id = as_id(record if isinstance(record, str) else record.get("id"))
            if not room_id:
                continue
            room_type[room_id] = subtype
            geom = geometry_from_record(record if isinstance(record, dict) else None)
            if geom is not None:
                room_geom[room_id] = geom

    relations = normalize_relation_ids(get_relations_dict(plan))

    bounded_rel = relations.get("bounded_by", [])
    if isinstance(bounded_rel, dict):
        bounded_iterable = bounded_rel.get("edges", [])
    else:
        bounded_iterable = bounded_rel

    wall_to_rooms: Dict[str, set] = {}
    for entry in bounded_iterable or []:
        wall_id = entry.get("wall")
        room_id = entry.get("room")
        if wall_id and room_id:
            wall_to_rooms.setdefault(wall_id, set()).add(room_id)

    opening_to_walls: Dict[str, List[str]] = {}
    for entry in relations.get("hosts_opening", []):
        opening_id = entry.get("opening")
        wall_id = entry.get("wall")
        if opening_id and wall_id:
            opening_to_walls.setdefault(opening_id, []).append(wall_id)

    return wall_type, room_type, room_geom, wall_to_rooms, opening_to_walls


def nearest_two_rooms_on_host_walls(
    door_id: str,
    door_geom_map: Dict[str, Any],
    host_walls: Iterable[str],
    wall_to_rooms: Dict[str, Iterable[str]],
    room_geom: Dict[str, Any],
) -> List[str]:
    """Return up to two rooms closest to a given door based on its host walls."""
    candidates: List[str] = []
    for wall_id in host_walls:
        candidates.extend(list(wall_to_rooms.get(wall_id, [])))
    candidates = list(dict.fromkeys(candidates))
    if not candidates:
        return []

    door_geom = door_geom_map.get(door_id)
    if door_geom is None or door_geom.is_empty:
        return candidates[:2]

    centroid = door_geom.centroid
    scored: List[tuple[float, str]] = []
    for room_id in candidates:
        geom = room_geom.get(room_id)
        if geom is None or geom.is_empty:
            continue
        scored.append((geom.distance(centroid), room_id))
    scored.sort(key=lambda pair: pair[0])

    result: List[str] = []
    for _, room_id in scored:
        if room_id not in result:
            result.append(room_id)
        if len(result) == 2:
            break
    return result


def build_connected_via_door_from_hosts(plan: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Construct connected_via_door relations using existing host mappings."""
    wall_type, room_type, room_geom, wall_to_rooms, opening_to_walls = _build_lookups(plan)

    structural = plan.get("instances", {}).get("structural", {}) or {}
    door_records = (structural.get("door") or []) + (structural.get("front_door") or [])

    door_geom: Dict[str, Any] = {}
    for record in door_records:
        door_id = as_id(record if isinstance(record, str) else record.get("id"))
        if not door_id:
            continue
        geom = geometry_from_record(record if isinstance(record, dict) else None)
        if geom is not None:
            door_geom[door_id] = geom

    passages: List[Dict[str, Any]] = []
    for record in structural.get("door", []) or []:
        door_id = as_id(record if isinstance(record, str) else record.get("id"))
        if not door_id:
            continue
        host_walls = list(dict.fromkeys(opening_to_walls.get(door_id, [])))
        if not host_walls:
            continue
        rooms = nearest_two_rooms_on_host_walls(door_id, door_geom, host_walls, wall_to_rooms, room_geom)
        if len(rooms) != 2:
            continue
        passages.append(
            {
                "id": f"E-pass-{len(passages) + 1:05d}",
                "door": door_id,
                "door_type": "door",
                "rooms": rooms,
                "through_wall": _choose_through_wall(door_id, host_walls, "door", wall_type, door_geom, wall_to_rooms, room_geom),
                "room_types": [room_type.get(room_id) for room_id in rooms],
            }
        )

    for record in structural.get("front_door", []) or []:
        door_id = as_id(record if isinstance(record, str) else record.get("id"))
        if not door_id:
            continue
        host_walls = list(dict.fromkeys(opening_to_walls.get(door_id, [])))
        if not host_walls:
            continue
        rooms = nearest_two_rooms_on_host_walls(door_id, door_geom, host_walls, wall_to_rooms, room_geom)
        if rooms:
            targets = [rooms[0], "OUT-0000"]
        else:
            exterior_contact = any(wall_type.get(wall_id) == "exterior_wall" for wall_id in host_walls)
            if not exterior_contact:
                continue
            any_room = []
            for wall_id in host_walls:
                any_room.extend(list(wall_to_rooms.get(wall_id, [])))
            any_room = [room_id for room_id in dict.fromkeys(any_room) if room_id in room_geom]
            if not any_room:
                continue
            targets = [any_room[0], "OUT-0000"]

        through_wall = _choose_through_wall(
            door_id, host_walls, "front_door", wall_type, door_geom, wall_to_rooms, room_geom
        )
        passages.append(
            {
                "id": f"E-pass-{len(passages) + 1:05d}",
                "door": door_id,
                "door_type": "front_door",
                "rooms": targets,
                "through_wall": through_wall,
                "room_types": [
                    "outside" if room_id == "OUT-0000" else room_type.get(room_id)
                    for room_id in targets
                ],
            }
        )

    return passages


def _choose_through_wall(
    door_id: str,
    host_walls: List[str],
    kind: str,
    wall_type: Dict[str, str],
    door_geom: Dict[str, Any],
    wall_to_rooms: Dict[str, Iterable[str]],
    room_geom: Dict[str, Any],
) -> Optional[str]:
    """Select the most appropriate wall referenced by a door."""
    if kind == "front_door":
        for wall_id in host_walls:
            if wall_type.get(wall_id) == "exterior_wall":
                return wall_id

    geometry = door_geom.get(door_id)
    if geometry is None or geometry.is_empty or not host_walls:
        return host_walls[0] if host_walls else None

    centroid = geometry.centroid
    best = (float("inf"), None)
    for wall_id in host_walls:
        rooms = wall_to_rooms.get(wall_id, [])
        if not rooms:
            continue
        minimum = min(
            (room_geom[room_id].centroid.distance(centroid) for room_id in rooms if room_id in room_geom),
            default=float("inf"),
        )
        if minimum < best[0]:
            best = (minimum, wall_id)
    return best[1] or (host_walls[0] if host_walls else None)


__all__ = [
    "as_id",
    "bounded_by_per_room",
    "build_connected_via_door_from_hosts",
    "geometry_from_record",
    "nearest_two_rooms_on_host_walls",
    "normalize_relation_ids",
]
