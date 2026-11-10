import { confirmationHtml, confirmationSubject, confirmationText } from './email-template';

interface Job {
  email?: string;
  key: string;
  status: 'pending' | 'sending' | 'accepted' | 'failed' | 'unknown';
  attempts: number;
  messageId?: string;
  updatedAt: string;
}
interface Store {
  get<T>(key: string): Promise<T | undefined>;
  put(key: string, value: unknown): Promise<void>;
  getAlarm(): Promise<number | null>;
  setAlarm(at: number): Promise<void>;
  transaction<T>(callback: (store: Store) => Promise<T>): Promise<T>;
}
interface State {
  storage: Store;
  blockConcurrencyWhile<T>(callback: () => Promise<T>): Promise<T>;
}
interface ConfirmationEnv {
  EMAIL: {
    send(message: {
      from: { email: string; name: string };
      to: string;
      subject: string;
      html: string;
      text: string;
    }): Promise<{ messageId: string }>;
  };
  INTEREST: { put(key: string, value: string): Promise<void> };
}

// One Durable Object per normalized email hash serializes all registration retries.
// Provider acceptance is not proof of inbox delivery. Unknown sends are not replayed.
export class ConfirmationEmail {
  constructor(
    private readonly ctx: State,
    private readonly env: ConfirmationEnv,
  ) {}

  async fetch(request: Request): Promise<Response> {
    if (request.method !== 'POST') return new Response(null, { status: 405 });
    const input = (await request.json()) as { email?: unknown; key?: unknown };
    if (
      typeof input.email !== 'string' ||
      input.email.length > 254 ||
      typeof input.key !== 'string' ||
      !/^[a-f0-9]{64}$/.test(input.key)
    ) {
      return new Response(null, { status: 400 });
    }
    const email = input.email;
    const key = input.key;
    const status = await this.ctx.blockConcurrencyWhile(async () => {
      return await this.ctx.storage.transaction(async (store) => {
        const existing = await store.get<Job>('job');
        if (existing) {
          if (existing.status === 'pending' && (await store.getAlarm()) === null) {
            await store.setAlarm(Date.now() + 1000);
          }
          return existing.status;
        }
        await store.put('job', {
          email,
          key,
          status: 'pending',
          attempts: 0,
          updatedAt: new Date().toISOString(),
        } satisfies Job);
        await store.setAlarm(Date.now() + 1000);
        return 'pending';
      });
    });
    return Response.json({ status }, { status: 202 });
  }

  private async record(job: Job) {
    await this.ctx.storage.put('job', job);
    // Optional dashboard evidence. Its failure must never repeat an accepted email.
    try {
      await this.env.INTEREST.put(
        `confirmation:${job.key}`,
        JSON.stringify({
          status: job.status,
          attempts: job.attempts,
          messageId: job.messageId,
          updatedAt: job.updatedAt,
          template: 'early-access-confirmation-v1',
        }),
      );
    } catch {
      /* Durable Object state remains authoritative. */
    }
  }

  async alarm(): Promise<void> {
    let job = await this.ctx.storage.get<Job>('job');
    if (!job || ['accepted', 'failed', 'unknown'].includes(job.status)) return;
    // An interrupted attempt may already have reached the provider. Do not resend.
    if (job.status === 'sending') {
      await this.record({
        ...job,
        email: undefined,
        status: 'unknown',
        updatedAt: new Date().toISOString(),
      });
      return;
    }
    if (!job.email) throw new Error('Missing pending recipient');
    job = {
      ...job,
      status: 'sending',
      attempts: job.attempts + 1,
      updatedAt: new Date().toISOString(),
    };
    // Persist the attempt before crossing the sending boundary.
    await this.ctx.storage.put('job', job);
    let result: { messageId: string };
    try {
      result = await this.env.EMAIL.send({
        from: { email: 'hello@lotuspond.ai', name: 'Lotuspond' },
        to: job.email!,
        subject: confirmationSubject,
        html: confirmationHtml,
        text: confirmationText,
      });
    } catch (error) {
      const code = error && typeof error === 'object' && 'code' in error ? String(error.code) : '';
      const retryable = [
        'E_RATE_LIMIT_EXCEEDED',
        'E_DAILY_LIMIT_EXCEEDED',
        'E_SENDER_NOT_VERIFIED',
        'E_SENDER_DOMAIN_NOT_AVAILABLE',
      ].includes(code);
      if (retryable && job.attempts < 6) {
        const next = { ...job, status: 'pending' as const, updatedAt: new Date().toISOString() };
        await this.ctx.storage.transaction(async (store) => {
          await store.put('job', next);
          await store.setAlarm(Date.now() + Math.min(3600000, 60000 * 4 ** (job!.attempts - 1)));
        });
        return;
      }
      const rejected =
        retryable ||
        [
          'E_VALIDATION_ERROR',
          'E_RECIPIENT_NOT_ALLOWED',
          'E_RECIPIENT_SUPPRESSED',
          'E_ACCOUNT_SUSPENDED',
          'E_CONTENT_TOO_LARGE',
        ].includes(code);
      await this.record({
        ...job,
        email: undefined,
        status: rejected ? 'failed' : 'unknown',
        updatedAt: new Date().toISOString(),
      });
      return;
    }
    // Outside the send catch: if persistence fails, alarm replay sees 'sending'.
    await this.record({
      ...job,
      email: undefined,
      status: 'accepted',
      messageId: result.messageId,
      updatedAt: new Date().toISOString(),
    });
  }
}
