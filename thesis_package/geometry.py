"""Geometry-centric utilities for working with plan instances and relations."""

from __future__ import annotations

from collections import namedtuple
from copy import deepcopy
from typing import Any, Dict, List

from shapely.geometry import LineString, MultiLineString, MultiPolygon, Point, Polygon, shape
from shapely.strtree import STRtree

from .constants import EPS_LEN, OPENING_BUFFER, WALL_BUFFER
from .plan_utils import format_metric, round_float


GeoRec = namedtuple("GeoRec", "id cls subtype level geom raw")


def _geom(obj: Any):
    """Return shapely geometry for an instance record."""
    if obj is None:
        return None
    geom = obj.get("geom") if isinstance(obj, dict) else None
    if isinstance(geom, dict) and "type" in geom:
        return shape(geom)
    return None


def _id(obj: Any, fallback_prefix: str) -> str:
    """Fetch existing identifier or build a deterministic fallback."""
    if isinstance(obj, dict) and "id" in obj:
        return str(obj["id"])
    return f"{fallback_prefix}-{abs(hash(str(obj))) % 10**8:08d}"


def _level(obj: Dict[str, Any]) -> Any:
    """Best-effort level/storey lookup from instance records."""
    if not isinstance(obj, dict):
        return None
    return obj.get("level") or obj.get("storey") or obj.get("props", {}).get("level")


def find_instances(plan: Dict[str, Any]) -> Dict[str, List[Dict[str, Any]]]:
    """Collect room, wall, and opening instances from a plan structure."""
    out = {"rooms": [], "walls": [], "openings": []}
    instances = plan.get("instances")
    if not isinstance(instances, dict):
        return out

    room_data = instances.get("room")
    if isinstance(room_data, dict):
        for room_list in room_data.values():
            out["rooms"].extend(room_list or [])

    structural = instances.get("structural")
    if isinstance(structural, dict):
        for wall_type in ("interior_wall", "exterior_wall"):
            for wall in structural.get(wall_type, []) or []:
                wall_copy = deepcopy(wall)
                wall_copy.setdefault("subtype", "exterior" if "exterior" in wall_type else "interior")
                out["walls"].append(wall_copy)
        for opening_type in ("door", "window", "front_door"):
            for opening in structural.get(opening_type, []) or []:
                opening_copy = deepcopy(opening)
                opening_copy.setdefault("subtype", opening_type)
                out["openings"].append(opening_copy)
    return out


def boundary_overlap_length(room_poly: Polygon, wall_geom) -> float:
    """Measure overlap length between a room boundary and wall geometry."""
    if isinstance(wall_geom, (LineString, MultiLineString)):
        wall_geom = wall_geom.buffer(WALL_BUFFER, cap_style=2, join_style=2)
    intersection = room_poly.boundary.intersection(wall_geom)
    if intersection.is_empty:
        return 0.0
    if hasattr(intersection, "geoms"):
        total = sum(g.length for g in intersection.geoms)
    else:
        total = intersection.length
    return float(format_metric(total))


def _opening_wall_overlap_score(opening_geom, wall_geom) -> float:
    """Return overlap metric between a buffered opening and wall geometry."""
    if isinstance(wall_geom, (LineString, MultiLineString)):
        wall_geom = wall_geom.buffer(WALL_BUFFER, cap_style=2, join_style=2)
    buffered_opening = opening_geom.buffer(OPENING_BUFFER)
    overlap = buffered_opening.intersection(wall_geom)
    if overlap.is_empty:
        return 0.0
    area = getattr(overlap, "area", 0.0)
    return float(area if area > 0 else overlap.length)


def opening_on_wall(opening_geom, wall_geom) -> bool:
    """Return True when an opening lies on / intersects the wall geometry."""
    return _opening_wall_overlap_score(opening_geom, wall_geom) >= EPS_LEN


def _expand_bounds(bounds, margin: float) -> tuple[float, float, float, float]:
    minx, miny, maxx, maxy = bounds
    return (minx - margin, miny - margin, maxx + margin, maxy + margin)


def _bounds_overlap(a, b) -> bool:
    return not (a[2] < b[0] or a[0] > b[2] or a[3] < b[1] or a[1] > b[3])


def _shared_span(a, b) -> float:
    overlap_x = min(a[2], b[2]) - max(a[0], b[0])
    overlap_y = min(a[3], b[3]) - max(a[1], b[1])
    return max(0.0, overlap_x, overlap_y)


def index_instances(plan: Dict[str, Any]):
    """Build spatial indexes for rooms, walls, and openings."""
    inst = find_instances(plan)
    rooms: List[GeoRec] = []
    walls: List[GeoRec] = []
    openings: List[GeoRec] = []

    for record in inst["rooms"]:
        geom = _geom(record)
        if geom is None or geom.is_empty:
            continue
        rooms.append(GeoRec(_id(record, "RM"), "Room", record.get("subtype") or record.get("type"), _level(record), geom, record))

    for record in inst["walls"]:
        geom = _geom(record)
        if geom is None or geom.is_empty:
            continue
        walls.append(GeoRec(_id(record, "WL"), "Wall", record.get("subtype") or record.get("type"), _level(record), geom, record))

    for record in inst["openings"]:
        geom = _geom(record)
        if geom is None or geom.is_empty:
            continue
        openings.append(
            GeoRec(_id(record, "OP"), "Opening", record.get("subtype") or record.get("type"), _level(record), geom, record)
        )

    return {
        "rooms": rooms,
        "walls": walls,
        "openings": openings,
        "tree": {
            "rooms": STRtree([rec.geom for rec in rooms]) if rooms else None,
            "walls": STRtree([rec.geom for rec in walls]) if walls else None,
            "openings": STRtree([rec.geom for rec in openings]) if openings else None,
        },
    }


