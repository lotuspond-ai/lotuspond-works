# Tag

A short status: a dot plus one to three words. The word carries the meaning; the colour only supports it.

**You provide** the word, in sentence case.

| Class | Means |
| --- | --- |
| `lp-tag lp-tag--live` | Available or healthy: "Early access", "Connected". A light tint. |
| `lp-tag lp-tag--soon` | Not here yet: "In development", "Planned". An outline with a hollow dot. |
| `lp-tag lp-tag--wait` | A person has to act: "Waiting for you". A solid `accent` fill. |
| `lp-tag lp-tag--failed` | Something went wrong: "Failed". A solid `danger-fill`. |

- On `deep`, set a `soon` Tag's colour to `on-deep` and its border to `on-deep-muted`.
- Don't use a Tag as a button, and don't put more than one beside a title.
- A `wait` Tag counts as the view's tomato; don't pair it with an `accent` Button, and don't put it on `field`.
