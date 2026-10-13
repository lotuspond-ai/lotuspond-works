import type { CSSProperties, ReactNode } from 'react';
import { useArrival } from '../lib/useArrival.js';

// The field-record glyphs: broad-nib ink marks on a 64-unit square. Each stroke carries its
// order (--i) so a glyph draws itself the way a hand would, then its nodes are set down.
export type GlyphName =
  'snapshot' | 'trace' | 'seal' | 'pulse' | 'network' | 'enclosure' | 'sealed';

const order = (index: number) => ({ '--i': index }) as CSSProperties;
const stroke = (d: string, index: number, className = 'ink') => (
  <path key={d} d={d} pathLength={1} className={className} style={order(index)} />
);
const node = (cx: number, cy: number, r: number, index: number, className = 'node') => (
  <circle key={`${cx}-${cy}`} cx={cx} cy={cy} r={r} className={className} style={order(index)} />
);

const glyphs: Record<GlyphName, ReactNode> = {
  // A frozen scenario snapshot: a viewfinder closing around three seeds.
  snapshot: [
    stroke('M8 20 V8 H20', 0),
    stroke('M44 8 H56 V20', 1),
    stroke('M56 44 V56 H44', 2),
    stroke('M20 56 H8 V44', 3),
    stroke('M22 38 L32 26 L42 38', 4, 'ink ink-fine'),
    node(22, 38, 4, 5),
    node(32, 26, 4.6, 6, 'node node-warm'),
    node(42, 38, 4, 7),
  ],
  // A measured run: a trace with its start and end recorded.
  trace: [
    stroke('M6 38 H14 L20 22 L28 50 L35 14 L42 44 L47 32 H58', 0),
    node(6, 38, 3.6, 2),
    node(58, 32, 4.6, 3, 'node node-warm'),
  ],
  // A decision: a seal around the mark it records.
  seal: [
    stroke('M32 7 A25 25 0 1 1 31.99 7', 0),
    stroke('M32 15 A17 17 0 1 1 31.99 15', 1, 'ink ink-fine'),
    stroke('M23 33 L29.5 39.5 L42 26', 2, 'ink ink-accent'),
  ],
  // Production evidence: signals returning to where they are observed.
  pulse: [
    stroke('M22 48 A10 10 0 0 1 42 48', 0),
    stroke('M14 48 A18 18 0 0 1 50 48', 1),
    stroke('M6 48 A26 26 0 0 1 58 48', 2, 'ink ink-fine'),
    node(32, 48, 4.6, 3, 'node node-warm'),
  ],
  // Connected: one node reaching several.
  network: [
    stroke('M32 32 L32 9', 0),
    stroke('M32 32 L52 20.5', 1),
    stroke('M32 32 L52 43.5', 2),
    stroke('M32 32 L32 55', 3),
    stroke('M32 32 L12 43.5', 4),
    stroke('M32 32 L12 20.5', 5),
    node(32, 9, 3.6, 6),
    node(52, 20.5, 3.6, 6),
    node(52, 43.5, 3.6, 6),
    node(32, 55, 3.6, 6),
    node(12, 43.5, 3.6, 6),
    node(12, 20.5, 3.6, 6),
    node(32, 32, 5.4, 7, 'node node-warm'),
  ],
  // On premises: a small network inside walls you own.
  enclosure: [
    <path key="field" d="M22 40 L32 22 L42 40 Z" className="ink-field" />,
    stroke('M15 8 H49 Q56 8 56 15 V49 Q56 56 49 56 H15 Q8 56 8 49 V15 Q8 8 15 8 Z', 0),
    stroke('M22 40 L32 22 L42 40 Z', 1, 'ink ink-fine'),
    node(22, 40, 4, 2),
    node(32, 22, 4.6, 2, 'node node-warm'),
    node(42, 40, 4, 2),
  ],
  // Air-gapped: a sealed dial; nothing inside reaches the rim.
  sealed: [
    stroke('M32 6 A26 26 0 1 1 31.99 6', 0),
    stroke('M32 13 V19', 1, 'ink ink-fine'),
    stroke('M51 32 H45', 1, 'ink ink-fine'),
    stroke('M32 51 V45', 1, 'ink ink-fine'),
    stroke('M13 32 H19', 1, 'ink ink-fine'),
    node(32, 32, 6, 2, 'node node-warm'),
  ],
};

export function InkGlyph({
  name,
  drawn,
  className = '',
}: {
  name: GlyphName;
  // When omitted, the glyph draws itself once most of it is in view.
  drawn?: boolean;
  className?: string;
}) {
  const [ref, arrived] = useArrival<SVGSVGElement>(0.6);
  return (
    <svg
      ref={ref}
      className={`ink-glyph ${className}`}
      viewBox="0 0 64 64"
      data-drawn={drawn ?? arrived}
      aria-hidden="true"
      focusable="false"
    >
      {glyphs[name]}
    </svg>
  );
}
