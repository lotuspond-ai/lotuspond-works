# Lotuspond design

One product identity, one application. Public copy describes what the user can do and what evidence supports a decision. The application has four workflow groups and a small workspace utility area.

The console uses a calm daylight palette (a soft grey frame around a white workspace) with a muted green for action; the public pages read on warm archival paper. Both share the same ink, semantic colours and wordmark. A small warm detail accompanies the wordmark. Public headlines may use a reading serif; application records use system sans. Prefer clear rows and meaningful sections to decorative metric cards.

Every screen must handle loading, no records, filtered-empty results, unavailable data, and authorization errors honestly. Never present simulated data as customer data, a measured result, or a benchmark. The only simulated records allowed anywhere are the public platform preview's labelled sample workspace, described below. Maintain visible keyboard focus, explicit input labels, responsive navigation, and reduced-motion compatibility.

WCAG 2.2 AA is the floor for both surfaces: text at 4.5:1 (3:1 for large display type), text-field edges and the focus ring at 3:1 against every surface they are drawn on, including the pond scene, a descriptive browser title for every view, no sideways page scroll at 320px, and 24px targets for standalone controls. `web/src/lib/contrast.test.ts` holds the tokens to these ratios; `docs/design-system.md` lists the keyboard and announcement behaviour each component owes.

## Console

The console is a working tool first: modern, calm, and easy for a newcomer to start in. It follows published navigation and onboarding research (NN/g on vertical navigation, GitLab Pajamas on sidebars, onboarding-checklist and command-palette practice):

- **Frame.** A soft grey sidebar and an inset white panel for the work. Page titles are sans, 24px semibold; there is no serif or uppercase mono inside the console.
- **Navigation.** Always visible on desktop, left-aligned, every item an icon plus a one- or two-word label with the keyword first, grouped in the order work happens: Home, Build, Evaluate, Production, then Workspace utilities at the bottom. The active item is a filled white row with a green icon; no coloured side bars. The workspace switcher sits at the top, the account menu at the foot.
- **Search.** "Search or jump to" and ⌘K open a command palette for every page and create action. It is a shortcut to the visible navigation, never a replacement for it.
- **First run.** Home shows a six-step setup checklist computed from the workspace's own records, ordered by value, with the next step highlighted and a button that opens the right create form. It can be hidden, and a small progress card stays in the sidebar until setup is complete. Every empty list names its single next action.
- **Records.** Tables with readable column names and relative times, details in a drawer beside the list, and release decisions marked with a verdict stamp.
- Do not add dashboard tiles without data, charts without data, icon-only controls, gradients, glass or glowing accents. Every screen keeps loading, empty, filtered-empty, unavailable and authorization states distinct.

`docs/design-system.md` describes the components, and `web/dev/console.html` renders the console with fixtures for review without an account.

The public platform preview must be built from the console's own classes and components (`NavList`, `.page-heading`, `.table-wrap`, `StatusBadge`, `DecisionSummary`), so a console change reaches the landing page without a second implementation.

## Brand theme: the field record

Lotuspond keeps a scientific record of something alive. A pond is observed, its specimens are labelled, its traces are measured, and what is written down cannot be changed. That is the product's promise — evidence with provenance, decisions on the record — and it is the theme every public surface draws from. The pond is the signature scene, not the whole brand; the field record is the vocabulary around it.

| Layer           | Where                                               | What it looks like                                                                                                                                                                                                                                                  |
| --------------- | --------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Signature scene | Hero, closing band, account choices                 | The dusk pond drawn in code (`components/Pond.tsx`, `components/PondPlate.tsx`): headline on the waterline, the wordmark's warm dot as the sun, reflections, lily pads                                                                                              |
| Product         | Platform section                                    | The console itself (`components/platform/`), rebuilt with the console's own tokens inside a window and holding one sample workspace: an interactive release decision, a run filling in, production evidence opening an incident, and a pipeline stopped by the gate |
| Ink glyphs      | Workflow, deployment, future feature marks          | Broad-nib pictograms on a 64-unit square (`components/InkGlyph.tsx`): strokes and set-down nodes, near-black ink, one warm node, an occasional sage field or accent-green mark. They draw themselves in stroke order                                                |
| Traces          | Outcomes and anything that shows evidence over time | Instrument lines on graph paper (`components/TraceSpecimen.tsx`): no axes values or data, only the shape of an outcome — completing, breaking, dissolving, or absent                                                                                                |
| Marks           | Decision records                                    | A rubber stamp (`.record-stamp`) that states a real property, such as "Immutable"                                                                                                                                                                                   |
| Threads         | Sequences                                           | One continuous ink line through a sequence, drawn with the reader's scroll (`components/ThreadedSteps.tsx`)                                                                                                                                                         |

