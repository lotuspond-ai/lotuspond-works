import { useEffect, useRef, useState } from 'react';
import { api, ApiError } from '../../api.js';
import {
  bindingTemplate,
  deploymentChanges,
  opaqueId,
  operateMessage,
  scopedPath,
  type DependencyRef,
  type DeploymentStatus,
  type Scope,
} from '../../lib/operate.js';
import { Alert, StatusBadge } from '../ui.js';

export type Deployment = {
  id: string;
  revision_id: string | null;
  status: string;
  unreviewed_operations: string[];
};
type Publication = { id: string; kind: string; name: string; version: string };
type Pool = { id: string; name: string; status: string };

const short = (id: string) => `…${id.slice(-8)}`;

const DECLARATIONS = [
  'models',
  'connectors',
  'agents',
  'policies',
  'knowledge',
  'artifacts',
  'evaluations',
  'subworkflows',
];

function problem(reason: unknown) {
  if (reason instanceof ApiError && reason.code === 'assurance_unavailable')
    return 'This installation requires a release, and none proves this package version. Name a release whose evaluation run tested exactly this version.';
  return reason instanceof ApiError
    ? operateMessage(reason.code, reason.message)
    : 'The request could not be completed. Please try again.';
}

// Deployment changes (docs/superpowers/specs/2026-11-16-control-room-operations-design.md):
// enable, disable or revoke, and deploy a published workflow. Owners and admins (`manage`).
export function DeploymentPanel({
  workspace,
  scope,
  items,
  canManage,
  onChanged,
}: {
  workspace: string;
  scope: Scope;
  items: Deployment[] | null;
  canManage: boolean;
  onChanged: () => void;
}) {
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [busy, setBusy] = useState(false);
  const [confirming, setConfirming] = useState('');
  const [deploying, setDeploying] = useState(false);
  const keys = useRef<Record<string, string>>({});

  async function post(intent: string, path: string, body: unknown, done: string) {
    keys.current[intent] ??= opaqueId('control-room');
    setBusy(true);
    setError('');
    setNotice('');
    try {
      await api(scopedPath(scope, path), workspace, body, 'POST', undefined, {
        'idempotency-key': keys.current[intent]!,
      });
      delete keys.current[intent];
      setNotice(done);
      onChanged();
      return true;
    } catch (reason) {
      setError(problem(reason));
      return false;
    } finally {
      setBusy(false);
    }
  }

  function change(item: Deployment, status: DeploymentStatus) {
    setConfirming('');
    void post(
      `status:${item.id}:${status}`,
      `deployments/${item.id}/status`,
      { status },
      status === 'revoked'
        ? 'Revoked. It admits nothing new; runs already admitted continue under their version.'
        : status === 'enabled'
          ? 'Enabled. New runs can start on its active revision.'
          : 'Disabled. New runs are refused until it is enabled again.',
    );
  }

  return (
    <section className="panel" aria-labelledby="deployments-heading">
      <div className="panel-head">
        <h2 id="deployments-heading">Deployments</h2>
        {canManage && !deploying && (
          <button className="secondary" onClick={() => setDeploying(true)}>
            Deploy a package
          </button>
        )}
      </div>
      {!canManage && (
        <p className="hint">Owners and administrators change deployments; you can read them.</p>
      )}
      {error && <Alert>{error}</Alert>}
      {notice && (
        <p className="notice" role="status">
          {notice}
        </p>
      )}
      {deploying && (
        <NewDeployment
          workspace={workspace}
          scope={scope}
          busy={busy}
          onCancel={() => setDeploying(false)}
          onDeploy={async (body) => {
            const ok = await post(
              `deploy:${JSON.stringify(body)}`,
              'deployments',
              body,
              'Deployed. Enable it to start runs on this revision.',
            );
            if (ok) setDeploying(false);
          }}
        />
      )}
      {items === null ? (
        <p className="loading-state" role="status">
          Loading deployments…
        </p>
      ) : !items.length ? (
        <p className="hint">
          Nothing is deployed here yet. A deployment admits one published package version to this
          environment, bound to its connections and limits.
        </p>
      ) : (
        <div className="table-wrap">
          <table>
            <caption className="sr-only">Deployments</caption>
            <thead>
              <tr>
                <th scope="col">Deployment</th>
                <th scope="col">Status</th>
                <th scope="col">Active revision</th>
                <th scope="col">Writes sent without review</th>
                <th scope="col">
                  <span className="sr-only">Actions</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {items.map((item) => (
                <tr key={item.id}>
                  <td className="primary-cell cell-mono" title={item.id}>
                    {short(item.id)}
                  </td>
                  <td>
                    <StatusBadge value={item.status} />
                  </td>
                  <td className="cell-mono" title={item.revision_id ?? undefined}>
                    {item.revision_id ? short(item.revision_id) : 'None'}
                  </td>
                  <td>
                    {item.unreviewed_operations?.length
                      ? item.unreviewed_operations.join(', ')
                      : 'None'}
                  </td>
                  <td>
                    {canManage && (
                      <span className="operate-actions deployment-actions">
                        {deploymentChanges(item.status).map((status) =>
                          status === 'revoked' ? (
                            confirming === item.id ? (
                              <span key={status} className="operate-confirm">
                                <span>Revoke for good?</span>
                                <button
                                  className="secondary danger"
                                  disabled={busy}
                                  onClick={() => change(item, 'revoked')}
                                >
                                  Yes, revoke
                                </button>
                                <button className="text-button" onClick={() => setConfirming('')}>
                                  Keep
                                </button>
                              </span>
                            ) : (
                              <button
                                key={status}
                                className="text-button danger"
                                onClick={() => setConfirming(item.id)}
                              >
                                Revoke…
                              </button>
                            )
                          ) : (
                            <button
                              key={status}
                              className="secondary"
                              disabled={busy}
                              onClick={() => change(item, status)}
                            >
                              {status === 'enabled' ? 'Enable' : 'Disable'}
                            </button>
                          ),
                        )}
                      </span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

function NewDeployment({
  workspace,
  scope,
  busy,
  onCancel,
  onDeploy,
}: {
  workspace: string;
  scope: Scope;
  busy: boolean;
  onCancel: () => void;
  onDeploy: (body: Record<string, unknown>) => void;
}) {
  const [workflows, setWorkflows] = useState<Publication[] | null>(null);
  const [pools, setPools] = useState<Pool[]>([]);
  const [publicationId, setPublicationId] = useState('');
  const [poolId, setPoolId] = useState('');
  const [releaseId, setReleaseId] = useState('');
  const [configuration, setConfiguration] = useState('');
  const [error, setError] = useState('');

  useEffect(() => {
    api<{ items: Publication[] }>(scopedPath(scope, 'executable-packages?limit=100'), workspace)
      .then((page) => setWorkflows(page.items.filter((item) => item.kind === 'workflow')))
      .catch((reason) => {
        setWorkflows([]);
        setError(problem(reason));
      });
    api<{ items: Pool[] }>(scopedPath(scope, 'runtime-pools?limit=100'), workspace)
      .then((page) => setPools(page.items.filter((pool) => pool.status === 'active')))
      .catch(() => setPools([]));
  }, [scope, workspace]);

  // Prefill the configuration from the package itself: its limits, and one binding for each
  // pinned dependency, the connections and sources left to complete.
  useEffect(() => {
    if (!publicationId) return;
    fetch(`/api/v1${scopedPath(scope, `executable-packages/${publicationId}/content`)}`, {
      credentials: 'same-origin',
      headers: { 'x-lotuspond-workspace': workspace },
    })
      .then((response) => (response.ok ? response.json() : Promise.reject(new Error())))
      .then((pkg: Record<string, unknown>) => {
        const refs = DECLARATIONS.flatMap((name) =>
          Array.isArray(pkg[name])
            ? (pkg[name] as { ref?: DependencyRef }[]).flatMap((entry) =>
                entry.ref ? [entry.ref] : [],
              )
            : [],
        );
        setConfiguration(
          JSON.stringify(
            {
              bindings: bindingTemplate(refs),
              limits: { ...(pkg.limits as object), active_capacity: 1, pending_capacity: 100 },
            },
            null,
            2,
          ),
        );
      })
      .catch(() => setError('The package content could not be read.'));
  }, [publicationId, scope, workspace]);

  function submit() {
    let parsed: unknown;
    try {
      parsed = JSON.parse(configuration);
    } catch {
      setError('The configuration is not valid JSON.');
      return;
    }
    if (configuration.includes('<connection id>') || configuration.includes('<source id>')) {
      setError('Replace each <connection id> or <source id> with the one to bind.');
      return;
    }
    setError('');
    onDeploy({
      deployment_id: null,
      publication_id: publicationId,
      pool_id: poolId,
      configuration: parsed,
      release_id: releaseId.trim() || null,
    });
  }

  return (
    <div className="record-form new-deployment">
      <h3>Deploy a package</h3>
      {error && <Alert>{error}</Alert>}
      <div className="form-grid">
        <label>
          Published workflow
          <select value={publicationId} onChange={(event) => setPublicationId(event.target.value)}>
            <option value="">{workflows === null ? 'Loading…' : 'Choose…'}</option>
            {(workflows ?? []).map((item) => (
              <option key={item.id} value={item.id}>
                {item.name} {item.version}
              </option>
            ))}
          </select>
        </label>
        <label>
          Runtime pool
          <select value={poolId} onChange={(event) => setPoolId(event.target.value)}>
            <option value="">Choose…</option>
            {pools.map((pool) => (
              <option key={pool.id} value={pool.id}>
                {pool.name}
              </option>
            ))}
          </select>
        </label>
        <label>
          Release ID <span className="optional">(required when the installation requires one)</span>
          <input value={releaseId} onChange={(event) => setReleaseId(event.target.value)} />
        </label>
      </div>
      {publicationId && (
        <label>
          Configuration (JSON): connections to bind and limits within the package's own
          <textarea
            className="code-input"
            rows={14}
            spellCheck={false}
            value={configuration}
            onChange={(event) => setConfiguration(event.target.value)}
          />
        </label>
      )}
      {workflows !== null && !workflows.length && (
        <p className="hint">
          No workflow is published in this environment yet; publish one from the Studio.
        </p>
      )}
      <div className="operate-actions">
        <button disabled={busy || !publicationId || !poolId || !configuration} onClick={submit}>
          Deploy
        </button>
        <button className="text-button" onClick={onCancel}>
          Cancel
        </button>
      </div>
      <p className="hint">
        A new deployment starts disabled. Enable it from its row when it is ready.
      </p>
    </div>
  );
}
