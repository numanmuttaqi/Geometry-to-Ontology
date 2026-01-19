#!/usr/bin/env python3
"""Convert exported ResPlan JSON artefacts into Turtle using the ResPlan ontology."""

from __future__ import annotations

import argparse
import json
import logging
from itertools import combinations
from pathlib import Path
from typing import Dict, Iterable, Tuple
from rdflib import BNode, Graph, Literal, Namespace, RDF, URIRef
from rdflib.namespace import RDFS, XSD
from shapely.geometry import shape, mapping

# Namespaces shared with the ontology/rules
RESPLAN = Namespace("http://resplan.org/resplan#")
BOT = Namespace("https://w3id.org/bot#")
IFC = Namespace("https://w3id.org/ifc/IFC4_ADD2#")

OUTSIDE_ID = "OUT-0000"

ROOM_CLASS_MAP = {
    "living"    : RESPLAN.LivingRoom,
    "bedroom"   : RESPLAN.Bedroom,
    "kitchen"   : RESPLAN.Kitchen,
    "bathroom"  : RESPLAN.Bathroom,
    "balcony"   : RESPLAN.Balcony,
    "storage"   : RESPLAN.Storage,
    "stair"     : RESPLAN.Stair,
    "veranda"   : RESPLAN.Veranda,
    "parking"   : RESPLAN.Parking,
}

STRUCT_CLASS_MAP = {
    "interior_wall" : RESPLAN.InteriorWall,
    "exterior_wall" : RESPLAN.ExteriorWall,
    "door"          : RESPLAN.Door,
    "front_door"    : RESPLAN.FrontDoor,
    "window"        : RESPLAN.Window,
}

LOGGER = logging.getLogger("json_to_ttl")


def _ensure_namespace(base: str) -> Namespace:
    base = base.strip()
    if not base:
        raise ValueError("Base namespace cannot be empty.")
    if not base.endswith(("#", "/")):
        base = f"{base}#"
    return Namespace(base)


def _determine_identifier(metadata: Dict, json_path: Path) -> str:
    plan_idx = metadata.get("plan_idx")
    if isinstance(plan_idx, int):
        return f"plan_{plan_idx:05d}"
    try:
        plan_idx_int = int(plan_idx)
        return f"plan_{plan_idx_int:05d}"
    except Exception:
        return json_path.stem


def _literal(number, ndigits: int = 3):
    if number is None:
        return None
    try:
        value = round(float(number), ndigits)
    except Exception:
        return None
    return Literal(value, datatype=XSD.decimal)


def _bbox_spans(bbox: Iterable[float]) -> Tuple[float, float] | None:
    if not bbox or len(bbox) != 4:
        return None
    minx, miny, maxx, maxy = map(float, bbox)
    return maxx - minx, maxy - miny


def _estimate_opening_width(bbox):
    spans = _bbox_spans(bbox)
    if spans is None:
        return None
    return max(spans)


def _shorten_uri(uri: URIRef | str) -> str:
    """Return the local part of a URIRef or string."""
    s = str(uri)
    if "#" in s:
        return s.split("#")[-1]
    return Path(s).name


def _add_geom_literals(graph: Graph, uri: URIRef, entry: Dict) -> None:
    """Attach geometry-related literals (geom JSON, area, centroid, bbox) to a node."""
    geom = entry.get("geom")
    if geom:
        try:
            geom_json = json.dumps(geom, separators=(",", ":"), ensure_ascii=False)
            graph.add((uri, RESPLAN.geomJSON, Literal(geom_json)))
        except Exception:
            LOGGER.debug("Failed to serialize geom for %s", uri)

    props = entry.get("props", {}) or {}
    area = _literal(props.get("area"))
    if area is not None:
        graph.add((uri, RESPLAN.area, area))

    centroid = props.get("centroid") or []
    if len(centroid) == 2:
        cx, cy = centroid
        cx_literal = _literal(cx)
        cy_literal = _literal(cy)
        if cx_literal is not None:
            graph.add((uri, RESPLAN.centroidX, cx_literal))
        if cy_literal is not None:
            graph.add((uri, RESPLAN.centroidY, cy_literal))

    bbox = props.get("bbox") or []
    if len(bbox) == 4:
        minx, miny, maxx, maxy = bbox
        for pred, value in (
            (RESPLAN.bboxMinX, minx),
            (RESPLAN.bboxMinY, miny),
            (RESPLAN.bboxMaxX, maxx),
            (RESPLAN.bboxMaxY, maxy),
        ):
            literal = _literal(value)
            if literal is not None:
                graph.add((uri, pred, literal))


