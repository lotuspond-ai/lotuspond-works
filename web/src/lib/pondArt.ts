// Drawing primitives and the animated pond plates for the public introduction, drawn in
// the same hand as the hero scene in components/Pond.tsx. The rest of the page uses the
// field-record vocabulary (ink glyphs, traces) rather than the pond.

export const TAU = Math.PI * 2;
export const clamp = (value: number, low = 0, high = 1) => Math.min(high, Math.max(low, value));
const ease = (value: number) => 1 - (1 - clamp(value)) ** 3;
const easeInOut = (value: number) => {
  const x = clamp(value);
  return x < 0.5 ? 4 * x ** 3 : 1 - (-2 * x + 2) ** 3 / 2;
};
// Progress through the part of a plate's entrance that runs from `start` to `end`.
const span = (progress: number, start: number, end: number) =>
  clamp((progress - start) / (end - start));

// A fixed seed keeps every composition identical on every visit.
export function seeded(seed: number) {
  return () => {
    seed = (seed + 0x6d2b79f5) | 0;
    let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

const PETAL_BASE = '#f3eee4';
const PETAL_TIP = '#e2ab7c';
const BACK_BASE = '#e2dccf';
const BACK_TIP = '#cf9a6d';
const LOTUS = '#c4864a';
const LINE = 'rgba(163, 196, 178, 0.4)';

export type View = {
  w: number;
  h: number;
  s: number;
  p: number;
  t: number;
  ratio: number;
  off: CanvasRenderingContext2D;
};

function stage(view: View) {
  const unit = Math.min(view.w, view.h * 1.25);
  return { cx: view.w / 2, y0: view.h * 0.68, R: unit * 0.3, L: unit * 0.235, unit };
}

function backdrop(c: CanvasRenderingContext2D, view: View, seed: number) {
  const { w, h, s, t } = view;
  const horizon = h * 0.22;
  const sunX = w * 0.8;
  const sky = c.createLinearGradient(0, 0, 0, horizon);
  sky.addColorStop(0, '#0c1612');
  sky.addColorStop(1, '#22352b');
  c.fillStyle = sky;
  c.fillRect(0, 0, w, horizon);
  const halo = c.createRadialGradient(sunX, horizon, 0, sunX, horizon, w * 0.62);
  halo.addColorStop(0, 'rgba(201, 138, 75, 0.34)');
  halo.addColorStop(0.22, 'rgba(201, 138, 75, 0.1)');
  halo.addColorStop(1, 'rgba(201, 138, 75, 0)');
  c.fillStyle = halo;
  c.fillRect(0, 0, w, horizon);
  const water = c.createLinearGradient(0, horizon, 0, h);
  water.addColorStop(0, '#1a2b22');
  water.addColorStop(0.45, '#101c17');
  water.addColorStop(1, '#0a120f');
  c.fillStyle = water;
  c.fillRect(0, horizon, w, h - horizon);
  c.save();
  c.translate(sunX, horizon);
  c.scale(1, 0.34);
  const pool = c.createRadialGradient(0, 0, 0, 0, 0, w * 0.5);
  pool.addColorStop(0, 'rgba(201, 138, 75, 0.2)');
  pool.addColorStop(1, 'rgba(201, 138, 75, 0)');
  c.fillStyle = pool;
  c.fillRect(-w, 0, w * 2, (h - horizon) / 0.34);
  c.restore();
  c.fillStyle = 'rgba(241, 236, 225, 0.13)';
  c.fillRect(0, horizon, w, 1);
  c.fillStyle = LOTUS;
  c.beginPath();
  c.arc(sunX, horizon - 2.2 * s, 2.3 * s, 0, TAU);
  c.fill();

  const random = seeded(seed);
  const rows = 30;
  for (let row = 0; row < rows; row++) {
    const near = ((row + random()) / rows) ** 2;
    const y = horizon + 2 + near * (h - horizon - 4);
    const scale = 0.3 + near * 1.6;
    const count = 2 + Math.floor(random() * 3);
    for (let index = 0; index < count; index++) {
      const x = random() * w;
      const phase = random() * TAU;
      const shimmer = 0.55 + 0.45 * Math.sin(t * (0.4 + random() * 0.6) + phase);
      const lit = Math.exp(-(((x - sunX) / (w * 0.2)) ** 2));
      c.fillStyle = `rgba(226, 236, 229, ${(0.035 + random() * 0.05) * (1 + lit) * shimmer})`;
      c.fillRect(
        x + Math.sin(t * 0.25 + phase) * 3 * scale,
        y,
        (8 + random() * 44) * scale * s,
        near > 0.6 ? 1.5 : 1,
      );
    }
  }
  for (let row = 0; row < 18; row++) {
    const depth = (row + random()) / 18;
    const y = horizon + 3 + depth ** 1.4 * (h - horizon) * 0.8;
    const pulse = (0.5 + 0.5 * Math.sin(t * (1 + random() * 1.5) + random() * TAU)) ** 2;
    const width = (3 + random() * 12) * (0.4 + depth * 1.4) * s;
    c.fillStyle = `rgba(228, 170, 106, ${(0.7 - depth * 0.55) * (0.3 + 0.7 * pulse)})`;
    c.fillRect(sunX + (random() - 0.5) * (6 + depth * 70) * s - width / 2, y, width, 1);
  }
}

export function pad(c: CanvasRenderingContext2D, x: number, y: number, rx: number, alpha = 1) {
  const ry = rx * 0.3;
  const notch = 0.55;
  c.save();
  c.globalAlpha *= alpha;
  c.beginPath();
  c.moveTo(x, y);
  c.ellipse(x, y, rx, ry, 0, notch + 0.17, notch + TAU - 0.17);
  c.closePath();
  const fill = c.createLinearGradient(x, y - ry, x, y + ry);
  fill.addColorStop(0, '#2c4a3b');
  fill.addColorStop(1, '#14251d');
  c.fillStyle = fill;
  c.fill();
  c.strokeStyle = LINE;
  c.lineWidth = 1;
  c.stroke();
  c.beginPath();
  for (let vein = 1; vein < 9; vein++) {
    const angle = notch + 0.17 + (vein * (TAU - 0.34)) / 9;
    c.moveTo(x, y);
    c.lineTo(x + Math.cos(angle) * rx * 0.86, y + Math.sin(angle) * ry * 0.86);
  }
  c.strokeStyle = 'rgba(163, 196, 178, 0.12)';
  c.stroke();
  c.restore();
}

function petal(
  c: CanvasRenderingContext2D,
  angle: number,
  length: number,
  base: string,
  tip: string,
  width = 1,
) {
  const half = length * 0.4 * width;
  c.save();
  c.translate(Math.sin(angle) * length * 0.1, 0);
  c.rotate(angle);
  c.beginPath();
  c.moveTo(0, 0);
  c.bezierCurveTo(-half, -length * 0.22, -half * 0.85, -length * 0.78, 0, -length);
  c.bezierCurveTo(half * 0.85, -length * 0.78, half, -length * 0.22, 0, 0);
  const fill = c.createLinearGradient(0, 0, 0, -length);
  fill.addColorStop(0, base);
  fill.addColorStop(0.5, base);
  fill.addColorStop(1, tip);
  c.fillStyle = fill;
  c.fill();
  c.strokeStyle = 'rgba(36, 48, 41, 0.4)';
  c.lineWidth = 0.8;
  c.stroke();
  c.beginPath();
  c.moveTo(0, -length * 0.12);
  c.lineTo(0, -length * 0.8);
  c.strokeStyle = 'rgba(120, 96, 64, 0.2)';
  c.stroke();
  c.restore();
}

// A lotus seen from the side; `bloom` opens it from a closed bud.
export function lotus(
  c: CanvasRenderingContext2D,
  x: number,
  y: number,
  length: number,
  bloom: number,
  alpha = 1,
) {
  const open = clamp(bloom);
  c.save();
  c.globalAlpha *= alpha;
  c.translate(x, y);
  for (const angle of [-0.8, -0.3, 0.3, 0.8]) {
    petal(c, angle * (0.1 + 0.64 * open), length * 0.92, BACK_BASE, BACK_TIP);
  }
  if (open > 0.35) {
    const reveal = clamp((open - 0.35) / 0.4);
    c.fillStyle = `rgba(201, 176, 102, ${reveal})`;
    c.beginPath();
    c.moveTo(-length * 0.1, -length * 0.16);
    c.lineTo(-length * 0.16, -length * 0.36);
    c.lineTo(length * 0.16, -length * 0.36);
    c.lineTo(length * 0.1, -length * 0.16);
    c.closePath();
    c.fill();
    c.fillStyle = `rgba(220, 199, 126, ${reveal})`;
    c.beginPath();
    c.ellipse(0, -length * 0.36, length * 0.16, length * 0.05, 0, 0, TAU);
    c.fill();
  }
  for (const angle of [-1, -0.55, 0.55, 1]) {
    petal(c, angle * (0.14 + 0.98 * open), length, PETAL_BASE, PETAL_TIP);
  }
  // The centre petal leans toward the reader as the flower opens, so it foreshortens.
  petal(c, 0, length * (1 - 0.42 * open), PETAL_BASE, PETAL_TIP, 1.12);
  c.restore();
}

// Draws `paint` off-screen, then lays it back mirrored below `waterline` in rippled slices.
function reflection(
  c: CanvasRenderingContext2D,
  view: View,
  waterline: number,
  depth: number,
  paint: (context: CanvasRenderingContext2D) => void,
  strength = 0.22,
) {
  const { off, ratio, w, s, t } = view;
  off.setTransform(1, 0, 0, 1, 0, 0);
  off.clearRect(0, 0, off.canvas.width, off.canvas.height);
  off.setTransform(ratio, 0, 0, ratio, 0, 0);
  paint(off);
  c.save();
  for (let row = 0; row < depth; row += 2) {
    const source = waterline - row - 2;
    if (source < 0) break;
    c.globalAlpha = strength * (1 - row / depth);
    const shift = Math.sin(row * 0.32 + t * 2.1) * 1.6 * s * (0.35 + row / depth);
    c.drawImage(off.canvas, 0, source * ratio, w * ratio, 2 * ratio, shift, waterline + row, w, 2);
  }
  c.restore();
}

export const scenes = {
  // Accounts --------------------------------------------------------------
  individual(c: CanvasRenderingContext2D, view: View) {
    backdrop(c, view, 127);
    const { cx, y0, R, L } = stage(view);
    const open = easeInOut(span(view.p, 0.1, 0.9)) * 0.55;
    const base = y0 - R * 0.06;
    reflection(c, view, y0, L * 1.3, (o) => lotus(o, cx, base, L * 0.95, open));
    pad(c, cx, y0, R);
    lotus(c, cx, base, L * 0.95, open);
  },
  organization(c: CanvasRenderingContext2D, view: View) {
    backdrop(c, view, 131);
    const { cx, y0, R, L } = stage(view);
    const colony: [number, number, number, number][] = [
      [-1.55, -0.28, 0.5, 0.2],
      [1.5, -0.3, 0.55, 0.35],
      [-0.55, -0.42, 0.45, 0.1],
      [0.62, 0.18, 0.62, 0.5],
      [-1.0, 0.22, 0.58, 0.3],
      [0, 0, 0.72, 0.55],
    ];
    const order = colony
      .map((item, index) => ({ item, index }))
      .sort((a, b) => a.item[1] - b.item[1]);
    c.save();
    c.setLineDash([2, 5]);
    c.strokeStyle = `rgba(163, 196, 178, ${0.22 * ease(span(view.p, 0.6, 1))})`;
    c.beginPath();
    colony.forEach(([dx, dy], index) => {
      const x = cx + dx * R;
      const y = y0 + dy * R;
      if (index === 0) c.moveTo(x, y);
      else c.lineTo(x, y);
    });
    c.stroke();
    c.restore();
    for (const { item, index } of order) {
      const [dx, dy, size, bloom] = item;
      const grow = ease(span(view.p, 0.05 + index * 0.1, 0.35 + index * 0.1));
      if (grow <= 0) continue;
      const pop = grow < 1 ? grow * (1 + 0.18 * Math.sin(grow * Math.PI)) : 1;
      const x = cx + dx * R;
      const y = y0 + dy * R;
      pad(c, x, y, R * size * pop, grow);
      lotus(c, x, y - R * size * 0.05, L * size * 1.05 * pop, bloom * grow, grow);
    }
  },
};

export type SceneName = keyof typeof scenes;

// Only these scenes draw a reflection, so only they need an off-screen buffer.
export const reflective: ReadonlySet<SceneName> = new Set(['individual']);
