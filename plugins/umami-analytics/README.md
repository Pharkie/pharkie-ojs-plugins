# Umami Analytics Plugin for OJS

Adds [Umami](https://umami.is) — privacy-friendly, cookieless web analytics — to the reader-facing pages of an OJS journal, and records custom events for the actions worth measuring on a paywalled journal.

## What it tracks

- **Page views** — automatic (article landings, issue tables of contents, search results, etc.).
- **`download`** — galley file downloads, tagged `type` = `pdf` / `html` / `xml`.
- **`paywall-view`** — the non-subscriber CTA (Inline HTML Galley plugin) was shown to a reader.
- **`membership-click`** — a click on the membership link in the paywall CTA.
- **`purchase-click`** — a click on an article-purchase / subscription payment link.
- **`doi-click`** — a click on a DOI / reference resolver link.
- **`search`** — a search form submission (the query text is deliberately not captured).
- **`login-click` / `register-click`** — clicks on the login / register links.

Only reader-facing (`frontend/`) pages are instrumented — the editorial back office is never tracked. Logged-in staff (managers, sub-editors, admins) can optionally be excluded so their browsing doesn't inflate reader stats.

## Requirements

- OJS 3.5+
- A Umami website ID (Umami Cloud or self-hosted).

## Setup

1. Enable the plugin in **Settings → Website → Plugins → Generic Plugins → Umami Analytics**.
2. Click **Settings** and paste your **Website ID**. For Umami Cloud leave the script URL at its default (`https://cloud.umami.is/script.js`); for self-hosted, point it at `https://your-umami-host/script.js`.

Nothing loads until a website ID is configured.

## Documentation

See **[docs/umami-analytics-plugin.md](../../docs/umami-analytics-plugin.md)** for the full guide — Umami account setup, event reference, and dashboard tips.

## LLM Generated, Human Reviewed

This code was generated with Claude Code (Anthropic, Claude Opus 4.8). Development was overseen by the human author with attention to reliability and security. Architectural decisions, configuration choices, and development sessions were closely planned, directed and verified by the human author throughout. The code and test results were reviewed and tested by the human author beyond the LLM. Still, the code has had limited manual review, I encourage you to make your own checks and use this code at your own risk.

## License

PolyForm Noncommercial 1.0.0 — see [LICENSE.md](../../LICENSE.md).
