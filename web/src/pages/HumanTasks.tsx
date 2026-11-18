import { useCallback, useEffect, useRef, useState } from 'react';
import { api, ApiError } from '../api.js';
import { EnvironmentScope } from '../components/operate/EnvironmentScope.js';
import {
  Alert,
  EmptyState,
  ObjectView,
  RelativeTime,
  SectionHeader,
  StatusBadge,
} from '../components/ui.js';
import {
  decisionMessage,
  formFields,
  formPayload,
  fromMs,
  inboxOrder,
  opaqueId,
  operateMessage,
  scopedPath,
  taskActions,
  type FormField,
  type FormValues,
  type HumanTask,
  type Proposal,
  type TaskForm,
  type Scope,
} from '../lib/operate.js';

function problem(reason: unknown) {
  return reason instanceof ApiError
    ? operateMessage(reason.code, reason.message)
    : 'The request could not be completed. Please try again.';
}

// The review inbox (docs/superpowers/specs/2026-11-15-review-inbox-control-room-design.md):
// open tasks of one environment, each opened to the exact action a person is asked to allow.
export function HumanTasks({ workspace, projectId }: { workspace: string; projectId: string }) {
  return (
    <div className="operate">
      <EnvironmentScope workspace={workspace} projectId={projectId}>
        {(scope, picker) => (
          <Inbox key={scope.environmentId} workspace={workspace} scope={scope} picker={picker} />
        )}
      </EnvironmentScope>
    </div>
  );
}

