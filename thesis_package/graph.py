from collections import defaultdict
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple

from .config import JSON_DIR
from .constants import ROOM_KEYS, STRUCT_KEYS, ROOM_PREFIX
from .geometry import compute_relations
from .plan_utils import assign_ids
from .relations import (
    as_id,
    bounded_by_per_room,
    build_connected_via_door_from_hosts,
    normalize_relation_ids,
)

OUTSIDE_ID = "OUT-0000"


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
    stack: Iterable[Any] = coords
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


def _extract_relation_rows(tbl):
    if isinstance(tbl, dict) and "edges" in tbl:
        return tbl.get("edges", [])
    return tbl


def _update_rel_table(tbl, slots, remap):
    rows = _extract_relation_rows(tbl)
    if not isinstance(rows, list) or not remap:
        return 0
    changed = 0
    for e in rows:
        if not isinstance(e, dict):
            continue
        for slot in slots:
            if slot == "rooms" and isinstance(e.get(slot), list):
                before = e[slot]
                after = [remap.get(x, x) for x in before]
                if after != before:
                    changed += sum(1 for old, new in zip(before, after) if old != new)
                e[slot] = after
            else:
                v = e.get(slot)
                if isinstance(v, str) and v in remap:
                    e[slot] = remap[v]
                    changed += 1
    return changed

def apply_room_id_map_to_relations_inplace(plan: dict, id_map: dict) -> int:
    if not id_map:
        return 0
    total = 0

    rel = plan.get("relations")
    if isinstance(rel, dict):
        total += _update_rel_table(rel.get("bounded_by"),         ["room"],     id_map)
        total += _update_rel_table(rel.get("adjacent_to"),        ["a","b"],    id_map)
        total += _update_rel_table(rel.get("connected_via_door"), ["rooms"],    id_map)
        total += _update_rel_table(rel.get("window_connects"),    ["from","to"],id_map)
        total += _update_rel_table(rel.get("contains"),           ["container"],id_map)

    grel = plan.get("graph", {}).get("relations")
    if isinstance(grel, dict):
        total += _update_rel_table(grel.get("bounded_by"),         ["room"],     id_map)
        total += _update_rel_table(grel.get("adjacent_to"),        ["a","b"],    id_map)
        total += _update_rel_table(grel.get("connected_via_door"), ["rooms"],    id_map)
        total += _update_rel_table(grel.get("window_connects"),    ["from","to"],id_map)
        total += _update_rel_table(grel.get("contains"),           ["container"],id_map)

    circ = plan.get("circulation")
    if isinstance(circ, dict):
        rooms = circ.get("room_nodes")
        if isinstance(rooms, list):
            circ["room_nodes"] = [id_map.get(node, node) for node in rooms]
        door_edges = circ.get("door_edges")
        if isinstance(door_edges, list):
            remapped_edges = []
            for edge in door_edges:
                if isinstance(edge, list) and len(edge) == 3:
                    a, b, door = edge
                    remapped_edges.append([id_map.get(a, a), id_map.get(b, b), door])
            circ["door_edges"] = remapped_edges
        paths = circ.get("reachability_paths")
        if isinstance(paths, dict):
            remapped_paths = {}
            for key, path in paths.items():
                new_key = id_map.get(key, key)
                if isinstance(path, list):
                    remapped_paths[new_key] = [id_map.get(node, node) for node in path]
            circ["reachability_paths"] = remapped_paths

    return total

def relabel_rooms_with_subtype_prefixes_inplace(plan):
    inst = plan.get("instances", {})
    remap = {}

    room_dict = inst.get("room")
    if isinstance(room_dict, dict):
        for subtype, arr in room_dict.items():
            pref = ROOM_PREFIX.get(subtype.lower(), "RM")
            new_ids = assign_ids(len(arr), pref)
            for i, rec in enumerate(arr):
                old, new = rec.get("id"), new_ids[i]
                if old and old != new: remap[old] = new; rec["id"] = new
    elif isinstance(inst.get("rooms"), list):
        by_sub = {}
        for rec in inst["rooms"]:
            st = (rec.get("subtype") or "unknown").lower()
            by_sub.setdefault(st, []).append(rec)
        for subtype, arr in by_sub.items():
            pref = ROOM_PREFIX.get(subtype, "RM")
            new_ids = assign_ids(len(arr), pref)
            for i, rec in enumerate(arr):
                old, new = rec.get("id"), new_ids[i]
                if old and old != new: remap[old] = new; rec["id"] = new
    else:
        for key in ("rooms","room"):
            if isinstance(plan.get(key), list):
                by_sub = {}
                for rec in plan[key]:
                    st = (rec.get("subtype") or "unknown").lower()
                    by_sub.setdefault(st, []).append(rec)
                for subtype, arr in by_sub.items():
                    pref = ROOM_PREFIX.get(subtype, "RM")
                    new_ids = assign_ids(len(arr), pref)
                    for i, rec in enumerate(arr):
                        old, new = rec.get("id"), new_ids[i]
                        if old and old != new: remap[old] = new; rec["id"] = new

    if remap: apply_room_id_map_to_relations_inplace(plan, remap)
    return remap


