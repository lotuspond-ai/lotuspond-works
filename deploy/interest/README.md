# Lotuspond launch registration

Standalone public early-access landing page, bounded email capture, and transactional confirmation, deployed to the existing
Cloudflare Worker `lotuspond-main`. Both `lotuspond.ai` and `www.lotuspond.ai` serve
this page. This replaces the old public Next.js site, not the local React console
or Python API. The archive remains unchanged. Deployment identifiers were recovered
from the archived `AG2/lotuspond-main-client/wrangler.jsonc` and verified against
Cloudflare before cutover; no legacy source or environment files were restored.

## Storage and access

Cloudflare account: `a9415202683c1d7629eef5f39330aa75`.
KV namespace: `LOTUSPOND_LAUNCH_INTEREST`, ID `ef17443628cc41b7932aa2fa4948ba13`.
Open Cloudflare → Storage & databases → KV → this namespace to inspect registrations.
Access requires the Cloudflare account; there is no public list/export endpoint.
Each value contains `email`, `registeredAt` (UTC), `source`, and consent version
`lotuspond-early-access-v1` (earlier records retain `lotuspond-launch-updates-v1`). Keys are `email:` followed by SHA-256 of the trimmed,
lowercase email. The email remains readable to authorized storage administrators;
hashing the key is not encryption. Re-registering updates the same record and
renews its consent timestamp. KV propagation is eventually consistent.

No IP address is retained in registration records. Cloudflare's rate limiter uses
the connecting IP for five attempts per minute per Cloudflare location; it is not
a global quota or a complete bot defense. A honeypot, required consent, restricted
origins, JSON-only requests and a 2 KiB request limit supplement validation.
Mailbox ownership is not verified. Each new signup queues one transactional confirmation
from **Lotuspond <hello@lotuspond.ai>**. Future early-access invitation campaigns are not
implemented here and must honor consent and opt-out requests. Monitor `srujan@lotuspond.ai` for replies to its `hello@lotuspond.ai` alias;
sender-domain verification alone does not create an incoming mailbox. Remove a registration on a valid withdrawal request; do not reuse this
list for advertising. Records have no automatic expiry.

## Local checks

From the repository root:

```sh
node --import tsx --test deploy/interest/worker.test.ts deploy/interest/confirmation.test.ts
npx tsc -p deploy/interest/tsconfig.json
npx wrangler@4.158.0 dev --config deploy/interest/wrangler.local.jsonc --port 8788
npx wrangler@4.158.0 deploy --dry-run --config deploy/interest/wrangler.jsonc
```

The local config has no account or routes and uses emulated storage only. Use it
for browser tests: production routes otherwise rewrite the preview request host.
Never pass `--remote` for local tests. The production namespace is not a test store.
The browser was checked at 1440, 768 and 375px with keyboard focus and successful
synthetic submission. Tests cover invalid shapes, consent, honeypot, origins,
content type, bounded bodies, normalization, duplicates, limits and storage failures.

## Returning visitors

After the API acknowledges a saved registration, the form and original headline are
replaced with a centered “You’re on the list” acknowledgement. “Register another
email” clears the presentation cookie and restores an empty form with email focus;
it does not remove any saved registration. Errors keep the form visible.

`lotuspond_interest=registered` is a six-month, SameSite=Lax cookie, Secure on HTTPS,
shared between the apex and www domain. It contains no email or identity and grants
no access. Blocking cookies still allows registration, but returning visits cannot
be remembered. Clearing cookies, changing browsers, or registrations made before
this feature show the form again. The site explains the cookie beside the form.
Browser checks cover successful and rejected submissions, reload persistence,
clearing the preference, another-email signup, responsive layout and keyboard focus.

## Confirmation delivery

`email-template.ts` owns matching HTML and plain-text templates. `ConfirmationEmail`
is a Durable Object per normalized email hash. The registration is saved before
queueing. Concurrent/repeated signups share the same job and do not resend accepted
messages. Alarms retry explicit throttling/domain rejections, with a six-attempt
limit; ambiguous sends are marked unknown rather than risking duplicate mail.
Provider acceptance is not inbox delivery. Terminal jobs clear their email field.
`confirmation:<hash>` KV records expose only delivery status/attempt count/message
ID/template version to the account administrator. No public list or status API exists.
The local binding emulates sending; local tests never contact recipient mailboxes.

