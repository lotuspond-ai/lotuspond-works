import type { MouseEvent } from 'react';
import { Wordmark } from '../components/Wordmark.js';
import { ConsolePreview } from '../components/platform/ConsolePreview.js';
import { RecentlyShipped } from '../components/RecentlyShipped.js';
import { Alert } from '../components/ui.js';
import tangleToBloom from '../assets/illustrations/tangle-to-bloom.svg';
import oneThenTheRest from '../assets/illustrations/one-then-the-rest.svg';
import manyIntoOne from '../assets/illustrations/many-into-one.svg';
import inYourHands from '../assets/illustrations/in-your-hands.svg';
import theHandoff from '../assets/illustrations/the-handoff.svg';

// The signed-out introduction, drawn in the LotusPond design system (ADR-0016): a sage field,
// off-white sheets, one pine chapter, and tomato spent once per view and never on sage. Who
// the product is for follows ADR-0017; every status on the page traces to
// docs/CAPABILITIES.md, and moves only when that register does.

type AccountChoice = 'individual' | 'organization';

const contactEmail = 'hello@lotuspond.ai';

// The kinds of work LotusPond Works is being built for (ADR-0017). Direction, not a catalogue:
// none of these is offered as a ready-made workflow.
const areas = [
  [
    'Procurement',
    ['Purchase order follow-ups', 'Supplier confirmations', 'Price and quantity checks'],
  ],
  [
    'Inventory',
    ['Stock reconciliation across sites', 'Reorder proposals', 'Discrepancy investigations'],
  ],
  ['Logistics', ['Shipment tracking and exceptions', 'Carrier rebooking', 'Delivery date updates']],
  ['Orders', ['Order entry from email and portals', 'Order status replies', 'Returns and claims']],
  [
    'Suppliers',
    ['Onboarding documents', 'Certificate and contract renewals', 'Performance summaries'],
  ],
  [
    'Finance operations',
    ['Invoice matching', 'Payment holds and credit notes', 'Exception reports'],
  ],
] as const;

// Systems are named by kind, never by vendor, until an integration is live (ADR-0017).
const systems = [
  'ERP',
  'Warehouse management',
  'Transport management',
  'Order management',
  'Carrier sites',
  'Supplier portals',
  'EDI',
  'Shared inboxes',
  'Spreadsheets',
  'Accounting',
  'Document stores',
] as const;

type Readiness = 'available' | 'in-progress';
const readinessLabel: Record<Readiness, string> = {
  available: 'Available',
  'in-progress': 'In progress',
};

// The core loop of docs/GOAL.md. Each "today" line traces to its row in docs/CAPABILITIES.md.
const loop = [
  [
    'Build',
    'in-progress',
    'A workflow package declares the systems a process reads and writes, the agent steps with their hard limits, and the points where a person decides.',
    'Today the SDK validates, signs and compiles packages, and the Studio composes the earlier package format.',
  ],
  [
    'Prove',
    'available',
    'Each version runs against your scenarios before release, and a release needs a decision under an immutable policy.',
    'Today: scenarios, evaluation runs, release policies, approval by a second administrator and the release gate.',
  ],
  [
    'Run',
    'in-progress',
    'A durable engine finishes the work or records exactly where it stopped. Workers you enroll run each step inside your own environment.',
    'Today the engine and its runtime are tested on our own infrastructure. They are not yet offered as a service.',
  ],
  [
    'Control',
    'in-progress',
    'A consequential action waits until a named person approves exactly that action, and its outcome is verified against the system it changed.',
    'Today review tasks and exact-action approval are tested in the engine, and the console has a review inbox. Production use waits on the command key provider.',
  ],
] as const satisfies readonly (readonly [string, Readiness, string, string])[];

