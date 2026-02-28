"""Helpers for extracting structured instances and metadata from plan geometries."""
from __future__ import annotations

from copy import deepcopy
import math
from typing import Any, Dict, Iterable, List, Tuple

from shapely import affinity
from shapely.geometry import (
    GeometryCollection,
    LineString,
    LinearRing,
    MultiLineString,
    MultiPolygon,
    Point,
    Polygon,
    shape,
)
from shapely.geometry.base import BaseGeometry
from shapely.geometry import mapping as shp_mapping
from shapely.ops import split as shp_split, unary_union

import resplan_utils as R

from .config import PKL_PATH, JSON_DIR, PLOT_DIR
from .constants import GEOM_LAYERS, META_KEYS, ROOM_KEYS, ROOM_PREFIX


def round_float(value: Any, ndigits: int = 6) -> Any:
    """Safely round numeric values; fall back to the original value when coercion fails."""
    try:
        return round(float(value), ndigits)
    except Exception:
        return value


def format_metric(value: Any, ndigits: int = 2) -> Any:
    """Round metric quantities to the desired decimal precision."""
    return round_float(value, ndigits)


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
    return [
        format_metric(minx),
        format_metric(miny),
        format_metric(maxx),
        format_metric(maxy),
    ]


def assign_ids(count: int, prefix: str) -> List[str]:
    """Generate deterministic identifiers with a `PREFIX-##` pattern."""
    return [f"{prefix}-{i:02d}" for i in range(1, count + 1)]


def extract_bbox(record: Dict[str, Any]) -> List[float] | None:
    """
    Return the bounding box stored on a record, or compute it from geometry if needed.
    """
    if not isinstance(record, dict):
        return None
    props = record.get("props")
    if isinstance(props, dict):
        bbox = props.get("bbox")
        if isinstance(bbox, (list, tuple)) and len(bbox) == 4:
            try:
                return [float(bbox[0]), float(bbox[1]), float(bbox[2]), float(bbox[3])]
            except (TypeError, ValueError):
                pass
    geom = record.get("geom")
    if isinstance(geom, dict):
        try:
            shapely_geom = shape(geom)
        except Exception:
            shapely_geom = None
        if shapely_geom is not None:
            return bbox_of_geom(shapely_geom)
    return None


def scale_plan_to_meters(plan: Dict[str, Any], area_tolerance: float = 0.05) -> Tuple[Dict[str, Any], Dict[str, float]]:
    """Return a copy of `plan` whose geometries/lengths are scaled to meters."""
    def fmt(value: Any, ndigits: int = 2) -> Any:
        return round_float(value, ndigits)

    def _scale_geom_value(value, factor: float):
        if value is None:
            return None
        if isinstance(value, BaseGeometry):
            return affinity.scale(value, xfact=factor, yfact=factor, origin=(0.0, 0.0))
        if isinstance(value, list):
            return [_scale_geom_value(v, factor) for v in value]
        if isinstance(value, tuple):
            return tuple(_scale_geom_value(list(value), factor))
        if isinstance(value, dict):
            return {k: _scale_geom_value(v, factor) for k, v in value.items()}
        return value

    target_area = (
        plan.get("net_area")
        or plan.get("metadata", {}).get("net_area")
        or plan.get("metadata", {}).get("area")
    )
    inner_geom = plan.get("inner")
    if not target_area or inner_geom is None or getattr(inner_geom, "is_empty", True):
        return deepcopy(plan), {"factor": 1.0, "computed_net_area": None, "mismatch_pct": None, "area_match": None}

    raw_area = float(inner_geom.area or 0.0)
    if raw_area <= 0:
        return deepcopy(plan), {"factor": 1.0, "computed_net_area": None, "mismatch_pct": None, "area_match": None}

    factor = math.sqrt(float(target_area) / raw_area)
    scaled = deepcopy(plan)

    for key, value in list(scaled.items()):
        if key in ("metadata", "instances", "graph", "relationships"):
            continue
        scaled[key] = _scale_geom_value(value, factor)

    # Scale numeric widths if present
    for width_key in ("wall_width", "wall_depth", "door_width", "window_width"):
        if isinstance(scaled.get(width_key), (int, float)):
            scaled[width_key] = float(scaled[width_key]) * factor

    computed_inner = scaled.get("inner")
    computed_area = float(computed_inner.area) if computed_inner is not None else None
    mismatch_pct = None
    area_match = None
    if computed_area is not None and float(target_area) > 0:
        mismatch_pct = abs(computed_area - float(target_area)) / float(target_area)
        area_match = mismatch_pct <= area_tolerance

    return scaled, {
        "factor": fmt(factor, ndigits=4),
        "computed_net_area": fmt(computed_area) if computed_area is not None else None,
        "mismatch_pct": fmt(mismatch_pct, ndigits=4) if mismatch_pct is not None else None,
        "area_match": area_match,
    }


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


