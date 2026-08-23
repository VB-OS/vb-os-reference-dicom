# Accessium DICOM Reference Environment

A vendor-neutral reference workflow demonstrating governed release of radiology
studies through VB-OS Cloud. It does **not** represent any organisation's
production topology.

**Governed decision:** release a completed radiology study to an external
referring physician portal.

**Framing:** was this release authorized, on what evidence, acquired from where,
and can it be proven six months from now. VB-OS holds no clinical or diagnostic
authority — it verifies provenance, not truth.

## Architecture

```
        ┌──────────────┐
        │   Orthanc    │   real DICOM/PACS, DICOMweb
        └──────┬───────┘
               │ active_provider
               ▼
   ┌────────────────────────┐
   │ E1  STUDY ADMISSIBLE   │
   └───────────┬────────────┘
      DEFER ◄──┴──► ASSERT
```

E2 (release authorization, FHIR-sourced) and E3 (delivery execution
verification) are drafted in `boundaries/` and not yet built.

## Boundary — E1

`B_ACCESSIUM_STUDY_ADMISSIBILITY` governs four conditions, all from a single
DICOM acquisition:

| Predicate | Rule |
|-----------|------|
| `modality_in_release_scope` | modality is one of CT, MR, CR, DX |
| `study_has_series` | the study contains at least one series |
| `study_has_instances` | the study contains at least one instance |
| `study_is_orderable` | the study carries a real accession number |

There is deliberately **no** instance-completeness predicate. The PACS exposes
no independently declared instance count to reconcile against — QIDO's
`NumberOfStudyRelatedInstances` is computed from what the archive holds — so
truncation is not detectable from DICOM evidence alone, and nothing was invented
to pretend otherwise.

## Studies

Loaded from the Orthanc project's public demo archive. Both are genuine
anonymized clinical studies.

| Study | Modality | Accession | E1 |
|-------|----------|-----------|----|
| PHENIX (CT head/sinus) | CT | `A10011234814` | **ASSERT** |
| BRAINIX (MR brain) | MR | `0` | **DEFER** — no accession, cannot be tied to an order |

BRAINIX's accession number is `"0"` in the source data. The DEFER comes from the
data, not from anything edited.

## Prerequisites

1. VB-OS Cloud API running on `localhost:8000`
2. Docker
3. Python 3.11+
4. A VB-OS project, environment and API key (see `.env.example`)

## Setup

```bash
# 1. Start the reference PACS (joins the VB-OS Cloud Docker network)
docker compose up -d

# 2. Load the anonymized studies
python seed_studies.py

# 3. Configure
cp .env.example .env      # fill in VBOS_API_KEY, VBOS_PROJECT_ID, VBOS_ENVIRONMENT_ID

# 4. Provision the boundary and connector in VB-OS Cloud (idempotent)
python bootstrap.py
```

Orthanc is reachable at `http://localhost:8042` (user `vbos`) and, from the
platform's containers, at `http://accessium-orthanc:8042/dicom-web`.

## Per-study scoping

Each connector is pinned to one study via `study_instance_uid` in its config, so
the evaluated subject is what was requested rather than whatever the archive
returned first. A scoped acquisition must resolve to exactly one study — zero or
several are rejected with no evidence and no evaluation, and the rejection is
recorded in the audit trail.

| Connector | Scoped to | E1 |
|-----------|-----------|-----|
| `accessium-pacs` | PHENIX | ASSERT |
| `accessium-pacs-unaccessioned` | BRAINIX | DEFER |

## Directory structure

```
accessium-dicom/
├── docker-compose.yml   # Orthanc, joined to the VB-OS Cloud network
├── orthanc.json         # PACS config: basic auth, DICOMweb
├── seed_studies.py      # loads the anonymized studies
├── bootstrap.py         # idempotent VB-OS provisioning (boundary + scoped connectors)
├── boundaries/          # DSL source of truth
├── tests/
└── manifest.json
```