// Rules that hold in the API today (docs/CAPABILITIES.md, Assurance and Identity rows).
const rules = [
  [
    'Approval takes two people.',
    'The administrator who approves a release must be someone other than the one who asked.',
  ],
  [
    'A missing result is not a pass.',
    'A timeout, a crash or a result that never arrives is recorded as unknown, and unknown blocks the release.',
  ],
  [
    'Model judgments are advisory.',
    'An assessment can inform a decision. It never approves a release on its own.',
  ],
  [
    'Policies are immutable.',
    'Change a rule by creating a new policy. Earlier decisions keep the rule they were made under.',
  ],
] as const;

// The shape printed by scripts/cli.mjs; the digest is elided rather than invented.
const gateExample = [
  ['command', 'npm run cli -- gate "$RELEASE_ID"'],
  [
    'output',
    '{\n  "verdict": "block",\n  "reasons": [\n    "A critical scenario failed or has no conclusive result."\n  ],\n  "evidenceSha256": "…"\n}',
  ],
  ['command', 'echo $?'],
  ['output', '2'],
] as const;

const navigation = [
  ['automate', 'What it automates'],
  ['how', 'How it works'],
  ['control', 'Control'],
  ['accounts', 'Accounts'],
] as const;

function jumpTo(event: MouseEvent<HTMLAnchorElement>, id: string) {
  const target = document.getElementById(id);
  if (!target) return;
  // The console routes on the URL hash, so in-page links scroll without changing it.
  event.preventDefault();
  const reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  target.scrollIntoView({ behavior: reduce ? 'auto' : 'smooth', block: 'start' });
  target.querySelector<HTMLElement>('h1, h2')?.focus({ preventScroll: true });
}

// The hand-drawn underline of the design system. On the sage field it is off-white; on a
// sheet or the pine chapter it is the view's one tomato mark.
function Underline({ children, draw = false }: { children: string; draw?: boolean }) {
  return (
    <span className="home-u" data-draw={draw}>
      {children}
      <svg viewBox="0 0 200 20" preserveAspectRatio="none" aria-hidden="true">
        <path pathLength={1} d="M3 12C38 4 86 17 128 9s52-3 69 2" />
      </svg>
    </span>
  );
}

