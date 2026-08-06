# Portfolio Improvement Roadmap

This document tracks the work needed to turn the thesis repository into a stronger portfolio project for ontology engineer, knowledge engineer, and knowledge graph roles.

The goal is not only to make the repository look cleaner. The goal is to present the thesis as a reproducible knowledge-engineering pipeline with clear ontology design, reasoning logic, software structure, evaluation, and honest limitations.

## Current Working Context

- Working repository: `Geometry-to-Ontology-backup`
- Working branch: `portfolio`
- Original thesis repository: `Geometry-to-Ontology`
- Main project topic: knowledge-based reconstruction of imperfect residential floor plans.
- Core method: JSON geometry -> RDF/Turtle -> SHACL validation -> SPARQL CONSTRUCT inference -> RDF/Turtle -> JSON geometry.
- Main reconstructed elements: interior walls, doors, and windows.

## Important Current Limitation

The project currently performs better at semantic reconstruction than exact geometric reconstruction.

This means the system can often infer that a missing wall, door, or window should exist and how it relates to rooms or walls. However, translating that inferred ontology-level knowledge back into precise geometry is still weak in some cases.

This should be treated as a project limitation and future-work area, not hidden. For portfolio purposes, this is useful because it shows the difference between:

- semantic inference: what element should exist and what it connects,
- geometric back-projection: where the element should be placed in the floor plan.

## Completed

- [x] Created/used a separate backup repository for safer portfolio work.
- [x] Created/used a dedicated `portfolio` branch.
- [x] Synced the `portfolio` branch with GitHub.
- [x] Removed generated cache files from Git tracking:
  - `.DS_Store`
  - `__pycache__/`
  - `.pyc` files
- [x] Simplified `.gitignore` so generated files do not return.
- [x] Improved `README.md` for portfolio presentation:
  - clearer project summary,
  - knowledge-engineering focus,
  - technical stack,
  - pipeline overview,
  - portfolio relevance.
- [x] Added `docs/ontology-design.md`:
  - ontology modeling goal,
  - core classes,
  - BOT/IFC alignment,
  - spatial relationships,
  - asserted vs inferred knowledge,
  - geometry preservation,
  - design tradeoffs.

## Phase 1: Repository Presentation

Goal: make the repository understandable and professional for a visitor.

- [x] Clean Git-tracked junk files.
- [x] Improve main README.
- [x] Add ontology design documentation.
- [ ] Add reasoning rules documentation.
- [ ] Add pipeline documentation.
- [ ] Add limitations documentation.
- [ ] Add a small reproducible example folder.

## Phase 2: Knowledge Engineering Documentation

Goal: show ontology and knowledge-graph competence clearly.

- [x] Explain ontology modeling decisions.
- [ ] Explain SHACL validation rules:
  - adjacency/interior-wall rule,
  - door rules,
  - window rules.
- [ ] Explain SPARQL CONSTRUCT inference rules:
  - interior wall inference,
  - door inference,
  - window inference.
- [ ] Explain asserted vs inferred triples with examples.
- [ ] Explain why SHACL/SPARQL are used instead of only OWL reasoning.
- [ ] Explain the difference between domain knowledge and dataset-specific heuristics.

Suggested next document:

```text
docs/reasoning-rules.md
```

## Phase 3: Minimal Reproducible Pipeline

Goal: reduce dependence on notebooks and provide one command-line path.

Current issue:

- notebooks are the main reproducible workflow,
- `thesis_package/main.py` is currently a placeholder,
- a reviewer cannot easily run one clean example from the terminal.

Target command:

```bash
python -m thesis_package.pipeline \
  --input examples/plan_missing_door.json \
  --scenario drop_door
```

Target output:

```text
examples/output/
  plan_missing_door.ttl
  validation_before.json
  inferred.ttl
  validation_after.json
  reconstructed.json
  report.json
```

Checklist:

- [ ] Inspect notebook workflow and identify reusable functions.
- [ ] Create `thesis_package/pipeline.py`.
- [ ] Add CLI arguments for input, scenario, and output directory.
- [ ] Connect JSON-to-TTL conversion.
- [ ] Connect SHACL validation.
- [ ] Connect SPARQL inference.
- [ ] Connect TTL-to-JSON back-projection.
- [ ] Generate a machine-readable summary report.
- [ ] Document the command in `README.md`.

## Phase 4: Evaluation and Reporting

Goal: make results measurable instead of only visual.

Target report example:

```json
{
  "scenario": "drop_door",
  "missing_elements": 1,
  "inferred_elements": 1,
  "post_validation_passed": true,
  "geometry_back_projection_quality": "partial"
}
```

Checklist:

- [ ] Define semantic reconstruction metrics.
- [ ] Define geometric reconstruction metrics.
- [ ] Count inferred walls, doors, and windows.
- [ ] Compare inferred semantic relationships to expected relationships.
- [ ] Add before/after validation counts.
- [ ] Add output report generation.
- [ ] Add example report to `examples/`.

## Phase 5: Geometry Back-Projection Improvement

Goal: improve the weak semantic-to-geometry step while keeping claims honest.

Key idea:

Ontology inference should answer:

```text
What element should exist?
What spaces or walls should it connect to?
Why was it inferred?
```

Geometry resolution should answer:

```text
Where should it be placed?
What should its orientation, width, length, and coordinates be?
How confident is the placement?
```

Possible improvements:

- [ ] Separate symbolic inference from geometry placement logic.
- [ ] Use shared-wall geometry to place inferred doors.
- [ ] Use exterior-wall membership to place inferred windows.
- [ ] Use adjacency metadata to reconstruct missing interior walls.
- [ ] Preserve original missing element metadata for synthetic experiments when available.
- [ ] Add quality labels such as `exact`, `estimated`, `partial`, or `failed`.
- [ ] Generate before/after visual comparisons.
- [ ] Document known failure cases.

## Phase 6: Tests

Goal: demonstrate software engineering maturity.

Suggested tests:

- [ ] Test JSON-to-RDF mapping.
- [ ] Test ontology terms/classes are emitted correctly.
- [ ] Test SHACL violation detection.
- [ ] Test SPARQL inference output.
- [ ] Test TTL-to-JSON conversion.
- [ ] Test one tiny end-to-end example.

Suggested structure:

```text
tests/
  test_json_to_ttl.py
  test_shacl_rules.py
  test_inference.py
  test_ttl_to_json.py
  test_pipeline.py
```

## Recommended Next Step

Create:

```text
docs/reasoning-rules.md
```

This should explain how the SHACL rules detect missing elements and how the SPARQL CONSTRUCT queries materialize inferred walls, doors, and windows.

After that, create:

```text
docs/limitations.md
```

This should honestly explain the semantic-vs-geometric reconstruction limitation and frame it as future work.

## Resume Notes

When continuing this work, start with:

```bash
cd "/Users/muhammadnumanmuttaqi/Documents/MScITBE/Thesis/Thesis/Geometry-to-Ontology-backup"
git checkout portfolio
git status
```

Then open this file and continue from the checklist.
