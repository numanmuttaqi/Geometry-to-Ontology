"""
Helpers to reconstruct the original plan JSON structure from a Turtle file.

UPDATED VERSION: Fixed door avoidance with proper splitting
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional

from rdflib import Graph, Namespace, RDF
from rdflib.namespace import RDFS
from shapely.geometry import LineString, shape, mapping, Point, MultiLineString, GeometryCollection, box
from shapely.ops import nearest_points, linemerge, unary_union

from .constants import ROOM_KEYS, STRUCT_KEYS

# ======================================================
# Namespaces
# ======================================================
RESPLAN = Namespace("http://resplan.org/resplan#")
BOT = Namespace("https://w3id.org/bot#")

OUTSIDE_ID = "OUT-0000"

# ======================================================
# Reverse mappings
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
    for _, _, cls in graph.triples((subj, RDF.type, None)):
        if cls in ROOM_CLASS_TO_KEY:
            return ROOM_CLASS_TO_KEY[cls]
    return None


def _struct_type(graph: Graph, subj) -> Optional[str]:
    for _, _, cls in graph.triples((subj, RDF.type, None)):
        if cls in STRUCT_CLASS_TO_KEY:
            return STRUCT_CLASS_TO_KEY[cls]
    return None

def _is_empty_geom(geom_lit):
    """Check if geometry literal is None or has empty coordinates"""
    if geom_lit is None:
        return True
    if isinstance(geom_lit, dict):
        coords = geom_lit.get('coordinates', [])
        return not coords or coords == []
    try:
        geom_dict = json.loads(str(geom_lit))
        coords = geom_dict.get('coordinates', [])
        return not coords or coords == []
    except:
        return True


# ======================================================
# WALL INFERENCE - FIXED VERSION
# ======================================================
def infer_interior_wall_GENERAL(
    graph,
    wall,
    adj_geom_index,
    geom_index,
    default_thickness=0.1897,
):
    """
    FIXED: Proper door avoidance without polygon carving artifacts.
    
    Changes:
    - Split at LINE level only (keeps full wall length visible)
    - NO carving at polygon level (prevents weird shapes)
    - Smaller buffer for doors (0.15m vs 0.30m)
    - Keep ALL segments (even small ones for visibility)
    """
    
    def _load_shape(val):
        if val is None:
            return None
        if isinstance(val, dict):
            try:
                return shape(val)
            except:
                return None
        try:
            return shape(json.loads(str(val)))
        except:
            return None

    def _extract_segments(poly):
        """Extract axis-aligned boundary segments."""
        segments = []
        coords = list(poly.exterior.coords)
        for i in range(len(coords) - 1):
            x1, y1 = coords[i]
            x2, y2 = coords[i + 1]
            dx, dy = abs(x2 - x1), abs(y2 - y1)
            if dx < 1e-6 and dy < 1e-6:
                continue
            if dx > dy:
                segments.append({
                    "orientation": "HORIZONTAL",
                    "minx": min(x1, x2),
                    "maxx": max(x1, x2),
                    "y": y1,
                })
            elif dy > dx:
                segments.append({
                    "orientation": "VERTICAL",
                    "x": x1,
                    "miny": min(y1, y2),
                    "maxy": max(y1, y2),
                })
        return segments

    def _candidate_gap_segments(polyA, polyB, max_gap=0.35, min_overlap=0.25):
        """Find gap segments between two polygons."""
        segA = _extract_segments(polyA)
        segB = _extract_segments(polyB)
        candidates = []

        for a in segA:
            for b in segB:
                if a["orientation"] != b["orientation"]:
                    continue

                if a["orientation"] == "HORIZONTAL":
                    overlap = min(a["maxx"], b["maxx"]) - max(a["minx"], b["minx"])
                    gap = abs(a["y"] - b["y"])
                    if overlap >= min_overlap and 0 < gap <= max_gap:
                        y_mid = (a["y"] + b["y"]) / 2.0
                        x_start = max(a["minx"], b["minx"])
                        x_end = min(a["maxx"], b["maxx"])
                        candidates.append((
                            "HORIZONTAL",
                            LineString([(x_start, y_mid), (x_end, y_mid)]),
                            overlap,
                            gap,
                        ))
                else:
                    overlap = min(a["maxy"], b["maxy"]) - max(a["miny"], b["miny"])
                    gap = abs(a["x"] - b["x"])
                    if overlap >= min_overlap and 0 < gap <= max_gap:
                        x_mid = (a["x"] + b["x"]) / 2.0
                        y_start = max(a["miny"], b["miny"])
                        y_end = min(a["maxy"], b["maxy"])
                        candidates.append((
                            "VERTICAL",
                            LineString([(x_mid, y_start), (x_mid, y_end)]),
                            overlap,
                            gap,
                        ))

        unique = []
        seen = set()
        for ori, line, overlap, gap in sorted(candidates, key=lambda t: (t[0], -t[2], -t[3])):
            key = tuple(round(v, 3) for v in line.bounds)
            if key in seen:
                continue
            seen.add(key)
            unique.append((ori, line))
        return unique

    def _adjacency_group(space_a, space_b, current_adj):
        """Return ordered adjacency list."""
        adjs = []
        for adj in graph.subjects(RDF.type, RESPLAN.AdjacencyEdge):
            sa = graph.value(adj, RESPLAN.spaceA)
            sb = graph.value(adj, RESPLAN.spaceB)
            shared_wall = graph.value(adj, RESPLAN.sharedWall)
            if shared_wall:
                sw_id = _local_id(shared_wall)
                if sw_id.startswith("EX-"):
                    continue
            if {sa, sb} == {space_a, space_b}:
                adjs.append(adj)
        adjs = sorted(adjs, key=lambda u: str(u))
        idx = adjs.index(current_adj) if current_adj in adjs else 0
        return adjs, idx
    
    # Get adjacency and spaces
    derived = graph.value(wall, RESPLAN.derivedFrom)
    if not derived:
        return None
    
    spaceA = graph.value(derived, RESPLAN.spaceA)
    spaceB = graph.value(derived, RESPLAN.spaceB)
    
    if not spaceA or not spaceB:
        return None
    
    geomA = _load_shape(geom_index.get(spaceA))
    geomB = _load_shape(geom_index.get(spaceB))
    
    if not geomA or not geomB or geomA.is_empty or geomB.is_empty:
        return None
    
    spaceA_id = str(spaceA).split("#")[-1]
    spaceB_id = str(spaceB).split("#")[-1]
    wall_id = str(wall).split("#")[-1]
    
    # Get bounding boxes
    minxA, minyA, maxxA, maxyA = geomA.bounds
    minxB, minyB, maxxB, maxyB = geomB.bounds
    
    # Calculate overlaps and gaps
    x_overlap = min(maxxA, maxxB) - max(minxA, minxB)
    y_overlap = min(maxyA, maxyB) - max(minyA, minyB)
    x_gap = max(0, max(minxA, minxB) - min(maxxA, maxxB))
    y_gap = max(0, max(minyA, minyB) - min(maxyA, maxyB))
    
    wall_line = None
    orientation = None
    
    # =========================================================================
    # WALL LINE DETERMINATION
    # =========================================================================
    candidate_lines = _candidate_gap_segments(
        geomA, geomB,
        max_gap=max(default_thickness * 2.0, 0.30),
        min_overlap=0.25,
    )
    adjacencies, adj_idx = _adjacency_group(spaceA, spaceB, derived)
    if candidate_lines:
        pick_idx = min(adj_idx, len(candidate_lines) - 1)
        orientation, wall_line = candidate_lines[pick_idx]
    
    if wall_line is None:
        if x_overlap > 0.5 and (y_gap > 0.05 or y_overlap < 0.5):
            x_start = max(minxA, minxB)
            x_end = min(maxxA, maxxB)
            if minyA > maxyB:
                y_wall = (minyA + maxyB) / 2
            elif minyB > maxyA:
                y_wall = (minyB + maxyA) / 2
            else:
                y_wall = (max(minyA, minyB) + min(maxyA, maxyB)) / 2
            wall_line = LineString([(x_start, y_wall), (x_end, y_wall)])
            orientation = "HORIZONTAL"
        
        elif y_overlap > 0.5 and (x_gap > 0.05 or x_overlap < 0.5):
            y_start = max(minyA, minyB)
            y_end = min(maxyA, maxyB)
            if minxA > maxxB:
                x_wall = (minxA + maxxB) / 2
            elif minxB > maxxA:
                x_wall = (minxB + maxxA) / 2
            else:
                x_wall = (max(minxA, minxB) + min(maxxA, maxxB)) / 2
            wall_line = LineString([(x_wall, y_start), (x_wall, y_end)])
            orientation = "VERTICAL"
        
        elif x_overlap > 0.5 and y_overlap > 0.5:
            adj_geom_lit = adj_geom_index.get(derived)
            adj_geom = _load_shape(adj_geom_lit)
            if adj_geom and not adj_geom.is_empty:
                if adj_geom.geom_type == "LineString":
                    wall_line = adj_geom
                    orientation = "L-CORNER-ADJ"
                elif adj_geom.geom_type == "Polygon":
                    wall_line = adj_geom.exterior
                    orientation = "L-CORNER-ADJ"
            if not wall_line:
                p1, p2 = nearest_points(geomA, geomB)
                if p1.distance(p2) < 0.5:
                    wall_line = LineString([p1, p2])
                    orientation = "L-CORNER"
        
        else:
            p1, p2 = nearest_points(geomA, geomB)
            if p1.distance(p2) < 0.5:
                wall_line = LineString([p1, p2])
                orientation = "NEAREST"
    
    if not wall_line or wall_line.is_empty or wall_line.length < 0.3:
        return None
    
    # =========================================================================
    # DOOR DETECTION AND SPLITTING - FIXED!
    # =========================================================================
    
    def _get_openings_between_rooms(space_a, space_b):
        """Find doors/windows via adjacency + sharedWall + hostsOpening."""
        openings = []
        
        space_a_id = str(space_a).split("#")[-1]
        space_b_id = str(space_b).split("#")[-1]
        
        # Find adjacency edges
        adjacencies = []
        for adj in graph.subjects(RDF.type, RESPLAN.AdjacencyEdge):
            spaceA = graph.value(adj, RESPLAN.spaceA)
            spaceB = graph.value(adj, RESPLAN.spaceB)
            if {spaceA, spaceB} == {space_a, space_b}:
                shared_wall = graph.value(adj, RESPLAN.sharedWall)
                if shared_wall:
                    adjacencies.append((adj, shared_wall))
        
        if not adjacencies:
            return openings
        
        # Get hosted openings
        for adj, shared_wall in adjacencies:
            wall_id = str(shared_wall).split("#")[-1]
            hosted_openings = list(graph.objects(shared_wall, RESPLAN.hostsOpening))
            
            if not hosted_openings:
                continue
            
            for opening_uri in hosted_openings:
                opening_id = str(opening_uri).split("#")[-1]
                opening_geom = _load_shape(geom_index.get(opening_uri))
                
                if not opening_geom or opening_geom.is_empty:
                    continue
                
                # Check type
                is_door = (opening_uri, RDF.type, RESPLAN.Door) in graph or \
                         (opening_uri, RDF.type, RESPLAN.FrontDoor) in graph
                is_window = (opening_uri, RDF.type, RESPLAN.Window) in graph
                
                # Convert LineString to polygon
                if opening_geom.geom_type == 'LineString':
                    if is_door:
                        opening_geom = opening_geom.buffer(0.45, cap_style=3)
                    else:
                        opening_geom = opening_geom.buffer(0.30, cap_style=3)
                
                # SMALLER buffer - 15cm instead of 30cm!
                blocking_buffer = 0.15 if is_door else 0.12
                opening_blocking = opening_geom.buffer(blocking_buffer)
                
                # NO extended blocking! Keep it simple
                openings.append(opening_blocking)
        
        return openings
    
    def _split_wall_by_openings(wall_line, openings):
        """Split wall line, keeping ALL segments (even small ones)."""
        if not openings:
            return [wall_line]
        
        openings_union = unary_union(openings)
        
        try:
            result = wall_line.difference(openings_union)
            
            segments = []
            if result.is_empty:
                return []
            elif result.geom_type == 'LineString':
                # Keep ALL segments, no minimum!
                segments.append(result)
            elif result.geom_type == 'MultiLineString':
                for line in result.geoms:
                    # Keep ALL segments!
                    segments.append(line)
            elif result.geom_type == 'GeometryCollection':
                for geom in result.geoms:
                    if geom.geom_type == 'LineString':
                        segments.append(geom)
            
            return segments
        except:
            return [wall_line]
    
    # Detect and split
    openings = _get_openings_between_rooms(spaceA, spaceB)
    
    if openings:
        wall_segments = _split_wall_by_openings(wall_line, openings)
    else:
        wall_segments = [wall_line]
    
    if not wall_segments:
        return None
    
    # Merge and buffer - NO CARVING!
    merged = linemerge(wall_segments)
    
    wall_poly = merged.buffer(
        default_thickness / 2.0,
        cap_style=3,  # square caps
        join_style=2,  # mitre joins
    )
    
    # NO CARVING AT POLYGON LEVEL!
    # This was causing the weird shapes!
    
    if wall_poly.is_empty or wall_poly.area < 0.001:
        return None
    
    # Log
    total_length = sum(seg.length for seg in wall_segments) if isinstance(wall_segments, list) else wall_segments.length
    print(f" {wall_id} ({spaceA_id}↔{spaceB_id}): {orientation}, L={total_length:.3f}m, {len(openings)} openings")
    
    return mapping(wall_poly)


# ======================================================
# Other inference functions (unchanged)
# ======================================================

def infer_interior_wall_geom(graph, wall, adj_geom_index, geom_index, default_thickness=0.19, **kwargs):
    return infer_interior_wall_GENERAL(graph, wall, adj_geom_index, geom_index, default_thickness)


def infer_door_geom_from_walls_or_adjacency(graph, door, adj_geom_index, geom_index):
    def _load_shape(val):
        if val is None:
            return None
        if isinstance(val, dict):
            try:
                return shape(val)
            except:
                return None
        try:
            return shape(json.loads(str(val)))
        except:
            return None
    
    derived = graph.value(door, RESPLAN.derivedFrom)
    if not derived:
        return None
    
    adj_geom = _load_shape(adj_geom_index.get(derived))
    if adj_geom and not adj_geom.is_empty:
        if adj_geom.geom_type == "LineString":
            return mapping(adj_geom)
        elif adj_geom.geom_type == "Polygon":
            coords = list(adj_geom.exterior.coords)
            max_length = 0
            best_line = None
            for i in range(len(coords) - 1):
                line = LineString([coords[i], coords[i+1]])
                if line.length > max_length:
                    max_length = line.length
                    best_line = line
            if best_line:
                return mapping(best_line)
    
    # Fallback: use the wall that hosts this opening
    for host in graph.subjects(RESPLAN.hostsOpening, door):
        wall_geom = _load_shape(geom_index.get(host))
        if wall_geom and not wall_geom.is_empty:
            centroid = wall_geom.centroid
            door_line = LineString([
                (centroid.x - 0.45, centroid.y),
                (centroid.x + 0.45, centroid.y)
            ])
            return mapping(door_line)

    return None


def infer_window_geom_from_primary_and_hosts(graph, window, geom_index):
    def _load_shape(val):
        if val is None:
            return None
        if isinstance(val, dict):
            try:
                return shape(val)
            except:
                return None
        try:
            return shape(json.loads(str(val)))
        except:
            return None
    
    window_id = str(window).split("#")[-1]
    print(f"\n=== DEBUG WINDOW {window_id} ===")
    
    # --------------------------------------------------
    # 1. Get primary wall (MUST exist)
    # --------------------------------------------------
    primary = graph.value(window, RESPLAN.primaryWall) or graph.value(window, RESPLAN.hasPrimaryHost)
    if not primary:
        print(f"  ❌ No primary wall found")
        return None
    
    print(f"  Primary wall: {str(primary).split('#')[-1]}")
    
    P = _load_shape(geom_index.get(primary))
    if not P or P.is_empty:
        print(f"  ❌ Primary wall geometry empty")
        return None
    
    print(f"  Primary bounds: {P.bounds}")
    
    # --------------------------------------------------
    # 2. Get exactly ONE other wall that hostsOpening
    # --------------------------------------------------
    host_walls = list(graph.subjects(RESPLAN.hostsOpening, window))
    print(f"  All host walls: {[str(w).split('#')[-1] for w in host_walls]}")
    
    other_hosts = [w for w in host_walls if w != primary]
    
    if not other_hosts:
        print(f"  ❌ No other host walls found")
        return None
    
    # Take first other host as secondary
    secondary = other_hosts[0]
    print(f"  Secondary wall: {str(secondary).split('#')[-1]}")
    
    S = _load_shape(geom_index.get(secondary))
    if not S or S.is_empty:
        print(f"  ❌ Secondary wall geometry empty")
        return None
    
    print(f"  Secondary bounds: {S.bounds}")
    
    # --------------------------------------------------
    # 3. Determine dominant axis and thickness from primary wall
    # --------------------------------------------------
    minx, miny, maxx, maxy = P.bounds
    width = maxx - minx
    height = maxy - miny
    
    is_horizontal = width >= height
    thickness = min(width, height)
    
    print(f"  Orientation: {'HORIZONTAL' if is_horizontal else 'VERTICAL'}, thickness={thickness:.3f}")
    
    # Build centerline axis through wall
    if is_horizontal:
        midy = (miny + maxy) / 2
        axis = LineString([(minx, midy), (maxx, midy)])
        axis_start = Point(minx, midy)
        axis_end = Point(maxx, midy)
    else:
        midx = (minx + maxx) / 2
        axis = LineString([(midx, miny), (midx, maxy)])
        axis_start = Point(midx, miny)
        axis_end = Point(midx, maxy)
    
    print(f"  Axis: {list(axis.coords)}")
    
    # --------------------------------------------------
    # 4. Choose axis end closest to secondary wall as anchor
    # --------------------------------------------------
    dist_to_start = axis_start.distance(S)
    dist_to_end = axis_end.distance(S)
    
    print(f"  Distance to axis start: {dist_to_start:.3f}, to end: {dist_to_end:.3f}")
    
    if dist_to_start < dist_to_end:
        anchor = axis_start
        print(f"  Anchor: START {(anchor.x, anchor.y)}")
    else:
        anchor = axis_end
        print(f"  Anchor: END {(anchor.x, anchor.y)}")
    
    # --------------------------------------------------
    # 5. Measure distance to secondary wall → window length
    # --------------------------------------------------
    _, nearest_on_secondary = nearest_points(anchor, S)
    
    window_length_raw = anchor.distance(nearest_on_secondary)
    window_length = max(0.6, min(window_length_raw, 2.4))
    
    print(f"  Window length: raw={window_length_raw:.3f}, clamped={window_length:.3f}")
    print(f"  Nearest on secondary: {(nearest_on_secondary.x, nearest_on_secondary.y)}")
    
    # --------------------------------------------------
    # 6. Place a line from anchor toward secondary wall along the axis direction
    #    (NO clamping to primary bounds)
    # --------------------------------------------------
    dx = nearest_on_secondary.x - anchor.x
    dy = nearest_on_secondary.y - anchor.y

    if is_horizontal:
        direction = 1.0 if dx > 0 else -1.0
        x_end = anchor.x + direction * window_length
        line = LineString([(anchor.x, anchor.y), (x_end, anchor.y)])
        print(f"  Window line (H): ({anchor.x:.3f}, {anchor.y:.3f}) → ({x_end:.3f}, {anchor.y:.3f})")
    else:
        direction = 1.0 if dy > 0 else -1.0
        y_end = anchor.y + direction * window_length
        line = LineString([(anchor.x, anchor.y), (anchor.x, y_end)])
        print(f"  Window line (V): ({anchor.x:.3f}, {anchor.y:.3f}) → ({anchor.x:.3f}, {y_end:.3f})")
    
    # --------------------------------------------------
    # 7. Buffer by half thickness to get window polygon
    # --------------------------------------------------
    try:
        window_poly = line.buffer(thickness / 2, cap_style=2, join_style=2)
        
        if window_poly.is_empty or window_poly.area < 0.001:
            print(f"  ❌ Window polygon empty or too small")
            return None
        
        result = mapping(window_poly)
        print(f"  ✅ Window created: area={window_poly.area:.3f}, geom_type={window_poly.geom_type}")
        print(f"  ✅ Result keys: {result.keys()}")
        print(f"  ✅ Result type: {result.get('type')}")
        
        return result
    except Exception as e:
        print(f"  ❌ Buffer/mapping error: {e}")
        import traceback
        traceback.print_exc()
        return None

# ======================================================
# Main conversion
# ======================================================
def ttl_to_plan_dict(ttl_path: str | Path, use_general_inference: bool = True) -> Dict[str, Any]:
    ttl_path = Path(ttl_path)
    graph = Graph()
    graph.parse(ttl_path)

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

    # Plan metadata
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

    # Rooms
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

    # Structural elements
    for struct_subj in set(graph.subjects(RDF.type, None)):

        struct_key = _struct_type(graph, struct_subj)
        if struct_key is None or struct_key not in STRUCT_KEYS:
            continue

        own_geom_lit = graph.value(struct_subj, RESPLAN.geomJSON)
        geom_lit = own_geom_lit
        geom_dict = None

        if _is_empty_geom(geom_lit):
            replaced = graph.value(struct_subj, RESPLAN.replacesWall)
            if replaced:
                geom_lit = geom_index.get(replaced)

        if _is_empty_geom(geom_lit):
            derived = graph.value(struct_subj, RESPLAN.derivedFrom)
            if derived:
                geom_lit = adj_geom_index.get(derived)

        if struct_key == "door" and _is_empty_geom(own_geom_lit):
            geom_dict = infer_door_geom_from_walls_or_adjacency(
                graph, struct_subj, adj_geom_index, geom_index
            )

        if struct_key == "window" and _is_empty_geom(own_geom_lit):
            geom_dict = infer_window_geom_from_primary_and_hosts(
                graph, struct_subj, geom_index
            )

        if (struct_key == "interior_wall" and 
            _is_empty_geom(own_geom_lit) and 
            _is_empty_geom(geom_lit)):
            
            if use_general_inference:
                geom_dict = infer_interior_wall_GENERAL(
                    graph, struct_subj, adj_geom_index, geom_index
                )
            else:
                geom_dict = infer_interior_wall_geom(
                    graph, struct_subj, adj_geom_index, geom_index
                )

        if geom_dict is not None:
            geom = geom_dict
        elif not _is_empty_geom(geom_lit):
            geom = (
                geom_lit
                if isinstance(geom_lit, dict)
                else json.loads(str(geom_lit))
            )
        else:
            continue

        is_inferred_lit = graph.value(struct_subj, RESPLAN.isInferred)

        if struct_key in {"interior_wall", "exterior_wall"}:
            try:
                shp = shape(geom)
                if shp.geom_type in {"LineString", "MultiLineString"}:
                    shp = shp.buffer(0.06, cap_style=2, join_style=2)
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

        plan_dict["instances"]["structural"].setdefault(struct_key, []).append(record)

    return plan_dict


def save_ttl_as_json(
    ttl_path: str | Path,
    output_path: str | Path | None = None,
    use_general_inference: bool = True
) -> Path:
    plan_dict = ttl_to_plan_dict(ttl_path, use_general_inference=use_general_inference)
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


__all__ = ["ttl_to_plan_dict", "save_ttl_as_json", "infer_interior_wall_GENERAL"]
