import { useEffect, useId, useState, type ReactNode } from 'react';
import {
  BOUNDS,
  LIMIT_RANGES,
  type AgentLimits,
  type ConnectorReference,
  type ModelReference,
  type PackageNode,
  type Problem,
  type SecretReference,
  type WorkflowPackage,
} from '../../lib/packageContract.js';
import {
  KIND_DESCRIPTIONS,
  KIND_LABELS,
  isIdentifier,
  parseOperations,
  type Draft,
} from '../../lib/studio.js';
import type { Selection } from './StudioCanvas.js';

const LIMIT_LABELS: Record<keyof AgentLimits, string> = {
  max_steps: 'Maximum steps',
  timeout_seconds: 'Timeout (seconds)',
  max_input_tokens: 'Maximum input tokens',
  max_output_tokens: 'Maximum output tokens',
  max_cost_microusd: 'Maximum cost (micro-USD)',
};

type Path = (string | number)[];
const samePath = (a: Path, b: Path) => a.length === b.length && a.every((part, i) => part === b[i]);
function problemAt(problems: Problem[], path: Path): string | undefined {
  return problems.find((problem) => samePath(problem.path, path))?.message;
}

// A field with its label, optional hint and the admission problem recorded for it.
function Field({
  label,
  hint,
  problem,
  children,
  wide,
}: {
  label: string;
  hint?: string;
  problem?: string;
  children: (ids: { describedBy: string | undefined; problemId: string }) => ReactNode;
  wide?: boolean;
}) {
  const id = useId();
  const hintId = hint ? `${id}-hint` : undefined;
  const problemId = `${id}-problem`;
  const describedBy =
    [hintId, problem ? problemId : undefined].filter(Boolean).join(' ') || undefined;
  return (
    <label className={wide ? 'wide' : undefined}>
      {label}
      {children({ describedBy, problemId })}
      {hint && (
        <span className="hint" id={hintId}>
          {hint}
        </span>
      )}
      {problem && (
        <span className="field-problem" id={problemId} role="alert">
          {problem}
        </span>
      )}
    </label>
  );
}

// Text that becomes a list only once editing pauses, so a comma can be typed.
function ListText({
  value,
  onCommit,
  describedBy,
}: {
  value: string[];
  onCommit: (items: string[]) => void;
  describedBy?: string;
}) {
  const [text, setText] = useState(value.join(', '));
  useEffect(() => setText(value.join(', ')), [value]);
  return (
    <input
      value={text}
      aria-describedby={describedBy}
      onChange={(event) => setText(event.target.value)}
      onBlur={() => onCommit(parseOperations(text))}
      onKeyDown={(event) => {
        if (event.key === 'Enter') {
          event.preventDefault();
          onCommit(parseOperations(text));
        }
      }}
    />
  );
}

// An identifier that is renamed on commit, so a half-typed name never collides or breaks edges.
function IdentifierField({
  label,
  value,
  taken,
  onRename,
  hint,
}: {
  label: string;
  value: string;
  taken: readonly string[];
  onRename: (next: string) => void;
  hint?: string;
}) {
  const [text, setText] = useState(value);
  useEffect(() => setText(value), [value]);
  const trimmed = text.trim();
  const collision = trimmed !== value && taken.includes(trimmed);
  const invalid = trimmed !== '' && !isIdentifier(trimmed);
  const commit = () => {
    if (trimmed && trimmed !== value && !collision && !invalid) onRename(trimmed);
    else setText(value);
  };
  return (
    <Field
      label={label}
      hint={hint ?? 'Lowercase letters, digits, single hyphens or underscores.'}
      problem={
        collision
          ? 'Another node already has this identifier.'
          : invalid
            ? 'Not a valid identifier.'
            : undefined
      }
    >
      {({ describedBy }) => (
        <input
          className="code-input"
          value={text}
          maxLength={BOUNDS.identifier.max}
          aria-describedby={describedBy}
          aria-invalid={collision || invalid || undefined}
          onChange={(event) => setText(event.target.value)}
          onBlur={commit}
          onKeyDown={(event) => {
            if (event.key === 'Enter') {
              event.preventDefault();
              commit();
            }
          }}
        />
      )}
    </Field>
  );
}

