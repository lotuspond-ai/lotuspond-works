# Lotuspond console design system

The console is a working environment for agent evaluation and release decisions. Its four workflows are Releases, Scenarios, Production Evidence, and Governance. Workspace and platform administration are separate supporting surfaces.

## Visual language

The console is a calm daylight workspace: a soft grey frame holds the navigation, an inset white panel holds the work, one green marks action, and semantic colours always travel with a written label. The public pages share the ink, semantic colours and wordmark but read on warm archival paper. Avoid decorative gradients, invented charts, simulated activity, or arbitrary confidence percentages; the public introduction's drawn pond scene is the one illustrated exception. No external font is loaded: the console uses the system sans (`--sans`) throughout, the public pages add the local `--serif`, and identifiers and commands use `--mono`.

Stylesheets live in `web/src/styles/`: `console.css` defines the tokens on `:root` and `.console-window` and holds the console system; `landing.css` holds the public introduction and overrides the surface tokens on `.landing`. `web/src/styles.css` only imports the two.

| Token           | Console                | Public pages           | Purpose                                    |
| --------------- | ---------------------- | ---------------------- | ------------------------------------------ |
| `--canvas`      | `#f5f5f2`              | `#f4f1e9`              | Frame and page background                  |
| `--paper`       | `#ffffff`              | `#fdfcf8`              | Workspace panel, tables, forms, cards      |
| `--sunken`      | `#efefeb`              | `#ece8de`              | Hover fills and inset areas                |
| `--ink`         | `#1b2420`              | `#1f2a24`              | Primary text                               |
| `--muted`       | `#636d67`              | `#5a6a61`              | Secondary text (about 5:1)                 |
| `--line`        | `#e6e5e0`              | `#dcd8cc`              | Structural separation                      |
| `--line-strong` | `#d4d3cc`              | `#c8c3b5`              | Secondary buttons and strong rules         |
| `--field-line`  | `#858c87`              | same                   | Text-field edges (at least 3:1)            |
| `--focus`       | `#3f7d66`              | `#86ad9d` on the scene | Focus ring (at least 3:1 on every surface) |
| `--accent`      | `#204e3e`              | same                   | Primary actions, links, the active icon    |
| `--success`     | `#285e42` on `#e5f0e7` | same                   | Explicit passing results                   |
| `--warning`     | `#7a5516` on `#f7eed9` | same                   | Pending decisions and active work          |
| `--danger`      | `#983d34` on `#f8e6e2` | same                   | Failures, blocked actions and suspension   |
| `--lotus`       | `#c4864a`              | same                   | The wordmark's warm dot                    |
| `--lotus-ink`   | —                      | `#a86b36`              | The warm note as large display type        |

`web/src/lib/contrast.test.ts` reads these tokens from the stylesheets and fails when text drops below 4.5:1, large display type below 3:1, or a text-field edge or the focus ring below 3:1 on a surface it is drawn on.

Spacing follows a 4px scale. Controls are 32–34px tall with 8px corners; cards and tables use 10–12px corners and a one-pixel line with a faint shadow. Console titles are 24px semibold sans, body text 13.5–14px, metadata 12–12.5px. Icons (`components/console/icons.tsx`) sit on a 16-unit grid with a 1.5 stroke and always accompany a written label.

## Console layout

