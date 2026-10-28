import { useId, type Ref } from 'react';
import { emptyDraft, type Draft } from '../../lib/studio.js';
import { patternDraft, patternPackage, patternSteps, patterns } from '../../lib/studioPatterns.js';

// Where a new draft begins: one of the synthetic patterns, a blank workflow, or a file. Each
// choice is a button named by its title and described by what the pattern does.
export function PatternChooser({
  onStart,
  onOpenFile,
  headingRef,
  fullNote = '',
}: {
  onStart: (draft: Draft, label: string) => void;
  onOpenFile: () => void;
  headingRef?: Ref<HTMLHeadingElement>;
  // Set when the browser already holds as many drafts as it keeps; every choice is then off.
  fullNote?: string;
}) {
  const id = useId();
  return (
    <section className="studio-start" aria-labelledby={`${id}-heading`}>
      <div className="section-row">
        <div>
          <p className="eyebrow">New draft</p>
          <h2 id={`${id}-heading`} ref={headingRef} tabIndex={-1}>
            Start from a pattern
          </h2>
        </div>
        <button type="button" className="secondary" disabled={!!fullNote} onClick={onOpenFile}>
          Open JSON…
        </button>
      </div>
      <p className="hint studio-start-lede">
        Each pattern is a synthetic example from the package contract. Its models and connectors are
        placeholders: declare your own before you publish.
      </p>
      {fullNote && <p className="notice">{fullNote}</p>}
      <ul className="studio-patterns">
        {patterns.map((pattern) => {
          const pkg = patternPackage(pattern);
          const key = `${id}-${pattern.id}`;
          return (
            <li key={pattern.id}>
              <button
                type="button"
                className="studio-pattern"
                disabled={!!fullNote}
                aria-labelledby={`${key}-title`}
                aria-describedby={`${key}-summary ${key}-steps`}
                onClick={() => onStart(patternDraft(pattern.id), pattern.title)}
              >
                <span className="studio-pattern-title" id={`${key}-title`}>
                  {pattern.title}
                </span>
                <span className="studio-pattern-family">{pattern.family}</span>
                <span className="studio-pattern-summary" id={`${key}-summary`}>
                  {pattern.summary}
                </span>
                <span className="studio-pattern-steps" id={`${key}-steps`}>
                  {pkg ? patternSteps(pkg) : ''}
                </span>
              </button>
            </li>
          );
        })}
        <li>
          <button
            type="button"
            className="studio-pattern studio-pattern-blank"
            disabled={!!fullNote}
            aria-labelledby={`${id}-blank-title`}
            aria-describedby={`${id}-blank-summary`}
            onClick={() => onStart(emptyDraft(), 'a blank workflow')}
          >
            <span className="studio-pattern-title" id={`${id}-blank-title`}>
              Blank workflow
            </span>
            <span className="studio-pattern-summary" id={`${id}-blank-summary`}>
              A start connected to an end. Add the steps, models and connectors yourself.
            </span>
          </button>
        </li>
      </ul>
    </section>
  );
}
