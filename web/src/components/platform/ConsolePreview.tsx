import { useRef, useState, type KeyboardEvent } from 'react';
import { useArrival } from '../../lib/useArrival.js';
import { Icon } from '../console/icons.js';
import { NavList } from '../console/navigation.js';
import { ReleaseView } from './ReleaseView.js';
import { EvidenceView, GateView, RunView } from './StreamViews.js';

const views = [
  {
    id: 'release',
    title: 'Release decision',
    summary: 'Change an outcome or the policy and watch the verdict follow.',
    path: 'releases',
    nav: 'releases',
  },
  {
    id: 'run',
    title: 'Evaluation run',
    summary: 'Results arrive from a runner on your side, one scenario at a time.',
    path: 'runs',
    nav: 'runs',
  },
  {
    id: 'evidence',
    title: 'Production evidence',
    summary: 'Traces arrive; an assessment that cannot run opens an incident.',
    path: 'traces',
    nav: 'traces',
  },
  {
    id: 'gate',
    title: 'Delivery gate',
    summary: 'The pipeline stops where the decision does.',
    path: 'releases / gate',
    nav: 'releases',
  },
] as const;
type ViewId = (typeof views)[number]['id'];

// A tour of the console with a sample workspace. It advances on its own while the reader
// watches, stops for good once they interact, and never advances under reduced motion.
export function ConsolePreview() {
  const [active, setActive] = useState<ViewId>('release');
  const [touring, setTouring] = useState(true);
  const [ref, arrived] = useArrival<HTMLDivElement>(0.35);
  const tabs = useRef<(HTMLButtonElement | null)[]>([]);
  const index = views.findIndex((view) => view.id === active);
  const current = views[index] ?? views[0];

  function choose(next: number, focus = false) {
    const view = views[(next + views.length) % views.length] ?? views[0];
    setActive(view.id);
    if (focus) tabs.current[views.indexOf(view)]?.focus();
  }
  function keys(event: KeyboardEvent) {
    const moves: Record<string, number> = {
      ArrowRight: index + 1,
      ArrowLeft: index - 1,
      Home: 0,
      End: views.length - 1,
    };
    const next = moves[event.key];
    if (next === undefined) return;
    event.preventDefault();
    setTouring(false);
    choose(next, true);
  }

  return (
    <div className="platform" ref={ref} data-touring={touring && arrived}>
      <div className="platform-tabs" role="tablist" aria-label="Platform views" onKeyDown={keys}>
        {views.map((view, position) => (
          <button
            key={view.id}
            ref={(element) => {
              tabs.current[position] = element;
            }}
            type="button"
            role="tab"
            id={`platform-tab-${view.id}`}
            aria-selected={view.id === active}
            aria-controls="platform-panel"
            tabIndex={view.id === active ? 0 : -1}
            onClick={() => {
              setTouring(false);
              choose(position);
            }}
          >
            <span className="platform-tab-title">{view.title}</span>
            <span className="platform-tab-summary">{view.summary}</span>
            {view.id === active && (
              <span className="platform-tab-progress" onAnimationEnd={() => choose(position + 1)} />
            )}
          </button>
        ))}
      </div>

      <div
        className="console-window"
        id="platform-panel"
        role="tabpanel"
        aria-labelledby={`platform-tab-${current.id}`}
        onPointerDown={() => setTouring(false)}
        onFocus={() => setTouring(false)}
      >
        <div className="console-chrome">
          <span className="console-dots" aria-hidden="true">
            <span />
            <span />
            <span />
          </span>
          <span className="console-path">lotuspond / example workspace / {current.path}</span>
          <span className="console-sample">Sample workspace · illustrative</span>
        </div>
        <div className="console-body">
          <div className="console-side" aria-hidden="true">
            <div className="sidebar-brand">
              <span className="wordmark">
                Lotuspond
                <span className="wordmark-dot" />
              </span>
            </div>
            <div className="workspace-switch">
              <span className="workspace-avatar">E</span>
              <span className="workspace-name">
                <strong>Example workspace</strong>
                <small>Organization · owner</small>
              </span>
              <Icon name="switch" />
            </div>
            <div className="sidebar-search">
              <Icon name="search" />
              <span>Search or jump to…</span>
              <kbd>⌘K</kbd>
            </div>
            <div className="console-nav">
              <NavList
                active={current.nav}
                interactive={false}
                hidden={[
                  'members',
                  'invitations',
                  'api-keys',
                  'integrations',
                  'audit-events',
                  'settings',
                ]}
              />
            </div>
          </div>
          <div className="preview-panel">
            <div className="topbar">
              <span className="crumbs">
                <span>Example workspace</span>
                <span className="crumb-divider">/</span>
                <span aria-current="page">Refund assistant</span>
              </span>
            </div>
            <div className="console-main" key={current.id}>
              {current.id === 'release' && <ReleaseView onInteract={() => setTouring(false)} />}
              {current.id === 'run' && <RunView running={arrived} />}
              {current.id === 'evidence' && <EvidenceView running={arrived} />}
              {current.id === 'gate' && <GateView running={arrived} />}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
