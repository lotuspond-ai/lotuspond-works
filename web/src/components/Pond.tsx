import { useEffect, useId, useRef, type ReactNode } from 'react';
import { clamp, seeded, TAU } from '../lib/pondArt.js';

// The public introduction's signature scene: a pond at dusk whose horizon is the
// baseline of the headline. The wordmark's warm dot becomes the sun resting on it.

type Stroke = {
  x: number;
  y: number;
  w: number;
  h: number;
  a: number;
  scale: number;
  phase: number;
  speed: number;
};
type Pad = { x: number; y: number; rx: number; ry: number; notch: number; phase: number };
type Scene = {
  width: number;
  height: number;
  horizon: number;
  sunX: number;
  sunR: number;
  water: Stroke[];
  glints: Stroke[];
  pads: Pad[];
};

// Pads sit toward the edges so they never sit under the reading column on wide screens.
const padLayout = [
  [0.9, 0.2, 0.05],
  [0.97, 0.52, 0.075],
  [0.66, 0.1, 0.028],
  [0.03, 0.82, 0.085],
  [0.78, 0.8, 0.07],
] as const;

function compose(width: number, height: number, horizon: number, sunX: number, sunR: number) {
  const random = seeded(1847);
  const depth = Math.max(1, height - horizon);
  const water: Stroke[] = [];
  const rows = Math.round(clamp(depth / 5, 36, 96));
  for (let row = 0; row < rows; row++) {
    const near = ((row + random()) / rows) ** 2;
    const y = horizon + 3 + near * (depth - 6);
    const scale = 0.3 + near * 1.7;
    const count = 2 + Math.floor(random() * 4);
    for (let index = 0; index < count; index++) {
      const x = random() * width;
      const lit = Math.exp(-(((x - sunX) / (width * 0.16)) ** 2));
      water.push({
        x,
        y,
        w: (16 + random() * 84) * scale,
        h: near > 0.55 ? 2 : 1,
        a: (0.03 + random() * 0.06) * (1 + lit * 1.6),
        scale,
        phase: random() * TAU,
        speed: 0.35 + random() * 0.7,
      });
    }
  }
  const glints: Stroke[] = [];
  const glintRows = Math.round(rows * 0.85);
  for (let row = 0; row < glintRows; row++) {
    const t = (row + random()) / glintRows;
    const near = t ** 1.5;
    const y = horizon + sunR + 2 + near * (depth - 8) * 0.9;
    const spread = 3 + near * Math.min(150, width * 0.11);
    const count = 1 + Math.floor(random() * 3);
    for (let index = 0; index < count; index++) {
      glints.push({
        x: sunX + (random() - 0.5) * 2 * spread,
        y,
        w: (4 + random() * 22) * (0.35 + near * 1.6),
        h: near > 0.6 ? 2 : 1,
        a: (0.8 - t * 0.62) * (0.4 + random() * 0.6),
        scale: 1,
        phase: random() * TAU,
        speed: 0.9 + random() * 1.8,
      });
    }
  }
  const pads = padLayout.map(([x, y, size]) => {
    const near = y ** 1.4;
    const rx = width * size * (0.45 + near);
    return {
      x: x * width,
      y: horizon + 10 + near * (depth - 20),
      rx,
      ry: rx * (0.16 + near * 0.2),
      notch: random() * TAU,
      phase: random() * TAU,
    };
  });
  return { width, height, horizon, sunX, sunR, water, glints, pads } satisfies Scene;
}

