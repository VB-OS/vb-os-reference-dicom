# VB-OS DICOM Reference Integration — Setup Guide

Complete setup and execution guide for the governed medical imaging pipeline.
Covers study admissibility (E1), AI draft authority (E2-AI), release
authorization (E2), and delivery execution verification (E3) — all four
boundaries evaluated by VB-OS Cloud against real source systems.

---

## Prerequisites

- **Python 3.11+**
- **Docker Desktop** (running)
- **Git**
- **An OpenAI API key** — the E2-AI boundary uses VB-OS Cloud's OpenAI
  connector (active-provider acquisition) to invoke a screening model
  server-side. You need your own key from https://platform.openai.com.

---

## Part 1: VB-OS Cloud Setup

### 1.1 Log in to the VB-OS Console

Open https://vbos.cloud and sign in.

### 1.2 Fork the DICOM Imaging Governance template

1. In the sidebar, click **Registry**.
2. Find **"DICOM Imaging Governance"**.
3. Click **Fork Template** and give the project a name (e.g. "DICOM Evaluation").

This creates a fully provisioned project with:
- 4 boundaries (E1, E2-AI, E2, E3) — approved and deployed
- 7 connectors — pre-configured with evidence mappings
- 4 governance flows — approved, active, and deployed
- 1 certification model

### 1.3 Copy the Project ID and Environment ID

Inside the new project:

1. Go to **Settings** (in the project sub-navigation). Under the **General**
   tab, copy the **Project ID**.
2. Switch to the **Environments** tab. Copy the **Environment ID** for the
   "Development" environment created by the fork.

You will need both in your `.env` file.

### 1.4 Configure the AI Screening connector

The template fork creates all 7 connectors with evidence mappings already
configured. Six of them use **caller-supplied payload** (the local scripts
push data to VB-OS Cloud), so they need no configuration. The seventh —
**Imaging AI Screening** — uses **active provider** acquisition, meaning
VB-OS Cloud calls OpenAI server-side on your behalf. This connector needs
your OpenAI API key and model configuration.

1. Inside the project, go to **Integrate** (in the project sub-navigation)
   and select the **Connectors** tab.
2. Click on **"Imaging AI Screening"** to open the edit form.
3. Fill in the following fields:

| Field | Value |
|-------|-------|
| **Model** | `gpt-4o-2024-08-06` |
| **Max Tokens** | Leave default (optional) |
| **Response Format** | `{"type": "json_object"}` |
| **API Key** (under Credentials) | Your OpenAI API key (starts with `sk-`) |
| **Webhook Secret** | Leave empty |
| **Allowed Acquisition Classes** | Leave as "All acquisition classes are permitted" |

4. **Do not modify the Evidence Mappings** — they are pre-configured by the
   template and map the OpenAI response fields to boundary evidence:

   | Evidence Field | Source Path | Transform |
   |----------------|-------------|-----------|
   | `ai_classification` | `parsed_content.classification` | Identity |
   | `ai_recommended_workflow` | `parsed_content.recommended_workflow` | Identity |
   | `ai_summary` | `parsed_content.summary` | Identity |
   | `ai_requires_escalation` | `parsed_content.requires_escalation` | To Integer |
   | `model_used` | `model` | Identity |

5. Click **Save**.

### 1.5 Create an API key

Inside the project, go to **Integrate** and select the **API Keys** tab.
Select the environment from the dropdown, then click **Create Key**.

**Select all scopes.** This is a reference integration that exercises the
full platform surface — boundaries, connectors, flows, evaluations,
certifications, and replay. Copy the key immediately; it is shown only once.

---

## Part 2: Local Environment Setup

### 2.1 Clone and enter the repository

```bash
git clone https://github.com/VB-OS/vb-os-reference-dicom.git
cd vb-os-reference-dicom
```

### 2.2 Create a virtual environment and install the SDK

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install vb-os
```

This creates an isolated Python environment and installs the official VB-OS
Python SDK from PyPI. Only `demo.py` uses it — all other scripts
(`bootstrap.py`, `verify.py`, `seed_studies.py`, `seed_fhir.py`) use the
standard library only.

Every time you open a new terminal to run scripts, activate the environment
first:

```bash
source .venv/bin/activate
```

### 2.3 Configure environment variables

```bash
cp .env.example .env
```

Open `.env` and fill in the four values from Part 1:

```
VBOS_URL=https://api.vb-os.org
VBOS_API_KEY=<your API key from step 1.5>
VBOS_PROJECT_ID=<your project ID from step 1.3>
VBOS_ENVIRONMENT_ID=<your environment ID from step 1.3>
OPENAI_API_KEY=<your OpenAI API key>
```

Leave all other values at their defaults — they configure the local Orthanc
PACS and HAPI FHIR server that ship with this repository.

### 2.4 Start the local source systems

```bash
docker compose up -d
```

This starts two containers:
- **Orthanc** (DICOM PACS) at `http://localhost:8042` — user `vbos`, password `imaging_dev`
- **HAPI FHIR** (RIS / order system) at `http://localhost:8090/fhir`

