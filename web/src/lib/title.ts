import { useEffect } from 'react';

// Each view names itself in the browser tab, history and window list (WCAG 2.4.2), most
// specific part first, so several open tabs stay distinguishable.
export function documentTitle(...parts: (string | undefined)[]): string {
  return [...parts.filter((part): part is string => !!part?.trim()), 'LotusPond Works'].join(' · ');
}

// `null` leaves the title to whichever view is rendered inside this one.
export function useDocumentTitle(title: string | null) {
  useEffect(() => {
    if (title !== null) document.title = title;
  }, [title]);
}
