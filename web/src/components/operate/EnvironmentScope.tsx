import { useEffect, useState, type ReactNode } from 'react';
import { api, type Row } from '../../api.js';
import { Alert, EmptyState } from '../ui.js';
import type { Scope } from '../../lib/operate.js';

const remembered = (projectId: string) => `lotuspond.operate.environment.${projectId}`;

function recall(projectId: string) {
  try {
    return sessionStorage.getItem(remembered(projectId)) ?? '';
  } catch {
    return '';
  }
}

function remember(projectId: string, environmentId: string) {
  try {
    sessionStorage.setItem(remembered(projectId), environmentId);
  } catch {
    /* Private browsers can disable storage; the picker still works for this visit. */
  }
}

// The Operate pages act in one environment of the project chosen in the top bar. The picker
// lists that project's environments and remembers the last one for this browser tab.
export function EnvironmentScope({
  workspace,
  projectId,
  children,
}: {
  workspace: string;
  projectId: string;
  children: (scope: Scope, picker: ReactNode) => ReactNode;
}) {
  const [environments, setEnvironments] = useState<Row[] | null>(null);
  const [error, setError] = useState('');
  const [environmentId, setEnvironmentId] = useState('');
  useEffect(() => {
    if (!projectId) return;
    const controller = new AbortController();
    setEnvironments(null);
    setError('');
    api<{ items: Row[] }>(
      `/environments?${new URLSearchParams({ project_id: projectId })}`,
      workspace,
      undefined,
      'GET',
      controller.signal,
    )
      .then((data) => {
        const items = data.items.filter((item) => item.project_id === projectId);
        setEnvironments(items);
        const kept = recall(projectId);
        setEnvironmentId(
          items.some((item) => item.id === kept)
            ? kept
            : ((items.find((item) => item.kind === 'production') ?? items[0])?.id ?? ''),
        );
      })
      .catch((reason) => {
        if (!controller.signal.aborted) setError((reason as Error).message);
      });
    return () => controller.abort();
  }, [workspace, projectId]);
  if (!projectId)
    return (
      <EmptyState
        glyph="enclosure"
        title="Choose a project"
        description="Pick a project in the top bar. Deployments, executions and tasks belong to one of its environments."
      />
    );
  if (error) return <Alert>{error}</Alert>;
  if (!environments)
    return (
      <p className="loading-state" role="status">
        Loading environments…
      </p>
    );
  if (!environments.length)
    return (
      <EmptyState
        glyph="enclosure"
        title="No environments in this project"
        description="Add an environment first. Work runs in one environment at a time."
        action={
          <a className="button-link" href="#environments">
            Open environments
          </a>
        }
      />
    );
  const picker = (
    <label className="operate-environment">
      <span>Environment</span>
      <select
        value={environmentId}
        onChange={(event) => {
          setEnvironmentId(event.target.value);
          remember(projectId, event.target.value);
        }}
      >
        {environments.map((item) => (
          <option key={item.id} value={item.id}>
            {String(item.name).toLowerCase() === String(item.kind)
              ? String(item.name)
              : `${String(item.name)} (${String(item.kind)})`}
          </option>
        ))}
      </select>
    </label>
  );
  return <>{children({ projectId, environmentId }, picker)}</>;
}