Verify both are running:

```bash
curl -u vbos:imaging_dev http://localhost:8042/system
curl http://localhost:8090/fhir/metadata
```

Both should return JSON responses.

### 2.5 Seed the DICOM studies

```bash
source .env
python3 seed_studies.py
```

Downloads two real anonymized clinical studies from the Orthanc public demo
archive and uploads them to your local PACS:

| Study | Modality | Accession | E1 outcome |
|-------|----------|-----------|------------|
| PHENIX (CT head/sinus) | CT | `A10011234814` | ASSERT |
| BRAINIX (MR brain) | MR | `0` | DEFER — no accession, cannot be tied to an order |

BRAINIX's accession number is `"0"` in the original clinical data. The DEFER
comes from the data, not from anything edited.

### 2.6 Seed the FHIR resources

```bash
python3 seed_fhir.py
```

Creates the patient, imaging order, and three diagnostic reports in the local
HAPI FHIR server:

| Resource | Purpose |
|----------|---------|
| `Patient/imaging-pt-phenix` | Reference patient |
| `ServiceRequest/imaging-order-phenix` | Imaging order (carries accession) |
| `DiagnosticReport/imaging-report-phenix` | Final report → E2 ASSERT |
| `DiagnosticReport/imaging-report-preliminary` | Preliminary report → E2 DEFER |
| `DiagnosticReport/imaging-report-restricted` | Export-restricted report → E2 DEFER (prohibition) |

### 2.7 Verify the setup

At this point, everything is ready:

- **VB-OS Cloud** has your project with 4 boundaries, 7 connectors, 4 flows,
  and 1 certification model — all provisioned by the template fork.
- **The OpenAI connector** has your API key configured (step 1.4).
- **Local source systems** (Orthanc + HAPI FHIR) are running with seeded data.
- **Environment variables** are set in `.env`.

---

## Part 3: Run the Integration

All commands below require the environment to be loaded:

```bash
source .env
```

### 3.1 List available scenarios

```bash
python3 demo.py --list
```

### 3.2 Run individual scenarios

```bash
python3 demo.py --scenario 1    # E1: Complete accessioned CT study → ASSERT
python3 demo.py --scenario 2    # E1: Study with no accession number → DEFER
python3 demo.py --scenario 3    # E2: Finalised report → ASSERT
python3 demo.py --scenario 4    # E2: Preliminary report → DEFER
python3 demo.py --scenario 5    # E2: Export-restricted report → DEFER (prohibition)
python3 demo.py --scenario 6    # E1 → E2-AI: Live AI screening pipeline
```

Each scenario reads from the local source system (Orthanc or HAPI FHIR),
submits the raw payload to VB-OS Cloud, and displays the evidence extracted,
boundary evaluation, predicate results, and decision.

Scenario 6 is the AI pipeline: it runs E1, and on ASSERT, invokes the VB-OS
OpenAI connector. VB-OS Cloud calls OpenAI server-side, extracts structured
evidence from the AI response, and evaluates the E2-AI boundary. The local
script never touches OpenAI directly — VB-OS governs the entire interaction.

### 3.3 Run all scenarios

```bash
python3 demo.py --all
```

Runs scenarios 1–6 in sequence and reports the pass count.

### 3.4 Run the full E1 → E2-AI → E2 → E3 pipeline

```bash
python3 demo.py --pipeline
```

Executes the complete governed imaging workflow end-to-end:

1. **E1** — Reads the PHENIX CT study from Orthanc, submits raw DICOMweb
   payload to VB-OS Cloud. On ASSERT, proceeds.
2. **E2-AI** — VB-OS Cloud calls OpenAI with the study metadata, extracts
   structured AI evidence, evaluates the AI draft authority boundary. On
   ASSERT, proceeds.
3. **E2** — Reads the final DiagnosticReport from HAPI FHIR, submits the raw
   FHIR resource to VB-OS Cloud. On ASSERT, proceeds.
4. **E3** — Simulates the receiving portal's delivery receipt (FHIR Task),
   submits to VB-OS Cloud. Verifies delivered count matches authorized count.

Each stage gates the next — a DEFER at any boundary halts the pipeline.

### 3.5 Run the E2-AI deterministic verification suite

```bash
python3 verify.py
```

Nine test vectors that prove each governance condition in the E2-AI boundary
evaluates correctly. No live AI call is made — every evidence field is
explicit. Each vector targets one specific predicate:

| Vector | Title | Expected |
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

