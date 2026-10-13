import { useEffect, useRef, useState } from 'react';

// True once `share` of the element has been in view. Content already on screen arrives at
// mount rather than waiting for an observer, and reduced motion arrives immediately.
export function useArrival<T extends Element>(share = 0.5) {
  const ref = useRef<T>(null);
  const [arrived, setArrived] = useState(false);
  useEffect(() => {
    const element = ref.current;
    if (!element) return;
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
      setArrived(true);
      return;
    }
    const box = element.getBoundingClientRect();
    const shown = Math.min(box.bottom, window.innerHeight) - Math.max(box.top, 0);
    if (box.height && shown / box.height >= share) {
      setArrived(true);
      return;
    }
    const view = new IntersectionObserver(
      ([entry]) => {
        if ((entry?.intersectionRatio ?? 0) >= share) {
          setArrived(true);
          view.disconnect();
        }
      },
      { threshold: [share] },
    );
    view.observe(element);
    return () => view.disconnect();
  }, [share]);
  return [ref, arrived] as const;
}
