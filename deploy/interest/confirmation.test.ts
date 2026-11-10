import assert from 'node:assert/strict';
import test from 'node:test';

import { ConfirmationEmail } from './confirmation.ts';
import { confirmationHtml, confirmationText } from './email-template.ts';

function harness(sendError?: Error) {
  const values = new Map<string, unknown>();
  const receipts = new Map<string, string>();
  let alarmAt: number | null = null;
  let sends = 0;
  let failAccepted = false;
  let lock: Promise<unknown> = Promise.resolve();
  const store = {
    get: async <T>(key: string) => structuredClone(values.get(key)) as T | undefined,
    put: async (key: string, value: unknown) => {
      if (failAccepted && (value as { status: string }).status === 'accepted')
        throw new Error('storage failed');
      values.set(key, structuredClone(value));
    },
    getAlarm: async () => alarmAt,
    setAlarm: async (at: number) => {
      alarmAt = at;
    },
    transaction: async <T>(callback: (s: typeof store) => Promise<T>) => await callback(store),
  };
  const object = new ConfirmationEmail(
    {
      storage: store,
      blockConcurrencyWhile: <T>(callback: () => Promise<T>) => {
        const next = lock.then(callback);
        lock = next.catch(() => undefined);
        return next;
      },
    },
    {
      EMAIL: {
        send: async (message) => {
          sends++;
          assert.equal(message.from.email, 'hello@lotuspond.ai');
          assert.equal(message.to, 'local@example.com');
          assert.match(message.html, /Your interest in Lotuspond is registered/);
          if (sendError) throw sendError;
          return { messageId: 'synthetic-message-id' };
        },
      },
      INTEREST: {
        put: async (key, value) => {
          receipts.set(key, value);
        },
      },
    },
  );
  const enqueue = () =>
    object.fetch(
      new Request('https://confirmation.internal/register', {
        method: 'POST',
        body: JSON.stringify({ email: 'local@example.com', key: 'a'.repeat(64) }),
      }),
    );
  return {
    object,
    values,
    receipts,
    enqueue,
    get sends() {
      return sends;
    },
    get alarmAt() {
      return alarmAt;
    },
    failAfterSend: () => {
      failAccepted = true;
    },
  };
}

test('repeated and concurrent registrations queue one confirmation and alarm replay does not resend', async () => {
  const h = harness();
  const results = await Promise.all(Array.from({ length: 10 }, h.enqueue));
  assert.ok(results.every((response) => response.status === 202));
  assert.ok(h.alarmAt);
  await h.object.alarm();
  await h.enqueue();
  await h.object.alarm();
  assert.equal(h.sends, 1);
  const job = h.values.get('job') as { status: string; email?: string };
  assert.equal(job.status, 'accepted');
  assert.equal(job.email, undefined);
  assert.match([...h.receipts.values()][0], /synthetic-message-id/);
});
test('explicit provider throttling is retried with bounded attempts', async () => {
  const h = harness(Object.assign(new Error('throttled'), { code: 'E_RATE_LIMIT_EXCEEDED' }));
  await h.enqueue();
  await h.object.alarm();
  assert.equal((h.values.get('job') as { status: string }).status, 'pending');
  assert.ok(h.alarmAt! >= Date.now() + 59000);
  for (let i = 0; i < 7; i++) await h.object.alarm();
  assert.equal(h.sends, 6);
  assert.equal((h.values.get('job') as { status: string }).status, 'failed');
});
test('unknown delivery outcomes and interrupted sends are not replayed', async () => {
  const h = harness(new Error('connection disappeared'));
  await h.enqueue();
  await h.object.alarm();
  await h.enqueue();
  await h.object.alarm();
  assert.equal(h.sends, 1);
  assert.equal((h.values.get('job') as { status: string }).status, 'unknown');
  const interrupted = harness();
  interrupted.values.set('job', {
    email: 'local@example.com',
    key: 'a'.repeat(64),
    status: 'sending',
    attempts: 1,
  });
  await interrupted.object.alarm();
  assert.equal(interrupted.sends, 0);
  assert.equal((interrupted.values.get('job') as { status: string }).status, 'unknown');
});
test('failure to record provider acceptance cannot cause a duplicate send', async () => {
  const h = harness();
  await h.enqueue();
  h.failAfterSend();
  await assert.rejects(h.object.alarm(), /storage failed/);
  await h.object.alarm();
  assert.equal(h.sends, 1);
  assert.equal((h.values.get('job') as { status: string }).status, 'unknown');
});
test('suppressed recipients are not retried and templates reveal no product category', async () => {
  const h = harness(Object.assign(new Error('suppressed'), { code: 'E_RECIPIENT_SUPPRESSED' }));
  await h.enqueue();
  await h.object.alarm();
  await h.object.alarm();
  assert.equal(h.sends, 1);
  assert.equal((h.values.get('job') as { status: string }).status, 'failed');
  assert.doesNotMatch(confirmationHtml + confirmationText, /enterprise|automation|\bagent\b/i);
});