def _is_axis_aligned_rectangle(poly: Polygon, tol: float = 1e-6) -> bool:
    coords = list(poly.exterior.coords)
    if len(coords) != 5:
        return False
    minx, miny, maxx, maxy = poly.bounds
    return abs(poly.area - (maxx - minx) * (maxy - miny)) <= tol


def _find_reflex_vertices(poly: Polygon, tol: float = 1e-9) -> List[Tuple[float, float]]:
    coords = list(poly.exterior.coords)
    if len(coords) < 4:
        return []
    ring = LinearRing(coords)
    is_ccw = ring.is_ccw
    coords = coords[:-1]
    n = len(coords)
    reflex: List[Tuple[float, float]] = []
    for i in range(n):
        ax, ay = coords[i - 1]
        bx, by = coords[i]
        cx, cy = coords[(i + 1) % n]
        v1x, v1y = bx - ax, by - ay
        v2x, v2y = cx - bx, cy - by
        cross = v1x * v2y - v1y * v2x
        if (is_ccw and cross < -tol) or ((not is_ccw) and cross > tol):
            reflex.append((bx, by))
    return reflex


def _build_splitter(poly: Polygon, vertex: Tuple[float, float], axis: str) -> LineString:
    minx, miny, maxx, maxy = poly.bounds
    if axis == "vertical":
        return LineString([(vertex[0], miny - 1.0), (vertex[0], maxy + 1.0)])
    return LineString([(minx - 1.0, vertex[1]), (maxx + 1.0, vertex[1])])


def _rectilinear_split(poly: Polygon, tol: float = 1e-9) -> List[Polygon]:
    if _is_axis_aligned_rectangle(poly):
        return [poly]
    reflex_vertices = _find_reflex_vertices(poly, tol=tol)
    if not reflex_vertices:
        return [poly]

    minx, miny, maxx, maxy = poly.bounds
    width = maxx - minx
    height = maxy - miny
    axis_order = ["vertical", "horizontal"] if width >= height else ["horizontal", "vertical"]

    for vertex in reflex_vertices:
        for axis in axis_order:
            splitter = _build_splitter(poly, vertex, axis)
            split_result = shp_split(poly, splitter)
            parts = [geom for geom in split_result.geoms if geom.area > tol]
            if len(parts) > 1:
                rectangles: List[Polygon] = []
                for part in parts:
                    rectangles.extend(_rectilinear_split(part, tol))
                return rectangles
    return [poly]


def _rectilinearize(polygons: Iterable[Polygon]) -> List[Polygon]:
    rectified: List[Polygon] = []
    for poly in polygons:
        if not isinstance(poly, Polygon):
            continue
        rectified.extend(_rectilinear_split(poly))
    return rectified


