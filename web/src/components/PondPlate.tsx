import { useEffect, useRef } from 'react';
import { clamp, reflective, scenes, type SceneName } from '../lib/pondArt.js';

const ENTRANCE_MS = 2800;

// A small animated pond that performs its section's idea once the reader arrives,
// then keeps only a quiet shimmer. Hovering replays it; reduced motion shows the result.
export function PondPlate({ scene, className = '' }: { scene: SceneName; className?: string }) {
  const canvas = useRef<HTMLCanvasElement>(null);
  useEffect(() => {
    const surface = canvas.current;
    const context = surface?.getContext('2d');
    const off = document.createElement('canvas').getContext('2d');
    if (!surface || !context || !off) return;
    off.canvas.width = off.canvas.height = 1;
    const still = window.matchMedia('(prefers-reduced-motion: reduce)');
    const paint = scenes[scene];
    let ratio = 1;
    let width = 0;
    let height = 0;
    let started: number | null = null;
    let visible = false;
    let frame = 0;
    let previous = 0;

    function size() {
      if (!surface || !off) return;
      ratio = Math.min(2, window.devicePixelRatio || 1);
      width = surface.clientWidth;
      height = surface.clientHeight;
      surface.width = Math.round(width * ratio);
      surface.height = Math.round(height * ratio);
      if (reflective.has(scene)) {
        off.canvas.width = surface.width;
        off.canvas.height = surface.height;
      }
    }

    function draw(now: number) {
      if (!context || !off || !width || !height) return;
      const moving = !still.matches;
      const progress = moving ? (started === null ? 0 : clamp((now - started) / ENTRANCE_MS)) : 1;
      context.setTransform(ratio, 0, 0, ratio, 0, 0);
      context.clearRect(0, 0, width, height);
      paint(context, {
        w: width,
        h: height,
        s: width / 400,
        p: progress,
        t: moving ? now / 1000 : 0,
        ratio,
        off,
      });
    }

    function loop(now: number) {
      frame = 0;
      if (now - previous >= 33) {
        previous = now;
        draw(now);
      }
      schedule();
    }

    function schedule() {
      if (frame || !visible || still.matches || document.visibilityState !== 'visible') return;
      frame = requestAnimationFrame(loop);
    }

    function arrive(share: number) {
      // Wait until most of the plate is in view so the reader sees the whole performance.
      if (started === null && share >= 0.55) started = performance.now();
    }

    function refresh() {
      size();
      draw(performance.now());
      schedule();
    }

    const resize = new ResizeObserver(refresh);
    resize.observe(surface);
    const view = new IntersectionObserver(
      ([entry]) => {
        visible = !!entry?.isIntersecting;
        arrive(entry?.intersectionRatio ?? 0);
        schedule();
      },
      { threshold: [0, 0.3, 0.55, 0.8] },
    );
    view.observe(surface);
    const box = surface.getBoundingClientRect();
    const shown = Math.min(box.bottom, window.innerHeight) - Math.max(box.top, 0);
    if (shown > 0) {
      visible = true;
      arrive(box.height ? shown / box.height : 0);
    }
    const holder = surface.parentElement;
    const replay = () => {
      if (still.matches || started === null) return;
      if (performance.now() - started >= ENTRANCE_MS) started = performance.now();
    };
    holder?.addEventListener('pointerenter', replay);
    document.addEventListener('visibilitychange', schedule);
    still.addEventListener('change', refresh);
    refresh();
    return () => {
      cancelAnimationFrame(frame);
      resize.disconnect();
      view.disconnect();
      holder?.removeEventListener('pointerenter', replay);
      document.removeEventListener('visibilitychange', schedule);
      still.removeEventListener('change', refresh);
    };
  }, [scene]);
  return <canvas ref={canvas} className={`pond-plate ${className}`} aria-hidden="true" />;
}