- **Sidebar** (`components/console/Sidebar.tsx`). The wordmark; a workspace switcher (a disclosure listing workspaces with the current one checked, and a link to create or manage workspaces); "Search or jump to" with ⌘K; the navigation from `components/console/navigation.tsx` (Home, then Build, Evaluate, Production, Workspace, plus Platform for operators); a "Finish setting up" progress card until setup is complete; and the account menu (Account security, Your workspaces, What's new, Sign out). The active item is a filled white row with a green icon. Below 860px the sidebar becomes a top bar whose Menu reveals everything.
- **Topbar.** A breadcrumb of workspace and page; on project-scoped pages the page slot becomes the project filter. "What's new" sits on the right above 860px; below it, it stays in the account menu and the palette.
- **Home** (`pages/Home.tsx`) is the default page. It greets the reader, shows the six-step setup checklist from `lib/setup.ts` (projects, scenarios, runners, policies, runs, releases), each step complete once its list returns a record and unknown when the reader cannot read it, and offers recent activity from the audit log and the release gate command. The checklist can be hidden per workspace in browser storage.
- **Command palette** (`components/console/CommandPalette.tsx`, ranking in `lib/commands.ts`). A modal combobox: arrow keys move, Enter runs, Escape closes from anywhere in it, Tab cannot leave it, the result count is announced as you type, and focus returns to where it was. Results rank prefix matches first, then word starts, then substrings, in groups named for their section. Create actions navigate to the page with its form open.
- **Page heading.** The navigation section as a quiet eyebrow, a 24px title, a description under 68ch, and the primary action ("Add scenario", "Create release").
- **Tables.** Readable column names (`lib/format.ts`), the first column emphasised, identifiers in mono, relative times with the exact time in the title, quiet row actions. A scrolling table is a positioning context so hidden labels cannot widen the page.
- **States.** Skeleton rows while loading; an empty list shows its record type's ink glyph, a title and its create action; a filtered-empty list offers to clear the filter; failures are danger panels with retry; `.panel` is a neutral card and `.notice` a real notice.
- **Record details** open in a drawer that takes focus, traps Tab, closes on Escape or the backdrop, stops the page scrolling behind it, and returns focus to the control that opened it (Open, Evaluate, Cancel, Approve, Reject or Revoke), or to the page when an action removed that control. An action refreshes its list in place, so the row and its buttons stay put.
- **Decisions.** `DecisionSummary` shows the verdict heading, a `VerdictStamp`, the recorded reasons and the four metrics; `RecordDetails` and the public preview both render it.
- **Studio** (`pages/Studio.tsx`, `components/studio/`, `lib/studio.ts`, `styles/studio.css`). A workflow-package v1 composed on the field record's graph paper (`--grid`, `--grid-major`). The canvas card has a palette row (Start, Agent, Connector, Human review, End), the surface, and a footer with the status line and zoom (−, percentage, +, Fit). Nodes are 176×60 index cards with the kind's ink glyph, a mono identifier and a quiet detail (the model or connector · operation); start and end are round stamps and a human review has a dashed edge. Edges are ink traces with a chevron arrowhead, drawn from the outer edge of the source port to the outer edge of the target port; the selected edge turns accent, and an edge whose two nodes the admission check both marked is a dashed danger trace. A pointer press on a card selects it and a press that travels 4px drags it (pointer capture keeps the gesture; positions snap to 8px); dragging the card's out-port draws a dashed accent wire that lands on another card; a plain press on the port enters connect mode, in which candidate cards tint accent-soft and the status line says what to do; a palette item dragged onto the surface lands where it is dropped with a ghost label following the pointer, and pressed adds at a free spot. The keyboard does everything the pointer does: cards are `role="button"` with arrow keys moving by 8px (40px with Shift), Enter or Space selecting or completing a connection, Delete removing, Escape leaving connect mode; the port is a sibling button, never nested inside the card's role. Where the canvas continues past the view, a shade fades its edge; the selected card is scrolled into view, so "Select read" from the admission check always shows the card. Marked cards take a danger border and dot and describe their problems to assistive technology. The inspector edits the selected node, edge or the package, then the declarations in disclosures with counts; each field's hint and problem are outside its label and describe the control. The admission check below is a card with the verdict stamp ("Admissible" or "Not yet"), the digest and canonical size, or the problems with their location and a button to select the node concerned, followed by the registry's published versions. Drafts live in browser storage per workspace and project; nothing on the page runs a workflow.

### Visual QA without an account

`npm run dev:web` also serves `web/dev/console.html`, a development-only harness that renders the real console with fixture responses in place of the API. Append `#releases` or any page, `?empty` for empty states or `?failing` for failures. Evaluating a fixture release returns the preview's sample decision so the decision layout can be reviewed. `#studio` answers the registry's requests from the page: one published example version, publication judged by the same local validator (so the 422 shape and a 409 for a changed version match the registry), and stored canonical content, so the Studio's publish, conflict and reopen states can be reviewed. `vite build` builds only `web/index.html`, so the harness and its fictional fixtures never ship.

## Components and behavior

- `SectionHeader` keeps the page title, explanation, and primary action together.
- `StatusBadge` renders a written status and semantic tone. An unknown value remains a neutral label; it never becomes a passing status. The `approval_required` release verdict uses the warning tone.
- `EmptyState` provides a concrete next action where one exists. Loading, empty, and failed fetches are separate states.
- `Alert` announces failures and exposes retry when retry can help.
- `ObjectView` presents structured records as labeled facts. Technical JSON is available only in a collapsed `RawRecord` disclosure.
- `RecordDetails` gives release decisions and evaluation runs dedicated layouts. Runs show expected scenarios, received results, critical failures, latency, and cost; missing results remain “Awaiting evidence.”
- `ScenarioForm` starts with task and required-response authoring. Structured input remains available for custom adapters. A voice modality label does not prove audio execution.

## Public introduction

`web/src/pages/Landing.tsx` renders the signed-out introduction; `Welcome` in `App.tsx` switches to the account form without changing the URL hash. The page opens on a scene and then reads as a document:

1. Pond hero (`web/src/components/Pond.tsx`): a canvas draws a dusk sky, water strokes in perspective, lily pads, and a column of warm glints. The headline is real text; its baseline is measured from layout offsets and becomes the horizon, and the wordmark's warm dot is the sun at the end of the last word. An `aria-hidden` copy of the headline, mirrored about the baseline and displaced by an SVG turbulence filter, is the reflection. The water calms as the reader scrolls past.
2. Surfacing: the release policy record rises out of the water beside the reading copy and a contents list with dot leaders. Each reason is quoted from `backend/lotuspond/policy.py` and shown with the verdict it produces.
3. Platform (section 1): `ConsolePreview` in `web/src/components/platform/` shows the console in a window with the console's own tokens and navigation, labelled "Sample workspace · illustrative". Four ARIA tabs (arrow keys, Home and End) switch between an interactive release decision (click an outcome to cycle passed, failed, unknown and missing; adjust the pass-rate floor, regression, P95 limit and approval; approve as a different administrator), an evaluation run filling in from a runner, production traces opening an incident when an assessment is unavailable, and a delivery pipeline stopped by the release gate. Every view shares one sample (`sample.ts`) whose decision is computed by `lib/policyPreview.ts`, so the run, the verdict and the gate output agree. The tour advances every nine seconds only while at least 35% of it is in view, pauses on hover or focus, stops on any interaction, and never advances under reduced motion.
4. Sections (2) to (7), each traceable to `docs/CAPABILITIES.md` and drawn in the field-record vocabulary described in `DESIGN.md`: Outcomes as four traces on graph paper (a line that completes, one that breaks into a fault, one that dissolves into static, and a dashed ghost with an empty end) above their written effect on a release; Workflow as `ThreadedSteps`, where one ink thread follows the reader's scroll through the four steps and inks each step's glyph (snapshot, trace, seal, pulse) as it arrives, followed by the release gate command and its output shape from `scripts/cli.mjs`; Agents with small ink SVG signals; Guarantees as a charter of six numbered articles; Deployment as three ink glyphs on graph paper (network, enclosure, sealed dial); Accounts as pond plates of one pad or a colony. The policy record carries an "Immutable" stamp that lands when it is first read.
5. Recently shipped: the three latest entries of the real `/api/v1/changelog`, omitted if it cannot be read.
6. A closing pond band with the primary action, followed by a dark footer.

Scene tokens are scoped to `.landing`: `--pond-deep` (`#09110e`) for the water and footer, `--pond-paper` (`#f1ece1`) for text on the scene, and `--lotus` (`#c4864a`), the wordmark's warm dot. The header is transparent over the scene, gains a dark backing once scrolled, and switches to the canvas tone past it. The grain texture is an inline `data:` SVG and scene values are set through the CSSOM, both allowed by the console's content security policy.

The scene uses a fixed seed, redraws at about 30 frames per second only while visible, and pauses in hidden tabs. Account plates share the scene's drawing hand through `web/src/lib/pondArt.ts`; `PondPlate` starts a plate's 2.8-second entrance once 55% of it is visible (or at mount if it already is), replays it on hover, allocates a reflection buffer only for scenes that draw one, and renders the final frame once under reduced motion. A scene already on screen starts its entrance at mount; one below the fold starts when it is scrolled into view. With reduced motion, entrances are skipped and the scene is drawn once. Changes to policy reasons or CLI output must be mirrored on this page. See `DESIGN.md` for the rules that keep public pages honest and usable.

Forms use explicit labels, native inputs, visible focus, and disabled pending actions. A hint or rule that governs a field (a password length, a budget, a sample rate) is its `aria-describedby` description; a hint written inside a label is excluded from the field's name with `aria-labelledby`, so it is read once. Radio groups use `fieldset` and `legend`. Tables have a visually hidden caption, column headers, and scroll horizontally on narrow displays; every row action names its record ("Open Refunds r42"), and a list's record count is a status that is announced when a filter changes it. Navigation has `aria-current`, and each titled navigation section is a named group; record inspection moves focus to its details. A skip link bypasses the sidebar. Reduced-motion preferences disable transitions. Mobile account setup hides the marketing introduction so the form remains immediately usable.

### Accessibility

The console and public pages are reviewed against WCAG 2.2 AA:

- **Titles and announcements.** Every view sets the browser title, most specific first ("Releases · Example organization · Lotuspond"; `lib/title.ts`), and a polite status announces the page when the reader moves to it, since focus stays on the link that was followed. An unknown route renders "Page not found" with an `h1`.
- **Focus.** The ring is 2px of `--focus`, offset 2px; rows inside scrolling lists (navigation, switcher options) draw it inset so the list cannot clip it, and the scene overrides the token with a light green. Disclosures (workspace switcher, account menu) close on Escape with focus back on their button, on an outside click, and when focus moves elsewhere, so an open panel never covers the next element Tab reaches. On narrow screens Escape closes the menu and returns to Menu; choosing a page from the menu continues reading at the new page.
- **Loading and status.** A loading list announces "Loading releases…" rather than an unlabelled skeleton; counts, saved states and route changes are `role="status"`; failures are `role="alert"`.
- **Reflow and targets.** Nothing scrolls the page sideways at 320px (single-column grids use `minmax(0, 1fr)`; wide commands and tables scroll inside their own focusable region). Standalone links keep a 24px target clear of neighbouring controls. On narrow screens the icon-only "What's new" link is removed rather than left unnamed; it stays in the account menu and the palette.
- **Forced colours.** The current page, the selected command and the checked workspace keep a system-colour outline when backgrounds and shadows are replaced.

`web/dev/console.html` is also the harness for accessibility review: every page, the open palette, drawer and menus, and the `?empty` and `?failing` states can be scanned without an account.

## Product flows

### Account to project

Individual registration creates a personal workspace. Organization registration asks for the organization name. An existing individual may create an organization while retaining the personal workspace. The active workspace changes request authorization context; the active project filters project-scoped records. A project selector is not an authorization mechanism: the server must scope every query.

### Evaluation to release

Create a project, define scenarios, create an evaluation run, connect an execution worker, and record measured results. Create a release with its candidate run, optional baseline, and immutable policy. Evaluate the release, inspect decision reasons and evidence, then handle independent approval if required. Results cannot be fabricated through the browser. A policy pass is a recorded decision, not a claim of general production readiness.

### Production evidence and voice

Inspect ingested traces and voice sessions. Trace details show measured latency, cost, retained attributes, and recorded model assessments. The same assessment component serves traces and voice transcripts, with explicit criteria, verdict, reason, model, timestamp, and provenance. Voice sessions show the retained transcript in sequence and display supplied metrics separately. Five-second refresh is labeled as fetching records; it does not imply a connected microphone or verified live transcription. Model assessments require explicit criteria and a configured backend model. Assessment results are advisory evidence and cannot authorize tool execution. Missing audio, timing, interruption, guardrail, or provenance evidence remains unknown.

### Administration

Platform administration is visible only when `/me` reports `is_platform_admin`. It shows actual API counts, users, and workspaces. Suspending or restoring workspace access requires a reason. All administrative authorization and audit guarantees belong on the server. Hiding navigation is not an access boundary.

## Recovery and validation boundaries

The voice evidence normalization module was recovered from the separately archived console, then adapted to the Lotuspond schema identifier and current API shape. Its provenance, metric coverage, privacy, timing, and fail-closed gate semantics are retained. The current shell, workspace flow, administration, scenario editor, and voice session page are newly composed for the single-product contracts. This is a focused recovery of useful behavior, not a claim that every archived feature has been restored.

Local checks: `npm run lint`, `npm run build`, and `node --import tsx --test web/src/lib/voiceEvidence.test.ts`. The voice tests cover missing coverage, provenance, opaque provider references, metric units, invalid timing, and absent privacy metadata. Browser inspection covers the public introduction and individual/organization account choices at desktop and mobile widths. Authenticated end-to-end validation requires the Python API and isolated test database. Hosted deployment, production traffic, model-provider quality, and voice-provider streaming require separate operational evidence.
