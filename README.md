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
        ┌──────────────┐              ┌──────────────┐
        │   Orthanc    │              │  HAPI FHIR   │
        │ DICOM / PACS │              │  RIS / order │
        └──────┬───────┘              └──────┬───────┘
               │ active_provider              │ active_provider
               ▼                              ▼
   ┌────────────────────────┐    ┌────────────────────────────┐
   │ E1  STUDY ADMISSIBLE   │    │ E2  RELEASE AUTHORIZED     │
   └───────────┬────────────┘    └─────────────┬──────────────┘
      DEFER ◄──┴──► ASSERT          DEFER ◄────┴────► ASSERT
                                                     │
                                        governed flow, minimum
                                        disclosure only
                                                     ▼
                                     ┌────────────────────────────┐
                                     │  Referring physician portal │
                                     └─────────────┬──────────────┘
                                       authenticated_provider_push
                                                   ▼
                                     ┌────────────────────────────┐
                                     │ E3  DELIVERY VERIFIED      │
                                     └─────────────┬──────────────┘
                                        DEFER ◄────┴────► ASSERT
```

All three stages are live: E1 from a real PACS, E2 from a local HAPI FHIR
server, and E3 from the receiving portal's own signed delivery receipt.

## Governed dispatch

E2 ASSERT dispatches a signed release to the portal. The flow discloses only
`report_accession` and `authorized_instance_count` — the portal receives what it
needs to perform and account for the delivery, and nothing else.

The portal does not treat an inbound ASSERT as authority. It verifies the
signature and timestamp, enforces delivery idempotency, consumes each
authorization at most once, then **re-fetches the evaluation from VB-OS** and
independently checks the decision, the boundary it came from, and its
acquisition class before releasing anything.

Its outcome returns as a FHIR `Task` signed with the platform's HMAC, which
arrives as an `authenticated_provider_push` and drives E3.

## Scenarios

| # | Scenario | Stage | Result | Failure class |
|---|----------|-------|--------|---------------|
| 1 | Complete accessioned CT | E1 | ASSERT | — |
| 2 | Study with no accession number | E1 | DEFER | predicate |
| 3 | Finalised report | E2 | ASSERT | — |
| 4 | Preliminary report | E2 | DEFER | predicate |
| 5 | Export-restricted report | E2 | DEFER | **prohibition** |
| 6 | Delivery completed, counts match | E3 | ASSERT | — |
| 7 | Portal accepted 700 of 723 authorized | E3 | DEFER | predicate (execution drift) |

Scenario 5 is the distinct one: evidence of a prohibited condition exists, so
the engine stops before evaluating predicates at all. All five replay
deterministically.

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

# 2. Load the anonymized studies and the RIS resources
python seed_studies.py
python seed_fhir.py

# 3. Configure
cp .env.example .env      # fill in VBOS_API_KEY, VBOS_PROJECT_ID, VBOS_ENVIRONMENT_ID

# 4. Provision boundaries, connectors and flows in VB-OS Cloud (idempotent)
python bootstrap.py

# 5. Start the referring physician portal (reads .env)
python delivery_gateway.py
```

Set `DELIVERY_DRIFT=23` to make the portal accept fewer instances than were
authorized, which is what E3 exists to catch.

Orthanc is at `http://localhost:8042` (user `vbos`) and HAPI FHIR at
`http://localhost:8090/fhir`. From the platform's containers they are
`http://accessium-orthanc:8042/dicom-web` and `http://accessium-fhir:8080/fhir`.

## Per-study scoping

Each connector is pinned to one study via `study_instance_uid` in its config, so
the evaluated subject is what was requested rather than whatever the archive
returned first. A scoped acquisition must resolve to exactly one study — zero or
several are rejected with no evidence and no evaluation, and the rejection is
recorded in the audit trail.

| Connector | Scoped to | Stage | Decision |
|-----------|-----------|-------|----------|
| `accessium-pacs` | PHENIX study | E1 | ASSERT |
| `accessium-pacs-unaccessioned` | BRAINIX study | E1 | DEFER |
| `accessium-ris-report` | final report | E2 | ASSERT |
| `accessium-ris-report-preliminary` | preliminary report | E2 | DEFER |
| `accessium-ris-report-restricted` | restricted report | E2 | DEFER (prohibition) |

A FHIR connector is scoped by direct read — `resource_type` holds a relative
resource reference such as `DiagnosticReport/accessium-report-phenix`, which
returns exactly one resource. A bare resource type returns a Bundle whose first
entry is decided by server ordering.

## Directory structure

```
accessium-dicom/
├── docker-compose.yml   # Orthanc, joined to the VB-OS Cloud network
├── orthanc.json         # PACS config: basic auth, DICOMweb
├── seed_studies.py      # loads the anonymized studies
├── seed_fhir.py         # loads the order and report resources
├── bootstrap.py         # idempotent VB-OS provisioning (boundaries, connectors, flows)
├── delivery_gateway.py  # referring physician portal — the governed downstream
├── boundaries/          # DSL source of truth
├── tests/
└── manifest.json
```
