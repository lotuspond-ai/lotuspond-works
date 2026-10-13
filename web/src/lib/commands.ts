import type { IconName } from '../components/console/icons.js';

export type Command = {
  id: string;
  label: string;
  section: string;
  icon: IconName;
  run: () => void;
};

// Prefix matches first, then matches at a word start, then anywhere, the way people type
// command names. Every command is also reachable from the visible navigation.
export function rank<T extends Pick<Command, 'label' | 'section'>>(
  commands: readonly T[],
  query: string,
): T[] {
  const needle = query.trim().toLowerCase();
  if (!needle) return [...commands];
  return commands
    .map((command) => {
      const label = command.label.toLowerCase();
      const score = label.startsWith(needle)
        ? 0
        : label.split(/\s+/).some((word) => word.startsWith(needle))
          ? 1
          : label.includes(needle) || command.section.toLowerCase().includes(needle)
            ? 2
            : -1;
      return { command, score };
    })
    .filter((entry) => entry.score >= 0)
    .sort((a, b) => a.score - b.score)
    .map((entry) => entry.command);
}
