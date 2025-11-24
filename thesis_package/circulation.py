"""Derive circulation information (entry, reachable rooms, door graph) for plans."""

from __future__ import annotations

from collections import defaultdict, deque
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

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


def _extract_bbox(record: Dict[str, Any]) -> Optional[Tuple[float, float, float, float]]:
    props = record.get("props") if isinstance(record, dict) else {}
    if not isinstance(props, dict):
        props = {}
    bbox = props.get("bbox")
    if isinstance(bbox, (list, tuple)) and len(bbox) == 4:
        try:
            return (float(bbox[0]), float(bbox[1]), float(bbox[2]), float(bbox[3]))
        except (TypeError, ValueError):
            return None
    geom = record.get("geom")
    if not isinstance(geom, dict):
        return None
    coords = geom.get("coordinates")
    if not coords:
        return None
    stack = coords
    while stack and isinstance(stack, list) and stack and isinstance(stack[0], list):
        stack = stack[0]
    xs: List[float] = []
    ys: List[float] = []
    for pt in stack or []:
        if not isinstance(pt, (list, tuple)) or len(pt) != 2:
            continue
        try:
            xs.append(float(pt[0]))
            ys.append(float(pt[1]))
        except (TypeError, ValueError):
            continue
    if not xs or not ys:
        return None
    return (min(xs), min(ys), max(xs), max(ys))


def _extract_centroid(record: Dict[str, Any]) -> Optional[Tuple[float, float]]:
    props = record.get("props") if isinstance(record, dict) else {}
    if not isinstance(props, dict):
        return None
    centroid = props.get("centroid")
    if isinstance(centroid, (list, tuple)) and len(centroid) == 2:
        try:
            return (float(centroid[0]), float(centroid[1]))
        except (TypeError, ValueError):
            return None
    return None


def extract_bbox(record: Dict[str, Any]) -> Optional[Tuple[float, float, float, float]]:
    """Public wrapper for legacy callers."""
    return _extract_bbox(record)


def _expand_bbox(bbox: Tuple[float, float, float, float], margin: float) -> Tuple[float, float, float, float]:
    minx, miny, maxx, maxy = bbox
    return (minx - margin, miny - margin, maxx + margin, maxy + margin)


def _bbox_overlap(a: Tuple[float, float, float, float], b: Tuple[float, float, float, float]) -> bool:
    return not (a[2] < b[0] or a[0] > b[2] or a[3] < b[1] or a[1] > b[3])


def _bbox_overlap_area(a: Tuple[float, float, float, float], b: Tuple[float, float, float, float]) -> float:
    if not _bbox_overlap(a, b):
        return 0.0
    x_overlap = min(a[2], b[2]) - max(a[0], b[0])
    y_overlap = min(a[3], b[3]) - max(a[1], b[1])
    return max(0.0, x_overlap) * max(0.0, y_overlap)


def _distance_sq(p1: Tuple[float, float], p2: Tuple[float, float]) -> float:
    return (p1[0] - p2[0]) ** 2 + (p1[1] - p2[1]) ** 2


