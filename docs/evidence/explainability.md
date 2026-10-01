# SaltLens — Explainability (legitimate state)

## What explainability means here today

- **Rule layer (live and explained in every response):** every decision carries the reasoning.
  The Stage 8 demonstration recommendation `TRANSFER BRINE` returned:

  ```json
  {
    "basis": "physics_and_rules",
    "ml_result": null,
    "rule_version": {"version": "v2", "sha256": "06a32ee5d7f61912af5327b3ae7725c0576cc8e1640601f7de8bb96fbd9a4d7b"},
    "recommendation_id": "rec-f41de75246d04802b8aedacdaa22e515",
    "notice": "Physics/rule-based recommendation — ML prediction unavailable ... DEMONSTRATION ONLY ..."
  }
  ```

  The rule hash travels with the recommendation so any change in the rule is auditable.

- **Physics layer (transparent by construction):** the mass-balance simulator returns its
  intermediate quantities — initial/post volumes, salt masses conserved to 1e-9 — so the "why" of a
  simulated salinity is checkable by hand (`equations_and_assumptions.md`).

- **ML layer (refuses honestly):** `Module 11` computes SHAP attributions **only from a registered
  model artifact**. There are 0 artifacts (`models = 0`), so the explainability endpoint returns the
  refusal rather than a fabricated attribution. No SHAP output exists, and this file will only ever
  gain one when a genuine model exists.

## Why this is evidence, not absence

A system that returns "no explanation because no model exists" is testable and falsifiable:
`GET /api/explain/...` with a non-existent model returns a machine-readable code. The demonstration
shows the attribution pipeline wiring (`test_explain.py`, 30 tests) without claiming an
explanation that has no underlying model to explain.