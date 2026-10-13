import { useEffect, useState } from 'react';

// Reveals `total` items one at a time while `running`, as records arriving would. With
// reduced motion every item is shown at once.
export function useStream(total: number, every: number, running: boolean) {
  const [shown, setShown] = useState(0);
  useEffect(() => {
    if (!running) return;
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
      setShown(total);
      return;
    }
    setShown(0);
    let count = 0;
    const timer = window.setInterval(() => {
      count += 1;
      setShown(count);
      if (count >= total) window.clearInterval(timer);
    }, every);
    return () => window.clearInterval(timer);
  }, [total, every, running]);
  return shown;
}