def convert(json_path: Path, output_path: Path | None = None, base_uri: str | None = None) -> Path:
    data = json.loads(json_path.read_text())
    metadata = data.get("metadata", {})
    identifier = _determine_identifier(metadata, json_path)
    base_ns = _ensure_namespace(base_uri or f"http://resplan.org/resplan/{identifier}")

    graph = Graph()
    graph.bind("resplan", RESPLAN)
    graph.bind("bot", BOT)
    graph.bind("ifc", IFC)
    graph.bind("rdfs", RDFS)

    plan_uri = base_ns[f"Plan_{identifier}"]
    graph.add((plan_uri, RDF.type, RESPLAN.ResPlan))
    graph.add((plan_uri, RDFS.label, Literal(metadata.get("plan_label", identifier))))

    _add_plan_metadata(graph, plan_uri, metadata)
    rooms = _add_rooms(graph, base_ns, data.get("instances", {}).get("room", {}))
    structural, structural_entries = _add_structurals(
        graph,
        base_ns,
        data.get("instances", {}).get("structural", {}),
        global_wall_depth=metadata.get("wall_depth"),
    )
    relations = data.get("relations") or data.get("graph", {}).get("relations", {})
    _add_relationships(graph, base_ns, rooms, structural, structural_entries, relations)
    _add_window_memberships(graph, base_ns, rooms, structural, relations)

    output_path = output_path or json_path.with_suffix(".ttl")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    graph.serialize(destination=str(output_path), format="turtle")
    return output_path


def _add_plan_metadata(graph: Graph, plan_uri: URIRef, metadata: Dict) -> None:
    dataset = metadata.get("dataset")
    if dataset:
        graph.add((plan_uri, RESPLAN.datasetName, Literal(dataset)))
    unit = metadata.get("unitType")
    if unit:
        graph.add((plan_uri, RESPLAN.unitType, Literal(unit)))
    area = _literal(metadata.get("area"))
    if area is not None:
        graph.add((plan_uri, RESPLAN.planArea, area))
    net_area = _literal(metadata.get("net_area"))
    if net_area is not None:
        graph.add((plan_uri, RESPLAN.netArea, net_area))
    source = metadata.get("source", {}).get("file")
    if source:
        graph.add((plan_uri, RESPLAN.sourceFile, Literal(Path(source).resolve().as_uri(), datatype=XSD.anyURI)))
    artifacts = metadata.get("artifacts", {})
    json_path = artifacts.get("json_path")
    if json_path:
        graph.add((plan_uri, RESPLAN.jsonPath, Literal(Path(json_path).resolve().as_uri(), datatype=XSD.anyURI)))
    plot_path = artifacts.get("plot_path")
    if plot_path:
        graph.add((plan_uri, RESPLAN.plotPath, Literal(Path(plot_path).resolve().as_uri(), datatype=XSD.anyURI)))
    wall_depth = _literal(metadata.get("wall_depth"))
    if wall_depth is not None:
        graph.add((plan_uri, RESPLAN.wallDepth, wall_depth))


def _add_rooms(graph: Graph, ns: Namespace, rooms_payload: Dict) -> Dict[str, URIRef]:
    room_nodes: Dict[str, URIRef] = {}
    for entries in rooms_payload.values():
        for entry in entries:
            room_id = entry["id"]
            room_uri = ns[room_id]
            room_nodes[room_id] = room_uri
            graph.add((room_uri, RDF.type, BOT.Space))
            graph.add((room_uri, RDF.type, RESPLAN.Room))
            room_label = entry.get("type", room_id)
            graph.add((room_uri, RDFS.label, Literal(room_label)))
            graph.add((room_uri, RESPLAN.sourceId, Literal(room_id)))

            room_type = ROOM_CLASS_MAP.get(entry.get("type", "").lower())
            if room_type:
                graph.add((room_uri, RESPLAN.hasRoomType, room_type))

            _add_geom_literals(graph, room_uri, entry)
            # Keep room-specific area as well
            props = entry.get("props", {})
            room_area = _literal(props.get("area"))
            if room_area is not None:
                graph.add((room_uri, RESPLAN.roomArea, room_area))
    return room_nodes


