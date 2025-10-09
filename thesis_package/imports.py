# --- Cell 0 ---
from . import geometry, graph, io, visualize
from .config import PKL_PATH, JSON_DIR, PLOT_DIR

import os, json, pickle, math
from copy import deepcopy
from typing import List, Dict, Any
from collections import namedtuple, defaultdict

import numpy as np
import matplotlib.pyplot as plt
from shapely.geometry import (
    Polygon, MultiPolygon, LineString, MultiLineString, Point, GeometryCollection, shape
)
from shapely.geometry import mapping as shp_mapping
from shapely.ops import unary_union
from shapely.strtree import STRtree

import resplan_utils as R  # your helper lib

# ---- paths ----
DATA_PKL     = "ResPlan.pkl"
OUT_JSON_DIR = "resplan_json"
OUT_PLOT_DIR = "resplan_plot"
os.makedirs(OUT_JSON_DIR, exist_ok=True)
os.makedirs(OUT_PLOT_DIR,  exist_ok=True)

# ---- categories ----
ROOM_KEYS   = ["bedroom","bathroom","kitchen","living","balcony","storage","stair","veranda","parking"]
STRUCT_KEYS = ["interior_wall","exterior_wall","door","window","front_door"]
GEOM_LAYERS = ["inner","garden","land","pool"]
META_KEYS   = ["id","unitType","area","net_area","wall_depth"]

# ---- colors (viz) ----
ROOM_COLORS = {
    "living":   "#d9d9d9",
    "bedroom":  "#66c2a5",
    "bathroom": "#fc8d62",
    "kitchen":  "#8da0cb",
    "balcony":  "#b3b3b3",
    "storage":  "#cccccc",
    "stair":    "#aaaaaa",
    "veranda":  "#bbbbbb",
    "parking":  "#dddddd",
}
STRUCT_COLORS = {
    "interior_wall": "#445DFF",
    "exterior_wall": "#FFD344",
    "door":          "#e78ac3",
    "window":        "#a6d854",
    "front_door":    "#a63603",
}

# ---- tolerances (m) ----
EPS_LEN = 0.02
EPS_AREA = 0.01
WALL_BUFFER = 0.02
OPENING_BUFFER = 0.005

# ---- small utils ----
def _f(x, nd=6):
    try: return round(float(x), nd)
    except: return x

GeoRec = namedtuple("GeoRec", "id cls subtype level geom raw")


# --- Cell 3 ---
def _walls_as_polygons(plan, fallback_frac=0.01):
    W = R.get_plan_width(plan) or 1.0
    bufw = fallback_frac * W
    polys = []
    for g in R.get_geometries(plan.get("wall")):
        if isinstance(g, (Polygon, MultiPolygon)):
            polys += [g] if isinstance(g, Polygon) else list(g.geoms)
        elif isinstance(g, (LineString, MultiLineString)):
            polys.append(g.buffer(bufw, join_style=2, cap_style=2))
    return unary_union(polys).buffer(0)

def _instances_from_geom(category: str, geom, min_area: float = 2.0) -> list:
    """
    Convert geometry to instances with proper validation.
    Only creates instances for significant, valid geometries.
    """
    if geom is None or geom.is_empty:
        return []
    
    # Extract polygons
    if isinstance(geom, MultiPolygon):
        polys = list(geom.geoms)
    elif isinstance(geom, Polygon):
        polys = [geom]
    else:
        print(f"WARNING: Unexpected geometry type for {category}: {geom.geom_type}")
        return []
    
    # Filter valid polygons BEFORE creating instances
    valid_polys = []
    for p in polys:
        if not isinstance(p, Polygon):
            continue
            
        area = p.area
        
        # Skip tiny artifacts
        if area < min_area:
            print(f"SKIPPED: {category} fragment with area {area:.2f}m²")
            continue
        
        # Skip invalid geometry
        if not p.is_valid:
            print(f"SKIPPED: Invalid {category} geometry")
            continue
        
        # Skip if too elongated (likely a sliver artifact)
        try:
            bounds = p.bounds
            width = bounds[2] - bounds[0]
            height = bounds[3] - bounds[1]
            aspect_ratio = max(width, height) / (min(width, height) + 1e-6)
            if aspect_ratio > 50:  # Very thin sliver
                print(f"SKIPPED: {category} sliver (aspect ratio {aspect_ratio:.1f})")
                continue
        except:
            pass
        
        valid_polys.append(p)
    
    if not valid_polys:
        return []
    
    # Now create instances only for valid geometries
    ids = assign_ids(len(valid_polys), category[:2].upper())
    out = []
    for _id, p in zip(ids, valid_polys):
        c = p.centroid
        out.append({
            "id": _id,
            "type": category,
            "geom": geojsonify(p),
            "props": {
                "area": float(p.area),
                "centroid": (float(c.x), float(c.y)),
                "bbox": bbox_of_geom(p)
            }
        })
    
    return out

