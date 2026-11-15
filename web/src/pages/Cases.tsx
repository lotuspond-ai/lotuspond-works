import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react';
import { api, ApiError } from '../api.js';
import { EnvironmentScope } from '../components/operate/EnvironmentScope.js';
import { Alert, EmptyState, RelativeTime, SectionHeader } from '../components/ui.js';
import {
  caseOrder,
  fromMs,
  opaqueId,
  operateMessage,
  outcomeOf,
  resolutionWords,
  scopedPath,
  type BusinessCase,
  type CaseResolution,
  type Scope,
} from '../lib/operate.js';

function problem(reason: unknown) {
  return reason instanceof ApiError
    ? operateMessage(reason.code, reason.message)
    : 'The request could not be completed. Please try again.';
}

const short = (id: string) => `…${id.slice(-8)}`;

function OutcomeBadge({ state }: { state: string }) {
  if (state === 'empty')
    return (
      <span className="badge badge-neutral">
        <span aria-hidden="true" />
        No runs yet
      </span>
    );
  const words = outcomeOf(state);
  return (
    <span className={`badge badge-${words.tone}`}>
      <span aria-hidden="true" />
      {words.label}
    </span>
  );
}

// Cases (docs/superpowers/specs/2026-11-15-cases-design.md): the business items of one
// environment, the runs that handled each, and who is dealing with the ones that need a person.
export function Cases({
  workspace,
  projectId,
  role,
}: {
  workspace: string;
  projectId: string;
  role: string;
}) {
  return (
    <div className="operate">
      <EnvironmentScope workspace={workspace} projectId={projectId}>
        {(scope, picker) => (
          <CaseList
            key={scope.environmentId}
            workspace={workspace}
            scope={scope}
            picker={picker}
            canEdit={role !== 'viewer'}
          />
        )}
      </EnvironmentScope>
    </div>
  );
}

