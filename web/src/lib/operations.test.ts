import assert from 'node:assert/strict';
import test from 'node:test';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { Operations, OperationsSnapshotView } from '../pages/Operations.js';
import { elapsedSeconds, pollLabel, type OperationsSnapshot } from './operations.js';

function snapshot(): OperationsSnapshot {
  const observation = {
    state: 'missing' as const,
    last_poll_at: null,
    age_seconds: null,
    fresh_for_seconds: 150,
    error_code: null,
  };
  const queue = {
    enabled: true,
    eligible_now: 0,
    delayed: 0,
    suspended: 0,
    expired_leases: 0,
    oldest_eligible_age_seconds: null,
  };
  return {
    observed_at: '2026-09-21T12:00:00Z',
    window: {
      start_at: '2026-09-20T12:00:00Z',
      end_at: '2026-09-21T12:00:00Z',
      duration_seconds: 86400,
    },
    dependencies: { database: { state: 'ok' }, redis: { state: 'ok' } },
    workers: {
      evaluation: { role: 'evaluation', enabled: true, observation: { ...observation } },
      mail: { role: 'mail', enabled: false, observation: { ...observation, state: 'disabled' } },
      alert: { role: 'alert', enabled: true, observation: { ...observation } },
    },
    queues: {
      evaluation: {
        ...queue,
        status_counts: { queued: 0, running: 0, completed: 0, failed: 0, cancelled: 0 },
      },
      mail: {
        ...queue,
        enabled: false,
        status_counts: { queued: 0, sending: 0, sent: 3, failed: 0, cancelled: 0 },
      },
      alert: {
        ...queue,
        status_counts: { queued: 0, sending: 0, delivered: 2, failed: 0, cancelled: 0 },
      },
    },
    assessments_last_24h: { failed: 7, unknown: 4 },
  };
}

test('unknown observation ages never become measured zero', () => {
  for (const value of [null, undefined, NaN, -1]) assert.equal(elapsedSeconds(value), 'Unknown');
  assert.equal(elapsedSeconds(0), 'Less than a second');
  assert.equal(elapsedSeconds(150), '2m 30s');
  assert.equal(elapsedSeconds(5400), '1h 30m');
  assert.equal(pollLabel('missing'), 'No poll recorded');
  assert.equal(pollLabel('stale'), 'Poll observation is stale');
});

test('missing and stale polls retain uncertainty and explicit snapshot freshness', () => {
  const data = snapshot();
  data.workers.evaluation.observation = {
    state: 'stale',
    last_poll_at: '2026-09-21T11:50:00Z',
    age_seconds: 600,
    fresh_for_seconds: 150,
    error_code: null,
  };
  const html = renderToStaticMarkup(createElement(OperationsSnapshotView, { snapshot: data }));
  assert.match(html, /Poll observation is stale/);
  assert.match(html, /No poll recorded/);
  assert.match(html, /does not establish that the worker has stopped/);
  assert.match(html, /Poll age at snapshot<\/dt><dd>10m 0s/);
  assert.match(html, /Freshness threshold<\/dt><dd>2m 30s/);
  assert.match(html, /Snapshot observed <time dateTime="2026-09-21T12:00:00Z"/);
  assert.doesNotMatch(html, /Worker healthy|Worker dead|All systems operational/);
});

test('disabled queues retain records and delivery acknowledgements stay qualified', () => {
  const html = renderToStaticMarkup(
    createElement(OperationsSnapshotView, { snapshot: snapshot() }),
  );
  assert.match(html, /Queue disabled/);
  assert.match(html, /Existing records remain visible/);
  assert.match(html, /SMTP accepted<\/dt><dd>3/);
  assert.match(html, /Webhook accepted<\/dt><dd>2/);
  assert.match(html, /SMTP acceptance does not confirm inbox delivery/);
  assert.match(
    html,
    /Webhook acceptance does not confirm that a downstream notification reached a person/,
  );
  assert.match(html, /No eligible work/);
});

test('queue failures, inconclusive assessments, and failed assessments remain distinct', () => {
  const data = snapshot();
  data.queues.evaluation.status_counts.failed = 5;
  data.queues.evaluation.eligible_now = 9;
  data.queues.evaluation.expired_leases = 2;
  data.queues.evaluation.oldest_eligible_age_seconds = 5400;
  const html = renderToStaticMarkup(createElement(OperationsSnapshotView, { snapshot: data }));
  assert.match(html, /Failed jobs<\/dt><dd>5/);
  assert.match(html, /Failed assessments<\/dt><dd>7/);
  assert.match(html, /Unknown assessments<\/dt><dd>4/);
  assert.match(html, /Oldest eligible age<\/dt><dd>1h 30m/);
  assert.match(html, /Expired leases<\/dt><dd>2/);
  assert.match(html, /These values should not be added together/);
  assert.match(html, /up to \(excluding\)/);
});

test('observation dependency outage does not suppress database queue measurements', () => {
  const data = snapshot();
  data.dependencies.redis.state = 'unavailable';
  data.workers.evaluation.observation.state = 'unavailable';
  data.workers.evaluation.observation.error_code = 'observation_store_unavailable';
  const html = renderToStaticMarkup(createElement(OperationsSnapshotView, { snapshot: data }));
  assert.match(html, /Query succeeded/);
  assert.match(html, /Check Redis connectivity and refresh/);
  assert.match(html, /worker activity remains unknown/);
  assert.match(html, /Observation unavailable/);
  assert.match(html, /observation_store_unavailable/);
  assert.match(html, /SMTP accepted<\/dt><dd>3/);
});

test('loading operations renders no fabricated queue or assessment totals', () => {
  const html = renderToStaticMarkup(createElement(Operations));
  assert.match(html, /Loading operations snapshot/);
  assert.match(html, /disabled="">Refreshing/);
  assert.doesNotMatch(html, /Failed assessments|No eligible work|Query succeeded/);
});
