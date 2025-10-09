from pathlib import Path

ROOT = Path(__file__).parent.parent[1]
DATA = ROOT / "data"
OUTPUT = ROOT / "output"
PLOT_DIR = OUTPUT / "resplan_plot"
PLOT_LABEL_DIR = OUTPUT / "resplan_plotlabel"
JSON_DIR = OUTPUT / "resplan_json"
PKL_PATH = DATA / "ResPlan.pkl"
for d in [OUTPUT, PLOT_DIR, PLOT_LABEL_DIR, JSON_DIR]:
    d.mkdir(parents=True, exist_ok=True)

# --- Cell 12 ---
def assemble_json(plan, idx, json_relpath, plot_relpath):
    p      = R.normalize_keys(plan.copy())
    rooms  = extract_room_instances(p)   # rooms sudah pakai ROOM_PREFIX (langkah #1)
    struct = split_walls(p)
    layers = extract_layers(p)
    meta   = extract_metadata(p, plan_idx=idx, json_relpath=json_relpath, plot_relpath=plot_relpath)

    # >>> relabel & propagate SEBELUM export_graph <<<
    tmp_plan_for_remap = {"instances": {"room": rooms}, "graph": {"relations": {}}}
    _ = relabel_rooms_with_subtype_prefixes_inplace(tmp_plan_for_remap)
    rooms = tmp_plan_for_remap["instances"]["room"]  # rooms sudah ter-relabel konsisten

    graph  = export_graph(p, rooms, struct)  # compute_relations → bounded_by memakai ID baru

    room_counts   = {k: len(rooms[k]) for k in ROOM_KEYS}
    rooms_total   = sum(room_counts.values())
    struct_counts = {k: len(struct[k]) for k in STRUCT_KEYS}

    return {
        "metadata": meta,
        "instances": {"room": rooms, "structural": struct},
        "geom": layers,
        "graph": graph,
        "counts": {
            "rooms_total": rooms_total, "room": room_counts, "structural": struct_counts
        },
        "relationships": {
            "summary": {
                "total_relationships": len(graph["edges"]),
                "adjacency_count": sum(1 for e in graph["edges"] if e["type"] == "adjacent"),
                "door_connections": sum(1 for e in graph["edges"] if e["type"] == "connected_via_door"),
                "bounded_by_count": sum(1 for e in graph["edges"] if e["type"] == "bounded_by"),
                "hosts_opening_count": sum(1 for e in graph["edges"] if e["type"] == "hosts_opening")
            }
        }
    }

def export_one(idx, plan):
    json_path = os.path.join(OUT_JSON_DIR, f"plan_{idx:05d}.json")
    plot_path = os.path.join(OUT_PLOT_DIR,  f"plan_{idx:05d}.png")

    j = assemble_json(plan, idx, json_path, plot_path)

    # relabel room IDs (BTH/BAL/etc.) BEFORE saving, so edges and relations are aligned
    _ = relabel_rooms_with_subtype_prefixes_inplace(j)

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(j, f, ensure_ascii=False, indent=2)

    ax = R.plot_plan(plan, title=f"Plan #{idx}")
    ax.get_figure().savefig(plot_path, bbox_inches="tight", dpi=150)
    plt.close(ax.get_figure())
    return json_path


# --- Cell 14 ---
with open(DATA_PKL, "rb") as f:
    plans = pickle.load(f)

idx = 0
plan = R.normalize_keys(plans[idx].copy())
print("Total plans:", len(plans), " sampled idx:", idx)

json_path = export_one(idx, plan)
print("exported:", json_path)

with open(json_path, "r", encoding="utf-8") as f:
    enhanced_json = json.load(f)

print("\nGraph statistics:")
print(f"- Nodes: {enhanced_json['graph']['statistics']['total_nodes']}")
print(f"- Edges: {enhanced_json['graph']['statistics']['total_edges']}")
print(f"- Relationship types: {enhanced_json['graph']['statistics']['relationship_types']}")
print("\nRoom counts:", enhanced_json["counts"]["room"])
print("Structural counts:", enhanced_json["counts"]["structural"])

print("\nRelations summary:")
for k,v in enhanced_json["graph"]["relations"].items():
    print(f"- {k}: {len(v)}")


# --- Cell 16 ---
# import pickle
# import json
# import os
# from tqdm import tqdm
# import matplotlib.pyplot as plt

# # Load all plans
# with open(DATA_PKL, "rb") as f:
#     plans = pickle.load(f)

# # Create output directories
# OUT_JSON_DIR = "resplan_json"
# OUT_PNG_DIR = "resplan_png"
# OUT_PLOTLABEL_DIR = "resplan_plotlabel"

# os.makedirs(OUT_JSON_DIR, exist_ok=True)
# os.makedirs(OUT_PNG_DIR, exist_ok=True)
# os.makedirs(OUT_PLOTLABEL_DIR, exist_ok=True)

# print(f"Total plans to process: {len(plans)}")

# # Process all plans
# failed_indices = []

# for idx in tqdm(range(len(plans)), desc="Processing plans"):
#     try:
#         # Normalize and export to JSON
#         plan = R.normalize_keys(plans[idx].copy())
#         json_path = export_one(idx, plan)
        
#         # Move JSON to dedicated directory if needed
#         target_json = os.path.join(OUT_JSON_DIR, f"plan_{idx:05d}.json")
#         if json_path != target_json:
#             os.rename(json_path, target_json)
#             json_path = target_json
        
#         # Generate unlabeled PNG
#         ax = plot_plan_json(json_path, show_ids=False)
#         fig = ax.get_figure() if hasattr(ax, "get_figure") else plt.gcf()
#         out_png = os.path.join(OUT_PNG_DIR, f"plan_{idx:05d}.png")
#         fig.savefig(out_png, dpi=200, bbox_inches="tight")
#         plt.close(fig)
        
#         # Generate labeled PNG with IDs
#         ax = plot_plan_json(json_path, show_ids=True)
#         fig = ax.get_figure() if hasattr(ax, "get_figure") else plt.gcf()
#         out_labeled = os.path.join(OUT_PLOTLABEL_DIR, f"plan_{idx:05d}_ids.png")
#         fig.savefig(out_labeled, dpi=200, bbox_inches="tight")
#         plt.close(fig)
        
#     except Exception as e:
#         failed_indices.append(idx)
#         print(f"\nFailed at index {idx}: {e}")
#         continue

# print(f"\nProcessing complete.")
# print(f"Successful: {len(plans) - len(failed_indices)}")
# print(f"Failed: {len(failed_indices)}")

# if failed_indices:
#     print(f"Failed indices: {failed_indices[:20]}...")  # Show first 20
#     with open("failed_indices.txt", "w") as f:
#         f.write("\n".join(map(str, failed_indices)))
