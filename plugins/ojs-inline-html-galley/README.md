# Inline HTML Galley Plugin for OJS

Renders HTML galley content directly on article landing pages, replacing the separate "Full Text" viewer with inline content. Readers with access see the article body immediately — no extra click needed. Readers without access see a call-to-action with membership/purchase links.

## Requirements

- OJS 3.5+

## Installation

1. Copy the plugin folder to `plugins/generic/inlineHtmlGalley/` in your OJS installation.
2. Enable the plugin in **Website Settings → Plugins → Generic Plugins**.
3. Clear the OJS cache: `rm -rf cache/fc-* cache/wc-* cache/opcache`

No database changes or composer dependencies required.

### Files included

```
inlineHtmlGalley/
├── InlineHtmlGalleyPlugin.php   # Main plugin class
├── version.xml                  # Plugin metadata
├── locale/en/locale.po          # English strings
└── README.md
```

## How it works

For any article where the user **has access** (open-access, active subscription, domain-based access, or completed purchase) and a galley labeled **"Full Text"** exists (HTML file):

1. Reads the HTML file and extracts the `<body>` content.
2. Renders it inline on the article page in a `<section class="item inline-html-galley">` block.
3. Hides the "Full Text" galley link on article pages (PDF and other links remain visible).
4. On issue TOC / archive listing pages, hides **all** galley links — readers click the article title to reach the landing page.

### Access messages

The plugin shows a contextual info box above the inline content:

| User type | Message |
|---|---|
| Journal administrator | "You have access as a journal administrator." |
| Synced member (via WP-OJS sync) | "Showing article full text linked to your membership." * |
| Direct OJS subscriber | "Showing article full text via your journal subscription." |
| Article purchaser | "You have access via direct purchase." |

### Non-subscriber behaviour

Users without access see a call-to-action box with links to membership signup and per-article/issue purchase options.

## Customisation required

**This plugin contains hardcoded text specific to the Society for Existential Analysis.** If deploying on a different journal, you must edit `InlineHtmlGalleyPlugin.php`:

- **Non-subscriber CTA** (`getNonSubscriberNotice()`): Contains hardcoded URLs (`community.existentialanalysis.org.uk`), organisation name ("SEA membership"), and prices ("£3" / "£25"). Update these to match your journal.
- **Subscriber messages** (`getSubscriberNotice()`): * References "SEA membership" for synced members. Update to match your organisation name.
- **Archive quality notice** (`getArchiveNotice()`): Shows "digitally restored from print" on all inline HTML articles. Remove or customise if not applicable to your content.
- **Paywall section name**: The plugin paywalls only articles in a section titled exactly **"Articles"** (case-sensitive, in `getSubscriberNotice()`). If your journal uses a different section name, update this string match.

## HTML galley format

The plugin expects HTML galley files with a standard HTML structure (`<!DOCTYPE html>`, `<html>`, `<head>`, `<body>`). It extracts the content between `<body>` and `</body>` tags. Body-only files (no DOCTYPE) also work — the content is used as-is.

The galley must be labeled exactly **"Full Text"** (case-sensitive) in OJS.

## Integration with WP-OJS Sync

The plugin checks for a `wpojs_created_by_sync` user setting (set by the companion WP-OJS Subscription API plugin) to distinguish synced members from direct OJS subscribers. Without the sync plugin installed, all subscribers show the "direct OJS subscriber" message — functionally harmless.

## Docker deployment

Bind-mount the plugin directory:

```yaml
# docker-compose.yml
services:
  ojs:
    volumes:
      - ./plugins/ojs-inline-html-galley:/var/www/html/plugins/generic/inlineHtmlGalley
```

## Uninstallation

1. Disable the plugin in **Website Settings → Plugins**.
2. Remove the plugin directory and Docker bind mount.
3. No database cleanup needed — the plugin does not create tables or persistent settings.

## LLM Generated, Human Reviewed

This code was generated with Claude Code (Anthropic, Claude Opus 4.6). Development was overseen by the human author with attention to reliability and security. Architectural decisions, configuration choices, and development sessions were closely planned, directed and verified by the human author throughout. The code and test results were reviewed and tested by the human author beyond the LLM. Still, the code has had limited manual review, I encourage you to make your own checks and use this code at your own risk.

## License

PolyForm Noncommercial 1.0.0 — see [LICENSE.md](../../LICENSE.md).
