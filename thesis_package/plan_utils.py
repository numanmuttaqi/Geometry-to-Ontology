"""Helpers for extracting structured instances and metadata from plan geometries."""

from __future__ import annotations

from typing import Any, Dict, Iterable, List

from shapely.geometry import (
    GeometryCollection,
    LineString,
    MultiLineString,
    MultiPolygon,
    Point,
    Polygon,
    shape,
)
from shapely.geometry import mapping as shp_mapping
from shapely.ops import unary_union

import resplan_utils as R

from .config import PKL_PATH, JSON_DIR, PLOT_DIR
from .constants import GEOM_LAYERS, META_KEYS, ROOM_KEYS, ROOM_PREFIX


def round_float(value: Any, ndigits: int = 6) -> Any:
    """Safely round numeric values; fall back to the original value when coercion fails."""
    try:
        return round(float(value), ndigits)
    except Exception:
        return value


def geojsonify(geom) -> Dict[str, Any]:
    """Convert shapely geometries (or shapely-like records) to GeoJSON dictionaries."""
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


def bbox_of_geom(geom) -> List[float | None]:
    """Return bounding box [minx, miny, maxx, maxy] for shapely geometries."""
    if geom is None or getattr(geom, "is_empty", True):
        return [None, None, None, None]
    minx, miny, maxx, maxy = geom.bounds
    return [float(minx), float(miny), float(maxx), float(maxy)]


def assign_ids(count: int, prefix: str) -> List[str]:
    """Generate deterministic identifiers with a `PREFIX-####` pattern."""
    return [f"{prefix}-{i:04d}" for i in range(1, count + 1)]


def walls_as_polygons(plan: Dict[str, Any], fallback_frac: float = 0.01):
    """Approximate wall geometries as polygons, buffering lines when needed."""
    width = R.get_plan_width(plan) or 1.0
    buffer_width = fallback_frac * width
    polygons: List[Polygon] = []
    for geom in R.get_geometries(plan.get("wall")):
        if isinstance(geom, Polygon):
            polygons.append(geom)
        elif isinstance(geom, MultiPolygon):
            polygons.extend(list(geom.geoms))
        elif isinstance(geom, (LineString, MultiLineString)):
            polygons.append(geom.buffer(buffer_width, join_style=2, cap_style=2))
    if not polygons:
        return GeometryCollection()
    return unary_union(polygons).buffer(0)


def _iter_polygons(geom) -> Iterable[Polygon]:
    """Yield polygon parts from a shapely geometry object."""
    if isinstance(geom, Polygon):
        yield geom
    elif isinstance(geom, MultiPolygon):
        yield from geom.geoms
    elif geom is not None and hasattr(geom, "__iter__"):
        for part in geom:
            yield from _iter_polygons(part)


def instances_from_geom(category: str, geom, min_area: float = 2.0) -> List[Dict[str, Any]]:
    """Convert a geometry into instance dictionaries when shapes are significant."""
    if geom is None or getattr(geom, "is_empty", True):
        return []

    polygons = list(_iter_polygons(geom))
    if not polygons:
        print(f"WARNING: Unexpected geometry type for {category}: {getattr(geom, 'geom_type', type(geom))}")
        return []

    valid_polygons: List[Polygon] = []
    for poly in polygons:
        if not isinstance(poly, Polygon):
            continue
        area = poly.area
        if area < min_area:
            print(f"SKIPPED: {category} fragment with area {area:.2f}m²")
            continue
        if not poly.is_valid:
            print(f"SKIPPED: Invalid {category} geometry")
            continue
        try:
            minx, miny, maxx, maxy = poly.bounds
            width = maxx - minx
            height = maxy - miny
            aspect_ratio = max(width, height) / (min(width, height) + 1e-6)
            if aspect_ratio > 50:
                print(f"SKIPPED: {category} sliver (aspect ratio {aspect_ratio:.1f})")
                continue
        except Exception:
            pass
        valid_polygons.append(poly)

    if not valid_polygons:
        return []

    ids = assign_ids(len(valid_polygons), category[:2].upper())
    instances: List[Dict[str, Any]] = []
    for instance_id, poly in zip(ids, valid_polygons, strict=False):
        centroid = poly.centroid
        instances.append(
            {
                "id": instance_id,
                "type": category,
                "geom": geojsonify(poly),
                "props": {
                    "area": float(poly.area),
                    "centroid": (float(centroid.x), float(centroid.y)),
                    "bbox": bbox_of_geom(poly),
                },
            }
        )
    return instances


