// The console's icon set: one 16-unit grid, a 1.5 stroke, round joins. Icons always sit
// beside a written label; they are never the only way to understand a control.
const paths = {
  home: 'M2.75 6.75 8 2.5l5.25 4.25V13a.5.5 0 0 1-.5.5H9.75V10h-3.5v3.5H3.25a.5.5 0 0 1-.5-.5Z',
  projects:
    'M2 4.5A1.5 1.5 0 0 1 3.5 3h2.6l1.4 1.5h5A1.5 1.5 0 0 1 14 6v5.5a1.5 1.5 0 0 1-1.5 1.5h-9A1.5 1.5 0 0 1 2 11.5Z',
  studio:
    'M2 3.5h4.5v4H2ZM9.5 8.5H14v4H9.5ZM6.5 5.5h1.4a1.6 1.6 0 0 1 1.6 1.6v1.8a1.6 1.6 0 0 0 1.6 1.6',
  start: 'M8 14A6 6 0 1 0 8 2a6 6 0 0 0 0 12ZM6.6 5.6v4.8l3.9-2.4Z',
  end: 'M8 14A6 6 0 1 0 8 2a6 6 0 0 0 0 12ZM6 6h4v4H6Z',
  agent: 'M7.5 3 8.9 6.6l3.6 1.4-3.6 1.4L7.5 13 6.1 9.4 2.5 8l3.6-1.4ZM12.8 2.2v2.2M11.7 3.3h2.2',
  scenarios: 'M7 4h6.5M7 8h6.5M7 12h6.5M2.5 4.1l1 1 1.6-1.9M2.5 8.1l1 1 1.6-1.9M2.5 12h2.6',
  environments: 'M8 2.2 2.2 5.2 8 8.2l5.8-3ZM2.2 8.1 8 11.1l5.8-3M2.2 11 8 14l5.8-3',
  runners:
    'M5 5h6v6H5ZM3 3h10v10H3ZM6.5 1.5V3M9.5 1.5V3M6.5 13v1.5M9.5 13v1.5M1.5 6.5H3M1.5 9.5H3M13 6.5h1.5M13 9.5h1.5',
  runs: 'M4.5 2.8v10.4L13 8Z',
  releases: 'M8 1.8 13 3.8v4c0 3-2.2 5.2-5 6.4-2.8-1.2-5-3.4-5-6.4v-4ZM5.6 8l1.7 1.7 3.2-3.3',
  policies: 'M4 1.8h5.3l2.9 3V14H4ZM9 1.8V5h3.2M6 8.2h4.4M6 10.8h4.4',
  approvals:
    'M6 7.2a2.4 2.4 0 1 0 0-4.8 2.4 2.4 0 0 0 0 4.8ZM1.8 13.5c.5-2.3 2.2-3.7 4.2-3.7s3.7 1.4 4.2 3.7M10.4 8.3l1.4 1.4 2.5-2.7',
  receipts: 'M3.5 1.8h9v12.4l-1.8-1.1-1.8 1.1-1.9-1.1-1.8 1.1-1.7-1.1ZM6 5.5h4.5M6 8.5h4.5',
  traces: 'M1.5 8h3l2-5 3 10 2-5h3',
  connections:
    'M6.8 9.2 9.2 6.8M7.4 4.6l1.3-1.3a2.6 2.6 0 0 1 3.7 3.7l-1.3 1.3M8.6 11.4l-1.3 1.3a2.6 2.6 0 0 1-3.7-3.7l1.3-1.3',
  control: 'M2.5 11.5a5.5 5.5 0 0 1 11 0M8 11.5l2.6-4M1.8 13.5h12.4',
  tasks: 'M2 9.5h3.5l1 1.8h3l1-1.8H14M2 9.5 3.8 3h8.4L14 9.5V13H2Z',
  license:
    'M2.5 2.5h11v8.5h-3M5 5.5h6M5 8h2.5M5.6 14V11.2M8.4 14V11.2M7 11.6a2 2 0 1 0 0-4 2 2 0 0 0 0 4Z',
  monitors:
    'M1.5 8S4 3.5 8 3.5 14.5 8 14.5 8 12 12.5 8 12.5 1.5 8 1.5 8ZM8 10a2 2 0 1 0 0-4 2 2 0 0 0 0 4Z',
  incidents: 'M8 2.2 14.3 13.5H1.7ZM8 6.4v3.2M8 11.6v.1',
  members:
    'M5.8 7a2.3 2.3 0 1 0 0-4.6 2.3 2.3 0 0 0 0 4.6ZM1.5 13.5c.4-2.2 2.1-3.6 4.3-3.6s3.9 1.4 4.3 3.6M10.8 2.6a2.3 2.3 0 0 1 0 4.3M12.2 9.9c1.2.5 2 1.8 2.3 3.6',
  invitations: 'M2 3.5h12v9H2ZM2.3 4 8 8.8 13.7 4',
  keys: 'M10 9.5A3.5 3.5 0 1 0 6.8 7.4L2 12.2V14h2.2v-1.5h1.5V11h1.5l1.1-1.1a3.5 3.5 0 0 0 1.7-.4ZM11 4.8v.1',
  integrations: 'M5.5 1.8v3M10.5 1.8v3M3.8 4.8h8.4v2.7a4.2 4.2 0 0 1-8.4 0ZM8 11.7v2.5',
  audit: 'M2.5 8a5.5 5.5 0 1 0 1.6-3.9M2.5 2.5v2.8h2.8M8 5v3.2l2.2 1.3',
  settings: 'M2.5 4.5h7M12 4.5h1.5M2.5 11.5H4M6.5 11.5h7M10.5 3v3M5.2 10v3',
  security: 'M8 1.8 13 3.8v4c0 3-2.2 5.2-5 6.4-2.8-1.2-5-3.4-5-6.4v-4Z',
  news: 'M8 1.8v2.6M8 11.6v2.6M1.8 8h2.6M11.6 8h2.6M3.6 3.6l1.8 1.8M10.6 10.6l1.8 1.8M12.4 3.6l-1.8 1.8M5.4 10.6l-1.8 1.8',
  workspaces: 'M3 14V2.5h7V14M10 6h3v8M5.5 5h2M5.5 7.5h2M5.5 10h2M1.5 14h13',
  admin: 'M8 1.8 13 3.8v4c0 3-2.2 5.2-5 6.4-2.8-1.2-5-3.4-5-6.4v-4ZM8 5.5v3.2M8 10.6v.1',
  search: 'M7 12A5 5 0 1 0 7 2a5 5 0 0 0 0 10ZM14 14l-3.5-3.5',
  switch: 'M5 6l3-3 3 3M5 10l3 3 3-3',
  check: 'M3 8.5 6.5 12 13 4.5',
  plus: 'M8 3v10M3 8h10',
  signout: 'M6 2.5H3v11h3M10.5 5l3 3-3 3M13.5 8H6',
  menu: 'M2 4h12M2 8h12M2 12h12',
  arrow: 'M3 8h10M9 4l4 4-4 4',
  close: 'M4 4l8 8M12 4l-8 8',
} as const;

export type IconName = keyof typeof paths;

export function Icon({ name, className = '' }: { name: IconName; className?: string }) {
  return (
    <svg
      className={`icon ${className}`}
      viewBox="0 0 16 16"
      width="16"
      height="16"
      aria-hidden="true"
      focusable="false"
    >
      <path
        d={paths[name]}
        fill="none"
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}
