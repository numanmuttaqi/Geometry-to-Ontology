"""Helpers to reconstruct the original plan JSON structure from a Turtle file.

This is the reverse of ``ontology/json_to_ttl.py`` for quick visualization:

```python
from pathlib import Path
from thesis_package.ttl_to_json import save_ttl_as_json
from thesis_package.visualize import plot_plan_json

ttl_path = Path("output/inferred_resplan_ttl/plan_00000_constructed.ttl")
json_path = save_ttl_as_json(ttl_path)
ax = plot_plan_json(json_path, show_ids=True)
```

Only geometry + basic metadata are restored (enough for ``plot_plan_json``).
Relationship reconstruction can be added later if needed.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional

from rdflib import Graph, Namespace, RDF
from rdflib.namespace import RDFS, XSD

from .constants import ROOM_KEYS, STRUCT_KEYS

RESPLAN = Namespace("http://resplan.org/resplan#")
BOT = Namespace("https://w3id.org/bot#")

# Reverse mappings of the ones in ontology/json_to_ttl.py
ROOM_CLASS_TO_KEY = {
    RESPLAN.LivingRoom: "living",
    RESPLAN.Bedroom: "bedroom",
    RESPLAN.Kitchen: "kitchen",
    RESPLAN.Bathroom: "bathroom",
    RESPLAN.Balcony: "balcony",
    RESPLAN.Storage: "storage",
    RESPLAN.Stair: "stair",
    RESPLAN.Veranda: "veranda",
    RESPLAN.Parking: "parking",
}

STRUCT_CLASS_TO_KEY = {
    RESPLAN.InteriorWall: "interior_wall",
    RESPLAN.ExteriorWall: "exterior_wall",
    RESPLAN.Door: "door",
    RESPLAN.FrontDoor: "front_door",
    RESPLAN.Window: "window",
}


def _local_id(uri) -> str:
    s = str(uri)
    if "#" in s:
        return s.split("#", 1)[1]
    return s.rsplit("/", 1)[-1]


def _literal_float(graph: Graph, subj, pred) -> Optional[float]:
    val = graph.value(subj, pred)
    if val is None:
        return None
    try:
        return float(val)
    except Exception:
        return None


def _geom_props(graph: Graph, subj) -> Dict[str, Any]:
    cx = _literal_float(graph, subj, RESPLAN.centroidX)
    cy = _literal_float(graph, subj, RESPLAN.centroidY)
    bbox_vals = [
        _literal_float(graph, subj, RESPLAN.bboxMinX),
        _literal_float(graph, subj, RESPLAN.bboxMinY),
        _literal_float(graph, subj, RESPLAN.bboxMaxX),
        _literal_float(graph, subj, RESPLAN.bboxMaxY),
    ]
    bbox = bbox_vals if all(v is not None for v in bbox_vals) else None
    props: Dict[str, Any] = {}
    area = _literal_float(graph, subj, RESPLAN.roomArea) or _literal_float(graph, subj, RESPLAN.area)
    if area is not None:
        props["area"] = area
    if cx is not None and cy is not None:
        props["centroid"] = [cx, cy]
    if bbox is not None:
        props["bbox"] = bbox
    return props


def _empty_instances() -> Dict[str, Dict[str, list]]:
    return {
        "room": {key: [] for key in ROOM_KEYS},
        "structural": {key: [] for key in STRUCT_KEYS},
    }


def _room_type(graph: Graph, subj) -> Optional[str]:
    room_cls = graph.value(subj, RESPLAN.hasRoomType)
    if room_cls in ROOM_CLASS_TO_KEY:
        return ROOM_CLASS_TO_KEY[room_cls]
    # Fallback: infer from rdf:type if hasRoomType missing
    for _, _, cls in graph.triples((subj, RDF.type, None)):
        if cls in ROOM_CLASS_TO_KEY:
            return ROOM_CLASS_TO_KEY[cls]
    return None


def _struct_type(graph: Graph, subj) -> Optional[str]:
    for _, _, cls in graph.triples((subj, RDF.type, None)):
        if cls in STRUCT_CLASS_TO_KEY:
            return STRUCT_CLASS_TO_KEY[cls]
    return None


def ttl_to_plan_dict(ttl_path: str | Path) -> Dict[str, Any]:
    """Parse a ResPlan TTL file back into the minimal JSON shape used by plot_plan_json."""
    ttl_path = Path(ttl_path)
    graph = Graph()
    graph.parse(ttl_path)

    plan_dict: Dict[str, Any] = {"metadata": {}, "instances": _empty_instances()}

    # Grab the plan node (first ResPlan instance).
    plan_node = next(graph.subjects(RDF.type, RESPLAN.ResPlan), None)
    if plan_node:
        label = graph.value(plan_node, RDFS.label)
        if label:
            plan_dict["metadata"]["plan_label"] = str(label)
        # Optional numeric metadata
        for pred, key in (
            (RESPLAN.planArea, "area"),
            (RESPLAN.netArea, "net_area"),
            (RESPLAN.wallDepth, "wall_depth"),
        ):
            val = _literal_float(graph, plan_node, pred)
            if val is not None:
                plan_dict["metadata"][key] = val
        unit_type = graph.value(plan_node, RESPLAN.unitType)
        if unit_type:
            plan_dict["metadata"]["unitType"] = str(unit_type)

    # Rooms
    for subj in graph.subjects(RDF.type, BOT.Space):
        geom_lit = graph.value(subj, RESPLAN.geomJSON)
        if geom_lit is None:
            continue
        room_key = _room_type(graph, subj) or "room"
        record = {
            "id": _local_id(subj),
            "type": room_key,
            "geom": json.loads(str(geom_lit)),
            "props": _geom_props(graph, subj),
        }
        plan_dict["instances"]["room"].setdefault(room_key, []).append(record)

    # Structural elements
    for geom_subj, geom_lit in graph.subject_objects(RESPLAN.geomJSON):
        if graph.value(geom_subj, BOT.hasSpace) or (geom_subj, RDF.type, BOT.Space) in graph:
            # Skip rooms already handled
            continue
        struct_key = _struct_type(graph, geom_subj)
        if struct_key is None:
            continue
        record = {
            "id": _local_id(geom_subj),
            "type": struct_key,
            "geom": json.loads(str(geom_lit)),
            "props": _geom_props(graph, geom_subj),
        }
        plan_dict["instances"]["structural"].setdefault(struct_key, []).append(record)

    return plan_dict


def save_ttl_as_json(ttl_path: str | Path, output_path: str | Path | None = None) -> Path:
    """Convert TTL back to JSON file. Returns the written JSON path."""
    plan_dict = ttl_to_plan_dict(ttl_path)
    output_path = Path(output_path) if output_path else Path(ttl_path).with_suffix(".json")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(plan_dict, indent=2), encoding="utf-8")
    return output_path


__all__ = ["ttl_to_plan_dict", "save_ttl_as_json"]
