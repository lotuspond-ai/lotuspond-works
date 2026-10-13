import { useEffect, useId, useState, type FormEvent } from 'react';
import { api, type Row } from '../api.js';
import { Alert } from './ui.js';

export function ScenarioForm({
  workspace,
  projectId,
  onSubmit,
}: {
  workspace: string;
  projectId: string;
  onSubmit: (data: Record<string, unknown>) => Promise<void>;
}) {
  const [projects, setProjects] = useState<Row[]>([]);
  const [selected, setSelected] = useState(projectId);
  const [advanced, setAdvanced] = useState(false);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [loaded, setLoaded] = useState(false);
  // Hints sit inside their labels; each field is named by its label text and described by the
  // hint, so the hint is read once as a description.
  const id = useId();
  useEffect(() => {
    const controller = new AbortController();
    void api<{ items: Row[] }>(
      '/projects?limit=100',
      workspace,
      undefined,
      'GET',
      controller.signal,
    )
      .then((result) => {
        if (!controller.signal.aborted) {
          setProjects(result.items);
          setLoaded(true);
        }
      })
      .catch((reason: Error) => {
        if (!controller.signal.aborted) setError(reason.message);
      });
    return () => controller.abort();
  }, [workspace]);
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError('');
    const form = new FormData(event.currentTarget);
    try {
      const input: unknown = advanced
        ? JSON.parse(String(form.get('input')))
        : { prompt: String(form.get('prompt')) };
      const expected: unknown = advanced
        ? JSON.parse(String(form.get('expected')))
        : { contains: String(form.get('contains')) };
      if (
        !input ||
        typeof input !== 'object' ||
        Array.isArray(input) ||
        !expected ||
        typeof expected !== 'object' ||
        Array.isArray(expected)
      )
        throw new Error('Input and expected outcome must each be a JSON object.');
      await onSubmit({
        project_id: selected,
        name: String(form.get('name')),
        modality: String(form.get('modality')),
        input,
        expected,
        critical: form.has('critical'),
      });
    } catch (reason) {
      setError((reason as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <form
      className="record-form"
      onSubmit={(event) => {
        void submit(event);
      }}
    >
      <h2>Add a scenario</h2>
      <p>
        Write a concrete task and a measurable expected outcome. The evaluation worker executes the
        task against your agent.
      </p>
      <div className="form-grid">
        <label>
          Project
          <select required value={selected} onChange={(event) => setSelected(event.target.value)}>
            <option value="">Choose a project…</option>
            {projects.map((row) => (
              <option key={row.id} value={row.id}>
                {String(row.name)}
              </option>
            ))}
          </select>
        </label>
        <label>
          Modality
          <select name="modality" defaultValue="text">
            <option value="text">Text</option>
            <option value="voice">Voice</option>
            <option value="tool">Tool use</option>
            <option value="multimodal">Multimodal</option>
          </select>
        </label>
        <label className="wide">
          Scenario name
          <input
            name="name"
            required
            maxLength={160}
            placeholder="e.g. Reschedule an appointment without losing the original booking"
          />
        </label>
        {!advanced ? (
          <>
            <label className="wide">
              Task or conversation input
              <textarea
                name="prompt"
                rows={3}
                required
                placeholder="The request your agent should respond to"
              />
            </label>
            <label className="wide">
              <span id={`${id}-contains`}>Required response text</span>
              <textarea
                name="contains"
                required
                rows={2}
                placeholder="Text the resulting response must contain"
                aria-labelledby={`${id}-contains`}
                aria-describedby={`${id}-contains-hint`}
              />
              <span className="hint" id={`${id}-contains-hint`}>
                A text containment check. Use structured expectations for tool calls, voice turns,
                and custom checks.
              </span>
            </label>
          </>
        ) : (
          <>
            <label className="wide">
              Input object
              <textarea
                name="input"
                className="code-input"
                required
                rows={5}
                defaultValue={'{\n  "prompt": ""\n}'}
                spellCheck={false}
              />
            </label>
            <label className="wide">
              <span id={`${id}-expected`}>Expected outcome object</span>
              <textarea
                name="expected"
                className="code-input"
                required
                rows={5}
                defaultValue={'{\n  "contains": ""\n}'}
                spellCheck={false}
                aria-labelledby={`${id}-expected`}
                aria-describedby={`${id}-expected-hint`}
              />
              <span className="hint" id={`${id}-expected-hint`}>
                These expectations are passed to your evaluation worker. Match the checks supported
                by your adapter.
              </span>
            </label>
          </>
        )}
        <label className="checkbox-label">
          <input type="checkbox" name="critical" />
          Critical: a failure blocks release
        </label>
      </div>
      <div className="button-row">
        <button disabled={busy || !loaded || !projects.length}>
          {busy ? 'Saving…' : 'Save scenario'}
        </button>
        <button type="button" className="text-button" onClick={() => setAdvanced(!advanced)}>
          {advanced ? 'Use text authoring' : 'Use structured input'}
        </button>
      </div>
      {loaded && !projects.length && (
        <p className="hint">
          Create a{' '}
          <a className="inline-link" href="#projects">
            project
          </a>{' '}
          before adding scenarios.
        </p>
      )}
      {error && <Alert>{error}</Alert>}
    </form>
  );
}
