# Button

The pill-shaped action. A view has one main action, for the thing the page is for: the `accent` Button on a sheet, the `ink` Button on `field`. Every other action is `ink`, `light` or `outline`.

**You provide** the label: verb first, sentence case, one to four words ("Get early access", "Approve"). Add a trailing "→" only when the button goes somewhere.

| Class | Use it |
| --- | --- |
| `lp-btn lp-btn--accent` | The one main action on `surface` or `deep`. Never on `field`. |
| `lp-btn lp-btn--ink` | The main action on `field`: the hero and the closing call to action. Any other action on `surface`. The nav button. |
| `lp-btn lp-btn--brand` | The main action on a product screen, where tomato is kept for decisions. |
| `lp-btn lp-btn--light` | A second action on `field`, or any action on `deep`. |
| `lp-btn lp-btn--outline` | A quiet action on `surface`. |
| add `lp-btn--sm` | In a nav bar or inside a product screen. |

- Don't put two `accent` Buttons in one view.
- Don't put an `accent` Button on `field`. Tomato on sage reads at 2.0:1, and for many colour-blind people the button fades into the page.
- In the dark theme `ink` and `light` are both light fills, so pair the `ink` main action on `field` with an `outline` Button.
- Don't use an `accent` Button for a destructive action; errors and deletes use `danger-fill`.
- Don't use an icon without a label.
