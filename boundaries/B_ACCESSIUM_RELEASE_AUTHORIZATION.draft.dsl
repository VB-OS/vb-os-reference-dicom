boundary_id: B_ACCESSIUM_RELEASE_AUTHORIZATION
version: 1
scope: "accessium/imaging-release"
eta_cap: 900

define_set authorizing_order_states: ("active", "completed")
define_set attesting_report_states: ("final", "amended")
define_set external_channels: ("referring_portal", "payer_portal")

require_type: order_state: string
require_type: report_state: string
require_type: release_channel: string
require_type: order_authorized: integer
require_type: patient_order_matched: integer

require_evidence: order_state
require_evidence: report_state
require_evidence: release_channel
require_evidence: order_authorized
require_evidence: patient_order_matched
require_evidence IF release_channel == "referring_portal": recipient_practitioner_id
require_evidence IF release_channel == "payer_portal": recipient_organization_id

prohibit_evidence: phi_export_hold WHERE phi_export_hold == 1

predicate: order_is_authorizing: order_state IN @authorizing_order_states AND order_authorized == 1 | "The imaging order is not in a state that authorizes release"
predicate: report_is_attested: report_state IN @attesting_report_states | "The diagnostic report has not been finalized or amended by the reporting radiologist"
predicate: order_matches_study: patient_order_matched == 1 | "The study subject does not reconcile with the order subject"
predicate: channel_is_governed: release_channel IN @external_channels | "Release channel is not a governed external channel"