def instances_from_geom(
    category: str,
    geom,
    min_area: float = 2.0,
    rectilinearize: bool = False,
) -> List[Dict[str, Any]]:
    """Convert a geometry into instance dictionaries when shapes are significant."""
    if geom is None or getattr(geom, "is_empty", True):
        return []

    polygons = list(_iter_polygons(geom))
    if rectilinearize:
        polygons = _rectilinearize(polygons)
    if not polygons:
        return []

    valid_polygons: List[Polygon] = []
    for poly in polygons:
        if not isinstance(poly, Polygon):
            continue
        area = poly.area
        if area < min_area:
            continue
        if not poly.is_valid:
            continue
        try:
            minx, miny, maxx, maxy = poly.bounds
            width = maxx - minx
            height = maxy - miny
            aspect_ratio = max(width, height) / (min(width, height) + 1e-6)
            if aspect_ratio > 50:
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
                    "area": format_metric(poly.area),
                    "centroid": (format_metric(centroid.x), format_metric(centroid.y)),
                    "bbox": bbox_of_geom(poly),
                },
            }
        )
    return instances


def split_walls(
    plan: Dict[str, Any],
    band_factor: float = 1.0,
    band_min_frac: float = 0.005,
    fallback_frac: float = 0.01,
) -> Dict[str, List[Dict[str, Any]]]:
    """Split walls into interior/exterior bands and extract structural instances."""
    normalized = R.normalize_keys(plan.copy())
    inner = normalized["inner"]
    if inner.geom_type == "MultiPolygon":
        inner = max(inner.geoms, key=lambda geom: geom.area)
    width = R.get_plan_width(normalized) or 1.0
    wall_thickness = float(
        normalized.get("wall_width")
        or normalized.get("wall_depth")
        or 0.2
    )
    band_half_width = max(band_factor * wall_thickness, band_min_frac * width)

    walls_poly = walls_as_polygons(normalized, fallback_frac=fallback_frac)
    boundary_band = inner.boundary.buffer(band_half_width, join_style=2, cap_style=2)

    exterior_wall = walls_poly.intersection(boundary_band).buffer(0)
    interior_wall = walls_poly.difference(boundary_band).buffer(0)

    return {
        "interior_wall": instances_from_geom(
            "interior_wall",
            interior_wall,
            min_area=0.001,
            rectilinearize=True,
        ),
        "exterior_wall": instances_from_geom(
            "exterior_wall",
            exterior_wall,
            min_area=0.001,
            rectilinearize=True,
        ),
        "door": instances_from_geom("door", normalized.get("door"), min_area=0.0025),
        "window": instances_from_geom("window", normalized.get("window"), min_area=0.0025),
        "front_door": instances_from_geom("front_door", normalized.get("front_door"), min_area=0.0025),
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

        # drop duplicate placements that share the same bounding box
        seen_bbox = set()
        unique_geometries = []
        for geom_obj in geometries:
            bbox = tuple(bbox_of_geom(geom_obj))
            if None in bbox or len(bbox) != 4:
                unique_geometries.append(geom_obj)
                continue
            if bbox in seen_bbox:
                continue
            seen_bbox.add(bbox)
            unique_geometries.append(geom_obj)
        geometries = unique_geometries

        prefix = ROOM_PREFIX.get(subtype)
        if not prefix:
            raise ValueError(f"ROOM_PREFIX missing for subtype '{subtype}'")

        ids = assign_ids(len(geometries), prefix)
        for instance_id, geom_obj in zip(ids, geometries, strict=False):
            centroid = geom_obj.centroid if geom_obj is not None else None
            centroid_xy = (None, None)
            if centroid is not None and not geom_obj.is_empty:
                centroid_xy = (format_metric(centroid.x), format_metric(centroid.y))
            result[subtype].append(
                {
                    "id": instance_id,
                    "type": subtype,
                    "geom": geojsonify(geom_obj),
                    "props": {
                        "area": format_metric(getattr(geom_obj, "area", 0.0)),
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
    "scale_plan_to_meters",
    "round_float",
    "format_metric",
    "split_walls",
    "walls_as_polygons",
]