function Inbox({
  workspace,
  scope,
  picker,
}: {
  workspace: string;
  scope: Scope;
  picker: React.ReactNode;
}) {
  const [tasks, setTasks] = useState<HumanTask[] | null>(null);
  const [next, setNext] = useState<string | null>(null);
  const [error, setError] = useState('');
  const [openId, setOpenId] = useState('');
  // What the last closed task's decision did; the task itself has left the inbox.
  const [closed, setClosed] = useState('');
  const load = useCallback(
    async (after?: string) => {
      setError('');
      try {
        const query = new URLSearchParams(after ? { after_id: after } : {});
        const data = await api<{ items: HumanTask[]; next_after_id: string | null }>(
          scopedPath(scope, `human-tasks${after ? `?${query}` : ''}`),
          workspace,
        );
        setTasks((current) => (after && current ? [...current, ...data.items] : data.items));
        setNext(data.next_after_id);
      } catch (reason) {
        setError(problem(reason));
        if (!after) setTasks([]);
      }
    },
    [scope, workspace],
  );
  useEffect(() => {
    void load();
  }, [load]);
  const ordered = tasks ? inboxOrder(tasks) : [];
  const opened = ordered.find((task) => task.id === openId);
  return (
    <>
      <SectionHeader
        eyebrow="Operate"
        title="Human tasks"
        description="Reviews and forms a running workflow is waiting on. Each review holds the exact action and inputs you are asked to allow."
        actions={
          <>
            {picker}
            <button className="secondary" onClick={() => void load()}>
              Refresh
            </button>
          </>
        }
      />
      {error &&
        (error === operateMessage('authority_unavailable', '') ? (
          <div className="notice operate-unavailable" role="status">
            <p>{error}</p>
            <button className="secondary" onClick={() => void load()}>
              Try again
            </button>
          </div>
        ) : (
          <Alert onRetry={() => void load()}>{error}</Alert>
        ))}
      {closed && (
        <p className="notice" role="status">
          {closed}
        </p>
      )}
      {tasks === null ? (
        <p className="loading-state" role="status">
          Loading tasks…
        </p>
      ) : !ordered.length ? (
        !error && (
          <EmptyState
            glyph="enclosure"
            title="Nothing is waiting on you"
            description="When a workflow stops for a review or a form in this environment, it appears here."
          />
        )
      ) : (
        <div className="operate-split">
          <section aria-labelledby="inbox-heading" className="operate-list">
            <h2 id="inbox-heading" className="sr-only">
              Open tasks
            </h2>
            <ul>
              {ordered.map((task) => (
                <li key={task.id}>
                  <button
                    className={`operate-row ${task.id === openId ? 'selected' : ''}`}
                    aria-current={task.id === openId ? 'true' : undefined}
                    onClick={() => {
                      setOpenId(task.id);
                      setClosed('');
                    }}
                  >
                    <span className="operate-row-title">
                      {task.kind === 'review' ? 'Review an action' : 'Complete a form'}
                    </span>
                    <span className="operate-row-meta">
                      {task.escalated && <StatusBadge value="escalated" />}
                      <span>
                        Due <RelativeTime value={fromMs(task.deadline_ms)} />
                      </span>
                      <span>
                        {task.claim === 'caller'
                          ? 'Claimed by you'
                          : task.claim === 'other'
                            ? 'Claimed by someone else'
                            : 'Unclaimed'}
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
            <TaskDetail
              key={opened.id}
              workspace={workspace}
              scope={scope}
              task={opened}
              onChanged={(updated, done) => {
                setTasks(
                  (current) =>
                    current
                      ?.map((item) => (item.id === updated.id ? updated : item))
                      .filter((item) => item.state === 'open') ?? null,
                );
                if (updated.state !== 'open') {
                  setOpenId('');
                  setClosed(done);
                }
              }}
              onGone={() => {
                setOpenId('');
                void load();
              }}
            />
          ) : (
            <div className="operate-detail operate-placeholder">
              <p>Choose a task to see what it asks of you.</p>
            </div>
          )}
        </div>
      )}
    </>
  );
}

function TaskDetail({
  workspace,
  scope,
  task,
  onChanged,
  onGone,
}: {
  workspace: string;
  scope: Scope;
  task: HumanTask;
  onChanged: (task: HumanTask, done: string) => void;
  onGone: () => void;
}) {
  const [proposal, setProposal] = useState<Proposal | null>(null);
  const [proposalError, setProposalError] = useState('');
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [busy, setBusy] = useState(false);
  const [payload, setPayload] = useState('{}');
  // A form's declared schema (what it asks), and the values a person has entered.
  const [form, setForm] = useState<TaskForm | null>(null);
  const [formError, setFormError] = useState('');
  const [values, setValues] = useState<FormValues>({});
  // One identity per intended decision or submission, so a repeated click is the same request.
  const pending = useRef<{ key: string; id: string } | null>(null);
  const heading = useRef<HTMLHeadingElement>(null);
  const actions = taskActions(task);
  const loadProposal = useCallback(async () => {
    if (task.kind !== 'review' || !task.intent_sha256) return;
    setProposalError('');
    try {
      setProposal(
        await api<Proposal>(scopedPath(scope, `human-tasks/${task.id}/proposal`), workspace),
      );
    } catch (reason) {
      setProposalError(problem(reason));
    }
  }, [scope, task.id, task.intent_sha256, task.kind, workspace]);
  const loadForm = useCallback(async () => {
    if (task.kind !== 'form') return;
    setFormError('');
    try {
      setForm(await api<TaskForm>(scopedPath(scope, `human-tasks/${task.id}/form`), workspace));
    } catch (reason) {
      setFormError(problem(reason));
    }
  }, [scope, task.id, task.kind, workspace]);
  const fields = form ? formFields(form) : null;
  useEffect(() => {
    heading.current?.focus();
    void loadProposal();
    void loadForm();
  }, [loadProposal, loadForm]);
  function identity(key: string, prefix: string) {
    if (pending.current?.key !== key) pending.current = { key, id: opaqueId(prefix) };
    return pending.current.id;
  }
  async function act(
    path: string,
    body: unknown,
    done?: string,
    explain: (reason: unknown) => string = problem,
  ) {
    setBusy(true);
    setError('');
    setNotice('');
    try {
      const updated = await api<HumanTask>(
        scopedPath(scope, `human-tasks/${task.id}/${path}`),
        workspace,
        body,
      );
      pending.current = null;
      if (done) setNotice(done);
      onChanged(updated, done ?? '');
    } catch (reason) {
      setError(explain(reason));
      if (reason instanceof ApiError && reason.code === 'conflict') {
        pending.current = null;
        void loadProposal();
      }
      if (reason instanceof ApiError && reason.code === 'not_found') onGone();
    } finally {
      setBusy(false);
    }
  }
  function decide(decision: 'allow' | 'deny') {
    if (!proposal) return;
    void act(
      'decision',
      {
        decision_id: identity(`${decision}:${proposal.intent_sha256}`, 'decision'),
        intent_sha256: proposal.intent_sha256,
        decision,
      },
      decision === 'allow' ? 'Allowed. The action can now be sent.' : 'Denied. Nothing is sent.',
      (reason) =>
        reason instanceof ApiError
          ? decisionMessage(reason.code, decision, proposal.independent_approval, reason.message)
          : problem(reason),
    );
  }
  function submit() {
    let value: unknown;
    if (fields) {
      const built = formPayload(fields, values);
      if ('error' in built) {
        setError(built.error);
        return;
      }
      value = built.payload;
    } else {
      try {
        value = JSON.parse(payload);
      } catch {
        setError('The form answer must be valid JSON.');
        return;
      }
    }
    const answer = JSON.stringify(value);
    void act(
      'submission',
      { submission_id: identity(`submit:${answer}`, 'submission'), payload: value },
      'Submitted.',
    );
  }
  const expired = proposal ? proposal.expires_at_ms <= Date.now() : false;
  return (
    <section className="operate-detail" aria-labelledby="task-heading">
      <h2 id="task-heading" ref={heading} tabIndex={-1}>
        {task.kind === 'review' ? 'Review an action' : 'Complete a form'}
      </h2>
      <dl className="record-facts">
        <div>
          <dt>Due</dt>
          <dd>
            <RelativeTime value={fromMs(task.deadline_ms)} />
          </dd>
        </div>
        <div>
          <dt>Claim</dt>
          <dd>
            {task.claim === 'caller' ? (
              <>
                Yours until <RelativeTime value={fromMs(task.claim_expires_at_ms)} />
              </>
            ) : task.claim === 'other' ? (
              'Someone else is working on it'
            ) : (
              'Unclaimed'
            )}
          </dd>
        </div>
        <div>
          <dt>Escalation</dt>
          <dd>
            {task.escalated ? (
              <StatusBadge value="escalated" />
            ) : task.escalate_at_ms ? (
              <>
                Escalates <RelativeTime value={fromMs(task.escalate_at_ms)} />
              </>
            ) : (
              'None'
            )}
          </dd>
        </div>
        <div>
          <dt>Execution</dt>
          <dd className="cell-mono">{task.execution_id}</dd>
        </div>
      </dl>

      {task.kind === 'review' &&
        (proposalError ? (
          <Alert onRetry={() => void loadProposal()}>{proposalError}</Alert>
        ) : !proposal ? (
          <p className="loading-state" role="status">
            Loading the proposed action…
          </p>
        ) : (
          <div className="proposal">
            <h3>The proposed action</h3>
            <p className="proposal-line">
              <strong>{proposal.operation}</strong> on{' '}
              <strong>
                {proposal.connector_name} {proposal.connector_version}
              </strong>
            </p>
            <dl className="record-facts">
              <div>
                <dt>Policy</dt>
                <dd>
                  {proposal.policy_name} {proposal.policy_version}
                </dd>
              </div>
              <div>
                <dt>Approval expires</dt>
                <dd>
                  <RelativeTime value={fromMs(proposal.expires_at_ms)} />
                </dd>
              </div>
              <div className="wide">
                <dt>Target</dt>
                <dd>
                  <ObjectView value={proposal.target} />
                </dd>
              </div>
              <div className="wide">
                <dt>Input</dt>
                <dd>
                  <ObjectView value={proposal.input} />
                </dd>
              </div>
              <div className="wide">
                <dt>Action digest</dt>
                <dd className="cell-mono">{proposal.intent_sha256}</dd>
              </div>
            </dl>
            {proposal.independent_approval && (
              <p className="hint">
                This action needs independent approval: the person who requested it cannot allow it.
              </p>
            )}
            {expired && (
              <p className="hint">This approval has expired. Allowing it now authorizes nothing.</p>
            )}
          </div>
        ))}

      {task.kind === 'form' && (
        <FormAsks
          taskId={task.id}
          fields={fields}
          loaded={form !== null}
          error={formError}
          onRetry={() => void loadForm()}
          editing={actions.decide}
          values={values}
          onValue={(name, value) => {
            setValues((current) => ({ ...current, [name]: value }));
            setError(''); // a corrected answer clears the last refusal
          }}
          payload={payload}
          onPayload={setPayload}
        />
      )}

      {error && <Alert>{error}</Alert>}
      {notice && (
        <p className="notice" role="status">
          {notice}
        </p>
      )}

      <div className="operate-actions">
        {actions.claim && (
          <button
            disabled={busy}
            onClick={() => void act('claim', {}, 'Claimed. It is yours to decide.')}
          >
            Claim
          </button>
        )}
        {actions.decide && task.kind === 'review' && (
          <>
            <button
              className="decide"
              disabled={busy || !proposal || expired}
              onClick={() => decide('allow')}
            >
              Allow this action
            </button>
            <button
              className="secondary danger"
              disabled={busy || !proposal}
              onClick={() => decide('deny')}
            >
              Deny
            </button>
          </>
        )}
        {actions.decide && task.kind === 'form' && (
          <button className="decide" disabled={busy} onClick={submit}>
            Submit
          </button>
        )}
        {actions.release && (
          <button
            className="text-button"
            disabled={busy}
            onClick={() => void act('release', {}, 'Released for someone else.')}
          >
            Release
          </button>
        )}
      </div>
      {!actions.claim && !actions.decide && task.claim === 'other' && (
        <p className="hint">
          Someone else holds this task. It returns to the inbox if they release it or their claim
          lapses.
        </p>
      )}
    </section>
  );
}

// What a form task asks, from its declared schema: each field with its limits before the
// task is claimed, and inputs for them once it is the caller's. A schema the console
// cannot lay out as fields (lists, nested objects) keeps the JSON editor.
function FormAsks({
  taskId,
  fields,
  loaded,
  error,
  onRetry,
  editing,
  values,
  onValue,
  payload,
  onPayload,
}: {
  taskId: string;
  fields: FormField[] | null;
  loaded: boolean;
  error: string;
  onRetry: () => void;
  editing: boolean;
  values: FormValues;
  onValue: (name: string, value: string | boolean) => void;
  payload: string;
  onPayload: (value: string) => void;
}) {
  const json = (
    <label>
      Your answer (JSON)
      <textarea
        className="code-input"
        rows={6}
        value={payload}
        onChange={(event) => onPayload(event.target.value)}
      />
    </label>
  );
  if (error)
    return (
      <>
        <Alert onRetry={onRetry}>{error}</Alert>
        {editing && json}
      </>
    );
  if (!loaded)
    return (
      <p className="loading-state" role="status">
        Loading what this form asks…
      </p>
    );
  return (
    <div className="proposal form-asks">
      <h3>What this form asks</h3>
      {!fields ? (
        <>
          <p className="hint">This answer has a nested shape; enter it as JSON.</p>
          {editing && json}
        </>
      ) : editing ? (
        fields.map((field) => (
          <FieldInput
            key={field.name ?? ''}
            id={`${taskId}-${field.name ?? 'answer'}`}
            field={field}
            value={values[field.name ?? '']}
            onChange={(value) => onValue(field.name ?? '', value)}
          />
        ))
      ) : (
        <dl className="record-facts form-asks-list">
          {fields.map((field) => (
            <div key={field.name ?? ''}>
              <dt>
                {field.label}
                {!field.required && <span className="optional"> (optional)</span>}
              </dt>
              <dd>{field.hint}</dd>
            </div>
          ))}
        </dl>
      )}
      {!editing && <p className="hint">Claim the task to answer.</p>}
    </div>
  );
}

function FieldInput({
  id,
  field,
  value,
  onChange,
}: {
  id: string;
  field: FormField;
  value: string | boolean | undefined;
  onChange: (value: string | boolean) => void;
}) {
  const hint = `${id}-hint`;
  const label = (
    <>
      {field.label}
      {!field.required && <span className="optional"> (optional)</span>}
    </>
  );
  if (field.kind === 'boolean')
    return (
      <div className="form-field">
        <label className="checkbox-label" htmlFor={id}>
          <input
            id={id}
            type="checkbox"
            checked={value === true}
            aria-describedby={hint}
            onChange={(event) => onChange(event.target.checked)}
          />
          {label}
        </label>
        <p className="hint" id={hint}>
          {field.hint}
        </p>
      </div>
    );
  const text = typeof value === 'string' ? value : '';
  const common = {
    id,
    value: text,
    required: field.required,
    'aria-describedby': hint,
  };
  return (
    <div className="form-field">
      <label htmlFor={id}>{label}</label>
      {field.kind === 'long_text' ? (
        <textarea
          {...common}
          rows={4}
          maxLength={field.maxLength}
          onChange={(event) => onChange(event.target.value)}
        />
      ) : (
        <input
          {...common}
          type="text"
          maxLength={field.kind === 'text' ? field.maxLength : undefined}
          inputMode={
            field.kind === 'integer' ? 'numeric' : field.kind === 'text' ? undefined : 'decimal'
          }
          autoComplete="off"
          onChange={(event) => onChange(event.target.value)}
        />
      )}
      <p className="hint" id={hint}>
        {field.hint}
      </p>
    </div>
  );
}