export function Landing({
  error,
  onCreateAccount,
  onSignIn,
}: {
  error: string;
  onCreateAccount: (choice?: AccountChoice) => void;
  onSignIn: () => void;
}) {
  return (
    <div className="home">
      <a className="home-skip" href="#overview" onClick={(event) => jumpTo(event, 'overview')}>
        Skip to content
      </a>
      <header className="home-nav">
        <div className="home-wrap home-nav-in">
          <Wordmark href="/" />
          <nav className="home-nav-links" aria-label="Sections">
            {navigation.map(([id, name]) => (
              <a key={id} href={`#${id}`} onClick={(event) => jumpTo(event, id)}>
                {name}
              </a>
            ))}
          </nav>
          <div className="home-nav-actions">
            <button className="home-btn home-btn-quiet home-btn-sm" onClick={onSignIn}>
              Sign in
            </button>
            <button className="home-btn home-btn-ink home-btn-sm" onClick={() => onCreateAccount()}>
              Create account
            </button>
          </div>
        </div>
      </header>

      <main id="overview">
        <section className="home-hero" aria-labelledby="landing-title">
          <div className="home-wrap home-hero-grid">
            <div>
              <p className="home-kicker">
                <b>LotusPond Works</b>
                <span className="home-tag home-tag-live">Early access</span>
              </p>
              <h1 id="landing-title" className="home-d-xl" tabIndex={-1}>
                AI agents for supply chain <Underline draw>operations.</Underline>
              </h1>
              <p className="home-lede">
                LotusPond Works connects the systems a process runs on and puts AI agents to work on
                the manual steps between them. A person approves every action that matters, and
                every outcome is recorded.
              </p>
              {error && <Alert>{error}</Alert>}
              <div className="home-cta">
                <button className="home-btn home-btn-ink" onClick={() => onCreateAccount()}>
                  Create account <span aria-hidden="true">→</span>
                </button>
                <button className="home-btn home-btn-light" onClick={onSignIn}>
                  Sign in
                </button>
              </div>
              <dl className="home-status">
                <div>
                  <dt>Available now</dt>
                  <dd>Agent testing, release decisions and a Studio for workflow packages</dd>
                </div>
                <div>
                  <dt>In progress</dt>
                  <dd>The workflow engine, connections and actions a person approves</dd>
                </div>
              </dl>
            </div>
            <img
              className="home-illo home-hero-illo"
              src={tangleToBloom}
              alt=""
              width={480}
              height={360}
            />
          </div>
        </section>

        <section id="automate" className="home-sheet" aria-labelledby="automate-title">
          <div className="home-wrap">
            <div className="home-head home-head-illo">
              <div>
                <h2 id="automate-title" className="home-d-lg" tabIndex={-1}>
                  Start with one process. Add the rest.
                </h2>
                <p className="home-lede">
                  LotusPond Works is not built for a single task. Supply chain operations come
                  first. LotusPond sets up your first processes with you, and your own builders can
                  add more on the same connections.
                </p>
              </div>
              <img
                className="home-illo"
                src={oneThenTheRest}
                alt=""
                width={480}
                height={360}
                loading="lazy"
              />
            </div>
            <p className="home-label">The work it is being built for</p>
            <div className="home-areas">
              {areas.map(([title, items]) => (
                <div key={title}>
                  <h3 className="home-d-sm">{title}</h3>
                  <ul>
                    {items.map((item) => (
                      <li key={item}>{item}</li>
                    ))}
                  </ul>
                </div>
              ))}
            </div>
            <p className="home-aside">
              These are directions, not ready-made workflows: each process is built for the customer
              who needs it and proven before it runs. Healthcare administration, such as referral
              intake and appointment operations, is built the same way. Diagnosis, treatment and
              clinical decision support are out of scope.
            </p>
          </div>
        </section>

        <section className="home-band" aria-labelledby="systems-title">
          <div className="home-wrap home-band-grid">
            <div>
              <h2 id="systems-title" className="home-d-lg">
                It connects to what you already run.
              </h2>
              <p className="home-lede">
                Agents work in the systems you have, so nothing needs replacing. A connection stays
                unverified until it is proven against your system, and credentials are resolved only
                when a step runs.
              </p>
              <ul className="home-chips" aria-label="Kinds of systems">
                {systems.map((system) => (
                  <li key={system}>{system}</li>
                ))}
              </ul>
            </div>
            <img
              className="home-illo"
              src={manyIntoOne}
              alt=""
              width={480}
              height={360}
              loading="lazy"
            />
          </div>
        </section>

        <section id="how" className="home-sheet" aria-labelledby="how-title">
          <div className="home-wrap">
            <div className="home-head">
              <h2 id="how-title" className="home-d-lg" tabIndex={-1}>
                Build. Prove. Run. Control.
              </h2>
              <p className="home-lede">
                Every process follows the same four steps. The tags say what each step does today.
              </p>
            </div>
            <ol className="home-steps">
              {loop.map(([name, state, copy, today]) => (
                <li key={name}>
                  <div className="home-step-head">
                    <h3 className="home-d-sm">{name}</h3>
                    <span
                      className={`home-tag home-tag-${state === 'available' ? 'live' : 'soon'}`}
                    >
                      {readinessLabel[state]}
                    </span>
                  </div>
                  <p>{copy}</p>
                  <p className="home-today">{today}</p>
                </li>
              ))}
            </ol>
            <div className="home-prove">
              <div className="home-head">
                <h3 className="home-d-md">Try the release rules.</h3>
                <p className="home-lede">
                  A sample workspace running the LotusPond Works release rules in your browser.
                  Change an outcome or the policy and the verdict follows, with the reasons the
                  server records.
                </p>
              </div>
              <ConsolePreview />
            </div>
          </div>
        </section>

        <section id="control" className="home-sheet home-deep" aria-labelledby="control-title">
          <div className="home-wrap">
            <div className="home-head">
              <h2 id="control-title" className="home-d-lg" tabIndex={-1}>
                Built so your team stays in <Underline>charge.</Underline>
              </h2>
              <p className="home-lede">
                These rules hold in the API today, not only in the console, and every workspace
                change records who made it and when.
              </p>
            </div>
            <div className="home-trust">
              <img
                className="home-illo"
                src={inYourHands}
                alt=""
                width={480}
                height={360}
                loading="lazy"
              />
              <div className="home-four">
                {rules.map(([title, copy]) => (
                  <div key={title}>
                    <h3 className="home-d-sm">{title}</h3>
                    <p>{copy}</p>
                  </div>
                ))}
              </div>
            </div>
            <div className="home-gate">
              <div>
                <h3 className="home-d-sm">Make the pipeline obey the decision.</h3>
                <p>
                  The release gate prints the verdict, its reasons and the evidence digest. Anything
                  other than a pass exits with status 2, so a deployment stops where the evidence
                  does.
                </p>
              </div>
              <pre
                className="home-code"
                role="region"
                aria-label="Release gate in a delivery pipeline"
                tabIndex={0}
              >
                <code>
                  {gateExample.map(([kind, text], index) => (
                    <span key={index} className={`home-code-${kind}`}>
                      {kind === 'command' && <span aria-hidden="true">$ </span>}
                      {text}
                      {'\n'}
                    </span>
                  ))}
                </code>
              </pre>
            </div>
          </div>
        </section>

        <section id="accounts" className="home-sheet" aria-labelledby="accounts-title">
          <div className="home-wrap">
            <div className="home-head">
              <h2 id="accounts-title" className="home-d-lg" tabIndex={-1}>
                Start on your own. Add a team deliberately.
              </h2>
              <p className="home-lede">
                Every account has a personal workspace. An organization is a separate choice, with
                its own members, roles and policies.
              </p>
            </div>
            <ul className="home-accounts">
              <li>
                <p className="home-label">Individual</p>
                <h3 className="home-d-sm">A personal workspace</h3>
                <p>
                  Your projects, runners and keys in one place. It stays yours when you later join
                  or create an organization.
                </p>
                <button
                  className="home-btn home-btn-outline"
                  onClick={() => onCreateAccount('individual')}
                >
                  Create a personal account <span aria-hidden="true">→</span>
                </button>
              </li>
              <li>
                <p className="home-label">Organization</p>
                <h3 className="home-d-sm">A shared workspace</h3>
                <p>Roles, invitations, scoped API keys and an audit log of every change.</p>
                <button
                  className="home-btn home-btn-outline"
                  onClick={() => onCreateAccount('organization')}
                >
                  Create an organization <span aria-hidden="true">→</span>
                </button>
              </li>
            </ul>
            <RecentlyShipped />
          </div>
        </section>

        <section className="home-close" aria-labelledby="closing-title">
          <div className="home-wrap">
            <h2 id="closing-title" className="home-d-xl">
              Pick one process. See it <Underline>run.</Underline>
            </h2>
            <p className="home-lede">
              Tell us which job costs your team the most time. We map it with you and build it on
              your own systems.
            </p>
            <div className="home-cta">
              <button className="home-btn home-btn-ink" onClick={() => onCreateAccount()}>
                Create account <span aria-hidden="true">→</span>
              </button>
              <a className="home-contact" href={`mailto:${contactEmail}`}>
                {contactEmail}
              </a>
            </div>
            <img
              className="home-illo home-pano"
              src={theHandoff}
              alt=""
              width={1200}
              height={300}
              loading="lazy"
            />
          </div>
        </section>
      </main>

      <footer className="home-footer">
        <div className="home-wrap home-foot">
          <Wordmark />
          <span>
            The product window on this page is a labelled sample workspace, not customer data.
          </span>
          <a href={`mailto:${contactEmail}`}>{contactEmail}</a>
        </div>
      </footer>
    </div>
  );
}