export function StudioInspector({
  draft,
  selection,
  problems,
  onUpdateNode,
  onRenameNode,
  onDeleteSelection,
  onAddEdge,
  onRemoveEdge,
  onUpdatePackage,
  onSelect,
}: {
  draft: Draft;
  selection: Selection;
  problems: Problem[];
  onUpdateNode: (id: string, node: PackageNode) => void;
  onRenameNode: (id: string, next: string) => void;
  onDeleteSelection: (selection: Selection) => void;
  onAddEdge: (source: string, target: string) => void;
  onRemoveEdge: (source: string, target: string) => void;
  onUpdatePackage: (patch: Partial<WorkflowPackage>) => void;
  onSelect: (selection: Selection) => void;
}) {
  const pkg = draft.package;
  const node =
    selection?.kind === 'node' ? pkg.nodes.find((item) => item.id === selection.id) : undefined;
  return (
    <aside className="studio-inspector" aria-label="Inspector">
      {node ? (
        <NodeEditor
          pkg={pkg}
          node={node}
          problems={problems}
          onUpdate={(next) => onUpdateNode(node.id, next)}
          onRename={(next) => onRenameNode(node.id, next)}
          onDelete={() => onDeleteSelection({ kind: 'node', id: node.id })}
          onAddEdge={(target) => onAddEdge(node.id, target)}
          onRemoveEdge={(target) => onRemoveEdge(node.id, target)}
          onSelect={onSelect}
        />
      ) : selection?.kind === 'edge' ? (
        <section className="studio-section">
          <h2>Edge</h2>
          <p className="studio-edge-summary">
            <code>{selection.edge.source}</code> → <code>{selection.edge.target}</code>
          </p>
          <div className="button-row">
            <button
              type="button"
              className="secondary danger"
              onClick={() => onDeleteSelection(selection)}
            >
              Remove edge
            </button>
            <button type="button" className="secondary" onClick={() => onSelect(null)}>
              Done
            </button>
          </div>
        </section>
      ) : (
        <section className="studio-section">
          <h2>Package</h2>
          <p className="hint">
            Select a node on the canvas to edit it. These fields identify the package itself.
          </p>
          <PackageEditor pkg={pkg} problems={problems} onUpdate={onUpdatePackage} />
        </section>
      )}
      <Declarations pkg={pkg} problems={problems} onUpdate={onUpdatePackage} />
    </aside>
  );
}

