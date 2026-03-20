# Inline HTML Galley Plugin for OJS

Renders HTML galley content directly on article landing pages when the user has access, replacing the separate "Full Text" viewer link with inline content. No extra click needed to read the article.

## Requirements

- OJS 3.5+

## Installation

1. Copy the plugin folder to `plugins/generic/inlineHtmlGalley/` in your OJS installation.
2. Enable the plugin in Website Settings > Plugins > Generic Plugins.

## How it works

For any article where:
- The user **has access** (open-access, active subscription, domain-based access, or completed purchase), and
- A galley labeled **"Full Text"** exists (HTML file)

The plugin:
1. Reads the HTML file and extracts the `<body>` content.
2. Renders it inline on the article page in a `<section class="inline-html-galley">` block.
3. On **article pages**: hides the "Full Text" galley link when inline content is shown. PDF and other galley links remain visible. Users without access still see the "Full Text" link with the purchase price.
4. On **issue TOC / archive pages**: hides **all** galley links (PDF, HTML, Full Text). Readers click the article title to reach the landing page, where access logic determines what they see.
