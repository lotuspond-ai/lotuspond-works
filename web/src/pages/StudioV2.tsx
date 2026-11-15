import { useCallback, useEffect, useMemo, useRef, useState, type ChangeEvent } from 'react';
import { api, ApiError } from '../api.js';
import { EnvironmentScope } from '../components/operate/EnvironmentScope.js';
import { Alert, StatusBadge } from '../components/ui.js';
import { operateMessage, scopedPath, type Scope } from '../lib/operate.js';
import {
  V2_PATTERNS,
  clone,
  isV2,
  matchDependencies,
  moveStep,
  newStep,
  parseSignature,
  pinnedRefs,
  problemsByStep,
  signCommand,
  stepDetail,
  stepLabel,
  uniqueStepId,
  type JsonObject,
  type NewStepKind,
  type Problem,
  type Publication,
  type Step,
  type V2Package,
} from '../lib/studioV2.js';

type Check = {
  valid: boolean;
  package_sha256: string | null;
  plan_sha256: string | null;
  canonical: string | null;
  problems: Problem[];
};

const DRAFT_KEY = 'lotuspond.studio.v2.draft';

function problem(reason: unknown) {
  return reason instanceof ApiError
    ? operateMessage(reason.code, reason.message)
    : 'The request could not be completed. Please try again.';
}

function loadDraft(): V2Package | null {
  try {
    const value = JSON.parse(localStorage.getItem(DRAFT_KEY) ?? 'null');
    return isV2(value) ? value : null;
  } catch {
    return null;
  }
}

function saveDraft(value: V2Package) {
  try {
    localStorage.setItem(DRAFT_KEY, JSON.stringify(value));
  } catch {
    /* Private browsers can refuse storage; the draft stays on the page. */
  }
}

// Studio authoring of executable-v2 (docs/superpowers/specs/2026-11-15-studio-v2-authoring-design.md).
export function StudioV2({
  workspace,
  projectId,
  role,
}: {
  workspace: string;
  projectId: string;
  role: string;
}) {
  return (
    <EnvironmentScope workspace={workspace} projectId={projectId}>
      {(scope, picker) => (
        <Editor
          key={scope.environmentId}
          workspace={workspace}
          scope={scope}
          picker={picker}
          canPublish={role !== 'viewer'}
        />
      )}
    </EnvironmentScope>
  );
}

