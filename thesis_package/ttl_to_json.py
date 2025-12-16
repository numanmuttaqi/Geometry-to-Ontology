"""
Helpers to reconstruct the original plan JSON structure from a Turtle file.

This is the reverse of ``ontology/json_to_ttl.py`` for quick visualization.
Only geometry + basic metadata are restored (enough for ``plot_plan_json``).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional

from rdflib import Graph, Namespace, RDF
from rdflib.namespace import RDFS
from shapely.geometry import LineString, shape, mapping
from shapely.ops import nearest_points

from .constants import ROOM_KEYS, STRUCT_KEYS

# ======================================================
# Namespaces
# ======================================================
RESPLAN = Namespace("http://resplan.org/resplan#")
BOT = Namespace("https://w3id.org/bot#")

OUTSIDE_ID = "OUT-0000"

# ======================================================
# Reverse mappings (from json_to_ttl.py)
# ======================================================
ROOM_CLASS_TO_KEY = {
    RESPLAN.LivingRoom: "living",
    RESPLAN.Bedroom: "bedroom",
    RESPLAN.Kitchen: "kitchen",
    RESPLAN.Bathroom: "bathroom",
    RESPLAN.Balcony: "balcony",
    RESPLAN.Storage: "storage",
    RESPLAN.Stair: "stair",
    RESPLAN.Veranda: "veranda",
    RESPLAN.Parking: "parking",
}

STRUCT_CLASS_TO_KEY = {
    RESPLAN.InteriorWall: "interior_wall",
    RESPLAN.ExteriorWall: "exterior_wall",
    RESPLAN.FrontDoor: "front_door",
    RESPLAN.Door: "door",
    RESPLAN.Window: "window",
}

# ======================================================
# Helpers
# ======================================================
def _local_id(uri) -> str:
    s = str(uri)
    if "#" in s:
        return s.split("#", 1)[1]
    return s.rsplit("/", 1)[-1]


def _literal_float(graph: Graph, subj, pred) -> Optional[float]:
    val = graph.value(subj, pred)
    if val is None:
        return None
    try:
        return float(val)
    except Exception:
        return None


def _geom_props(graph: Graph, subj) -> Dict[str, Any]:
    cx = _literal_float(graph, subj, RESPLAN.centroidX)
    cy = _literal_float(graph, subj, RESPLAN.centroidY)
    bbox_vals = [
        _literal_float(graph, subj, RESPLAN.bboxMinX),
        _literal_float(graph, subj, RESPLAN.bboxMinY),
        _literal_float(graph, subj, RESPLAN.bboxMaxX),
        _literal_float(graph, subj, RESPLAN.bboxMaxY),
    ]
    bbox = bbox_vals if all(v is not None for v in bbox_vals) else None

    props: Dict[str, Any] = {}
    area = (
        _literal_float(graph, subj, RESPLAN.roomArea)
        or _literal_float(graph, subj, RESPLAN.area)
    )
    if area is not None:
        props["area"] = area
    if cx is not None and cy is not None:
        props["centroid"] = [cx, cy]
    if bbox is not None:
        props["bbox"] = bbox
    return props


def _empty_instances() -> Dict[str, Dict[str, list]]:
    return {
        "room": {key: [] for key in ROOM_KEYS},
        "structural": {key: [] for key in STRUCT_KEYS},
    }


def _room_type(graph: Graph, subj) -> Optional[str]:
    room_cls = graph.value(subj, RESPLAN.hasRoomType)
    if room_cls in ROOM_CLASS_TO_KEY:
        return ROOM_CLASS_TO_KEY[room_cls]
    # fallback: infer from rdf:type
    for _, _, cls in graph.triples((subj, RDF.type, None)):
        if cls in ROOM_CLASS_TO_KEY:
            return ROOM_CLASS_TO_KEY[cls]
    return None


def _struct_type(graph: Graph, subj) -> Optional[str]:
    for _, _, cls in graph.triples((subj, RDF.type, None)):
        if cls in STRUCT_CLASS_TO_KEY:
            return STRUCT_CLASS_TO_KEY[cls]
    return None

# ======================================================
# Calculate (approx) Inferred Door GeomJSON
# ======================================================

def infer_door_geom_from_walls_or_adjacency(
    graph,
    door,
    adj_geom_index,
    geom_index,
    door_width=0.9,
):
    def _wall_thickness(poly) -> float:
        minx, miny, maxx, maxy = poly.bounds
        return max(1e-6, min(maxx - minx, maxy - miny))

    # 1. ambil wall host
    host_walls = list(graph.subjects(RESPLAN.hostsOpening, door))
    if not host_walls:
        return None

    wall_shapes = []
    for w in host_walls[:2]:  # pakai dua wall utama
        geom_lit = geom_index.get(w)
        if geom_lit is None:
            continue
        wall_shapes.append(shape(json.loads(str(geom_lit))))

    if len(wall_shapes) >= 2:
        w1, w2 = wall_shapes[0], wall_shapes[1]
        minx1, miny1, maxx1, maxy1 = w1.bounds
        minx2, miny2, maxx2, maxy2 = w2.bounds

        overlap_x = min(maxx1, maxx2) - max(minx1, minx2)
        overlap_y = min(maxy1, maxy2) - max(miny1, miny2)

        thickness = min(_wall_thickness(w1), _wall_thickness(w2))

        if overlap_x > 0 and (miny1 > maxy2 or miny2 > maxy1):
            # Walls stacked vertically: make vertical door at mid-overlap X, spanning the gap
            x_mid = (max(minx1, minx2) + min(maxx1, maxx2)) / 2
            if miny1 > maxy2:  # w1 above w2
                y1, y2 = maxy2, miny1
            else:  # w2 above w1
                y1, y2 = maxy1, miny2
            line = LineString([(x_mid, y1), (x_mid, y2)])
            return mapping(line.buffer(thickness / 2, cap_style=2, join_style=2))

        if overlap_y > 0 and (minx1 > maxx2 or minx2 > maxx1):
            # Walls side by side horizontally: make horizontal door at mid-overlap Y
            y_mid = (max(miny1, miny2) + min(maxy1, maxy2)) / 2
            if minx1 > maxx2:  # w1 right of w2
                x1, x2 = maxx2, minx1
            else:  # w2 right of w1
                x1, x2 = maxx1, minx2
            line = LineString([(x1, y_mid), (x2, y_mid)])
            return mapping(line.buffer(thickness / 2, cap_style=2, join_style=2))

        # Fallback: shortest segment between wall polygons
        p1, p2 = nearest_points(w1, w2)
        door_line = LineString([[p1.x, p1.y], [p2.x, p2.y]])
        door_poly = door_line.buffer(thickness / 2, cap_style=2, join_style=2)
        return mapping(door_poly)

    # --- single-wall fallback menggunakan adjacency guidance ---
    chosen_wall = host_walls[0]

    # 2. cari adjacency (jika ada) untuk memandu posisi pintu
    adj_point = None
    adj = graph.value(door, RESPLAN.derivedFrom)
    if adj is not None:
        adj_geom_lit = adj_geom_index.get(adj)
        if adj_geom_lit is not None:
            adj_point = shape(json.loads(str(adj_geom_lit))).centroid

    wall_geom_lit = geom_index.get(chosen_wall)
    if wall_geom_lit is None:
        return None

    wall_poly = shape(json.loads(str(wall_geom_lit)))

    # fallback: pakai centroid wall bila adjacency tidak tersedia
    if adj_point is None and room_geom is not None:
        adj_point = room_geom.centroid
    elif adj_point is None:
        return None  # jangan asal tempatkan window

    # 3. project titik ke boundary wall
    projected = wall_poly.exterior.interpolate(
        wall_poly.exterior.project(adj_point)
    )

    # 4. tentukan orientasi door (sejajar wall)
    x, y = projected.x, projected.y
    minx, miny, maxx, maxy = wall_poly.bounds

    if (maxx - minx) > (maxy - miny):
        # wall horizontal
        p1 = (x - door_width / 2, y)
        p2 = (x + door_width / 2, y)
    else:
        # wall vertical
        p1 = (x, y - door_width / 2)
        p2 = (x, y + door_width / 2)

    line = LineString([p1, p2])
    door_poly = line.buffer(_wall_thickness(wall_poly) / 2, cap_style=2, join_style=2)
    return mapping(door_poly)

# ======================================================
# Calculate (approx) Inferred Window GeomJSON
# ======================================================

def infer_window_geom_from_wall_or_adjacency(
    graph,
    window,
    adj_geom_index,
    geom_index,
    window_width=1.2,
):
    """
    Infer window geometry using hostsOpening/room contacts.
    Priority: contact segment on primary/exterior wall; fallback to multi-wall gap; then projection.
    """

    def _wall_thickness(poly) -> float:
        minx, miny, maxx, maxy = poly.bounds
        return max(1e-6, min(maxx - minx, maxy - miny))

    def _as_shape(geom_lit):
        if geom_lit is None:
            return None
        try:
            return shape(json.loads(str(geom_lit)))
        except Exception:
            return None

    def _place_on_wall(wall_poly, room_geom, desired_len=None):
        thickness = _wall_thickness(wall_poly)
        seg_len = None
        target_point = None
        if room_geom is not None:
            contact = wall_poly.boundary.intersection(room_geom.boundary)
            segments = []
            if contact.geom_type == "LineString":
                segments = [contact]
            elif contact.geom_type == "MultiLineString":
                segments = list(contact.geoms)
            if segments:
                seg = max(segments, key=lambda s: s.length)
                seg_len = seg.length
                target_point = seg.interpolate(0.5, normalized=True)

        base_geom = room_geom if room_geom is not None else wall_poly
        if target_point is None:
            adj_point = base_geom.centroid
            target_point = wall_poly.exterior.interpolate(
                wall_poly.exterior.project(adj_point)
            )

        minx, miny, maxx, maxy = wall_poly.bounds
        horiz = (maxx - minx) >= (maxy - miny)
        ux, uy = (1, 0) if horiz else (0, 1)
        target_width = desired_len or window_width
        half = min(target_width / 2, (seg_len or target_width) / 2)
        p1 = (target_point.x - ux * half, target_point.y - uy * half)
        p2 = (target_point.x + ux * half, target_point.y + uy * half)
        line = LineString([p1, p2])
        return mapping(line.buffer(thickness / 2, cap_style=2, join_style=2))

    def _width_from_hosts(primary_poly, host_wall_ids):
        """Estimate window span using projections of other host walls onto the primary wall."""
        distances = []
        for w in host_wall_ids:
            if w == primary_wall_uri:
                continue
            other_poly = _as_shape(geom_index.get(w))
            if other_poly is None:
                continue
            p_primary, p_other = nearest_points(primary_poly, other_poly)
            distances.append(primary_poly.exterior.project(p_primary))
        if not distances:
            return None
        return max(distances) - min(distances)

    host_walls = list(graph.subjects(RESPLAN.hostsOpening, window))

    # Fallback: jika tidak ada hostsOpening, pakai wall boundedBy ruang (prioritas exterior)
    if not host_walls:
        room = graph.value(window, RESPLAN.derivedFrom)
        if room is not None:
            wall_candidates = [
                w
                for w in graph.objects(room, RESPLAN.boundedBy)
                if (w, RDF.type, RESPLAN.ExteriorWall) in graph
                or (w, RDF.type, RESPLAN.InteriorWall) in graph
            ]
            ext_walls = [
                w for w in wall_candidates if (w, RDF.type, RESPLAN.ExteriorWall) in graph
            ]
            host_walls = ext_walls or wall_candidates

    if not host_walls:
        return None

    derived = graph.value(window, RESPLAN.derivedFrom)
    room_geom = _as_shape(geom_index.get(derived)) if derived is not None else None
    primary_wall_uri = graph.value(window, RESPLAN.primaryWall)

    # Jika primary wall ada, pakai geometri itu dahulu supaya orientasi window sejajar primary.
    if primary_wall_uri is not None:
        primary_wall_poly = _as_shape(geom_index.get(primary_wall_uri))
        if primary_wall_poly is not None:
            desired = _width_from_hosts(primary_wall_poly, host_walls)
            placed = _place_on_wall(primary_wall_poly, room_geom, desired_len=desired)
            if placed is not None:
                return placed

    wall_shapes = []
    for w in host_walls:
        poly = _as_shape(geom_index.get(w))
        if poly is not None:
            is_ext = (w, RDF.type, RESPLAN.ExteriorWall) in graph
            is_primary = primary_wall_uri is not None and w == primary_wall_uri
            wall_shapes.append((w, poly, is_ext, is_primary))

    if not wall_shapes:
        return None

    # Prioritas: segmen kontak wall-room (room dibuffer setengah ketebalan wall)
    best_seg = None
    best_thick = None
    best_is_ext = False
    best_is_primary = False
    best_wall_poly = None
    if room_geom is not None:
        for _, wall_poly, is_ext, is_primary in wall_shapes:
            thickness = _wall_thickness(wall_poly)
            buffered = room_geom.buffer(thickness / 2, join_style=2, cap_style=2)
            inter = wall_poly.boundary.intersection(buffered.boundary)
            segments = []
            if inter.geom_type == "LineString":
                segments = [inter]
            elif inter.geom_type == "MultiLineString":
                segments = list(inter.geoms)
            for seg in segments:
                if seg.length <= 1e-6:
                    continue
                better = False
                if best_seg is None:
                    better = True
                else:
                    if is_primary and not best_is_primary:
                        better = True
                    elif is_primary == best_is_primary:
                        if is_ext and not best_is_ext:
                            better = True
                        elif is_ext == best_is_ext and seg.length > best_seg.length:
                            better = True
                if better:
                    best_seg = seg
                    best_thick = thickness
                    best_is_ext = is_ext
                    best_is_primary = is_primary
                    best_wall_poly = wall_poly

    if best_seg is not None:
        start, end = best_seg.coords[0], best_seg.coords[-1]
        midx = (start[0] + end[0]) / 2
        midy = (start[1] + end[1]) / 2
        # Gunakan orientasi wall utama agar window sejajar wall,
        # bahkan bila segmen intersection tegak lurus.
        if best_wall_poly is not None:
            minx, miny, maxx, maxy = best_wall_poly.bounds
            horiz = (maxx - minx) >= (maxy - miny)
        else:
            dx, dy = end[0] - start[0], end[1] - start[1]
            horiz = abs(dx) >= abs(dy)
        ux, uy = (1, 0) if horiz else (0, 1)
        half = min(window_width / 2, best_seg.length / 2)
        p1 = (midx - ux * half, midy - uy * half)
        p2 = (midx + ux * half, midy + uy * half)
        line = LineString([p1, p2])
        win_poly = line.buffer(best_thick / 2, cap_style=2, join_style=2)
        return mapping(win_poly)

    # Jika ada >=2 wall host: posisikan di celah antar wall
    if len(wall_shapes) >= 2:
        best_pair = None
        best_dist = None
        for i in range(len(wall_shapes)):
            for j in range(i + 1, len(wall_shapes)):
                p1, p2 = nearest_points(wall_shapes[i][1], wall_shapes[j][1])
                dist = p1.distance(p2)
                if best_dist is None or dist < best_dist:
                    best_dist = dist
                    best_pair = (p1, p2, wall_shapes[i][1], wall_shapes[j][1])
        if best_pair is not None and best_dist is not None and best_dist > 1e-6:
            p1, p2, ws1, ws2 = best_pair
            thickness = min(_wall_thickness(ws1), _wall_thickness(ws2))
            line = LineString([[p1.x, p1.y], [p2.x, p2.y]])
            win_poly = line.buffer(thickness / 2, cap_style=2, join_style=2)
            return mapping(win_poly)

    # Single wall host: proyeksi centroid adjacency/room ke wall
    wall_poly = wall_shapes[0][1]
    thickness = _wall_thickness(wall_poly)

    adj_point = None
    adj = graph.value(window, RESPLAN.derivedFrom)
    if adj is not None:
        adj_geom_lit = adj_geom_index.get(adj)
        if adj_geom_lit is None:
            adj_geom_lit = geom_index.get(adj)
        adj_geom = _as_shape(adj_geom_lit)
        if adj_geom is not None:
            adj_point = adj_geom.centroid

    if adj_point is None:
        adj_point = wall_poly.centroid if room_geom is None else room_geom.centroid

    projected = wall_poly.exterior.interpolate(
        wall_poly.exterior.project(adj_point)
    )

    minx, miny, maxx, maxy = wall_poly.bounds
    horiz = (maxx - minx) >= (maxy - miny)

    if horiz:
        p1 = (projected.x - window_width / 2, projected.y)
        p2 = (projected.x + window_width / 2, projected.y)
    else:
        p1 = (projected.x, projected.y - window_width / 2)
        p2 = (projected.x, projected.y + window_width / 2)

    win_poly = LineString([p1, p2]).buffer(
        thickness / 2, cap_style=2, join_style=2
    )
    return mapping(win_poly)

# ======================================================
# Core conversion
# ======================================================
def ttl_to_plan_dict(ttl_path: str | Path) -> Dict[str, Any]:
    ttl_path = Path(ttl_path)
    graph = Graph()
    graph.parse(ttl_path)

    # --------------------------------------------------
    # Geometry caches (IMPORTANT for inferred elements)
    # --------------------------------------------------
    geom_index = {
        subj: geom
        for subj, geom in graph.subject_objects(RESPLAN.geomJSON)
    }

    adj_geom_index = {
        adj: geom
        for adj, geom in graph.subject_objects(RESPLAN.geomJSON)
        if (adj, RDF.type, RESPLAN.AdjacencyEdge) in graph
    }

    plan_dict: Dict[str, Any] = {
        "metadata": {},
        "instances": _empty_instances(),
    }

    # --------------------------------------------------
    # Plan metadata
    # --------------------------------------------------
    plan_node = next(graph.subjects(RDF.type, RESPLAN.ResPlan), None)
    if plan_node:
        label = graph.value(plan_node, RDFS.label)
        if label:
            plan_dict["metadata"]["plan_label"] = str(label)

        for pred, key in (
            (RESPLAN.planArea, "area"),
            (RESPLAN.netArea, "net_area"),
        ):
            val = _literal_float(graph, plan_node, pred)
            if val is not None:
                plan_dict["metadata"][key] = val

        unit_type = graph.value(plan_node, RESPLAN.unitType)
        if unit_type:
            plan_dict["metadata"]["unitType"] = str(unit_type)

    # --------------------------------------------------
    # Rooms
    # --------------------------------------------------
    for subj in graph.subjects(RDF.type, BOT.Space):
        geom_lit = graph.value(subj, RESPLAN.geomJSON)
        if geom_lit is None:
            continue

        room_key = _room_type(graph, subj)
        if room_key is None:
            continue

        record = {
            "id": _local_id(subj),
            "type": room_key,
            "geom": json.loads(str(geom_lit)),
            "props": _geom_props(graph, subj),
        }

        plan_dict["instances"]["room"].setdefault(room_key, []).append(record)

    # --------------------------------------------------
    # Structural elements (walls, doors, windows)
    # --------------------------------------------------
    for struct_subj in set(graph.subjects(RDF.type, None)):

        struct_key = _struct_type(graph, struct_subj)
        if struct_key is None or struct_key not in STRUCT_KEYS:
            continue

        # --- geometry resolution ---
        own_geom_lit = graph.value(struct_subj, RESPLAN.geomJSON)
        geom_lit = own_geom_lit

        # fallback 1: replacesWall
        if geom_lit is None:
            replaced = graph.value(struct_subj, RESPLAN.replacesWall)
            if replaced:
                geom_lit = geom_index.get(replaced)

        # fallback 2: derivedFrom adjacency
        if geom_lit is None:
            derived = graph.value(struct_subj, RESPLAN.derivedFrom)
            if derived:
                geom_lit = adj_geom_index.get(derived)

        # --------------------------------------------------
        # Infer Door Geometry
        # --------------------------------------------------

        if struct_key == "door" and own_geom_lit is None:
            geom_lit = infer_door_geom_from_walls_or_adjacency(
                graph,
                struct_subj,
                adj_geom_index,
                geom_index,
            )

        # --------------------------------------------------
        # Infer Window Geometry
        # --------------------------------------------------

        if struct_key == "window" and own_geom_lit is None:
            geom_lit = infer_window_geom_from_wall_or_adjacency(
                graph,
                struct_subj,
                adj_geom_index,
                geom_index,
            )

        # --------------------------------------------------

        if geom_lit is None:
            continue  # cannot visualize

        record_id = (
            graph.value(struct_subj, RESPLAN.sourceId)
            or _local_id(struct_subj)
        )

        is_inferred_lit = graph.value(struct_subj, RESPLAN.isInferred)

        # geom_lit can be a JSON literal from the TTL or a dict returned by
        # infer_door_geom_from_walls_or_adjacency; support both.
        geom = (
            geom_lit
            if isinstance(geom_lit, dict)
            else json.loads(str(geom_lit))
        )

        inferred_flag = (
            is_inferred_lit.toPython()
            if is_inferred_lit is not None
            else ("infer#" in str(struct_subj))
        )

        record = {
            "id": str(record_id),
            "type": struct_key,
            "geom": geom,
            "props": _geom_props(graph, struct_subj),
            "inferred": inferred_flag,
        }

        plan_dict["instances"]["structural"].setdefault(
            struct_key, []
        ).append(record)

    return plan_dict


# ======================================================
# Save helper
# ======================================================
def save_ttl_as_json(
    ttl_path: str | Path,
    output_path: str | Path | None = None
) -> Path:
    plan_dict = ttl_to_plan_dict(ttl_path)
    output_path = (
        Path(output_path)
        if output_path
        else Path(ttl_path).with_suffix(".json")
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(plan_dict, indent=2),
        encoding="utf-8",
    )
    return output_path


__all__ = ["ttl_to_plan_dict", "save_ttl_as_json"]
