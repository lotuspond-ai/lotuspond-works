# LotusPond Works design

One product identity, one application. Public copy describes what the user can do and what evidence supports a decision. The application has four workflow groups and a small workspace utility area.

LotusPond Works is drawn with the LotusPond design system, adopted on 2026-11-13 ([ADR-0016](docs/architecture/0016-adopt-the-lotuspond-design-system.md)). The system belongs to the company and is shared with LotusPond Health. Its source is `design-system/project/` in this repository: the brand book (`README.md`), the tokens (`tokens.json`) and the component guidelines. The same files are published as the "LotusPond" design system in Claude Design, so people and agents working outside this repository read the same rules. This document says how the system applies to this product.

Every screen must handle loading, no records, filtered-empty results, unavailable data, and authorization errors honestly. Never present simulated data as customer data, a measured result, or a benchmark. The only simulated records allowed anywhere are the public platform preview's labelled sample workspace, described below. Maintain visible keyboard focus, explicit input labels, responsive navigation, and reduced-motion compatibility.

WCAG 2.2 AA is the floor for both surfaces: text at 4.5:1 (3:1 for large display type), text-field edges and the focus ring at 3:1 against every surface they are drawn on, a descriptive browser title for every view, no sideways page scroll at 320px, and 24px targets for standalone controls. `web/src/lib/contrast.test.ts` holds the tokens to these ratios, `web/src/lib/designTokens.test.ts` holds the console's tokens to `design-system/project/tokens.json`, and `docs/design-system.md` lists the keyboard and announcement behaviour each component owes.

## The system in one page

- **The idea: below the surface, above the surface.** A lotus roots in mud and blooms above the water. Agents do the tangled work below the surface, in the systems a business runs on. People get one clean interface above it. Show it with the thread: a tangle where the systems are, one clean line after it, and a bloom at the end.
- **Three colours and one ink.** Sage (`#9CAF88`) is the brand and the ground of marketing pages. Off-white (`#F7F5EF`) is for reading. Tomato (`#E8412C`) is spent once per view. Pine (`#18211B`) is the ink and the one dark chapter. White or off-white text never goes on sage (2.2:1); text on sage is pine.
- **Tomato has one job per surface, and never sits on sage.** On a marketing page it is the single main action on an off-white or pine sheet, the bloom, the underline on a sheet and the wordmark dot. On the sage field the main action is a pine button and the underline is off-white, because tomato on sage reads at 1.7:1 and fades for colour-blind readers. In the console it marks only the place where a person has to decide. It never means an error; that is `--danger`.
- **Type.** Platypi, a serif, for headlines and the wordmark; Atkinson Hyperlegible Next for everything read or pressed; Atkinson Hyperlegible Mono for code only. They were chosen because they are not the faces every AI product uses. No all-caps or letter-spaced labels, no mono labels, no italics for emphasis. The three faces ship with the application as open-licensed font packages, because the API's content security policy allows fonts only from its own origin; each stack falls back to a system face of the same kind.
- **Illustration: one thread.** An agent's work is one continuous pine line through the real things a business handles: parcels, trucks, shelves and documents for Works. It may tangle where the systems are and runs clean after that, ending in the tomato bloom, which always sits on an off-white disc or on paper. People appear only as hands that give, receive or stamp; the bloom is made or held by a hand. The approved pictures are in `design-system/project/assets/Illustrations/`.
- **Shape.** Large radii that step down with nesting, pills for buttons and tags, flat colour, and shadow only on a raised card. No gradients, glass or glows.
- **Voice.** A headline says plainly what the product is and who it is for; a line that could sit on any company's site is rewritten. Plain words, verb first, written to "you". A term is explained where it first appears. Systems are named by kind, not vendor, until an integration is live. Each claim says where the product stands.

## Console

The console is a working tool first: modern, calm, and easy for a newcomer to start in. It follows published navigation and onboarding research (NN/g on vertical navigation, GitLab Pajamas on sidebars, onboarding-checklist and command-palette practice):