function drawPad(context: CanvasRenderingContext2D, pad: Pad, time: number) {
  const gap = 0.34;
  context.save();
  context.translate(pad.x, pad.y + Math.sin(time * 0.6 + pad.phase) * 1.2);
  context.beginPath();
  context.moveTo(0, 0);
  context.ellipse(0, 0, pad.rx, pad.ry, 0, pad.notch + gap / 2, pad.notch + TAU - gap / 2);
  context.closePath();
  context.fillStyle = 'rgba(17, 32, 26, 0.94)';
  context.fill();
  context.strokeStyle = 'rgba(163, 196, 178, 0.3)';
  context.lineWidth = 1;
  context.stroke();
  context.beginPath();
  for (let vein = 1; vein < 7; vein++) {
    const angle = pad.notch + gap / 2 + (vein * (TAU - gap)) / 7;
    context.moveTo(0, 0);
    context.lineTo(Math.cos(angle) * pad.rx * 0.82, Math.sin(angle) * pad.ry * 0.82);
  }
  context.strokeStyle = 'rgba(163, 196, 178, 0.1)';
  context.stroke();
  context.restore();
}

function paint(context: CanvasRenderingContext2D, scene: Scene, time: number, glow: number) {
  const { width, height, horizon, sunX, sunR } = scene;
  context.clearRect(0, 0, width, height);

  const sky = context.createLinearGradient(0, 0, 0, horizon);
  sky.addColorStop(0, '#0b1411');
  sky.addColorStop(0.62, '#122019');
  sky.addColorStop(1, '#1f3129');
  context.fillStyle = sky;
  context.fillRect(0, 0, width, horizon);

  const reach = Math.max(width, height);
  const halo = context.createRadialGradient(sunX, horizon, 0, sunX, horizon, reach * 0.62);
  halo.addColorStop(0, `rgba(201, 138, 75, ${0.3 * glow})`);
  halo.addColorStop(0.16, `rgba(201, 138, 75, ${0.12 * glow})`);
  halo.addColorStop(0.5, `rgba(201, 138, 75, ${0.03 * glow})`);
  halo.addColorStop(1, 'rgba(201, 138, 75, 0)');
  context.fillStyle = halo;
  context.fillRect(0, 0, width, horizon);

  const water = context.createLinearGradient(0, horizon, 0, height);
  water.addColorStop(0, '#17271f');
  water.addColorStop(0.35, '#0f1a16');
  water.addColorStop(1, '#09110e');
  context.fillStyle = water;
  context.fillRect(0, horizon, width, height - horizon);

  context.save();
  context.beginPath();
  context.rect(0, horizon, width, height - horizon);
  context.clip();
  context.translate(sunX, horizon);
  context.scale(1, 0.3);
  const pool = context.createRadialGradient(0, 0, 0, 0, 0, width * 0.5);
  pool.addColorStop(0, `rgba(201, 138, 75, ${0.2 * glow})`);
  pool.addColorStop(1, 'rgba(201, 138, 75, 0)');
  context.fillStyle = pool;
  context.fillRect(-width, 0, width * 2, (height - horizon) / 0.3);
  context.restore();

  const line = context.createLinearGradient(0, 0, width, 0);
  const at = sunX / width;
  line.addColorStop(0, 'rgba(241, 236, 225, 0.08)');
  line.addColorStop(clamp(at - 0.3), 'rgba(241, 236, 225, 0.16)');
  line.addColorStop(clamp(at), `rgba(226, 172, 112, ${0.22 + 0.5 * glow})`);
  line.addColorStop(clamp(at + 0.3), 'rgba(241, 236, 225, 0.16)');
  line.addColorStop(1, 'rgba(241, 236, 225, 0.08)');
  context.fillStyle = line;
  context.fillRect(0, horizon, width, 1);

  for (const stroke of scene.water) {
    const shimmer = 0.55 + 0.45 * Math.sin(time * stroke.speed + stroke.phase);
    const drift = Math.sin(time * 0.22 + stroke.phase) * 4 * stroke.scale;
    context.fillStyle = `rgba(226, 236, 229, ${stroke.a * shimmer})`;
    context.fillRect(stroke.x + drift, stroke.y, stroke.w, stroke.h);
  }

  for (const pad of scene.pads) drawPad(context, pad, time);

  const ripple = scene.pads[0];
  if (ripple && time > 0) {
    for (let ring = 0; ring < 3; ring++) {
      const life = (time * 0.09 + ring / 3) % 1;
      context.beginPath();
      context.ellipse(
        ripple.x,
        ripple.y,
        ripple.rx * (1.1 + life * 1.9),
        ripple.ry * (1.1 + life * 1.9),
        0,
        0,
        TAU,
      );
      context.strokeStyle = `rgba(163, 196, 178, ${0.16 * (1 - life) ** 2})`;
      context.stroke();
    }
  }

  for (const glint of scene.glints) {
    const pulse = (0.5 + 0.5 * Math.sin(time * glint.speed + glint.phase)) ** 2;
    context.fillStyle = `rgba(228, 170, 106, ${glint.a * (0.25 + 0.75 * pulse) * glow})`;
    context.fillRect(glint.x - glint.w / 2, glint.y, glint.w, glint.h);
  }

  const disc = context.createRadialGradient(
    sunX,
    horizon - sunR,
    0,
    sunX,
    horizon - sunR,
    sunR * 5,
  );
  disc.addColorStop(0, `rgba(230, 172, 108, ${0.32 * glow})`);
  disc.addColorStop(1, 'rgba(230, 172, 108, 0)');
  context.fillStyle = disc;
  context.fillRect(sunX - sunR * 5, horizon - sunR * 6, sunR * 10, sunR * 10);
}

