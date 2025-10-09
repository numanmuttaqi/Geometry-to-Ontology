from . import io, geometry, graph, visualize
from .config import PKL_PATH, JSON_DIR, PLOT_DIR, PLOT_LABEL_DIR

# --- Cell 1 ---
# Remap ID helper
ROOM_PREFIX = {
    "bathroom": "BTH",
    "balcony":"BAL",
    "bedroom":"BED",
    "living":"LIV",
    "kitchen":"KIT",
    "corridor":"COR",
    "hall":"HAL",
    "storage":"STRG",
    "toilet":"WC",
    "dining":"DIN",
    "study":"STD",
    "laundry":"LDY",
    "stair":"STR",  
    "veranda":"VER",
    "parking":"PRK",
}

def _as_id(x):
    if isinstance(x, str): return x
    if isinstance(x, dict): return x.get("id")
    return None

def _map_id(value, id_map):
    # Selalu kembalikan tipe yang kompatibel; tidak pernah None kecuali input None.
    if value is None:
        return None
    if isinstance(value, str):
        return id_map.get(value, value)
    if isinstance(value, list):
        out = []
        for v in value:
            if isinstance(v, str):
                out.append(id_map.get(v, v))
            elif isinstance(v, dict):
                vid = _as_id(v)
                nv = dict(v)
                if vid is not None:
                    nv["id"] = id_map.get(vid, vid)
                out.append(nv)
            else:
                out.append(v)
        return out
    if isinstance(value, dict):
        vid = _as_id(value)
        nv = dict(value)
        if vid is not None:
            nv["id"] = id_map.get(vid, vid)
        return nv
    return value

def _update_rel_table(tbl, slots, remap):
    """
    Remap ID dalam tabel relasi. Return jumlah perubahan (int).
    Kompatibel untuk field tunggal (string) maupun list 'rooms'.
    """
    if not isinstance(tbl, list) or not remap:
        return 0
    if not isinstance(slots, (list, tuple)):
        slots = [slots]

    changed = 0
    for e in tbl:
        if not isinstance(e, dict):
            continue
        for slot in slots:
            if slot == "rooms" and isinstance(e.get(slot), list):
                before = e[slot]
                after = [remap.get(x, x) for x in before]
                if after != before:
                    changed += sum(1 for b, a in zip(before, after) if a != b)
                e[slot] = after
            else:
                v = e.get(slot)
                if isinstance(v, str) and v in remap:
                    e[slot] = remap[v]
                    changed += 1
    return changed


# --- Cell 8 ---
def _nearest_two_rooms_on_host_walls(door_id, door_geom_map, host_walls, wall_to_rooms, room_geom):
    cand = []
    for w in host_walls:
        cand.extend(list(wall_to_rooms.get(w, [])))
    cand = list(dict.fromkeys(cand))
    if not cand: return []

    dg = door_geom_map.get(door_id)
    if dg is None or dg.is_empty: return cand[:2]

    c = dg.centroid
    scored = []
    for rid in cand:
        rg = room_geom.get(rid)
        if rg is None or rg.is_empty: continue
        scored.append((rg.distance(c), rid))
    scored.sort(key=lambda x: x[0])

    out = []
    for _, rid in scored:
        if rid not in out: out.append(rid)
        if len(out) == 2: break
    return out
