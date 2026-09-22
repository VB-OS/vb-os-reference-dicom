# Copyright 2026 MNC Labs, Inc. Licensed under Apache 2.0.

boundary_id: B_IMAGING_AI_DRAFT_AUTHORITY
version: 2
scope: "imaging/release"
eta_cap: 600

define_set authorized_classifications: ("negative")
define_set authorized_ai_workflows: ("draft_for_radiologist_review")
define_set acceptable_quality_states: ("acceptable", "good", "excellent")
define_set approved_model_versions: ("gpt-4o-2024-08-06")
define_set authorized_ai_modalities: ("CT", "MR", "CR", "DX")

require_type: ai_classification: string
require_type: ai_recommended_workflow: string
require_type: ai_summary: string
require_type: ai_requires_escalation: integer
require_type: model_used: string
require_type: study_modality: string
require_type: input_quality_status: string
require_type: study_instance_uid: string
require_type: accession_number: string

require_evidence: ai_classification
require_evidence: ai_recommended_workflow
require_evidence: ai_summary
require_evidence: ai_requires_escalation
require_evidence: model_used
require_evidence: study_modality
require_evidence: input_quality_status
require_evidence: study_instance_uid
require_evidence: accession_number

predicate: classification_is_governed: ai_classification IN @authorized_classifications | "AI classification is not in the set authorized for draft advancement"
predicate: workflow_is_governed: ai_recommended_workflow IN @authorized_ai_workflows | "AI proposed a workflow transition not authorized by this boundary"
predicate: no_escalation_flagged: ai_requires_escalation == 0 | "AI has flagged this study for escalation to standard review"
predicate: model_is_approved: model_used IN @approved_model_versions | "Model used for inference is not an approved model version"
predicate: modality_is_authorized: study_modality IN @authorized_ai_modalities | "Study modality is not authorized for AI screening"
predicate: quality_is_acceptable: input_quality_status IN @acceptable_quality_states | "Input quality does not meet minimum standards for AI screening"
predicate: study_is_traceable: accession_number != "" | "Study carries no accession number for audit traceability"
