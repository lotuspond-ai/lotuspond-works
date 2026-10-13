import assert from 'node:assert/strict';
import test from 'node:test';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { IncidentReview } from '../components/IncidentReview.js';
import type { IncidentDetail } from './incidents.js';

const fixture: IncidentDetail = {
  incident: {
    id: 'synthetic-incident',
    title: 'Synthetic evaluator outage',
    severity: 'medium',
    status: 'resolved',
    category: 'assessment_unavailable',
    source: 'voice_assessment',
    assessment_id: 'synthetic-assessment',
    voice_session_id: 'synthetic-voice',
    created_at: '2026-09-21T00:00:00Z',
    resolution_reason: 'Investigated evaluator availability; no agent outcome inferred.',
  },
  project: null,
  trace: null,
  voice_session: { id: 'synthetic-voice', name: 'Synthetic voice session' },
  monitor: null,
  assessment: {
    id: 'synthetic-assessment',
    status: 'unknown',
    verdict: 'unknown',
    model: 'Synthetic evaluator',
    reason: 'The evaluator was unavailable.',
    provenance: { provider_configured: false },
  },
  history: [
    {
      id: 'synthetic-history',
      action: 'resolved',
      actor_id: 'synthetic-reviewer',
      reason: 'Recorded investigation.',
      created_at: '2026-09-21T00:00:00Z',
    },
  ],
  alerts: [],
};
test('resolving an unavailable assessment retains its original unknown evidence', () => {
  const html = renderToStaticMarkup(
    createElement(IncidentReview, {
      detail: fixture,
      workspace: 'synthetic-workspace',
      onOpenProject: () => {},
    }),
  );
  assert.match(html, /Evaluation unavailable/);
  assert.match(html, />Unknown</);
  assert.match(html, /does not change its original assessment or release verdict/);
  assert.match(html, /No alert delivery record is available/);
  assert.match(html, /Recorded investigation/);
  assert.doesNotMatch(html, /All configured release checks passed|Agent passed/);
});
test('alert status and timestamps remain server-backed with explicit acceptance boundaries', () => {
  const detail: IncidentDetail = {
    ...fixture,
    alerts: [
      {
        id: 'synthetic-alert',
        destination: 'Synthetic operator receiver',
        event: 'incident.created',
        status: 'delivered',
        attempts: 2,
        available_at: '2026-09-21T00:00:00Z',
        delivered_at: '2026-09-21T00:01:00Z',
        last_error_code: null,
        created_at: '2026-09-21T00:00:00Z',
      },
    ],
  };
  const html = renderToStaticMarkup(
    createElement(IncidentReview, {
      detail,
      workspace: 'synthetic-workspace',
      onOpenProject: () => {},
    }),
  );
  assert.match(html, /Webhook accepted/);
  assert.match(html, /does not confirm that a downstream notification reached a person/);
  assert.match(html, /Synthetic operator receiver/);
  assert.match(html, /<dt>Attempts<\/dt><dd>2<\/dd>/);
  assert.doesNotMatch(html, /Notification delivered|Email delivered/);
});
