# LotusPond design system

The source of the LotusPond design system, shared by LotusPond Works and LotusPond Health ([ADR-0016](../docs/architecture/0016-adopt-the-lotuspond-design-system.md)).

- `project/README.md` is the brand book: the idea, colour, type, shape, product screens, illustration, motion and voice.
- `project/tokens.json` holds every colour in light and dark, the type scale, spacing, radii and shadows.
- `project/components/` holds one folder per component with its guidelines and a live preview, and `bundle.css` with the `lp-` classes.
- `project/assets/Illustrations/` holds the approved "one thread" pictures as SVG files, with a README on how they are drawn.
- `project/design-system.json` is the index Claude Design reads.

## How it reaches the product

`web/src/styles/console.css` carries the console's tokens under the console's own names. `web/src/lib/designTokens.test.ts` maps each one to its token here and fails when a value drifts. Change a colour here first, then in `console.css`.

## Publishing to Claude Design

The same files are published as the "LotusPond" design system artifact in Claude Design, so work done outside this repository follows the same rules. After changing a file here, ask Claude Code to publish the changed files to that artifact with this folder as the root. `tokens.css`, `manifest.json` and the `api/` cards are generated there and are not kept here.
