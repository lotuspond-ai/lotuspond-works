import { useEffect, useRef, useState, type MouseEvent } from 'react';
import { Pond, type PadLayout } from '../components/Pond.js';
import { InkGlyph } from '../components/InkGlyph.js';
import { PondPlate } from '../components/PondPlate.js';
import { PondWorkflow } from '../components/PondWorkflow.js';
import { ConsolePreview } from '../components/platform/ConsolePreview.js';
import { RecentlyShipped } from '../components/RecentlyShipped.js';
import { ThreadedSteps } from '../components/ThreadedSteps.js';
import { Alert, StatusBadge } from '../components/ui.js';
import { useArrival } from '../lib/useArrival.js';

type AccountChoice = 'individual' | 'organization';

// Set at build time for the hosted introduction; an installation built without it shows no
// contact action rather than an invented address.
const contactEmail = String(import.meta.env.VITE_LOTUSPOND_CONTACT_EMAIL ?? '').trim();

// The hero keeps the right of the water clear for the drawn workflow.
const heroPads: PadLayout = [
  [0.03, 0.82, 0.085],
  [0.97, 0.94, 0.07],
];

// The synthetic package in contracts/examples/reviewed-action.v1.json; the instruction is
// quoted from it and the limits are its declared values.
const packageNodes = [
  ['read', 'connector', 'Reads the case through synthetic-case-system 1.0.0.', ''],
  [
    'propose',
    'agent',
    'Propose a synthetic case update for a person to review.',
    '5 steps · 60 s · 4,000 input and 1,000 output tokens · cost ceiling',
  ],
  ['review', 'human review', 'A person reviews the proposal before anything is written.', ''],
  ['write', 'connector', 'Writes the case through the same connector.', ''],
] as const;

// Quoted from ERROR_MESSAGES in sdk/python/src/lotuspond_sdk/contract.py; keep them identical.
const packageRejections = [
  'An agent references an undeclared model.',
  'A node operation is not declared by its connector.',
  'The package graph must be acyclic.',
] as const;

const engagements = [
  [
    'solo',
    'On your own',
    'Install Lotuspond on your infrastructure and build with your own engineers. Nothing in the product needs our consultants to operate it.',
  ],
  [
    'together',
    'With our team',
    'We assess your processes, implement the first automations alongside your people, and can operate them for you afterward.',
  ],
  [
    'relay',
    'Through a partner',
    'We train and equip the implementation partner you choose, so they deliver on the same platform and methods we use.',
  ],
] as const;

const services = [
  [
    'Readiness assessment',
    'We inventory your processes, rank them by value and risk, and check data, documents, systems and permissions to decide where rules, an agent, a person or a mix belongs.',
    'Readiness scorecard, opportunity backlog, process maps and a phased plan',
  ],
  [
    'Implementation',
    'We design the process, build its connections and workflow package, test it with recorded evidence, deploy it, and hand it over with named owners.',
    'Process design, connections, package, test evidence and handover',
  ],
  [
    'Managed operations',
    'We watch automations in production, respond to incidents, manage capacity, and turn exceptions into tested improvements under agreed change control.',
    'Monitoring, incident response, approved changes and improvement reviews',
  ],
  [
    'Training and enablement',
    'We train your administrators, developers and reviewers, and give implementation partners the guides and methods we work by.',
    'Trained teams and a repeatable implementation method',
  ],
] as const;
const serviceGlyphs = ['survey', 'package', 'pulse', 'handover'] as const;

const readiness = [
  'Choose where rules, an agent, a person, or a combination belongs.',
  'Name process owners, inputs, decision rules and exception routes.',
  'Assess data quality, documents, identity, APIs, permissions and auditability.',
  'Build permitted integrations, typed tool contracts and governed knowledge.',
  'Introduce human review, incident handling and release practice.',
  'Train people, then measure adoption, outcomes and running costs.',
] as const;

type Readiness = 'available' | 'in-progress' | 'planned';
const readinessLabel: Record<Readiness, string> = {
  available: 'Available',
  'in-progress': 'In progress',
  planned: 'Planned',
};