- **Frame.** A light sage frame (`--canvas`, the system's `surface-tint`) holds the navigation, and an inset white panel (`--paper`, `surface-raised`) holds the work. Off-white (`--sunken`, `surface`) is for wells and hover fills. Page titles are sans, 24px semibold; display type is kept for the wordmark and the account screens, and there is no uppercase mono inside the console.
- **Action.** One sage green (`--accent`, the system's `sage-700`) marks the main action, links and the active navigation icon, with white text on its buttons. Buttons are pills. Secondary buttons are white with a line.
- **Decisions.** Tomato (`--decide` on `--decide-soft`) is reserved for the control and the row where a person has to decide, such as an approval or a review. Use it at most once per view, and never beside a destructive action.
- **Status.** Success, warning and danger each travel with a written label. A healthy state is a light tint; a waiting or failed state is a stronger fill, so the two never rely on hue alone.
- **Navigation.** Always visible on desktop, left-aligned, every item an icon plus a one- or two-word label with the keyword first, grouped as the platform plan organizes work: Home, then Build, Assure, Operate and Administer. An area that is not built yet carries a quiet Planned tag and opens a page that says what it will do, what it depends on and what to use meanwhile; it never shows sample records. The active item is a filled white row with a green icon; no coloured side bars. The workspace switcher sits at the top, the account menu at the foot.
- **Search.** "Search or jump to" and ⌘K open a command palette for every page and create action. It is a shortcut to the visible navigation, never a replacement for it.
- **First run.** Home shows a six-step setup checklist computed from the workspace's own records, ordered by value, with the next step highlighted and a button that opens the right create form. It can be hidden, and a small progress card stays in the sidebar until setup is complete. Every empty list names its single next action.
- **Records.** Tables with readable column names and relative times, details in a drawer beside the list, and release decisions marked with a verdict stamp.
- Do not add dashboard tiles without data, charts without data, icon-only controls, gradients, glass or glowing accents. Every screen keeps loading, empty, filtered-empty, unavailable and authorization states distinct.

`docs/design-system.md` describes the components, and `web/dev/console.html` renders the console with fixtures for review without an account.

The public platform preview must be built from the console's own classes and components (`NavList`, `.page-heading`, `.table-wrap`, `StatusBadge`, `DecisionSummary`), so a console change reaches the signed-out page without a second implementation.

Wordmark: the lockup **LotusPond Works** (`components/Wordmark.tsx`), the company name in the heavier weight, the product name lighter, then the tomato dot. Use the component rather than retyping the lockup. Company-level pages that name no product keep the company wordmark alone.

## Public pages

These rules apply to every public surface, in either theme:

- Every claim must trace to implemented behavior in `docs/CAPABILITIES.md`. Unfinished platform parts (Control Room, execution, connections, business operations, licensing, and Studio beyond v1 packages) appear only marked In progress or Planned, or in sentences that say they are being built. Do not add customer logos, testimonials, usage counts, benchmark numbers, percentages, uptime figures, or "trusted by" rows until real, attributable evidence exists.
- Show the platform the way it works, never by inventing results. The preview uses the console's layout, tokens and `StatusBadge`; one sample workspace that is always labelled "Sample workspace · illustrative"; and decisions computed by `web/src/lib/policyPreview.ts`, whose reason strings and rules are tested against `backend/lotuspond/policy.py`. It tours itself only while watched, stops for good on interaction, and does not advance under reduced motion.
- Show the product through its real rules. Quote product strings exactly, such as the reasons in `backend/lotuspond/policy.py` or the output shape of `scripts/cli.mjs`, and name the source in a code comment. Elide a value (a digest, an identifier) rather than invent one.
- "Recently shipped" reads the real `/api/v1/changelog` and is left out entirely when it cannot be read; never substitute placeholder news.
- Motion must never cost usability. Calls to action are readable within a second, scrolling is never pinned or hijacked, animation pauses off-screen and in hidden tabs, content already on screen never waits for an observer, and reduced motion shows a still frame. Anything that loops for more than five seconds has a visible Pause control (WCAG 2.2.2).
- Do not use glass effects, glowing interface accents, emoji, icon-grid feature cards, animated counters, or marquees.
- Keep individual and organization choices distinct. A choice made on the public page must carry into registration.
- The console routes on `location.hash`. In-page links must scroll without changing the hash and move focus to the target heading.
- The contact address is hello@lotuspond.ai.
- Before reporting a public page done, check it in a browser at 1440px, 768px, and 375px widths and at a mid-scroll position: no horizontal page scroll, headlines that break where intended, text contrast of at least 4.5:1, and visible keyboard focus.

A new public page follows the system's layout: a sage field with off-white sheets, one idea and one visual per sheet, the headline on the left seven columns and its supporting text on the right five, one main action per view (pine on the sage field, tomato on a sheet), and one "one thread" illustration (the work as one line from a tangle of systems to a bloom held by a hand) on a company page or one raised product card on a product page.

### The signed-out introduction

`web/src/pages/Landing.tsx` is drawn in the system (`web/src/styles/home.css`, scoped to `.home`): the sage field, off-white sheets, one pine chapter, and the one-thread illustrations from `design-system/project/assets/`, copied into `web/src/assets/illustrations/`. On the field the main action is the ink Button and the Underline is off-white; tomato appears only on a sheet or the pine chapter, as the Underline there and the sample tour's progress line, besides the wordmark dot. Who the product is for follows ADR-0017, and every status it shows traces to `docs/CAPABILITIES.md`. The sample console window inside it keeps the console's own tokens.

## Open work

- **Account screens.** The small labels above the sign-in and registration titles are still uppercase monospace, which the system does not allow.
- **The company site** at lotuspond.ai (now in [lotuspond-ai/lotuspond](https://github.com/lotuspond-ai/lotuspond), `site/`) still shows the early-access page. Replacing it is a hosted change with its own scope.
- **Icons and a logo file.** The system has neither yet. The wordmark is the logo, and a word or a dot plus a word comes before an icon.
