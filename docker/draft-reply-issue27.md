# Draft reply for pkp/containers#27 (PDF search indexing)

Not yet posted — waiting for resolution on the AI-generated code question first.

## Key argument

OJS comments out the `pdftotext` line because it can't assume the binary is available on every PHP host. That's reasonable for a generic tarball distribution.

But the container *does* know — it installed `poppler-utils`. So the container should finish the job and uncomment the line, just like `pkp-pre-start` already overrides `restful_urls = On` and `enable_cdn = Off`.

This doesn't conflict with "keeping OJS code intact" — it supports it. OJS is correct to leave it commented out by default. The container's job is to configure OJS for the environment it provides.

There's no scenario where a user would want PDF search indexing *not* to work. It's not a preference — it's wiring up a dependency the image already ships.

## If he insists on env var approach

Offer to PR it either way, but note that an opt-in env var implies someone might want to opt out of working search, which doesn't make sense.
