boundary_id: B_ACCESSIUM_RELEASE_AUTHORIZATION
version: 1
scope: "accessium/imaging-release"
eta_cap: 900

define_set attesting_report_states: ("final", "amended")

require_type: report_state: string
require_type: report_accession: string
require_type: order_reference: string
require_type: subject_reference: string

require_evidence: report_state
require_evidence: report_accession
require_evidence: order_reference
require_evidence: subject_reference

prohibit_evidence: export_restriction WHERE export_restriction == 1

predicate: report_is_attested: report_state IN @attesting_report_states | "The diagnostic report has not been finalised or amended by the reporting radiologist"
predicate: report_carries_accession: report_accession != "" | "The report carries no accession number and cannot be tied to a study"
predicate: report_linked_to_order: order_reference != "" | "The report is not linked to an imaging order"
predicate: report_has_subject: subject_reference != "" | "The report identifies no subject"
