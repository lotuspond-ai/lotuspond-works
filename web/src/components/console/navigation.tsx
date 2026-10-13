import { useId } from 'react';
import { Icon, type IconName } from './icons.js';

export type NavItem = { id: string; label: string; icon: IconName };
export type NavSection = { title?: string; items: readonly NavItem[] };

// Ordered the way a new workspace is set up: build what will be tested, evaluate it, watch
// production, then the workspace utilities. Labels are one or two words, keyword first.
export const navigation: readonly NavSection[] = [
  { items: [{ id: 'home', label: 'Home', icon: 'home' }] },
  {
    title: 'Build',
    items: [
      { id: 'projects', label: 'Projects', icon: 'projects' },
      { id: 'scenarios', label: 'Scenarios', icon: 'scenarios' },
      { id: 'environments', label: 'Environments', icon: 'environments' },
      { id: 'runners', label: 'Runners', icon: 'runners' },
    ],
  },
  {
    title: 'Evaluate',
    items: [
      { id: 'runs', label: 'Evaluation runs', icon: 'runs' },
      { id: 'releases', label: 'Releases', icon: 'releases' },
      { id: 'policies', label: 'Policies', icon: 'policies' },
      { id: 'approvals', label: 'Approvals', icon: 'approvals' },
      { id: 'receipts', label: 'Receipts', icon: 'receipts' },
    ],
  },
  {
    title: 'Production',
    items: [
      { id: 'traces', label: 'Traces', icon: 'traces' },
      { id: 'monitors', label: 'Monitors', icon: 'monitors' },
      { id: 'incidents', label: 'Incidents', icon: 'incidents' },
      { id: 'voice-sessions', label: 'Voice sessions', icon: 'voice' },
    ],
  },
  {
    title: 'Workspace',
    items: [
      { id: 'members', label: 'Members', icon: 'members' },
      { id: 'invitations', label: 'Invitations', icon: 'invitations' },
      { id: 'api-keys', label: 'API keys', icon: 'keys' },
      { id: 'integrations', label: 'Integrations', icon: 'integrations' },
      { id: 'audit-events', label: 'Audit log', icon: 'audit' },
      { id: 'settings', label: 'Settings', icon: 'settings' },
    ],
  },
];

// Pages reached from the account menu rather than the sidebar.
export const accountPages: readonly NavItem[] = [
  { id: 'account-security', label: 'Account security', icon: 'security' },
  { id: 'workspaces', label: 'Your workspaces', icon: 'workspaces' },
  { id: 'changelog', label: 'What’s new', icon: 'news' },
];

export const allPages: readonly (NavItem & { section: string })[] = [
  ...navigation.flatMap((section) =>
    section.items.map((item) => ({ ...item, section: section.title ?? 'Start' })),
  ),
  ...accountPages.map((item) => ({ ...item, section: 'Account' })),
];

export function pageLabel(id: string) {
  return allPages.find((item) => item.id === id)?.label;
}

// The sidebar's list, shared by the console (links) and the public preview (static rows).
export function NavList({
  active,
  hidden = [],
  interactive = true,
}: {
  active: string;
  hidden?: readonly string[];
  interactive?: boolean;
}) {
  const id = useId();
  return (
    <>
      {navigation.map((section, index) => {
        const items = section.items.filter((item) => !hidden.includes(item.id));
        if (!items.length) return null;
        // A titled section is a named group, so a screen reader announces "Build" on entry.
        const heading = section.title ? `${id}-${index}` : undefined;
        return (
          <div
            className="nav-section"
            key={section.title ?? index}
            role={heading ? 'group' : undefined}
            aria-labelledby={heading}
          >
            {section.title && (
              <p className="nav-heading" id={heading}>
                {section.title}
              </p>
            )}
            {items.map((item) => {
              const content = (
                <>
                  <Icon name={item.icon} />
                  <span>{item.label}</span>
                </>
              );
              const current = item.id === active;
              return interactive ? (
                <a
                  key={item.id}
                  href={`#${item.id}`}
                  className={current ? 'nav-link active' : 'nav-link'}
                  aria-current={current ? 'page' : undefined}
                >
                  {content}
                </a>
              ) : (
                <span key={item.id} className={current ? 'nav-link active' : 'nav-link'}>
                  {content}
                </span>
              );
            })}
          </div>
        );
      })}
    </>
  );
}
