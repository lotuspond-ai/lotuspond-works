# Underline

A hand-drawn stroke under one word of a headline. It is the only form of emphasis in display type.

**You provide** one word (two at most) inside `lp-underline`, followed by the stroke's inline SVG: `viewBox="0 0 200 20"`, `preserveAspectRatio="none"`, and the path `M3 12C38 4 86 17 128 9s52-3 69 2`.

- Use it once per view, on the word that carries the point: "operations", "record".
- On `surface` and `deep` it is tomato (`accent-mark`). On `field`, add `lp-underline--light` and it is `off-white`, because tomato never sits on sage.
- Use it only on `display-xl` and `display-lg`.
- It may draw itself once on load, in under a second. It never loops.
- Don't give it any other colour, and don't use it under links or body text.