def _ensure_outside_room(graph: Graph, ns: Namespace, rooms: Dict[str, URIRef]) -> URIRef:
    outside_uri = rooms.get(OUTSIDE_ID)
    if outside_uri:
        return outside_uri
    outside_uri = ns[OUTSIDE_ID]
    rooms[OUTSIDE_ID] = outside_uri
    graph.add((outside_uri, RDF.type, BOT.Space))
    graph.add((outside_uri, RDF.type, RESPLAN.Room))
    graph.add((outside_uri, RDF.type, RESPLAN.EntryNode))
    graph.add((outside_uri, RDFS.label, Literal("Outside")))
    return outside_uri


def _resolve_room(room_id: str | None, graph: Graph, ns: Namespace, rooms: Dict[str, URIRef]) -> URIRef | None:
    if not room_id:
        return None
    if room_id == OUTSIDE_ID:
        return _ensure_outside_room(graph, ns, rooms)
    return rooms.get(room_id)


def _add_structurals(
    graph: Graph,
    ns: Namespace,
    struct_payload: Dict,
    global_wall_depth=None,
) -> Tuple[Dict[str, URIRef], Dict[str, Dict]]:
    structural_nodes: Dict[str, URIRef] = {}
    structural_entries: Dict[str, Dict] = {}
    for entries in struct_payload.values():
        for entry in entries:
            struct_id = entry["id"]
            structural_entries[struct_id] = entry
            struct_type = entry.get("type", "").lower()
            struct_uri = ns[struct_id]
            structural_nodes[struct_id] = struct_uri
            graph.add((struct_uri, RDFS.label, Literal(struct_id)))
            graph.add((struct_uri, RESPLAN.sourceId, Literal(struct_id)))

            class_uri = STRUCT_CLASS_MAP.get(struct_type)
            if class_uri:
                graph.add((struct_uri, RDF.type, class_uri))
                if class_uri == RESPLAN.FrontDoor:
                    graph.add((struct_uri, RDF.type, RESPLAN.Door))

            if struct_type in ("interior_wall", "exterior_wall"):
                graph.add((struct_uri, RDF.type, IFC.Wall))

            if struct_type in ("door", "front_door"):
                width = _estimate_opening_width(entry.get("props", {}).get("bbox"))
                width_literal = _literal(width)
                if width_literal is not None:
                    graph.add((struct_uri, RESPLAN.width, width_literal))

            # Geometry props for all structural elements
            _add_geom_literals(graph, struct_uri, entry)

            # Apply global wall depth if present in metadata (propagated via plan)
            if struct_type in ("interior_wall", "exterior_wall"):
                # wall depth may be stored per element in props or globally in plan metadata; prefer element
                wall_depth = entry.get("props", {}).get("depth") or entry.get("props", {}).get("wall_depth")
                if wall_depth is None:
                    wall_depth = global_wall_depth
                wall_depth_literal = _literal(wall_depth)
            if wall_depth_literal is not None:
                graph.add((struct_uri, RESPLAN.wallDepth, wall_depth_literal))
    return structural_nodes, structural_entries


