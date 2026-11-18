import { useEffect, useState } from 'react';
import { environmentLabel } from '../lib/environmentLabel.js';

// A fixed badge on every page of a non-production server ("Staging"), so it is never mistaken
// for production. It reads the public health endpoint once and renders nothing when unset.
export function EnvironmentLabel() {
  const [label, setLabel] = useState<string | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    fetch('/api/v1/health', { signal: controller.signal, credentials: 'same-origin' })
      .then((response) => (response.ok ? response.json() : null))
      .then((health) => setLabel(environmentLabel(health)))
      .catch(() => undefined);
    return () => controller.abort();
  }, []);
  if (!label) return null;
  return (
    <p className="environment-label" role="note" aria-label={`Environment: ${label}`}>
      {label}
    </p>
  );
}
