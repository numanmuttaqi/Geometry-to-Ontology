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
from shapely.geometry import LineString, shape, mapping, Point
from shapely.ops import nearest_points
from shapely import box

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
# Calculate (approx) Inferred Interior Wall GeomJSON
# ======================================================

import json
from shapely.geometry import shape, mapping, LineString, MultiLineString, GeometryCollection
from shapely.ops import linemerge, unary_union

def infer_interior_wall_geom(
    graph,
    wall,
    adj_geom_index,
    geom_index,
    default_thickness=0.19,
    min_seg_len=0.05,
    exterior_overlap_thresh=0.90,
    tol=1e-6,
):
    """
    Infer interior wall polygon when explicit geomJSON is absent.

    Key fixes:
    - If adjacency/shared boundary ends up being *exterior boundary*, skip (avoid duplicating exterior walls).
    - Robust extraction of shared boundary segments (handles GeometryCollection).
    - Merge + pick longest segment, then buffer with square caps for clean wall rectangles.
    """

    # -------------------------
    # Helpers
    # -------------------------
    def _load_shape(val):
        """val can be rdflib Literal containing GeoJSON str, or a dict (already geojson)."""
        if val is None:
            return None
        if isinstance(val, dict):
            try:
                return shape(val)
            except Exception:
                return None
        try:
            return shape(json.loads(str(val)))
        except Exception:
            return None

    def _as_lines(geom):
        if geom is None or geom.is_empty:
            return []
        if isinstance(geom, LineString):
            return [geom]
        if isinstance(geom, MultiLineString):
            return list(geom.geoms)
        if isinstance(geom, GeometryCollection):
            out = []
            for g in geom.geoms:
                out.extend(_as_lines(g))
            return out
        return []

    def _overlap_ratio(a, b):
        """Area overlap ratio relative to smaller polygon."""
        if a is None or b is None or a.is_empty or b.is_empty:
            return 0.0
        inter = a.intersection(b)
        if inter.is_empty:
            return 0.0
        denom = min(a.area, b.area)
        if denom <= tol:
            return 0.0
        return inter.area / denom

    # -------------------------
    # Strategy 0: already has geom
    # -------------------------
    existing = _load_shape(geom_index.get(wall))
    if existing is not None and (not existing.is_empty) and existing.geom_type in {"Polygon", "MultiPolygon"}:
        return mapping(existing)

    # Pre-collect exterior wall polygons (to prevent accidental duplication)
    # (string-based fallback if rdf:type not available in your graph for some reason)
    exterior_polys = []
    try:
        for w_ex in graph.subjects(RDF.type, RESPLAN.ExteriorWall):
            shp = _load_shape(geom_index.get(w_ex))
            if shp is not None and (not shp.is_empty) and shp.geom_type in {"Polygon", "MultiPolygon"}:
                exterior_polys.append(shp)
    except Exception:
        # If RDF/RESPLAN not in scope here, keep list empty; function still works.
        pass

    # -------------------------
    # Strategy 1: adjacency-derived geometry (BUT guard against exterior duplication)
    # -------------------------
    derived = graph.value(wall, RESPLAN.derivedFrom)
    if derived:
        adj_shp = _load_shape(adj_geom_index.get(derived))
        if adj_shp is not None and (not adj_shp.is_empty) and adj_shp.geom_type in {"Polygon", "MultiPolygon"}:
            # Reject if it basically duplicates an exterior wall polygon
            for ex_poly in exterior_polys:
                if _overlap_ratio(adj_shp, ex_poly) >= exterior_overlap_thresh:
                    adj_shp = None
                    break
            if adj_shp is not None:
                return mapping(adj_shp)

    # -------------------------
    # Strategy 2: derive from shared boundary between two spaces
    # -------------------------
    if derived is not None:
        s1 = graph.value(derived, RESPLAN.spaceA)
        s2 = graph.value(derived, RESPLAN.spaceB)

        g1 = _load_shape(geom_index.get(s1))
        g2 = _load_shape(geom_index.get(s2))

        if g1 is not None and g2 is not None and (not g1.is_empty) and (not g2.is_empty):
            shared = g1.boundary.intersection(g2.boundary)
            lines = [l for l in _as_lines(shared) if l.length >= min_seg_len]

            if lines:
                # merge fragments, then take longest
                merged = linemerge(unary_union(lines))
                merged_lines = _as_lines(merged)
                if not merged_lines:
                    merged_lines = lines

                longest = max(merged_lines, key=lambda x: x.length)

                # IMPORTANT GUARD:
                # If the "shared" segment lies on the exterior boundary of (g1 ∪ g2),
                # it is not an interior separator -> skip.
                u = g1.union(g2)
                inter_len = longest.intersection(u.boundary).length
                if longest.length > tol and (inter_len / longest.length) > 0.8:
                    return None

                wall_poly = longest.buffer(
                    default_thickness / 2.0,
                    cap_style=3,   # square caps
                    join_style=2,  # mitre joins
                )

                if wall_poly.is_empty or wall_poly.area <= tol:
                    return None

                # Reject if it duplicates an exterior wall polygon
                for ex_poly in exterior_polys:
                    if _overlap_ratio(wall_poly, ex_poly) >= exterior_overlap_thresh:
                        return None

                return mapping(wall_poly)

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
    """Infer a door polygon when explicit geomJSON is absent."""
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
# Calculate (approx) Inferred Window GeomJSON
# ======================================================