function CaseList({
  workspace,
  scope,
  picker,
  canEdit,
}: {
  workspace: string;
  scope: Scope;
  picker: React.ReactNode;
  canEdit: boolean;
}) {
  const [cases, setCases] = useState<BusinessCase[] | null>(null);
  const [next, setNext] = useState<string | null>(null);
  const [error, setError] = useState('');
  const [openId, setOpenId] = useState('');
  const [adding, setAdding] = useState(false);
  const load = useCallback(
    async (after?: string) => {
      setError('');
      try {
        const query = after ? `?${new URLSearchParams({ after_id: after })}` : '';
        const data = await api<{ items: BusinessCase[]; next_after_id: string | null }>(
          scopedPath(scope, `cases${query}`),
          workspace,
        );
        setCases((current) => (after && current ? [...current, ...data.items] : data.items));
        setNext(data.next_after_id);
      } catch (reason) {
        setError(problem(reason));
        if (!after) setCases([]);
      }
    },
    [scope, workspace],
  );
  useEffect(() => {
    void load();
  }, [load]);
  async function add(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const key = String(new FormData(event.currentTarget).get('key') ?? '').trim();
    if (!key) return;
    setError('');
    try {
      const created = await api<BusinessCase>(scopedPath(scope, 'cases'), workspace, { key });
      setAdding(false);
      await load();
      setOpenId(created.id);
    } catch (reason) {
      setError(problem(reason));
    }
  }
  const ordered = cases ? caseOrder(cases) : [];
  const opened = ordered.find((item) => item.id === openId);
  const unavailable = error === operateMessage('authority_unavailable', '');
  return (
    <>
      <SectionHeader
        eyebrow="Operate"
        title="Cases"
        description="Each business item, such as an invoice or a purchase order, with the runs that handled it and the person dealing with it when it needs one."
        actions={
          <>
            {picker}
            {canEdit && !unavailable && (
              <button className="secondary" onClick={() => setAdding(!adding)}>
                Add case
              </button>
            )}
          </>
        }
      />
      {adding && (
        <form className="record-form operate-inline-form" onSubmit={(event) => void add(event)}>
          <label>
            Business key
            <input name="key" maxLength={200} required placeholder="For example PO-1042" />
          </label>
          <p className="hint">
            The key is stored encrypted, like the values a workflow handles. Adding an existing key
            opens that case.
          </p>
          <button type="submit">Open case</button>
        </form>
      )}
      {error &&
        (unavailable ? (
          <div className="notice operate-unavailable" role="status">
            <p>{error}</p>
            <button className="secondary" onClick={() => void load()}>
              Try again
            </button>
          </div>
        ) : (
          <Alert onRetry={() => void load()}>{error}</Alert>
        ))}
      {cases === null ? (
        <p className="loading-state" role="status">
          Loading cases…
        </p>
      ) : !ordered.length ? (
        !error && (
          <EmptyState
            glyph="enclosure"
            title="No cases yet"
            description="A case is opened for a business item, and each run that handles it is linked to it."
          />
        )
      ) : (
        <div className="operate-split">
          <section aria-labelledby="cases-heading" className="operate-list">
            <h2 id="cases-heading" className="sr-only">
              Cases
            </h2>
            <ul>
              {ordered.map((item) => (
                <li key={item.id}>
                  <button
                    className={`operate-row ${item.id === openId ? 'selected' : ''}`}
                    aria-current={item.id === openId ? 'true' : undefined}
                    onClick={() => setOpenId(item.id)}
                  >
                    <span className="operate-row-title">
                      {item.key ?? `Case ${short(item.id)}`}
                    </span>
                    <span className="operate-row-meta">
                      <OutcomeBadge state={item.outcome} />
                      {item.needs_person && (
                        <strong>
                          {item.owner
                            ? `Needs a person · owner: ${item.owner.name ?? 'someone'}`
                            : 'Needs a person'}
                        </strong>
                      )}
                      <span>
                        {item.execution_count} run{item.execution_count === 1 ? '' : 's'}
                      </span>
                    </span>
                  </button>
                </li>
              ))}
            </ul>
            {next && (
              <button className="secondary" onClick={() => void load(next)}>
                Load more
              </button>
            )}
          </section>
          {opened ? (
            <CaseDetail
              key={opened.id}
              workspace={workspace}
              scope={scope}
              listed={opened}
              canEdit={canEdit}
              onChanged={(updated) =>
                setCases((current) =>
                  current ? current.map((item) => (item.id === updated.id ? updated : item)) : null,
                )
              }
            />
          ) : (
            <div className="operate-detail operate-placeholder">
              <p>Choose a case to see its runs and who is dealing with it.</p>
            </div>
          )}
        </div>
      )}
    </>
  );
}

