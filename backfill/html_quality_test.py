#!/usr/bin/env python3
"""
HTML quality test: compare PyMuPDF raw, Haiku cleanup, and Sonnet cleanup
for 10 sample articles spanning different quality levels.
"""

import json
import os
import sys
import time
import fitz  # PyMuPDF
import anthropic

OUTPUT_DIR = "/tmp/html-quality-test"

# 10 test articles: (label, pdf_path, description)
# For vol 1, extract pages from source PDF since no split exists
SAMPLES = [
    {
        "label": "v01-editorial",
        "pdf": "/workspaces/pharkie-ojs-plugins/backfill/private/input/1.pdf",
        "pages": (4, 4),  # 0-based
        "desc": "Vol 1 Editorial (1994, early, short)",
    },
    {
        "label": "v25-editorial",
        "pdf": "/workspaces/pharkie-ojs-plugins/backfill/private/output/25.1/01-editorial.pdf",
        "desc": "Vol 25.1 Editorial (2014, born-digital)",
    },
    {
        "label": "v33-editorial",
        "pdf": "/workspaces/pharkie-ojs-plugins/backfill/private/output/33.2/01-editorial.pdf",
        "desc": "Vol 33.2 Editorial (2022, 2-page)",
    },
    {
        "label": "v26-bookreview-ed",
        "pdf": "/workspaces/pharkie-ojs-plugins/backfill/private/output/26.1/16-book-reviews.pdf",
        "desc": "Vol 26.1 Book Review Editorial (2015, 4 pages)",
    },
    {
        "label": "v30-article",
        "pdf": "/workspaces/pharkie-ojs-plugins/backfill/private/output/30.1/02-facing-an-uncertain-future-the-next-30-years-of-existential-therapy.pdf",
        "desc": "Vol 30.1 Article (2019, born-digital, 14 pages)",
    },
    {
        "label": "v22-article",
        "pdf": "/workspaces/pharkie-ojs-plugins/backfill/private/output/22.2/02-de-beauvoir-bridget-jones-pants-and-vaginismus.pdf",
        "desc": "Vol 22.2 Article (2011, medium length)",
    },
    {
        "label": "v10-article",
        "pdf": "/workspaces/pharkie-ojs-plugins/backfill/private/output/10.1/01-reculer-pour-mieux-sauter.pdf",
        "desc": "Vol 10.1 Article (1999, older format)",
    },
    {
        "label": "v13-article",
        "pdf": "/workspaces/pharkie-ojs-plugins/backfill/private/output/13.1/02-death-a-philosophical-perspective.pdf",
        "desc": "Vol 13.1 Article (2002, scanned/OCR)",
    },
    {
        "label": "v06-article",
        "pdf": "/workspaces/pharkie-ojs-plugins/backfill/private/output/6.2/02-husserl-phenomenology-and-psychology.pdf",
        "desc": "Vol 6.2 Article (1995, scanned/OCR)",
    },
    {
        "label": "v37-article",
        "pdf": "/workspaces/pharkie-ojs-plugins/backfill/private/output/37.1/02-therapy-for-the-revolution-lessons-from-the-front-line.pdf",
        "desc": "Vol 37.1 Article (2026, most recent)",
    },
]

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
  body {{ max-width: 700px; margin: 2em auto; padding: 0 1em; font-family: Georgia, serif; line-height: 1.6; color: #333; }}
  h1, h2, h3 {{ font-family: sans-serif; }}
  .meta {{ color: #666; font-style: italic; margin-bottom: 2em; border-bottom: 1px solid #ccc; padding-bottom: 1em; }}
  blockquote {{ border-left: 3px solid #ccc; margin-left: 0; padding-left: 1em; color: #555; }}
</style>
</head>
<body>
<div class="meta">{meta}</div>
{body}
</body>
</html>"""


def extract_text(pdf_path, pages=None):
    """Extract text from PDF using PyMuPDF. pages=(start, end) 0-based inclusive."""
    doc = fitz.open(pdf_path)
    text_parts = []
    if pages:
        start, end = pages
        for i in range(start, min(end + 1, len(doc))):
            text_parts.append(doc[i].get_text())
    else:
        for page in doc:
            text_parts.append(page.get_text())
    doc.close()
    return "\n\n".join(text_parts)


def raw_to_html(text, title, meta):
    """Convert raw extracted text to paragraph-wrapped HTML."""
    paragraphs = []
    for block in text.split("\n\n"):
        block = block.strip()
        if not block:
            continue
        # Merge single newlines within a block (line breaks from PDF columns)
        block = block.replace("\n", " ")
        # Clean up multiple spaces
        while "  " in block:
            block = block.replace("  ", " ")
        paragraphs.append(f"<p>{block}</p>")

    body = "\n".join(paragraphs)
    return HTML_TEMPLATE.format(title=title, meta=meta, body=body)


PROMPT_STANDARD = """Convert this raw text extracted from a journal article PDF into clean, semantic HTML.

Rules:
- Use semantic tags: <h1> for title, <h2>/<h3> for sections, <p> for paragraphs, <blockquote> for quotes, <em>/<strong> as appropriate
- Fix OCR artifacts, column interleaving, broken words, and stray characters
- Preserve all actual content — do not summarize or omit text
- Do not add content that isn't in the source
- Merge broken lines/paragraphs that were split by PDF column layout
- Output ONLY the HTML body content (no <html>, <head>, <body> tags, no CSS)
- If text has footnotes/endnotes, keep them at the bottom with appropriate markup

Raw text:
---
{text}
---"""

PROMPT_ENHANCED = """You are an expert editor converting raw OCR text from an academic journal PDF into publication-quality semantic HTML.

This is from "Existential Analysis", a peer-reviewed journal of the Society for Existential Analysis. The text was extracted via PyMuPDF and may have:
- Column interleaving (left/right columns merged incorrectly)
- Broken words across line breaks (hyphenation artifacts)
- Headers/footers mixed into body text (e.g. "Journal of the Society for Existential Analysis", page numbers, vol/issue info)
- OCR errors in older scanned issues (misread characters, missing spaces)
- Footnote numbers mixed into body text

Your task:
1. REMOVE all headers, footers, page numbers, and journal metadata that aren't part of the article
2. Use semantic HTML: <h1> for article title, <h2>/<h3> for section headings, <p> for paragraphs
3. Use <blockquote> for extended quotes, <em> for emphasis/titles, <strong> for strong emphasis
4. Use <ol>/<ul> for lists, <sup> for footnote references
5. Place footnotes/endnotes in a <section class="footnotes"> at the bottom with <ol>
6. Fix OCR artifacts: rejoin hyphenated words, fix obvious character substitutions
7. Reconstruct proper paragraph breaks from column layout
8. PRESERVE all actual content — never summarize, condense, or omit
9. Do NOT add content not in the source
10. Output ONLY the HTML body content (no <html>, <head>, <body>, no CSS)

Raw text:
---
{text}
---"""


## Pricing per million tokens (as of 2025)
# Haiku 4.5: $0.80 input, $4.00 output
PRICING = {
    "claude-haiku-4-5-20251001": {"input": 0.80, "output": 4.00},
}

# Global cost tracker
cost_tracker = {"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0, "calls": 0}


def claude_cleanup(text, model, title, meta, prompt_template=None):
    """Send extracted text to Claude for semantic HTML cleanup. Returns (html, usage_dict)."""
    client = anthropic.Anthropic()

    if prompt_template is None:
        prompt_template = PROMPT_STANDARD
    prompt = prompt_template.format(text=text)

    response = client.messages.create(
        model=model,
        max_tokens=8192,
        messages=[{"role": "user", "content": prompt}],
    )

    # Track usage
    usage = response.usage
    input_tokens = usage.input_tokens
    output_tokens = usage.output_tokens
    pricing = PRICING.get(model, {"input": 0, "output": 0})
    call_cost = (input_tokens * pricing["input"] + output_tokens * pricing["output"]) / 1_000_000
    cost_tracker["input_tokens"] += input_tokens
    cost_tracker["output_tokens"] += output_tokens
    cost_tracker["cost_usd"] += call_cost
    cost_tracker["calls"] += 1

    body = response.content[0].text
    # Strip markdown code fences if present
    if body.startswith("```html"):
        body = body[7:]
    if body.startswith("```"):
        body = body[3:]
    if body.endswith("```"):
        body = body[:-3]
    body = body.strip()

    return HTML_TEMPLATE.format(title=title, meta=meta, body=body), {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cost_usd": call_cost,
    }


def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    # Check for API key
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ERROR: ANTHROPIC_API_KEY not set. Run: export $(grep ANTHROPIC_API_KEY .env)")
        sys.exit(1)

    total = len(SAMPLES)
    for i, sample in enumerate(SAMPLES, 1):
        label = sample["label"]
        desc = sample["desc"]
        pdf_path = sample["pdf"]
        pages = sample.get("pages")

        print(f"\n[{i}/{total}] {desc}")
        print(f"  PDF: {pdf_path}")

        if not os.path.exists(pdf_path):
            print(f"  SKIP: PDF not found")
            continue

        # Step 1: Extract text
        text = extract_text(pdf_path, pages)
        char_count = len(text)
        print(f"  Extracted {char_count} chars")

        if char_count < 50:
            print(f"  SKIP: Too little text extracted")
            continue

        meta = f"Method: {{method}} | {desc} | {char_count} chars extracted"

        # Step 2: PyMuPDF raw HTML
        path_raw = os.path.join(OUTPUT_DIR, f"{label}-pymupdf.html")
        html_raw = raw_to_html(text, f"{desc} (PyMuPDF raw)", meta.format(method="PyMuPDF raw"))
        with open(path_raw, "w") as f:
            f.write(html_raw)
        print(f"  ✓ pymupdf.html")

        # Step 3: Haiku cleanup
        try:
            path_haiku = os.path.join(OUTPUT_DIR, f"{label}-haiku.html")
            t0 = time.time()
            html_haiku, usage = claude_cleanup(
                text, "claude-haiku-4-5-20251001", f"{desc} (Haiku)", meta.format(method="Claude Haiku")
            )
            dt = time.time() - t0
            with open(path_haiku, "w") as f:
                f.write(html_haiku)
            print(f"  ✓ haiku.html ({dt:.1f}s, {usage['input_tokens']}in/{usage['output_tokens']}out, ${usage['cost_usd']:.4f})")
        except Exception as e:
            print(f"  ✗ haiku.html FAILED: {e}")

        # Step 4: Haiku with enhanced prompt (substitute for Sonnet which isn't available on this API key)
        try:
            path_enhanced = os.path.join(OUTPUT_DIR, f"{label}-haiku-enhanced.html")
            t0 = time.time()
            html_enhanced, usage = claude_cleanup(
                text, "claude-haiku-4-5-20251001",
                f"{desc} (Haiku enhanced prompt)",
                meta.format(method="Claude Haiku (enhanced prompt)"),
                prompt_template=PROMPT_ENHANCED,
            )
            dt = time.time() - t0
            with open(path_enhanced, "w") as f:
                f.write(html_enhanced)
            print(f"  ✓ haiku-enhanced.html ({dt:.1f}s, {usage['input_tokens']}in/{usage['output_tokens']}out, ${usage['cost_usd']:.4f})")
        except Exception as e:
            print(f"  ✗ haiku-enhanced.html FAILED: {e}")

    # Cost summary
    print(f"\n{'='*60}")
    print(f"COST SUMMARY (10 articles, 2 API calls each)")
    print(f"  Total API calls: {cost_tracker['calls']}")
    print(f"  Total input tokens: {cost_tracker['input_tokens']:,}")
    print(f"  Total output tokens: {cost_tracker['output_tokens']:,}")
    print(f"  Total cost: ${cost_tracker['cost_usd']:.4f}")
    avg_cost = cost_tracker['cost_usd'] / max(cost_tracker['calls'], 1)
    print(f"  Avg cost per API call: ${avg_cost:.4f}")
    avg_per_article = cost_tracker['cost_usd'] / max(total, 1)
    print(f"  Avg cost per article (2 calls): ${avg_per_article:.4f}")
    print(f"  Estimated cost for 1,356 articles (standard prompt): ${avg_per_article/2 * 1356:.2f}")
    print(f"  Estimated cost for 1,356 articles (enhanced prompt): ${avg_per_article/2 * 1356:.2f}")

    print(f"\nOutput: {OUTPUT_DIR}/")
    files = sorted(os.listdir(OUTPUT_DIR))
    print(f"Files: {len(files)}")
    for f in files:
        size = os.path.getsize(os.path.join(OUTPUT_DIR, f))
        print(f"  {f} ({size:,} bytes)")


if __name__ == "__main__":
    main()
