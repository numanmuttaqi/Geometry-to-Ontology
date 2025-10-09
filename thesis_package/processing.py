from . import io, geometry, graph, visualize
from .config import PKL_PATH, JSON_DIR, PLOT_DIR, PLOT_LABEL_DIR
from .constants import ROOM_PREFIX

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


def run_pipeline(cfg):
    """
    Run the full processing pipeline.
    Return dict artefact: data antar tahap, path output, dan figures.
    """
    data = io.load_data(cfg.PKL_PATH)
    rooms, walls = process_to_instances(data)
    G = graph.build_graph(rooms, walls)
    fig1 = visualize.plot_plan(rooms, walls)       # return matplotlib.figure.Figure
    fig2 = visualize.plot_graph(G)                 # return Figure
    out = {
        "rooms": rooms,
        "walls": walls,
        "graph": G,
        "fig_plan": fig1,
        "fig_graph": fig2,
        "json_dir": cfg.JSON_DIR,
        "plots_dir": cfg.PLOT_DIR,
    }
    return out