def _add_relationships(
    graph: Graph,
    ns: Namespace,
    rooms: Dict[str, URIRef],
    structural: Dict[str, URIRef],
    structural_entries: Dict[str, Dict],
    relations: Dict,
) -> None:
    def _order_pair(a: URIRef, b: URIRef):
        return (a, b) if str(a) <= str(b) else (b, a)

    # cache room geometries from triples (added in _add_rooms)
    room_geom_index = {
        room_uri: graph.value(room_uri, RESPLAN.geomJSON)
        for room_uri in rooms.values()
    }

    bounded = relations.get("bounded_by", {}).get("edges", [])
    for edge in bounded:
        room_uri = _resolve_room(edge.get("room"), graph, ns, rooms)
        wall_id = edge.get("wall")
        wall_uri = structural.get(wall_id) if wall_id else None
        if room_uri and wall_id:
            # Keep the boundedBy link even if the wall instance was dropped;
            # in that case the wall stays untyped so SHACL can flag it as missing.
            wall_uri = wall_uri or ns[wall_id]
            graph.add((room_uri, RESPLAN.boundedBy, wall_uri))
        elif room_uri:
            LOGGER.debug("Skipping bounded_by edge with unknown wall id: %s", edge)
        else:
            LOGGER.debug("Skipping bounded_by edge with unknown ids: %s", edge)

    hosts = relations.get("hosts_opening", [])
    for edge in hosts:
        wall_uri = structural.get(edge.get("wall"))
        opening_uri = structural.get(edge.get("opening"))
        if wall_uri and opening_uri:
            graph.add((wall_uri, RESPLAN.hostsOpening, opening_uri))
        else:
            LOGGER.debug("Skipping hosts_opening edge with unknown ids: %s", edge)

    adjacency = relations.get("adjacent_to", [])
    seen_adj_edges = set()
    for entry in adjacency:
        a = _resolve_room(entry.get("a"), graph, ns, rooms)
        b = _resolve_room(entry.get("b"), graph, ns, rooms)
        if a and b:
            graph.add((a, RESPLAN.adjacentTo, b))
            graph.add((b, RESPLAN.adjacentTo, a))
            shared_walls = entry.get("shared_walls") or []
            for wall_id in shared_walls:
                wall_uri = structural.get(wall_id) or ns[wall_id]
                graph.add((a, RESPLAN.boundedBy, wall_uri))
                graph.add((b, RESPLAN.boundedBy, wall_uri))

            oa, ob = _order_pair(a, b)

            # Materialize one adjacency edge per shared wall so missing walls are counted per ID.
            if shared_walls:
                for idx, wall_id in enumerate(shared_walls):
                    wall_uri = structural.get(wall_id) or ns[wall_id]
                    adj_id = f"adj-{Path(_shorten_uri(oa)).name}-{Path(_shorten_uri(ob)).name}-{Path(_shorten_uri(wall_uri)).name}"
                    # Keep unique even if same wall appears twice
                    if adj_id in seen_adj_edges:
                        continue
                    seen_adj_edges.add(adj_id)
                    adj_uri = ns[adj_id]
                    graph.add((adj_uri, RDF.type, RESPLAN.AdjacencyEdge))
                    graph.add((adj_uri, RESPLAN.spaceA, oa))
                    graph.add((adj_uri, RESPLAN.spaceB, ob))
                    graph.add((adj_uri, RESPLAN.sharedWallCount, Literal(1, datatype=XSD.integer)))
                    graph.add((adj_uri, RESPLAN.sharedWall, wall_uri))
                    # Prefer explicit geom on adjacency entry; fallback to host wall geom.
                    if entry.get("geom"):
                        _add_geom_literals(graph, adj_uri, entry)
                    else:
                        wall_entry = structural_entries.get(wall_id)
                        if wall_entry and wall_entry.get("geom"):
                            _add_geom_literals(graph, adj_uri, wall_entry)
                        else:
                            # Derive shared boundary from room geometries if available
                            g_a = room_geom_index.get(oa)
                            g_b = room_geom_index.get(ob)
                            if g_a and g_b:
                                try:
                                    sa = shape(json.loads(str(g_a)))
                                    sb = shape(json.loads(str(g_b)))
                                    shared = sa.boundary.intersection(sb.boundary)
                                    lines = []
                                    if shared.geom_type == "LineString":
                                        lines = [shared]
                                    elif shared.geom_type == "MultiLineString":
                                        lines = list(shared.geoms)
                                    if lines:
                                        longest = max(lines, key=lambda g: g.length)
                                        _add_geom_literals(
                                            graph,
                                            adj_uri,
                                            {"geom": mapping(longest)},
                                        )
                                except Exception:
                                    LOGGER.debug("Failed to derive adjacency geom for %s", adj_uri)
            else:
                # No shared wall data; keep single adjacency edge for topology only
                adj_id = f"adj-{Path(_shorten_uri(oa)).name}-{Path(_shorten_uri(ob)).name}"
                if adj_id not in seen_adj_edges:
                    seen_adj_edges.add(adj_id)
                    adj_uri = ns[adj_id]
                    graph.add((adj_uri, RDF.type, RESPLAN.AdjacencyEdge))
                    graph.add((adj_uri, RESPLAN.spaceA, oa))
                    graph.add((adj_uri, RESPLAN.spaceB, ob))
                    graph.add((adj_uri, RESPLAN.sharedWallCount, Literal(0, datatype=XSD.integer)))
        else:
            LOGGER.debug("Skipping adjacency edge with unknown ids: %s", entry)

    connections = relations.get("connected_via_door", [])
    for entry in connections:
        room_ids = [rid for rid in (entry.get("rooms") or []) if rid]
        if len(room_ids) < 2:
            continue
        for room_a, room_b in combinations(room_ids, 2):
            a = _resolve_room(room_a, graph, ns, rooms)
            b = _resolve_room(room_b, graph, ns, rooms)
            if a and b:
                graph.add((a, RESPLAN.connectedViaDoor, b))
                graph.add((b, RESPLAN.connectedViaDoor, a))
            else:
                LOGGER.debug("Skipping connected_via_door pair (%s, %s).", room_a, room_b)

        wall_id = entry.get("through_wall")
        wall_uri = structural.get(wall_id) if wall_id else None
        if wall_id and not wall_uri:
            LOGGER.debug("Wall %s not found for connected_via_door entry %s.", wall_id, entry.get("id"))
        if wall_uri and OUTSIDE_ID in room_ids:
            outside_uri = _resolve_room(OUTSIDE_ID, graph, ns, rooms)
            graph.add((outside_uri, RESPLAN.boundedBy, wall_uri))

        door_id = entry.get("door")
        door_uri = structural.get(door_id) if door_id else None
        if door_id and not door_uri:
            LOGGER.debug("Door %s not found for connected_via_door entry %s.", door_id, entry.get("id"))
        if door_uri:
            seen_rooms = set()
            for room_id in room_ids:
                room_uri = _resolve_room(room_id, graph, ns, rooms)
                if room_uri and room_uri not in seen_rooms:
                    graph.add((door_uri, RESPLAN.connectsSpace, room_uri))
                    seen_rooms.add(room_uri)