def split_walls(
    plan: Dict[str, Any],
    band_factor: float = 1.0,
    band_min_frac: float = 0.02,
    fallback_frac: float = 0.01,
) -> Dict[str, List[Dict[str, Any]]]:
    """Split walls into interior/exterior bands and extract structural instances."""
    normalized = R.normalize_keys(plan.copy())
    inner = normalized["inner"]
    if inner.geom_type == "MultiPolygon":
        inner = max(inner.geoms, key=lambda geom: geom.area)
    width = R.get_plan_width(normalized) or 1.0
    wall_thickness = float(normalized.get("wall_width", 4) or 4)
    band_half_width = max(band_factor * wall_thickness, band_min_frac * width)

    walls_poly = walls_as_polygons(normalized, fallback_frac=fallback_frac)
    boundary_band = inner.boundary.buffer(band_half_width, join_style=2, cap_style=2)

    exterior_wall = walls_poly.intersection(boundary_band).buffer(0)
    interior_wall = walls_poly.difference(boundary_band).buffer(0)

    return {
        "interior_wall": instances_from_geom("interior_wall", interior_wall),
        "exterior_wall": instances_from_geom("exterior_wall", exterior_wall),
        "door": instances_from_geom("door", normalized.get("door")),
        "window": instances_from_geom("window", normalized.get("window")),
        "front_door": instances_from_geom("front_door", normalized.get("front_door")),
    }


def extract_room_instances(plan: Dict[str, Any]) -> Dict[str, List[Dict[str, Any]]]:
    """Extract room instances grouped by subtype and assign prefixed identifiers."""
    single_instance_rooms = {"living", "kitchen"}
    result = {key: [] for key in ROOM_KEYS}

    for subtype in ROOM_KEYS:
        geom = plan.get(subtype)
        if geom is None:
            continue

        geometries: List[Any] = []
        if hasattr(geom, "geoms"):
            geometries = list(geom.geoms)
        elif hasattr(geom, "__iter__"):
            for item in geom:
                if hasattr(item, "geoms"):
                    geometries.extend(list(item.geoms))
                else:
                    geometries.append(item)
        else:
            geometries = [geom]

        geometries = [g for g in geometries if not getattr(g, "is_empty", True)]
        if not geometries:
            continue

        if subtype in single_instance_rooms and len(geometries) > 1:
            merged = unary_union(geometries)
            if merged.geom_type == "MultiPolygon":
                significant = [g for g in merged.geoms if g.area >= 2.0]
                geometries = significant if len(significant) == 1 else [max(merged.geoms, key=lambda g: g.area)]
            elif merged.geom_type == "Polygon":
                geometries = [merged]

        prefix = ROOM_PREFIX.get(subtype)
        if not prefix:
            raise ValueError(f"ROOM_PREFIX missing for subtype '{subtype}'")

        ids = assign_ids(len(geometries), prefix)
        for instance_id, geom_obj in zip(ids, geometries, strict=False):
            centroid = geom_obj.centroid
            centroid_xy = (
                (float(centroid.x), float(centroid.y))
                if centroid is not None and not geom_obj.is_empty
                else (None, None)
            )
            result[subtype].append(
                {
                    "id": instance_id,
                    "type": subtype,
                    "geom": geojsonify(geom_obj),
                    "props": {
                        "area": float(getattr(geom_obj, "area", 0.0)),
                        "centroid": centroid_xy,
                        "bbox": bbox_of_geom(geom_obj),
                    },
                }
            )
    return result


def extract_layers(plan: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """Export raw plan layers as GeoJSON serialisable dictionaries."""
    return {key: geojsonify(plan.get(key)) for key in GEOM_LAYERS}


def extract_metadata(
    plan: Dict[str, Any],
    plan_idx: int,
    json_relpath: str,
    plot_relpath: str,
    dataset_name: str = "ResPlan",
    source_file: str | None = None,
    split: str | None = None,
) -> Dict[str, Any]:
    """Collect metadata fields with sensible defaults for downstream exports."""
    metadata: Dict[str, Any] = {}
    for key in META_KEYS:
        if key in plan:
            metadata[key] = plan[key]

    if "id" in metadata and "plan_id" not in metadata:
        metadata["plan_id"] = metadata["id"]

    metadata.update(
        {
            "dataset": dataset_name,
            "plan_idx": int(plan_idx),
            "plan_label": f"Plan #{plan_idx}",
            "units": "m",
        }
    )
    if split is not None:
        metadata["split"] = split

    metadata["source"] = {"file": source_file or str(PKL_PATH)}
    metadata["artifacts"] = {"json_path": json_relpath or str(JSON_DIR), "plot_path": plot_relpath or str(PLOT_DIR)}
    return metadata


__all__ = [
    "assign_ids",
    "bbox_of_geom",
    "extract_layers",
    "extract_metadata",
    "extract_room_instances",
    "instances_from_geom",
    "geojsonify",
    "round_float",
    "split_walls",
    "walls_as_polygons",
]