function CaseDetail({
  workspace,
  scope,
  listed,
  canEdit,
  onChanged,
}: {
  workspace: string;
  scope: Scope;
  listed: BusinessCase;
  canEdit: boolean;
  onChanged: (item: BusinessCase) => void;
}) {
  const [item, setItem] = useState<BusinessCase>(listed);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [kind, setKind] = useState<CaseResolution>('corrected_by_hand');
  const pending = useRef<{ key: string; id: string } | null>(null);
  const heading = useRef<HTMLHeadingElement>(null);
  const base = `cases/${listed.id}`;
  const load = useCallback(async () => {
    setError('');
    try {
      setItem(await api<BusinessCase>(scopedPath(scope, base), workspace));
    } catch (reason) {
      setError(problem(reason));
    }
  }, [base, scope, workspace]);
  useEffect(() => {
    heading.current?.focus();
    void load();
  }, [load]);
  async function post(path: string, body: Record<string, unknown>, intent?: string) {
    setBusy(true);
    setError('');
    if (intent && pending.current?.key !== intent)
      pending.current = { key: intent, id: opaqueId('case') };
    try {
      const updated = await api<BusinessCase>(
        scopedPath(scope, `${base}/${path}`),
        workspace,
        intent ? { ...body, request_id: pending.current!.id } : body,
      );
      pending.current = null;
      setItem(updated);
      onChanged(updated);
    } catch (reason) {
      setError(problem(reason));
      if (reason instanceof ApiError && reason.code === 'conflict') {
        pending.current = null;
        void load();
      }
    } finally {
      setBusy(false);
    }
  }
  function link(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const execution = String(new FormData(form).get('execution_id') ?? '').trim();
    if (execution) void post('executions', { execution_id: execution }).then(() => form.reset());
  }
  return (
    <section className="operate-detail" aria-labelledby="case-heading">
      <h2 id="case-heading" ref={heading} tabIndex={-1}>
        {item.key ?? `Case ${short(item.id)}`}
      </h2>
      <div
        className={`outcome outcome-${item.outcome === 'empty' ? 'neutral' : outcomeOf(item.outcome).tone}`}
      >
        <p className="outcome-line">
          <OutcomeBadge state={item.outcome} />
          {item.needs_person && <strong>Needs a person</strong>}
        </p>
        <p>
          {item.outcome === 'empty'
            ? 'No run has been linked to this case yet.'
            : `Its latest run: ${outcomeOf(item.outcome).text}`}
        </p>
        {item.resolution && (
          <p className="hint">
            Last resolved: {resolutionWords[item.resolution.kind]}, by{' '}
            {item.resolution.resolved_by?.name ?? 'a person'}{' '}
            <RelativeTime value={fromMs(item.resolution.resolved_at_ms)} />.
          </p>
        )}
      </div>

      <h3>Who is dealing with it</h3>
      <p>
        {item.owner ? (
          <>
            {item.owner.name ?? 'Someone'}, since{' '}
            <RelativeTime value={fromMs(item.owner.since_ms)} />
          </>
        ) : (
          'Nobody yet.'
        )}
      </p>
      {error && <Alert>{error}</Alert>}
      {canEdit && (
        <div className="operate-actions">
          {!item.owner && (
            <button
              disabled={busy}
              className={item.needs_person ? undefined : 'secondary'}
              onClick={() => void post('owner', { action: 'take' }, 'take')}
            >
              Take this case
            </button>
          )}
          {item.owner && (
            <button
              className="text-button"
              disabled={busy}
              onClick={() => void post('owner', { action: 'release' }, 'release')}
            >
              Release
            </button>
          )}
        </div>
      )}
      {canEdit && item.owner && item.needs_person && (
        <div className="operate-resolve">
          <label>
            How it was dealt with
            <select
              value={kind}
              onChange={(event) => setKind(event.target.value as CaseResolution)}
            >
              {Object.entries(resolutionWords).map(([value, words]) => (
                <option key={value} value={value}>
                  {words}
                </option>
              ))}
            </select>
          </label>
          <button
            className="decide"
            disabled={busy}
            onClick={() => void post('resolution', { kind }, `resolve:${kind}`)}
          >
            Mark resolved
          </button>
          <p className="hint">
            Only the person dealing with the case can resolve it. Resolving records the decision; it
            changes no run and sends nothing.
          </p>
        </div>
      )}

      <h3>Runs</h3>
      {!item.executions ? (
        <p className="hint">Loading…</p>
      ) : !item.executions.length ? (
        <p className="hint">None yet.</p>
      ) : (
        <ul className="operate-facts">
          {item.executions.map((run) => (
            <li key={run.id}>
              <span className="cell-mono" title={run.id}>
                {short(run.id)}
              </span>
              <OutcomeBadge state={run.outcome} />
              <span>
                admitted <RelativeTime value={fromMs(run.admitted_at_ms)} />
              </span>
            </li>
          ))}
        </ul>
      )}
      <p className="hint">
        Each run's writes, and who allowed them, are in{' '}
        <a className="inline-link" href="#control-room">
          Control Room
        </a>
        .
      </p>
      {canEdit && (
        <form className="operate-inline-form" onSubmit={link}>
          <label>
            Link a run by its execution ID
            <input name="execution_id" required pattern="[0-9a-f-]{36}" />
          </label>
          <button className="secondary" type="submit" disabled={busy}>
            Link run
          </button>
        </form>
      )}
    </section>
  );
}
