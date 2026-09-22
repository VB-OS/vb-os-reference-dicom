# Copyright 2026 MNC Labs, Inc. Licensed under Apache 2.0.

boundary_id: B_IMAGING_STUDY_ADMISSIBILITY
version: 1
scope: "imaging/release"
eta_cap: 3600

define_set released_modalities: ("CT", "MR", "CR", "DX")

require_type: study_instance_uid: string
require_type: modality: string
require_type: series_count: integer
require_type: instance_count: integer
require_type: accession_number: string

require_evidence: study_instance_uid
require_evidence: modality
require_evidence: series_count
require_evidence: instance_count
require_evidence: accession_number

predicate: modality_in_release_scope: modality IN @released_modalities | "Study modality is outside the released imaging scope"
predicate: study_has_series: series_count > 0 | "The study contains no series"
predicate: study_has_instances: instance_count > 0 | "The study contains no instances"
predicate: study_is_orderable: accession_number != "0" | "The study carries no accession number and cannot be tied to an imaging order"
