# Copyright 2026 MNC Labs, Inc. Licensed under Apache 2.0.

boundary_id: B_IMAGING_DELIVERY_EXECUTION
version: 1
scope: "imaging/release"
eta_cap: 300

define_set completed_task_states: ("completed")

require_type: delivery_state: string
require_type: delivered_instance_count: integer
require_type: authorized_instance_count: integer
require_type: recipient_reference: string
require_type: delivery_accession: string

require_evidence: delivery_state
require_evidence: delivered_instance_count
require_evidence: authorized_instance_count
require_evidence: recipient_reference
require_evidence: delivery_accession

predicate: delivery_completed: delivery_state IN @completed_task_states | "The receiving portal did not report the delivery as completed"
predicate: no_execution_drift: delivered_instance_count == authorized_instance_count | "The portal received a different number of instances than were authorized"
predicate: recipient_is_named: recipient_reference != "" | "The delivery task names no recipient"
predicate: accession_carried_through: delivery_accession != "" | "The delivery receipt carries no accession number"