def compute_relations(plan: Dict[str, Any]) -> Dict[str, List[Dict[str, Any]]]:
    """Compute basic spatial relations (bounded_by, adjacency, hosts_opening)."""
    index = index_instances(plan)
    rooms, walls, openings = index["rooms"], index["walls"], index["openings"]

    bounded_by: List[Dict[str, Any]] = []
    for room in rooms:
        for wall in walls:
            overlap = boundary_overlap_length(room.geom, wall.geom)
            if overlap >= EPS_LEN:
                bounded_by.append(
                    {
                        "id": f"E-bnd-{len(bounded_by) + 1:05d}",
                        "room": room.id,
                        "wall": wall.id,
                        "length": overlap,
                        "wall_type": wall.subtype or "unknown",
                    }
                )

    # Targeted fallback: balconies/verandas sometimes miss exterior edges if bounded_by geometry breaks.
    existing_pairs = {(edge["room"], edge["wall"]) for edge in bounded_by}
    exterior_wall_ids = {
        wall.id
        for wall in walls
        if isinstance(wall.subtype, str) and wall.subtype.lower().startswith("exterior")
    }
    ROOM_MARGIN = 0.05
    WALL_MARGIN = 0.02
    SPECIAL_ROOM_TYPES = {"balcony", "veranda"}
    for room in rooms:
        subtype = (room.subtype or "").lower()
        if subtype not in SPECIAL_ROOM_TYPES:
            continue
        room_bounds = room.geom.bounds
        expanded_room = _expand_bounds(room_bounds, ROOM_MARGIN)
        for wall in walls:
            if wall.id not in exterior_wall_ids:
                continue
            pair = (room.id, wall.id)
            if pair in existing_pairs:
                continue
            expanded_wall = _expand_bounds(wall.geom.bounds, WALL_MARGIN)
            if not _bounds_overlap(expanded_room, expanded_wall):
                continue
            overlap = boundary_overlap_length(room.geom, wall.geom)
            if overlap < EPS_LEN:
                overlap = round_float(_shared_span(room_bounds, wall.geom.bounds))
                if overlap <= 0:
                    continue
            bounded_by.append(
                {
                    "id": f"E-bnd-{len(bounded_by) + 1:05d}",
                    "room": room.id,
                    "wall": wall.id,
                    "length": overlap,
                    "wall_type": wall.subtype or "unknown",
                }
            )
            existing_pairs.add(pair)

    adjacent_to: List[Dict[str, Any]] = []
    seen_pairs = set()
    for idx, room_a in enumerate(rooms):
        for room_b in rooms[idx + 1 :]:
            intersection = room_a.geom.boundary.intersection(room_b.geom.boundary)
            shared_length = format_metric(intersection.length) if not intersection.is_empty else 0.0
            if shared_length >= EPS_LEN:
                key = tuple(sorted((room_a.id, room_b.id)))
                if key in seen_pairs:
                    continue
                seen_pairs.add(key)
                adjacent_to.append(
                    {
                        "id": f"E-adj-{len(adjacent_to) + 1:05d}",
                        "a": room_a.id,
                        "b": room_b.id,
                        "overlap_length": shared_length,
                    }
                )

    hosts_opening: List[Dict[str, Any]] = []
    for opening in openings:
        contact_candidates: List[tuple[float, GeoRec]] = []
        for wall in walls:
            score = _opening_wall_overlap_score(opening.geom, wall.geom)
            if score >= EPS_LEN:
                contact_candidates.append((score, wall))

        selected: List[GeoRec] = []
        if contact_candidates:
            contact_candidates.sort(key=lambda item: item[0], reverse=True)
            top_score, top_wall = contact_candidates[0]
            selected.append(top_wall)
            if len(contact_candidates) > 1:
                second_score, second_wall = contact_candidates[1]
                if second_score >= max(EPS_LEN, top_score * 0.3):
                    selected.append(second_wall)
        else:
            distance_candidates = sorted(
                ((opening.geom.distance(wall.geom), wall) for wall in walls),
                key=lambda item: item[0],
            )
            if not distance_candidates:
                continue
            selected.append(distance_candidates[0][1])
            if (
                len(distance_candidates) > 1
                and distance_candidates[1][0] <= distance_candidates[0][0] + EPS_LEN
            ):
                selected.append(distance_candidates[1][1])

        for wall in selected:
            hosts_opening.append(
                {
                    "id": f"E-host-{len(hosts_opening) + 1:05d}",
                    "wall": wall.id,
                    "opening": opening.id,
                    "opening_type": opening.subtype or "opening",
                }
            )

    return {
        "bounded_by": bounded_by,
        "adjacent_to": adjacent_to,
        "hosts_opening": hosts_opening,
        "connected_via_door": [],  # populated later in graph.rebuild_connected_via_door_inplace
    }


__all__ = [
    "GeoRec",
    "boundary_overlap_length",
    "compute_relations",
    "find_instances",
    "index_instances",
    "opening_on_wall",
]
