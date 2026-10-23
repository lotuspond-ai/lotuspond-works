import { useCallback, useEffect, useMemo, useRef, useState, type ChangeEvent } from 'react';
import { api, ApiError, type FieldProblem } from '../api.js';
import { Alert, EmptyState, RelativeTime, SectionHeader } from '../components/ui.js';
import { StudioCanvas, type Selection } from '../components/studio/StudioCanvas.js';
import { StudioInspector } from '../components/studio/StudioInspector.js';
import {
  jsonBoundsProblem,
  readPackage,
  packageDigest,
  type NodeKind,
  type PackageNode,
  type Problem,
  type WorkflowPackage,
} from '../lib/packageContract.js';
import {
  addEdge,
  addNode,
  autoLayout,
  checkDraft,
  draftFromPackage,
  emptyDraft,
  moveNode,
  nextNodeId,
  parseDraft,
  problemLocation,
  removeEdge,
  removeNode,
  renameNode,
  serializeDraft,
  updateNode,
  updatePackage,
  type Draft,
  type Position,
} from '../lib/studio.js';
import example from '../../../contracts/examples/reviewed-action.v1.json';

type PackageVersion = {
  id: string;
  created_at: string;
  project_id: string;
  package_id: string;
  version: string;
  schema_version: string;
  validator_revision: string;
  sha256: string;
  content_bytes: number;
  publisher_kind: string;
};
type Notice = { tone: 'success' | 'neutral'; text: string; undo?: Draft };

const storageKey = (workspace: string, projectId: string) =>
  `lotuspond.studio.${workspace}.${projectId || 'workspace'}`;

function exampleDraft(): Draft {
  const read = readPackage(example);
  return read.package ? draftFromPackage(read.package) : emptyDraft();
}

