# ADR-0016: Adopt the LotusPond design system

Date: 2026-11-13. Status: **accepted. On 2026-11-13 the owner supplied three brand colours, asked for a unique, uncluttered design and a design system kept in Claude Design, accepted the resulting company sample page, asked for Works and Health pages in the same design, and asked for the design to be brought into this repository.** It changes how the product looks. It changes no scope, behaviour, API or stored data.

## Context

The console and the signed-out introduction used a theme called the field record: warm archival paper, a reading serif, a dusk pond scene, ink glyphs, stamps and one warm orange note ([DESIGN.md](../../DESIGN.md) before this change).

On 2026-11-13 the owner reviewed a company page drawn in that vocabulary and found it cluttered and generic. Warm cream paper with a serif headline and a terracotta accent is also one of the most common looks for AI products today, so it did not set LotusPond apart.

The owner supplied a palette (sage `#9CAF88`, off-white `#F7F5EF`, tomato red `#E8412C`) and delegated how to use it. LotusPond now has two products, LotusPond Works and LotusPond Health ([ADR-0015](0015-name-the-product-lotuspond-works.md)), so the design has to belong to the company, not to one application.

## Decision

LotusPond has one design system, and this product is drawn with it.

- **Idea.** Below the surface, above the surface, shown as one thread: a tangle where the systems are, one clean line after it, a bloom at the end. Agents do the tangled work in the systems a business runs on, and people get one clean interface. Pictures draw an agent's work as that one line through real work objects, ending in the lotus bloom, with people shown as hands.
- **Colour.** Sage leads and is the ground of marketing pages. Off-white is the reading surface. Pine (`#18211B`) is the ink. Tomato is spent once per view and never sits on sage, where it reads at 1.7:1; on the sage field the main action is pine.
- **Tomato in the console** marks only the place where a person has to decide. The console's action colour is a sage green (`#55694A`), so the product keeps one calm green for action, as before.
- **Type.** Platypi for display, Atkinson Hyperlegible Next for text, Atkinson Hyperlegible Mono for code only. Later on 2026-11-13 the owner chose these in place of a first set (Bricolage Grotesque, Instrument Sans, JetBrains Mono), so the faces are not the ones every AI product uses, and approved the "one thread" illustration set that replaced the pond drawing.
- **Source.** `design-system/project/` holds the brand book, `tokens.json` and the component guidelines. It is published to Claude Design as the "LotusPond" design system. `web/src/lib/designTokens.test.ts` fails when the console's tokens drift from `tokens.json`.

## Options considered

| Option                                | For                                                                                | Against                                                                                         |
| ------------------------------------- | ---------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------- |
| Adopt the sage system                 | The owner's palette; distinct from other AI products; one system for both products | The signed-out introduction has to be redrawn                                                   |
| Keep the field record, recolour it    | Least work                                                                         | The owner rejected the look, not only the colours                                               |
| Tomato as the console's action colour | Matches the marketing pages                                                        | Sits beside destructive red, appears on every page, and loses its meaning as "a person decides" |
| Pine as the console's action colour   | Highest contrast                                                                   | Links would look like body text; the console loses its one green                                |

## What changes and what does not

Changed now:

- `DESIGN.md`, `docs/design-system.md` and the design paragraph of `CLAUDE.md`.
- The console's tokens in `web/src/styles/console.css`: a light sage frame, a white panel, sage green actions, pill buttons, pine ink and focus ring, and the tomato wordmark dot. Every console page and the account screens take the new look through those tokens.
- `web/src/lib/contrast.test.ts` holds the new tokens to WCAG 2.2 AA.
- `design-system/project/` and its token test are added.
- The brand typefaces are bundled: three open-licensed (OFL-1.1) Fontsource packages are dependencies, and `web/src/styles.css` imports them, so the build serves the font files from the application's own origin as the API's content security policy requires. The owner approved adding them on 2026-11-13.
- A changelog entry, `sage-design-system`.

Unchanged, on purpose:

- Layout, navigation, components, keyboard behaviour and copy.
- The signed-out introduction (`web/src/pages/Landing.tsx`) keeps the field record for now. `landing.css` pins its action green, its 8px buttons and its earlier system typefaces so the page does not shift, and the console preview inside it shows the new console.
- The early-access page in `deploy/interest/`.

Not done yet, in this order:

1. Redraw the signed-out introduction in the system. Its claims must still trace to `docs/CAPABILITIES.md`. Done on 2026-11-14: `Landing.tsx` is drawn from `home.css`, and the field-record pond, plates and their styles were removed.
2. Replace the early-access page with the company site. That is a hosted change with its own scope.

## Consequences

New interface work uses the system's tokens and rules. The field record is not extended. A change to a colour or a scale is made in `design-system/project/tokens.json` first, then in `console.css`, and republished to Claude Design.

The sample pages the owner approved describe LotusPond Works as automation for supply-chain operations. That positioning is not in `docs/GOAL.md` or `docs/CAPABILITIES.md`, and this record does not adopt it. Public copy in this repository keeps to implemented behaviour until the owner decides the positioning in its own record.