def split_walls(plan: Dict[str, Any],
                band_factor: float = 1,
                band_min_frac: float = 0.02,
                fallback_frac: float = 0.01) -> Dict[str, list]:
    p = R.normalize_keys(plan.copy())
    inner = p["inner"]
    if inner.geom_type == "MultiPolygon": inner = max(inner.geoms, key=lambda g: g.area)
    W = R.get_plan_width(p) or 1.0
    t = float(p.get("wall_width", 4) or 4)
    band_hw = max(band_factor * t, band_min_frac * W)

    walls_poly    = _walls_as_polygons(p, fallback_frac=fallback_frac)
    boundary_band = inner.boundary.buffer(band_hw, join_style=2, cap_style=2)

    exterior_wall = walls_poly.intersection(boundary_band).buffer(0)
    interior_wall = walls_poly.difference(boundary_band).buffer(0)

    return {
        "interior_wall": _instances_from_geom("interior_wall", interior_wall),
        "exterior_wall": _instances_from_geom("exterior_wall", exterior_wall),
        "door":          _instances_from_geom("door",          p.get("door")),
        "window":        _instances_from_geom("window",        p.get("window")),
        "front_door":    _instances_from_geom("front_door",    p.get("front_door")),
    }

from shapely.ops import unary_union

def extract_room_instances(plan: Dict[str, Any]) -> Dict[str, list]:
    """
    Extract rooms with automatic merging for single-instance types.
    ID ruangan menggunakan ROOM_PREFIX agar konsisten dengan graph.relations.
    """
    SINGLE_INSTANCE_ROOMS = {"living", "kitchen"}
    out = {k: [] for k in ROOM_KEYS}

    for rk in ROOM_KEYS:
        geom = plan.get(rk)
        if geom is None:
            continue

        geoms = []
        if hasattr(geom, "geoms"):
            geoms = list(geom.geoms)
        elif hasattr(geom, "__iter__"):
            for g in geom:
                if hasattr(g, "geoms"):
                    geoms.extend(list(g.geoms))
                else:
                    geoms.append(g)
        else:
            geoms = [geom]

        geoms = [g for g in geoms if not getattr(g, "is_empty", True)]
        if not geoms:
            continue

        if rk in SINGLE_INSTANCE_ROOMS and len(geoms) > 1:
            merged = unary_union(geoms)
            if merged.geom_type == "MultiPolygon":
                significant = [g for g in merged.geoms if g.area >= 2.0]
                geoms = significant if len(significant) == 1 else [max(merged.geoms, key=lambda g: g.area)]
            elif merged.geom_type == "Polygon":
                geoms = [merged]

        # >>> ID pakai ROOM_PREFIX <<<
        pref = ROOM_PREFIX.get(rk)
        if not pref:
            raise ValueError(f"ROOM_PREFIX missing for subtype '{rk}'")
        ids = assign_ids(len(geoms), pref)

        for _id, g in zip(ids, geoms):
            c = g.centroid
            centroid = (float(c.x), float(c.y)) if c is not None and not g.is_empty else (None, None)
            out[rk].append({
                "id": _id,
                "type": rk,
                "geom": geojsonify(g),
                "props": {
                    "area": float(getattr(g, "area", 0.0)),
                    "centroid": centroid,
                    "bbox": bbox_of_geom(g)
                }
            })

    return out