Cloudflare Email Sending requires Workers Paid; the owner upgraded the account on
6 November 2026. The current account sending quota is 200 messages/day. This quota
may change; check Cloudflare before launch traffic increases. Cloudflare sending is
configured for `lotuspond.ai`, with `cf-bounce` return path and DKIM selector. Root
incoming MX still points to Google (`smtp.google.com`). This deployment does not
send to registrations collected before confirmation was enabled unless they sign
up again. A queue-enqueue failure leaves the registration saved and returns a delay
notice; a subsequent signup can retry enqueueing. No historical campaign was sent.

## Deployment and rollback

```sh
npx wrangler@4.158.0 deploy --config deploy/interest/wrangler.jsonc
```

Wrangler authentication stays outside the repository. The pre-cutover version of
`lotuspond-main` was `3b32a162-a985-4256-acef-aaa4bee629bb`, deployed 15 September 2026. Roll back the Worker to that version if necessary, preserving the KV
namespace and all registration data. Verify existing domains and the current
deployment before a future change. Do not delete the namespace during rollback.

The root API changelog entry is a local source change; deploying this standalone
Worker does not deploy the Python API or publish Git commits.

## Observed deployment — 6 November 2026

Initial capture-only version: `80e07bc6-e4ef-45a7-a1d6-a39c3cae47ab`.
Redesigned page and confirmations: `dc1aebd5-7204-439c-a6d5-bdb0dd00fd33`.
Arrow-removal version: `2a6fa1c7-a101-4b19-9970-f920a539a09d`, removing the decorative
arrow above the signup-card title. Desktop, tablet and mobile layout and keyboard
focus were rechecked on the live page.
Both domains served the corrected teaser-only copy, with no product category in
the visible text or search metadata. The live browser form acknowledged a
synthetic registration; its exact KV record was independently read back to verify
email, timestamp, consent and source, then that synthetic record alone was deleted.
No visitor records were listed or read. The initial disclosure-bearing deployment
attempt was blocked before publication; only the corrected copy was deployed.

The current design removes the illustrated background and eyebrow entirely, using
ivory paper, oversized serif lettering, copper accents and a green invitation panel.
The signup benefit is early access; no product category is disclosed. A live signup
to the account login address was stored and its confirmation accepted in one attempt;
repeating signup returned the existing registration without queueing another email.
Cloudflare subsequently reported `dsn_hard_bounce 5.1.1`; the owner confirmed that
login address has no mailbox. That test registration alone was deleted with explicit
owner approval; its hash-only confirmation tombstone remains. A second test was
requested to the owner’s Gmail inbox and accepted in one attempt. Cloudflare’s
message lifecycle subsequently reported Delivered for that Gmail test. Incoming
`hello@lotuspond.ai` was saved and read back as a Google Workspace alias of the
owner’s existing `srujan@lotuspond.ai` inbox. The existing `s@lotuspond.ai` alias
was preserved, as was the root Google MX. An incoming reply through the new alias
has not independently been observed; monitor the receiving inbox for opt-outs.
Delivery receipts distinguish provider acceptance from later delivery outcomes.
Only exact test confirmation receipts were inspected; no visitor records were listed.

Validation: twelve registration/confirmation tests, strict TypeScript, deployment dry run, application
build and two isolated PostgreSQL API boundary/changelog integration tests passed.
Responsive browser checks covered 1440/768/375px and visible keyboard focus. The
root `npm run check` did not pass: its Python run reported 5 fleet-test failures
and 490 execution-test setup errors (qualification requires a verified PostgreSQL
test environment), with 606 passed and 195 skipped. These broader results do not
certify the platform. The standalone Worker does not use that Python runtime.
The disposable local PostgreSQL server was stopped after the focused checks.

Current version: `771bb104-ba56-4358-bbba-bade875859c4`. The centered acknowledgement,
six-month cookie and alternate-email flow are deployed. The owner’s existing Gmail
registration showed the new acknowledgement without another email; navigating to
www retained the remembered view. Local browser checks covered reset/reload and a
server-rejected email retaining the form. Focused tests (12), strict TypeScript and
local PostgreSQL boundary tests (2) passed. The full repository check again reported
5 failures and 490 setup errors with 606 passed, matching the earlier broader result.

## Artwork provenance