export function Studio({
  workspace,
  projectId,
  projectName,
  role,
}: {
  workspace: string;
  projectId: string;
  projectName: string;
  role: string;
}) {
  const [draft, setDraft] = useState<Draft | null | undefined>(undefined);
  const [selection, setSelection] = useState<Selection>(null);
  const [connecting, setConnecting] = useState<string | null>(null);
  const [digest, setDigest] = useState('');
  const [notice, setNotice] = useState<Notice | null>(null);
  const [importError, setImportError] = useState<{ text: string; problems: Problem[] } | null>(
    null,
  );
  const [publishing, setPublishing] = useState(false);
  const [publishError, setPublishError] = useState<{
    text: string;
    problems: FieldProblem[];
  } | null>(null);
  const [versions, setVersions] = useState<PackageVersion[]>([]);
  const [versionsNext, setVersionsNext] = useState<string | null>(null);
  const [versionsState, setVersionsState] = useState<'loading' | 'ready' | 'error'>('loading');
  const [versionsError, setVersionsError] = useState('');
  const [versionsRefresh, setVersionsRefresh] = useState(0);
  const publishKey = useRef<string | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  const canWrite = role !== 'viewer';

  // The draft lives in this browser only, per workspace and project.
  useEffect(() => {
    setDraft(parseDraft(localStorage.getItem(storageKey(workspace, projectId))));
  }, [workspace, projectId]);
  useEffect(() => {
    if (draft === undefined) return;
    const key = storageKey(workspace, projectId);
    const timer = setTimeout(() => {
      if (draft) localStorage.setItem(key, serializeDraft(draft));
      else localStorage.removeItem(key);
    }, 250);
    return () => clearTimeout(timer);
  }, [draft, workspace, projectId]);

  const validation = useMemo(() => (draft ? checkDraft(draft.package) : null), [draft?.package]);
  const canonical = validation?.ok ? validation.canonical : '';
  useEffect(() => {
    publishKey.current = null;
    if (!canonical) {
      setDigest('');
      return;
    }
    let cancelled = false;
    void packageDigest(canonical).then((value) => {
      if (!cancelled) setDigest(value);
    });
    return () => {
      cancelled = true;
    };
  }, [canonical]);
  const problems = validation?.problems ?? [];
  const flagged = useMemo(() => {
    const map = new Map<string, string[]>();
    for (const problem of problems)
      for (const id of problem.nodes) map.set(id, [...(map.get(id) ?? []), problem.message]);
    return map;
  }, [problems]);

  const loadVersions = useCallback(
    async (signal: AbortSignal, cursor?: string) => {
      if (!projectId) {
        setVersions([]);
        setVersionsNext(null);
        setVersionsState('ready');
        return;
      }
      const query = new URLSearchParams({ project_id: projectId, limit: '50' });
      if (cursor) query.set('cursor', cursor);
      if (!cursor) setVersionsState('loading');
      try {
        const result = await api<{ items: PackageVersion[]; next: string | null }>(
          `/packages?${query}`,
          workspace,
          undefined,
          'GET',
          signal,
        );
        if (signal.aborted) return;
        setVersions((current) => (cursor ? [...current, ...result.items] : result.items));
        setVersionsNext(result.next);
        setVersionsState('ready');
        setVersionsError('');
      } catch (reason) {
        if (signal.aborted) return;
        setVersionsState('error');
        setVersionsError((reason as Error).message);
      }
    },
    [workspace, projectId],
  );
  useEffect(() => {
    const controller = new AbortController();
    void loadVersions(controller.signal);
    return () => controller.abort();
  }, [loadVersions, versionsRefresh]);

  const change = (update: (current: Draft) => Draft) =>
    setDraft((current) => (current ? update(current) : current));
  const select = useCallback((next: Selection) => setSelection(next), []);
  const startConnect = useCallback((id: string | null) => setConnecting(id), []);

  function deleteSelection(target: Selection) {
    if (!target) return;
    if (target.kind === 'node') change((current) => removeNode(current, target.id));
    else change((current) => removeEdge(current, target.edge));
    setSelection(null);
  }
  function replaceDraft(next: Draft, text: string) {
    setNotice({ tone: 'neutral', text, undo: draft ?? undefined });
    setDraft(next);
    setSelection(null);
    setConnecting(null);
    setImportError(null);
    setPublishError(null);
  }

  async function publish() {
    if (!draft || !validation?.ok || !projectId) return;
    publishKey.current ??= crypto.randomUUID();
    setPublishing(true);
    setPublishError(null);
    setNotice(null);
    try {
      const result = await api<PackageVersion>(
        `/projects/${encodeURIComponent(projectId)}/packages`,
        workspace,
        JSON.parse(validation.canonical),
        'POST',
        undefined,
        { 'Idempotency-Key': publishKey.current },
      );
      publishKey.current = null;
      // The registry answers an identical, already published version with the existing record.
      const existing = versions.some((version) => version.id === result.id);
      setNotice({
        tone: 'success',
        text:
          (existing
            ? `${result.package_id} ${result.version} was already published to ${projectName || 'the project'} with this exact content. `
            : `Published ${result.package_id} ${result.version} to ${projectName || 'the project'}. `) +
          (result.sha256 === digest
            ? 'The registry digest matches the local digest.'
            : 'The registry digest differs from the local digest; review the stored content before relying on it.'),
      });
      setVersionsRefresh((value) => value + 1);
    } catch (reason) {
      const error = reason as ApiError;
      setPublishError({
        text: error.message,
        problems: error instanceof ApiError ? error.problems : [],
      });
    } finally {
      setPublishing(false);
    }
  }

  function importFile(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    event.target.value = '';
    if (!file) return;
    void file.text().then((text) => {
      let raw: unknown;
      try {
        raw = JSON.parse(text);
      } catch {
        setImportError({ text: 'The file is not valid JSON.', problems: [] });
        return;
      }
      const bounds = jsonBoundsProblem(raw);
      if (bounds) {
        setImportError({ text: 'The file cannot be opened.', problems: [bounds] });
        return;
      }
      const read = readPackage(raw);
      if (!read.package) {
        setImportError({
          text: 'The file does not match the workflow-package v1 contract.',
          problems: read.problems,
        });
        return;
      }
      replaceDraft(draftFromPackage(read.package), `Opened ${file.name} as the draft.`);
    });
  }
  function exportJson() {
    if (!draft) return;
    const text = validation?.ok ? validation.canonical : JSON.stringify(draft.package, null, 2);
    const blob = new Blob([text], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = `${draft.package.package_id || 'workflow'}-${draft.package.version || 'draft'}.json`;
    link.click();
    URL.revokeObjectURL(url);
    setNotice({
      tone: 'neutral',
      text: validation?.ok
        ? 'Exported the canonical package JSON.'
        : 'Exported the draft as JSON. It does not pass the admission check yet.',
    });
  }
  async function openVersion(version: PackageVersion) {
    setPublishError(null);
    try {
      const content = await api<unknown>(
        `/packages/${encodeURIComponent(version.id)}/content`,
        workspace,
      );
      const read = readPackage(content);
      if (!read.package) throw new Error('The stored content does not match the local contract.');
      replaceDraft(
        draftFromPackage(read.package),
        `Opened ${version.package_id} ${version.version} as a new draft. A published version is immutable: changes need a new version number.`,
      );
    } catch (reason) {
      setVersionsError((reason as Error).message);
      setVersionsState('error');
    }
  }

  const header = (
    <SectionHeader
      eyebrow="Build"
      title="Studio"
      description="Compose a workflow package as a graph: where it starts, the agents, connectors and human reviews it passes through, and where it ends. The admission check mirrors the registry validator; publishing stores an immutable version. Nothing here runs a workflow."
      actions={
        draft ? (
          <>
            <button type="button" className="secondary" onClick={exportJson}>
              Export JSON
            </button>
            <button
              type="button"
              disabled={!validation?.ok || !projectId || !canWrite || publishing}
              title={
                !canWrite
                  ? 'Publishing needs editor access.'
                  : !projectId
                    ? 'Choose a project in the breadcrumb to publish.'
                    : !validation?.ok
                      ? 'Resolve the admission problems first.'
                      : undefined
              }
              onClick={() => void publish()}
            >
              {publishing ? 'Publishing…' : 'Publish version'}
            </button>
          </>
        ) : undefined
      }
    />
  );

  const noticeElement = notice && (
    <p className={notice.tone === 'success' ? 'success-notice' : 'notice'} role="status">
      {notice.text}
      {notice.undo && (
        <>
          {' '}
          <button
            type="button"
            className="text-button"
            onClick={() => {
              setDraft(notice.undo ?? null);
              setNotice(null);
              setSelection(null);
            }}
          >
            Restore the previous draft
          </button>
        </>
      )}
    </p>
  );
  const publishNote = !projectId
    ? 'Choose a project in the breadcrumb to publish. Drafts stay in this browser until then.'
    : !canWrite
      ? 'Publishing needs editor access. Drafts stay in this browser.'
      : 'Drafts stay in this browser until published.';

  if (draft === undefined) {
    return (
      <>
        {header}
        <p className="loading-state" role="status">
          Opening the Studio draft…
        </p>
      </>
    );
  }
  if (!draft) {
    return (
      <>
        {header}
        {noticeElement}
        <EmptyState
          glyph="network"
          title="Compose a workflow package"
          description="Start from the reviewed-action example — a connector read, an agent proposal, a human review and a connector write — or from a blank start and end."
          action={
            <div className="button-row">
              <button type="button" onClick={() => setDraft(exampleDraft())}>
                Start from the example
              </button>
              <button type="button" className="secondary" onClick={() => setDraft(emptyDraft())}>
                Start blank
              </button>
              <button
                type="button"
                className="secondary"
                onClick={() => fileInput.current?.click()}
              >
                Open JSON…
              </button>
            </div>
          }
        />
        <input
          ref={fileInput}
          type="file"
          accept="application/json,.json"
          className="sr-only"
          tabIndex={-1}
          aria-hidden="true"
          onChange={importFile}
        />
        {importError && <ImportAlert error={importError} onDismiss={() => setImportError(null)} />}
        <VersionsSection
          projectId={projectId}
          projectName={projectName}
          versions={versions}
          state={versionsState}
          error={versionsError}
          next={versionsNext}
          onRetry={() => setVersionsRefresh((value) => value + 1)}
          onMore={() => void loadVersions(new AbortController().signal, versionsNext ?? undefined)}
          onOpen={(version) => void openVersion(version)}
        />
      </>
    );
  }

  return (
    <>
      {header}
      {noticeElement}
      {importError && <ImportAlert error={importError} onDismiss={() => setImportError(null)} />}
      {publishError && (
        <Alert>
          {publishError.text}
          {publishError.problems.length > 0 && (
            <ul className="studio-problem-list">
              {publishError.problems.slice(0, 8).map((problem, index) => (
                <li key={index}>
                  <span className="studio-problem-location">
                    {problemLocation({ ...problem, nodes: [] })}
                  </span>{' '}
                  {problem.message}
                </li>
              ))}
            </ul>
          )}
        </Alert>
      )}
      <div className="studio-toolbar">
        <button
          type="button"
          className="secondary"
          onClick={() => change((current) => ({ ...current, layout: autoLayout(current.package) }))}
        >
          Tidy layout
        </button>
        <button type="button" className="secondary" onClick={() => fileInput.current?.click()}>
          Open JSON…
        </button>
        <input
          ref={fileInput}
          type="file"
          accept="application/json,.json"
          className="sr-only"
          tabIndex={-1}
          aria-hidden="true"
          onChange={importFile}
        />
        <button
          type="button"
          className="secondary"
          onClick={() =>
            replaceDraft(exampleDraft(), 'Replaced the draft with the reviewed-action example.')
          }
        >
          Use the example
        </button>
        <button
          type="button"
          className="text-button danger"
          onClick={() => {
            setNotice({ tone: 'neutral', text: 'Discarded the draft.', undo: draft });
            setDraft(null);
            setSelection(null);
            setConnecting(null);
          }}
        >
          Discard draft
        </button>
        <span className="studio-toolbar-note">{publishNote}</span>
      </div>
      <div className="studio-workbench">
        <StudioCanvas
          draft={draft}
          selected={selection}
          flagged={flagged}
          connecting={connecting}
          onSelect={select}
          onMove={(id: string, position: Position) =>
            change((current) => moveNode(current, id, position))
          }
          onConnect={(source: string, target: string) =>
            change((current) => addEdge(current, { source, target }))
          }
          onStartConnect={startConnect}
          onAdd={(kind: NodeKind, position?: Position) => {
            const id = nextNodeId(draft.package, kind);
            change((current) => addNode(current, kind, position));
            setSelection({ kind: 'node', id });
          }}
          onDelete={deleteSelection}
        />
        <StudioInspector
          draft={draft}
          selection={selection}
          problems={problems}
          onUpdateNode={(id: string, node: PackageNode) =>
            change((current) => updateNode(current, id, node))
          }
          onRenameNode={(id: string, next: string) => {
            change((current) => renameNode(current, id, next));
            setSelection({ kind: 'node', id: next });
          }}
          onDeleteSelection={deleteSelection}
          onAddEdge={(source: string, target: string) =>
            change((current) => addEdge(current, { source, target }))
          }
          onRemoveEdge={(source: string, target: string) =>
            change((current) => removeEdge(current, { source, target }))
          }
          onUpdatePackage={(patch: Partial<WorkflowPackage>) =>
            change((current) => updatePackage(current, patch))
          }
          onSelect={select}
        />
      </div>
      <section className="studio-check" aria-labelledby="studio-check-heading">
        <div className="section-row">
          <div>
            <p className="eyebrow">Admission check</p>
            <h2 id="studio-check-heading">
              {validation?.ok
                ? 'The package satisfies the v1 contract'
                : `${problems.length} ${problems.length === 1 ? 'problem' : 'problems'} to resolve`}
            </h2>
          </div>
          <span
            className={`verdict-stamp ${validation?.ok ? 'verdict-pass' : 'verdict-block'}`}
            aria-hidden="true"
          >
            {validation?.ok ? 'Admissible' : 'Not yet'}
          </span>
        </div>
        {validation?.ok ? (
          <dl className="studio-digest">
            <div>
              <dt>Content digest (SHA-256)</dt>
              <dd>
                <code>{digest || 'Computing…'}</code>
              </dd>
            </div>
            <div>
              <dt>Canonical size</dt>
              <dd>
                {new TextEncoder().encode(validation.canonical).length.toLocaleString()} bytes
              </dd>
            </div>
          </dl>
        ) : (
          <ul className="studio-problem-list">
            {problems.map((problem, index) => (
              <li key={`${problem.code}-${index}`}>
                <span className="studio-problem-location">{problemLocation(problem)}</span>{' '}
                {problem.message}
                {problem.nodes.length > 0 && (
                  <>
                    {' '}
                    <button
                      type="button"
                      className="text-button"
                      onClick={() => setSelection({ kind: 'node', id: problem.nodes[0] })}
                    >
                      Select {problem.nodes[0]}
                    </button>
                  </>
                )}
              </li>
            ))}
          </ul>
        )}
        <p className="footnote">
          This check mirrors the registry validator; the server decides admission when you publish.
          The digest identifies the content only — it is not a signature and proves neither
          publisher identity, approval, connector compatibility nor safe execution.
        </p>
      </section>
      <VersionsSection
        projectId={projectId}
        projectName={projectName}
        versions={versions}
        state={versionsState}
        error={versionsError}
        next={versionsNext}
        onRetry={() => setVersionsRefresh((value) => value + 1)}
        onMore={() => void loadVersions(new AbortController().signal, versionsNext ?? undefined)}
        onOpen={(version) => void openVersion(version)}
      />
    </>
  );
}

function ImportAlert({
  error,
  onDismiss,
}: {
  error: { text: string; problems: Problem[] };
  onDismiss: () => void;
}) {
  return (
    <Alert>
      {error.text}
      <button type="button" className="text-button" onClick={onDismiss}>
        Dismiss
      </button>
      {error.problems.length > 0 && (
        <ul className="studio-problem-list">
          {error.problems.slice(0, 8).map((problem, index) => (
            <li key={index}>
              <span className="studio-problem-location">{problemLocation(problem)}</span>{' '}
              {problem.message}
            </li>
          ))}
          {error.problems.length > 8 && (
            <li className="hint">{error.problems.length - 8} further problems.</li>
          )}
        </ul>
      )}
    </Alert>
  );
}

function VersionsSection({
  projectId,
  projectName,
  versions,
  state,
  error,
  next,
  onRetry,
  onMore,
  onOpen,
}: {
  projectId: string;
  projectName: string;
  versions: PackageVersion[];
  state: 'loading' | 'ready' | 'error';
  error: string;
  next: string | null;
  onRetry: () => void;
  onMore: () => void;
  onOpen: (version: PackageVersion) => void;
}) {
  return (
    <section className="studio-versions" aria-labelledby="studio-versions-heading">
      <div className="section-row">
        <div>
          <p className="eyebrow">Registry</p>
          <h2 id="studio-versions-heading">
            {projectId
              ? `Published versions in ${projectName || 'this project'}`
              : 'Published versions'}
          </h2>
        </div>
        {projectId && (
          <button type="button" className="secondary" onClick={onRetry}>
            Refresh
          </button>
        )}
      </div>
      {!projectId ? (
        <p className="notice">
          Choose a project in the breadcrumb to publish this draft and to list the versions it
          already holds.
        </p>
      ) : state === 'error' ? (
        <Alert onRetry={onRetry}>{error}</Alert>
      ) : state === 'loading' ? (
        <p className="loading-state" role="status">
          Loading published versions…
        </p>
      ) : !versions.length ? (
        <p className="hint">
          No versions have been published to this project yet. Each publication is immutable and
          recorded in the audit log.
        </p>
      ) : (
        <>
          <div className="table-wrap">
            <table>
              <caption className="sr-only">Published package versions</caption>
              <thead>
                <tr>
                  <th scope="col">Package</th>
                  <th scope="col">Version</th>
                  <th scope="col">Digest</th>
                  <th scope="col">Size</th>
                  <th scope="col">Validator</th>
                  <th scope="col">Published</th>
                  <th className="actions">
                    <span className="sr-only">Actions</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {versions.map((version) => (
                  <tr key={version.id}>
                    <td className="primary-cell">{version.package_id}</td>
                    <td className="cell-mono">{version.version}</td>
                    <td className="cell-mono" title={version.sha256}>
                      {version.sha256.slice(0, 12)}…
                    </td>
                    <td>{version.content_bytes.toLocaleString()} bytes</td>
                    <td className="cell-mono" title={version.validator_revision}>
                      {version.validator_revision.replace(/^.*validator\./, '')}
                    </td>
                    <td>
                      <RelativeTime value={version.created_at} />
                    </td>
                    <td className="actions">
                      <button
                        type="button"
                        className="text-button"
                        aria-label={`Open ${version.package_id} ${version.version} in Studio`}
                        onClick={() => onOpen(version)}
                      >
                        Open
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {next && (
            <button type="button" className="secondary" onClick={onMore}>
              Load more versions
            </button>
          )}
        </>
      )}
    </section>
  );
}
