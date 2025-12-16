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

from shapely.geometry import LineString, shape, mapping
from shapely.ops import nearest_points

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
    if adj_point is None:
        adj_point = wall_poly.centroid

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

# import json
# from pathlib import Path
# from rdflib import Graph, Namespace, RDF
# from decimal import Decimal

# # =========================
# # NAMESPACES
# # =========================
# RESPLAN = Namespace("http://resplan.org/resplan#")
# BOT     = Namespace("https://w3id.org/bot#")
# IFC     = Namespace("https://w3id.org/ifc/IFC4_ADD2#")

# # =========================
# # HELPERS
# # =========================
# def short(uri):
#     s = str(uri)
#     if "#" in s:
#         return s.split("#")[-1]
#     return s.rstrip("/").split("/")[-1]

# def lit(v):
#     if v is None:
#         return None
#     try:
#         return v.toPython()
#     except Exception:
#         return str(v)

# def json_default(o):
#     if isinstance(o, Decimal):
#         return float(o)
#     raise TypeError(f"Object of type {type(o)} is not JSON serializable")

# # =========================
# # MAIN FUNCTION
# # =========================
# def ttl_to_json(ttl_input, json_output):
#     ttl_input = Path(ttl_input)
#     json_output = Path(json_output)

#     g = Graph()
#     g.parse(ttl_input, format="turtle")
#     print("Loaded triples:", len(g))

#     # --------------------------------------------------
#     # ROOMS (GROUPED BY ROOM TYPE)
#     # --------------------------------------------------
#     rooms_by_type = {}

#     for r in g.subjects(RDF.type, RESPLAN.Room):
#         geom_lit = g.value(r, RESPLAN.geomJSON)
#         rtype = g.value(r, RESPLAN.hasRoomType)

#         if not geom_lit or not rtype:
#             continue

#         try:
#             geom_json = json.loads(str(geom_lit))
#         except Exception:
#             continue

#         room_obj = {
#             "id": short(r),
#             "geom": geom_json,
#             "area": lit(g.value(r, RESPLAN.area)),
#         }

#         subtype = short(rtype)
#         rooms_by_type.setdefault(subtype, []).append(room_obj)

#     print("Rooms exported:", sum(len(v) for v in rooms_by_type.values()))

#     # --------------------------------------------------
#     # WALLS (INTERIOR + EXTERIOR + INFERRED)
#     # --------------------------------------------------
#     walls = []

#     for w in g.subjects(RDF.type, IFC.Wall):
#         geom_lit = g.value(w, RESPLAN.geomJSON)
#         if not geom_lit:
#             continue

#         try:
#             geom_json = json.loads(str(geom_lit))
#         except Exception:
#             continue

#         wall_obj = {
#             "id": short(w),
#             "geom": geom_json,
#             "inferred": bool(lit(g.value(w, RESPLAN.isInferred))),
#             "spaces": sorted({
#                 short(s)
#                 for s in g.objects(w, RESPLAN.separatesSpace)
#             }),
#         }

#         # optional metadata (keep round-trip info)
#         for p, k in [
#             (RESPLAN.wallDepth, "depth"),
#             (RESPLAN.length, "length"),
#             (RESPLAN.area, "area"),
#             (RESPLAN.sourceId, "sourceId"),
#         ]:
#             val = g.value(w, p)
#             if val is not None:
#                 wall_obj[k] = lit(val)

#         walls.append(wall_obj)

#     print("Walls exported:", len(walls))

#     # --------------------------------------------------
#     # DOORS
#     # --------------------------------------------------
#     doors = []

#     for d in g.subjects(RDF.type, RESPLAN.Door):
#         geom_lit = g.value(d, RESPLAN.geomJSON)
#         if not geom_lit:
#             continue

#         try:
#             geom_json = json.loads(str(geom_lit))
#         except Exception:
#             continue

#         doors.append({
#             "id": short(d),
#             "geom": geom_json,
#             "spaces": sorted({
#                 short(s)
#                 for s in g.objects(d, RESPLAN.connectsSpace)
#             })
#         })

#     print("Doors exported:", len(doors))

#     # --------------------------------------------------
#     # WINDOWS
#     # --------------------------------------------------
#     windows = []

#     for w in g.subjects(RDF.type, RESPLAN.Window):
#         geom_lit = g.value(w, RESPLAN.geomJSON)
#         if not geom_lit:
#             continue

#         try:
#             geom_json = json.loads(str(geom_lit))
#         except Exception:
#             continue

#         windows.append({
#             "id": short(w),
#             "geom": geom_json
#         })

#     print("Windows exported:", len(windows))

#     # --------------------------------------------------
#     # FINAL JSON (⚠️ SAME CONTRACT AS OLD plan_00000.json)
#     # --------------------------------------------------
#     output = {
#         "instances": {
#             "room": rooms_by_type,
#             "structural": {
#                 "wall": walls,
#                 "door": doors,
#                 "window": windows
#             }
#         }
#     }

#     json_output.parent.mkdir(parents=True, exist_ok=True)
#     with open(json_output, "w", encoding="utf-8") as f:
#         json.dump(output, f, indent=2, default=json_default)

#     print("Saved JSON to:", json_output)


# # =========================
# # CLI ENTRY POINT
# # =========================
# if __name__ == "__main__":
#     ttl_to_json(
#         "../output/inferred_resplan_ttl/plan_00000_walls_back.ttl",
#         "../output/inferred_resplan_json/plan_00000_walls_back.json"
#     )
