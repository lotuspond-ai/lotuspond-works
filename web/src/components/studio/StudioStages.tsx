import { useId } from 'react';
import { studioStages } from '../../lib/roadmap.js';
import { Readiness } from '../ui.js';

export function StudioStages() {
  const id = useId();
  return (
    <section className="studio-stages" aria-labelledby={`${id}-heading`}>
      <p className="eyebrow">Lifecycle</p>
      <h2 id={`${id}-heading`}>From draft to production</h2>
      <ol className="studio-stage-list">
        {studioStages.map((stage, index) => (
          <li key={stage.name} data-state={stage.state}>
            <div className="studio-stage-head">
              <span className="studio-stage-number" aria-hidden="true">
                {index + 1}
              </span>
              <h3>{stage.name}</h3>
              <Readiness state={stage.state} />
            </div>
            <p>
              {stage.text}
              {stage.link && (
                <>
                  {' '}
                  <a className="inline-link" href={stage.link.href}>
                    {stage.link.label}
                  </a>
                </>
              )}
            </p>
          </li>
        ))}
      </ol>
    </section>
  );
}