// Each status traces to docs/CAPABILITIES.md; move a row only when that register moves it.
const register = [
  [
    'Build',
    [
      [
        'Workflow packages',
        'in-progress',
        'A strict, versioned declaration of connectors, agents and human review steps. The SDK validates and signs it locally, and each workspace keeps published versions in an immutable registry.',
      ],
      [
        'Studio',
        'in-progress',
        'Compose a package on a canvas, check it in the browser against the same rules as the SDK, and publish it to the registry. Authoring executable workflows is planned.',
      ],
      [
        'Connections and knowledge',
        'in-progress',
        'Connection configurations and secret references, managed by owners and administrators through an opt-in API. Using them in running workflows, and versioned knowledge sources, are planned.',
      ],
    ],
  ],
  [
    'Operate',
    [
      [
        'Control Room',
        'planned',
        'Publish versions and manage environments, deployments, workers, queues and schedules.',
      ],
      [
        'Execution',
        'planned',
        'Durable runs that combine agent steps, deterministic actions and waits for people, on workers you control.',
      ],
      [
        'Business operations',
        'planned',
        'Cases, exceptions and human tasks, with each outcome linked to the version that produced it.',
      ],
    ],
  ],
  [
    'Assure',
    [
      [
        'Assurance',
        'available',
        'Frozen scenarios, evaluation runs for agents behind HTTP, immutable release policies and independent approval. A deployment needs a current release decision.',
      ],
    ],
  ],
  [
    'Administer',
    [
      [
        'Identity and access',
        'available',
        'Personal and organization workspaces, roles, invitations, scoped API keys, authenticator MFA and audit records. Licensing and delegated service access are planned.',
      ],
    ],
  ],
] as const satisfies readonly (readonly [
  string,
  readonly (readonly [string, Readiness, string])[],
])[];

const controls = [
  [
    'Model judgments are advisory.',
    'An assessment can inform a decision. It never approves a release or authorizes a tool.',
  ],
  [
    'Approval takes two people.',
    'The administrator who approves a release must be someone other than the one who asked.',
  ],
  [
    'A missing result is not a pass.',
    'A timeout, a crash, malformed evidence or a result that never arrives is recorded as unknown, and unknown blocks the release.',
  ],
  [
    'Policies are immutable.',
    'Change a rule by creating a new policy. Earlier decisions keep the rule they were made under.',
  ],
  [
    'Registered is not connected.',
    'An integration stays unverified until its connection is independently proven.',
  ],
  [
    'Evidence leaves with its digest.',
    'Release evidence exports as canonical JSON with a SHA-256 digest anyone can recompute.',
  ],
] as const;

// The workflow families in docs/plans/2026-10-13-platform-master-plan.md, section 6.
const families = [
  [
    'documents',
    'Document intake and validation',
    'Orders, invoices, forms and claims intake.',
    'Extraction and validation accuracy, source references and exception outcomes.',
  ],
  [
    'knowledge',
    'Knowledge and assistance',
    'Internal support and guided operations.',
    'Access-correct retrieval, source freshness and answer quality.',
  ],
  [
    'transactions',
    'Business-system transactions',
    'CRM and ERP updates, reconciliations and service requests.',
    'Authorization, deduplication, downstream confirmation and recovery.',
  ],
  [
    'cases',
    'Case and approval processes',
    'Procurement, onboarding and referral administration.',
    'Long-running state, escalation, deadlines and independent review.',
  ],
  [
    'voice',
    'Voice and conversation',
    'Scheduling, intake and support.',
    'Real audio, provider and channel tests, and handoff to a person.',
  ],
  [
    'legacy',
    'Browser, desktop and legacy systems',
    'Authorized systems that have no suitable API.',
    'Isolated sessions, credential handling and recovery from interface changes.',
  ],
] as const;