function NodeEditor({
  pkg,
  node,
  problems,
  onUpdate,
  onRename,
  onDelete,
  onAddEdge,
  onRemoveEdge,
  onSelect,
}: {
  pkg: WorkflowPackage;
  node: PackageNode;
  problems: Problem[];
  onUpdate: (node: PackageNode) => void;
  onRename: (next: string) => void;
  onDelete: () => void;
  onAddEdge: (target: string) => void;
  onRemoveEdge: (target: string) => void;
  onSelect: (selection: Selection) => void;
}) {
  const index = pkg.nodes.indexOf(node);
  const at = (...rest: Path): Path => ['nodes', index, node.kind, ...rest];
  const outgoing = pkg.edges.filter((edge) => edge.source === node.id);
  const candidates = pkg.nodes.filter(
    (item) =>
      item.id !== node.id &&
      item.kind !== 'start' &&
      !outgoing.some((edge) => edge.target === item.id),
  );
  const [target, setTarget] = useState('');
  useEffect(() => setTarget(''), [node.id]);
  const graphProblems = problems.filter(
    (problem) => !problem.path.length && problem.nodes.includes(node.id),
  );
  return (
    <section className="studio-section">
      <p className="eyebrow">{KIND_LABELS[node.kind]}</p>
      <h2>{node.id}</h2>
      <p className="hint">{KIND_DESCRIPTIONS[node.kind]}</p>
      {graphProblems.length > 0 && (
        <ul className="studio-node-problems" aria-label="Problems with this node">
          {graphProblems.map((problem) => (
            <li key={problem.code}>{problem.message}</li>
          ))}
        </ul>
      )}
      <div className="form-grid studio-form">
        <IdentifierField
          label="Identifier"
          value={node.id}
          taken={pkg.nodes.map((item) => item.id)}
          onRename={onRename}
        />
        {node.kind === 'agent' && (
          <>
            <Field
              label="Model"
              problem={problemAt(problems, at('model_ref'))}
              hint={pkg.models.length ? undefined : 'Declare a model under Declarations first.'}
            >
              {({ describedBy }) => (
                <select
                  value={node.model_ref}
                  aria-describedby={describedBy}
                  onChange={(event) => onUpdate({ ...node, model_ref: event.target.value })}
                >
                  <option value="">Choose a declared model…</option>
                  {pkg.models.map((model) => (
                    <option key={model.id} value={model.id}>
                      {model.id} · {model.profile} @ {model.revision}
                    </option>
                  ))}
                </select>
              )}
            </Field>
            <Field
              label="Instructions"
              wide
              problem={problemAt(problems, at('instructions'))}
              hint={`${node.instructions.length.toLocaleString()} of ${BOUNDS.instructions.max.toLocaleString()} characters.`}
            >
              {({ describedBy }) => (
                <textarea
                  rows={5}
                  value={node.instructions}
                  maxLength={BOUNDS.instructions.max}
                  aria-describedby={describedBy}
                  onChange={(event) => onUpdate({ ...node, instructions: event.target.value })}
                />
              )}
            </Field>
            {(Object.keys(LIMIT_RANGES) as (keyof AgentLimits)[]).map((field) => (
              <Field
                key={field}
                label={LIMIT_LABELS[field]}
                problem={problemAt(problems, at('limits', field))}
                hint={`${LIMIT_RANGES[field].min.toLocaleString()}–${LIMIT_RANGES[field].max.toLocaleString()}`}
              >
                {({ describedBy }) => (
                  <input
                    type="number"
                    inputMode="numeric"
                    min={LIMIT_RANGES[field].min}
                    max={LIMIT_RANGES[field].max}
                    step={1}
                    value={Number.isFinite(node.limits[field]) ? node.limits[field] : ''}
                    aria-describedby={describedBy}
                    onChange={(event) =>
                      onUpdate({
                        ...node,
                        limits: {
                          ...node.limits,
                          [field]:
                            event.target.value === '' ? Number.NaN : Number(event.target.value),
                        },
                      })
                    }
                  />
                )}
              </Field>
            ))}
          </>
        )}
        {node.kind === 'connector' && (
          <>
            <Field
              label="Connector"
              problem={problemAt(problems, at('connector_ref'))}
              hint={
                pkg.connectors.length ? undefined : 'Declare a connector under Declarations first.'
              }
            >
              {({ describedBy }) => (
                <select
                  value={node.connector_ref}
                  aria-describedby={describedBy}
                  onChange={(event) => {
                    const connector = pkg.connectors.find((item) => item.id === event.target.value);
                    onUpdate({
                      ...node,
                      connector_ref: event.target.value,
                      operation: connector?.operations[0] ?? '',
                    });
                  }}
                >
                  <option value="">Choose a declared connector…</option>
                  {pkg.connectors.map((connector) => (
                    <option key={connector.id} value={connector.id}>
                      {connector.id} · {connector.connector} {connector.version}
                    </option>
                  ))}
                </select>
              )}
            </Field>
            <Field label="Operation" problem={problemAt(problems, at('operation'))}>
              {({ describedBy }) => (
                <select
                  value={node.operation}
                  aria-describedby={describedBy}
                  onChange={(event) => onUpdate({ ...node, operation: event.target.value })}
                >
                  <option value="">Choose an operation…</option>
                  {(
                    pkg.connectors.find((item) => item.id === node.connector_ref)?.operations ?? []
                  ).map((operation) => (
                    <option key={operation} value={operation}>
                      {operation}
                    </option>
                  ))}
                </select>
              )}
            </Field>
          </>
        )}
      </div>
      <h3>Leads to</h3>
      {node.kind === 'end' ? (
        <p className="hint">End nodes have no outgoing edges.</p>
      ) : (
        <>
          {outgoing.length ? (
            <ul className="studio-edge-list">
              {outgoing.map((edge) => (
                <li key={edge.target}>
                  <button
                    type="button"
                    className="text-button"
                    onClick={() => onSelect({ kind: 'node', id: edge.target })}
                  >
                    {edge.target}
                  </button>
                  <button
                    type="button"
                    className="text-button danger"
                    aria-label={`Remove edge to ${edge.target}`}
                    onClick={() => onRemoveEdge(edge.target)}
                  >
                    Remove
                  </button>
                </li>
              ))}
            </ul>
          ) : (
            <p className="hint">Nothing yet. Every node except an end must lead somewhere.</p>
          )}
          <div className="studio-connect">
            <label>
              <span className="sr-only">Connect to</span>
              <select value={target} onChange={(event) => setTarget(event.target.value)}>
                <option value="">Connect to…</option>
                {candidates.map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.id} ({KIND_LABELS[item.kind]})
                  </option>
                ))}
              </select>
            </label>
            <button
              type="button"
              className="secondary"
              disabled={!target}
              onClick={() => {
                onAddEdge(target);
                setTarget('');
              }}
            >
              Add edge
            </button>
          </div>
        </>
      )}
      <div className="button-row studio-node-actions">
        <button type="button" className="secondary danger" onClick={onDelete}>
          Delete node
        </button>
        <button type="button" className="secondary" onClick={() => onSelect(null)}>
          Done
        </button>
      </div>
    </section>
  );
}

