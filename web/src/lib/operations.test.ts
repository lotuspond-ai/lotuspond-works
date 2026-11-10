import assert from 'node:assert/strict';
import test from 'node:test';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { Operations, OperationsSnapshotView } from '../pages/Operations.js';
import { elapsedSeconds, pollLabel, type OperationsSnapshot } from './operations.js';

function snapshot(): OperationsSnapshot {
  return {
    observed_at: '2026-09-21T12:00:00Z',
    dependencies: { database: { state: 'ok' }, redis: { state: 'ok' } },
    workers: {
      mail: {
        role: 'mail',
        enabled: false,
        observation: {
          state: 'disabled',
          last_poll_at: null,
          age_seconds: null,
          fresh_for_seconds: 150,
          error_code: null,
        },
      },
    },
    queues: {
      mail: {
        enabled: false,
        eligible_now: 0,
        delayed: 0,
        suspended: 0,
        expired_leases: 0,
        oldest_eligible_age_seconds: null,
        status_counts: { queued: 0, sending: 0, sent: 3, failed: 0, cancelled: 0 },
      },
    },
  };
}

function enabled(data: OperationsSnapshot): OperationsSnapshot {
  data.workers.mail.enabled = true;
  data.queues.mail.enabled = true;
  data.workers.mail.observation.state = 'missing';
  return data;
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
  const missing = renderToStaticMarkup(
    createElement(OperationsSnapshotView, { snapshot: enabled(snapshot()) }),
  );
  assert.match(missing, /No poll recorded/);
  const data = enabled(snapshot());
  data.workers.mail.observation = {
    state: 'stale',
    last_poll_at: '2026-09-21T11:50:00Z',
    age_seconds: 600,
    fresh_for_seconds: 150,
    error_code: null,
  };
  const html = renderToStaticMarkup(createElement(OperationsSnapshotView, { snapshot: data }));
  assert.match(html, /Poll observation is stale/);
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
  assert.match(html, /SMTP acceptance does not confirm inbox delivery/);
  assert.match(html, /No eligible work/);
});

test('only the identity-mail queue is shown after the assurance workers were retired', () => {
  const html = renderToStaticMarkup(
    createElement(OperationsSnapshotView, { snapshot: snapshot() }),
  );
  assert.match(html, /Identity mail/);
  assert.doesNotMatch(html, /Evaluations|Incident alerts|Webhook accepted|assessments/i);
});

test('queue failures, leases and eligibility remain distinct', () => {
  const data = enabled(snapshot());
  data.queues.mail.status_counts.failed = 5;
  data.queues.mail.eligible_now = 9;
  data.queues.mail.expired_leases = 2;
  data.queues.mail.oldest_eligible_age_seconds = 5400;
  const html = renderToStaticMarkup(createElement(OperationsSnapshotView, { snapshot: data }));
  assert.match(html, /Failed messages<\/dt><dd>5/);
  assert.match(html, /Eligible now<\/dt><dd>9/);
  assert.match(html, /Oldest eligible age<\/dt><dd>1h 30m/);
  assert.match(html, /Expired leases<\/dt><dd>2/);
  assert.match(html, /These values should not be added together/);
});

test('observation dependency outage does not suppress database queue measurements', () => {
  const data = enabled(snapshot());
  data.dependencies.redis.state = 'unavailable';
  data.workers.mail.observation.state = 'unavailable';
  data.workers.mail.observation.error_code = 'observation_store_unavailable';
  const html = renderToStaticMarkup(createElement(OperationsSnapshotView, { snapshot: data }));
  assert.match(html, /Query succeeded/);
  assert.match(html, /Check Redis connectivity and refresh/);
  assert.match(html, /worker activity remains unknown/);
  assert.match(html, /Observation unavailable/);
  assert.match(html, /observation_store_unavailable/);
  assert.match(html, /SMTP accepted<\/dt><dd>3/);
});

test('loading operations renders no fabricated queue totals', () => {
  const html = renderToStaticMarkup(createElement(Operations));
  assert.match(html, /Loading operations snapshot/);
  assert.match(html, /disabled="">Refreshing/);
  assert.doesNotMatch(html, /No eligible work|Query succeeded/);
});
