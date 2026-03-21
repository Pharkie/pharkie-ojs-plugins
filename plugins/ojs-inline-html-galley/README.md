# Inline HTML Galley Plugin for OJS

Renders HTML galley content directly on article landing pages, replacing the separate "Full Text" viewer with inline content. Readers with access see the article body immediately — no extra click needed. Readers without access see a call-to-action with membership/purchase links.

## Requirements

- OJS 3.5+

## Installation

1. Copy the plugin folder to `plugins/generic/inlineHtmlGalley/` in your OJS installation.
2. Enable the plugin in **Website Settings → Plugins → Generic Plugins**.
3. Clear the OJS cache: `rm -rf cache/fc-* cache/wc-* cache/opcache`

No database changes or composer dependencies required.

## How it works

For any article where the user **has access** (open-access, active subscription, domain-based access, or completed purchase) and a galley labeled **"Full Text"** exists (HTML file):

1. Reads the HTML file and extracts the `<body>` content.
2. Renders it inline on the article page in a `<section class="inline-html-galley">` block.
3. Hides the "Full Text" galley link (PDF and other links remain visible).

### Access messages

The plugin shows a contextual info box above the inline content:

| User type | Message |
|---|---|
| Journal administrator | "You have access as a journal administrator." |
| Synced SEA member (via WP) | "Showing article full text linked to your SEA membership." |
| Direct OJS subscriber | "Showing article full text via your journal subscription." |
| Article purchaser | "You have access via direct purchase." |

### Non-subscriber behaviour

Users without access see a call-to-action box with links to membership signup and information about per-article/issue purchase options.

### Issue TOC / archive pages

On issue table-of-contents and archive listing pages, **all galley links are hidden** (PDF, HTML, Full Text). Readers click the article title to reach the landing page, where access logic determines what they see.

## Paywall sections

Only articles in the **"Articles"** section are paywalled. Editorial, Book Review Editorial, and Book Reviews sections are always open/free. The access message box only appears on paywalled articles.

## Docker deployment

Bind-mount the plugin directory:

```yaml
# docker-compose.yml
services:
  ojs:
    volumes:
      - ./plugins/ojs-inline-html-galley:/var/www/html/plugins/generic/inlineHtmlGalley
```

## HTML galley format

The plugin expects HTML galley files with a standard HTML structure (`<!DOCTYPE html>`, `<html>`, `<head>`, `<body>`). It extracts the content between `<body>` and `</body>` tags. Body-only files (no DOCTYPE) also work — the content is used as-is.

The galley must be labeled exactly **"Full Text"** (case-sensitive) in OJS.

## License

GNU General Public License v3.0. See [LICENSE](https://www.gnu.org/licenses/gpl-3.0.html).
