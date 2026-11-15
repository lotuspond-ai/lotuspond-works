"""Exact schema v16 as PostgreSQL renders it: every table's check constraints, and the
partial-index predicates `_predicate` does not reduce to the model's text. Captured from
tests/execution/fixtures/schema-v16.sql (the committed v16 export); never edit by hand.
"""

# fmt: off
V16_FROZEN_CHECKS = {'alert_deliveries': ['attempts >= 0 AND attempts <= 5',
                      "event::text = ANY (ARRAY['incident.opened'::character varying, "
                      "'incident.resolved'::character varying, 'incident.reopened'::character "
                      'varying]::text[])',
                      "status::text = ANY (ARRAY['queued'::character varying, 'sending'::character "
                      "varying, 'delivered'::character varying, 'failed'::character varying, "
                      "'cancelled'::character varying]::text[])"],
 'api_keys': [],
 'approvals': ['decided_by IS NULL OR decided_by::text <> requested_by::text',
               "status::text = 'pending'::text AND decided_by IS NULL AND decided_at IS NULL OR "
               "(status::text = ANY (ARRAY['approved'::character varying, 'rejected'::character "
               'varying]::text[])) AND decided_by IS NOT NULL AND decided_at IS NOT NULL'],
 'assessments': ["status::text = ANY (ARRAY['passed'::character varying, 'failed'::character "
                 "varying, 'unknown'::character varying]::text[])"],
 'audit_events': [],
 'connection_version_secrets': ['connection_version >= 1 AND connection_version <= 2147483647',
                                'secret_version >= 1 AND secret_version <= 2147483647'],
 'connection_versions': ['length(canonical_config) >= 1 AND length(canonical_config) <= 32768',
                         'length(config_digest::text) = 64',
                         'version >= 1 AND version <= 2147483647'],
 'connections': ["status::text = ANY (ARRAY['active'::character varying, 'disabled'::character "
                 "varying, 'revoked'::character varying]::text[])"],
 'deployment_assurance_bindings': [],
 'deployment_bindings': ['(connection_id IS NULL) = (connection_version IS NULL)',
                         '(source_id IS NULL) = (source_version IS NULL)'],
 'environments': ["kind::text = ANY (ARRAY['local'::character varying, 'sandbox'::character "
                  "varying, 'production'::character varying]::text[])"],
 'evaluation_jobs': [],
 'executable_dependencies': ['length(canonical_dependency) >= 1 AND length(canonical_dependency) '
                             '<= 65536',
                             'length(dependency_sha256::text) = 64'],
 'executable_publications': ['(canonical_plan IS NULL) = (compiler_id IS NULL)',
                             '(canonical_plan IS NULL) = (plan_sha256 IS NULL)',
                             "(kind::text = 'workflow'::text) = (canonical_plan IS NOT NULL)",
                             "(kind::text = 'workflow'::text) = (canonical_signature IS NOT NULL)",
                             "kind::text = ANY (ARRAY['workflow'::character varying, "
                             "'connector'::character varying, 'model'::character varying, "
                             "'agent'::character varying, 'evaluation'::character varying, "
                             "'policy'::character varying, 'knowledge'::character varying, "
                             "'artifact'::character varying]::text[])",
                             'length(canonical_package) >= 1 AND length(canonical_package) <= '
                             '1048576',
                             'length(canonical_plan) >= 1 AND length(canonical_plan) <= 1048576',
                             'length(canonical_signature) >= 1 AND length(canonical_signature) <= '
                             '65536',
                             'length(package_sha256::text) = 64',
                             'length(plan_sha256::text) = 64',
                             "status::text = ANY (ARRAY['active'::character varying, "
                             "'revoked'::character varying]::text[])"],
 'execution_action_commands': ['(lease_token_digest IS NULL) = (claimed_at_ms IS NULL) AND '
                               '(claimed_at_ms IS NULL) = (renewed_at_ms IS NULL) AND '
                               '(claimed_at_ms IS NULL) = (lease_expires_at_ms IS NULL) AND '
                               '(claimed_at_ms IS NULL) = (lease_revision IS NULL)',
                               'claimed_at_ms IS NULL OR claimed_at_ms >= 0 AND claimed_at_ms <= '
                               "'9007199254740991'::bigint",
                               'claimed_at_ms IS NULL OR renewed_at_ms >= claimed_at_ms AND '
                               'lease_expires_at_ms > renewed_at_ms AND lease_expires_at_ms <= '
                               '(renewed_at_ms + 60000)',
                               'lease_expires_at_ms IS NULL OR lease_expires_at_ms >= 0 AND '
                               "lease_expires_at_ms <= '9007199254740991'::bigint",
                               'lease_revision IS NULL OR lease_revision >= 1 AND lease_revision '
                               "<= '9007199254740991'::bigint",
                               'length(command_digest::text) = 64',
                               'length(command_id::text) >= 1 AND length(command_id::text) <= 128',
                               'length(installation_id::text) >= 1 AND '
                               'length(installation_id::text) <= 128',
                               'length(lease_token_digest::text) = 64',
                               'recovery_epoch >= 0 AND recovery_epoch <= '
                               "'9007199254740991'::bigint",
                               'renewed_at_ms IS NULL OR renewed_at_ms >= 0 AND renewed_at_ms <= '
                               "'9007199254740991'::bigint",
                               "status::text <> 'claimed'::text OR claimed_at_ms IS NOT NULL",
                               "status::text = ANY (ARRAY['admitted'::character varying, "
                               "'claimed'::character varying, 'stopped'::character varying, "
                               "'uncertain'::character varying, 'expired'::character "
                               'varying]::text[])'],
 'execution_action_decisions': ["decision::text = ANY (ARRAY['allow'::character varying, "
                                "'deny'::character varying]::text[])",
                                'expires_at_ms > issued_at_ms AND expires_at_ms <= '
                                'intent_expires_at_ms',
                                'expires_at_ms >= 0 AND expires_at_ms <= '
                                "'9007199254740991'::bigint",
                                'intent_expires_at_ms >= 0 AND intent_expires_at_ms <= '
                                "'9007199254740991'::bigint",
                                "issued_at_ms >= 0 AND issued_at_ms <= '9007199254740991'::bigint",
                                'length(decision_bytes) >= 1 AND length(decision_bytes) <= 65536',
                                'length(decision_id::text) >= 1 AND length(decision_id::text) <= '
                                '128',
                                'length(decision_sha256::text) = 64'],
 'execution_action_intents': ["approval_mode::text = ANY (ARRAY['none'::character varying, "
                              "'human'::character varying]::text[])",
                              "authorization_state::text = ANY (ARRAY['proposed'::character "
                              "varying, 'awaiting_review'::character varying, "
                              "'approved'::character varying, 'denied'::character varying, "
                              "'review_expired'::character varying, 'expired'::character varying, "
                              "'withdrawn'::character varying, 'superseded'::character "
                              'varying]::text[])',
                              'connection_version >= 1 AND connection_version <= 2147483647',
                              "effect_state::text = ANY (ARRAY['not_dispatched'::character "
                              "varying, 'dispatching'::character varying, 'accepted'::character "
                              "varying, 'observed'::character varying, 'unknown'::character "
                              "varying, 'absent'::character varying]::text[])",
                              'expires_at_ms > issued_at_ms',
                              "expires_at_ms >= 0 AND expires_at_ms <= '9007199254740991'::bigint",
                              "issued_at_ms >= 0 AND issued_at_ms <= '9007199254740991'::bigint",
                              'length(binding_bytes) >= 1 AND length(binding_bytes) <= 65536',
                              'length(input_sha256::text) = 64',
                              'length(intent_sha256::text) = 64',
                              'length(target_sha256::text) = 64',
                              "outcome_state::text = ANY (ARRAY['unknown'::character varying, "
                              "'verified'::character varying, 'not_verified'::character "
                              'varying]::text[])',
                              'recovery_epoch >= 0 AND recovery_epoch <= '
                              "'9007199254740991'::bigint"],
 'execution_action_observations': ["(source::text = 'resolution'::text) = (actor_user_id IS NOT "
                                   'NULL)',
                                   "NOT qualified OR result::text = 'effect_absent'::text",
                                   'length(observation_id::text) >= 1 AND '
                                   'length(observation_id::text) <= 128',
                                   'length(payload_bytes) >= 1 AND length(payload_bytes) <= 65536',
                                   'length(payload_sha256::text) = 64',
                                   'length(rule_id::text) >= 1 AND length(rule_id::text) <= 128',
                                   'length(rule_version::text) >= 1 AND length(rule_version::text) '
                                   '<= 128',
                                   "result::text = ANY (ARRAY['effect_present'::character varying, "
                                   "'effect_absent'::character varying, 'unknown'::character "
                                   'varying]::text[])',
                                   "source::text = ANY (ARRAY['readback'::character varying, "
                                   "'restore_readback'::character varying, 'historical'::character "
                                   "varying, 'resolution'::character varying]::text[])"],
 'execution_action_outcomes': ['(receipt_row_id IS NULL) <> (observation_row_id IS NULL)',
                               'length(outcome_bytes) >= 1 AND length(outcome_bytes) <= 65536',
                               'length(outcome_sha256::text) = 64',
                               'length(rule_id::text) >= 1 AND length(rule_id::text) <= 128',
                               'length(rule_version::text) >= 1 AND length(rule_version::text) <= '
                               '128',
                               'observed_at_ms >= 0 AND observed_at_ms <= '
                               "'9007199254740991'::bigint",
                               "state::text = ANY (ARRAY['verified'::character varying, "
                               "'not_verified'::character varying, 'unknown'::character "
                               'varying]::text[])'],
 'execution_action_receipts': ['(response_artifact_id IS NULL) = (response_artifact_version IS '
                               'NULL)',
                               '(response_artifact_id IS NULL) = (response_sha256 IS NULL)',
                               'length(receipt_bytes) >= 1 AND length(receipt_bytes) <= 65536',
                               'length(receipt_id::text) >= 1 AND length(receipt_id::text) <= 128',
                               'length(receipt_sha256::text) = 64',
                               'length(response_sha256::text) = 64',
                               "source::text = ANY (ARRAY['live'::character varying, "
                               "'historical'::character varying]::text[])",
                               "status::text = ANY (ARRAY['accepted'::character varying, "
                               "'observed_effect'::character varying, 'unknown'::character "
                               'varying]::text[])'],
 'execution_activity_deliveries': ['expires_at_ms > issued_at_ms AND expires_at_ms <= '
                                   '(issued_at_ms + 300000)',
                                   'expires_at_ms >= 0 AND expires_at_ms <= '
                                   "'9007199254740991'::bigint",
                                   "generation >= 1 AND generation <= '9007199254740991'::bigint",
                                   'issued_at_ms >= 0 AND issued_at_ms <= '
                                   "'9007199254740991'::bigint",
                                   'length(canonical_envelope) > 0 AND length(canonical_signature) '
                                   '> 0 AND (length(canonical_envelope) + '
                                   'length(canonical_signature)) <= 65536',
                                   'length(digest::text) = 64',
                                   'length(installation_id::text) >= 1 AND '
                                   'length(installation_id::text) <= 128',
                                   'recovery_epoch >= 0 AND recovery_epoch <= '
                                   "'9007199254740991'::bigint",
                                   'recovery_epoch >= 0 AND recovery_epoch <= '
                                   "'9007199254740991'::bigint",
                                   'retain_until_ms >= (expires_at_ms + 30000)',
                                   'retain_until_ms >= 0 AND retain_until_ms <= '
                                   "'9007199254740991'::bigint",
                                   "status::text = ANY (ARRAY['pending'::character varying, "
                                   "'leased'::character varying, 'delivered'::character varying, "
                                   "'conflict'::character varying]::text[])"],
 'execution_artifacts': ['(source_id IS NULL) = (source_version IS NULL)',
                         'content_bytes >= 1 AND content_bytes <= 1048576',
                         'length(ciphertext) = (content_bytes + 16)',
                         'length(classification::text) >= 1 AND length(classification::text) <= 64',
                         'length(nonce) = 12',
                         'length(sha256::text) = 64',
                         "retain_until_ms >= 0 AND retain_until_ms <= '9007199254740991'::bigint",
                         'version >= 1 AND version <= 2147483647'],
 'execution_attempts': ['(parent_attempt_id IS NULL) = (child_request_digest IS NULL)',
                        '(parent_attempt_id IS NULL) = (child_request_id IS NULL)',
                        '(result_artifact_id IS NULL) = (result_artifact_version IS NULL)',
                        '(result_artifact_id IS NULL) = (result_sha256 IS NULL)',
                        'length(child_request_digest::text) = 64',
                        'ordinal >= 1 AND ordinal <= 2147483647',
                        'parent_attempt_id IS NULL OR parent_attempt_id::text <> id::text',
                        "state::text = ANY (ARRAY['reserved'::character varying, "
                        "'dispatched'::character varying, 'claimed'::character varying, "
                        "'succeeded'::character varying, 'failed'::character varying, "
                        "'uncertain'::character varying, 'cancelled'::character varying]::text[])"],
 'execution_capacities': ['active_limit >= 0 AND active_limit <= 5 AND active >= 0 AND active <= '
                          'active_limit',
                          'next_admission_sequence >= 1 AND next_admission_sequence <= '
                          "'9007199254740991'::bigint",
                          'pending_limit >= 0 AND pending_limit <= 1000 AND pending >= 0 AND '
                          'pending <= pending_limit',
                          'wait_limit >= 0 AND wait_limit <= 10000 AND retained_waits >= 0 AND '
                          'retained_waits <= wait_limit'],
 'execution_context_sources': ["status::text = ANY (ARRAY['active'::character varying, "
                               "'revoked'::character varying, 'deleted'::character "
                               'varying]::text[])'],
 'execution_context_versions': ['(citation_artifact_id IS NULL) = (citation_artifact_version IS '
                                'NULL)',
                                '(citation_artifact_id IS NULL) = (citation_sha256 IS NULL)',
                                '(content_artifact_id IS NULL) = (content_artifact_version IS '
                                'NULL)',
                                '(content_artifact_id IS NULL) = (content_sha256 IS NULL)',
                                'expires_at_ms >= 0 AND expires_at_ms <= '
                                "'9007199254740991'::bigint",
                                'expires_at_ms >= observed_at_ms',
                                'length(classification::text) >= 1 AND '
                                'length(classification::text) <= 64',
                                'observed_at_ms >= 0 AND observed_at_ms <= '
                                "'9007199254740991'::bigint",
                                'version >= 1 AND version <= 2147483647'],
 'execution_control_requests': ["action::text = ANY (ARRAY['pause'::character varying, "
                                "'resume'::character varying, 'cancel'::character "
                                'varying]::text[])',
                                'length(digest::text) = 64'],
 'execution_deployment_revisions': ['length(canonical_configuration) >= 1 AND '
                                    'length(canonical_configuration) <= 1048576',
                                    'length(configuration_sha256::text) = 64',
                                    'revision >= 1 AND revision <= 2147483647',
                                    "status::text = ANY (ARRAY['active'::character varying, "
                                    "'disabled'::character varying, 'revoked'::character "
                                    'varying]::text[])'],
 'execution_deployments': ["status::text = ANY (ARRAY['active'::character varying, "
                           "'disabled'::character varying, 'revoked'::character varying]::text[])"],
 'execution_events': ["(application_state::text = 'pending'::text) = (gap_slot IS NOT NULL)",
                      "application_state::text = ANY (ARRAY['pending'::character varying, "
                      "'applied'::character varying, 'conflict'::character varying]::text[])",
                      'event_artifact_version > 0',
                      "family::text = 'sdk_runtime_event'::text AND (code::text = ANY "
                      "(ARRAY['admission'::character varying, 'step_started'::character varying, "
                      "'step_result'::character varying, 'step_failed'::character varying, "
                      "'wait_started'::character varying, 'signal_received'::character varying, "
                      "'timer_elapsed'::character varying, 'intent_created'::character varying, "
                      "'decision_recorded'::character varying, 'action_dispatched'::character "
                      "varying, 'action_receipt'::character varying, 'dispatch_timeout'::character "
                      "varying, 'reconciliation_observed'::character varying, "
                      "'outcome_recorded'::character varying, 'execution_completed'::character "
                      "varying, 'cancellation_requested'::character varying, "
                      "'execution_failed'::character varying]::text[])) OR family::text = "
                      "'execution_control'::text AND (code::text = ANY "
                      "(ARRAY['admitted'::character varying, 'started'::character varying, "
                      "'attempt_reserved'::character varying, 'attempt_dispatched'::character "
                      "varying, 'attempt_claimed'::character varying, "
                      "'attempt_succeeded'::character varying, 'attempt_failed'::character "
                      "varying, 'attempt_uncertain'::character varying, 'waiting'::character "
                      "varying, 'signal_received'::character varying, 'signal_conflict'::character "
                      "varying, 'paused'::character varying, 'resumed'::character varying, "
                      "'cancelled'::character varying, 'succeeded'::character varying, "
                      "'failed'::character varying, 'reconciliation_required'::character varying, "
                      "'capacity_refused'::character varying, 'budget_refused'::character varying, "
                      "'authority_revoked'::character varying, 'deadline_exceeded'::character "
                      "varying, 'reconciled'::character varying, 'task_opened'::character varying, "
                      "'task_claimed'::character varying, 'task_released'::character varying, "
                      "'task_escalated'::character varying, 'task_expired'::character varying, "
                      "'task_decided'::character varying, 'task_submitted'::character varying, "
                      "'intent_held'::character varying, 'intent_superseded'::character varying, "
                      "'action_resolved'::character varying]::text[]))",
                      'format_version = 1',
                      'gap_slot >= 1 AND gap_slot <= 100',
                      'length(digest::text) = 64',
                      'length(event_id::text) >= 1 AND length(event_id::text) <= 128',
                      'length(installation_id::text) >= 1 AND length(installation_id::text) <= 128',
                      'length(source_id::text) >= 1 AND length(source_id::text) <= 128',
                      "occurred_at_ms >= 0 AND occurred_at_ms <= '9007199254740991'::bigint",
                      "recovery_epoch >= 0 AND recovery_epoch <= '9007199254740991'::bigint",
                      "sequence >= 1 AND sequence <= '9007199254740991'::bigint",
                      "source_generation >= 0 AND source_generation <= '9007199254740991'::bigint",
                      "source_sequence >= 0 AND source_sequence <= '9007199254740991'::bigint"],
 'execution_human_tasks': ['(claim_user_id IS NULL) = (claim_expires_at_ms IS NULL) AND '
                           '(claim_user_id IS NULL) = (claim_revision IS NULL)',
                           '(escalation_assignment IS NULL) = (escalate_at_ms IS NULL)',
                           "(kind::text = 'review'::text) = (intent_id IS NOT NULL)",
                           "assignment::text = ANY (ARRAY['workspace_owners'::character varying, "
                           "'workspace_admins'::character varying, 'workspace_editors'::character "
                           'varying]::text[])',
                           'claim_expires_at_ms IS NULL OR claim_expires_at_ms <= deadline_ms',
                           'claim_expires_at_ms IS NULL OR claim_expires_at_ms >= 0 AND '
                           "claim_expires_at_ms <= '9007199254740991'::bigint",
                           'claim_revision IS NULL OR claim_revision >= 1 AND claim_revision <= '
                           "'9007199254740991'::bigint",
                           "deadline_ms >= 0 AND deadline_ms <= '9007199254740991'::bigint",
                           'escalate_at_ms IS NULL OR escalate_at_ms < deadline_ms',
                           'escalate_at_ms IS NULL OR escalate_at_ms >= 0 AND escalate_at_ms <= '
                           "'9007199254740991'::bigint",
                           'escalated_at_ms IS NULL OR escalate_at_ms IS NOT NULL',
                           'escalated_at_ms IS NULL OR escalated_at_ms >= 0 AND escalated_at_ms <= '
                           "'9007199254740991'::bigint",
                           'escalation_assignment IS NULL OR (escalation_assignment::text = ANY '
                           "(ARRAY['workspace_owners'::character varying, "
                           "'workspace_admins'::character varying, 'workspace_editors'::character "
                           'varying]::text[]))',
                           "kind::text = ANY (ARRAY['review'::character varying, 'form'::character "
                           'varying]::text[])',
                           "state::text = ANY (ARRAY['open'::character varying, "
                           "'decided'::character varying, 'submitted'::character varying, "
                           "'expired'::character varying, 'cancelled'::character "
                           'varying]::text[])'],
 'execution_inbox': ['(leased_at_ms IS NULL) = (lease_expires_at_ms IS NULL)',
                     '(leased_at_ms IS NULL) = (lease_token_digest IS NULL)',
                     "available_at_ms >= 0 AND available_at_ms <= '9007199254740991'::bigint",
                     'delivery_count >= 0 AND delivery_count <= 2147483647',
                     "expires_at_ms >= 0 AND expires_at_ms <= '9007199254740991'::bigint",
                     'lease_expires_at_ms > leased_at_ms AND lease_expires_at_ms <= (leased_at_ms '
                     '+ 60000)',
                     'lease_expires_at_ms >= 0 AND lease_expires_at_ms <= '
                     "'9007199254740991'::bigint",
                     "leased_at_ms >= 0 AND leased_at_ms <= '9007199254740991'::bigint",
                     'length(digest::text) = 64',
                     'length(lease_token_digest::text) = 64',
                     "status::text <> 'leased'::text OR leased_at_ms IS NOT NULL",
                     "status::text = ANY (ARRAY['pending'::character varying, 'leased'::character "
                     "varying, 'delivered'::character varying, 'conflict'::character "
                     'varying]::text[])'],
 'execution_leases': ["claimed_at_ms >= 0 AND claimed_at_ms <= '9007199254740991'::bigint",
                      "expires_at_ms >= 0 AND expires_at_ms <= '9007199254740991'::bigint",
                      'length(token_digest::text) = 64',
                      "renewed_at_ms >= 0 AND renewed_at_ms <= '9007199254740991'::bigint",
                      'renewed_at_ms >= claimed_at_ms AND expires_at_ms > renewed_at_ms AND '
                      'expires_at_ms <= (renewed_at_ms + 60000)',
                      "status::text = ANY (ARRAY['active'::character varying, 'stopped'::character "
                      "varying, 'uncertain'::character varying]::text[])"],
 'execution_outbox': ['(leased_at_ms IS NULL) = (lease_expires_at_ms IS NULL)',
                      '(leased_at_ms IS NULL) = (lease_token_digest IS NULL)',
                      "available_at_ms >= 0 AND available_at_ms <= '9007199254740991'::bigint",
                      'delivery_count >= 0 AND delivery_count <= 2147483647',
                      "kind::text = ANY (ARRAY['engine_start'::character varying, "
                      "'engine_signal'::character varying, 'activity'::character varying, "
                      "'control'::character varying]::text[])",
                      'lease_expires_at_ms > leased_at_ms AND lease_expires_at_ms <= (leased_at_ms '
                      '+ 60000)',
                      'lease_expires_at_ms >= 0 AND lease_expires_at_ms <= '
                      "'9007199254740991'::bigint",
                      "leased_at_ms >= 0 AND leased_at_ms <= '9007199254740991'::bigint",
                      'length(digest::text) = 64',
                      'length(lease_token_digest::text) = 64',
                      "status::text <> 'leased'::text OR leased_at_ms IS NOT NULL",
                      "status::text = ANY (ARRAY['pending'::character varying, 'leased'::character "
                      "varying, 'delivered'::character varying, 'conflict'::character "
                      'varying]::text[])'],
 'execution_reconciliation_observations': ["code::text = ANY (ARRAY['compute_stopped'::character "
                                           "varying, 'compute_running'::character varying, "
                                           "'outcome_unknown'::character varying, "
                                           "'usage_known'::character varying, "
                                           "'authority_revoked'::character varying, "
                                           "'engine_missing'::character varying, "
                                           "'identity_conflict'::character varying]::text[])",
                                           'length(digest::text) = 64',
                                           'observed_at_ms >= 0 AND observed_at_ms <= '
                                           "'9007199254740991'::bigint"],
 'execution_requests': ['length(digest::text) = 64',
                        'length(request_key::text) >= 1 AND length(request_key::text) <= 128'],
 'execution_scheduler_cursors': ['active_limit >= 0 AND active_limit <= 25 AND active >= 0 AND '
                                 'active <= active_limit',
                                 "cursor >= 0 AND cursor <= '9007199254740991'::bigint"],
 'execution_step_instances': ['length(occurrence_path::text) >= 1 AND '
                              'length(occurrence_path::text) <= 1024'],
 'execution_usage_reservations': ["active_ms >= 0 AND active_ms <= '9007199254740991'::bigint",
                                  'completed_at_ms >= 0 AND completed_at_ms <= '
                                  "'9007199254740991'::bigint",
                                  'compute_stopped_at_ms >= 0 AND compute_stopped_at_ms <= '
                                  "'9007199254740991'::bigint",
                                  'cost_microusd >= 0 AND cost_microusd <= '
                                  "'9007199254740991'::bigint",
                                  'input_tokens >= 0 AND input_tokens <= '
                                  "'9007199254740991'::bigint",
                                  'length(settlement_digest::text) = 64',
                                  'observed_cost_microusd >= 0 AND observed_cost_microusd <= '
                                  "'9007199254740991'::bigint",
                                  'observed_input_tokens >= 0 AND observed_input_tokens <= '
                                  "'9007199254740991'::bigint",
                                  'observed_output_tokens >= 0 AND observed_output_tokens <= '
                                  "'9007199254740991'::bigint",
                                  'output_tokens >= 0 AND output_tokens <= '
                                  "'9007199254740991'::bigint",
                                  "state::text <> 'released'::text OR compute_stopped_at_ms IS NOT "
                                  'NULL',
                                  "state::text <> 'settled'::text OR observed_input_tokens IS NOT "
                                  'NULL AND observed_output_tokens IS NOT NULL AND '
                                  'observed_cost_microusd IS NOT NULL AND completed_at_ms IS NOT '
                                  'NULL AND settlement_digest IS NOT NULL',
                                  "state::text = ANY (ARRAY['reserved'::character varying, "
                                  "'settled'::character varying, 'uncertain'::character varying, "
                                  "'released'::character varying]::text[])"],
 'execution_waits': ["deadline_ms >= 0 AND deadline_ms <= '9007199254740991'::bigint",
                     "state::text = ANY (ARRAY['open'::character varying, 'satisfied'::character "
                     "varying, 'expired'::character varying, 'cancelled'::character "
                     'varying]::text[])'],
 'executions': ['(result_artifact_id IS NULL) = (result_artifact_version IS NULL)',
                '(result_artifact_id IS NULL) = (result_sha256 IS NULL)',
                "admission_sequence >= 1 AND admission_sequence <= '9007199254740991'::bigint",
                "admitted_at_ms >= 0 AND admitted_at_ms <= '9007199254740991'::bigint",
                'deadline_ms > admitted_at_ms',
                "deadline_ms >= 0 AND deadline_ms <= '9007199254740991'::bigint",
                'length(admission_digest::text) = 64',
                "next_event_sequence >= 1 AND next_event_sequence <= '9007199254740991'::bigint",
                "reason::text = ANY (ARRAY['capacity'::character varying, 'budget'::character "
                "varying, 'entitlement_unavailable'::character varying, "
                "'authority_revoked'::character varying, 'deadline_exceeded'::character varying, "
                "'signal'::character varying, 'control'::character varying, "
                "'provider_unavailable'::character varying, 'uncertain'::character varying, "
                "'completed'::character varying, 'invalid_result'::character varying, "
                "'review'::character varying, 'action_uncertain'::character varying, "
                "'containment'::character varying, 'approval_denied'::character varying, "
                "'review_expired'::character varying, 'approval_expired'::character varying, "
                "'approval_revoked'::character varying, 'binding_refused'::character varying, "
                "'not_applied'::character varying, 'outcome_not_verified'::character varying, "
                "'outcome_unknown'::character varying, 'recovery_epoch_changed'::character "
                'varying]::text[])',
                "recovery_epoch >= 0 AND recovery_epoch <= '9007199254740991'::bigint",
                "requested_state::text = ANY (ARRAY['running'::character varying, "
                "'paused'::character varying, 'cancelled'::character varying]::text[])",
                "runnable_at_ms >= 0 AND runnable_at_ms <= '9007199254740991'::bigint",
                "runnable_at_ms IS NULL OR (state::text <> ALL (ARRAY['succeeded'::character "
                "varying, 'failed'::character varying, 'cancelled'::character varying]::text[]))",
                "state::text = ANY (ARRAY['pending'::character varying, 'running'::character "
                "varying, 'waiting'::character varying, 'paused'::character varying, "
                "'cancelled'::character varying, 'succeeded'::character varying, "
                "'failed'::character varying, 'reconciliation_required'::character "
                'varying]::text[])'],
 'fleet_mutation_requests': ['length(request_digest::text) = 64',
                             'length(request_key::text) >= 1 AND length(request_key::text) <= 128'],
 'gateway_usage': ['actual_usd IS NULL OR actual_usd >= 0::numeric',
                   'completion_tokens IS NULL OR completion_tokens >= 0',
                   'latency_ms IS NULL OR latency_ms >= 0',
                   'prompt_tokens IS NULL OR prompt_tokens >= 0',
                   'reserved_usd >= 0::numeric AND charged_usd >= 0::numeric',
                   "status::text = ANY (ARRAY['reserved'::character varying, 'settled'::character "
                   "varying, 'uncertain'::character varying]::text[])"],
 'identity_challenges': ["kind::text = ANY (ARRAY['email_verification'::character varying, "
                         "'password_reset'::character varying]::text[])"],
 'incident_events': ["action::text = ANY (ARRAY['created'::character varying, "
                     "'resolved'::character varying, 'reopened'::character varying]::text[])"],
 'incidents': ["category::text = ANY (ARRAY['manual'::character varying, "
               "'assessment_failed'::character varying, 'assessment_unavailable'::character "
               'varying]::text[])',
               "severity::text = ANY (ARRAY['low'::character varying, 'medium'::character varying, "
               "'high'::character varying, 'critical'::character varying]::text[])",
               "source::text = ANY (ARRAY['manual'::character varying, 'monitor'::character "
               "varying, 'trace_assessment'::character varying, 'voice_assessment'::character "
               'varying]::text[])',
               "status::text = 'open'::text AND resolved_at IS NULL OR status::text = "
               "'resolved'::text AND resolved_at IS NOT NULL",
               "status::text = ANY (ARRAY['open'::character varying, 'resolved'::character "
               'varying]::text[])'],
 'integrations': [],
 'invitations': ["role::text = ANY (ARRAY['admin'::character varying, 'editor'::character varying, "
                 "'viewer'::character varying]::text[])"],
 'mail_outbox': ['attempts >= 0 AND attempts <= 5',
                 "kind::text = 'security_notice'::text AND identity_challenge_id IS NULL AND "
                 "invitation_id IS NULL OR kind::text <> 'security_notice'::text AND "
                 '(identity_challenge_id IS NOT NULL AND invitation_id IS NULL OR '
                 'identity_challenge_id IS NULL AND invitation_id IS NOT NULL)',
                 "kind::text = ANY (ARRAY['email_verification'::character varying, "
                 "'password_reset'::character varying, 'invitation'::character varying, "
                 "'security_notice'::character varying]::text[])",
                 "status::text = ANY (ARRAY['queued'::character varying, 'sending'::character "
                 "varying, 'sent'::character varying, 'failed'::character varying, "
                 "'cancelled'::character varying]::text[])"],
 'memberships': ["role::text = ANY (ARRAY['owner'::character varying, 'admin'::character varying, "
                 "'editor'::character varying, 'viewer'::character varying]::text[])"],
 'mfa_enrollments': ['expires_at > created_at',
                     'factor_version >= 1',
                     'length(secret_ciphertext) > 0'],
 'mfa_factors': ['last_totp_counter IS NULL OR last_totp_counter >= 0',
                 'length(secret_ciphertext) > 0',
                 'version >= 1'],
 'mfa_login_challenges': ['NOT (consumed_at IS NOT NULL AND revoked_at IS NOT NULL)',
                          'attempts >= 0 AND attempts <= 8',
                          'consumed_at IS NULL OR consumed_at >= created_at',
                          'expires_at > created_at',
                          'factor_version >= 1',
                          'length(token_hash::text) = 64',
                          'revoked_at IS NULL OR revoked_at >= created_at'],
 'mfa_recovery_codes': ['consumed_at IS NULL OR consumed_at >= created_at',
                        'factor_version >= 1',
                        'length(code_hash::text) = 64'],
 'monitors': ['sample_basis_points >= 0 AND sample_basis_points <= 10000'],
 'offline_enrollments': ['NOT (consumed_at IS NOT NULL AND revoked_at IS NOT NULL)',
                         'consumed_at IS NULL OR consumed_at >= created_at',
                         'revoked_at IS NULL OR revoked_at >= created_at'],
 'offline_identity_attestations': ['expires_at IS NULL OR expires_at > attested_at',
                                   'revoked_at IS NULL OR revoked_at >= attested_at',
                                   "source::text = ANY (ARRAY['in_person'::character varying, "
                                   "'government_credential'::character varying, "
                                   "'enterprise_record'::character varying]::text[])"],
 'package_publication_requests': ['length(request_hash::text) = 64',
                                  'length(request_key::text) >= 1 AND length(request_key::text) <= '
                                  '128'],
 'policies': ['minimum_pass_rate >= 0::numeric AND minimum_pass_rate <= 1::numeric AND '
              'maximum_regression >= 0::numeric AND maximum_regression <= 1::numeric'],
 'projects': [],
 'receipts': ["outcome::text = ANY (ARRAY['success'::character varying, 'failure'::character "
              "varying, 'unknown'::character varying]::text[])"],
 'release_decisions': ["verdict::text = ANY (ARRAY['pass'::character varying, 'block'::character "
                       "varying, 'approval_required'::character varying]::text[])"],
 'releases': [],
 'run_results': ['latency_ms >= 0 AND cost_usd >= 0::numeric'],
 'runners': [],
 'runs': ["status::text = ANY (ARRAY['queued'::character varying, 'running'::character varying, "
          "'completed'::character varying, 'failed'::character varying, 'cancelled'::character "
          'varying]::text[])'],
 'runtime_command_admissions': ['admitted_at_ms >= 0 AND admitted_at_ms <= '
                                "'9007199254740991'::bigint",
                                'claimed_at_ms IS NULL OR claimed_at_ms >= 0 AND claimed_at_ms <= '
                                "'9007199254740991'::bigint",
                                'command_expires_at_ms >= 0 AND command_expires_at_ms <= '
                                "'9007199254740991'::bigint",
                                "generation >= 1 AND generation <= '9007199254740991'::bigint",
                                'length(command_bytes) >= 1 AND length(command_bytes) <= 65536',
                                'length(command_digest::text) = 64',
                                'length(installation_id::text) >= 1 AND '
                                'length(installation_id::text) <= 128',
                                'length(signature_bytes) >= 1 AND length(signature_bytes) <= 65536',
                                'recovery_epoch >= 0 AND recovery_epoch <= '
                                "'9007199254740991'::bigint",
                                'retain_until_ms IS NULL OR retain_until_ms >= 0 AND '
                                "retain_until_ms <= '9007199254740991'::bigint",
                                "status::text <> 'terminal'::text OR terminal_at_ms IS NOT NULL "
                                'AND retain_until_ms IS NOT NULL AND retain_until_ms >= '
                                '(terminal_at_ms + 86400000) AND retain_until_ms >= '
                                '(command_expires_at_ms + 30000)',
                                "status::text = ANY (ARRAY['admitted'::character varying, "
                                "'claimed'::character varying, 'terminal'::character varying, "
                                "'uncertain'::character varying]::text[])",
                                'terminal_at_ms IS NULL OR terminal_at_ms >= 0 AND terminal_at_ms '
                                "<= '9007199254740991'::bigint",
                                'trust_revision >= 1 AND trust_revision <= '
                                "'9007199254740991'::bigint"],
 'runtime_credentials': ['activation_deadline_ms IS NULL OR activation_deadline_ms >= 0 AND '
                         "activation_deadline_ms <= '9007199254740991'::bigint",
                         "expires_at_ms >= 0 AND expires_at_ms <= '9007199254740991'::bigint",
                         'generation IS NULL OR generation >= 1 AND generation <= '
                         "'9007199254740991'::bigint",
                         'length(installation_id::text) >= 1 AND length(installation_id::text) <= '
                         '128',
                         'length(token_digest::text) = 64',
                         'predecessor_id IS NULL OR predecessor_id::text <> id::text',
                         "recovery_epoch >= 0 AND recovery_epoch <= '9007199254740991'::bigint",
                         'session_id IS NULL AND generation IS NULL OR session_id IS NOT NULL AND '
                         'generation IS NOT NULL',
                         "status::text <> 'pending'::text OR predecessor_id IS NOT NULL AND "
                         'session_id IS NOT NULL AND generation IS NOT NULL AND '
                         'prepared_policy_version IS NOT NULL AND activation_deadline_ms IS NOT '
                         'NULL',
                         "status::text = ANY (ARRAY['pending'::character varying, "
                         "'active'::character varying, 'revoked'::character varying]::text[])",
                         'successor_id IS NULL OR successor_id::text <> id::text',
                         'version >= 1 AND version <= 2147483647'],
 'runtime_enrollments': ['consumed_at_ms IS NULL OR consumed_at_ms >= 0 AND consumed_at_ms <= '
                         "'9007199254740991'::bigint",
                         "expires_at_ms >= 0 AND expires_at_ms <= '9007199254740991'::bigint",
                         'length(installation_id::text) >= 1 AND length(installation_id::text) <= '
                         '128',
                         'length(observation_ceiling) >= 1 AND length(observation_ceiling) <= '
                         '32768',
                         'length(token_digest::text) = 64',
                         'policy_version >= 1 AND policy_version <= 2147483647',
                         "recovery_epoch >= 0 AND recovery_epoch <= '9007199254740991'::bigint",
                         "status::text = ANY (ARRAY['issued'::character varying, "
                         "'consumed'::character varying, 'revoked'::character varying]::text[])"],
 'runtime_instances': ["generation >= 0 AND generation <= '9007199254740991'::bigint",
                       'last_seen_at_ms IS NULL OR last_seen_at_ms >= 0 AND last_seen_at_ms <= '
                       "'9007199254740991'::bigint",
                       'length(installation_id::text) >= 1 AND length(installation_id::text) <= '
                       '128',
                       'length(observation_ceiling) >= 1 AND length(observation_ceiling) <= 32768',
                       "recovery_epoch >= 0 AND recovery_epoch <= '9007199254740991'::bigint",
                       "status::text = ANY (ARRAY['active'::character varying, "
                       "'revoked'::character varying]::text[])"],
 'runtime_pool_policies': ['length(canonical_policy) >= 1 AND length(canonical_policy) <= 32768',
                           'length(policy_digest::text) = 64',
                           'version >= 1 AND version <= 2147483647'],
 'runtime_pool_policy_connections': ['policy_version >= 1 AND policy_version <= 2147483647'],
 'runtime_pools': ['current_policy_version IS NULL OR current_policy_version >= 1 AND '
                   'current_policy_version <= 2147483647',
                   "status::text = ANY (ARRAY['active'::character varying, 'disabled'::character "
                   "varying, 'revoked'::character varying]::text[])"],
 'runtime_sessions': ["expires_at_ms >= 0 AND expires_at_ms <= '9007199254740991'::bigint",
                      "generation >= 1 AND generation <= '9007199254740991'::bigint",
                      'length(installation_id::text) >= 1 AND length(installation_id::text) <= 128',
                      'length(token_digest::text) = 64',
                      "recovery_epoch >= 0 AND recovery_epoch <= '9007199254740991'::bigint",
                      "status::text = ANY (ARRAY['active'::character varying, 'revoked'::character "
                      'varying]::text[])'],
 'scenarios': ["modality::text = ANY (ARRAY['text'::character varying, 'voice'::character varying, "
               "'tool'::character varying, 'multimodal'::character varying]::text[])"],
 'schema_version': [],
 'secret_references': ["status::text = ANY (ARRAY['active'::character varying, "
                       "'revoked'::character varying]::text[])",
                       'version >= 1 AND version <= 2147483647'],
 'sessions': ['mfa_factor_version IS NULL OR mfa_factor_version >= 1',
              'mfa_verified_at IS NULL AND mfa_factor_id IS NULL AND mfa_factor_version IS NULL OR '
              'mfa_verified_at IS NOT NULL AND mfa_factor_id IS NOT NULL AND mfa_factor_version IS '
              'NOT NULL',
              'mfa_verified_at IS NULL OR mfa_verified_at <= created_at'],
 'traces': ["(status::text = ANY (ARRAY['ok'::character varying, 'error'::character "
            'varying]::text[])) AND latency_ms >= 0 AND cost_usd >= 0::numeric',
            "cost_measurement::text = 'reported'::text AND cost_usd IS NOT NULL OR "
            "cost_measurement::text = 'unavailable'::text AND cost_usd IS NULL OR "
            "cost_measurement::text = 'unknown'::text"],
 'users': ['email::text = lower(email::text)'],
 'voice_segments': ['sequence >= 0 AND revision >= 0 AND start_ms >= 0 AND end_ms >= start_ms'],
 'voice_sessions': ["status::text = ANY (ARRAY['open'::character varying, 'closed'::character "
                    'varying]::text[])'],
 'workflow_package_versions': ['content_bytes > 0 AND content_bytes = length(canonical_content)',
                               'length(sha256::text) = 64',
                               "publisher_kind::text = ANY (ARRAY['user'::character varying, "
                               "'api_key'::character varying]::text[])"],
 'workspaces': ["kind::text = 'organization'::text OR require_mfa = false",
                "kind::text = ANY (ARRAY['personal'::character varying, 'organization'::character "
                'varying]::text[])',
                'monthly_budget_usd IS NULL OR monthly_budget_usd >= 0::numeric',
                "plan::text = ANY (ARRAY['developer'::character varying, 'team'::character "
                "varying, 'enterprise'::character varying]::text[])",
                "status::text = ANY (ARRAY['active'::character varying, 'suspended'::character "
                'varying]::text[])']}

V16_FROZEN_PREDICATES = {'execution_action_hold_idx': "((effect_state) = ANY ((ARRAY['not_dispatched', 'dispatching', "
                              "'accepted', 'unknown']))) AND ((authorization_state) <> ALL "
                              "((ARRAY['denied', 'review_expired', 'expired', 'withdrawn', "
                              "'superseded'])))",
 'execution_action_lease_due_idx': "(status) = 'claimed'",
 'execution_restored_idx': "(state) <> ALL ((ARRAY['succeeded', 'failed', 'cancelled']))",
 'execution_task_due_idx': "(state) = 'open'",
 'execution_task_escalation_idx': "((state) = 'open') AND (escalated_at_ms IS NULL) AND "
                                  '(escalate_at_ms IS NOT NULL)',
 'execution_wait_due_idx': "(state) = 'open'"}
# fmt: on
