import { useEffect, useRef, useState, type FormEvent } from 'react';
import { api, type Row } from '../api.js';
import { Alert, ObjectView, StatusBadge, displayValue } from './ui.js';

export function Assessments({
  items,
  workspace,
  resourcePath,
  canWrite,
  subject,
  onRecorded,
}: {
  items: Row[];
  workspace: string;
  resourcePath: string;
  canWrite: boolean;
  subject: 'trace' | 'transcript';
  onRecorded: () => void | Promise<void>;
}) {
  const [showForm, setShowForm] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [job, setJob] = useState<Row | null>(null);
  const [jobAssessments, setJobAssessments] = useState<Row[]>([]);
  const [pollRevision, setPollRevision] = useState(0);
  const onRecordedRef = useRef(onRecorded);
  onRecordedRef.current = onRecorded;
  const pending = job !== null && ['queued', 'running'].includes(String(job.status));
  useEffect(() => {
    if (!job?.id || !pending) return;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    async function poll() {
      try {
        const result = await api<{ job: Row; assessments: Row[] }>(
          `/evaluation-jobs/${encodeURIComponent(job!.id)}`,
          workspace,
          undefined,
          'GET',
          controller.signal,
        );
        if (controller.signal.aborted) return;
        setJob(result.job);
        setJobAssessments(result.assessments);
        setError('');
        if (result.job.status === 'completed') {
          setNotice(
            result.assessments.length
              ? 'Assessment completed. Review its result and provenance below.'
              : 'The evaluation job completed; its assessment is not available yet.',
          );
          await onRecordedRef.current();
        } else if (result.job.status === 'failed') {
          setError(
            'The assessment job could not finish. Review the worker configuration before requesting another assessment.',
          );
        } else {
          timer = setTimeout(() => {
            void poll();
          }, 3000);
        }
      } catch (reason) {
        if (!controller.signal.aborted)
          setError(`Unable to refresh the assessment job. ${(reason as Error).message}`);
      }
    }
    void poll();
    return () => {
      controller.abort();
      clearTimeout(timer);
    };
  }, [job?.id, pending, workspace, pollRevision]);
  const visibleItems = [
    ...items,
    ...jobAssessments.filter((assessment) => !items.some((item) => item.id === assessment.id)),
  ];
  async function assess(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError('');
    setNotice('');
    const form = new FormData(event.currentTarget);
    try {
      const queued = await api<Row>(`${resourcePath}/assess`, workspace, {
        criteria: String(form.get('criteria')).trim(),
      });
      setShowForm(false);
      setJob(queued);
      setJobAssessments([]);
      setNotice(
        'Assessment queued. A worker must process this evidence before a result is available.',
      );
    } catch (reason) {
      setError((reason as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className="evidence-section">
      <div className="section-row">
        <h3>Model assessments</h3>
        {canWrite && (
          <button
            className="text-button"
            disabled={busy || pending}
            onClick={() => {
              setShowForm(!showForm);
              setNotice('');
            }}
          >
            {showForm ? 'Close form' : `Assess ${subject}`}
          </button>
        )}
      </div>
      {error && (
        <Alert onRetry={pending ? () => setPollRevision((value) => value + 1) : undefined}>
          {error}
        </Alert>
      )}
      {job && (
        <div className="job-status">
          <span>Assessment job</span>
          <StatusBadge value={job.status} />
          <span className="hint">
            {String(job.id).slice(0, 12)}
            {pending ? ' · Checking every 3 seconds' : ''}
          </span>
        </div>
      )}
      {notice && (
        <p className="success-notice" role="status">
          {notice}
        </p>
      )}
      {showForm && (
        <form
          className="record-form"
          onSubmit={(event) => {
            void assess(event);
          }}
        >
          <label>
            Assessment criteria
            <textarea
              name="criteria"
              rows={3}
              required
              minLength={3}
              maxLength={4000}
              placeholder="Describe the intended outcome and behaviors to check"
              autoFocus
            />
          </label>
          <p className="hint">
            A configured model reviews the retained {subject}. Its assessment is advisory evidence;
            it cannot grant authorization or override a release policy.
          </p>
          <button disabled={busy || pending}>{busy ? 'Assessing…' : 'Record assessment'}</button>
        </form>
      )}
      {!visibleItems.length ? (
        <p className="hint">
          No model assessment has been recorded. Missing assessments do not establish safety or
          success.
        </p>
      ) : (
        visibleItems.map((assessment, index) => (
          <article className="assessment" key={assessment.id || String(index)}>
            <div className="section-row">
              <h4>{String(assessment.model || 'Model not recorded')}</h4>
              <StatusBadge value={assessment.verdict || assessment.status || 'unknown'} />
            </div>
            <p className="assessment-reason">
              {String(assessment.reason || 'No explanation recorded.')}
            </p>
            <p className="hint">{displayValue(assessment.created_at)} · Advisory assessment</p>
            {assessment.provenance !== undefined && (
              <details className="evidence-disclosure">
                <summary>Assessment provenance</summary>
                <ObjectView value={assessment.provenance} />
              </details>
            )}
          </article>
        ))
      )}
    </section>
  );
}
