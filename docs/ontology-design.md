# Ontology Design Notes

This document summarizes the main ontology-design decisions behind the Geometry-to-Ontology thesis project. The goal is to make the semantic model easier to review without reading the full RDF/Turtle ontology first.

## Modeling Goal

The ontology represents residential floor plans as knowledge graphs so that incomplete plans can be validated and repaired with explicit rules. The model keeps both semantic relationships and geometric metadata, allowing the pipeline to move between JSON geometry and RDF knowledge representation.

The core modeling problem is:

1. represent rooms and structural elements as RDF resources,
2. describe how rooms are bounded, adjacent, connected, and opened,
3. detect missing structural elements through validation,
4. infer replacement elements while preserving source identifiers and geometry references.

## Core Classes

The main namespace is `resplan:`. The ontology also aligns selected concepts with building-domain vocabularies:

- `resplan:Room` is modeled as a subclass of `bot:Space`.
- `resplan:InteriorWall` and `resplan:ExteriorWall` are modeled as subclasses of `ifc:Wall`.
- `resplan:Door` is aligned with `ifc:Door` and also treated as a `resplan:Opening`.
- `resplan:Window` is aligned with `ifc:Window` and also treated as a `resplan:Opening`.

Room categories such as `resplan:Bedroom`, `resplan:Kitchen`, `resplan:Bathroom`, and `resplan:LivingRoom` are represented as subclasses of `resplan:RoomType`. Individual room instances are connected to these categories through `resplan:hasRoomType`.

## Spatial Relationships

The ontology separates direct spatial relationships from relationship metadata.

- `resplan:boundedBy` links a room/space to the wall resources that bound it.
- `resplan:hostsOpening` links a wall to a door or window hosted on that wall.
- `resplan:connectsSpace` links a door to the spaces it connects.
- `resplan:hasWindow` links a room to an observed or inferred window.

For adjacency, the project uses `resplan:AdjacencyEdge` as an explicit relationship node. This makes it possible to attach metadata to an adjacency relation:

- `resplan:spaceA` and `resplan:spaceB` identify the two spaces.
- `resplan:sharedWall` identifies the shared wall candidate.
- `resplan:sharedWallCount` stores the number of shared wall segments.

This design is useful because missing-wall inference needs more than a simple `resplan:adjacentTo` triple. The pipeline needs to know which wall identifier or wall slot is missing so it can infer a replacement.

## Missing Knowledge Representation

The model distinguishes between asserted and inferred knowledge.

- Asserted elements come from the original or imperfect JSON floor-plan data.
- Inferred elements are created by SPARQL CONSTRUCT rules and marked with `resplan:isInferred true`.

Several properties preserve traceability:

- `resplan:sourceId` keeps the original JSON or generated element identifier.
- `resplan:derivedFrom` links an inferred element to the adjacency edge or expected slot that caused the inference.
- `resplan:replacesWall` links an inferred wall to the missing wall identifier it replaces.
- `resplan:separatesSpace` records the rooms separated by an inferred wall.

This traceability is important because reconstruction should not produce anonymous geometry. The system should be able to explain why an element was inferred.

## Validation and Inference Pattern

The project uses a validation-first pattern:

1. Convert an imperfect floor plan from JSON to RDF/Turtle.
2. Validate the graph with SHACL.
3. Use validation-relevant graph patterns to infer missing elements.
4. Add inferred triples to the graph.
5. Revalidate the enriched graph.
6. Convert the result back to geometry-oriented JSON.

Examples:

- A missing interior wall is detected when an adjacency edge references an interior-wall identifier but no corresponding `resplan:InteriorWall` resource exists.
- A missing door is detected when two spaces have a `resplan:connectedViaDoor` relation but no door connects both spaces.
- A missing window is detected when a room has `resplan:expectedWindow true` but the expected window resource is absent.

## Geometry Preservation

The ontology stores geometry-oriented data as RDF literals so the semantic graph can be converted back into JSON. Important geometry properties include:

- `resplan:geomJSON`
- `resplan:geomWKT`
- `resplan:centroidX` and `resplan:centroidY`
- `resplan:bboxMinX`, `resplan:bboxMinY`, `resplan:bboxMaxX`, and `resplan:bboxMaxY`
- `resplan:length`, `resplan:wallDepth`, and `resplan:openingWidth`

This is a pragmatic design choice. The ontology is not only a conceptual vocabulary; it also carries enough implementation metadata to support round-trip reconstruction.

## Design Tradeoffs

The ontology is intentionally lightweight. It does not attempt to fully reproduce IFC or BOT. Instead, it uses selected alignments to make the project interoperable while keeping the reasoning pipeline manageable.

The main tradeoff is that some geometric assumptions remain outside OWL semantics. SHACL and SPARQL are used because the task needs closed-world validation and rule-based materialization, while OWL reasoning alone would not be suitable for detecting missing data in incomplete floor plans.