function Editor({
  workspace,
  scope,
  picker,
  canPublish,
}: {
  workspace: string;
  scope: Scope;
  picker: React.ReactNode;
  canPublish: boolean;
}) {
  const [draft, setDraft] = useState<V2Package | null>(() => loadDraft());
  const [selected, setSelected] = useState<number | 'package'>('package');
  const [check, setCheck] = useState<Check | null>(null);
  const [checking, setChecking] = useState(false);
  const [error, setError] = useState('');
  const [publications, setPublications] = useState<Publication[] | null>(null);
  const [signature, setSignature] = useState<JsonObject | null>(null);
  const [published, setPublished] = useState<{ id: string; package_sha256: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const requestKey = useRef<string>('');

  useEffect(() => {
    api<{ items: Publication[] }>(scopedPath(scope, 'executable-packages?limit=100'), workspace)
      .then((data) => setPublications(data.items))
      .catch((reason) => {
        setPublications([]);
        setError(problem(reason));
      });
  }, [scope, workspace]);

  const refs = useMemo(() => (draft ? pinnedRefs(draft) : []), [draft]);
  const dependencies = useMemo(
    () => matchDependencies(refs, publications ?? []),
    [refs, publications],
  );

  // Every change is checked by the server after a short pause.
  useEffect(() => {
    if (!draft) return;
    saveDraft(draft);
    setSignature(null);
    setPublished(null);
    const timer = window.setTimeout(async () => {
      setChecking(true);
      try {
        setCheck(
          await api<Check>(scopedPath(scope, 'executable-packages/check'), workspace, {
            package: draft,
            dependency_ids: dependencies.ids,
          }),
        );
        setError('');
      } catch (reason) {
        setError(problem(reason));
      } finally {
        setChecking(false);
      }
    }, 500);
    return () => window.clearTimeout(timer);
  }, [draft, dependencies.ids, scope, workspace]);

  const byStep = useMemo(() => problemsByStep(check?.problems ?? []), [check]);

  function update(change: (value: V2Package) => void) {
    if (!draft) return;
    const next = clone(draft);
    change(next);
    setDraft(next);
  }

  function start(pkg: V2Package) {
    setDraft(clone(pkg));
    setSelected('package');
    setCheck(null);
  }

  async function open(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    event.target.value = '';
    if (!file) return;
    try {
      const value = JSON.parse(await file.text());
      if (!isV2(value)) throw new Error();
      start(value);
    } catch {
      setError('That file is not an executable-v2 workflow package.');
    }
  }

  function download() {
    if (!check?.canonical || !draft) return;
    const url = URL.createObjectURL(new Blob([check.canonical], { type: 'application/json' }));
    const link = Object.assign(document.createElement('a'), {
      href: url,
      download: `${draft.package_id}-${draft.version}.json`,
    });
    link.click();
    URL.revokeObjectURL(url);
  }

  async function attach(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    event.target.value = '';
    if (!file) return;
    const value = parseSignature(await file.text());
    if (!value) setError('That file is not a signature envelope.');
    else {
      setError('');
      setSignature(value);
    }
  }

  async function publish() {
    if (!draft || !signature) return;
    setBusy(true);
    setError('');
    if (!requestKey.current) requestKey.current = `studio-${crypto.randomUUID()}`;
    try {
      const view = await api<{ id: string; package_sha256: string }>(
        scopedPath(scope, 'executable-packages'),
        workspace,
        { package: draft, dependency_ids: dependencies.ids, signature },
        'POST',
        undefined,
        { 'idempotency-key': requestKey.current },
      );
      requestKey.current = '';
      setPublished(view);
    } catch (reason) {
      setError(
        reason instanceof ApiError && reason.code === 'trust_unavailable'
          ? 'The signature is not from a publisher this installation trusts, or it signs different content. Sign the downloaded file again with a trusted key.'
          : problem(reason),
      );
    } finally {
      setBusy(false);
    }
  }

  if (!draft)
    return (
      <div className="studio-v2">
        <div className="studio-v2-bar">{picker}</div>
        <section className="panel" aria-labelledby="v2-start">
          <h2 id="v2-start">Start a deployable workflow</h2>
          <p>
            Workflows run on the durable engine. Start from a synthetic pattern or structure, or
            open a package file. A pattern pins synthetic documents: publish real ones in this
            environment, or replace the pins, before it can be published.
          </p>
          {(
            [
              ['domain', 'Patterns'],
              ['structure', 'Structures'],
            ] as const
          ).map(([family, heading]) => (
            <div key={family}>
              <h3>{heading}</h3>
              <ul className="studio-v2-patterns">
                {V2_PATTERNS.filter((pattern) => pattern.family === family).map((pattern) => (
                  <li key={pattern.id}>
                    <button className="operate-row" onClick={() => start(pattern.package)}>
                      <span className="operate-row-title">{pattern.title}</span>
                      <span className="operate-row-meta">{pattern.text}</span>
                    </button>
                  </li>
                ))}
              </ul>
            </div>
          ))}
          <label className="studio-v2-file">
            Open a package file
            <input type="file" accept="application/json,.json" onChange={(e) => void open(e)} />
          </label>
          {error && <Alert>{error}</Alert>}
        </section>
      </div>
    );

  const steps = draft.body.steps;
  return (
    <div className="studio-v2">
      <div className="studio-v2-bar">
        {picker}
        <button className="secondary" onClick={() => setDraft(null)}>
          Start over
        </button>
      </div>
      {error && <Alert>{error}</Alert>}
      <div className="studio-v2-grid">
        <nav className="studio-v2-outline" aria-label="Package outline">
          <button
            className={`operate-row ${selected === 'package' ? 'selected' : ''}`}
            aria-current={selected === 'package' ? 'true' : undefined}
            onClick={() => setSelected('package')}
          >
            <span className="operate-row-title">
              {draft.package_id} {draft.version}
            </span>
            <span className="operate-row-meta">Package, limits and declarations</span>
          </button>
          <h3>Steps</h3>
          <ol>
            {steps.map((step, index) => (
              <li key={`${step.id}-${index}`}>
                <button
                  className={`operate-row ${selected === index ? 'selected' : ''}`}
                  aria-current={selected === index ? 'true' : undefined}
                  onClick={() => setSelected(index)}
                >
                  <span className="operate-row-title">
                    {index + 1}. {stepLabel(step)} <span className="cell-mono">{step.id}</span>
                  </span>
                  <span className="operate-row-meta">
                    {stepDetail(step)}
                    {byStep.steps.has(index) && (
                      <strong className="danger">
                        {byStep.steps.get(index)!.length} problem
                        {byStep.steps.get(index)!.length === 1 ? '' : 's'}
                      </strong>
                    )}
                  </span>
                </button>
              </li>
            ))}
          </ol>
          <label>
            Add a step
            <select
              value=""
              onChange={(event) => {
                const kind = event.target.value as NewStepKind;
                if (!kind) return;
                update((pkg) => {
                  pkg.body.steps.push(newStep(kind, uniqueStepId(pkg.body.steps, kind)));
                });
                setSelected(steps.length);
              }}
            >
              <option value="">Choose a kind…</option>
              <option value="connector_read">Read from a connector</option>
              <option value="agent">Agent</option>
              <option value="transform">Transform</option>
              <option value="timer">Timer</option>
            </select>
          </label>
        </nav>

        <section className="studio-v2-editor" aria-label="Editor">
          {selected === 'package' ? (
            <PackageForm draft={draft} update={update} refs={dependencies} />
          ) : steps[selected] ? (
            <StepEditor
              key={`${selected}-${steps.length}`}
              step={steps[selected]!}
              index={selected}
              count={steps.length}
              problems={byStep.steps.get(selected) ?? []}
              onChange={(step) =>
                update((pkg) => {
                  pkg.body.steps[selected] = step;
                })
              }
              onMove={(to) => {
                update((pkg) => {
                  pkg.body.steps = moveStep(pkg.body.steps, selected, to);
                });
                setSelected(to);
              }}
              onRemove={() => {
                update((pkg) => {
                  pkg.body.steps.splice(selected, 1);
                });
                setSelected('package');
              }}
            />
          ) : null}
        </section>

        <aside className="studio-v2-check" aria-labelledby="v2-check">
          <h2 id="v2-check">Check</h2>
          {checking || !check ? (
            <p className="hint" role="status">
              Checking with the server…
            </p>
          ) : check.valid ? (
            <p>
              <StatusBadge value="passed" /> Valid and compiles.
            </p>
          ) : (
            <p>
              <StatusBadge value="failed" /> {check.problems.length} problem
              {check.problems.length === 1 ? '' : 's'}.
            </p>
          )}
          {check && byStep.rest.length > 0 && (
            <ul className="studio-v2-problems">
              {byStep.rest.map((item, index) => (
                <li key={index}>
                  <span className="cell-mono">{item.path || 'package'}</span> {item.message}
                </li>
              ))}
            </ul>
          )}
          {check?.package_sha256 && (
            <p className="hint">
              Content digest <span className="cell-mono">{check.package_sha256.slice(0, 16)}</span>
            </p>
          )}
          <h3>Dependencies</h3>
          {refs.length === 0 ? (
            <p className="hint">This package pins no catalog documents.</p>
          ) : (
            <ul className="studio-v2-problems">
              {dependencies.matched.map(({ ref }) => (
                <li key={`${ref.kind}${ref.name}`}>
                  <StatusBadge value="ok" /> {ref.kind} {ref.name} {ref.version}
                </li>
              ))}
              {dependencies.missing.map((ref) => (
                <li key={`${ref.kind}${ref.name}`}>
                  <StatusBadge value="blocked" /> {ref.kind} {ref.name} {ref.version} is not
                  published in this environment
                </li>
              ))}
            </ul>
          )}
          <h3>Sign and publish</h3>
          {published ? (
            <p className="notice" role="status">
              Published. Content digest{' '}
              <span className="cell-mono">{published.package_sha256.slice(0, 16)}</span>; deploy it
              from the API, and watch it in{' '}
              <a className="inline-link" href="#control-room">
                Control Room
              </a>
              .
            </p>
          ) : !check?.valid || dependencies.missing.length ? (
            <p className="hint">Fix every problem and dependency first.</p>
          ) : (
            <ol className="studio-v2-steps">
              <li>
                <button className="secondary" onClick={download}>
                  Download the package
                </button>
              </li>
              <li>
                Sign it with your publisher key, outside the browser:
                <pre className="studio-v2-command">
                  {signCommand(`${draft.package_id}-${draft.version}.json`)}
                </pre>
              </li>
              <li>
                <label className="studio-v2-file">
                  Attach signature.json
                  <input
                    type="file"
                    accept="application/json,.json"
                    onChange={(e) => void attach(e)}
                  />
                </label>
              </li>
              <li>
                <button disabled={!signature || busy || !canPublish} onClick={() => void publish()}>
                  Publish
                </button>
                {!canPublish && <p className="hint">Viewers cannot publish.</p>}
              </li>
            </ol>
          )}
        </aside>
      </div>
    </div>
  );
}

function PackageForm({
  draft,
  update,
  refs,
}: {
  draft: V2Package;
  update: (change: (value: V2Package) => void) => void;
  refs: ReturnType<typeof matchDependencies>;
}) {
  const [json, setJson] = useState(() => JSON.stringify(draft, null, 2));
  const [jsonError, setJsonError] = useState('');
  const limits = draft.limits as Record<string, number>;
  return (
    <>
      <h2>Package</h2>
      <div className="form-grid">
        {(['package_id', 'version', 'name'] as const).map((field) => (
          <label key={field}>
            {field === 'package_id' ? 'Package ID' : field === 'version' ? 'Version' : 'Name'}
            <input
              value={String(draft[field] ?? '')}
              onChange={(event) =>
                update((pkg) => {
                  pkg[field] = event.target.value;
                })
              }
            />
          </label>
        ))}
      </div>
      <h3>Limits</h3>
      <p className="hint">Hard limits on the whole run; the engine enforces them.</p>
      <div className="form-grid">
        {Object.keys(limits).map((field) => (
          <label key={field}>
            {field.replaceAll('_', ' ')}
            <input
              type="number"
              min={0}
              value={limits[field]}
              onChange={(event) =>
                update((pkg) => {
                  (pkg.limits as Record<string, number>)[field] = Number(event.target.value);
                })
              }
            />
          </label>
        ))}
      </div>
      <h3>Pinned documents</h3>
      <p className="hint">
        {refs.matched.length + refs.missing.length} pinned, {refs.missing.length} missing in this
        environment.
      </p>
      <details
        className="raw-record"
        onToggle={(event) => {
          if ((event.target as HTMLDetailsElement).open) setJson(JSON.stringify(draft, null, 2));
        }}
      >
        <summary>Edit the whole package as JSON</summary>
        <textarea
          className="code-input"
          rows={18}
          value={json}
          onChange={(event) => setJson(event.target.value)}
        />
        {jsonError && <Alert>{jsonError}</Alert>}
        <button
          className="secondary"
          onClick={() => {
            try {
              const value = JSON.parse(json);
              if (!isV2(value)) throw new Error();
              setJsonError('');
              update((pkg) => {
                for (const key of Object.keys(pkg)) delete pkg[key];
                Object.assign(pkg, value);
              });
            } catch {
              setJsonError('This is not a valid executable-v2 workflow package as JSON.');
            }
          }}
        >
          Apply
        </button>
      </details>
    </>
  );
}

function StepEditor({
  step,
  index,
  count,
  problems,
  onChange,
  onMove,
  onRemove,
}: {
  step: Step;
  index: number;
  count: number;
  problems: Problem[];
  onChange: (step: Step) => void;
  onMove: (to: number) => void;
  onRemove: () => void;
}) {
  const [text, setText] = useState(() => JSON.stringify(step, null, 2));
  const [parseError, setParseError] = useState('');
  const apply = useCallback(
    (value: string) => {
      setText(value);
      try {
        const parsed = JSON.parse(value);
        if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed) || !parsed.kind)
          throw new Error();
        setParseError('');
        onChange(parsed as Step);
      } catch {
        setParseError('Not valid JSON for a step yet; the last valid version is kept.');
      }
    },
    [onChange],
  );
  return (
    <>
      <h2>
        {index + 1}. {stepLabel(step)} <span className="cell-mono">{step.id}</span>
      </h2>
      <div className="operate-actions">
        <button className="secondary" disabled={index === 0} onClick={() => onMove(index - 1)}>
          Move up
        </button>
        <button
          className="secondary"
          disabled={index === count - 1}
          onClick={() => onMove(index + 1)}
        >
          Move down
        </button>
        <button className="text-button danger" onClick={onRemove}>
          Remove
        </button>
      </div>
      {problems.length > 0 && (
        <ul className="studio-v2-problems">
          {problems.map((item, n) => (
            <li key={n}>
              <span className="cell-mono">{item.path}</span> {item.message}
            </li>
          ))}
        </ul>
      )}
      <label>
        Step as JSON
        <textarea
          className="code-input"
          rows={22}
          spellCheck={false}
          value={text}
          onChange={(event) => apply(event.target.value)}
        />
      </label>
      {parseError && <p className="hint">{parseError}</p>}
    </>
  );
}
