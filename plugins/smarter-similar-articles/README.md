# Smarter Similar Articles Plugin for OJS

Drop-in replacement for the stock [Similar Articles](https://github.com/pkp/ojs/tree/main/plugins/generic/recommendBySimilarity) plugin. Surfaces more relevant "related articles" by combining shared terminology with how closely two articles match in meaning — so the sidebar finds conceptually close papers, not just ones that happen to share common keywords. All the heavy lifting runs offline on a schedule, and the article page serves pre-computed suggestions instantly, no matter how big the journal grows.

## Requirements

- OJS 3.5+
- Python 3.12+ with `scikit-learn`, `sentence-transformers`, `beautifulsoup4`, `pymysql` (for the offline builder — see [`scripts/ojs/requirements.txt`](../../scripts/ojs/requirements.txt))
- A scheduler (GitHub Actions, cron, etc.) to run the builder periodically

## Documentation

See **[docs/smarter-similar-articles-plugin.md](../../docs/smarter-similar-articles-plugin.md)** for the full guide — architecture, installation, configuration, offline builder usage, scheduling, operations, rollback, and tunable constants.

## LLM Generated, Human Reviewed

This code was generated with Claude Code (Anthropic, Claude Opus 4.7). Development was overseen by the human author with attention to reliability and security. Architectural decisions, configuration choices, and development sessions were closely planned, directed and verified by the human author throughout. The code and test results were reviewed and tested by the human author beyond the LLM. Still, the code has had limited manual review, I encourage you to make your own checks and use this code at your own risk.

## License

PolyForm Noncommercial 1.0.0 — see [LICENSE.md](../../LICENSE.md).
