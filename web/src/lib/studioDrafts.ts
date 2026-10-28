// The drafts a reader keeps in this browser, several per workspace and project. They never
// leave the browser until one is published. Pure, so studioDrafts.test.ts covers storage,
// the move from the single-draft key, ordering and the cap.
import { isRecord } from './packageContract.js';
import { readDraft, type Draft } from './studio.js';

export type StoredDraft = { id: string; updatedAt: string; draft: Draft };
// Most recently changed first. `open` is the draft on the canvas, or null for the list.
export type DraftShelf = { drafts: StoredDraft[]; open: string | null };

// Browser storage is small and shared with the rest of the console, so the shelf is bounded.
export const MAX_DRAFTS = 20;

const scope = (workspace: string, projectId: string) => `${workspace}.${projectId || 'workspace'}`;
export const shelfKey = (workspace: string, projectId: string) =>
  `lotuspond.studio.drafts.${scope(workspace, projectId)}`;
// Where the Studio kept its one draft before drafts were named; read once and moved here.
export const singleDraftKey = (workspace: string, projectId: string) =>
  `lotuspond.studio.${scope(workspace, projectId)}`;

const newest = (a: StoredDraft, b: StoredDraft) =>
  a.updatedAt < b.updatedAt ? 1 : a.updatedAt > b.updatedAt ? -1 : 0;

export function readShelf(
  stored: string | null,
  single: Draft | null,
  now: string,
  newId: () => string,
): DraftShelf {
  const drafts: StoredDraft[] = [];
  let open: string | null = null;
  try {
    const raw: unknown = stored ? JSON.parse(stored) : null;
    if (isRecord(raw) && Array.isArray(raw.drafts)) {
      for (const item of raw.drafts) {
        if (!isRecord(item) || typeof item.id !== 'string' || !item.id) continue;
        if (drafts.some((entry) => entry.id === item.id)) continue;
        const draft = readDraft(item.draft);
        if (!draft) continue;
        const updatedAt = typeof item.updatedAt === 'string' ? item.updatedAt : now;
        drafts.push({ id: item.id, updatedAt, draft });
      }
      if (typeof raw.open === 'string' && drafts.some((entry) => entry.id === raw.open))
        open = raw.open;
    }
  } catch {
    // An unreadable shelf is treated as empty; the next save replaces it.
  }
  // The earlier single draft joins the shelf and stays open, so nothing a reader had is lost.
  if (single) {
    const id = newId();
    drafts.push({ id, updatedAt: now, draft: single });
    open = id;
  }
  return { drafts: drafts.sort(newest), open };
}

export function serializeShelf(shelf: DraftShelf): string {
  return JSON.stringify({ open: shelf.open, drafts: shelf.drafts });
}

export const canAddDraft = (shelf: DraftShelf) => shelf.drafts.length < MAX_DRAFTS;

export function addDraft(shelf: DraftShelf, draft: Draft, now: string, id: string): DraftShelf {
  if (!canAddDraft(shelf)) return shelf;
  return { drafts: [{ id, updatedAt: now, draft }, ...shelf.drafts], open: id };
}

export function updateDraft(shelf: DraftShelf, id: string, draft: Draft, now: string): DraftShelf {
  const current = shelf.drafts.find((entry) => entry.id === id);
  if (!current || current.draft === draft) return shelf;
  return {
    ...shelf,
    drafts: [{ id, updatedAt: now, draft }, ...shelf.drafts.filter((entry) => entry.id !== id)],
  };
}

export function removeDraft(shelf: DraftShelf, id: string): DraftShelf {
  return {
    drafts: shelf.drafts.filter((entry) => entry.id !== id),
    open: shelf.open === id ? null : shelf.open,
  };
}

// Puts a deleted draft back where its time places it, without opening it.
export function restoreDraft(shelf: DraftShelf, entry: StoredDraft): DraftShelf {
  if (shelf.drafts.some((item) => item.id === entry.id)) return shelf;
  return { ...shelf, drafts: [...shelf.drafts, entry].sort(newest) };
}

export function openDraft(shelf: DraftShelf, id: string | null): DraftShelf {
  return id === null || shelf.drafts.some((entry) => entry.id === id)
    ? { ...shelf, open: id }
    : shelf;
}

// What the list calls a draft: its package name, else its identifier.
export function draftLabel(draft: Draft): string {
  return draft.package.name.trim() || draft.package.package_id.trim() || 'Untitled workflow';
}