---

## Part 4: Simulate a Delivery Anomaly

To demonstrate E3 catching a delivery discrepancy:

```bash
export DELIVERY_DRIFT=23
python3 demo.py --pipeline
```

The portal simulation delivers 23 fewer instances than were authorized. E3
evaluates the delivery receipt and DEFERs — the delivery does not match the
authorization.

Reset to normal:

```bash
export DELIVERY_DRIFT=0
```

---

## What Each Script Does

| Script | Purpose | Dependencies |
|--------|---------|--------------|
| `seed_studies.py` | Downloads real anonymized DICOM studies from the Orthanc public archive and uploads them to the local PACS | Standard library only |
| `seed_fhir.py` | Creates the patient, order, and diagnostic reports in the local HAPI FHIR server | Standard library only |
| `demo.py` | Runs individual scenarios and the full pipeline against real source systems | `vb-os` SDK (`pip install vb-os`) |
| `verify.py` | Deterministic E2-AI verification vectors — no live AI calls | Standard library only |
| `bootstrap.py` | (Optional) Standalone provisioning of boundaries, connectors, flows, and certifications — use if not forking the template | Standard library only |

---

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `VBOS_URL` | Yes | VB-OS Cloud API URL. Default: `https://api.vb-os.org` |
| `VBOS_API_KEY` | Yes | API key from Part 1, step 1.5 |
| `VBOS_PROJECT_ID` | Yes | Project ID from Part 1, step 1.3 |
| `VBOS_ENVIRONMENT_ID` | Yes | Environment ID from Part 1, step 1.3 |
| `OPENAI_API_KEY` | No | Only needed if running `bootstrap.py` standalone (the connector credential is configured in the console in step 1.4) |
| `INFERENCE_MODEL_ID` | No | OpenAI model. Default: `gpt-4o-2024-08-06`. Only used by `bootstrap.py`. |
| `ORTHANC_URL` | No | Local PACS URL. Default: `http://localhost:8042` |
| `ORTHANC_USER` | No | PACS username. Default: `vbos` |
| `ORTHANC_PASSWORD` | No | PACS password. Default: `imaging_dev` |
| `FHIR_URL` | No | Local FHIR server URL. Default: `http://localhost:8090/fhir` |
| `DELIVERY_DRIFT` | No | Instances to subtract from delivery. Default: `0` |

---

## Acquisition Modes

This integration demonstrates both acquisition modes supported by VB-OS:

**Caller-supplied payload** (E1, E2, E3): The local script reads from the
source system (Orthanc or HAPI FHIR), then pushes the raw payload to VB-OS
Cloud. VB-OS extracts evidence, evaluates the boundary, and records the
provenance. The platform never touches the source system directly.

**Active provider** (E2-AI): VB-OS Cloud calls OpenAI server-side using the
credentials configured on the connector. The local script sends the prompt
and context evidence; VB-OS makes the API call, parses the response, extracts
structured evidence, and evaluates the boundary. The local script never
communicates with OpenAI.

---

## Boundary DSL Source

The four boundary definitions live in `boundaries/` as VB-OS DSL source files:

| File | Boundary | Stage |
|------|----------|-------|
| `B_IMAGING_STUDY_ADMISSIBILITY.dsl` | Study admissibility | E1 |
| `B_IMAGING_AI_DRAFT_AUTHORITY.dsl` | AI draft authority | E2-AI |
| `B_IMAGING_RELEASE_AUTHORIZATION.dsl` | Release authorization | E2 |
| `B_IMAGING_DELIVERY_EXECUTION.dsl` | Delivery execution | E3 |

These are the source of truth. `bootstrap.py` syncs them to VB-OS Cloud on
every run.

---

## Troubleshooting

**demo.py fails with "HTTP 401"**
Your API key is invalid or expired. Create a new one under **Integrate >
API Keys** (step 1.5).

**demo.py fails with "HTTP 403"**
The API key is missing required scopes. Delete it and create a new one with
all scopes selected.

**demo.py scenario 6 or --pipeline fails at E2-AI**
Check that the OpenAI API key is configured on the **Imaging AI Screening**
connector (step 1.4). VB-OS Cloud calls OpenAI server-side using those
credentials. Also verify `OPENAI_API_KEY` is set in your `.env` (used by
`bootstrap.py` if you run it separately).

**"Connector 'Imaging AI Screening' not found"**
The template fork should have created all 7 connectors. Check your project
under **Integrate > Connectors**. If connectors are missing, re-fork the
template.

**seed_studies.py is slow**
It downloads real DICOM studies (~100 MB) from the Orthanc public archive.
This is a one-time download.

**Orthanc or HAPI FHIR not responding**
Run `docker compose ps` to check container status. If not running:
`docker compose up -d`.
