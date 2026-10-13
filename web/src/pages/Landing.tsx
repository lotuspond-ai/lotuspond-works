import { useEffect, useRef, useState, type MouseEvent } from 'react';
import { AgentSignal } from '../components/AgentSignal.js';
import { Pond } from '../components/Pond.js';
import { InkGlyph } from '../components/InkGlyph.js';
import { PondPlate } from '../components/PondPlate.js';
import { ConsolePreview } from '../components/platform/ConsolePreview.js';
import { RecentlyShipped } from '../components/RecentlyShipped.js';
import { ThreadedSteps } from '../components/ThreadedSteps.js';
import { TraceSpecimen } from '../components/TraceSpecimen.js';
import { Alert, StatusBadge } from '../components/ui.js';
import { useArrival } from '../lib/useArrival.js';

type AccountChoice = 'individual' | 'organization';

// Reasons are quoted from backend/lotuspond/policy.py; keep them identical.
const releaseChecks = [
  ['Coverage', 'Candidate run lacks a conclusive result for every scenario.'],
  ['Critical scenarios', 'A critical scenario failed or has no conclusive result.'],
  ['Pass rate', 'Pass rate is below the policy minimum.'],
  ['Baseline', 'Candidate regresses beyond the permitted baseline threshold.'],
  ['Latency', 'P95 latency exceeds the policy limit or is unavailable.'],
  ['Cost', 'Evaluation cost exceeds the policy limit or is unavailable.'],
] as const;

const outcomes = [
  ['passed', 'The agent met the scenario’s expected outcome.', 'Counts toward the pass rate.'],
  [
    'failed',
    'The expected outcome was not met.',
    'Lowers the pass rate. Blocks the release when the scenario is critical.',
  ],
  ['unknown', 'A timeout, crash, or malformed or incomplete evidence.', 'Blocks the release.'],
  ['missing', 'No result arrived for a scenario in the frozen snapshot.', 'Blocks the release.'],
] as const;

const steps = [
  [
    'Define scenarios',
    'Write a concrete input, the outcome you expect, and whether a failure must block the release. Scenarios cover text, voice, tool-using, and multimodal agents.',
    'The scenario snapshot, frozen for each run',
  ],
  [
    'Run them on your side',
    'Your runner claims a leased run, calls your agent, and submits measured results. Agent endpoints and credentials stay with your runner.',
    'Outcome, latency, and reported cost per scenario',
  ],
  [
    'Decide the release',
    'Compare the candidate with a baseline that ran the same snapshot, then apply an immutable policy. When approval is required, a different administrator gives it.',
    'Verdict, reasons, and an evidence digest',
  ],
  [
    'Watch production',
    'Send traces over OTLP or the API. Monitors sample them for assessment, and failures or unavailable assessments open incidents you resolve with evidence.',
    'Traces, assessments, and incident history',
  ],
] as const;

const agentKinds = [
  [
    'http',
    'HTTP agents',
    'Send plain input or OpenAI-style chat to your endpoint and assert on an explicit response path. A completed response is not, by itself, a pass.',
  ],
  [
    'mcp · a2a',
    'Tools and agent protocols',
    'Execute an explicitly configured MCP tool, or send an A2A message where only a completed task is judged. Tool discovery stays read-only.',
  ],
  [
    'voice',
    'Voice agents',
    'Ingest partial and final transcript revisions. Final segments never change, and every assessment names its criteria, model, and provenance.',
  ],
  [
    'gym-anything',
    'Software tasks',
    'Run pinned Gym-Anything tasks in isolated containers with bounded actions, hard deadlines, and verified cleanup.',
  ],
] as const;

const guarantees = [
  [
    'Model judgments are advisory.',
    'An assessment can inform a decision. It never approves a release or authorizes a tool.',
  ],
  [
    'Policies are immutable.',
    'Change a rule by creating a new policy. Earlier decisions keep the rule they were made under.',
  ],
  [
    'Approval takes two people.',
    'The administrator who approves a release must be someone other than the one who asked.',
  ],
  [
    'Registered is not connected.',
    'An integration stays unverified until its connection is independently proven.',
  ],
  [
    'Unmeasured cost stays unknown.',
    'A cost that was never reported is recorded as unavailable, never as zero.',
  ],
  [
    'Evidence leaves with its digest.',
    'Release evidence exports as canonical JSON with a SHA-256 digest anyone can recompute.',
  ],
] as const;

