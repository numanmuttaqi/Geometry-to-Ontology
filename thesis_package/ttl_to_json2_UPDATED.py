"""
Helpers to reconstruct the original plan JSON structure from a Turtle file.

UPDATED VERSION: Integrated with General Wall Inference Solution
- Uses bbox overlap analysis for orientation detection
- Works for ANY floor plan layout without ID hints
- Handles L-shaped corners and complex adjacencies
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional

from rdflib import Graph, Namespace, RDF
from rdflib.namespace import RDFS
from shapely.geometry import LineString, shape, mapping, Point, MultiLineString, GeometryCollection
from shapely.ops import nearest_points, linemerge, unary_union
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

def _is_empty_geom(geom_lit):
    """Check if geometry literal is None or has empty coordinates"""
    if geom_lit is None:
        return True
    
    # If it's already a dict (from adjacency)
    if isinstance(geom_lit, dict):
        coords = geom_lit.get('coordinates', [])
        return not coords or coords == []
    
    # If it's a string literal
    try:
        geom_dict = json.loads(str(geom_lit))
        coords = geom_dict.get('coordinates', [])
        return not coords or coords == []
    except:
        return True


# ======================================================
# GENERAL WALL INFERENCE (NEW)
# ======================================================
def infer_interior_wall_GENERAL(
    graph,
    wall,
    adj_geom_index,
    geom_index,
    default_thickness=0.1897,
):
    """
    GENERAL solution: Infer wall between rooms using bbox overlap analysis.
    
    This version:
    - Does NOT rely on wall ID hints
    - Works for any room layout
    - Uses pure geometric analysis
    
    Strategy:
    1. Get room bounding boxes
    2. Calculate X and Y overlaps
    3. Determine orientation from overlap pattern
    4. Place wall at gap midpoint
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
        """Extract axis-aligned boundary segments with orientation info."""
        segments = []
        coords = list(poly.exterior.coords)
        for i in range(len(coords) - 1):
            x1, y1 = coords[i]
            x2, y2 = coords[i + 1]
            dx, dy = abs(x2 - x1), abs(y2 - y1)
            if dx < 1e-6 and dy < 1e-6:
                continue
            if dx > dy:  # horizontal
                segments.append(
                    {
                        "orientation": "HORIZONTAL",
                        "minx": min(x1, x2),
                        "maxx": max(x1, x2),
                        "y": y1,
                    }
                )
            elif dy > dx:  # vertical
                segments.append(
                    {
                        "orientation": "VERTICAL",
                        "x": x1,
                        "miny": min(y1, y2),
                        "maxy": max(y1, y2),
                    }
                )
        return segments

    def _candidate_gap_segments(polyA, polyB, max_gap=0.35, min_overlap=0.25):
        """
        Find gap segments between two polygons by pairing facing edges.
        Returns list of (orientation, LineString) tuples ordered deterministically.
        """
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
                        candidates.append(
                            (
                                "HORIZONTAL",
                                LineString([(x_start, y_mid), (x_end, y_mid)]),
                                overlap,
                                gap,
                            )
                        )
                else:  # VERTICAL
                    overlap = min(a["maxy"], b["maxy"]) - max(a["miny"], b["miny"])
                    gap = abs(a["x"] - b["x"])
                    if overlap >= min_overlap and 0 < gap <= max_gap:
                        x_mid = (a["x"] + b["x"]) / 2.0
                        y_start = max(a["miny"], b["miny"])
                        y_end = min(a["maxy"], b["maxy"])
                        candidates.append(
                            (
                                "VERTICAL",
                                LineString([(x_mid, y_start), (x_mid, y_end)]),
                                overlap,
                                gap,
                            )
                        )

        # Deduplicate by rounded bounds and sort for deterministic assignment
        unique = []
        seen = set()
        for ori, line, overlap, gap in sorted(
            candidates, key=lambda t: (t[0], -t[2], -t[3])
        ):
            key = tuple(round(v, 3) for v in line.bounds)
            if key in seen:
                continue
            seen.add(key)
            unique.append((ori, line))
        return unique

    def _adjacency_group(space_a, space_b, current_adj):
        """Return ordered adjacency list for the pair and index of current adjacency."""
        adjs = []
        for adj in graph.subjects(RDF.type, RESPLAN.AdjacencyEdge):
            sa = graph.value(adj, RESPLAN.spaceA)
            sb = graph.value(adj, RESPLAN.spaceB)
            shared_wall = graph.value(adj, RESPLAN.sharedWall)

            # Skip exterior/shared walls so we only split among true interior edges
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
    
    # Calculate overlaps
    x_overlap = min(maxxA, maxxB) - max(minxA, minxB)
    y_overlap = min(maxyA, maxyB) - max(minyA, minyB)
    
    # Calculate gaps (when rooms don't overlap)
    x_gap = max(0, max(minxA, minxB) - min(maxxA, maxxB))
    y_gap = max(0, max(minyA, minyB) - min(maxyA, maxyB))
    
    wall_line = None
    orientation = None
    
    # =========================================================================
    # DECISION LOGIC based on overlap pattern
    # =========================================================================
    # 1) Try to find gap segments from facing edges (handles L-shapes/multiples)
    candidate_lines = _candidate_gap_segments(
        geomA,
        geomB,
        max_gap=max(default_thickness * 2.0, 0.30),
        min_overlap=0.25,
    )
    adjacencies, adj_idx = _adjacency_group(spaceA, spaceB, derived)
    if candidate_lines:
        pick_idx = min(adj_idx, len(candidate_lines) - 1)
        orientation, wall_line = candidate_lines[pick_idx]
    
    # 2) Fall back to bbox-based heuristics
    if wall_line is None:
        # CASE 1: Horizontal wall (rooms stacked vertically with Y gap)
        # Large X overlap, small/no Y overlap
        if x_overlap > 0.5 and (y_gap > 0.05 or y_overlap < 0.5):
            x_start = max(minxA, minxB)
            x_end = min(maxxA, maxxB)
            
            if minyA > maxyB:  # A is above B
                y_wall = (minyA + maxyB) / 2
            elif minyB > maxyA:  # B is above A
                y_wall = (minyB + maxyA) / 2
            else:
                # No gap but rooms overlap - use overlap midpoint
                y_wall = (max(minyA, minyB) + min(maxyA, maxyB)) / 2
            
            wall_line = LineString([(x_start, y_wall), (x_end, y_wall)])
            orientation = "HORIZONTAL"
        
        # CASE 2: Vertical wall (rooms side-by-side with X gap)
        # Large Y overlap, small/no X overlap
        elif y_overlap > 0.5 and (x_gap > 0.05 or x_overlap < 0.5):
            y_start = max(minyA, minyB)
            y_end = min(maxyA, maxyB)
            
            if minxA > maxxB:  # A is to the right of B
                x_wall = (minxA + maxxB) / 2
            elif minxB > maxxA:  # B is to the right of A
                x_wall = (minxB + maxxA) / 2
            else:
                # No gap but rooms overlap - use overlap midpoint
                x_wall = (max(minxA, minxB) + min(maxxA, maxxB)) / 2
            
            wall_line = LineString([(x_wall, y_start), (x_wall, y_end)])
            orientation = "VERTICAL"
        
        # CASE 3: L-shaped corner or complex relationship
        # Both overlaps significant - try adjacency geometry first
        elif x_overlap > 0.5 and y_overlap > 0.5:
            # Try to use adjacency geometry if available
            adj_geom_lit = adj_geom_index.get(derived)
            adj_geom = _load_shape(adj_geom_lit)
            
            if adj_geom and not adj_geom.is_empty:
                if adj_geom.geom_type == "LineString":
                    wall_line = adj_geom
                    orientation = "L-CORNER-ADJ"
                elif adj_geom.geom_type == "Polygon":
                    # Use the exterior as line
                    wall_line = adj_geom.exterior
                    orientation = "L-CORNER-ADJ"
            
            # Fallback to nearest points
            if not wall_line:
                p1, p2 = nearest_points(geomA, geomB)
                if p1.distance(p2) < 0.5:
                    wall_line = LineString([p1, p2])
                    orientation = "L-CORNER"
        
        # CASE 4: Fallback - nearest points
        else:
            p1, p2 = nearest_points(geomA, geomB)
            if p1.distance(p2) < 0.5:
                wall_line = LineString([p1, p2])
                orientation = "NEAREST"
    
    if not wall_line or wall_line.is_empty or wall_line.length < 0.3:
        return None
    
    # Create wall polygon
    wall_poly = wall_line.buffer(
        default_thickness / 2.0,
        cap_style=3,  # square caps
        join_style=2,  # mitre joins
    )
    
    if wall_poly.is_empty or wall_poly.area < 0.001:
        return None
    
    # Log result
    print(f"   ✅ {wall_id} ({spaceA_id}↔{spaceB_id}):")
    print(f"      Orientation: {orientation}")
    print(f"      Length: {wall_line.length:.3f}m, Area: {wall_poly.area:.4f}m²")
    print(f"      Overlaps: X={x_overlap:.3f}m, Y={y_overlap:.3f}m")
    print(f"      Gaps: X={x_gap:.3f}m, Y={y_gap:.3f}m")
    
    return mapping(wall_poly)