Palette: warm archival paper below the scene (`--canvas` #f4f1e9, `--paper` #fdfcf8, `--line` #dcd8cc), near-black ink (`--ink-mark` #1c2520), the console's accent green and semantic colours, and `--lotus` (#c4864a) used sparingly as the one warm note, darkened to `--lotus-ink` (#a86b36) when it is set as display type. Type: `--serif` for display, system sans for reading, `--mono` for labels, commands and field names.

Extend the vocabulary before inventing a new motif. A new glyph uses the same nib, grid and stroke order; a new trace stays free of numbers; a new mark states only what is true.

## Public pages

The signed-out introduction (`web/src/pages/Landing.tsx`) is the reference for any public surface: one memorable scene, then a well-edited technical document.

- The signature scene is the pond: a dusk horizon drawn along the headline's baseline, the wordmark's warm dot resting on it as the sun, and the headline reflected in the water. No stock photography, clip art, or generic gradient backgrounds; gradients live only inside the scene, never on interface surfaces.
- Sections animate in the field-record vocabulary above. Glyphs and traces draw once when most of them are in view, the workflow thread follows scroll and is reversible, and the stamp lands once. Small inline SVG signals beside the agent kinds loop quietly. Every animation is decorative; the adjacent text carries the full meaning, and reduced motion shows the finished state.
- Show the platform the way it works, as the best developer-platform sites do, but never by inventing results. The preview uses the console's layout, tokens and `StatusBadge`; one sample workspace that is always labelled "Sample workspace · illustrative"; and decisions computed by `web/src/lib/policyPreview.ts`, whose reason strings and rules are tested against `backend/lotuspond/policy.py`. Keep customer names, logos, usage figures and performance claims out of it. It tours itself only while watched, stops for good on interaction, and does not advance under reduced motion.
- "Recently shipped" reads the real `/api/v1/changelog` and is left out entirely when it cannot be read; never substitute placeholder news.
- Show the product through its real rules. Quote product strings exactly, such as the reasons in `backend/lotuspond/policy.py` or the output shape of `scripts/cli.mjs`, and name the source in a code comment. Elide a value (a digest, an identifier) rather than invent one.
- Every claim must trace to implemented behavior in `docs/CAPABILITIES.md`. Do not add customer logos, testimonials, usage counts, benchmark numbers, percentages, uptime figures, or "trusted by" rows until real, attributable evidence exists.
- Type roles: `--serif` for display and section titles, system sans for reading text, `--mono` for commands, identifiers, and technical tags. Display type is 44–92px, section titles 32–50px, reading text 16–17px with lines under about 36em.
- Layout after the scene: one 1280px container, numbered section rails, a contents list with dot leaders, thin rules between rows, and paper panels only for distinct objects such as a record or a choice. One primary action per band; Sign in stays secondary.
- Motion must never cost usability. Calls to action are readable within a second, scrolling is never pinned or hijacked, animation pauses off-screen and in hidden tabs, content already on screen never waits for an observer, and reduced motion shows a still scene. Scroll-linked effects (the water calming, the policy record surfacing) must leave every element readable at every scroll position.
- Do not use glass effects, glowing interface accents, emoji, icon-grid feature cards, animated counters, or marquees.
- Keep individual and organization choices distinct. A choice made on the public page must carry into registration.
- The console routes on `location.hash`. In-page links must scroll without changing the hash and move focus to the target heading.
- Before reporting a public page done, check it in a browser at 1440px, 768px, and 375px widths and at a mid-scroll position: no horizontal page scroll, headlines that break where intended, the horizon on the headline baseline, text contrast of at least 4.5:1 over the scene, and visible keyboard focus.