# --- Cell 10 ---
def ensure_outside_virtual_inplace(plan):
    virt = plan.setdefault("instances", {}).setdefault("virtual", [])
    if not any(isinstance(v, dict) and v.get("id") == "OUT-0000" for v in virt):
        virt.append({"id": "OUT-0000", "class": "Outside", "props": {"note": "virtual exterior"}})

def rebuild_connected_via_door_inplace(plan):
    ensure_outside_virtual_inplace(plan)
    # pastikan relasi lain sudah dinormalisasi agar downstream aman
    gr = plan.setdefault("graph", {})
    rel = gr.setdefault("relations", {})
    rel.update(normalize_relation_ids(rel))
    passages = build_connected_via_door_from_hosts(plan)
    rel["connected_via_door"] = passages
    metadata = plan.setdefault("metadata", {})
    summary = metadata.setdefault("summary", {})
    rel_summary = summary.setdefault("relationship_summary", {})
    rel_summary["door_connections"] = len(passages)
    return passages


def derive_window_analysis(plan: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Extract window exposure information for downstream validation/exports.
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
            if not _bbox_overlap(expanded_room, expanded_wall):
                continue
            room_to_walls[room_id].add(wall_id)
            wall_to_rooms[wall_id].add(room_id)

    room_windows: Dict[str, Set[str]] = defaultdict(set)
    window_entries: List[Dict[str, Any]] = []

    for window_id in window_ids:
        connected_walls = sorted(window_to_walls.get(window_id, set()))
        room_entries = sorted(wall_to_rooms.get(wall_id, set()) for wall_id in connected_walls)
        flattened_rooms = sorted({room for rooms in room_entries for room in rooms})

        window_entry = {
            "id": window_id,
            "wall": connected_walls[0] if connected_walls else None,
            "host_walls": connected_walls,
            "area": round(float(windows_by_id[window_id].get("props", {}).get("area") or 0.0), 2),
        }
        window_entries.append(window_entry)

        candidate_rooms = flattened_rooms
        if not candidate_rooms:
            window_bbox = window_bboxes.get(window_id)
            if window_bbox:
                best_room = None
                best_overlap = 0.0
                expanded_window = _expand_bbox(window_bbox, WINDOW_MARGIN)
                for room_id, bbox in room_bboxes.items():
                    overlap = _bbox_overlap_area(expanded_window, bbox)
                    if overlap > best_overlap:
                        best_room = room_id
                        best_overlap = overlap
                if best_room:
                    candidate_rooms = [best_room]
        if not candidate_rooms:
            continue

        if len(candidate_rooms) == 1:
            room_windows[candidate_rooms[0]].add(window_id)
            continue

        window_bbox = window_bboxes.get(window_id)
        if window_bbox:
            overlaps = []
            expanded_window = _expand_bbox(window_bbox, WINDOW_MARGIN)
            for room_id in candidate_rooms:
                room_bbox = room_bboxes.get(room_id)
                if not room_bbox:
                    continue
                area = _bbox_overlap_area(expanded_window, room_bbox)
                if area > 0:
                    overlaps.append((room_id, area))
            if overlaps:
                overlaps.sort(key=lambda item: item[1], reverse=True)
                filtered_rooms = [room_id for room_id, _ in overlaps]
                if len(filtered_rooms) > 1:
                    best_area = overlaps[0][1]
                    threshold = best_area * 0.5
                    filtered_rooms = [room_id for room_id, area in overlaps if area >= threshold]
                if filtered_rooms:
                    for room_id in filtered_rooms:
                        room_windows[room_id].add(window_id)
                    continue
                filtered_rooms = [room_id for room_id, _ in overlaps]
            else:
                filtered_rooms = candidate_rooms
        else:
            filtered_rooms = candidate_rooms

        window_centroid = window_centroids.get(window_id)
        if not window_centroid:
            continue
        best_room = None
        best_dist = None
        for room_id in filtered_rooms:
            centroid = room_centroids.get(room_id)
            if not centroid:
                continue
            dist = _distance_sq(window_centroid, centroid)
            if best_dist is None or dist < best_dist:
                best_dist = dist
                best_room = room_id
        if best_room:
            room_windows[best_room].add(window_id)

    room_summaries: List[Dict[str, Any]] = []
    for room_id in sorted(room_windows):
        windows = sorted(room_windows.get(room_id, set()))
        if not windows:
            continue
        exterior = sorted(w for w in room_to_walls.get(room_id, set()) if w in exterior_ids)
        room_summaries.append(
            {
                "roomHasWindow": room_id,
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

        rooms = sorted(door_to_rooms.get(door_id, set()))
        door_entries.append(
            {
                "id": door_id,
                "wall": [selected_wall] if selected_wall else [],
                "host_walls": host_walls or candidate_walls,
                "rooms": rooms,
                "width": width,
                "height": height,
                "wall_exists": selected_wall in all_wall_ids if selected_wall else False,
            }
        )

    thickness_values: List[float] = []
    for wall_id in interior_wall_ids:
        wall = next((wall for wall in interior_walls if as_id(wall) == wall_id), None)
        if not wall or not isinstance(wall, dict):
            continue
        bbox = _extract_bbox(wall)
        if not bbox:
            continue
        dx = float(bbox[2]) - float(bbox[0])
        dy = float(bbox[3]) - float(bbox[1])
        thickness_values.append(round(min(dx, dy), 3))

    wall_thickness_mean = round(sum(thickness_values) / len(thickness_values), 3) if thickness_values else None

    return {
        "doors": door_entries,
        "wall_thickness_mean": wall_thickness_mean,
    }


def embed_structural_analyses_in_relations(plan: Dict[str, Any]) -> None:
    """Compute window/door structural analyses and store them under graph.relations."""
    if not isinstance(plan, dict):
        return
    relations = plan.setdefault("graph", {}).setdefault("relations", {})
    window_analysis = derive_window_analysis(plan)
    if window_analysis:
        relations["window_analysis"] = window_analysis
    else:
        relations.pop("window_analysis", None)
    door_consistency = derive_door_consistency(plan)
    if door_consistency:
        relations["door_wall_consistency"] = door_consistency
    else:
        relations.pop("door_wall_consistency", None)


# --- Cell 11 ---
def convert_instances_for_relations(room_instances, struct_instances):
    mock_plan = {"instances": {"room": {}, "structural": {}}}
    for room_type, rooms in room_instances.items():
        if rooms: mock_plan["instances"]["room"][room_type] = rooms
    for struct_type, structures in struct_instances.items():
        if structures: mock_plan["instances"]["structural"][struct_type] = structures
    return mock_plan

def export_graph(plan, room_instances, struct_instances=None):
    mock_plan = convert_instances_for_relations(room_instances, struct_instances or {})

    relations = compute_relations(mock_plan)
    relations = normalize_relation_ids(relations)
    mock_plan.setdefault("graph", {}).setdefault("relations", relations)

    bounded_edges = relations.get("bounded_by", [])
    relations["bounded_by"] = {
        "edges": bounded_edges,
        "per_room": bounded_by_per_room({"bounded_by": bounded_edges}),
    }

    # bangun koneksi pintu dari hosts_opening + bounded_by
    passages = build_connected_via_door_from_hosts(mock_plan)
    relations["connected_via_door"] = passages

    nodes = []
    for rk in ROOM_KEYS:
        for r in room_instances.get(rk, []):
            nodes.append({
                "id": r["id"], "type": r["type"], "category": "room",
                "area": r["props"]["area"], "centroid": r["props"]["centroid"], "bbox": r["props"]["bbox"]
            })
    if struct_instances:
        for sk in STRUCT_KEYS:
            for s in struct_instances.get(sk, []):
                nodes.append({
                    "id": s["id"], "type": s["type"], "category": "structural",
                    "area": s["props"]["area"], "centroid": s["props"]["centroid"], "bbox": s["props"]["bbox"]
                })

    edges = []
    for rel in relations["adjacent_to"]:
        edges.append({"source": rel["a"], "target": rel["b"], "type": "adjacent",
                      "properties": {"overlap_length": rel["overlap_length"]}})
    for rel in relations["connected_via_door"]:
        if len(rel["rooms"]) == 2:
            edges.append({"source": rel["rooms"][0], "target": rel["rooms"][1],
                          "type": "connected_via_door", "properties": {"door": rel["door"]}})
    for rel in relations["bounded_by"]["edges"]:
        edges.append({"source": rel["room"], "target": rel["wall"], "type": "bounded_by",
                      "properties": {"length": rel["length"], "wall_type": rel["wall_type"]}})
    for rel in relations["hosts_opening"]:
        edges.append({"source": rel["wall"], "target": rel["opening"], "type": "hosts_opening",
                      "properties": {"opening_type": rel["opening_type"]}})

    return {
        "nodes": nodes, "edges": edges, "relations": relations,
        "statistics": {
            "total_nodes": len(nodes),
            "total_edges": len(edges),
            "relationship_types": list(relations.keys()),
        }
    }