`public/vitruvian-man.svg` is the unmodified [Vitruvian Man tracing](https://commons.wikimedia.org/wiki/File:Da_Vinci_Vitruvian_Man_traced.svg),
vectorization credited to Transoxsiana, extracted with background removal by
Ilzolende (1 July 2025), after Leonardo da Vinci. It is distributed under
[CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/). Attribution and
license links appear directly below the illustration. The source SVG was inspected
for active/external content before bundling; visitors load it from this site's own
assets, without third-party requests. It is only shown on the signup view; the
remembered confirmation stays centered. Desktop/tablet/mobile and focus checks
were repeated after adding the illustration.

Illustration deployment: `22e211d8-6378-4a58-8fbc-4d8c0495fd04`. Background alternatives are local visual previews under `output/landing-variants/`; none of those treatments has been deployed.

## Selected background design and DNA detail

The owner selected preview 3 (light invitation): a pale signup card, green action,
and oversized Vitruvian Man behind it. Credits remain in the footer. A small inline
DNA helix and decorative A/T/G/C lettering sit beneath the opening headline; they
are an original visual motif, not biological data or a claim about the product.
The artwork and DNA disappear with the signup view after registration, preserving
the centered acknowledgement. Light-theme validation errors and keyboard focus
use dark, contrasting colours. Desktop/tablet/mobile layout, successful local
signup and remembered reload were checked after applying the selected design.

Selected design deployed as `4bc3e1c7-b5b0-49e6-83af-77351817efdd` on
6 November 2026; hosted artwork and DNA were visually verified. Twelve focused
registration/mail tests, strict TypeScript, formatting and two isolated database
boundary checks passed. The required full repository check again reported the
same 5 failures and 490 setup errors (606 passed, 195 skipped); it is not green.

## Link previews

The browser tab is titled `Lotuspond · Early access`; the saved-registration view
uses `Lotuspond · You’re on the list`. Static Open Graph and Twitter Card tags
provide the same product-neutral title and description without requiring JavaScript.
The 1200 × 630 JPEG at `/social/lotuspond-early-access-20261106-v1.jpg` reflects the
selected design and is served publicly through the Worker. Its filename is versioned
so future artwork updates can use a new URL. The image preserves the artwork credit
and license; it was rendered from the local share-card HTML, not AI-generated.
External platforms manage their own preview caches. Publishing new tags does not
prove a platform has refreshed its stored preview or rewritten existing posts.

Title and sharing metadata deployed as `10828939-7f14-4c15-826c-af9acb58bc85`.
The live browser title and public 1200×630 image were verified after deployment.
Requests using Facebook, X and WhatsApp crawler user-agent strings received the
updated static tags; the live image bytes matched the local artifact. These are
origin-access checks, not evidence that those services have refreshed their caches.
Focused tests and isolated boundary checks passed; the full repository check still
reported the same 5 failures and 490 execution setup errors (606 passed).

## Artwork visibility correction

The artwork is hidden at widths up to 1100px and on touch-first devices. On desktop,
a clip on the artwork itself retains only the upper 42%, with a fade above the
waist. The card no longer provides coverage: scrolling, window height, and form
position cannot reveal the lower body. Browsers without clip-path support hide
the backdrop. The original attributed SVG remains unmodified.

Social metadata now points to `lotuspond-early-access-20261106-v2.jpg`, rendered
with the same upper-body crop. The old v1 image URL also serves the cropped image
for clients that re-fetch it. Existing third-party caches may retain prior bytes
until refreshed.

Deployed as `1d8bf51d-b3dd-4655-8961-a4e7e8d29511` on 6 November 2026.
Verified live mobile hiding and desktop scrolling crop, plus both domains' CSS,
metadata and exact v2 JPEG bytes. Local widths 320/375/768/1100/1101/1440/1920
and a large touch-screen simulation passed; keyboard focus remained visible.
Twelve focused tests, strict TypeScript, formatting and deployment dry run passed.
The full repository check again reported 5 failures and 490 execution setup errors,
with 606 passed and 195 skipped. No registration data was accessed or changed.
LinkedIn's inspector displayed the cropped image, Facebook re-scraped the v2 image
URL with HTTP 200, and X's validator reported successful fetch/card load. No posts
were published; existing posts and untested apps can retain their own cached cards.
