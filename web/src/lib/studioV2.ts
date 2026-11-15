// Studio v2 (docs/superpowers/specs/2026-11-15-studio-v2-authoring-design.md): pure helpers for
// authoring an executable-v2 workflow package. The browser never decides validity: the server's
// check runs the SDK's validator and compiler, and these helpers only shape the draft.
import decision from '../../../contracts/examples/executable-v2/decision.json';
import iteration from '../../../contracts/examples/executable-v2/iteration.json';
import minimal from '../../../contracts/examples/executable-v2/minimal.json';
import parallel from '../../../contracts/examples/executable-v2/parallel.json';
import caseApproval from '../../../contracts/examples/executable-v2/patterns/case-approval.json';
import documentIntake from '../../../contracts/examples/executable-v2/patterns/document-intake.json';
import knowledgeAnswer from '../../../contracts/examples/executable-v2/patterns/knowledge-answer.json';
import recordReconciliation from '../../../contracts/examples/executable-v2/patterns/record-reconciliation.json';
import reviewedAction from '../../../contracts/examples/executable-v2/patterns/reviewed-action.json';

export const SCHEMA_V2 = 'lotuspond.workflow-package.v2';

export type Json = null | boolean | number | string | Json[] | { [key: string]: Json };
export type JsonObject = { [key: string]: Json };
export type Step = JsonObject & { kind: string; id: string };
export type V2Package = JsonObject & {
  schema_version: string;
  package_id: string;
  version: string;
  name: string;
  body: JsonObject & { steps: Step[] };
  limits: JsonObject;
};

export type Pattern = {
  id: string;
  title: string;
  text: string;
  family: 'domain' | 'structure';
  package: V2Package;
};

// The five domain patterns (contracts/examples/executable-v2/patterns/, generated and checked
// by the server's own rules in tests/execution/test_pattern_examples.py), then the SDK's
// structural examples. Each is synthetic: a shape to start from, not a configured integration.
export const V2_PATTERNS: readonly Pattern[] = [
  {
    id: 'reviewed-action',
    title: 'Reviewed action',
    text: 'Read a case, let an agent propose an update, have a person allow it, then write it and verify it.',
    family: 'domain',
    package: reviewedAction as unknown as V2Package,
  },
  {
    id: 'document-intake',
    title: 'Document intake',
    text: 'Read a document, extract and check its fields, have a person review, then create the record.',
    family: 'domain',
    package: documentIntake as unknown as V2Package,
  },
  {
    id: 'case-approval',
    title: 'Case approval',
    text: 'Recommend a decision, have a person make it, record it, then notify without another review.',
    family: 'domain',
    package: caseApproval as unknown as V2Package,
  },
  {
    id: 'knowledge-answer',
    title: 'Knowledge answer',
    text: 'Read a request, search the knowledge base, draft a cited reply, and post it once reviewed.',
    family: 'domain',
    package: knowledgeAnswer as unknown as V2Package,
  },
  {
    id: 'record-reconciliation',
    title: 'Record reconciliation',
    text: 'Read two systems, match their records, and post the adjustment a person approves.',
    family: 'domain',
    package: recordReconciliation as unknown as V2Package,
  },
  {
    id: 'decision',
    title: 'Decision',
    text: 'Two branches chosen by a condition.',
    family: 'structure',
    package: decision as unknown as V2Package,
  },
  {
    id: 'parallel',
    title: 'Parallel',
    text: 'Two named branches that run together and join.',
    family: 'structure',
    package: parallel as unknown as V2Package,
  },
  {
    id: 'iteration',
    title: 'Iteration',
    text: 'One bounded step for each item of a list.',
    family: 'structure',
    package: iteration as unknown as V2Package,
  },
  {
    id: 'minimal',
    title: 'Blank',
    text: 'An empty body, to build from.',
    family: 'structure',
    package: minimal as unknown as V2Package,
  },
];

export function clone<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T;
}

export function isV2(value: unknown): value is V2Package {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return false;
  const record = value as JsonObject;
  const body = record.body as JsonObject | undefined;
  return (
    record.schema_version === SCHEMA_V2 &&
    !!body &&
    typeof body === 'object' &&
    Array.isArray(body.steps)
  );
}

// A step in a few words for the outline: its kind and what it names.
const KIND_LABELS: Record<string, string> = {
  agent: 'Agent',
  connector_read: 'Read',
  decision: 'Decision',
  parallel: 'Parallel',
  iteration: 'Iteration',
  subworkflow: 'Subworkflow',
  action_intent: 'Propose an action',
  human_task: 'Person',
  action_dispatch: 'Send the action',
  outcome_verification: 'Verify the outcome',
  timer: 'Timer',
  external_wait: 'Wait for a signal',
  transform: 'Transform',
};

export function stepLabel(step: Step) {
  return KIND_LABELS[step.kind] ?? step.kind.replaceAll('_', ' ');
}

export function stepDetail(step: Step) {
  const parts = [step.connector_ref, step.operation, step.agent_ref, step.model_ref, step.mode]
    .filter((part) => typeof part === 'string' && part)
    .map(String);
  return parts.join(' · ');
}

