import { useId } from 'react';
import { useArrival } from '../lib/useArrival.js';

// An outcome drawn as a trace on graph paper, in the manner of an instrument record.
// Illustrative only: there are no axes or values, and the written outcome beside it carries
// the meaning.
export type TraceOutcome = 'passed' | 'failed' | 'unknown' | 'missing';

const LEAD = 'M12 104 C 34 104, 40 70, 58 72 S 84 108, 102 96';
const FULL = `${LEAD} S 130 46, 150 58 S 180 94, 200 78 S 220 58, 228 60`;

export function TraceSpecimen({ outcome }: { outcome: TraceOutcome }) {
  const [ref, arrived] = useArrival<SVGSVGElement>(0.6);
  const clip = `trace-${useId().replace(/[^a-zA-Z0-9]/g, '')}`;
  return (
    <svg
      ref={ref}
      className={`trace-specimen trace-${outcome}`}
      viewBox="0 0 240 150"
      data-drawn={arrived}
      aria-hidden="true"
      focusable="false"
    >
      <defs>
        <clipPath id={clip}>
          <rect className="trace-reveal" x="0" y="0" width="240" height="150" />
        </clipPath>
      </defs>
      <line x1="12" y1="128" x2="228" y2="128" className="trace-axis" />
      <line x1="12" y1="24" x2="12" y2="128" className="trace-axis" />
      {outcome === 'missing' ? (
        <g className="trace-late">
          <path d={FULL} className="trace-ghost" />
          <circle cx="228" cy="60" r="6" className="trace-empty" />
        </g>
      ) : (
        <g clipPath={`url(#${clip})`}>
          {outcome === 'passed' && <path d={FULL} className="trace-line" />}
          {outcome === 'failed' && (
            <>
              <path d={`${LEAD} S 130 46, 150 58`} className="trace-line" />
              <path d="M150 58 L156 64 M160 70 L172 124" className="trace-line trace-fault" />
            </>
          )}
          {outcome === 'unknown' && (
            <>
              <path d={`${LEAD} S 124 58, 136 56`} className="trace-line" />
              <path d="M136 56 S 164 72, 180 80 S 214 64, 228 60" className="trace-dotted" />
            </>
          )}
        </g>
      )}
      {outcome === 'passed' && (
        <g className="trace-late">
          <circle cx="228" cy="60" r="9" className="trace-halo" />
          <circle cx="228" cy="60" r="5" className="trace-end" />
        </g>
      )}
      {outcome === 'failed' && (
        <path d="M184 112 L198 126 M198 112 L184 126" className="trace-late trace-cross" />
      )}
      {outcome === 'unknown' && (
        <g className="trace-late trace-static">
          {[
            [186, 70],
            [196, 58],
            [204, 76],
            [214, 64],
            [222, 72],
            [210, 52],
            [230, 58],
            [192, 86],
          ].map(([x, y]) => (
            <circle key={`${x}-${y}`} cx={x} cy={y} r="1.6" />
          ))}
        </g>
      )}
    </svg>
  );
}