def infer_window_geom_from_primary_and_hosts(
    graph,
    window,
    geom_index,
):
    """Infer a window polygon based on primary and secondary walls."""
    # --- primary wall ---
    primary = graph.value(window, RESPLAN.primaryWall)
    if primary is None:
        return None

    P = shape(json.loads(str(geom_index.get(primary))))

    # --- secondary wall (HARUS 1) ---
    hosts = [
        w for w in graph.subjects(RESPLAN.hostsOpening, window)
        if w != primary
    ]
    if len(hosts) != 1:
        return None

    S = shape(json.loads(str(geom_index.get(hosts[0]))))

    # --- orientation ---
    minx, miny, maxx, maxy = P.bounds
    width, height = maxx - minx, maxy - miny
    is_horizontal = width >= height
    thickness = min(width, height)

    # --- primary axis ---
    if is_horizontal:
        midy = (miny + maxy) / 2
        axis = LineString([(minx, midy), (maxx, midy)])
    else:
        midx = (minx + maxx) / 2
        axis = LineString([(midx, miny), (midx, maxy)])

    # --- anchor = ujung axis terdekat ke secondary ---
    a0 = Point(axis.coords[0])
    a1 = Point(axis.coords[-1])
    anchor = a0 if a0.distance(S) < a1.distance(S) else a1

    # --- length = jarak ke secondary ---
    p_anchor, p_sec = nearest_points(anchor, S)
    length = max(0.6, min(anchor.distance(p_sec), 2.4))

    # --- build window ---
    if is_horizontal:
        direction = 1 if p_sec.x > anchor.x else -1
        p1 = (anchor.x, anchor.y)
        p2 = (anchor.x + direction * length, anchor.y)
    else:
        direction = 1 if p_sec.y > anchor.y else -1
        p1 = (anchor.x, anchor.y)
        p2 = (anchor.x, anchor.y + direction * length)

    line = LineString([p1, p2])
    return mapping(
        line.buffer(thickness / 2, cap_style=2, join_style=2)
    )

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
            geom_lit = infer_window_geom_from_primary_and_hosts(
                graph,
                struct_subj,
                geom_index,
            )

        # --------------------------------------------------
        # Infer Interior Wall Geometry
        # --------------------------------------------------
        if struct_key == "interior_wall" and own_geom_lit is None and geom_lit is None:
            geom_lit = infer_interior_wall_geom(
                graph,
                struct_subj,
                adj_geom_index,
                geom_index,
            )

        # --------------------------------------------------

        if geom_lit is None:
            continue  # cannot visualize

        is_inferred_lit = graph.value(struct_subj, RESPLAN.isInferred)

        # geom_lit can be a JSON literal from the TTL or a dict returned by
        # infer_door_geom_from_walls_or_adjacency; support both.
        geom = (
            geom_lit
            if isinstance(geom_lit, dict)
            else json.loads(str(geom_lit))
        )

        # Normalize wall geometries: buffer lines into polygons for visibility
        if struct_key in {"interior_wall", "exterior_wall"}:
            try:
                shp = shape(geom)
                if shp.geom_type in {"LineString", "MultiLineString"}:
                    shp = shp.buffer(0.12 / 2, cap_style=2, join_style=2)
                geom = mapping(shp)
            except Exception:
                pass

        inferred_flag = (
            is_inferred_lit.toPython()
            if is_inferred_lit is not None
            else ("infer#" in str(struct_subj))
        )

        record_id = (
            _local_id(struct_subj)
            if inferred_flag
            else (graph.value(struct_subj, RESPLAN.sourceId) or _local_id(struct_subj))
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
