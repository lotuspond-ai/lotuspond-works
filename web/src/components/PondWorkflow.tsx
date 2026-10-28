import { useEffect, useRef } from 'react';
import { clamp } from '../lib/pondArt.js';

// The hero's second idea: a workflow package drawn on the water. A warm light (the case)
// hops from pad to pad, circles while the agent works, waits until a person has reviewed,
// then lands where the case is written. The four nodes, their kinds and their order are the
// synthetic package in contracts/examples/reviewed-action.v1.json.

type Stop = { kind: string; label: string; x: number; y: number; rx: number; ry: number };
type Point = readonly [number, number];

const stops: Stop[] = [
  { kind: 'connector', label: 'Read the case', x: 64, y: 74, rx: 30 },
  { kind: 'agent', label: 'Propose an update', x: 212, y: 124, rx: 38 },
  { kind: 'human review', label: 'A person reviews', x: 362, y: 182, rx: 47 },
  { kind: 'connector', label: 'Write the case', x: 496, y: 246, rx: 56 },
].map((stop) => ({ ...stop, ry: stop.rx * (0.26 + (stop.y / 320) * 0.12) }));

// Each hop arcs above the water, so the path never crosses a label set below a pad.
const hops = stops.slice(1).map((to, index) => {
  const from = stops[index] as Stop;
  const control: Point = [(from.x + to.x) / 2, Math.min(from.y, to.y) - 70];
  return { from: [from.x, from.y] as Point, control, to: [to.x, to.y] as Point };
});

// One cycle, in seconds. Arrivals are when the light lands on each pad.
const cycle = 13.5;
const arrive = [0.2, 2.6, 6, 9.8] as const;
const hopAt = [1.6, 5, 8.8] as const;
const hopFor = 1;
const approved = 8;
const settle = 12.2;
const gone = 13;

const ease = (t: number) => 0.5 - Math.cos(Math.PI * t) / 2;

function along(hop: (typeof hops)[number], t: number): Point {
  const u = 1 - t;
  return [
    u * u * hop.from[0] + 2 * u * t * hop.control[0] + t * t * hop.to[0],
    u * u * hop.from[1] + 2 * u * t * hop.control[1] + t * t * hop.to[1],
  ];
}

function light(time: number): { at: Point; alpha: number; lift: number } {
  const fade = 1 - clamp((time - settle) / (gone - settle));
  for (const [index, start] of hopAt.entries()) {
    const hop = hops[index];
    if (hop && time >= start && time < start + hopFor) {
      const t = ease((time - start) / hopFor);
      return { at: along(hop, t), alpha: 1, lift: Math.sin(Math.PI * t) };
    }
  }
  const agent = stops[1] as Stop;
  if (time >= (arrive[1] as number) && time < hopAt[1]) {
    // The agent works: the case circles its pad, easing out from the centre and back.
    const span = time - arrive[1];
    const reach = clamp(span / 0.4) * clamp((hopAt[1] - time) / 0.4);
    const angle = span * 3.4;
    return {
      at: [
        agent.x + Math.cos(angle) * agent.rx * 0.46 * reach,
        agent.y + Math.sin(angle) * agent.ry * 0.46 * reach,
      ],
      alpha: 1,
      lift: 0,
    };
  }
  const resting = [...arrive].reverse().findIndex((at) => time >= at);
  const stop = stops[resting === -1 ? 0 : stops.length - 1 - resting] as Stop;
  // While the case waits for a person, its light breathes rather than holding still.
  const waiting = time >= arrive[2] && time < approved ? 0.72 + 0.28 * Math.cos(time * 3) : 1;
  return {
    at: [stop.x, stop.y],
    alpha: clamp((time - arrive[0]) / 0.5) * fade * waiting,
    lift: 0,
  };
}

