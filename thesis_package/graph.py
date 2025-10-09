from .config import JSON_DIR

# --- Cell 4 ---
def _update_rel_table(tbl, slots, remap):
    if not isinstance(tbl, list): return
    for e in tbl:
        for slot in slots:
            if slot == "rooms" and isinstance(e.get(slot), list):
                e[slot] = [remap.get(x, x) for x in e[slot]]
            else:
                v = e.get(slot)
                if v in remap: e[slot] = remap[v]

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
    rel.update(_norm_relations_ids(rel))
    passages = build_connected_via_door_from_hosts(plan)
    rel["connected_via_door"] = passages
    plan.setdefault("relationships", {}).setdefault("summary", {})
    plan["relationships"]["summary"]["door_connections"] = len(passages)
    return passages


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
    relations = _norm_relations_ids(relations)
    mock_plan.setdefault("graph", {}).setdefault("relations", relations)

    relations["bounded_by_per_room"] = _bounded_by_per_room(relations)

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
    for rel in relations["bounded_by"]:
        edges.append({"source": rel["room"], "target": rel["wall"], "type": "bounded_by",
                      "properties": {"length": rel["length"], "wall_type": rel["wall_type"]}})
    for rel in relations["hosts_opening"]:
        edges.append({"source": rel["wall"], "target": rel["opening"], "type": "hosts_opening",
                      "properties": {"opening_type": rel["opening_type"]}})

    return {
        "nodes": nodes, "edges": edges, "relations": relations,
        "statistics": {
            "total_nodes": len(nodes), "total_edges": len(edges),
            "relationship_types": list(relations.keys())
        }
    }
