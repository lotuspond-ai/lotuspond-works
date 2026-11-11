import { useEffect, useState } from 'react';
import { api } from '../api.js';

type Entry = { id: string; date: string; title: string; description: string };

const dated = (value: string) =>
  new Date(`${value}T00:00:00`).toLocaleDateString(undefined, {
    day: 'numeric',
    month: 'short',
    year: 'numeric',
  });

// The three latest entries of the real changelog. If it cannot be read, the section is left
// out rather than filled with placeholder news.
export function RecentlyShipped() {
  const [entries, setEntries] = useState<Entry[] | null>(null);
  const [unavailable, setUnavailable] = useState(false);
  useEffect(() => {
    const controller = new AbortController();
    api<{ items: Entry[] }>('/changelog', undefined, undefined, 'GET', controller.signal)
      .then((data) => setEntries(data.items.slice(0, 3)))
      .catch(() => {
        if (!controller.signal.aborted) setUnavailable(true);
      });
    return () => controller.abort();
  }, []);
  if (unavailable || (entries && !entries.length)) return null;
  return (
    <section className="shipped" aria-labelledby="shipped-title" aria-busy={!entries}>
      <div className="shipped-head">
        <h2 id="shipped-title" className="section-title">
          Recently shipped
        </h2>
        <p>Read from the LotusPond Works changelog.</p>
      </div>
      <ol className="shipped-list">
        {(entries ?? [null, null, null]).map((entry, index) => (
          <li key={entry?.id ?? index}>
            {entry ? (
              <>
                <time dateTime={entry.date}>{dated(entry.date)}</time>
                <h3>{entry.title}</h3>
                <p>{entry.description}</p>
              </>
            ) : (
              <span className="shipped-placeholder" aria-hidden="true" />
            )}
          </li>
        ))}
      </ol>
    </section>
  );
}
