from .config import DATA, OUTPUT, JSON_DIR, PLOT_DIR, PLOT_LABEL_DIR, PKL_PATH

# --- Cell 15 ---
# 1) make sure an output dir exists
OUT_PLOTLABEL_DIR = "resplan_plotlabel"
os.makedirs(OUT_PLOTLABEL_DIR, exist_ok=True)

# 2) draw from JSON (IDs already relabeled)
ax_or_none = plot_plan_json(json_path, show_ids=True)
fig = ax_or_none.get_figure() if hasattr(ax_or_none, "get_figure") else plt.gcf()

# 4) save and close
out_png = os.path.join(OUT_PLOTLABEL_DIR, f"plan_{idx:05d}_ids.png")
fig.savefig(out_png, dpi=200, bbox_inches="tight")
plt.close(fig)


print("Saved:", out_png)