// Steps are edited as whole values; the outline moves and removes them by index.
export function moveStep(steps: readonly Step[], from: number, to: number): Step[] {
  if (from < 0 || from >= steps.length || to < 0 || to >= steps.length) return [...steps];
  const next = [...steps];
  const [step] = next.splice(from, 1);
  next.splice(to, 0, step!);
  return next;
}

export function uniqueStepId(steps: readonly Step[], base: string) {
  const taken = new Set(steps.map((step) => step.id));
  if (!taken.has(base)) return base;
  for (let n = 2; ; n++) if (!taken.has(`${base}_${n}`)) return `${base}_${n}`;
}

// A starting value for each step kind an author adds by hand, shaped as the SDK's step models
// (sdk/python/src/lotuspond_sdk/executable/package.py). References start empty; the server's
// check says what each still needs.
export type NewStepKind = 'connector_read' | 'agent' | 'timer' | 'transform';
const NOTHING = { kind: 'literal', schema: { kind: 'null' }, value: null };
const RETRY = { max_attempts: 1, timeout_seconds: 30, backoff_seconds: 0, retry_codes: [] };

export function newStep(kind: NewStepKind, id: string): Step {
  if (kind === 'connector_read')
    return {
      kind,
      id,
      connector_ref: '',
      operation: '',
      target: NOTHING,
      input: NOTHING,
      retry: RETRY,
    };
  if (kind === 'agent')
    return {
      kind,
      id,
      model_ref: '',
      input: NOTHING,
      instructions: 'Describe what this step must do.',
      output_schema: { kind: 'null' },
      read_tools: [],
      knowledge_refs: [],
      limits: {
        max_steps: 5,
        timeout_seconds: 60,
        max_input_tokens: 4000,
        max_output_tokens: 1000,
        max_cost_microusd: 100000,
      },
      retry: RETRY,
    };
  if (kind === 'timer') return { kind, id, delay_seconds: 60 };
  return { kind, id, mapping: NOTHING, output_schema: { kind: 'null' } };
}

// Problems from the server's check, by the step they point into (`/body/steps/<n>/…`).
export type Problem = { path: string; code: string; message: string };

export function stepOfProblem(problem: Problem): number | null {
  const match = /^\/body\/steps\/(\d+)(?:\/|$)/.exec(problem.path);
  return match ? Number(match[1]) : null;
}

export function problemsByStep(problems: readonly Problem[]) {
  const steps = new Map<number, Problem[]>();
  const rest: Problem[] = [];
  for (const problem of problems) {
    const index = stepOfProblem(problem);
    if (index === null) rest.push(problem);
    else steps.set(index, [...(steps.get(index) ?? []), problem]);
  }
  return { steps, rest };
}

// Every catalog or workflow document the package pins, as `{kind, name, version, sha256}`.
export type PinnedRef = { kind: string; name: string; version: string; sha256: string };

const DECLARATIONS = [
  'models',
  'connectors',
  'agents',
  'policies',
  'knowledge',
  'artifacts',
  'evaluations',
  'subworkflows',
] as const;

export function pinnedRefs(pkg: JsonObject): PinnedRef[] {
  const found = new Map<string, PinnedRef>();
  for (const name of DECLARATIONS) {
    const list = pkg[name];
    if (!Array.isArray(list)) continue;
    for (const entry of list) {
      const ref =
        entry && typeof entry === 'object' && !Array.isArray(entry)
          ? (entry as JsonObject).ref
          : null;
      if (!ref || typeof ref !== 'object' || Array.isArray(ref)) continue;
      const { kind, name: refName, version, sha256 } = ref as JsonObject;
      if ([kind, refName, version, sha256].every((value) => typeof value === 'string')) {
        const item = {
          kind: String(kind),
          name: String(refName),
          version: String(version),
          sha256: String(sha256),
        };
        found.set(`${item.kind}|${item.name}|${item.version}|${item.sha256}`, item);
      }
    }
  }
  return [...found.values()];
}

export type Publication = {
  id: string;
  kind: string;
  name: string;
  version: string;
  package_sha256: string;
};

// Each pin matched exactly (kind, name, version and digest) to a publication in this
// environment. Nothing is guessed: a pin without an exact match is missing.
export function matchDependencies(
  refs: readonly PinnedRef[],
  publications: readonly Publication[],
) {
  const matched: { ref: PinnedRef; publication: Publication }[] = [];
  const missing: PinnedRef[] = [];
  for (const ref of refs) {
    const publication = publications.find(
      (item) =>
        item.kind === ref.kind &&
        item.name === ref.name &&
        item.version === ref.version &&
        item.package_sha256 === ref.sha256,
    );
    if (publication) matched.push({ ref, publication });
    else missing.push(ref);
  }
  return { matched, missing, ids: [...new Set(matched.map((m) => m.publication.id))] };
}

// The SDK command that signs the downloaded canonical package with the publisher's key.
export function signCommand(file = 'package.json') {
  return `lotuspond-package v2 sign ${file} --key <publisher-key.pem> --output signature.json`;
}

export function parseSignature(text: string): JsonObject | null {
  try {
    const value = JSON.parse(text) as Json;
    return value && typeof value === 'object' && !Array.isArray(value) ? value : null;
  } catch {
    return null;
  }
}
