"""Legacy aggregator for backwards compatibility.

Prefer importing from the dedicated modules:
- thesis_package.constants
- thesis_package.plan_utils
- thesis_package.geometry
- thesis_package.relations
- thesis_package.visualize
"""

from .constants import (
    GEOM_LAYERS,
    META_KEYS,
    ROOM_COLORS,
    ROOM_KEYS,
    ROOM_PREFIX,
    STRUCT_COLORS,
    STRUCT_KEYS,
)
from .geometry import (
    GeoRec,
    boundary_overlap_length,
    compute_relations,
    find_instances,
    index_instances,
    opening_on_wall,
)
from .plan_utils import (
    assign_ids,
    bbox_of_geom,
    extract_layers,
    extract_metadata,
    extract_room_instances,
    geojsonify,
    instances_from_geom,
    round_float,
    split_walls,
    walls_as_polygons,
)
from .relations import (
    bounded_by_per_room,
    build_connected_via_door_from_hosts,
    nearest_two_rooms_on_host_walls,
    normalize_relation_ids,
)
from .visualize import plot_plan_json

__all__ = [
    # constants
    "GEOM_LAYERS",
    "META_KEYS",
    "ROOM_COLORS",
    "ROOM_KEYS",
    "ROOM_PREFIX",
    "STRUCT_COLORS",
    "STRUCT_KEYS",
    # geometry
    "GeoRec",
    "boundary_overlap_length",
    "compute_relations",
    "find_instances",
    "index_instances",
    "opening_on_wall",
    # plan utilities
    "assign_ids",
    "bbox_of_geom",
    "extract_layers",
    "extract_metadata",
    "extract_room_instances",
    "geojsonify",
    "instances_from_geom",
    "round_float",
    "split_walls",
    "walls_as_polygons",
    # relations
    "bounded_by_per_room",
    "build_connected_via_door_from_hosts",
    "nearest_two_rooms_on_host_walls",
    "normalize_relation_ids",
    # visualize
    "plot_plan_json",
]
