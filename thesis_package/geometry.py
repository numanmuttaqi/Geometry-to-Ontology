from .config import JSON_DIR

# --- Cell 2 ---
def geojsonify(geom):
    if geom is None:
        return {"type": "GeometryCollection", "geometries": []}
    if isinstance(geom, (Polygon, MultiPolygon, LineString, MultiLineString, Point)):
        return {"type": "GeometryCollection", "geometries": []} if geom.is_empty else shp_mapping(geom)
    parts = [g for g in R.get_geometries(geom)]
    if not parts:
        return {"type": "GeometryCollection", "geometries": []}
    if all(isinstance(g, Polygon) for g in parts):
        return {"type": "MultiPolygon", "coordinates": [shp_mapping(g)["coordinates"] for g in parts]}
    return {"type": "GeometryCollection", "geometries": [shp_mapping(g) for g in parts]}

def bbox_of_geom(geom):
    if geom is None or getattr(geom, "is_empty", True): return [None, None, None, None]
    x1, y1, x2, y2 = geom.bounds
    return [float(x1), float(y1), float(x2), float(y2)]

def assign_ids(n, prefix):
    return [f"{prefix}-{i:04d}" for i in range(1, n+1)]

def _geom(obj):
    if obj is None: return None
    g = obj.get("geom") if isinstance(obj, dict) else None
    if isinstance(g, dict) and "type" in g: return shape(g)
    return None

def _id(obj, fallback_prefix):
    if isinstance(obj, dict) and "id" in obj: return str(obj["id"])
    return f"{fallback_prefix}-{abs(hash(str(obj)))%10**8:08d}"

def _level(obj):
    return obj.get("level") or obj.get("storey") or obj.get("props", {}).get("level")

def find_instances(plan):
    out = {"rooms": [], "walls": [], "openings": []}
    if "instances" not in plan: return out

    # rooms
    if "room" in plan["instances"]:
        room_data = plan["instances"]["room"]
        for room_type, room_list in room_data.items():
            for room in room_list: out["rooms"].append(room)

    # structural
    if "structural" in plan["instances"]:
        st = plan["instances"]["structural"]
        for wall_type in ["interior_wall","exterior_wall"]:
            for wall in st.get(wall_type, []):
                w = deepcopy(wall); w.setdefault("subtype", "exterior" if "exterior" in wall_type else "interior")
                out["walls"].append(w)
        for opening_type in ["door","window","front_door"]:
            for opening in st.get(opening_type, []):
                o = deepcopy(opening); o.setdefault("subtype", opening_type)
                out["openings"].append(o)
    return out


# --- Cell 5 ---
def boundary_overlap_length(room_poly: Polygon, wall_geom) -> float:
    if isinstance(wall_geom, (LineString, MultiLineString)):
        buf = wall_geom.buffer(WALL_BUFFER, cap_style=2, join_style=2)
        inter = room_poly.boundary.intersection(buf)
    else:
        inter = room_poly.boundary.intersection(wall_geom)
    if inter.is_empty: return 0.0
    try: return _f(inter.length)
    except Exception:
        if hasattr(inter, "geoms"): return _f(sum(g.length for g in inter.geoms))
        return 0.0

def opening_on_wall(opening_geom, wall_geom) -> bool:
    a, b = opening_geom, wall_geom
    if isinstance(b, (LineString, MultiLineString)):
        b = b.buffer(WALL_BUFFER, cap_style=2, join_style=2)
    return a.buffer(OPENING_BUFFER).intersects(b)

def index_instances(plan):
    inst = find_instances(plan)
    rooms, walls, openings = [], [], []
    for r in inst["rooms"]:
        g = _geom(r)
        if g is None or g.is_empty: continue
        rooms.append(GeoRec(_id(r,"RM"), "Room", r.get("subtype") or r.get("type"), _level(r), g, r))
    for w in inst["walls"]:
        g = _geom(w)
        if g is None or g.is_empty: continue
        walls.append(GeoRec(_id(w,"WL"), "Wall", w.get("subtype") or w.get("type"), _level(w), g, w))
    for o in inst["openings"]:
        g = _geom(o)
        if g is None or g.is_empty: continue
        openings.append(GeoRec(_id(o,"OP"), "Opening", o.get("subtype") or o.get("type"), _level(o), g, o))
    return {
        "rooms": rooms, "walls": walls, "openings": openings,
        "tree": {
            "rooms": STRtree([x.geom for x in rooms]) if rooms else None,
            "walls": STRtree([x.geom for x in walls]) if walls else None,
            "openings": STRtree([x.geom for x in openings]) if openings else None,
        }
    }

def compute_relations(plan):
    idx = index_instances(plan)
    rooms, walls, openings = idx["rooms"], idx["walls"], idx["openings"]

    bounded_by = []
    for r in rooms:
        cand = walls if idx["tree"]["walls"] is None else [w for w in walls if w.geom.bounds and True]
        for w in cand:
            olap = boundary_overlap_length(r.geom, w.geom)
            if olap >= EPS_LEN:
                bounded_by.append({
                    "id": f"E-bnd-{len(bounded_by)+1:05d}",
                    "room": r.id, "wall": w.id, "length": olap, "wall_type": w.subtype or "unknown"
                })

    adjacent_to, seen = [], set()
    for i, ri in enumerate(rooms):
        for rj in rooms[i+1:]:
            inter = ri.geom.boundary.intersection(rj.geom.boundary)
            shared_len = _f(inter.length) if not inter.is_empty else 0.0
            if shared_len >= EPS_LEN:
                key = tuple(sorted((ri.id, rj.id)))
                if key not in seen:
                    seen.add(key)
                    adjacent_to.append({
                        "id": f"E-adj-{len(adjacent_to)+1:05d}",
                        "a": ri.id, "b": rj.id, "overlap_length": shared_len
                    })

    hosts_opening = []
    for op in openings:
        cand = walls if idx["tree"]["walls"] is None else walls
        for w in cand:
            if opening_on_wall(op.geom, w.geom):
                hosts_opening.append({
                    "id": f"E-host-{len(hosts_opening)+1:05d}",
                    "wall": w.id, "opening": op.id, "opening_type": op.subtype or "opening"
                })

    # leave connected_via_door to the simple routine in the next cell
    return {
        "bounded_by": bounded_by,
        "adjacent_to": adjacent_to,
        "hosts_opening": hosts_opening,
        "connected_via_door": []  # will be replaced
    }
