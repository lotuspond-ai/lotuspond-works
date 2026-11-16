LotusPond builds AI agents that work inside the systems a business depends on. LotusPond Works automates supply chain and operations. LotusPond Health keeps a family's health records, ready for any AI agent; its API lives only in the Health repository. Every page, product screen and deck for either product starts from these rules.

## The idea: below the surface, above the surface

A lotus roots in mud and blooms above the water. Agents do the tangled work below the surface, in ERPs, warehouse tools, record systems and inboxes. People and products get one clean interface above it.

- Show the idea with the thread: a tangle where the systems are, one clean line after it, and a bloom at the end.
- Tell every story in that order: the mess that exists, the agents that handle it, the one interface you get.

## Colour

Three brand colours and one ink. Use them in this order of area.

1. **Sage leads.** `sage-500` is the brand. Use it as `field`, the ground of marketing pages, covers and hero panels. Set text on it in `on-field` or `on-field-muted`. Never set white or `off-white` text on sage: it reads at 2.2:1.
2. **Off-white is for reading.** Long text, product screens and forms sit on `surface` sheets. Raise a card with `surface-raised`, and group cards on `surface-tint`.
3. **Pine gives depth.** Use `deep` for developer content, code and one dark chapter per page, with `on-deep` text.
4. **Tomato is spent once per view, and never on sage.** `accent` fills the single main action on a `surface` or `deep` sheet. `accent-mark` draws the bloom, the Underline on a sheet and the wordmark dot. Use tomato for nothing else: no borders, no backgrounds, no second button.

Rules that keep it readable:

- Body text is `ink` on `surface` (15:1) or `on-field` on `field` (7:1).
- Tomato never sits on sage. The two are too close in lightness: on `sage-500`, `accent-mark` reads at 1.7:1 and an `accent` Button at 2.0:1. For people with the most common kind of red-green colour blindness the pair drops to 1.4:1 and both look khaki.
- So on `field` the main action is the `ink` Button (7:1) and the Underline is `off-white`. Tomato sits only on `surface` and `surface-raised`, where it reads at 3.7:1 or better, and on `deep`, where a mark reads at 4.1:1.
- The wordmark dot is the one exception. It is part of the logo, and the name reads without it.
- Put `on-accent` text on `accent`, never on `accent-mark`. `accent-mark` passes only for marks and for type of 24px and up.
- Tomato never means an error. Errors use `danger` and `danger-fill`.
- Sage and tomato are close in lightness, so never use them alone to tell two states apart. Every status carries a word. A healthy state is a light tint (`positive-tint`); a waiting or failed state is a solid fill (`accent`, `danger-fill`).
- The dark theme keeps every role and every rule. `field` becomes a deep sage, and its main action is still the `ink` Button. `accent` lightens, so its label switches to the dark `on-accent`.

## Type

The three faces were chosen because they are not the ones every AI product uses. Do not swap in Inter, Geist, Space Grotesk, Instrument Serif or Sans, Bricolage Grotesque, Syne or Fraunces.

- **Platypi** (`display`) is for headlines and the wordmark only. It is a serif with wedge-cut terminals that echo the cut-paper illustrations. Use `display-xl` for the one headline of a page, `display-lg` for section titles, `display-md` for statements and `display-sm` for card and column titles. Weight 600, sentence case, and a full stop at the end of a headline.
- **Atkinson Hyperlegible Next** (`sans`) is for everything that is read or pressed: `lede`, `body`, `body-sm`, `label`, `caption`. It was drawn so that 0 and O, and 1, l and I, cannot be mistaken for each other, which matters for order numbers, amounts and health records.
- **Atkinson Hyperlegible Mono** (`mono`) is for code and nothing else. Never use it for labels, tags or eyebrows.
- Display sizes are fluid on the web. The tokens hold the largest size. On a phone, `display-xl` is 42px, `display-lg` 34px, `display-md` 26px and `display-sm` 20px.
- Keep running text under 34em. Give headlines `text-wrap: balance`.
- No all-caps, no letter-spaced labels, no italics for emphasis. Emphasis in a headline is the Underline, once per view at most.
- All three faces are open source and load from Google Fonts. Where a content security policy blocks font services, serve the font files from the product's own origin.

## Shape and layout

- A page is a `field` with sheets on it. A Sheet is a `surface` or `deep` panel with `radius-xl` corners, inset `space-4` from the page edge (`space-2` on a phone), with `space-4` between sheets.
- Inside a sheet, radii step down: `radius-lg` for panels and code, `radius-md` for cards, `radius-sm` for rows and inputs, `radius-pill` for buttons, tags, tabs and chips.
- Use `shadow-card` only on a card that sits on `surface-tint`, and `shadow-float` only on the one floating card over an illustration. No other shadows, no gradients, no glass, no glows.
- Separate columns with a 1.5px `ink` rule above each, not with boxes. Separate rows with a 1px `line`.
- Lay out on 12 columns, 1240px wide at most: headline on the left seven, supporting text on the right five. Never centre a whole section.
- One idea per sheet. If a sheet needs a second visual, make it two sheets.
- Between two sheets, a band on the `field` may hold one headline, one `lede` and a row of Chips. Apart from that, only the hero and the closing call to action sit on the bare field.
- The home page hero is the headline across the full width. It ends in two system names on `off-white` chips, joined by the thread, and the pair changes every few seconds. One wide `deep` scene sits below it: the systems tangled on the left, the thread, and the bloom in a hand on the right. A product page hero shows one raised product card or one CodeBlock beside the headline, with `shadow-float`.
- Number things only when the order is real, and use Steps for it.

