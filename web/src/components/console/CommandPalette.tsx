import { useEffect, useMemo, useRef, useState, type KeyboardEvent } from 'react';
import { rank, type Command } from '../../lib/commands.js';
import { Icon } from './icons.js';

export type { Command } from '../../lib/commands.js';

// A modal combobox. Focus stays in the search field: arrow keys move the active option, Enter
// runs it, Escape closes, and Tab cannot leave the dialog for the page behind it.
export function CommandPalette({
  open,
  commands,
  onClose,
}: {
  open: boolean;
  commands: readonly Command[];
  onClose: () => void;
}) {
  const [query, setQuery] = useState('');
  const [index, setIndex] = useState(0);
  const input = useRef<HTMLInputElement>(null);
  const returnFocus = useRef<HTMLElement | null>(null);
  const results = useMemo(() => rank(commands, query), [commands, query]);
  // Consecutive results of one section form a labelled group. Ranking can interleave sections,
  // so a section may appear as more than one group.
  const groups = useMemo(() => {
    const sections: {
      key: string;
      title: string;
      items: { command: Command; position: number }[];
    }[] = [];
    results.forEach((command, position) => {
      const last = sections[sections.length - 1];
      if (last?.title === command.section) last.items.push({ command, position });
      else
        sections.push({
          key: `${command.section}-${position}`,
          title: command.section,
          items: [{ command, position }],
        });
    });
    return sections;
  }, [results]);

  useEffect(() => {
    if (!open) return;
    returnFocus.current = document.activeElement as HTMLElement | null;
    setQuery('');
    setIndex(0);
    input.current?.focus();
    return () => returnFocus.current?.focus();
  }, [open]);
  useEffect(() => setIndex(0), [query]);
  useEffect(() => {
    document.getElementById(`command-${results[index]?.id}`)?.scrollIntoView({ block: 'nearest' });
  }, [index, results]);

  if (!open) return null;
  function choose(command?: Command) {
    if (!command) return;
    onClose();
    command.run();
  }
  function keys(event: KeyboardEvent) {
    if (event.key === 'ArrowDown') {
      event.preventDefault();
      setIndex((current) => Math.min(results.length - 1, current + 1));
    } else if (event.key === 'ArrowUp') {
      event.preventDefault();
      setIndex((current) => Math.max(0, current - 1));
    } else if (event.key === 'Enter') {
      event.preventDefault();
      choose(results[index]);
    }
  }
  // Handled on the dialog, so they apply wherever focus is inside it.
  function dialogKeys(event: KeyboardEvent) {
    if (event.key === 'Escape') {
      event.preventDefault();
      onClose();
    } else if (event.key === 'Tab') {
      event.preventDefault();
      input.current?.focus();
    }
  }
  return (
    <div className="palette-backdrop" onPointerDown={onClose}>
      <div
        className="palette"
        role="dialog"
        aria-modal="true"
        aria-label="Search or jump to"
        onPointerDown={(event) => event.stopPropagation()}
        onKeyDown={dialogKeys}
      >
        <div className="palette-input">
          <Icon name="search" />
          <input
            ref={input}
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            onKeyDown={keys}
            placeholder="Search pages and actions…"
            role="combobox"
            aria-expanded="true"
            aria-controls="command-list"
            aria-autocomplete="list"
            aria-activedescendant={results[index] ? `command-${results[index].id}` : undefined}
            aria-label="Search pages and actions"
          />
          <kbd aria-hidden="true">Esc</kbd>
        </div>
        <p className="sr-only" role="status">
          {results.length === 0
            ? 'No results'
            : `${results.length} ${results.length === 1 ? 'result' : 'results'}`}
        </p>
        {/* Scrolls, but is not a Tab stop: the combobox owns keyboard movement. */}
        <div
          className="palette-list"
          id="command-list"
          role="listbox"
          aria-label="Results"
          tabIndex={-1}
        >
          {groups.map((group) => (
            <div key={group.key} role="group" aria-label={group.title}>
              <p className="palette-section" aria-hidden="true">
                {group.title}
              </p>
              {group.items.map(({ command, position }) => (
                <div
                  key={command.id}
                  id={`command-${command.id}`}
                  role="option"
                  aria-selected={position === index}
                  className="palette-option"
                  onPointerMove={() => setIndex(position)}
                  onClick={() => choose(command)}
                >
                  <Icon name={command.icon} />
                  <span>{command.label}</span>
                  {position === index && <Icon name="arrow" className="palette-go" />}
                </div>
              ))}
            </div>
          ))}
        </div>
        {results.length === 0 && <p className="palette-empty">Nothing matches “{query}”.</p>}
      </div>
    </div>
  );
}