// Layout offsets ignore the entrance transform, so the scene matches where the title comes to rest.
function offsetWithin(element: HTMLElement, ancestor: HTMLElement) {
  let left = 0;
  let top = 0;
  let node: HTMLElement | null = element;
  while (node && node !== ancestor) {
    left += node.offsetLeft;
    top += node.offsetTop;
    node = node.offsetParent as HTMLElement | null;
  }
  return { left, top };
}

export function Pond({
  as: Heading = 'h2',
  id,
  title,
  eyebrow,
  className = '',
  children,
}: {
  as?: 'h1' | 'h2';
  id: string;
  title: string;
  eyebrow?: ReactNode;
  className?: string;
  children?: ReactNode;
}) {
  const filterId = `pond-ripple-${useId().replace(/[^a-zA-Z0-9]/g, '')}`;
  const stage = useRef<HTMLElement>(null);
  const canvas = useRef<HTMLCanvasElement>(null);
  const wrap = useRef<HTMLDivElement>(null);
  const heading = useRef<HTMLHeadingElement>(null);
  const sun = useRef<HTMLSpanElement>(null);
  const turbulence = useRef<SVGFETurbulenceElement>(null);
  const displacement = useRef<SVGFEDisplacementMapElement>(null);
  const split = title.lastIndexOf(' ') + 1;
  const lead = title.slice(0, split);
  const last = title.slice(split);

  useEffect(() => {
    const root = stage.current;
    const surface = canvas.current;
    const titleWrap = wrap.current;
    const titleHeading = heading.current;
    const disc = sun.current;
    const context = surface?.getContext('2d');
    if (!root || !surface || !titleWrap || !titleHeading || !disc || !context) return;
    const still = window.matchMedia('(prefers-reduced-motion: reduce)');
    const started = performance.now();
    let scene: Scene | null = null;
    let ratio = 1;
    let frame = 0;
    let previous = 0;
    let visible = false;

    function measure() {
      if (!root || !surface || !titleWrap || !titleHeading || !disc) return;
      ratio = Math.min(2, window.devicePixelRatio || 1);
      const width = root.clientWidth;
      const height = root.clientHeight;
      // A scene without layout (hidden, or mid-resize) has no horizon to draw against.
      if (!width || !height) {
        scene = null;
        return;
      }
      surface.width = Math.round(width * ratio);
      surface.height = Math.round(height * ratio);
      const place = offsetWithin(disc, root);
      const inWrap = offsetWithin(disc, titleWrap);
      const descent =
        titleHeading.offsetTop + titleHeading.offsetHeight - (inWrap.top + disc.offsetHeight);
      titleWrap.style.setProperty('--descent', `${Math.max(0, descent)}px`);
      scene = compose(
        width,
        height,
        place.top + disc.offsetHeight,
        place.left + disc.offsetWidth / 2,
        disc.offsetWidth / 2,
      );
    }

    function render(now: number) {
      if (!context || !scene || !root) return;
      const moving = !still.matches;
      const time = moving ? (now - started) / 1000 : 0;
      const glow = moving ? 1 - (1 - clamp((now - started - 500) / 1600)) ** 3 : 1;
      context.setTransform(ratio, 0, 0, ratio, 0, 0);
      paint(context, scene, time, glow);
      // The reflection settles as the reader moves past the scene.
      const calm = moving
        ? clamp(-root.getBoundingClientRect().top / (root.clientHeight * 0.6))
        : 0;
      const swell = moving ? 3 * Math.sin(time * 1.3) : 0;
      displacement.current?.setAttribute('scale', ((13 + swell) * (1 - calm)).toFixed(2));
      turbulence.current?.setAttribute(
        'baseFrequency',
        `0.008 ${(0.085 + (moving ? 0.006 * Math.sin(time * 0.7) : 0)).toFixed(4)}`,
      );
    }

    function loop(now: number) {
      frame = 0;
      if (now - previous >= 33) {
        previous = now;
        render(now);
      }
      schedule();
    }

    function schedule() {
      if (frame || !visible || still.matches || document.visibilityState !== 'visible') return;
      frame = requestAnimationFrame(loop);
    }

    function refresh() {
      measure();
      render(performance.now());
      schedule();
    }

    const resize = new ResizeObserver(refresh);
    resize.observe(root);
    const view = new IntersectionObserver(
      ([entry]) => {
        visible = !!entry?.isIntersecting;
        if (visible) root.dataset.shown = 'true';
        schedule();
      },
      { threshold: 0.2 },
    );
    view.observe(root);
    // A scene already on screen starts at once rather than waiting for the observer.
    const box = root.getBoundingClientRect();
    if (box.top < window.innerHeight && box.bottom > 0) {
      visible = true;
      root.dataset.shown = 'true';
    }
    refresh();
    document.addEventListener('visibilitychange', schedule);
    still.addEventListener('change', refresh);
    void document.fonts?.ready.then(refresh);
    return () => {
      cancelAnimationFrame(frame);
      resize.disconnect();
      view.disconnect();
      document.removeEventListener('visibilitychange', schedule);
      still.removeEventListener('change', refresh);
    };
  }, []);

  const words = (anchor?: typeof sun) => (
    <span className="pond-title-text">
      {lead}
      <span className="pond-last">
        {last}
        <span className="pond-sun" ref={anchor} aria-hidden="true" />
      </span>
    </span>
  );
  return (
    <section ref={stage} className={`pond ${className}`} aria-labelledby={id}>
      <canvas ref={canvas} className="pond-canvas" aria-hidden="true" />
      <div className="pond-grain" aria-hidden="true" />
      <svg className="pond-filters" aria-hidden="true" focusable="false" width="0" height="0">
        <filter id={filterId} x="-4%" y="-10%" width="108%" height="120%">
          <feTurbulence
            ref={turbulence}
            type="fractalNoise"
            baseFrequency="0.008 0.085"
            numOctaves={2}
            seed={7}
            result="swell"
          />
          <feDisplacementMap
            ref={displacement}
            in="SourceGraphic"
            in2="swell"
            scale={13}
            xChannelSelector="R"
            yChannelSelector="G"
          />
        </filter>
      </svg>
      <div className="landing-container pond-inner">
        <div className="pond-sky">
          {eyebrow}
          <div className="pond-title-wrap" ref={wrap}>
            <Heading id={id} ref={heading} className="pond-title" tabIndex={-1}>
              {words(sun)}
            </Heading>
            <div
              className="pond-title pond-reflection"
              aria-hidden="true"
              style={{ filter: `url(#${filterId})` }}
            >
              {words()}
            </div>
          </div>
        </div>
        <div className="pond-water">{children}</div>
      </div>
    </section>
  );
}