const deployments = [
  [
    'Connected',
    'Container images for the API, console, background workers, and the OTLP collector.',
  ],
  [
    'On-premises',
    'An offline bundle of pinned images with checksums and a verifier, for networks you control.',
  ],
  [
    'Air-gapped',
    'Cloud model calls, mail, and external alerts switch off. Authenticators and operator-attested enrollment work without egress; assessments that need a cloud model report unknown.',
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

const stepGlyphs = ['snapshot', 'trace', 'seal', 'pulse'] as const;
const agentSignals = ['http', 'protocol', 'voice', 'software'] as const;
const deploymentGlyphs = ['network', 'enclosure', 'sealed'] as const;
const numerals = ['I', 'II', 'III', 'IV', 'V', 'VI'];

// Page order drives the contents list, the section numbers, and the header links.
const sections = [
  ['platform', 'Platform', '4 views'],
  ['outcomes', 'Outcomes', '4 states'],
  ['workflow', 'Workflow', '4 steps'],
  ['agents', 'Agents', '4 kinds'],
  ['guarantees', 'Guarantees', '6 rules'],
  ['deployment', 'Deployment', '3 modes'],
  ['accounts', 'Accounts', '2 paths'],
] as const;
type SectionId = (typeof sections)[number][0];
const headerLinks: SectionId[] = ['platform', 'outcomes', 'workflow', 'deployment'];

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
      // The policy record rises out of the water as the reader arrives at it.
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
          title="Release agents on the record"
          className="pond-hero"
          eyebrow={<p className="pond-eyebrow">Agent release assurance</p>}
        >
          {error && <Alert>{error}</Alert>}
          <p className="pond-lede">
            Lotuspond evaluates every candidate against frozen scenarios, compares it with a
            baseline, and applies a policy no one can quietly edit. Missing or unknown results block
            the release.
          </p>
          <div className="landing-actions">
            <button onClick={() => onCreateAccount()}>
              Create account <span aria-hidden="true">→</span>
            </button>
            <button className="secondary" onClick={onSignIn}>
              Sign in
            </button>
          </div>
          <p className="pond-note">
            For text, voice, and tool-using agents. Self-hosted, including air-gapped networks.
          </p>
        </Pond>

        <section ref={surfacing} className="surfacing" aria-labelledby="decision-title">
          <div className="landing-container surfacing-inner">
            <div className="surfacing-copy">
              <p className="eyebrow">How a release is decided</p>
              <h2 id="decision-title" className="section-title" tabIndex={-1}>
                Every verdict carries its reasons.
              </h2>
              <p className="section-lede">
                A release passes only when every configured check clears. Otherwise Lotuspond blocks
                it, or holds it for an independent administrator, and records exactly why.
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
              aria-labelledby="decision-record-title"
            >
              <div className="decision-record-head">
                <div>
                  <p className="decision-record-kicker">Release policy</p>
                  <h3 id="decision-record-title">Every check a candidate must clear</h3>
                </div>
                <span className="record-stamp" data-stamped={stamped}>
                  Immutable
                </span>
              </div>
              <ul className="decision-checks" aria-label="Checks that block a release">
                {releaseChecks.map(([name, reason]) => (
                  <li key={name}>
                    <span className="check-name">{name}</span>
                    <span className="check-reason">{reason}</span>
                    <StatusBadge value="block" />
                  </li>
                ))}
              </ul>
              <ul className="decision-checks decision-verdicts" aria-label="Remaining verdicts">
                <li>
                  <span className="check-name">Approval</span>
                  <span className="check-reason">
                    An independent administrator must approve this release.
                  </span>
                  <StatusBadge value="approval_required" />
                </li>
                <li>
                  <span className="check-name">Otherwise</span>
                  <span className="check-reason">All configured release checks passed.</span>
                  <StatusBadge value="pass" />
                </li>
              </ul>
              <figcaption>
                Reasons appear exactly as Lotuspond records them. Latency and cost apply when the
                policy sets a limit.
              </figcaption>
            </figure>
          </div>
        </section>

        <div className="landing-container">
          <section id="platform" className="landing-section" aria-labelledby="platform-title">
            <Rail id="platform" />
            <div>
              <h2 id="platform-title" className="section-title" tabIndex={-1}>
                The console, deciding in front of you.
              </h2>
              <p className="section-lede">
                A sample workspace running Lotuspond’s release rules in your browser. Change an
                outcome or the policy and the verdict follows, with the same reasons the server
                records.
              </p>
              <ConsolePreview />
            </div>
          </section>

          <section id="outcomes" className="landing-section" aria-labelledby="outcomes-title">
            <Rail id="outcomes" />
            <div>
              <h2 id="outcomes-title" className="section-title section-statement" tabIndex={-1}>
                A result you don’t have is not a pass.
              </h2>
              <p className="section-lede">
                Agents time out, crash, return malformed evidence, or never report back. Lotuspond
                records each of those as unknown, and an unknown outcome blocks the release
                outright.
              </p>
              <ul className="specimens" aria-label="Scenario outcomes">
                {outcomes.map(([status, meaning, effect]) => (
                  <li key={status}>
                    <TraceSpecimen outcome={status} />
                    <div className="specimen-text">
                      <StatusBadge value={status} />
                      <p>{meaning}</p>
                      <p
                        className={
                          status === 'passed'
                            ? 'specimen-effect'
                            : 'specimen-effect specimen-blocks'
                        }
                      >
                        {effect}
                      </p>
                    </div>
                  </li>
                ))}
              </ul>
            </div>
          </section>

          <section id="workflow" className="landing-section" aria-labelledby="workflow-title">
            <Rail id="workflow" />
            <div>
              <h2 id="workflow-title" className="section-title" tabIndex={-1}>
                From scenario to signed-off release.
              </h2>
              <p className="section-lede">
                One record runs from the first scenario to production. Each step keeps what the next
                one needs to explain a decision.
              </p>
              <ThreadedSteps steps={steps} glyphs={stepGlyphs} />
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

          <section id="agents" className="landing-section" aria-labelledby="agents-title">
            <Rail id="agents" />
            <div>
              <h2 id="agents-title" className="section-title" tabIndex={-1}>
                Text, voice, tools, and real software.
              </h2>
              <p className="section-lede">
                Adapters are configured explicitly by an operator. Each one reports what it
                observed, and anything it could not observe remains unknown.
              </p>
              <ul className="rule-grid rule-grid-two">
                {agentKinds.map(([tag, title, copy], index) => (
                  <li key={title}>
                    <div className="agent-head">
                      <AgentSignal kind={agentSignals[index] ?? 'http'} />
                      <span className="rule-tag">{tag}</span>
                    </div>
                    <h3>{title}</h3>
                    <p>{copy}</p>
                  </li>
                ))}
              </ul>
            </div>
          </section>

          <section id="guarantees" className="landing-section" aria-labelledby="guarantees-title">
            <Rail id="guarantees" />
            <div>
              <h2 id="guarantees-title" className="section-title" tabIndex={-1}>
                Designed to be audited.
              </h2>
              <p className="section-lede">
                These rules hold in the API, not only in the console, and every workspace change
                records who made it and when.
              </p>
              <ol className="charter">
                {guarantees.map(([title, copy], index) => (
                  <li key={title}>
                    <span className="charter-numeral" aria-hidden="true">
                      {numerals[index]}
                    </span>
                    <h3>{title}</h3>
                    <p>{copy}</p>
                  </li>
                ))}
              </ol>
            </div>
          </section>

          <section id="deployment" className="landing-section" aria-labelledby="deployment-title">
            <Rail id="deployment" />
            <div>
              <h2 id="deployment-title" className="section-title" tabIndex={-1}>
                Runs where your agents run.
              </h2>
              <p className="section-lede">
                Install the control plane on infrastructure you operate, from a connected network to
                one with no route out.
              </p>
              <ul className="panel-grid">
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
              <p className="landing-aside">
                Execution ships separately: a nonroot image with HTTP, MCP, voice, and release-gate
                commands and no built-in credentials, so evaluations run beside your agents.
              </p>
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
                  <p className="eyebrow">Individual developer</p>
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
                  <p>
                    Roles, invitations, scoped API keys, and an optional authenticator requirement
                    for every member.
                  </p>
                  <button className="text-button" onClick={() => onCreateAccount('organization')}>
                    Create an organization <span aria-hidden="true">→</span>
                  </button>
                </li>
              </ul>
            </div>
          </section>

          <RecentlyShipped />
        </div>

        <Pond
          id="closing-title"
          title="Put your next agent release on the record"
          className="pond-closing"
        >
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
          <span>Release decisions grounded in evidence.</span>
        </div>
      </footer>
    </div>
  );
}
