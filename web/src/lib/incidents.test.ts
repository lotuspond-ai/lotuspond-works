import assert from 'node:assert/strict';
import test from 'node:test';
import { alertDelivery, incidentCategory, incidentSource } from './incidents.js';

test('an unavailable evaluator remains distinct from failed agent behavior', () => {
  assert.equal(incidentCategory('assessment_failed').label, 'Assessment failed');
  const unavailable = incidentCategory('assessment_unavailable');
  assert.equal(unavailable.label, 'Evaluation unavailable');
  assert.match(unavailable.explanation, /does not establish that the agent failed or passed/);
  assert.equal(incidentCategory('manual').label, 'Manual report');
  assert.equal(incidentCategory(undefined).label, 'Category not recorded');
  assert.equal(incidentSource('monitor'), 'Production monitor');
});
test('webhook HTTP acceptance is never described as downstream notification delivery', () => {
  assert.equal(alertDelivery('delivered'), 'Webhook accepted');
  assert.equal(alertDelivery('queued'), 'Queued');
  assert.equal(alertDelivery('sending'), 'Sending');
  assert.equal(alertDelivery('failed'), 'Delivery failed');
  assert.equal(alertDelivery(undefined), 'Status not recorded');
});