## Product screens

- A product screen is a frame and a panel: `surface-tint` for the frame and its navigation, `surface-raised` for the inset panel that holds the work, and `surface` for wells and hover fills.
- The main action on a product screen is the `brand` Button: a `sage-700` fill with white text. Links and the active navigation icon use the same green.
- Tomato appears on a product screen only where a person has to decide: an approval, a review, a row that waits for someone. Use `accent` for that control and `accent-tint` for its row, once per view at most, and never beside a destructive action.
- Body text on a product screen is 14px. Titles are `sans`, 24px, weight 600; `display` is kept for the wordmark and account screens.
- Every status carries a written label, and every screen has honest loading, empty and failed states. Never show sample data as a customer's own.

## Illustration

One idea runs through every picture: **one thread**. An agent's work is a single continuous line that runs through the real things a business works with and ends in the lotus bloom.

| Shape | Means | Drawn as |
| --- | --- | --- |
| Thread | An agent's work | One `pine-900` line, 3px, round ends, never broken. It may tangle where the systems are, and it runs clean after that |
| Bloom | The outcome, or the decision | Five `accent-mark` petals on an `off-white` disc. The only tomato shape, one per picture |
| Hand | A person | An open palm that receives or gives, or a closed hand that stamps. `off-white`, with a `sage-700` cuff. Never a full figure |
| Things | What the work is about | Parcels, trucks, shelves and documents for Works; record folders, vials, pill bottles and a pulse card for Health |
| Seed | A record on its way | A small `off-white` dot on the thread |

- Flat shapes with no outlines, gradients or shadows. Use only the brand colours.
- One ground per picture: `sage-500`, `sage-200` or `pine-900`. On `pine-900` the thread turns `off-white`. A large disc in a neighbouring sage may sit behind the subject.
- The bloom never touches sage. It sits on an `off-white` disc a little wider than itself, on every ground, or on the paper when a hand stamps a document. Where the thread crosses the disc it is `pine-900`.
- People decide and agents do the work, so the bloom is made or held by a hand, and the thread never stamps or signs anything.
- At most about seven things in a picture. Tilt objects a few degrees so they look placed by hand.
- No words inside a picture, except a Chip that labels the thread in the home page scene. The text beside a picture carries the meaning, so give it `alt=""`.
- Sizes: 4:3 for sections and cards, and a wide 4:1 strip for closings. Round the corners with `radius-lg`.
- Placement: one picture per sheet or band, or a row of three above three columns, which counts as one visual. On the `field`, use a `sage-500` picture so it sits without a frame; on a `deep` sheet, a `pine-900` one.
- The pictures are in the Illustrations asset group. Draw a new one from the same shapes for each launch, post or page, so they stay in one hand.
- Product screens in marketing are simplified, use sample data and say so in a caption.

## Motion

- One moving thing per view. On the home page the headline's pair of systems changes every few seconds, and in the scene below it seeds travel along the thread to the bloom. One Pause control stops both.
- Movement follows the thread, from the systems toward the bloom.
- Draw the Underline once, on load. Everything else appears in place.
- Anything that loops for more than five seconds gets a visible Pause control, and `prefers-reduced-motion` shows the still frame.

## Voice

- A headline says plainly what the product is and who it is for: "AI agents that do the manual work between your systems.", "AI agents for supply chain operations.", "One API for every health record." If a line could sit on any company's site, rewrite it.
- Keep a clever line for the end of a page, after the plain one has done its job, or leave it out.
- Explain a term the first time it appears: "electronic health record (EHR) system", "MCP, the standard way AI assistants connect to outside data".
- Write to "you". Say "LotusPond Works" and "LotusPond Health" on first mention, then "Works" and "Health".
- Name systems by kind, not by vendor, unless the integration is live.
- Say where each product stands: "Early access", "In development". Never show customer logos, numbers or quotes that are not real.
- A button says what happens: "Get early access", "Approve", "Join the Health waitlist".
- No emoji, no exclamation marks, and none of "revolutionary", "seamless" or "powerful".

## Iconography and logo

- There is no icon set yet. Use a word, or a dot plus a word, before reaching for an icon.
- There is no logo file yet. The Wordmark component is the logo: the name set in `display`, weight 700, with the `accent-mark` dot.

## Components

Button, Tag, Tabs, Sheet, Chip, CodeBlock, Steps, Compare, Underline and Wordmark. Each has its own guidelines. Their styles are the `lp-` classes in `components/bundle.css`, which read only the tokens above.