def extract_metadata(plan, plan_idx: int, json_relpath: str, plot_relpath: str,
                     dataset_name: str = "ResPlan", source_file: str = DATA_PKL,
                     split: str | None = None) -> dict:
    meta = {}
    for k in META_KEYS:
        if k in plan: meta[k] = plan[k]
    if "id" in meta and "plan_id" not in meta: meta["plan_id"] = meta["id"]
    meta.update({"dataset": dataset_name, "plan_idx": int(plan_idx),
                 "plan_label": f"Plan #{plan_idx}", "units": "m"})
    if split is not None: meta["split"] = split
    meta["source"]    = {"file": source_file}
    meta["artifacts"] = {"json_path": json_relpath, "plot_path": plot_relpath}
    return meta

def extract_layers(plan):
    return {k: geojsonify(plan.get(k)) for k in GEOM_LAYERS}


# --- Cell 6 ---
from collections import defaultdict

def _bounded_by_per_room(relations: dict) -> list:
    """
    Kelompokkan bounded_by per room.
    Output JSON-serializable:
    [
      {
        "room": "BED-0001",
        "walls": ["EX-0001","IN-0001","IN-0002",...],           # unik & terurut
        "by_wall": [{"wall":"IN-0001","length": 3.42}, ...],     # ringkasan per dinding
        "length_total": 12.87                                     # total perimeter kontak terukur
      },
      ...
    ]
    """
    acc = defaultdict(lambda: {"walls": set(), "by_wall_len": defaultdict(float)})

    for e in relations.get("bounded_by", []):
        r = e.get("room"); w = e.get("wall"); L = float(e.get("length", 0.0) or 0.0)
        if not r or not w:
            continue
        acc[r]["walls"].add(w)
        acc[r]["by_wall_len"][w] += L

    out = []
    for room_id, data in acc.items():
        by_wall = [{"wall": w, "length": round(data["by_wall_len"][w], 6)} for w in sorted(data["by_wall_len"])]
        length_total = round(sum(x["length"] for x in by_wall), 6)
        out.append({
            "room": room_id,
            "walls": sorted(list(data["walls"])),
            "length_total": length_total
        })
    # stabilkan urutan output
    out.sort(key=lambda x: x["room"])
    return out


# --- Cell 7 ---

def _as_id(x):
    if isinstance(x, str): return x
    if isinstance(x, dict): return x.get("id")
    return None

def _geom_of(obj):
    g = obj.get("geom") or obj.get("geometry") if isinstance(obj, dict) else None
    if isinstance(g, dict) and "type" in g: return shape(g)
    try:
        import resplan_utils as R
        return R.to_shape(obj)
    except Exception:
        return None

def _norm_relations_ids(rel):
    out = {}
    for k, arr in (rel or {}).items():
        if not isinstance(arr, list):
            out[k] = arr; continue
        norm = []
        for e in arr:
            if not isinstance(e, dict): continue
            ee = {}
            for key, val in e.items():
                if key in ("room","wall","opening","from","to","door","through_wall"):
                    vid = _as_id(val)
                    if vid is None: continue
                    ee[key] = vid
                else:
                    ee[key] = val
            norm.append(ee)
        out[k] = norm
    return out

def _build_lookups(plan):
    inst = plan.get("instances", {})
    inst_struct = inst.get("structural", {})
    rooms_by_type = inst.get("room", {})

    wall_type = {}
    for w in inst_struct.get("interior_wall", []) or []:
        wid = _as_id(w if isinstance(w, str) else w.get("id"))
        if wid: wall_type[wid] = "interior_wall"
    for w in inst_struct.get("exterior_wall", []) or []:
        wid = _as_id(w if isinstance(w, str) else w.get("id"))
        if wid: wall_type[wid] = "exterior_wall"

    room_type, room_geom = {}, {}
    for cat, arr in rooms_by_type.items():
        for r in arr:
            rid = _as_id(r if isinstance(r, str) else r.get("id"))
            if not rid: continue
            room_type[rid] = cat
            rg = _geom_of(r) if isinstance(r, dict) else None
            if rg is not None: room_geom[rid] = rg

    rel = plan.get("graph", {}).get("relations", {}) or {}
    rel = _norm_relations_ids(rel)

    wall_to_rooms = {}
    for e in rel.get("bounded_by", []):
        w = e.get("wall"); r = e.get("room")
        if w and r: wall_to_rooms.setdefault(w, set()).add(r)

    opening_to_walls = {}
    for e in rel.get("hosts_opening", []):
        o = e.get("opening"); w = e.get("wall")
        if o and w: opening_to_walls.setdefault(o, []).append(w)

    return wall_type, room_type, room_geom, wall_to_rooms, opening_to_walls


