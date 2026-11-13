# Sheet

The large rounded panel that holds one idea. A page is a `field` with Sheets on it.

**You provide** the content: one headline, its supporting text and at most one visual.

| Class | Use it |
| --- | --- |
| `lp-sheet` | Reading, product screens and forms, on `surface`. |
| `lp-sheet lp-sheet--deep` | Developer content and code, on `deep`. One per page. |
| `lp-panel` | A `surface-tint` area inside a sheet that groups cards. |
| `lp-card` | A raised card on a panel, with `shadow-card`. |

- Inset a Sheet `space-4` from the page edge (`space-2` on a phone) and leave `space-4` between Sheets.
- On a phone, step the corners down to `radius-lg`.
- Don't nest a Sheet in a Sheet, and don't put a card straight on `surface`: it needs a panel under it.