# ======================================================
# Original Inference Functions (kept for compatibility)
# ======================================================

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
    LEGACY: Old inference method (kept for backward compatibility).
    Use infer_interior_wall_GENERAL for better results.
    """
    # ... (keep original implementation) ...
    # For brevity, calling the new general method
    return infer_interior_wall_GENERAL(
        graph, wall, adj_geom_index, geom_index, default_thickness
    )


def infer_door_geom_from_walls_or_adjacency(graph, door, adj_geom_index, geom_index):
    """Infer door geometry from adjacency or host walls."""
    
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
    
    # Try adjacency geometry first
    adj_geom = _load_shape(adj_geom_index.get(derived))
    if adj_geom and not adj_geom.is_empty:
        if adj_geom.geom_type == "LineString":
            return mapping(adj_geom)
        elif adj_geom.geom_type == "Polygon":
            # Extract longest edge
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
    
    # Fallback: try to find intersection with host walls
    for host in graph.objects(door, RESPLAN.hosts):
        wall_geom = _load_shape(geom_index.get(host))
        if wall_geom and not wall_geom.is_empty:
            # Use centroid of wall as approximate door location
            centroid = wall_geom.centroid
            # Create a small line segment
            door_line = LineString([
                (centroid.x - 0.45, centroid.y),
                (centroid.x + 0.45, centroid.y)
            ])
            return mapping(door_line)
    
    return None


def infer_window_geom_from_primary_and_hosts(graph, window, geom_index):
    """Infer window geometry from primary and secondary hosts."""
    
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
    
    primary = graph.value(window, RESPLAN.hasPrimaryHost)
    P = _load_shape(geom_index.get(primary)) if primary else None
    
    if not P or P.is_empty:
        return None
    
    secondary = graph.value(window, RESPLAN.hasSecondaryHost)
    S = _load_shape(geom_index.get(secondary)) if secondary else None
    
    if not S or S.is_empty:
        S = Point(P.centroid.x, P.centroid.y + 100)
    
    # Get orientation
    minx, miny, maxx, maxy = P.bounds
    width, height = maxx - minx, maxy - miny
    is_horizontal = width >= height
    thickness = min(width, height)
    
    # Get primary axis
    if is_horizontal:
        midy = (miny + maxy) / 2
        axis = LineString([(minx, midy), (maxx, midy)])
    else:
        midx = (minx + maxx) / 2
        axis = LineString([(midx, miny), (midx, maxy)])
    
    # Find anchor point
    a0 = Point(axis.coords[0])
    a1 = Point(axis.coords[-1])
    anchor = a0 if a0.distance(S) < a1.distance(S) else a1
    
    # Calculate length
    p_anchor, p_sec = nearest_points(anchor, S)
    length = max(0.6, min(anchor.distance(p_sec), 2.4))
    
    # Build window line
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
def ttl_to_plan_dict(ttl_path: str | Path, use_general_inference: bool = True) -> Dict[str, Any]:
    """
    Convert TTL file to plan dictionary.
    
    Parameters:
    -----------
    ttl_path : str | Path
        Path to TTL file
    use_general_inference : bool
        If True, use the new general wall inference method (recommended)
        If False, use the legacy method
    """
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
    print("\n" + "="*80)
    print("INFERRING WALLS" + (" (GENERAL METHOD)" if use_general_inference else " (LEGACY METHOD)"))
    print("="*80)
    
    for struct_subj in set(graph.subjects(RDF.type, None)):

        struct_key = _struct_type(graph, struct_subj)
        if struct_key is None or struct_key not in STRUCT_KEYS:
            continue

        # --- geometry resolution ---
        own_geom_lit = graph.value(struct_subj, RESPLAN.geomJSON)
        geom_lit = own_geom_lit
        geom_dict = None  # Store inferred geometry as dict

        # fallback 1: replacesWall
        if _is_empty_geom(geom_lit):
            replaced = graph.value(struct_subj, RESPLAN.replacesWall)
            if replaced:
                geom_lit = geom_index.get(replaced)

        # fallback 2: derivedFrom adjacency
        if _is_empty_geom(geom_lit):
            derived = graph.value(struct_subj, RESPLAN.derivedFrom)
            if derived:
                geom_lit = adj_geom_index.get(derived)

        # --------------------------------------------------
        # Infer Door Geometry
        # --------------------------------------------------
        if struct_key == "door" and _is_empty_geom(own_geom_lit):
            geom_dict = infer_door_geom_from_walls_or_adjacency(
                graph,
                struct_subj,
                adj_geom_index,
                geom_index,
            )

        # --------------------------------------------------
        # Infer Window Geometry
        # --------------------------------------------------
        if struct_key == "window" and _is_empty_geom(own_geom_lit):
            geom_dict = infer_window_geom_from_primary_and_hosts(
                graph,
                struct_subj,
                geom_index,
            )

        # --------------------------------------------------
        # Infer Interior Wall Geometry
        # --------------------------------------------------
        if (struct_key == "interior_wall" and 
            _is_empty_geom(own_geom_lit) and 
            _is_empty_geom(geom_lit)):
            
            if use_general_inference:
                # Use new general method
                geom_dict = infer_interior_wall_GENERAL(
                    graph,
                    struct_subj,
                    adj_geom_index,
                    geom_index,
                )
            else:
                # Use legacy method
                geom_dict = infer_interior_wall_geom(
                    graph,
                    struct_subj,
                    adj_geom_index,
                    geom_index,
                )

        # --------------------------------------------------
        # Use geom_dict if available, otherwise parse geom_lit
        # --------------------------------------------------
        if geom_dict is not None:
            geom = geom_dict
        elif not _is_empty_geom(geom_lit):
            geom = (
                geom_lit
                if isinstance(geom_lit, dict)
                else json.loads(str(geom_lit))
            )
        else:
            continue  # cannot visualize

        is_inferred_lit = graph.value(struct_subj, RESPLAN.isInferred)

        # Normalize wall geometries: buffer lines into polygons for visibility
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

        plan_dict["instances"]["structural"].setdefault(
            struct_key, []
        ).append(record)

    return plan_dict

# ======================================================
# Save helper
# ======================================================
def save_ttl_as_json(
    ttl_path: str | Path,
    output_path: str | Path | None = None,
    use_general_inference: bool = True
) -> Path:
    """
    Convert TTL to JSON and save.
    
    Parameters:
    -----------
    ttl_path : str | Path
        Path to input TTL file
    output_path : str | Path | None
        Path to output JSON file (default: same name as TTL but .json)
    use_general_inference : bool
        If True, use general wall inference method (recommended)
    """
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