# --- Cell 9 ---
def build_connected_via_door_from_hosts(plan):
    wall_type, room_type, room_geom, wall_to_rooms, opening_to_walls = _build_lookups(plan)

    inst_struct = plan.get("instances", {}).get("structural", {})
    door_list       = inst_struct.get("door", []) or []
    front_door_list = inst_struct.get("front_door", []) or []

    door_geom = {}
    for d in door_list + front_door_list:
        did = _as_id(d if isinstance(d, str) else d.get("id"))
        if not did: continue
        dg = _geom_of(d) if isinstance(d, dict) else None
        if dg is not None: door_geom[did] = dg

    def _choose_through_wall(door_id, host_walls, kind):
        if kind == "front_door":
            for wid in host_walls:
                if wall_type.get(wid) == "exterior_wall": return wid
        dg = door_geom.get(door_id)
        if dg is None or dg.is_empty or not host_walls:
            return host_walls[0] if host_walls else None
        c = dg.centroid
        best = (1e18, None)
        for wid in host_walls:
            rs = wall_to_rooms.get(wid, [])
            if not rs: continue
            dmin = min((room_geom[r].centroid.distance(c) for r in rs if r in room_geom), default=1e18)
            if dmin < best[0]: best = (dmin, wid)
        return best[1] or (host_walls[0] if host_walls else None)

    passages = []
    for rec, kind in [(d, "door") for d in door_list] + [(d, "front_door") for d in front_door_list]:
        did = _as_id(rec if isinstance(rec, str) else rec.get("id"))
        if not did: continue
        host_walls = list(dict.fromkeys(opening_to_walls.get(did, [])))
        if not host_walls: continue

        rooms2 = _nearest_two_rooms_on_host_walls(did, door_geom, host_walls, wall_to_rooms, room_geom)

        if kind == "door":
            if len(rooms2) != 2: continue
            rids = rooms2
        else:
            if rooms2:
                rids = [rooms2[0], "OUT-0000"]
            else:
                if any(wall_type.get(w) == "exterior_wall" for w in host_walls):
                    any_rooms = []
                    for w in host_walls: any_rooms += list(wall_to_rooms.get(w, []))
                    any_rooms = [r for r in dict.fromkeys(any_rooms) if r in room_geom]
                    if not any_rooms: continue
                    rids = [any_rooms[0], "OUT-0000"]
                else:
                    continue

        through_wall = _choose_through_wall(did, host_walls, kind)

        types = []
        for r in rids:
            if r == "OUT-0000": types.append("outside")
            else:
                t = plan.get("instances", {}).get("room", {})
                # cepat: ambil dari _build_lookups
                import typing as _t
                # sudah ada room_type di _build_lookups, pakai itu
        passages.append({
            "id": f"E-pass-{len(passages)+1:05d}",
            "door": did,
            "door_type": kind,
            "rooms": rids,
            "through_wall": through_wall,
            "room_types": [("outside" if r=="OUT-0000" else _build_lookups(plan)[1].get(r)) for r in rids]
        })
    return passages


# --- Cell 13 ---
from matplotlib.patches import Patch
from matplotlib.lines import Line2D