const deployments = [
  [
    'The service',
    'One Compose file runs the API and console, the identity-mail worker, PostgreSQL and Redis, on a host you choose.',
  ],
  [
    'Your side',
    'A separate nonroot image runs the HTTP evaluation runner and the release gate beside your agents, with no built-in credentials.',
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

const deploymentGlyphs = ['network', 'enclosure'] as const;
const numerals = ['I', 'II', 'III', 'IV', 'V', 'VI'];

// Page order drives the contents list, the section numbers, and the header links.
const sections = [
  ['engage', 'Engagement', '3 paths'],
  ['services', 'Services', '4 services'],
  ['platform', 'Platform', '8 parts'],
  ['control', 'Human control', '6 rules'],
  ['workflows', 'Workflows', '6 families'],
  ['deployment', 'Deployment', '3 modes'],
  ['accounts', 'Accounts', '2 paths'],
] as const;
type SectionId = (typeof sections)[number][0];
const headerLinks: SectionId[] = ['services', 'platform', 'workflows', 'deployment'];

function jumpTo(event: MouseEvent<HTMLAnchorElement>, id: string) {
  const target = document.getElementById(id);
  if (!target) return;
  // The console routes on the URL hash, so in-page links scroll without changing it.
  event.preventDefault();
  const reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  target.scrollIntoView({ behavior: reduce ? 'auto' : 'smooth', block: 'start' });
  target.querySelector<HTMLElement>('h1, h2')?.focus({ preventScroll: true });
}

function Rail({ id }: { id: SectionId }) {
  const index = sections.findIndex(([key]) => key === id);
  return (
    <p className="section-rail">
      <span>({index + 1})</span>
      {sections[index]?.[1]}
    </p>
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
  const [header, setHeader] = useState({ scrolled: false, overPond: true });
  const [moving, setMoving] = useState(true);
  const surfacing = useRef<HTMLElement>(null);
  const record = useRef<HTMLElement>(null);
  const [stampTarget, stamped] = useArrival<HTMLElement>(0.5);
  useEffect(() => {
    const still = window.matchMedia('(prefers-reduced-motion: reduce)');
    let frame = 0;
    function update() {
      frame = 0;
      const scrolled = window.scrollY > 8;
      const overPond = [...document.querySelectorAll<HTMLElement>('.landing .pond')].some(
        (pond) => {
          const box = pond.getBoundingClientRect();
          return box.top < 36 && box.bottom > 36;
        },
      );
      setHeader((current) =>
        current.scrolled === scrolled && current.overPond === overPond
          ? current
          : { scrolled, overPond },
      );
      // The package record rises out of the water as the reader arrives at it.
      const section = surfacing.current;
      const card = record.current;
      if (section && card) {
        const view = window.innerHeight;
        const arrived = (view - section.getBoundingClientRect().top) / (view * 0.8);
        const lift = still.matches ? 0 : Math.round((1 - Math.min(1, Math.max(0, arrived))) * 56);
        card.style.setProperty('--lift', `${lift}px`);
      }
    }
    const request = () => {
      if (!frame) frame = requestAnimationFrame(update);
    };
    update();
    window.addEventListener('scroll', request, { passive: true });
    window.addEventListener('resize', request);
    return () => {
      cancelAnimationFrame(frame);
      window.removeEventListener('scroll', request);
      window.removeEventListener('resize', request);
    };
  }, []);
  return (
    <div className="landing">
      <a className="skip-link" href="#overview" onClick={(event) => jumpTo(event, 'overview')}>
        Skip to content
      </a>
      <header
        className="landing-header"
        data-scrolled={header.scrolled}
        data-tone={header.overPond ? 'pond' : 'page'}
      >
        <div className="landing-container landing-header-inner">
          <a className="wordmark" href="/">
            Lotuspond
            <span className="wordmark-dot" />
          </a>
          <nav className="landing-nav" aria-label="Overview sections">
            {headerLinks.map((id) => (
              <a key={id} href={`#${id}`} onClick={(event) => jumpTo(event, id)}>
                {sections.find(([key]) => key === id)?.[1]}
              </a>
            ))}
          </nav>
          <div className="landing-header-actions">
            <button className="text-button landing-signin" onClick={onSignIn}>
              Sign in
            </button>
            <button onClick={() => onCreateAccount()}>Create account</button>
          </div>
        </div>
      </header>

      <main id="overview">
        <Pond
          as="h1"
          id="landing-title"
          title="Agents at work, people in charge"
          className="pond-hero"
          pads={heroPads}
          paused={!moving}
          eyebrow={<p className="pond-eyebrow">Enterprise agent automation</p>}
        >
          <div className="pond-copy">
            {error && <Alert>{error}</Alert>}
            <p className="pond-lede">
              Lotuspond helps companies put AI agents to work in real business processes: rules
              where rules suffice, agents where judgment helps, and people where accountability
              belongs. Use our platform yourself, work with our team, or bring a partner.
            </p>
            <div className="landing-actions">
              <button onClick={() => onCreateAccount()}>
                Create account <span aria-hidden="true">→</span>
              </button>
              <a
                className="button-link"
                href="#services"
                onClick={(event) => jumpTo(event, 'services')}
              >
                Work with our team
              </a>
            </div>
            <dl className="pond-status">
              <div data-state="available">
                <dt>Available now</dt>
                <dd>Agent testing, release decisions and a Studio for workflow packages</dd>
              </div>
              <div data-state="planned">
                <dt>Planned</dt>
                <dd>Control Room and the production runtime</dd>
              </div>
            </dl>
          </div>
          <figure className="pond-flow-figure">
            <PondWorkflow paused={!moving} />
            <figcaption>
              <span>One workflow package, drawn on the water</span>
              <button
                type="button"
                className="text-button pond-motion"
                aria-pressed={!moving}
                onClick={() => setMoving((current) => !current)}
              >
                {moving ? 'Pause motion' : 'Play motion'}
              </button>
            </figcaption>
          </figure>
        </Pond>

        <section ref={surfacing} className="surfacing" aria-labelledby="package-title">
          <div className="landing-container surfacing-inner">
            <div className="surfacing-copy">
              <p className="eyebrow">How an automation is declared</p>
              <h2 id="package-title" className="section-title" tabIndex={-1}>
                One process, three kinds of work.
              </h2>
              <p className="section-lede">
                Systems do what rules can. An agent reasons where judgment helps, within limits you
                set. A person decides where accountability belongs. A Lotuspond workflow package
                declares all three, so what an automation may touch is known before it runs.
              </p>
              <nav className="contents" aria-labelledby="contents-title">
                <p id="contents-title">On this page</p>
                <ol>
                  {sections.map(([id, name, extent], index) => (
                    <li key={id}>
                      <a href={`#${id}`} onClick={(event) => jumpTo(event, id)}>
                        <span className="contents-number">({index + 1})</span>
                        <span className="contents-name">{name}</span>
                        <span className="contents-leader" aria-hidden="true" />
                        <span className="contents-extent">{extent}</span>
                      </a>
                    </li>
                  ))}
                </ol>
              </nav>
            </div>

            <figure
              ref={(element) => {
                record.current = element;
                stampTarget.current = element;
              }}
              className="decision-record surfacing-record"
              aria-labelledby="package-record-title"
            >
              <div className="decision-record-head">
                <div>
                  <p className="decision-record-kicker">
                    Workflow package ·{' '}
                    <span className="kicker-id">lotuspond.workflow-package.v1</span>
                  </p>
                  <h3 id="package-record-title">Synthetic reviewed action</h3>
                </div>
                <span className="record-stamp" data-stamped={stamped}>
                  Validated
                </span>
              </div>
              <ol className="decision-checks package-nodes" aria-label="Steps in order">
                {packageNodes.map(([id, kind, text, limits]) => (
                  <li key={id}>
                    <span className="check-name">
                      <code>{id}</code>
                    </span>
                    <span className="check-reason">
                      {text}
                      {limits && <span className="package-limits">{limits}</span>}
                    </span>
                    <span className="kind-tag" data-kind={kind.replace(' ', '-')}>
                      {kind}
                    </span>
                  </li>
                ))}
              </ol>
              <ul
                className="decision-checks decision-verdicts"
                aria-label="Declarations the validator rejects"
              >
                {packageRejections.map((reason) => (
                  <li key={reason}>
                    <span className="check-name">Rejects</span>
                    <span className="check-reason">{reason}</span>
                    <StatusBadge value="rejected" />
                  </li>
                ))}
              </ul>
              <figcaption>
                The synthetic example from the package contract, as the local validator accepts it.
                Validation checks the declaration and records a content digest; it does not run or
                approve the workflow.
              </figcaption>
            </figure>
          </div>
        </section>

        <div className="landing-container">
          <section id="engage" className="landing-section" aria-labelledby="engage-title">
            <Rail id="engage" />
            <div>
              <h2 id="engage-title" className="section-title" tabIndex={-1}>
                Use it yourself, with our team, or through a partner.
              </h2>
              <p className="section-lede">
                The platform is built to run without our consultants, and each service is scoped to
                stand on its own. Combine them in whatever way suits how your company builds.
              </p>
              <ul className="panel-grid">
                {engagements.map(([glyph, title, copy]) => (
                  <li key={title}>
                    <div className="panel-glyph">
                      <InkGlyph name={glyph} />
                    </div>
                    <h3>{title}</h3>
                    <p>{copy}</p>
                  </li>
                ))}
              </ul>
            </div>
          </section>

          <section id="services" className="landing-section" aria-labelledby="services-title">
            <Rail id="services" />
            <div>
              <h2 id="services-title" className="section-title" tabIndex={-1}>
                From first assessment to steady operation.
              </h2>
              <p className="section-lede">
                Start with an assessment and stop there if that is all you need, or carry on into
                implementation and operations. Each service has its own scope and deliverables.
              </p>
              <ThreadedSteps steps={services} glyphs={serviceGlyphs} retainedLabel="You receive" />
              <div className="gate readiness">
                <div>
                  <h3>What we mean by agent-ready.</h3>
                  <p>
                    Not a model added to a process. A company is ready when each automated process
                    has an owner, dependable inputs, permitted integrations, a review path and a way
                    to recover. A readiness score is a planning aid, not a compliance certification.
                  </p>
                  {contactEmail && (
                    <a className="button-link readiness-contact" href={`mailto:${contactEmail}`}>
                      Talk to our team <span aria-hidden="true">→</span>
                    </a>
                  )}
                </div>
                <ol className="readiness-list">
                  {readiness.map((item) => (
                    <li key={item}>{item}</li>
                  ))}
                </ol>
              </div>
            </div>
          </section>

          <section id="platform" className="landing-section" aria-labelledby="platform-title">
            <Rail id="platform" />
            <div>
              <h2 id="platform-title" className="section-title" tabIndex={-1}>
                One product to build, run and assure automations.
              </h2>
              <p className="section-lede">
                Studio, Control Room, execution and assurance are parts of one product with one
                record, not separate tools. Assurance is available today, Studio and workflow
                packages are arriving in stages, and the rest is being built on them. The register
                says which is which.
              </p>
              <div className="register">
                {register.map(([group, rows]) => (
                  <div key={group} className="register-group">
                    <h3 className="register-heading">{group}</h3>
                    <dl>
                      {rows.map(([name, state, copy]) => (
                        <div key={name} className="register-row">
                          <dt>{name}</dt>
                          <dd>{copy}</dd>
                          <dd className="register-state">
                            <span className="readiness-tag" data-state={state}>
                              {readinessLabel[state]}
                            </span>
                          </dd>
                        </div>
                      ))}
                    </dl>
                  </div>
                ))}
              </div>
              <div className="platform-today">
                <h3>Available today: the assurance console, deciding in front of you.</h3>
                <p>
                  A sample workspace running Lotuspond’s release rules in your browser. Change an
                  outcome or the policy and the verdict follows, with the same reasons the server
                  records.
                </p>
              </div>
              <ConsolePreview />
            </div>
          </section>

          <section id="control" className="landing-section" aria-labelledby="control-title">
            <Rail id="control" />
            <div>
              <h2 id="control-title" className="section-title" tabIndex={-1}>
                People stay in charge, and the API enforces it.
              </h2>
              <p className="section-lede">
                These rules hold today in the API, not only in the console, and every workspace
                change records who made it and when. The production runtime is being designed to the
                same standard: no package, model output or cached approval can authorize an action
                on its own.
              </p>
              <ol className="charter">
                {controls.map(([title, copy], index) => (
                  <li key={title}>
                    <span className="charter-numeral" aria-hidden="true">
                      {numerals[index]}
                    </span>
                    <h3>{title}</h3>
                    <p>{copy}</p>
                  </li>
                ))}
              </ol>
              <div className="gate">
                <div>
                  <h3>Make the pipeline obey the decision.</h3>
                  <p>
                    The release gate prints the verdict, its reasons, and the evidence digest.
                    Anything other than a pass exits with status 2, so a deployment stops where the
                    evidence does.
                  </p>
                </div>
                <pre
                  className="code-block"
                  role="region"
                  aria-label="Release gate in a delivery pipeline"
                  tabIndex={0}
                >
                  <code>
                    {gateExample.map(([kind, text], index) => (
                      <span key={index} className={`code-${kind}`}>
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

          <section id="workflows" className="landing-section" aria-labelledby="workflows-title">
            <Rail id="workflows" />
            <div>
              <h2 id="workflows-title" className="section-title" tabIndex={-1}>
                Shared capabilities, qualified one workflow at a time.
              </h2>
              <p className="section-lede">
                Most business automation falls into a handful of families. Lotuspond builds each
                capability once, packages industry knowledge on top, and proves every workflow with
                its own evidence before calling it supported.
              </p>
              <ul className="rule-grid rule-grid-three">
                {families.map(([tag, title, examples, evidence]) => (
                  <li key={title}>
                    <span className="rule-tag">{tag}</span>
                    <h3>{title}</h3>
                    <p>{examples}</p>
                    <dl className="family-evidence">
                      <dt>Evidence before support</dt>
                      <dd>{evidence}</dd>
                    </dl>
                  </li>
                ))}
              </ul>
              <p className="landing-aside">
                Healthcare starts with administrative work such as referral intake and appointment
                operations. Diagnostic, treatment and clinical decision support functions need a
                separate validation program with clinical experts and regulatory review.
              </p>
            </div>
          </section>

          <section id="deployment" className="landing-section" aria-labelledby="deployment-title">
            <Rail id="deployment" />
            <div>
              <h2 id="deployment-title" className="section-title" tabIndex={-1}>
                Runs on infrastructure you control.
              </h2>
              <p className="section-lede">
                Install Lotuspond where your systems are. Evaluation and, later, workflow steps run
                on workers inside your own boundary.
              </p>
              <ul className="panel-grid panel-grid-two">
                {deployments.map(([title, copy], index) => (
                  <li key={title}>
                    <div className="panel-glyph">
                      <InkGlyph name={deploymentGlyphs[index] ?? 'network'} />
                    </div>
                    <h3>{title}</h3>
                    <p>{copy}</p>
                  </li>
                ))}
              </ul>
            </div>
          </section>

          <section id="accounts" className="landing-section" aria-labelledby="accounts-title">
            <Rail id="accounts" />
            <div>
              <h2 id="accounts-title" className="section-title" tabIndex={-1}>
                Start on your own. Add a team deliberately.
              </h2>
              <p className="section-lede">
                Every account has a personal workspace. An organization is a separate choice, with
                its own members, roles, and policies.
              </p>
              <ul className="panel-grid panel-grid-two account-choices">
                <li>
                  <PondPlate scene="individual" className="plate-wide" />
                  <p className="eyebrow">Individual</p>
                  <h3>A personal workspace</h3>
                  <p>
                    Your projects, runners, and keys in one place. It stays yours when you later
                    join or create an organization.
                  </p>
                  <button className="text-button" onClick={() => onCreateAccount('individual')}>
                    Create a personal account <span aria-hidden="true">→</span>
                  </button>
                </li>
                <li>
                  <PondPlate scene="organization" className="plate-wide" />
                  <p className="eyebrow">Organization</p>
                  <h3>A shared workspace</h3>
                  <p>Roles, invitations, scoped API keys and an audit log of every change.</p>
                  <button className="text-button" onClick={() => onCreateAccount('organization')}>
                    Create an organization <span aria-hidden="true">→</span>
                  </button>
                </li>
              </ul>
            </div>
          </section>

          <RecentlyShipped />
        </div>

        <Pond id="closing-title" title="Start with one process" className="pond-closing">
          <div className="landing-actions">
            <button onClick={() => onCreateAccount()}>
              Create account <span aria-hidden="true">→</span>
            </button>
            <button className="secondary" onClick={onSignIn}>
              Sign in
            </button>
          </div>
        </Pond>
      </main>

      <footer className="landing-footer">
        <div className="landing-container landing-footer-inner">
          <span className="wordmark">
            Lotuspond
            <span className="wordmark-dot" />
          </span>
          <span>Business automation with people in charge.</span>
        </div>
      </footer>
    </div>
  );
}