function PackageEditor({
  pkg,
  problems,
  onUpdate,
}: {
  pkg: WorkflowPackage;
  problems: Problem[];
  onUpdate: (patch: Partial<WorkflowPackage>) => void;
}) {
  return (
    <div className="form-grid studio-form">
      <Field label="Package identifier" problem={problemAt(problems, ['package_id'])}>
        {({ describedBy }) => (
          <input
            className="code-input"
            value={pkg.package_id}
            maxLength={BOUNDS.identifier.max}
            aria-describedby={describedBy}
            onChange={(event) => onUpdate({ package_id: event.target.value })}
          />
        )}
      </Field>
      <Field
        label="Version"
        hint="Three numbers, such as 1.0.0. A published version is immutable."
        problem={problemAt(problems, ['version'])}
      >
        {({ describedBy }) => (
          <input
            className="code-input"
            value={pkg.version}
            maxLength={BOUNDS.version.max}
            aria-describedby={describedBy}
            onChange={(event) => onUpdate({ version: event.target.value })}
          />
        )}
      </Field>
      <Field label="Name" wide problem={problemAt(problems, ['name'])}>
        {({ describedBy }) => (
          <input
            value={pkg.name}
            maxLength={BOUNDS.name.max}
            aria-describedby={describedBy}
            onChange={(event) => onUpdate({ name: event.target.value })}
          />
        )}
      </Field>
      <div className="wide">
        <span className="studio-fact-label">Entry node</span>
        <p className="studio-fact">
          <code>{pkg.entry_node || '—'}</code>
          <span className="hint"> Follows the single start node.</span>
        </p>
      </div>
    </div>
  );
}