def _add_window_memberships(
    graph: Graph,
    ns: Namespace,
    rooms: Dict[str, URIRef],
    structural: Dict[str, URIRef],
    window_relations: Dict,
) -> None:
    if not isinstance(window_relations, dict):
        return

    window_connects = window_relations.get("window_connects")
    # Backward compatibility: fall back to legacy window_analysis structure if present.
    if window_connects is None and isinstance(window_relations.get("window_analysis"), dict):
        window_connects = window_relations["window_analysis"].get("window_connects")

    if not window_connects:
        return

    expected_rooms: set[str] = set()
    for entry in window_connects:
        if not isinstance(entry, dict):
            continue
        room_id = entry.get("room")
        window_id = entry.get("window")
        if not room_id or not window_id:
            continue
        room_uri = _resolve_room(room_id, graph, ns, rooms)
        if not room_uri:
            continue
        expected_rooms.add(room_id)

        present = bool(entry.get("present"))
        # Jangan hidupkan kembali instance yang sudah di-drop; untuk window yang hilang,
        # pakai bnode supaya hostsOpening tetap bisa dicatat tanpa membuat URI WI-xx lagi.
        window_uri = structural.get(window_id) if present else None

        primary_wall_id = entry.get("primary_wall")
        primary_wall_uri = structural.get(primary_wall_id) if primary_wall_id else None
        if primary_wall_uri is None and primary_wall_id:
            primary_wall_uri = ns[primary_wall_id]

        if window_uri is None:
            if present:
                window_uri = ns[window_id]
                graph.add((window_uri, RDF.type, RESPLAN.Window))
                graph.add((window_uri, RESPLAN.sourceId, Literal(window_id)))
            else:
                # Placeholder bnode only to keep windowOpening/hostsOpening topology.
                window_uri = BNode()
        else:
            # Pastikan tipe dan source id tercatat untuk window yang memang ada.
            graph.add((window_uri, RDF.type, RESPLAN.Window))
            graph.add((window_uri, RESPLAN.sourceId, Literal(window_id)))

        if primary_wall_uri is not None:
            graph.add((window_uri, RESPLAN.primaryWall, primary_wall_uri))

        # expected slot selalu dicatat
        graph.add((room_uri, RESPLAN.windowOpening, window_uri))

        if present:
            # Window instance ada; record presence.
            graph.add((room_uri, RESPLAN.hasWindow, window_uri))

        # hostsOpening dari host_walls (jika tersedia)
        host_walls = entry.get("host_walls") or []
        for wall_id in host_walls:
            wall_uri = structural.get(wall_id) or ns[wall_id]
            graph.add((wall_uri, RESPLAN.hostsOpening, window_uri))

    for room_id in expected_rooms:
        room_uri = _resolve_room(room_id, graph, ns, rooms)
        if room_uri:
            graph.add((room_uri, RESPLAN.expectedWindow, Literal(True)))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("json_path", type=Path, help="Path to the exported plan JSON file.")
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        help="Destination .ttl path (defaults to replacing the JSON suffix with .ttl).",
    )
    parser.add_argument(
        "--base",
        type=str,
        help="Base namespace for generated individuals (defaults to http://resplan.org/resplan/<plan_idx>#).",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable debug logging for missing references during conversion.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(levelname)s: %(message)s")
    output = convert(args.json_path, args.output, args.base)
    LOGGER.info("Wrote %s", output)


if __name__ == "__main__":
    main()
