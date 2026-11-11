import { useEffect, useRef, useState, type ReactNode } from 'react';
import { Wordmark } from '../Wordmark.js';
import type { Account, Workspace } from '../../api.js';
import { initials } from '../../lib/format.js';
import type { SetupProgress } from '../../lib/setup.js';
import { Icon } from './icons.js';
import { NavList, accountPages } from './navigation.js';

// A button that reveals a small panel, closing on Escape (focus returns to the button), on an
// outside click, or when focus leaves it, so an open panel never covers what the keyboard
// reaches next. It is a disclosure, not an ARIA menu: every choice is a plain link or button
// reached with Tab.
function Disclosure({
  label,
  className,
  panelClassName,
  children,
}: {
  label: ReactNode;
  className: string;
  panelClassName: string;
  children: (close: () => void) => ReactNode;
}) {
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  const button = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (!open) return;
    const outside = (event: PointerEvent) => {
      if (!root.current?.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener('pointerdown', outside);
    return () => document.removeEventListener('pointerdown', outside);
  }, [open]);
  return (
    <div
      className="disclosure"
      ref={root}
      onKeyDown={(event) => {
        if (open && event.key === 'Escape') {
          // Handled here, so an enclosing menu does not also close.
          event.stopPropagation();
          setOpen(false);
          button.current?.focus();
        }
      }}
      onBlur={(event) => {
        // Only a move to another focused element closes it. Safari does not focus a clicked
        // button, so a click inside the panel blurs with no target and must not close it
        // before the click lands; outside clicks are handled by the pointer listener.
        const next = event.relatedTarget as Node | null;
        if (open && next && !root.current?.contains(next)) setOpen(false);
      }}
    >
      <button
        ref={button}
        type="button"
        className={className}
        aria-expanded={open}
        onClick={() => setOpen(!open)}
      >
        {label}
      </button>
      {open && <div className={panelClassName}>{children(() => setOpen(false))}</div>}
    </div>
  );
}

export function Sidebar({
  account,
  workspace,
  onWorkspace,
  page,
  hidden,
  navOpen,
  onToggleNav,
  onSearch,
  setup,
  onSignOut,
}: {
  account: Account;
  workspace?: Workspace;
  onWorkspace: (id: string) => void;
  page: string;
  hidden: readonly string[];
  navOpen: boolean;
  onToggleNav: () => void;
  onSearch: () => void;
  setup: SetupProgress;
  onSignOut: () => void;
}) {
  const kind = workspace?.kind === 'organization' ? 'Organization' : 'Personal';
  const toggle = useRef<HTMLButtonElement>(null);
  return (
    <aside className="sidebar">
      <div className="sidebar-brand">
        <Wordmark href="#home" />
        <button
          ref={toggle}
          className="mobile-nav-toggle secondary"
          aria-expanded={navOpen}
          aria-controls="sidebar-panel"
          onClick={onToggleNav}
        >
          <Icon name={navOpen ? 'close' : 'menu'} />
          {navOpen ? 'Close' : 'Menu'}
        </button>
      </div>

      {/* On narrow screens this is the menu the toggle opens; Escape closes it. */}
      <div
        className="sidebar-body"
        id="sidebar-panel"
        data-open={navOpen}
        onKeyDown={(event) => {
          if (navOpen && event.key === 'Escape') {
            onToggleNav();
            toggle.current?.focus();
          }
        }}
      >
        <Disclosure
          className="workspace-switch"
          panelClassName="switch-panel"
          label={
            <>
              <span className="workspace-avatar" aria-hidden="true">
                {initials(workspace?.name || '?').slice(0, 1)}
              </span>
              <span className="workspace-name">
                <strong>{workspace?.name || 'No workspace'}</strong>
                <small>
                  {kind} · {workspace?.role}
                </small>
              </span>
              <Icon name="switch" />
              <span className="sr-only">Switch workspace</span>
            </>
          }
        >
          {(close) => (
            <>
              <p className="switch-heading">Workspaces</p>
              {account.workspaces.map((item) => (
                <button
                  key={item.id}
                  type="button"
                  className="switch-option"
                  aria-current={item.id === workspace?.id ? 'true' : undefined}
                  onClick={() => {
                    onWorkspace(item.id);
                    close();
                  }}
                >
                  <span className="workspace-avatar" aria-hidden="true">
                    {initials(item.name).slice(0, 1)}
                  </span>
                  <span>
                    {item.name}
                    <small>{item.kind === 'organization' ? 'Organization' : 'Personal'}</small>
                  </span>
                  {item.id === workspace?.id && <Icon name="check" />}
                </button>
              ))}
              <a className="switch-footer" href="#workspaces" onClick={close}>
                <Icon name="plus" />
                Create or manage workspaces
              </a>
            </>
          )}
        </Disclosure>

        <button
          type="button"
          className="sidebar-search"
          aria-keyshortcuts="Meta+K Control+K"
          onClick={onSearch}
        >
          <Icon name="search" />
          <span>Search or jump to…</span>
          <kbd aria-hidden="true">⌘K</kbd>
        </button>

        <nav id="main-navigation" aria-label="Main navigation">
          <NavList active={page} hidden={hidden} />
          {account.user.is_platform_admin && (
            <div className="nav-section" role="group" aria-labelledby="nav-platform">
              <p className="nav-heading" id="nav-platform">
                Platform
              </p>
              <a
                href="#admin"
                className={page === 'admin' ? 'nav-link active' : 'nav-link'}
                aria-current={page === 'admin' ? 'page' : undefined}
              >
                <Icon name="admin" />
                <span>Administration</span>
              </a>
            </div>
          )}
        </nav>

        {!setup.loading && setup.done < setup.total && page !== 'home' && (
          <a className="setup-mini" href="#home">
            <span>
              <strong>Finish setting up</strong>
              <small>
                {setup.done} of {setup.total} steps
              </small>
            </span>
            <span className="setup-meter" aria-hidden="true">
              <span style={{ width: `${(setup.done / setup.total) * 100}%` }} />
            </span>
          </a>
        )}

        <Disclosure
          className="account-switch"
          panelClassName="switch-panel switch-panel-up"
          label={
            <>
              <span className="account-avatar" aria-hidden="true">
                {initials(account.user.name)}
              </span>
              <span className="workspace-name">
                <strong>{account.user.name}</strong>
                <small>{account.user.email}</small>
              </span>
              <Icon name="switch" />
              <span className="sr-only">Account menu</span>
            </>
          }
        >
          {(close) => (
            <>
              {accountPages.map((item) => (
                <a key={item.id} className="switch-option" href={`#${item.id}`} onClick={close}>
                  <Icon name={item.icon} />
                  <span>{item.label}</span>
                </a>
              ))}
              <button
                type="button"
                className="switch-option"
                onClick={() => {
                  close();
                  onSignOut();
                }}
              >
                <Icon name="signout" />
                <span>Sign out</span>
              </button>
            </>
          )}
        </Disclosure>
      </div>
    </aside>
  );
}