def derive_window_analysis(plan: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Extract window exposure information needed for JSON export.

    Returns a dictionary ready to insert under circulation.window_analysis, or None when
    there are no windows and no relevant exterior associations.
    """
    instances = plan.get("instances", {}) or {}
    structural = instances.get("structural", {}) or {}
    room_instances = instances.get("room", {}) or {}

    window_records = structural.get("window") or []
    exterior_walls = structural.get("exterior_wall") or []

    window_ids: List[str] = []
    windows_by_id: Dict[str, Dict[str, Any]] = {}
    for record in window_records:
        window_id = as_id(record)
        if not window_id:
            continue
        window_ids.append(window_id)
        windows_by_id[window_id] = record

    if not window_ids:
        return None

    exterior_ids: Set[str] = set()
    for wall in exterior_walls:
        wall_id = as_id(wall)
        if wall_id:
            exterior_ids.add(wall_id)

    room_bboxes: Dict[str, Tuple[float, float, float, float]] = {}
    room_centroids: Dict[str, Tuple[float, float]] = {}
    room_types: Dict[str, str] = {}
    for subtype, records in room_instances.items():
        for record in records or []:
            if not isinstance(record, dict):
                continue
            room_id = record.get("id")
            if not isinstance(room_id, str):
                continue
            if isinstance(subtype, str):
                room_types[room_id] = subtype.lower()
            bbox = _extract_bbox(record)
            if bbox:
                room_bboxes[room_id] = bbox
            centroid = _extract_centroid(record)
            if centroid:
                room_centroids[room_id] = centroid

    window_bboxes: Dict[str, Tuple[float, float, float, float]] = {}
    window_centroids: Dict[str, Tuple[float, float]] = {}
    for window_id, record in windows_by_id.items():
        bbox = _extract_bbox(record)
        if bbox:
            window_bboxes[window_id] = bbox
        centroid = _extract_centroid(record)
        if centroid:
            window_centroids[window_id] = centroid

    wall_bboxes: Dict[str, Tuple[float, float, float, float]] = {}
    for category in ("exterior_wall", "interior_wall"):
        for wall in structural.get(category, []) or []:
            wall_id = as_id(wall)
            if not wall_id:
                continue
            bbox = _extract_bbox(wall)
            if bbox:
                wall_bboxes[wall_id] = bbox

    relations = plan.get("graph", {}).get("relations", {}) or {}
    bounded = relations.get("bounded_by")

    room_to_walls: Dict[str, Set[str]] = defaultdict(set)
    wall_to_rooms: Dict[str, Set[str]] = defaultdict(set)

    def _register_room_walls(room: Optional[str], walls: Sequence[str]) -> None:
        if not room:
            return
        for wall_id in walls:
            if not wall_id:
                continue
            room_to_walls[room].add(wall_id)
            wall_to_rooms[wall_id].add(room)

    if isinstance(bounded, dict):
        for edge in bounded.get("edges", []) or []:
            _register_room_walls(edge.get("room"), [edge.get("wall")])
        for entry in bounded.get("per_room", []) or []:
            walls = entry.get("walls") or []
            _register_room_walls(entry.get("room"), walls)
    elif isinstance(bounded, list):
        for edge in bounded:
            _register_room_walls(edge.get("room"), [edge.get("wall")])

    window_to_walls: Dict[str, Set[str]] = defaultdict(set)
    hosts_opening = relations.get("hosts_opening") or []
    for relation in hosts_opening:
        if relation.get("opening_type") != "window":
            continue
        window_id = relation.get("opening")
        wall_id = relation.get("wall")
        if not window_id or not wall_id:
            continue
        window_to_walls[window_id].add(wall_id)

    WINDOW_MARGIN = 0.05
    ROOM_MARGIN = 0.05
    WALL_MARGIN = 0.02

    # Limited fallback: only fix missing exterior associations for balconies/verandas.
    SPECIAL_ROOM_TYPES = {"balcony", "veranda"}
    for room_id, subtype in room_types.items():
        if subtype not in SPECIAL_ROOM_TYPES:
            continue
        room_bbox = room_bboxes.get(room_id)
        if not room_bbox:
            continue
        expanded_room = _expand_bbox(room_bbox, ROOM_MARGIN)
        for wall_id in exterior_ids:
            wall_bbox = wall_bboxes.get(wall_id)
            if not wall_bbox:
                continue
            expanded_wall = _expand_bbox(wall_bbox, WALL_MARGIN)
            if _bbox_overlap(expanded_room, expanded_wall):
                room_to_walls[room_id].add(wall_id)
                wall_to_rooms[wall_id].add(room_id)

    window_entries: List[Dict[str, Any]] = []
    for window_id in sorted(window_ids):
        record = windows_by_id[window_id]
        props = record.get("props") if isinstance(record, dict) else {}
        area_val = 0.0
        if isinstance(props, dict):
            raw_area = props.get("area")
            if isinstance(raw_area, (int, float)):
                area_val = float(raw_area)
        candidate_walls = window_to_walls.get(window_id, set())
        selected_wall = None
        window_bbox = window_bboxes.get(window_id)
        if candidate_walls and window_bbox:
            enlarged_window_bbox = _expand_bbox(window_bbox, WALL_MARGIN)
            best_overlap = 0.0
            for candidate in sorted(candidate_walls):
                wall_bbox = wall_bboxes.get(candidate)
                if not wall_bbox:
                    continue
                overlap = _bbox_overlap_area(enlarged_window_bbox, _expand_bbox(wall_bbox, WALL_MARGIN))
                if overlap > best_overlap:
                    best_overlap = overlap
                    selected_wall = candidate
        if selected_wall is None:
            for candidate in sorted(candidate_walls):
                if candidate in exterior_ids:
                    selected_wall = candidate
                    break
        if selected_wall is None and candidate_walls:
            selected_wall = sorted(candidate_walls)[0]
        window_entries.append(
            {
                "id": window_id,
                "wall": selected_wall,
                "host_walls": sorted(candidate_walls),
                "area": area_val,
            }
        )

    room_windows: Dict[str, Set[str]] = defaultdict(set)
    for window_id in sorted(window_ids):
        walls = window_to_walls.get(window_id, set())
        candidate_rooms: Set[str] = set()
        for wall_id in walls:
            if wall_id not in exterior_ids:
                continue
            candidate_rooms.update(wall_to_rooms.get(wall_id, ()))
        if not candidate_rooms:
            continue
        assigned: List[str] = []
        window_bbox = window_bboxes.get(window_id)
        expanded_window_bbox = _expand_bbox(window_bbox, WINDOW_MARGIN) if window_bbox else None
        if expanded_window_bbox:
            for room_id in sorted(candidate_rooms):
                room_bbox = room_bboxes.get(room_id)
                if not room_bbox:
                    continue
                expanded_room_bbox = _expand_bbox(room_bbox, ROOM_MARGIN)
                if _bbox_overlap(expanded_room_bbox, expanded_window_bbox):
                    assigned.append(room_id)
        if assigned:
            filtered_rooms = assigned
            window_centroid = window_centroids.get(window_id)
            if window_centroid:
                distance_data: List[Tuple[str, float]] = []
                for room_id in assigned:
                    centroid = room_centroids.get(room_id)
                    if not centroid:
                        continue
                    dist_sq = _distance_sq(window_centroid, centroid)
                    distance_data.append((room_id, dist_sq))
                if distance_data:
                    distance_data.sort(key=lambda item: item[1])
                    best_sq = distance_data[0][1]
                    threshold = (best_sq ** 0.5) + 1.0
                    threshold_sq = threshold * threshold
                    filtered = [room_id for room_id, dist in distance_data if dist <= threshold_sq]
                    if filtered:
                        filtered_rooms = filtered
            for room_id in filtered_rooms:
                room_windows[room_id].add(window_id)
            continue
        window_centroid = window_centroids.get(window_id)
        if not window_centroid:
            continue
        best_room: Optional[str] = None
        best_dist: Optional[float] = None
        for room_id in candidate_rooms:
            centroid = room_centroids.get(room_id)
            if not centroid:
                continue
            dist = _distance_sq(window_centroid, centroid)
            if best_dist is None or dist < best_dist:
                best_dist = dist
                best_room = room_id
        if best_room:
            room_windows[best_room].add(window_id)

    room_ids = sorted(_collect_room_ids(plan))
    room_summaries: List[Dict[str, Any]] = []
    for room_id in room_ids:
        exterior = sorted(w for w in room_to_walls.get(room_id, set()) if w in exterior_ids)
        windows = sorted(room_windows.get(room_id, set()))
        room_summaries.append(
            {
                "room": room_id,
                "exterior_walls": exterior,
                "windows_on_exterior": windows,
            }
        )

    if not window_entries and not room_summaries:
        return None

    return {"windows": window_entries, "rooms": room_summaries}


def derive_door_consistency(plan: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Capture door-to-wall relationships and structural wall statistics to help spot issues.
    """
    instances = plan.get("instances", {}) or {}
    structural = instances.get("structural", {}) or {}

    door_records = structural.get("door") or []
    if not door_records:
        return None

    interior_walls = structural.get("interior_wall") or []
    exterior_walls = structural.get("exterior_wall") or []

    doors_by_id: Dict[str, Dict[str, Any]] = {}
    for record in door_records:
        door_id = as_id(record)
        if not door_id:
            continue
        doors_by_id[door_id] = record
    if not doors_by_id:
        return None

    interior_wall_ids: Set[str] = {as_id(wall) for wall in interior_walls if as_id(wall)}
    all_wall_ids: Set[str] = interior_wall_ids | {as_id(wall) for wall in exterior_walls if as_id(wall)}

    relations = plan.get("graph", {}).get("relations", {}) or {}
    hosts_opening = relations.get("hosts_opening") or []
    connected = relations.get("connected_via_door") or []

    door_to_walls: Dict[str, Set[str]] = defaultdict(set)
    for relation in hosts_opening:
        if relation.get("opening_type") != "door":
            continue
        door_id = relation.get("opening")
        wall_id = relation.get("wall")
        if not door_id or not wall_id:
            continue
        door_to_walls[door_id].add(wall_id)

    door_to_rooms: Dict[str, Set[str]] = defaultdict(set)
    door_to_through_wall: Dict[str, Set[str]] = defaultdict(set)
    for relation in connected:
        door_id = relation.get("door")
        if not door_id:
            continue
        rooms = relation.get("rooms") or []
        for room_id in rooms:
            if isinstance(room_id, str) and room_id != OUTSIDE_ID:
                door_to_rooms[door_id].add(room_id)
        through = relation.get("through_wall")
        if isinstance(through, str) and through:
            door_to_through_wall[door_id].add(through)

    door_entries: List[Dict[str, Any]] = []
    for door_id in sorted(doors_by_id):
        record = doors_by_id[door_id]
        bbox = _extract_bbox(record)
        width = height = None
        if bbox:
            dx = float(bbox[2]) - float(bbox[0])
            dy = float(bbox[3]) - float(bbox[1])
            short = min(dx, dy)
            long = max(dx, dy)
            width = round(short, 3)
            height = round(long, 3)

        host_walls = sorted(door_to_walls.get(door_id, set()))
        through_walls = sorted(door_to_through_wall.get(door_id, set()))
        candidate_walls = sorted(set(host_walls) | set(through_walls))
        selected_wall = None
        if host_walls:
            selected_wall = host_walls[0]
        elif through_walls:
            selected_wall = through_walls[0]
        elif candidate_walls:
            selected_wall = candidate_walls[0]

        wall_exists = False
        if host_walls:
            wall_exists = any(wall in all_wall_ids for wall in host_walls)
        elif through_walls:
            wall_exists = any(wall in all_wall_ids for wall in through_walls)
        elif selected_wall:
            wall_exists = selected_wall in all_wall_ids

        rooms = sorted(door_to_rooms.get(door_id, set()))

        door_entries.append(
            {
                "id": door_id,
                "wall": through_walls,
                "host_walls": host_walls,
                "rooms": rooms,
                "width": width,
                "height": height,
                "wall_exists": wall_exists,
            }
        )

    thickness_values: List[float] = []
    for wall in interior_walls:
        bbox = _extract_bbox(wall)
        if not bbox:
            continue
        dx = float(bbox[2]) - float(bbox[0])
        dy = float(bbox[3]) - float(bbox[1])
        thickness = min(dx, dy)
        if thickness > 0:
            thickness_values.append(thickness)

    wall_thickness_mean = round(sum(thickness_values) / len(thickness_values), 3) if thickness_values else None

    return {
        "doors": door_entries,
        "wall_thickness_mean": wall_thickness_mean,
    }


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


def _structural_payload(
    window_analysis: Optional[Dict[str, Any]], door_consistency: Optional[Dict[str, Any]]
) -> Optional[Dict[str, Any]]:
    if not window_analysis and not door_consistency:
        return None
    payload: Dict[str, Any] = {}
    if window_analysis:
        payload["window_analysis"] = window_analysis
    if door_consistency:
        payload["door_wall_consistency"] = door_consistency
    return payload


def build_circulation(plan: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Create a circulation summary from plan instances and relations."""
    window_analysis = derive_window_analysis(plan)
    door_consistency = derive_door_consistency(plan)

    structural = plan.get("instances", {}).get("structural", {}) or {}
    front_records = structural.get("front_door") or []

    entry_id: Optional[str] = None
    for record in front_records:
        entry_id = as_id(record)
        if entry_id:
            break
    if not entry_id:
        return _structural_payload(window_analysis, door_consistency)

    entry_node = f"front_door_{entry_id}"

    relations = plan.get("graph", {}).get("relations") or {}
    relations = normalize_relation_ids(relations)
    passages = relations.get("connected_via_door") or []
    if not passages:
        return _structural_payload(window_analysis, door_consistency)

    adjacency, door_edges = _build_adjacency(entry_node, passages)
    if entry_node not in adjacency:
        # No room connects to the front door; nothing useful to report.
        return _structural_payload(window_analysis, door_consistency)

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
    if window_analysis:
        result["window_analysis"] = window_analysis
    if door_consistency:
        result["door_wall_consistency"] = door_consistency
    return result


__all__ = ["build_circulation", "derive_window_analysis", "derive_door_consistency", "extract_bbox"]
