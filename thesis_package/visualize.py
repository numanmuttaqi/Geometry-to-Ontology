"""Visualization helpers for geometry-to-ontology plans."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
from matplotlib.patches import Patch
from shapely.geometry import shape

from .config import PLOT_DIR, PLOT_LABEL_DIR
from .constants import ROOM_COLORS, STRUCT_COLORS


def plot_plan_json(
    json_path: str | Path,
    show_ids: bool = True,
    figsize: tuple[int, int] = (10, 9),
    label_box_alpha: float = 0.6,
    wall_label_box_alpha: float = 1.0,
    wall_alpha: float = 1.0,
    door_fill_alpha: float = 1.0,
    window_fill_alpha: float = 1.0,
    show_wall_ids: bool = True,
    show_legend: bool = True,
):
    """Plot a plan JSON artefact with rooms, walls, and openings."""
    with open(json_path, "r", encoding="utf-8") as fh:
        plan = json.load(fh)

    rooms_by_type = plan["instances"]["room"]
    structural = plan["instances"]["structural"]

    fig, ax = plt.subplots(figsize=figsize)
    ax.set_aspect("equal")
    ax.set_axis_off()
    ax.set_title(plan.get("metadata", {}).get("plan_label", "Plan"))

    def _annotate(ax_obj, geom_obj, text, alpha):
        point = geom_obj.representative_point() if hasattr(geom_obj, "representative_point") else geom_obj.centroid
        ax_obj.text(
            point.x,
            point.y,
            text,
            fontsize=5, 
            ha="center",
            va="center",
            zorder=5,
            clip_on=True,
            bbox=dict(boxstyle="square,pad=0.15", facecolor="white", edgecolor="none", alpha=label_box_alpha),
        )

    # ======================================================
    # Rooms
    # ======================================================
    for subtype, records in rooms_by_type.items():
        face = ROOM_COLORS.get(subtype, "#CCCCCC")
        for record in records:
            geom = shape(record["geom"])
            if geom.is_empty:
                continue
            exterior = geom.exterior if hasattr(geom, "exterior") else geom
            x, y = exterior.xy
            ax.fill(x, y, facecolor=face, edgecolor="black", linewidth=0.5, alpha=0.85)
            if show_ids:
                _annotate(ax, geom, record["id"], label_box_alpha)

    # ======================================================
    # Walls
    # ======================================================
    def _draw_walls(key):
        face = STRUCT_COLORS.get(key, "#777777")
        for record in structural.get(key, []):
            geom = shape(record["geom"])
            if geom.is_empty:
                continue
            
            edge_color = "black"
            lw = 0.7
            ls = "-"
            alpha = wall_alpha

            if hasattr(geom, "exterior"):
                x, y = geom.exterior.xy
                ax.fill(
                    x, y,
                    facecolor=face,
                    edgecolor=edge_color,
                    linewidth=lw,
                    linestyle=ls,
                    alpha=alpha,
                )
            else:
                x, y = geom.xy
                ax.plot(
                    x, y,
                    color=edge_color if is_inferred else face,
                    linewidth=lw,
                    linestyle=ls,
                    alpha=alpha,
                )

            if show_wall_ids:
                _annotate(ax, geom, record["id"], wall_label_box_alpha)

    _draw_walls("exterior_wall")
    _draw_walls("interior_wall")

    # ======================================================
    # Doors and Windows
    # ======================================================
    def _draw_openings(key, color, fill_alpha, line_width, line_style):
        for record in structural.get(key, []):
            geom = shape(record["geom"])
            if geom.is_empty:
                continue
            if hasattr(geom, "exterior"):
                x, y = geom.exterior.xy
                ax.fill(x, y, facecolor=color, edgecolor="black", linewidth=0.6, alpha=fill_alpha)
            else:
                x, y = geom.xy
                ax.plot(x, y, color=color, linewidth=line_width, linestyle=line_style)
            if show_ids:
                centroid = geom.centroid
                ax.text(
                    centroid.x,
                    centroid.y,
                    record["id"],
                    fontsize=5,
                    ha="center",
                    va="center",
                    bbox=dict(boxstyle="square,pad=0.15", facecolor="white", edgecolor="none", alpha=label_box_alpha),
                )

    _draw_openings("door", STRUCT_COLORS["door"], door_fill_alpha, 2.2, "-")
    _draw_openings("front_door", STRUCT_COLORS["front_door"], door_fill_alpha, 2.4, "-")
    _draw_openings("window", STRUCT_COLORS["window"], window_fill_alpha, 1.8, "--")

    if show_legend:
        handles: list[Any] = []
        for subtype, color in ROOM_COLORS.items():
            if rooms_by_type.get(subtype):
                handles.append(Patch(facecolor=color, edgecolor="black", label=subtype.title()))
        for key in ("exterior_wall", "interior_wall"):
            if structural.get(key):
                handles.append(Patch(facecolor=STRUCT_COLORS[key], edgecolor="black", label=key.replace("_", " ").title()))
        for key in ("door", "front_door", "window"):
            if structural.get(key):
                handles.append(Patch(facecolor=STRUCT_COLORS[key], edgecolor="black", label=key.replace("_", " ").title()))
        if handles:
            ax.legend(handles=handles, loc="center left", bbox_to_anchor=(1, 0.5), frameon=False)

    plt.tight_layout()
    return ax


__all__ = ["PLOT_DIR", "PLOT_LABEL_DIR", "plot_plan_json"]