export function PondWorkflow({ paused = false }: { paused?: boolean }) {
  const root = useRef<SVGSVGElement>(null);
  const halted = useRef(paused);
  const resume = useRef<() => void>(() => undefined);

  useEffect(() => {
    halted.current = paused;
    resume.current();
  }, [paused]);

  useEffect(() => {
    const svg = root.current;
    if (!svg) return;
    const still = window.matchMedia('(prefers-reduced-motion: reduce)');
    const pick = <T extends Element>(selector: string) => [...svg.querySelectorAll<T>(selector)];
    const inks = pick<SVGPathElement>('.flow-ink');
    const splashes = pick<SVGEllipseElement>('.flow-splash');
    const labels = pick<SVGGElement>('.flow-stop-label');
    const pads = pick<SVGGElement>('.flow-stop');
    const caseLight = svg.querySelector<SVGGElement>('.flow-case');
    const wait = svg.querySelector<SVGEllipseElement>('.flow-wait');
    const check = svg.querySelector<SVGPathElement>('.flow-check');
    let clock = 0;
    let previous = 0;
    let frame = 0;
    let visible = false;

    function draw(time: number, moving: boolean) {
      const fade = moving ? 1 - clamp((time - settle) / (gone - settle)) : 1;
      inks.forEach((ink, index) => {
        const start = hopAt[index] ?? 0;
        const drawn = moving ? clamp((time - start) / hopFor) : 1;
        ink.style.strokeDashoffset = String(1 - drawn);
        ink.style.opacity = String(fade);
      });
      const active = moving
        ? arrive.reduce((current, at, index) => (time >= at ? index : current), -1)
        : -1;
      labels.forEach((label, index) => {
        label.dataset.active = String(moving && index === active && time < settle);
      });
      pads.forEach((pad, index) => {
        pad.dataset.active = String(moving && index === active && time < settle);
      });
      splashes.forEach((splash, index) => {
        const stop = stops[Math.min(index, stops.length - 1)] as Stop;
        // The last pad gets a second, later ring: the case has been written.
        const landed = (arrive[Math.min(index, arrive.length - 1)] ?? 0) + (index > 3 ? 0.35 : 0);
        const life = moving ? (time - landed) / 1.4 : 1;
        const shown = life >= 0 && life < 1;
        splash.setAttribute('rx', (stop.rx * (1 + clamp(life) * 1.1)).toFixed(1));
        splash.setAttribute('ry', (stop.ry * (1 + clamp(life) * 1.1)).toFixed(1));
        splash.style.opacity = shown ? (0.55 * (1 - life) ** 2).toFixed(3) : '0';
      });
      if (wait) {
        const before = clamp((time - arrive[2]) / 0.3) * (time < approved ? 1 : 0);
        const after = time >= approved ? 1 - clamp((time - approved) / 0.7) : 0;
        wait.style.opacity = moving ? String(Math.max(before * 0.8, after)) : '0';
        wait.dataset.approved = String(time >= approved);
        wait.style.strokeDashoffset = String(-time * 9);
      }
      if (check) {
        const inked = moving ? clamp((time - approved) / 0.45) : 1;
        check.style.strokeDashoffset = String(1 - inked);
        check.style.opacity = String(fade);
      }
      if (caseLight) {
        const { at, alpha, lift } = moving
          ? light(time)
          : { at: [0, 0] as Point, alpha: 0, lift: 0 };
        caseLight.setAttribute(
          'transform',
          `translate(${at[0].toFixed(1)} ${at[1].toFixed(1)}) scale(${(1 + lift * 0.25).toFixed(3)})`,
        );
        caseLight.style.opacity = alpha.toFixed(3);
      }
    }

    function loop(now: number) {
      frame = 0;
      if (!previous) previous = now;
      if (now - previous >= 33) {
        clock = (clock + Math.min(0.1, (now - previous) / 1000)) % cycle;
        previous = now;
        draw(clock, true);
      }
      schedule();
    }

    function schedule() {
      if (frame) return;
      if (!visible || halted.current || still.matches || document.visibilityState !== 'visible') {
        // A paused or hidden figure resumes where it stopped instead of jumping ahead.
        previous = 0;
        return;
      }
      frame = requestAnimationFrame(loop);
    }

    function refresh() {
      // Reduced motion shows the finished path: every hop inked and the review approved.
      if (still.matches) draw(0, false);
      else draw(clock, true);
      schedule();
    }

    resume.current = schedule;
    const view = new IntersectionObserver(
      ([entry]) => {
        visible = !!entry?.isIntersecting;
        schedule();
      },
      { threshold: 0.2 },
    );
    view.observe(svg);
    // A figure already on screen starts at once rather than waiting for the observer.
    const box = svg.getBoundingClientRect();
    visible = box.top < window.innerHeight && box.bottom > 0 && box.width > 0;
    refresh();
    document.addEventListener('visibilitychange', schedule);
    still.addEventListener('change', refresh);
    return () => {
      cancelAnimationFrame(frame);
      view.disconnect();
      resume.current = () => undefined;
      document.removeEventListener('visibilitychange', schedule);
      still.removeEventListener('change', refresh);
    };
  }, []);

  const review = stops[2] as Stop;
  return (
    <svg
      ref={root}
      className="pond-flow"
      viewBox="0 0 560 320"
      role="img"
      aria-label="A workflow drawn on the water: a connector reads the case, an agent proposes an update, a person reviews it, and a connector writes the case."
      focusable="false"
    >
      {hops.map((hop, index) => {
        const d = `M${hop.from[0]} ${hop.from[1]} Q${hop.control[0]} ${hop.control[1]} ${hop.to[0]} ${hop.to[1]}`;
        return (
          <g key={d}>
            <path d={d} className="flow-wake" />
            <path d={d} className="flow-ink" pathLength={1} data-hop={index} />
          </g>
        );
      })}
      {[...stops, stops[stops.length - 1] as Stop].map((stop, index) => (
        <ellipse
          key={`splash-${index}`}
          className="flow-splash"
          cx={stop.x}
          cy={stop.y}
          rx={stop.rx}
          ry={stop.ry}
        />
      ))}
      {stops.map((stop, index) => (
        <g key={stop.label} className="flow-stop" data-kind={stop.kind.replace(' ', '-')}>
          <ellipse className="flow-pad" cx={stop.x} cy={stop.y} rx={stop.rx} ry={stop.ry} />
          <path
            className="flow-vein"
            d={[0.7, 1.5, 2.3, 3.1, 3.9, 4.7, 5.5]
              .map(
                (angle) =>
                  `M${stop.x} ${stop.y} L${(stop.x + Math.cos(angle + index) * stop.rx * 0.8).toFixed(1)} ${(stop.y + Math.sin(angle + index) * stop.ry * 0.8).toFixed(1)}`,
              )
              .join(' ')}
          />
        </g>
      ))}
      <ellipse
        className="flow-wait"
        cx={review.x}
        cy={review.y}
        rx={review.rx * 1.28}
        ry={review.ry * 1.36}
        pathLength={100}
      />
      <path
        className="flow-check"
        // Beside the resting case rather than under its glow.
        d={`M${review.x + 13} ${review.y - 1} L${review.x + 20} ${review.y + 4} L${review.x + 33} ${review.y - 7}`}
        pathLength={1}
      />
      {stops.map((stop) => (
        <g key={stop.label} className="flow-stop-label">
          <text className="flow-kind" x={stop.x} y={stop.y + stop.ry + 20}>
            {stop.kind}
          </text>
          <text className="flow-label" x={stop.x} y={stop.y + stop.ry + 37}>
            {stop.label}
          </text>
        </g>
      ))}
      <g className="flow-case">
        <circle className="flow-case-glow" r={13} />
        <circle className="flow-case-core" r={4.5} />
      </g>
    </svg>
  );
}
