boundary_id: B_ACCESSIUM_DELIVERY_EXECUTION
version: 1
scope: "accessium/imaging-release"
eta_cap: 300

require_type: delivery_terminal_status: integer
require_type: delivered_instance_count: integer
require_type: authorized_instance_count: integer
require_type: recipient_matched: integer

require_evidence: delivery_terminal_status
require_evidence: delivered_instance_count
require_evidence: authorized_instance_count
require_evidence: recipient_matched
require_evidence IF delivery_terminal_status == 0: failure_code

predicate: delivery_confirmed: delivery_terminal_status == 1 | "The receiving portal did not confirm delivery"
predicate: recipient_is_expected: recipient_matched == 1 | "The confirming recipient is not the authorized recipient"
predicate: no_execution_drift: delivered_instance_count == authorized_instance_count | "The portal received a different number of instances than were authorized"
