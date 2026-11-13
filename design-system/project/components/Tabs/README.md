# Tabs

A pill-shaped switch between two to four views of the same thing, such as "API" and "MCP".

**You provide** the labels (one or two words each), the panels, and the script that shows one panel at a time. Give the list `role="tablist"`, each button `role="tab"` with `aria-selected` and `aria-controls`, and each panel `role="tabpanel"`. Left and right arrow keys move between tabs.

- The selected tab is `ink` with `surface` text. Tabs never use tomato.
- On `deep`, give the list no background, set unselected tabs in `on-deep-muted`, and fill the selected tab with `on-deep` and `pine-900` text.
- For three long options with a description each, stack them as a vertical list on `surface-tint` and mark the selected one with a `surface-raised` card and an `accent-mark` dot.
- Don't use Tabs for page navigation, and don't switch tabs automatically.
