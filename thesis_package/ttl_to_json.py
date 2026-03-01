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
from shapely.affinity import translate

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
# WALL INFERENCE FUNCTION
# ======================================================
def infer_interior_wall_GENERAL(
    graph,
    wall,
    adj_geom_index,
    geom_index,
    default_thickness=0.1897,
):
    
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
    # WALL RECONSTRUCTION
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
    # DOOR DETECTION AND SPLITTING
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
                segments.append(result)
            elif result.geom_type == 'MultiLineString':
                for line in result.geoms:
                    segments.append(line)
            elif result.geom_type == 'GeometryCollection':
                for geom in result.geoms:
                    if geom.geom_type == 'LineString':
                        segments.append(geom)
            
            return segments
        except:
            return [wall_line]
    
    openings = _get_openings_between_rooms(spaceA, spaceB)
    
    if openings:
        wall_segments = _split_wall_by_openings(wall_line, openings)
    else:
        wall_segments = [wall_line]
    
    if not wall_segments:
        return None
    
    merged = linemerge(wall_segments)
    
    wall_poly = merged.buffer(
        default_thickness / 2.0,
        cap_style=3,  # square caps
        join_style=2,  # mitre joins
    )
    
    if wall_poly.is_empty or wall_poly.area < 0.001:
        return None
    
    # Log
    total_length = sum(seg.length for seg in wall_segments) if isinstance(wall_segments, list) else wall_segments.length
    print(f" {wall_id} ({spaceA_id}↔{spaceB_id}): {orientation}, L={total_length:.3f}m, {len(openings)} openings")
    
    return mapping(wall_poly)


# ======================================================
# Other inference functions
# ======================================================

def infer_interior_wall_geom(graph, wall, adj_geom_index, geom_index, default_thickness=0.19, **kwargs):
    return infer_interior_wall_GENERAL(graph, wall, adj_geom_index, geom_index, default_thickness)


