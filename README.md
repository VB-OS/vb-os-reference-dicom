# VB-OS DICOM Reference Integration

A reference implementation demonstrating governed release of radiology studies
through [VB-OS Cloud](https://vb-os.org). This integration exercises all four
governance boundaries — study admissibility (E1), AI draft authority (E2-AI),
release authorization (E2), and delivery execution verification (E3) — against
real source systems.

VB-OS holds no clinical or diagnostic authority. It verifies provenance, not
truth: was this action authorized, on what evidence, acquired from where, and
can it be proven six months from now.

## Quick Start

See [SETUP.md](SETUP.md) for complete setup instructions — from forking the
template in VB-OS Cloud to running the full integration locally.

## Architecture

```
        ┌──────────────┐              ┌──────────────┐
        │   Orthanc    │              │  HAPI FHIR   │
        │ DICOM / PACS │              │  RIS / order │
        └──────┬───────┘              └──────┬───────┘
               │ caller_supplied_payload      │ caller_supplied_payload
               ▼                              ▼
   ┌────────────────────────┐    ┌────────────────────────────┐
   │ E1  STUDY ADMISSIBLE   │    │ E2  RELEASE AUTHORIZED     │
   └───────────┬────────────┘    └─────────────┬──────────────┘
      DEFER ◄──┴──► ASSERT          DEFER ◄────┴────► ASSERT
                    │                                  │
                    ▼                                  ▼
         ┌──────────────────┐           ┌──────────────────────────┐
         │ VB-OS OpenAI     │           │ E3  DELIVERY VERIFIED    │
         │ (active_provider)│           └─────────────┬────────────┘
         └────────┬─────────┘              DEFER ◄────┴────► ASSERT
                  ▼
   ┌──────────────────────────────┐
   │ E2-AI  AI DRAFT AUTHORITY    │
   └──────────────┬───────────────┘
      DEFER ◄─────┴─────► ASSERT
```

## Acquisition Modes

This integration demonstrates both acquisition modes supported by VB-OS:

**Caller-supplied payload** (E1, E2, E3): The local script reads from the
source system (Orthanc or HAPI FHIR), then pushes the raw payload to VB-OS
Cloud. VB-OS extracts evidence, evaluates the boundary, and records the
provenance.

**Active provider** (E2-AI): VB-OS Cloud calls OpenAI server-side using the
credentials configured on the connector. The local script sends context
evidence; VB-OS makes the API call, parses the response, extracts structured
evidence, and evaluates the boundary.

## Scenarios

| # | Scenario | Stage | Result |
|---|----------|-------|--------|
| 1 | Complete accessioned CT study | E1 | ASSERT |
| 2 | Study with no accession number | E1 | DEFER |
| 3 | Finalised diagnostic report | E2 | ASSERT |
| 4 | Preliminary report | E2 | DEFER |
| 5 | Export-restricted report | E2 | DEFER (prohibition) |
| 6 | Live AI screening pipeline | E1 → E2-AI | varies |

Run `python3 demo.py --list` for descriptions, or `python3 demo.py --all` to
run all scenarios. `python3 demo.py --pipeline` runs the full E1 → E2-AI → E2
→ E3 pipeline end-to-end.

## Verification Vectors

`verify.py` contains 9 deterministic test vectors that prove each governance
condition in the E2-AI boundary evaluates correctly. No live AI call is made.

| Vector | Tests | Expected |
|--------|-------|----------|
| V1 | All governance conditions met | ASSERT |
| V2 | Input quality unacceptable | DEFER |
| V3 | Unapproved model version (model drift) | DEFER |
| V4 | AI flags escalation with abnormal classification | DEFER |
| V5 | Missing accession number (traceability failure) | DEFER |
| V6 | AI proposes out-of-contract workflow | DEFER |
| V7 | Missing required AI evidence (fail-closed) | DEFER |
| V8 | Replay verification (deterministic reproducibility) | ASSERT |
| V9 | Unauthorized modality (mutation sensitivity) | DEFER |

V8 additionally replays the evaluation to prove deterministic reproducibility.

## AI Governance (E2-AI)

The E2-AI boundary (`B_IMAGING_AI_DRAFT_AUTHORITY`) governs whether an AI
screening system's output may advance to a radiologist's review queue. The AI
itself has no governance authority — it proposes, and VB-OS verifies.

Evidence separation is enforced: the AI supplies its claims (classification,
recommended workflow, escalation flag), while the deployment supplies
independently sourced governance facts (model version approval, modality
authorization, preprocessing quality).

| Predicate | Rule |
|-----------|------|
| `classification_is_governed` | AI classification is in the authorized set |
| `workflow_is_governed` | Recommended workflow is in the authorized set |
| `no_escalation_flagged` | AI has not flagged the study for escalation |
| `model_is_approved` | Deployed model version matches the approved version |
| `modality_is_authorized` | Study modality is authorized for AI processing |
| `quality_is_acceptable` | Input preprocessing quality meets the threshold |
| `study_is_traceable` | Study has both a UID and an accession number |

## Studies

Loaded from the Orthanc project's public demo archive. Both are genuine
anonymized clinical studies.

| Study | Modality | Accession | E1 |
|-------|----------|-----------|----|
| PHENIX (CT head/sinus) | CT | `A10011234814` | ASSERT |
| BRAINIX (MR brain) | MR | `0` | DEFER |

BRAINIX's accession number is `"0"` in the source data. The DEFER comes from
the data, not from anything edited.

## Directory Structure

```
vb-os-reference-dicom/
├── SETUP.md               # Complete setup guide
├── demo.py                # Scenario runner (--scenario N, --all, --pipeline)
├── verify.py              # Deterministic E2-AI verification vectors
├── bootstrap.py           # (Optional) Standalone VB-OS provisioning
├── seed_studies.py        # Loads anonymized DICOM studies into Orthanc
├── seed_fhir.py           # Creates patient, order, and reports in HAPI FHIR
├── docker-compose.yml     # Local Orthanc PACS + HAPI FHIR server
├── orthanc.json           # PACS configuration
├── .env.example           # Environment variable template
└── boundaries/            # VB-OS DSL boundary definitions
    ├── B_IMAGING_STUDY_ADMISSIBILITY.dsl
    ├── B_IMAGING_AI_DRAFT_AUTHORITY.dsl
    ├── B_IMAGING_RELEASE_AUTHORIZATION.dsl
    └── B_IMAGING_DELIVERY_EXECUTION.dsl
```

## License

Copyright 2026 MNC Labs, Inc. Licensed under the Apache License, Version 2.0.
See [LICENSE](LICENSE) for the full text.
