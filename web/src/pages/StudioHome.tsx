import { useState, type ReactNode } from 'react';
import { SectionHeader } from '../components/ui.js';
import { StudioV2 } from './StudioV2.js';

type Mode = 'v2' | 'v1';
const MODE_KEY = 'lotuspond.studio.mode';

function recall(): Mode {
  try {
    return sessionStorage.getItem(MODE_KEY) === 'v1' ? 'v1' : 'v2';
  } catch {
    return 'v2';
  }
}

// The Studio authors executable-v2, the only format the engine runs (ADR-0013); the v1 graph
// stays until its retirement plan lands, labelled as such.
export function StudioHome({
  workspace,
  projectId,
  role,
  legacy,
}: {
  workspace: string;
  projectId: string;
  role: string;
  legacy: ReactNode;
}) {
  const [mode, setMode] = useState<Mode>(recall);
  function choose(next: Mode) {
    setMode(next);
    try {
      sessionStorage.setItem(MODE_KEY, next);
    } catch {
      /* Private browsers can refuse storage. */
    }
  }
  return (
    <div className="studio-home">
      <div className="studio-mode" role="tablist" aria-label="Package format">
        <button
          role="tab"
          aria-selected={mode === 'v2'}
          className={mode === 'v2' ? undefined : 'secondary'}
          onClick={() => choose('v2')}
        >
          Deployable workflow
        </button>
        <button
          role="tab"
          aria-selected={mode === 'v1'}
          className={mode === 'v1' ? undefined : 'secondary'}
          onClick={() => choose('v1')}
        >
          Legacy graph (v1)
        </button>
      </div>
      {mode === 'v2' ? (
        <>
          <SectionHeader
            eyebrow="Build"
            title="Studio"
            description="Author an executable-v2 workflow: the steps it runs, the systems it reads and writes, and its limits. The server checks every change; you sign it with your publisher key before it is published."
          />
          <StudioV2 workspace={workspace} projectId={projectId} role={role} />
        </>
      ) : (
        <>
          <p className="notice">
            The legacy graph publishes workflow-package v1, which the engine does not run. It stays
            until its retirement; author new workflows as deployable workflows.
          </p>
          {legacy}
        </>
      )}
    </div>
  );
}
