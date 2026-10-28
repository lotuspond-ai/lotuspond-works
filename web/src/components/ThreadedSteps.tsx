import { useEffect, useRef, useState } from 'react';
import { InkGlyph, type GlyphName } from './InkGlyph.js';

type Step = readonly [title: string, copy: string, retained: string];
type Point = [number, number];

const clamp = (value: number) => Math.min(1, Math.max(0, value));

// Catmull-Rom through the points, written as cubic Béziers.
function smooth(points: Point[]) {
  const at = (index: number) => points[Math.min(points.length - 1, Math.max(0, index))] as Point;
  let d = `M${at(0)[0]} ${at(0)[1]}`;
  for (let index = 0; index < points.length - 1; index++) {
    const [p0, p1, p2, p3] = [at(index - 1), at(index), at(index + 1), at(index + 2)];
    const c1 = [p1[0] + (p2[0] - p0[0]) / 6, p1[1] + (p2[1] - p0[1]) / 6];
    const c2 = [p2[0] - (p3[0] - p1[0]) / 6, p2[1] - (p3[1] - p1[1]) / 6];
    d += ` C${c1[0]} ${c1[1]} ${c2[0]} ${c2[1]} ${p2[0]} ${p2[1]}`;
  }
  return d;
}

// The workflow as one record: an ink thread runs through every step and inks each step's
// glyph as it arrives there, following the reader's scroll.
export function ThreadedSteps({
  steps,
  glyphs,
  retainedLabel = 'Retained',
}: {
  steps: readonly Step[];
  glyphs: readonly GlyphName[];
  // Names what each step leaves behind, such as a record or a deliverable.
  retainedLabel?: string;
}) {
  const root = useRef<HTMLDivElement>(null);
  const ink = useRef<SVGPathElement>(null);
  const [path, setPath] = useState('');
  const [reached, setReached] = useState(0);
  useEffect(() => {
    const frame = root.current;
    if (!frame) return;
    const still = window.matchMedia('(prefers-reduced-motion: reduce)');
    const wide = window.matchMedia('(min-width: 721px)');
    let anchors: number[] = [];
    let pending = 0;

    function wells() {
      return [...(frame?.querySelectorAll<HTMLElement>('.step-glyph') ?? [])];
    }

    function update() {
      pending = 0;
      if (!frame) return;
      const box = frame.getBoundingClientRect();
      const view = window.innerHeight;
      const progress = still.matches ? 1 : clamp((view * 0.84 - box.top) / (view * 0.5));
      if (ink.current) ink.current.style.strokeDashoffset = String(1 - progress);
      const count = still.matches
        ? steps.length
        : wide.matches
          ? anchors.filter((anchor) => progress >= anchor - 0.03).length
          : wells().filter((well) => well.getBoundingClientRect().top < view * 0.8).length;
      setReached(count);
    }

    function measure() {
      if (!frame) return;
      const box = frame.getBoundingClientRect();
      const centres = wells().map((well): Point => {
        const own = well.getBoundingClientRect();
        return [own.left + own.width / 2 - box.left, own.top + own.height / 2 - box.top];
      });
      const first = centres[0];
      if (!box.width || !first) return;
      const y = first[1];
      const points: Point[] = [[0, y]];
      centres.forEach(([x], index) => {
        const previous = points[points.length - 1] as Point;
        points.push([(previous[0] + x) / 2, y + (index % 2 ? -14 : 14)]);
        points.push([x, y]);
      });
      const last = points[points.length - 1] as Point;
      points.push([(last[0] + box.width) / 2, y - 14], [box.width, y]);
      setPath(smooth(points));
      anchors = centres.map(([x]) => x / box.width);
      update();
    }

    const request = () => {
      if (!pending) pending = requestAnimationFrame(update);
    };
    const resize = new ResizeObserver(measure);
    resize.observe(frame);
    measure();
    window.addEventListener('scroll', request, { passive: true });
    still.addEventListener('change', measure);
    wide.addEventListener('change', measure);
    return () => {
      cancelAnimationFrame(pending);
      resize.disconnect();
      window.removeEventListener('scroll', request);
      still.removeEventListener('change', measure);
      wide.removeEventListener('change', measure);
    };
  }, [steps.length]);

  return (
    <div className="threaded" ref={root}>
      <svg className="thread" aria-hidden="true" focusable="false">
        <path d={path} className="thread-ahead" />
        <path ref={ink} d={path} pathLength={1} className="thread-ink" />
      </svg>
      <ol className="workflow-steps">
        {steps.map(([title, copy, retained], index) => (
          <li key={title}>
            <div className="step-glyph">
              <InkGlyph name={glyphs[index] ?? 'trace'} drawn={index < reached} />
            </div>
            <span className="step-number">{String(index + 1).padStart(2, '0')}</span>
            <h3>{title}</h3>
            <p>{copy}</p>
            <dl>
              <dt>{retainedLabel}</dt>
              <dd>{retained}</dd>
            </dl>
          </li>
        ))}
      </ol>
    </div>
  );
}
