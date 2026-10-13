import type { Row } from '../api.js';

export type IncidentCategory = 'manual' | 'assessment_failed' | 'assessment_unavailable';
export type Incident = Row & {
  title: string;
  severity: 'low' | 'medium' | 'high' | 'critical';
  status: 'open' | 'resolved';
  category: IncidentCategory;
  source: 'manual' | 'monitor' | 'trace_assessment' | 'voice_assessment';
  project_id?: string | null;
  trace_id?: string | null;
  voice_session_id?: string | null;
  assessment_id?: string | null;
  monitor_id?: string | null;
  resolution_reason?: string | null;
  created_at: string;
  resolved_at?: string | null;
  reopened_at?: string | null;
};
export type IncidentHistory = {
  id: string;
  action: 'created' | 'resolved' | 'reopened';
  actor_id: string;
  reason: string | null;
  created_at: string;
};
export type IncidentAlert = {
  id: string;
  destination: string;
  event: string;
  status: 'queued' | 'sending' | 'delivered' | 'failed' | 'cancelled';
  attempts: number;
  available_at: string;
  delivered_at: string | null;
  last_error_code: string | null;
  created_at: string;
};
export type IncidentDetail = {
  incident: Incident;
  project: Row | null;
  trace: Row | null;
  voice_session: Row | null;
  assessment: Row | null;
  monitor: Row | null;
  history: IncidentHistory[];
  alerts: IncidentAlert[];
};

export function incidentCategory(value: unknown) {
  switch (value) {
    case 'assessment_failed':
      return {
        label: 'Assessment failed',
        explanation:
          'A recorded assessment judged the agent behavior as failed. Review the retained evidence and the evaluator’s explanation.',
        tone: 'danger',
      };
    case 'assessment_unavailable':
      return {
        label: 'Evaluation unavailable',
        explanation:
          'The evaluator did not produce a conclusive result. This does not establish that the agent failed or passed.',
        tone: 'warning',
      };
    case 'manual':
      return {
        label: 'Manual report',
        explanation:
          'A person or application reported this incident. The report itself is not an automated assessment verdict.',
        tone: 'neutral',
      };
    default:
      return {
        label: 'Category not recorded',
        explanation:
          'The incident category was not recorded. Inspect its evidence before drawing a conclusion.',
        tone: 'neutral',
      };
  }
}
export function incidentSource(value: unknown): string {
  return (
    (
      {
        manual: 'Manual report',
        monitor: 'Production monitor',
        trace_assessment: 'Trace assessment',
        voice_assessment: 'Voice assessment',
      } as Record<string, string>
    )[String(value)] || 'Source not recorded'
  );
}
export function alertDelivery(value: unknown): string {
  return (
    (
      {
        queued: 'Queued',
        sending: 'Sending',
        delivered: 'Webhook accepted',
        failed: 'Delivery failed',
        cancelled: 'Cancelled',
      } as Record<string, string>
    )[String(value)] || 'Status not recorded'
  );
}
