import { Readiness } from '../components/ui.js';
import type { PlannedArea } from '../lib/roadmap.js';

// A planned console area: what it will do, what must be true before it ships, and what to use
// meanwhile. It shows no sample records, so nothing here can be mistaken for a working feature.
export function Planned({ area }: { area: PlannedArea }) {
  return (
    <div className="planned">
      <div className="page-heading">
        <div>
          <p className="eyebrow">{area.section}</p>
          <h1>
            {area.title} <Readiness state="planned" />
          </h1>
          <p>{area.summary}</p>
        </div>
      </div>
      <p className="planned-note">
        This part of the console is not available yet. It has no records to show and nothing on this
        page runs.
      </p>
      <div className="planned-grid">
        <section className="panel" aria-labelledby={`${area.id}-will`}>
          <h2 id={`${area.id}-will`}>What it will do</h2>
          <ul className="planned-list">
            {area.will.map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ul>
        </section>
        <section className="panel" aria-labelledby={`${area.id}-gate`}>
          <h2 id={`${area.id}-gate`}>Before it ships</h2>
          <p>It builds on:</p>
          <ul className="planned-list">
            {area.dependsOn.map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ul>
          <p className="hint">{area.acceptance}</p>
          {area.foundation && (
            <>
              <h3>Built so far</h3>
              <p className="hint">{area.foundation}</p>
            </>
          )}
        </section>
      </div>
      <section className="panel" aria-labelledby={`${area.id}-today`}>
        <h2 id={`${area.id}-today`}>Use today</h2>
        <ul className="planned-today">
          {area.today.map((item) => (
            <li key={item.href}>
              <a className="inline-link" href={item.href}>
                {item.label}
              </a>
              <span>{item.note}</span>
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}