function Declarations({
  pkg,
  problems,
  onUpdate,
}: {
  pkg: WorkflowPackage;
  problems: Problem[];
  onUpdate: (patch: Partial<WorkflowPackage>) => void;
}) {
  const replace = <T,>(list: T[], index: number, item: T) =>
    list.map((current, i) => (i === index ? item : current));
  const without = <T,>(list: T[], index: number) => list.filter((_, i) => i !== index);
  return (
    <section className="studio-section">
      <h2>Declarations</h2>
      <p className="hint">
        What the nodes may reference: models, connectors with their operations, and the names of
        secrets. Names only — never a credential value.
      </p>
      <details className="studio-declaration" open={pkg.models.length > 0}>
        <summary>
          Models <span className="studio-count">{pkg.models.length}</span>
        </summary>
        {pkg.models.map((model, index) => (
          <DeclarationRow
            key={index}
            title={model.id || 'New model'}
            onRemove={() => onUpdate({ models: without(pkg.models, index) })}
          >
            <Field label="Identifier" problem={problemAt(problems, ['models', index, 'id'])}>
              {({ describedBy }) => (
                <input
                  className="code-input"
                  value={model.id}
                  maxLength={BOUNDS.identifier.max}
                  aria-describedby={describedBy}
                  onChange={(event) =>
                    onUpdate({
                      models: replace(pkg.models, index, { ...model, id: event.target.value }),
                    })
                  }
                />
              )}
            </Field>
            <Field
              label="Profile"
              hint="The configured model profile this agent runs under."
              problem={problemAt(problems, ['models', index, 'profile'])}
            >
              {({ describedBy }) => (
                <input
                  className="code-input"
                  value={model.profile}
                  maxLength={BOUNDS.identifier.max}
                  aria-describedby={describedBy}
                  onChange={(event) =>
                    onUpdate({
                      models: replace(pkg.models, index, { ...model, profile: event.target.value }),
                    })
                  }
                />
              )}
            </Field>
            <Field label="Revision" problem={problemAt(problems, ['models', index, 'revision'])}>
              {({ describedBy }) => (
                <input
                  className="code-input"
                  value={model.revision}
                  maxLength={BOUNDS.revision.max}
                  aria-describedby={describedBy}
                  onChange={(event) =>
                    onUpdate({
                      models: replace(pkg.models, index, {
                        ...model,
                        revision: event.target.value,
                      }),
                    })
                  }
                />
              )}
            </Field>
          </DeclarationRow>
        ))}
        <button
          type="button"
          className="secondary"
          disabled={pkg.models.length >= BOUNDS.models.max}
          onClick={() =>
            onUpdate({
              models: [...pkg.models, { id: '', profile: '', revision: '' } as ModelReference],
            })
          }
        >
          Add model
        </button>
      </details>
      <details className="studio-declaration" open={pkg.connectors.length > 0}>
        <summary>
          Connectors <span className="studio-count">{pkg.connectors.length}</span>
        </summary>
        {pkg.connectors.map((connector, index) => (
          <DeclarationRow
            key={index}
            title={connector.id || 'New connector'}
            onRemove={() => onUpdate({ connectors: without(pkg.connectors, index) })}
          >
            <Field label="Identifier" problem={problemAt(problems, ['connectors', index, 'id'])}>
              {({ describedBy }) => (
                <input
                  className="code-input"
                  value={connector.id}
                  maxLength={BOUNDS.identifier.max}
                  aria-describedby={describedBy}
                  onChange={(event) =>
                    onUpdate({
                      connectors: replace(pkg.connectors, index, {
                        ...connector,
                        id: event.target.value,
                      }),
                    })
                  }
                />
              )}
            </Field>
            <Field
              label="Connector"
              hint="The connector type this reference points at."
              problem={problemAt(problems, ['connectors', index, 'connector'])}
            >
              {({ describedBy }) => (
                <input
                  className="code-input"
                  value={connector.connector}
                  maxLength={BOUNDS.identifier.max}
                  aria-describedby={describedBy}
                  onChange={(event) =>
                    onUpdate({
                      connectors: replace(pkg.connectors, index, {
                        ...connector,
                        connector: event.target.value,
                      }),
                    })
                  }
                />
              )}
            </Field>
            <Field label="Version" problem={problemAt(problems, ['connectors', index, 'version'])}>
              {({ describedBy }) => (
                <input
                  className="code-input"
                  value={connector.version}
                  maxLength={BOUNDS.version.max}
                  aria-describedby={describedBy}
                  onChange={(event) =>
                    onUpdate({
                      connectors: replace(pkg.connectors, index, {
                        ...connector,
                        version: event.target.value,
                      }),
                    })
                  }
                />
              )}
            </Field>
            <Field
              label="Secret reference"
              problem={problemAt(problems, ['connectors', index, 'secret_ref'])}
            >
              {({ describedBy }) => (
                <select
                  value={connector.secret_ref ?? ''}
                  aria-describedby={describedBy}
                  onChange={(event) =>
                    onUpdate({
                      connectors: replace(pkg.connectors, index, {
                        ...connector,
                        secret_ref: event.target.value || null,
                      }),
                    })
                  }
                >
                  <option value="">None</option>
                  {pkg.secret_refs.map((secret) => (
                    <option key={secret.id} value={secret.id}>
                      {secret.id}
                    </option>
                  ))}
                </select>
              )}
            </Field>
            <Field
              label="Operations"
              wide
              hint="Comma-separated identifiers, such as read-case, write-case."
              problem={
                problemAt(problems, ['connectors', index]) ??
                problems.find(
                  (problem) =>
                    problem.path[0] === 'connectors' &&
                    problem.path[1] === index &&
                    problem.path[2] === 'operations',
                )?.message
              }
            >
              {({ describedBy }) => (
                <ListText
                  value={connector.operations}
                  describedBy={describedBy}
                  onCommit={(operations) =>
                    onUpdate({
                      connectors: replace(pkg.connectors, index, { ...connector, operations }),
                    })
                  }
                />
              )}
            </Field>
          </DeclarationRow>
        ))}
        <button
          type="button"
          className="secondary"
          disabled={pkg.connectors.length >= BOUNDS.connectors.max}
          onClick={() =>
            onUpdate({
              connectors: [
                ...pkg.connectors,
                {
                  id: '',
                  connector: '',
                  version: '1.0.0',
                  secret_ref: null,
                  operations: [],
                } as ConnectorReference,
              ],
            })
          }
        >
          Add connector
        </button>
      </details>
      <details className="studio-declaration" open={pkg.secret_refs.length > 0}>
        <summary>
          Secret references <span className="studio-count">{pkg.secret_refs.length}</span>
        </summary>
        {pkg.secret_refs.map((secret, index) => (
          <DeclarationRow
            key={index}
            title={secret.id || 'New secret reference'}
            onRemove={() => onUpdate({ secret_refs: without(pkg.secret_refs, index) })}
          >
            <Field
              label="Configuration name"
              wide
              hint="A name the deployment resolves; never a value or a secret-store path."
              problem={problemAt(problems, ['secret_refs', index, 'id'])}
            >
              {({ describedBy }) => (
                <input
                  className="code-input"
                  value={secret.id}
                  maxLength={BOUNDS.identifier.max}
                  aria-describedby={describedBy}
                  onChange={(event) =>
                    onUpdate({
                      secret_refs: replace(pkg.secret_refs, index, { id: event.target.value }),
                    })
                  }
                />
              )}
            </Field>
          </DeclarationRow>
        ))}
        <button
          type="button"
          className="secondary"
          disabled={pkg.secret_refs.length >= BOUNDS.secret_refs.max}
          onClick={() =>
            onUpdate({ secret_refs: [...pkg.secret_refs, { id: '' } as SecretReference] })
          }
        >
          Add secret reference
        </button>
      </details>
    </section>
  );
}

function DeclarationRow({
  title,
  onRemove,
  children,
}: {
  title: string;
  onRemove: () => void;
  children: ReactNode;
}) {
  return (
    <div className="studio-declaration-row">
      <div className="section-row">
        <h3>{title}</h3>
        <button
          type="button"
          className="text-button danger"
          aria-label={`Remove ${title}`}
          onClick={onRemove}
        >
          Remove
        </button>
      </div>
      <div className="form-grid studio-form">{children}</div>
    </div>
  );
}
