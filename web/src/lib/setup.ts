import { useCallback, useEffect, useState } from 'react';
import { api } from '../api.js';

// The first-run path, in the order that gets a workspace to its first recorded release
// decision. Each step is complete once its records exist; nothing here is stored.
export const setupSteps = [
  {
    id: 'projects',
    title: 'Create a project',
    detail: 'A project is the agent workflow whose releases you decide.',
    action: 'Create project',
    admin: false,
  },
  {
    id: 'scenarios',
    title: 'Describe what good looks like',
    detail: 'Add scenarios with an input, the expected outcome, and whether a failure must block.',
    action: 'Add scenario',
    admin: false,
  },
  {
    id: 'runners',
    title: 'Connect a runner',
    detail: 'Register the worker that calls your agent and reports measured results.',
    action: 'Register runner',
    admin: false,
  },
  {
    id: 'policies',
    title: 'Set a release policy',
    detail: 'Choose the pass rate, regression, latency and approval rules a release must meet.',
    action: 'Create policy',
    admin: true,
  },
  {
    id: 'runs',
    title: 'Run an evaluation',
    detail: 'Freeze your scenarios for a revision and let the runner evaluate it.',
    action: 'Start a run',
    admin: false,
  },
  {
    id: 'releases',
    title: 'Make a release decision',
    detail: 'Compare the candidate with a baseline and record the verdict with its reasons.',
    action: 'Create release',
    admin: false,
  },
] as const;

export type StepState = 'done' | 'todo' | 'unknown';
export type SetupProgress = {
  states: Record<string, StepState>;
  done: number;
  total: number;
  loading: boolean;
  refresh: () => void;
};

export function summarize(states: Record<string, StepState>) {
  return setupSteps.filter((step) => states[step.id] === 'done').length;
}

// Asks each list for one record. A list the reader may not see stays unknown, never done.
export function useSetupProgress(workspace: string): SetupProgress {
  const [states, setStates] = useState<Record<string, StepState>>({});
  const [loading, setLoading] = useState(true);
  const [version, setVersion] = useState(0);
  useEffect(() => {
    if (!workspace) return;
    const controller = new AbortController();
    setLoading(true);
    void Promise.all(
      setupSteps.map(async (step): Promise<[string, StepState]> => {
        try {
          const data = await api<{ items: unknown[] }>(
            `/${step.id}?limit=1`,
            workspace,
            undefined,
            'GET',
            controller.signal,
          );
          return [step.id, data.items.length ? 'done' : 'todo'];
        } catch {
          return [step.id, 'unknown'];
        }
      }),
    ).then((entries) => {
      if (controller.signal.aborted) return;
      setStates(Object.fromEntries(entries));
      setLoading(false);
    });
    return () => controller.abort();
  }, [workspace, version]);
  const refresh = useCallback(() => setVersion((value) => value + 1), []);
  return { states, done: summarize(states), total: setupSteps.length, loading, refresh };
}

// A one-shot request, made from Home or the command palette, to open a page's create form.
let pendingCreate: string | null = null;
export function requestCreate(page: string) {
  pendingCreate = page;
  location.hash = page;
}
export function takeCreateRequest(page: string) {
  if (pendingCreate !== page) return false;
  pendingCreate = null;
  return true;
}
