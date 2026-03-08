# Email Setup

Both OJS and WP need to send transactional emails (password resets, editorial notifications). Docker containers can't send email directly — you need an external SMTP relay.

Email is not required for the sync to work. Members log in with their WP password (synced via hash). Email is needed for OJS editorial workflows (password resets, reviewer notifications, etc.).

Hetzner blocks port 25 (outbound SMTP) by default, but port 587 (submission with TLS) works fine, which is what all transactional email services use.

## Recommended: Resend

[Resend](https://resend.com) — modern transactional email service, built on Amazon SES. 3,000 emails/month free (100/day). Clean API, good docs, supports standard SMTP so it works with OJS's built-in config without code changes.

Other options:

| Service | Free tier | Notes |
|---|---|---|
| **Postmark** | 100 emails/month | Best deliverability reputation |
| **Mailgun** | 1,000 emails/month (first 3 months) | Flexible, good logs |
| **Brevo** (ex-Sendinblue) | 300 emails/day | Generous free tier |
| **Amazon SES** | ~$0.10 per 1,000 | Cheapest at scale, more setup |

For ~700 members with occasional password resets and editorial notifications, any of these work.

## OJS email configuration

OJS SMTP is configured via `.env` — the plumbing is already in docker-compose.yml:

```
OJS_SMTP_ENABLED=On
OJS_SMTP_HOST=smtp.resend.com
OJS_SMTP_PORT=587
OJS_SMTP_AUTH=tls
OJS_SMTP_USER=resend
OJS_SMTP_PASSWORD=re_your_api_key_here
OJS_MAIL_FROM=journal@yourdomain.org
```

## WP email configuration

WP email is typically handled by an SMTP plugin (WP Mail SMTP, FluentSMTP, etc.) pointed at the same service. If the live WP already sends email via a relay, reuse those credentials.

## DNS records (required for deliverability)

Add these DNS records for your sending domain:

- **SPF** — `TXT` record authorizing the service to send on your behalf
- **DKIM** — `TXT` record for cryptographic email signing
- **DMARC** — `TXT` record with your policy (start with `p=none`)

Each service provides the exact records to add. Without these, emails land in spam.

## Testing email delivery

Test before going live:

1. Send a password reset to yourself — check it arrives, check spam score
2. Verify DKIM passes (Gmail: "Show original" → look for `dkim=pass`)
