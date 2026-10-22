// Starting patterns for a new Studio draft. Each one is a synthetic package in
// contracts/examples/, so the CLI, the Python contract tests and studioPatterns.test.ts all
// validate the same file. Add a pattern there, never as a browser-only definition.
import caseApproval from '../../../contracts/examples/case-approval.v1.json';
import documentIntake from '../../../contracts/examples/document-intake.v1.json';
import knowledgeAnswer from '../../../contracts/examples/knowledge-answer.v1.json';
import recordReconciliation from '../../../contracts/examples/record-reconciliation.v1.json';
import reviewedAction from '../../../contracts/examples/reviewed-action.v1.json';
import { readPackage, type NodeKind, type WorkflowPackage } from './packageContract.js';
import { draftFromPackage, emptyDraft, type Draft } from './studio.js';

export type Pattern = {
  id: string;
  title: string;
  // The workflow family on the public introduction that the shape belongs to.
  family: string;
  summary: string;
  source: unknown;
};

export const patterns: readonly Pattern[] = [
  {
    id: 'document-intake',
    title: 'Document intake',
    family: 'Document intake and validation',
    summary:
      'Read a document, extract its fields, check them against declared rules, have a person review the exceptions, then create the record.',
    source: documentIntake,
  },
  {
    id: 'case-approval',
    title: 'Case approval',
    family: 'Case and approval processes',
    summary:
      'Read a request, have an agent recommend against stated criteria, let a person decide, then record the decision and send a message.',
    source: caseApproval,
  },
  {
    id: 'knowledge-answer',
    title: 'Knowledge answer',
    family: 'Knowledge and assistance',
    summary:
      'Read a request, search a knowledge base, draft a reply that cites its sources, have a person review it, then post it.',
    source: knowledgeAnswer,
  },
  {
    id: 'record-reconciliation',
    title: 'Record reconciliation',
    family: 'Business-system transactions',
    summary:
      'Read two systems, match entries with records, have a person review the proposed adjustments, then post them.',
    source: recordReconciliation,
  },
  {
    id: 'reviewed-action',
    title: 'Reviewed action',
    family: 'Business-system transactions',
    summary:
      'Read a case, have an agent propose an update, have a person review it, then write the case.',
    source: reviewedAction,
  },
];

export function patternPackage(pattern: Pattern): WorkflowPackage | null {
  return readPackage(pattern.source).package ?? null;
}

export function patternDraft(id: string): Draft {
  const pattern = patterns.find((item) => item.id === id);
  const pkg = pattern ? patternPackage(pattern) : null;
  return pkg ? draftFromPackage(pkg) : emptyDraft();
}

// The steps a pattern passes through, as the chooser lists them: "2 connectors · 1 agent".
const counted: readonly [NodeKind, string, string][] = [
  ['connector', 'connector', 'connectors'],
  ['agent', 'agent', 'agents'],
  ['human_review', 'human review', 'human reviews'],
];
export function patternSteps(pkg: WorkflowPackage): string {
  return counted
    .map(([kind, one, many]) => {
      const count = pkg.nodes.filter((node) => node.kind === kind).length;
      return count ? `${count} ${count === 1 ? one : many}` : '';
    })
    .filter(Boolean)
    .join(' · ');
}