def infer_door_geom_from_walls_or_adjacency(
    graph,
    door,
    adj_geom_index,
    geom_index,
    default_width=0.9,
):
    def _load_shape(val):
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

    def _wall_thickness(poly):
        minx, miny, maxx, maxy = poly.bounds
        w = maxx - minx
        h = maxy - miny
        return max(1e-6, min(w, h))

    def _dominant_is_horizontal(poly):
        minx, miny, maxx, maxy = poly.bounds
        w = maxx - minx
        h = maxy - miny
        return w >= h

    def _clamp_segment_to_bounds_horizontal(x0, x1, minx, maxx):
        a = max(minx, min(x0, x1))
        b = min(maxx, max(x0, x1))
        return (a, b) if b >= a else (x0, x1)

    def _clamp_segment_to_bounds_vertical(y0, y1, miny, maxy):
        a = max(miny, min(y0, y1))
        b = min(maxy, max(y0, y1))
        return (a, b) if b >= a else (y0, y1)

    def _shared_boundary_midpoint(spaces):
        # Try to compute midpoint of shared boundary between two spaces if available
        if len(spaces) < 2:
            return None
        s1, s2 = spaces[0], spaces[1]
        g1 = _load_shape(geom_index.get(s1))
        g2 = _load_shape(geom_index.get(s2))
        if not g1 or not g2 or g1.is_empty or g2.is_empty:
            return None
        shared = g1.boundary.intersection(g2.boundary)
        if shared.is_empty:
            return None
        if shared.geom_type in {"LineString", "MultiLineString"} and shared.length > 0:
            return shared.interpolate(0.5, normalized=True) if shared.geom_type == "LineString" else linemerge(shared).interpolate(0.5, normalized=True)
        if shared.geom_type == "Point":
            return shared
        return shared.centroid

    def _shared_boundary_line(spaces):
        if len(spaces) < 2:
            return None
        s1, s2 = spaces[0], spaces[1]
        
        g1 = _load_shape(geom_index.get(s1))
        g2 = _load_shape(geom_index.get(s2))
        if not g1 or not g2 or g1.is_empty or g2.is_empty:
            return None
        
        shared = g1.boundary.intersection(g2.boundary)
        
        if shared.is_empty or shared.length == 0:
            return None
        if shared.geom_type == "LineString":
            return shared
        if shared.geom_type == "MultiLineString":
            result = max(list(shared.geoms), key=lambda g: g.length)
            return result
        return None

    def _wall_room_overlap_line(polyA, polyB, room1, room2):
        """Try to get a line on the wall boundaries that overlaps the two room boundaries."""
        g1 = _load_shape(geom_index.get(room1))
        g2 = _load_shape(geom_index.get(room2))
        if not g1 or not g2 or g1.is_empty or g2.is_empty:
            return None
        union_rooms = g1.union(g2)
        candidates = []
        for poly in (polyA, polyB):
            inter = poly.boundary.intersection(union_rooms.boundary)
            if inter.is_empty or inter.length == 0:
                continue
            if inter.geom_type == "LineString":
                candidates.append(inter)
            elif inter.geom_type == "MultiLineString":
                candidates.extend(list(inter.geoms))
        if candidates:
            return max(candidates, key=lambda g: g.length)
        return None

    spaces = list(graph.objects(door, RESPLAN.connectsSpace))
    if len(spaces) < 2:
        # fallback: use derivedFrom adjacency spaces if available
        adj = graph.value(door, RESPLAN.derivedFrom)
        if adj:
            sA = graph.value(adj, RESPLAN.spaceA)
            sB = graph.value(adj, RESPLAN.spaceB)
            if sA and sB:
                spaces = [sA, sB]

    # --------------------------------------------------
    # 1) Collect host walls (1–2)
    # --------------------------------------------------
    host_walls = list(graph.subjects(RESPLAN.hostsOpening, door))
    host_polys = []
    for w in host_walls:
        poly = _load_shape(geom_index.get(w))
        if poly and (not poly.is_empty) and poly.geom_type in {"Polygon", "MultiPolygon"}:
            host_polys.append((w, poly))

    if not host_polys:
        return None

    # Prefer at most 2 walls (per your spec)
    host_polys = host_polys[:2]

    # --------------------------------------------------
    # 2) Two walls: simple placement between host walls
    # --------------------------------------------------
    if len(host_polys) == 2:
        (_, A), (_, B) = host_polys
        t = min(_wall_thickness(A), _wall_thickness(B))

        # Get nearest segment between the two walls to determine gap span
        pA, pB = nearest_points(A.boundary, B.boundary)
        
        # Determine orientation
        dx = abs(pB.x - pA.x)
        dy = abs(pB.y - pA.y)
        
        minxA, minyA, maxxA, maxyA = A.bounds
        minxB, minyB, maxxB, maxyB = B.bounds
        
        door_line = None
        
        if dx < 1e-6:  # Vertical door
            x_base = pA.x  # nearest point X
            
            if len(spaces) >= 2:
                g1 = _load_shape(geom_index.get(spaces[0]))
                g2 = _load_shape(geom_index.get(spaces[1]))
                
                if g1 and g2 and not g1.is_empty and not g2.is_empty:
                    # Get room bounds
                    minx1, _, maxx1, _ = g1.bounds
                    minx2, _, maxx2, _ = g2.bounds
                    
                    # Find closest edge
                    edges = [
                        (abs(maxx1 - x_base), maxx1, "room1_right"),
                        (abs(minx1 - x_base), minx1, "room1_left"),
                        (abs(maxx2 - x_base), maxx2, "room2_right"),
                        (abs(minx2 - x_base), minx2, "room2_left"),
                    ]
                    edges.sort(key=lambda e: e[0])
                    
                    _, x_edge, edge_name = edges[0]
                    
                    # TWEAK: Weighted average between x_base and x_edge
                    weight = 0 # ADJUSTABLE
                    x_door = weight * x_base + (1 - weight) * x_edge
                else:
                    x_door = x_base
            else:
                x_door = x_base
            
            # Y-span from gap between walls (FULL LENGTH)
            y_min = max(minyA, minyB)
            y_max = min(maxyA, maxyB)
            
            if y_max < y_min:
                y_min = min(maxyA, maxyB)
                y_max = max(minyA, minyB)
            
            door_line = LineString([(x_door, y_min), (x_door, y_max)])
            
        elif dy < 1e-6:  # Horizontal door
            y_base = pA.y
            
            if len(spaces) >= 2:
                g1 = _load_shape(geom_index.get(spaces[0]))
                g2 = _load_shape(geom_index.get(spaces[1]))
                
                if g1 and g2 and not g1.is_empty and not g2.is_empty:
                    # Get room bounds
                    _, miny1, _, maxy1 = g1.bounds
                    _, miny2, _, maxy2 = g2.bounds
                    
                    # Find closest edge
                    edges = [
                        (abs(maxy1 - y_base), maxy1, "room1_top"),
                        (abs(miny1 - y_base), miny1, "room1_bottom"),
                        (abs(maxy2 - y_base), maxy2, "room2_top"),
                        (abs(miny2 - y_base), miny2, "room2_bottom"),
                    ]
                    edges.sort(key=lambda e: e[0])
                    
                    _, y_edge, edge_name = edges[0]
                    
                    # TWEAK: Weighted average
                    weight = 0  # ADJUST THIS
                    y_door = weight * y_base + (1 - weight) * y_edge
                else:
                    y_door = y_base
            else:
                y_door = y_base
            
            # X-span from gap between walls (FULL LENGTH)
            x_min = max(minxA, minxB)
            x_max = min(maxxA, maxxB)
            
            if x_max < x_min:
                x_min = min(maxxA, maxxB)
                x_max = max(minxA, minxB)
            
            door_line = LineString([(x_min, y_door), (x_max, y_door)])
            
        else:
            door_line = LineString([pA, pB])
        
        if door_line is None or door_line.is_empty:
            return None
        
        door_poly = door_line.buffer(t / 2.0, cap_style=2, join_style=2)
        return mapping(door_poly) if (not door_poly.is_empty) else None

    # --------------------------------------------------
    # 3) One wall: adjacency point (derivedFrom centroid) or wall centroid
    #    -> project to wall boundary -> orient along dominant axis -> width 0.9m
    # --------------------------------------------------
    (_, W) = host_polys[0]
    t = _wall_thickness(W)
    half_t = t / 2.0

    derived = graph.value(door, RESPLAN.derivedFrom)
    ref_pt = None

    if derived:
        adj_geom = _load_shape(adj_geom_index.get(derived))
        if adj_geom and not adj_geom.is_empty:
            ref_pt = adj_geom.centroid

    if ref_pt is None:
        ref_pt = W.centroid

    # project to boundary (point on boundary nearest to ref_pt)
    # nearest_points returns points in the same order as inputs. 
    _, on_boundary = nearest_points(ref_pt, W.boundary)

    minx, miny, maxx, maxy = W.bounds
    is_horizontal = _dominant_is_horizontal(W)

    if is_horizontal:
        x0 = on_boundary.x - default_width / 2.0
        x1 = on_boundary.x + default_width / 2.0
        x0, x1 = _clamp_segment_to_bounds_horizontal(x0, x1, minx, maxx)
        line = LineString([(x0, on_boundary.y), (x1, on_boundary.y)])
    else:
        y0 = on_boundary.y - default_width / 2.0
        y1 = on_boundary.y + default_width / 2.0
        y0, y1 = _clamp_segment_to_bounds_vertical(y0, y1, miny, maxy)
        line = LineString([(on_boundary.x, y0), (on_boundary.x, y1)])

    # Align single-wall door to shared boundary midpoint if connectsSpace is available
    mid = _shared_boundary_midpoint(spaces)
    if mid is not None and not line.is_empty:
        dx = mid.x - line.centroid.x
        dy = mid.y - line.centroid.y
        line = translate(line, xoff=dx, yoff=dy)

    door_poly = line.buffer(half_t, cap_style=2, join_style=2)
    return mapping(door_poly) if (not door_poly.is_empty) else None


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
    
    # --------------------------------------------------
    # 1. Get primary wall (MUST exist)
    # --------------------------------------------------
    primary = graph.value(window, RESPLAN.primaryWall) or graph.value(window, RESPLAN.hasPrimaryHost)
    if not primary:
        return None
    
    P = _load_shape(geom_index.get(primary))
    if not P or P.is_empty:
        return None
    
    # --------------------------------------------------
    # 2. Get exactly ONE other wall that hostsOpening
    # --------------------------------------------------
    host_walls = list(graph.subjects(RESPLAN.hostsOpening, window))
    
    other_hosts = [w for w in host_walls if w != primary]
    
    if not other_hosts:
        return None
    
    # Take first other host as secondary
    secondary = other_hosts[0]
    
    S = _load_shape(geom_index.get(secondary))
    if not S or S.is_empty:
        return None
    
    # --------------------------------------------------
    # 3. Determine dominant axis and thickness from primary wall
    # --------------------------------------------------
    minx, miny, maxx, maxy = P.bounds
    width = maxx - minx
    height = maxy - miny
    
    is_horizontal = width >= height
    thickness = min(width, height)
    
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
    
    # --------------------------------------------------
    # 4. Choose axis end closest to secondary wall as anchor
    # --------------------------------------------------
    dist_to_start = axis_start.distance(S)
    dist_to_end = axis_end.distance(S)
    
    if dist_to_start < dist_to_end:
        anchor = axis_start
    else:
        anchor = axis_end
    
    # --------------------------------------------------
    # 5. Measure distance to secondary wall → window length
    # --------------------------------------------------
    _, nearest_on_secondary = nearest_points(anchor, S)
    
    window_length_raw = anchor.distance(nearest_on_secondary)
    window_length = max(0.6, min(window_length_raw, 2.4))
    
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
    else:
        direction = 1.0 if dy > 0 else -1.0
        y_end = anchor.y + direction * window_length
        line = LineString([(anchor.x, anchor.y), (anchor.x, y_end)])
    
    # --------------------------------------------------
    # 7. Buffer by half thickness to get window polygon
    # --------------------------------------------------
    try:
        window_poly = line.buffer(thickness / 2, cap_style=2, join_style=2)
        
        if window_poly.is_empty or window_poly.area < 0.001:
            return None
        
        result = mapping(window_poly)
        return result
    except Exception:
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
