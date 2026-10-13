import assert from 'node:assert/strict';
import test from 'node:test';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { Enrollment, EnrollmentForm, type EnrollmentDetails } from '../pages/Enrollment.js';
import { createIdentityLinks } from './identityLinks.js';

const details: EnrollmentDetails = {
  email: 'synthetic-enrollment@example.com',
  name: 'Synthetic enrollment holder',
  expires_at: '2026-09-21T20:00:00Z',
  organization: null,
  projects: [],
  identity_verification_source: 'operator_attested',
};

test('operator enrollment details are read-only and do not claim mailbox verification', () => {
  const html = renderToStaticMarkup(
    createElement(EnrollmentForm, { details, busy: false, onSubmit: () => {} }),
  );
  assert.match(html, /Operator attested/);
  assert.match(html, /does not verify ownership of your mailbox/);
  assert.match(html, /No organization will be created by this form/);
  assert.match(html, /type="password"/);
  assert.match(html, /name="confirmation"/);
  assert.doesNotMatch(html, /name="email"|name="name"|name="token"|name="organization"/);
});

test('only explicitly provisioned organization details appear during enrollment', () => {
  const html = renderToStaticMarkup(
    createElement(EnrollmentForm, {
      details: {
        ...details,
        organization: { id: 'synthetic-org', name: 'Synthetic provisioned organization' },
        projects: [{ id: 'synthetic-project', name: 'Synthetic provisioned project' }],
      },
      busy: true,
      onSubmit: () => {},
    }),
  );
  assert.match(html, /Synthetic provisioned organization/);
  assert.match(html, /Synthetic provisioned project/);
  assert.match(html, /<button disabled="">Activating account/);
  assert.doesNotMatch(html, /Create organization|name="organization"/);
});

test('missing enrollment token cannot expose an activation form', () => {
  const html = renderToStaticMarkup(
    createElement(Enrollment, {
      links: createIdentityLinks(),
      account: null,
      onRefresh: async () => {},
      onCompleted: async () => {},
    }),
  );
  assert.match(html, /Reopen the complete link supplied by your operator/);
  assert.doesNotMatch(html, /<form|name="password"/);
});
