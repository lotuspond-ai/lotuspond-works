import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react';
import { api, ApiError } from '../../api.js';
import {
  enrollCommand,
  fromMs,
  opaqueId,
  operateMessage,
  scopedPath,
  seenRecently,
  type Scope,
} from '../../lib/operate.js';
import { Alert, RelativeTime, StatusBadge } from '../ui.js';

type Pool = { id: string; name: string; status: string; current_version: number | null };
type Enrollment = {
  id: string;
  pool_id: string;
  status: string;
  expires_at_ms: number;
  consumed_at_ms: number | null;
};
type Instance = {
  id: string;
  pool_id: string;
  status: string;
  generation: number;
  current_policy_version: number | null;
  last_seen_at_ms: number | null;
};
type Issued = { enrollment_id: string; token: string; expires_at_ms: number };

const short = (id: string) => `…${id.slice(-8)}`;

function problem(reason: unknown) {
  return reason instanceof ApiError
    ? operateMessage(reason.code, reason.message)
    : 'The request could not be completed. Please try again.';
}

// Workers: runtime pools, one-time enrollment tokens and enrolled runtime instances
// (docs/superpowers/specs/2026-11-16-control-room-operations-design.md). Owners and admins.
export function Workers({
  workspace,
  scope,
  canManage,
}: {
  workspace: string;
  scope: Scope;
  canManage: boolean;
}) {
  const [pools, setPools] = useState<Pool[] | null>(null);
  const [instances, setInstances] = useState<Instance[] | null>(null);
  const [enrollments, setEnrollments] = useState<Record<string, Enrollment[]>>({});
  const [issued, setIssued] = useState<Issued | null>(null);
  const [confirming, setConfirming] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const keys = useRef<Record<string, string>>({});

  const load = useCallback(async () => {
    setError('');
    try {
      const [poolPage, instancePage] = await Promise.all([
        api<{ items: Pool[] }>(scopedPath(scope, 'runtime-pools?limit=100'), workspace),
        api<{ items: Instance[] }>(scopedPath(scope, 'runtime-instances?limit=100'), workspace),
      ]);
      setPools(poolPage.items);
      setInstances(instancePage.items);
      const lists = await Promise.all(
        poolPage.items.map((pool) =>
          api<{ items: Enrollment[] }>(
            scopedPath(scope, `runtime-pools/${pool.id}/enrollments?limit=100`),
            workspace,
          ).then((page) => [pool.id, page.items] as const),
        ),
      );
      setEnrollments(Object.fromEntries(lists));
    } catch (reason) {
      setError(problem(reason));
      setPools((current) => current ?? []);
      setInstances((current) => current ?? []);
    }
  }, [scope, workspace]);
  useEffect(() => {
    if (canManage) void load();
  }, [canManage, load]);

  // One idempotency key per intent, reused if the same request is retried.
  async function mutate<T>(intent: string, path: string, body: unknown) {
    keys.current[intent] ??= opaqueId('control-room');
    setBusy(true);
    setError('');
    try {
      const answer = await api<T>(scopedPath(scope, path), workspace, body, 'POST', undefined, {
        'idempotency-key': keys.current[intent]!,
      });
      delete keys.current[intent];
      await load();
      return answer;
    } catch (reason) {
      setError(problem(reason));
      return null;
    } finally {
      setBusy(false);
    }
  }

  if (!canManage)
    return (
      <p className="notice">
        Workers are managed by workspace owners and administrators. Ask one to enroll a runtime or
        revoke one.
      </p>
    );

  async function createPool(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const name = String(new FormData(form).get('name') ?? '').trim();
    if (name && (await mutate(`pool:${name}`, 'runtime-pools', { name }))) form.reset();
  }

  return (
    <div className="workers">
      {error && <Alert onRetry={() => void load()}>{error}</Alert>}
      {issued && (
        <section className="notice worker-token" role="status" aria-labelledby="token-heading">
          <h2 id="token-heading">Enrollment token</h2>
          <p>
            Shown once. Save it to a private file on the runtime host (owner-only), then run the
            command below there. It expires <RelativeTime value={fromMs(issued.expires_at_ms)} />{' '}
            and works once.
          </p>
          <pre className="studio-v2-command">{issued.token}</pre>
          <pre className="studio-v2-command">{enrollCommand()}</pre>
          <button className="secondary" onClick={() => setIssued(null)}>
            I have saved it
          </button>
        </section>
      )}

      <section className="panel" aria-labelledby="pools-heading">
        <div className="panel-head">
          <h2 id="pools-heading">Runtime pools</h2>
          <button className="secondary" onClick={() => void load()}>
            Refresh
          </button>
        </div>
        <p className="hint">
          A pool groups the runtimes that run this environment's work inside your boundary. Its
          policy (connectors, connections, limits) is set through the API.
        </p>
        {pools === null ? (
          <p className="loading-state" role="status">
            Loading pools…
          </p>
        ) : !pools.length ? (
          <p className="hint">No pool yet. Create one to enroll a runtime.</p>
        ) : (
          <ul className="operate-trace">
            {pools.map((pool) => {
              const tokens = enrollments[pool.id] ?? [];
              return (
                <li key={pool.id}>
                  <p className="operate-trace-title">
                    <strong>{pool.name}</strong> <StatusBadge value={pool.status} />{' '}
                    <span className="hint">
                      policy {pool.current_version ? `v${pool.current_version}` : 'not published'}
                    </span>
                  </p>
                  <div className="operate-actions">
                    <button
                      disabled={busy || pool.status !== 'active' || !pool.current_version}
                      onClick={async () => {
                        const answer = await mutate<Issued>(
                          `enroll:${pool.id}`,
                          `runtime-pools/${pool.id}/enrollments`,
                          { platform: 'linux', connectors: [] },
                        );
                        if (answer) setIssued(answer);
                      }}
                    >
                      Issue enrollment token
                    </button>
                    <button
                      className="secondary"
                      disabled={busy}
                      onClick={() =>
                        void mutate(
                          `pool-status:${pool.id}:${pool.status}`,
                          `runtime-pools/${pool.id}/status`,
                          { status: pool.status === 'active' ? 'disabled' : 'active' },
                        )
                      }
                    >
                      {pool.status === 'active' ? 'Disable' : 'Enable'}
                    </button>
                  </div>
                  {pool.status === 'active' && !pool.current_version && (
                    <p className="hint">Publish a pool policy before enrolling runtimes.</p>
                  )}
                  {tokens.length > 0 && (
                    <ul className="operate-facts">
                      {tokens.map((token) => (
                        <li key={token.id}>
                          <span className="cell-mono">{short(token.id)}</span>
                          <StatusBadge value={token.status} />
                          {token.status === 'issued' ? (
                            <>
                              <span>
                                expires <RelativeTime value={fromMs(token.expires_at_ms)} />
                              </span>
                              <button
                                className="text-button danger"
                                disabled={busy}
                                onClick={() =>
                                  void mutate(
                                    `revoke-token:${token.id}`,
                                    `runtime-pools/${pool.id}/enrollments/${token.id}/revoke`,
                                    {},
                                  )
                                }
                              >
                                Revoke
                              </button>
                            </>
                          ) : token.consumed_at_ms ? (
                            <span>
                              used <RelativeTime value={fromMs(token.consumed_at_ms)} />
                            </span>
                          ) : null}
                        </li>
                      ))}
                    </ul>
                  )}
                </li>
              );
            })}
          </ul>
        )}
        <form className="operate-inline-form" onSubmit={(event) => void createPool(event)}>
          <label>
            New pool
            <input name="name" required maxLength={128} placeholder="For example plant-a-linux" />
          </label>
          <button className="secondary" type="submit" disabled={busy}>
            Create pool
          </button>
        </form>
      </section>

      <section className="panel" aria-labelledby="instances-heading">
        <h2 id="instances-heading">Enrolled runtimes</h2>
        {instances === null ? (
          <p className="loading-state" role="status">
            Loading runtimes…
          </p>
        ) : !instances.length ? (
          <p className="hint">
            No runtime has enrolled yet. Issue a token from a pool and run the enroll command on the
            host.
          </p>
        ) : (
          <div className="table-wrap">
            <table>
              <caption className="sr-only">Enrolled runtimes</caption>
              <thead>
                <tr>
                  <th scope="col">Runtime</th>
                  <th scope="col">Pool</th>
                  <th scope="col">Status</th>
                  <th scope="col">Last seen</th>
                  <th scope="col">
                    <span className="sr-only">Actions</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {instances.map((instance) => {
                  const fresh = seenRecently(instance.last_seen_at_ms);
                  return (
                    <tr key={instance.id}>
                      <td className="primary-cell cell-mono" title={instance.id}>
                        {short(instance.id)}
                      </td>
                      <td>
                        {pools?.find((pool) => pool.id === instance.pool_id)?.name ??
                          short(instance.pool_id)}
                      </td>
                      <td>
                        <StatusBadge value={instance.status} />
                      </td>
                      <td>
                        {instance.last_seen_at_ms ? (
                          <>
                            <RelativeTime value={fromMs(instance.last_seen_at_ms)} />
                            {!fresh && instance.status === 'active' && (
                              <span className="hint"> · not seen recently</span>
                            )}
                          </>
                        ) : (
                          'Never'
                        )}
                      </td>
                      <td>
                        {instance.status === 'active' &&
                          (confirming === instance.id ? (
                            <span className="operate-confirm">
                              <span>Revoke? It stops at its next session; sent work stays.</span>
                              <button
                                className="secondary danger"
                                disabled={busy}
                                onClick={() => {
                                  setConfirming('');
                                  void mutate(
                                    `revoke-instance:${instance.id}`,
                                    `runtime-instances/${instance.id}/revoke`,
                                    {},
                                  );
                                }}
                              >
                                Yes, revoke
                              </button>
                              <button className="text-button" onClick={() => setConfirming('')}>
                                Keep
                              </button>
                            </span>
                          ) : (
                            <button
                              className="text-button danger"
                              onClick={() => setConfirming(instance.id)}
                            >
                              Revoke…
                            </button>
                          ))}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}