def plot_plan_json(
    json_path,
    show_ids=True,
    figsize=(10, 9),
    # label boxes
    label_box_alpha=1,          # room/opening label box alpha
    wall_label_box_alpha=1,     # wall label box alpha
    # fills
    wall_alpha=1,
    door_fill_alpha=1,
    window_fill_alpha=1,
    # label toggles
    show_wall_ids=True,            # << enable/disable wall labels
    show_legend=True
):
    with open(json_path, "r", encoding="utf-8") as f:
        plan = json.load(f)

    rooms_by_type = plan["instances"]["room"]
    struct        = plan["instances"]["structural"]

    fig, ax = plt.subplots(figsize=figsize)
    ax.set_aspect("equal")
    ax.set_axis_off()
    ax.set_title(plan.get("metadata",{}).get("plan_label","Plan"))

    # ---- ROOMS (filled) ----
    for subtype, arr in rooms_by_type.items():
        face = ROOM_COLORS.get(subtype, "#CCCCCC")
        for r in arr:
            g = shape(r["geom"])
            if g.is_empty: 
                continue
            ge = g.exterior if hasattr(g, "exterior") else g
            x, y = ge.xy
            ax.fill(x, y, facecolor=face, edgecolor="black", linewidth=0.5, alpha=0.85)
            if show_ids:
                rp = g.representative_point()
                ax.text(
                    rp.x, rp.y, r["id"],
                    fontsize=7, ha="center", va="center", zorder=5, clip_on=True,
                    bbox=dict(boxstyle="square,pad=0.15", facecolor="white", edgecolor="none", alpha=label_box_alpha)
                )

    # ---- WALLS (filled + labels) ----
    def _draw_walls(key):
        face = STRUCT_COLORS.get(key, "#777777")
        for s in struct.get(key, []):
            g = shape(s["geom"])
            if g.is_empty:
                continue
            if hasattr(g, "exterior"):  # polygonal walls
                x, y = g.exterior.xy
                ax.fill(x, y, facecolor=face, edgecolor="black", linewidth=0.7, alpha=wall_alpha)
            else:
                ge = g
                x, y = ge.xy
                ax.plot(x, y, color=face, linewidth=2.0)
            if show_wall_ids:
                c = g.representative_point()
                ax.text(
                    c.x, c.y, s["id"],
                    fontsize=7, ha="center", va="center",
                    bbox=dict(boxstyle="square,pad=0.15", facecolor="white", edgecolor="none", alpha=wall_label_box_alpha)
                )

    _draw_walls("exterior_wall")
    _draw_walls("interior_wall")

    # ---- OPENINGS (fill polygons; lines otherwise) ----
    def _draw_openings(key, color, fill_alpha, line_lw, line_ls):
        for s in struct.get(key, []):
            g = shape(s["geom"])
            if g.is_empty:
                continue
            if hasattr(g, "exterior"):
                x, y = g.exterior.xy
                ax.fill(x, y, facecolor=color, edgecolor="black", linewidth=0.6, alpha=fill_alpha)
            else:
                ge = g
                x, y = ge.xy
                ax.plot(x, y, color=color, linewidth=line_lw, linestyle=line_ls)
            if show_ids:
                c = g.centroid
                ax.text(
                    c.x, c.y, s["id"], fontsize=7, ha="center", va="center",
                    bbox=dict(boxstyle="square,pad=0.15", facecolor="white", edgecolor="none", alpha=label_box_alpha)
                )

    _draw_openings("door",       STRUCT_COLORS["door"],       door_fill_alpha,   2.2, "-")
    _draw_openings("front_door", STRUCT_COLORS["front_door"], door_fill_alpha,   2.4, "-")
    _draw_openings("window",     STRUCT_COLORS["window"],     window_fill_alpha, 1.8, "--")

    # ---- LEGEND ----
    if show_legend:
        handles = []
        for subtype, color in ROOM_COLORS.items():
            if rooms_by_type.get(subtype):
                handles.append(Patch(facecolor=color, edgecolor="black", label=subtype.title()))
        for sk in ("exterior_wall","interior_wall"):
            if struct.get(sk):
                handles.append(Patch(facecolor=STRUCT_COLORS[sk], edgecolor="black", label=sk.replace("_"," ").title()))
        for sk in ("door","front_door","window"):
            if struct.get(sk):
                handles.append(Patch(facecolor=STRUCT_COLORS[sk], edgecolor="black", label=sk.replace("_"," ").title()))
        if handles:
            ax.legend(handles=handles, loc='center left', bbox_to_anchor=(1,0.5), frameon=False)

    plt.tight_layout()
    plt.show()
    return ax
