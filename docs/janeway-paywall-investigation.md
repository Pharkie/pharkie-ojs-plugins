# Janeway Paywall Investigation: Concrete Technical Plan

Last updated: 2026-02-16

This is a deep technical investigation of what it would actually take to build a Stripe-powered paywall in Janeway for SEA's journal "Existential Analysis". Based on reading the actual Janeway source code, not documentation summaries.

---

## 1. How Janeway Currently Serves Articles (The Access Model You'd Be Fighting)

### The short version

Janeway has **zero access control on published content**. Every published article is fully open to every visitor. There is no paywall, no subscription check, no "is this user allowed to read this?" logic anywhere in the article-serving chain. The entire architecture assumes open access.

### The article-serving chain, traced through actual code

**Entry points** (from `src/journal/urls.py`):

| URL pattern | View function | What it does |
|---|---|---|
| `article/<type>/<id>/` | `views.article` | Renders article page with HTML galley inline |
| `article/<article_id>/galley/<galley_id>/download/` | `views.download_galley` | Streams galley file (PDF, XML, etc.) |
| `article/<article_id>/galley/<galley_id>/view/` | `views.view_galley` | Streams PDF to browser for in-page viewing |
| `article/<type>/<id>/download/pdf/` | `views.serve_article_pdf` | Serves first PDF galley |
| `article/<type>/<id>/download/xml/` | `views.serve_article_xml` | Serves XML galley |

**The `article()` view** (`src/journal/views.py`) has three decorators:

```python
@decorators.frontend_enabled
@article_exists
@article_stage_accepted_or_later_required
def article(request, identifier_type, identifier):
```

- `@frontend_enabled` -- checks the journal hasn't disabled its front-end entirely.
- `@article_exists` -- confirms the article exists in the database (404 otherwise).
- `@article_stage_accepted_or_later_required` -- confirms the article has been accepted/published. That is it. No user authentication check. No subscription check.

The view then fetches the article, gets the "best galley" (HTML preferred), renders its content inline into the template via `{{ article_content|safe }}`, and serves the page. Anyone can see it.

**The `download_galley()` view** is even simpler:

```python
def download_galley(request, article_id, galley_id):
    article = get_object_or_404(
        submission_models.Article,
        pk=article_id,
        journal=request.journal,
        date_published__lte=timezone.now(),
        stage__in=submission_models.PUBLISHED_STAGES,
    )
    galley = get_object_or_404(
        core_models.Galley,
        pk=galley_id,
        public=True,
    )
    # ... metrics logging ...
    return files.serve_file(request, galley.file, article, public=True)
```

No decorator at all (beyond what Django's URL routing provides). It checks the article is published and the galley is marked public -- then serves the file. The `public=True` parameter in `serve_file()` just controls the filename in the Content-Disposition header; it is not an access control flag.

**The file-serving layer** (`src/core/files.py`) has no access checks:

```python
def serve_file(request, file_to_serve, article, public=False, hide_name=False):
    path_parts = ("articles", article.pk)
    return serve_any_file(request, file_to_serve, public, hide_name=hide_name, path_parts=path_parts)

def serve_file_to_browser(file_path, file_to_serve, public=False, hide_name=False):
    # Opens the file, wraps it in StreamingHttpResponse, returns it.
    # Zero permission checks.
```

### What access control does exist?

The `security/decorators.py` module has role-based decorators, but they are all for **editorial workflow** (editors, reviewers, authors, typesetters), not for readers:

- `@editor_user_required` -- is this user an editor?
- `@reviewer_user_required` -- is this user assigned as a reviewer?
- `@file_user_required` -- can this user access this file in the editorial workflow?
- `@article_stage_accepted_or_later_required` -- has this article reached acceptance stage?

None of these ask "has this reader paid?" or "does this reader have a subscription?" Those concepts do not exist in Janeway's codebase.

### Models: No paywall fields anywhere

Checked all relevant models:

- **`Article`** (`src/submission/models.py`) -- no access control fields. Has `stage` (editorial workflow state), `date_published`, `peer_reviewed`. No `is_open_access`, `is_paywalled`, `requires_subscription` field.
- **`Galley`** (`src/core/models.py`) -- has a `public` boolean, but this controls whether a galley appears in the public listing. If `public=False`, it is hidden from the article page, but there is no per-user check.
- **`Journal`** (`src/journal/models.py`) -- has display toggles (`hide_from_press`, `disable_front_end`, navigation booleans). No subscription or paywall settings.
- **`Issue`** (`src/journal/models.py`) -- no access control fields at all.
- **`Account`** (user model in `src/core/models.py`) -- role-based access for editorial workflow only. No subscription/purchase history.

### Summary of what you are up against

You are not "enabling a disabled feature" or "configuring a setting". You are grafting a paywall onto a system that was designed, at every layer, to have no paywalls. Every article view, every file download, every galley render assumes the content is free. The word "subscription" does not appear in any model in the core codebase.

---

## 2. What the Paywall Django App Would Look Like

### Architecture decision: Plugin vs. core modification

Janeway has a plugin system. After studying the actual plugin loader (`src/core/plugin_loader.py`, `src/core/include_urls.py`) and a real example plugin (`openlibhums/apc`), here is what plugins can and cannot do:

**What plugins CAN do:**
- Add their own URL routes (automatically included under `/plugins/<name>/`)
- Register template hooks that inject HTML into specific hook points
- Register for events in the editorial workflow
- Add Django models (migrations, admin, etc.)
- Add management commands
- Add settings via the Janeway settings system

**What plugins CANNOT do:**
- Replace or wrap existing view functions
- Add decorators to existing views
- Modify URL patterns for existing routes
- Override the article-serving chain
- Intercept file downloads before they're served

This is the critical problem. The template hook system lets you inject HTML (e.g., a "Buy this article" button in the sidebar), but it cannot prevent content from being rendered. The `article()` view will still render `{{ article_content|safe }}` -- the full article text inline in the HTML -- regardless of any plugin.

### The hard truth: You need to modify core views

A pure plugin cannot implement a paywall. You must modify:

1. **`src/journal/views.py`** -- the `article()`, `download_galley()`, `view_galley()`, `serve_article_pdf()`, and `serve_article_xml()` functions. Each needs a check: "does this user have access to this article?"
2. **The article template** (in your theme) -- to not render `{{ article_content|safe }}` when the user hasn't paid.

This is what makes the paywall a permanent fork. These are core Janeway files that change with every release. You'll be merging upstream changes forever.

### Recommended hybrid approach: Minimal core patches + plugin for everything else

Minimize the fork surface by keeping as much as possible in a plugin, and making the smallest possible changes to core code.

**Core modifications (the permanent fork):**

1. Add a single access-check function call in each article-serving view
2. Add a template context variable (`has_access`) to the article template context
3. Wrap `{{ article_content|safe }}` in an `{% if has_access %}` block in the theme template

**Plugin handles everything else:**

- Purchase/subscription models
- Stripe integration
- Purchase flow views
- Paywall page template
- WP membership sync API endpoint
- Admin management views

### Models

```python
# plugins/sea_paywall/models.py

from django.db import models
from core.models import Account
from submission.models import Article
from journal.models import Journal, Issue

class Purchase(models.Model):
    """Records a completed purchase of an article or issue."""
    PURCHASE_TYPES = [
        ('article', 'Single Article'),
        ('current_issue', 'Current Issue'),
        ('back_issue', 'Back Issue'),
    ]
    user = models.ForeignKey(Account, on_delete=models.CASCADE, null=True, blank=True)
    email = models.EmailField()  # For guest purchases (no account)
    journal = models.ForeignKey(Journal, on_delete=models.CASCADE)
    article = models.ForeignKey(Article, on_delete=models.CASCADE, null=True, blank=True)
    issue = models.ForeignKey(Issue, on_delete=models.CASCADE, null=True, blank=True)
    purchase_type = models.CharField(max_length=20, choices=PURCHASE_TYPES)
    stripe_payment_intent_id = models.CharField(max_length=255, unique=True)
    stripe_checkout_session_id = models.CharField(max_length=255, unique=True)
    amount_pence = models.IntegerField()  # Amount in pence
    currency = models.CharField(max_length=3, default='gbp')
    completed = models.BooleanField(default=False)
    created = models.DateTimeField(auto_now_add=True)
    refunded = models.BooleanField(default=False)

    class Meta:
        indexes = [
            models.Index(fields=['user', 'article']),
            models.Index(fields=['user', 'issue']),
            models.Index(fields=['email', 'article']),
            models.Index(fields=['stripe_checkout_session_id']),
        ]

class MemberAccess(models.Model):
    """
    Records that a user has member access via WP sync.
    Separate from Purchase because members don't pay per-article.
    """
    user = models.ForeignKey(Account, on_delete=models.CASCADE)
    journal = models.ForeignKey(Journal, on_delete=models.CASCADE)
    granted_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    wp_membership_id = models.CharField(max_length=255, blank=True)
    active = models.BooleanField(default=True)

    class Meta:
        unique_together = ('user', 'journal')

class PaywallConfiguration(models.Model):
    """Per-journal paywall settings."""
    journal = models.OneToOneField(Journal, on_delete=models.CASCADE)
    enabled = models.BooleanField(default=False)
    article_price_pence = models.IntegerField(default=300)       # GBP3
    current_issue_price_pence = models.IntegerField(default=2500) # GBP25
    back_issue_price_pence = models.IntegerField(default=1800)    # GBP18
    stripe_publishable_key = models.CharField(max_length=255)
    stripe_secret_key = models.CharField(max_length=255)
    stripe_webhook_secret = models.CharField(max_length=255)
    # Number of months before an issue is considered a "back issue"
    back_issue_months = models.IntegerField(default=3)
    # Optional: make articles older than N months open access
    open_after_months = models.IntegerField(null=True, blank=True)
```

### The access-check function

This is the heart of the system. It lives in the plugin but gets called from patched core views:

```python
# plugins/sea_paywall/access.py

from django.utils import timezone
from plugins.sea_paywall.models import Purchase, MemberAccess, PaywallConfiguration

def user_has_access(request, article):
    """
    Check if the current user/session has access to this article.
    Returns True if:
    - Paywall is disabled for this journal
    - Article is in an open-access section or old enough to be open
    - User is staff/editor
    - User has an active member access record
    - User (or session) has purchased this specific article
    - User (or session) has purchased the issue containing this article
    """
    journal = request.journal

    # Check if paywall is even enabled
    try:
        config = PaywallConfiguration.objects.get(journal=journal)
        if not config.enabled:
            return True
    except PaywallConfiguration.DoesNotExist:
        return True  # No config = no paywall

    # Staff and editors always have access
    if request.user.is_authenticated:
        if request.user.is_staff or request.user.is_superuser:
            return True
        if hasattr(request.user, 'check_role'):
            if request.user.check_role(journal, 'editor'):
                return True

    # Check if article is old enough to be open access
    if config.open_after_months and article.date_published:
        from dateutil.relativedelta import relativedelta
        cutoff = timezone.now() - relativedelta(months=config.open_after_months)
        if article.date_published < cutoff:
            return True

    if request.user.is_authenticated:
        # Check member access
        if MemberAccess.objects.filter(
            user=request.user,
            journal=journal,
            active=True,
            expires_at__gt=timezone.now(),
        ).exists():
            return True

        # Check article purchase
        if Purchase.objects.filter(
            user=request.user,
            article=article,
            completed=True,
            refunded=False,
        ).exists():
            return True

        # Check issue purchase (any issue containing this article)
        article_issues = article.issues.all()
        if Purchase.objects.filter(
            user=request.user,
            issue__in=article_issues,
            completed=True,
            refunded=False,
        ).exists():
            return True

    # Check session-based purchase (for guest purchases)
    session_purchases = request.session.get('sea_purchases', [])
    if session_purchases:
        if Purchase.objects.filter(
            stripe_checkout_session_id__in=session_purchases,
            article=article,
            completed=True,
            refunded=False,
        ).exists():
            return True
        article_issues = article.issues.all()
        if Purchase.objects.filter(
            stripe_checkout_session_id__in=session_purchases,
            issue__in=article_issues,
            completed=True,
            refunded=False,
        ).exists():
            return True

    return False
```

### Core view patches (the fork)

The modifications to `src/journal/views.py` are small but unavoidable:

```python
# In the article() view, ADD before the return statement:

    # --- SEA PAYWALL PATCH START ---
    has_access = True
    try:
        from plugins.sea_paywall.access import user_has_access
        has_access = user_has_access(request, article_object)
    except ImportError:
        pass  # Plugin not installed = open access
    context['has_access'] = has_access
    if not has_access:
        context['article_content'] = None  # Don't send full text to template
        context['galleys'] = article_object.galley_set.none()  # Hide download links
    # --- SEA PAYWALL PATCH END ---
```

For `download_galley()`, `view_galley()`, `serve_article_pdf()`, `serve_article_xml()`:

```python
# ADD at the top of each function, after the article is fetched:

    # --- SEA PAYWALL PATCH START ---
    try:
        from plugins.sea_paywall.access import user_has_access
        if not user_has_access(request, article):
            from django.http import HttpResponseForbidden
            return HttpResponseForbidden("Purchase required")
    except ImportError:
        pass
    # --- SEA PAYWALL PATCH END ---
```

Total lines changed in core: approximately 30 lines across 5 functions in one file. Small, but permanent.

### Theme template patch

In your theme's `templates/journal/article.html`:

```html
{# Replace the article content rendering section #}

{% if has_access %}
    {% if article_content %}
        <article id="main_article">
            <div itemprop="articleBody">
                {{ article_content|safe }}
                <div id="article_footer_block">
                    {% hook 'article_footer_block' %}
                </div>
            </div>
        </article>
    {% endif %}
{% else %}
    {# Paywall message - this can also be injected via the hook system #}
    <div class="sea-paywall-notice">
        <h2>This article requires purchase or membership</h2>
        <p>{{ article.abstract|safe }}</p>
        {% include "sea_paywall/purchase_options.html" %}
    </div>
{% endif %}
```

The galley download links in the sidebar also need wrapping with `{% if has_access %}`. This affects two locations in the OLH template (one for mobile, one for desktop sidebar) and similar locations in clean/material themes.

---

## 3. Stripe Integration Specifics

### Does Janeway have any existing payment integration?

**No.** There is zero payment processing code in Janeway core. The closest things are:

- **`openlibhums/apc`** -- Article Publication Charges plugin. Tracks fees but does NOT process payments. Staff manually mark invoices as paid.
- **`openlibhums/consortial_billing`** -- Manages institutional supporter fees. Also no payment processing.

Neither uses Stripe, PayPal, or any payment gateway.

### Library choice: `stripe` (direct) vs `dj-stripe`

**Recommendation: Use the `stripe` Python library directly.** Do not use `dj-stripe`.

Reasoning:
- SEA's needs are simple: one-time purchases only, three price points, no recurring subscriptions.
- `dj-stripe` syncs all Stripe objects into Django models (products, prices, customers, subscriptions, invoices, etc.). This is massive overkill for one-time payments and adds 40+ database tables.
- `dj-stripe` requires Django 3.2+ and has its own upgrade cadence that could conflict with Janeway's Django version.
- Direct `stripe` library is a single dependency, well-documented, and gives you exactly the control you need.

**Dependencies:**
```
stripe>=7.0.0
```

That is it. One library.

### Stripe products and prices

Create in the Stripe Dashboard (or via API during setup):

```
Product: "Existential Analysis - Single Article"
  Price: GBP 3.00, one-time

Product: "Existential Analysis - Current Issue"
  Price: GBP 25.00, one-time

Product: "Existential Analysis - Back Issue"
  Price: GBP 18.00, one-time
```

These can be Stripe "prices" with `type: one_time`. The article/issue metadata gets passed via `metadata` on the Checkout Session, not encoded in the price.

### Checkout flow: Stripe Checkout (redirect)

**Use Stripe Checkout (hosted page), not embedded.** Reasons:

- No PCI compliance burden -- Stripe handles the payment form entirely
- Mobile-optimized out of the box
- Supports Apple Pay, Google Pay, Link automatically
- Less code to write and maintain
- The redirect flow is standard for academic content purchases

**Purchase flow:**

```
User views article page
  -> Sees abstract, title, metadata (but not full text or download links)
  -> Sees purchase options: "Buy article GBP3" / "Buy this issue GBP25" / "Buy back issue GBP18"
  -> Clicks a purchase button

Plugin view creates Stripe Checkout Session:
  -> mode='payment'
  -> line_items with the appropriate price
  -> metadata: { article_id, issue_id, purchase_type, journal_id }
  -> success_url = /plugins/sea-paywall/success/?session_id={CHECKOUT_SESSION_ID}
  -> cancel_url = article URL
  -> Redirects user to Stripe Checkout page

User completes payment on Stripe
  -> Stripe redirects to success_url

Plugin success view:
  -> Retrieves Checkout Session from Stripe API
  -> Verifies payment_status == 'paid'
  -> Creates Purchase record
  -> Stores session_id in user's Django session (for guest access)
  -> Redirects to the article page (which now grants access)

Stripe webhook (async, backup):
  -> checkout.session.completed event
  -> Creates/confirms Purchase record
  -> Handles edge cases where user closes browser before redirect
```

### Plugin views

```python
# plugins/sea_paywall/views.py

import stripe
from django.shortcuts import redirect, get_object_or_404
from django.http import HttpResponse, JsonResponse, HttpResponseBadRequest
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from submission.models import Article
from journal.models import Issue
from plugins.sea_paywall.models import Purchase, PaywallConfiguration


def create_checkout_session(request):
    """Create a Stripe Checkout Session for an article or issue purchase."""
    config = get_object_or_404(PaywallConfiguration, journal=request.journal)
    stripe.api_key = config.stripe_secret_key

    purchase_type = request.POST.get('purchase_type')
    article_id = request.POST.get('article_id')
    issue_id = request.POST.get('issue_id')

    if purchase_type == 'article':
        article = get_object_or_404(Article, pk=article_id)
        amount = config.article_price_pence
        description = f"Article: {article.title[:200]}"
        metadata = {'article_id': article_id, 'purchase_type': 'article', 'journal_id': request.journal.pk}
    elif purchase_type == 'current_issue':
        issue = get_object_or_404(Issue, pk=issue_id)
        amount = config.current_issue_price_pence
        description = f"Issue: {issue}"
        metadata = {'issue_id': issue_id, 'purchase_type': 'current_issue', 'journal_id': request.journal.pk}
    elif purchase_type == 'back_issue':
        issue = get_object_or_404(Issue, pk=issue_id)
        amount = config.back_issue_price_pence
        description = f"Back Issue: {issue}"
        metadata = {'issue_id': issue_id, 'purchase_type': 'back_issue', 'journal_id': request.journal.pk}
    else:
        return HttpResponseBadRequest("Invalid purchase type")

    session = stripe.checkout.Session.create(
        payment_method_types=['card'],
        mode='payment',
        line_items=[{
            'price_data': {
                'currency': 'gbp',
                'unit_amount': amount,
                'product_data': {
                    'name': description,
                },
            },
            'quantity': 1,
        }],
        metadata=metadata,
        success_url=request.build_absolute_uri(f'/plugins/sea-paywall/success/?session_id={{CHECKOUT_SESSION_ID}}'),
        cancel_url=request.META.get('HTTP_REFERER', '/'),
        customer_email=request.user.email if request.user.is_authenticated else None,
    )

    return redirect(session.url)


def payment_success(request):
    """Handle successful payment redirect from Stripe."""
    session_id = request.GET.get('session_id')
    if not session_id:
        return HttpResponseBadRequest("Missing session ID")

    config = get_object_or_404(PaywallConfiguration, journal=request.journal)
    stripe.api_key = config.stripe_secret_key

    session = stripe.checkout.Session.retrieve(session_id)

    if session.payment_status != 'paid':
        # Payment not yet confirmed; webhook will handle it
        # Show a "processing" page
        return render(request, 'sea_paywall/processing.html')

    purchase = _create_purchase_from_session(session, request)

    # Store in session for guest access
    if 'sea_purchases' not in request.session:
        request.session['sea_purchases'] = []
    request.session['sea_purchases'].append(session_id)
    request.session.modified = True

    # Redirect to the purchased content
    if purchase.article:
        return redirect(purchase.article.local_url)
    elif purchase.issue:
        return redirect(purchase.issue.get_absolute_url())
    return redirect('/')


@csrf_exempt
@require_POST
def stripe_webhook(request):
    """Handle Stripe webhook events."""
    payload = request.body
    sig_header = request.META.get('HTTP_STRIPE_SIGNATURE')

    config = get_object_or_404(PaywallConfiguration, journal=request.journal)
    stripe.api_key = config.stripe_secret_key

    try:
        event = stripe.Webhook.construct_event(
            payload, sig_header, config.stripe_webhook_secret
        )
    except (ValueError, stripe.error.SignatureVerificationError):
        return HttpResponse(status=400)

    if event['type'] == 'checkout.session.completed':
        session = event['data']['object']
        _create_purchase_from_session(session, request)

    elif event['type'] == 'charge.refunded':
        charge = event['data']['object']
        payment_intent_id = charge['payment_intent']
        Purchase.objects.filter(
            stripe_payment_intent_id=payment_intent_id
        ).update(refunded=True)

    return HttpResponse(status=200)


def _create_purchase_from_session(session, request):
    """Create or retrieve a Purchase from a Stripe Checkout Session."""
    purchase, created = Purchase.objects.get_or_create(
        stripe_checkout_session_id=session.id,
        defaults={
            'stripe_payment_intent_id': session.payment_intent,
            'email': session.customer_details.email if session.customer_details else '',
            'user': request.user if request.user.is_authenticated else None,
            'journal_id': session.metadata.get('journal_id'),
            'article_id': session.metadata.get('article_id'),
            'issue_id': session.metadata.get('issue_id'),
            'purchase_type': session.metadata.get('purchase_type'),
            'amount_pence': session.amount_total,
            'currency': session.currency,
            'completed': True,
        }
    )
    return purchase
```

### Webhook handling

The webhook endpoint is critical for reliability. It handles:

1. **`checkout.session.completed`** -- Confirms the purchase even if the user closed the browser before reaching the success URL
2. **`charge.refunded`** -- Revokes access when SEA issues a refund through the Stripe Dashboard

The webhook URL would be registered in the Stripe Dashboard as:
```
https://journal.existentialanalysis.org.uk/plugins/sea-paywall/webhook/
```

Stripe webhook signature verification is handled by the `stripe` library via `stripe.Webhook.construct_event()`.

### What about refunds?

Refunds are handled passively: if SEA issues a refund through the Stripe Dashboard, the webhook marks the `Purchase` as `refunded=True`. The `user_has_access()` function already filters out refunded purchases. No custom refund UI is needed -- Stripe's Dashboard handles it.

---

## 4. WP Membership Sync (Separate from Stripe)

Members get access via a sync from WordPress, not via Stripe. This is the same concept as the OJS Push-sync plan, but hitting a Janeway API endpoint instead.

```python
# plugins/sea_paywall/api_views.py

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from rest_framework.authentication import TokenAuthentication
from django.utils import timezone
from dateutil.relativedelta import relativedelta

from core.models import Account
from plugins.sea_paywall.models import MemberAccess


class MemberAccessAPIView(APIView):
    """
    API endpoint for WP to push membership changes.
    POST: Grant or update member access
    DELETE: Revoke member access
    """
    authentication_classes = [TokenAuthentication]
    permission_classes = [IsAuthenticated]

    def post(self, request):
        email = request.data.get('email')
        expires_at = request.data.get('expires_at')
        wp_membership_id = request.data.get('wp_membership_id', '')

        if not email or not expires_at:
            return Response({'error': 'email and expires_at required'}, status=400)

        user, _ = Account.objects.get_or_create(
            email=email,
            defaults={'username': email, 'is_active': True}
        )

        access, created = MemberAccess.objects.update_or_create(
            user=user,
            journal=request.journal,
            defaults={
                'expires_at': expires_at,
                'wp_membership_id': wp_membership_id,
                'active': True,
            }
        )

        return Response({'status': 'granted', 'created': created})

    def delete(self, request):
        email = request.data.get('email')
        if not email:
            return Response({'error': 'email required'}, status=400)

        MemberAccess.objects.filter(
            user__email=email,
            journal=request.journal,
        ).update(active=False)

        return Response({'status': 'revoked'})
```

This endpoint is called by the same WP plugin concept from the Push-sync architecture. The WP plugin implementation is identical regardless of whether the journal platform is OJS or Janeway.

---

## 5. Content Migration from OJS

### Existing tools

1. **OJS-Janeway plugin** ([openlibhums/ojs-janeway](https://github.com/openlibhums/ojs-janeway)) -- OJS plugin that exports articles in a custom JSON format. PHP, last updated May 2021. Exports published articles, articles in editing, and articles in review.

2. **Janeway OAI scraper** -- Management command `scrape_oai` that imports published articles from an OJS OAI-PMH feed. This is the recommended path.

3. **Janeway user importer** -- Management command `import_jms_users` for user account migration.

### What transfers cleanly

- **Article metadata**: titles, abstracts, authors, keywords, DOIs, dates, sections
- **Published article files**: PDFs, XML galleys (via OAI or direct file import)
- **Issue structure**: volume/issue numbering, issue dates
- **User accounts**: basic name/email (via import command)

### What does NOT transfer

- **Subscriptions and purchase history**: OJS subscriptions have no equivalent in Janeway. There is nothing to map to. Any existing purchase records are lost (or must be manually recreated as `Purchase` records in the new paywall plugin).
- **OJS user passwords**: Janeway uses Django's password hashing (PBKDF2 by default). OJS uses bcrypt or MD5. Users must reset their passwords.
- **Custom theme**: OJS themes (Smarty/PHP templates) have zero relation to Janeway themes (Django templates). The theme must be rebuilt from scratch using one of Janeway's three bundled themes as a base (OLH, clean, or material).
- **Editorial workflow state**: Articles in review, copyediting, or production stages need manual handling.
- **OJS plugin data**: Anything from OJS plugins (DOI registration status, usage statistics, etc.) does not transfer.

### Effort for 35+ years of back issues

The OAI scraper should handle bulk metadata import. The realistic workflow:

1. **Run OAI scrape** -- this gets article metadata and PDF URLs. **Estimated: 1-2 days** including troubleshooting. The scraper may not handle all edge cases with 35 years of content (missing metadata, inconsistent DOI formats, encoding issues).

2. **Verify PDF files** -- The OAI scraper may not download all PDFs correctly. Some back issues from the 1980s-90s may have been added to OJS in various ways. **Estimated: 2-3 days** of manual checking and re-uploading.

3. **Reconstruct issue structure** -- The scraper creates articles but may not correctly assign them to issues/volumes. **Estimated: 1-2 days** of manual issue creation and article assignment in Janeway admin.

4. **DOI verification** -- Ensure all existing DOIs are correctly imported and resolve properly. **Estimated: 1 day.**

5. **Total content migration estimate: 5-8 working days** with significant manual verification. This is the optimistic case assuming the OAI scraper basically works. If it does not, you are looking at CSV-based manual import, which could be 2-3 weeks.

---

## 6. Maintenance Burden

### How coupled is the paywall to Janeway core?

**The coupling is in exactly 6 places:**

1. `src/journal/views.py` -- `article()` view (patched)
2. `src/journal/views.py` -- `download_galley()` view (patched)
3. `src/journal/views.py` -- `view_galley()` view (patched)
4. `src/journal/views.py` -- `serve_article_pdf()` view (patched)
5. `src/journal/views.py` -- `serve_article_xml()` view (patched)
6. Theme template `templates/journal/article.html` (patched)

Everything else is in the plugin directory and does not conflict with upstream. The core patches are small (5-8 lines each, wrapped in try/except ImportError so they are safe even if the plugin is removed).

### What breaks when Janeway upgrades?

**On every Janeway upgrade, you must:**

1. Check if `src/journal/views.py` has changed. If any of the 5 patched functions have been modified upstream, you must re-apply the patches. Git merge will handle this automatically in most cases -- the patches are small and isolated. A real conflict only occurs if Janeway changes the return signature or control flow of these functions.

2. Check if the article template structure has changed. If Janeway modifies how `article_content` is rendered or where galley links appear, you update the template.

3. Check if `submission.models.Article`, `core.models.Galley`, `journal.models.Journal`, or `journal.models.Issue` have breaking changes to their fields or relationships.

4. Check if the plugin loader API has changed (unlikely -- it has been stable across 1.6, 1.7, 1.8).

### How often does Janeway release?

Based on the release history:

- **Major releases** (1.6, 1.7, 1.8): every 6-12 months. These include Django version bumps and potentially breaking changes. **These require careful merge testing.**
- **Patch releases** (1.7.1, 1.7.2, etc.): every 1-3 months. Usually bug fixes. **Low risk of conflicts.**

Janeway supports only one major version at a time. When 1.8 releases, 1.7 stops getting security patches. You must keep up.

### Breaking change history

| Release | Breaking change relevant to paywall |
|---|---|
| v1.6.0 | Django 3.x to 4.2 upgrade. URL patterns moved from `url()` to `path()`/`re_path()`. |
| v1.7.0 | Minimum Python 3.10+. Template tag changes. Memcached deprecated. |
| v1.8.0 | Typesetting plugin merged into core (irrelevant to paywall). |

The pattern: major releases change Django versions and refactor internal APIs. The `views.py` functions themselves have been relatively stable in structure.

### Is the plugin system stable enough?

Yes. The plugin loader mechanism (`core/plugin_loader.py`) has been consistent across at least 1.5-1.8. Plugin structure expectations (directory in `src/plugins/`, `plugin_settings.py` with `install()`, `hook_registry()`, `JANEWAY_VERSION`) have not changed. The `PLUGIN_HOOKS` template injection system has been stable.

The risk is not "Janeway breaks the plugin system." The risk is "Janeway changes the article-serving views that you've patched."

### Realistic annual maintenance

- **Per minor release** (4-5/year): 30 minutes to check for conflicts, apply patches if needed.
- **Per major release** (1/year): 2-4 hours to review changes, re-apply core patches, test article access control, test Stripe checkout flow.
- **Stripe API changes**: Stripe is backwards-compatible. The `stripe` Python library handles versioning. Minimal effort unless you need new features.
- **Total: approximately 1-2 days per year** for a developer who understands the fork.

---

## 7. Concrete Implementation Plan

### File-by-file list

**Plugin directory: `src/plugins/sea_paywall/`**

| File | Purpose | Effort |
|---|---|---|
| `__init__.py` | Empty | Trivial |
| `plugin_settings.py` | Plugin metadata, `install()`, `hook_registry()` | 1 hour |
| `models.py` | `Purchase`, `MemberAccess`, `PaywallConfiguration` | 2-3 hours |
| `access.py` | `user_has_access()` function | 2-3 hours |
| `views.py` | Checkout session creation, success handler, admin views | 4-6 hours |
| `api_views.py` | REST endpoint for WP membership sync | 2-3 hours |
| `hooks.py` | Template hook functions (sidebar purchase button, article paywall notice) | 2-3 hours |
| `urls.py` | Plugin URL routes | 30 minutes |
| `forms.py` | Admin configuration form | 1-2 hours |
| `admin.py` | Django admin registration for Purchase, MemberAccess models | 1 hour |
| `migrations/0001_initial.py` | Database migration | Auto-generated |
| `install/settings.json` | Default plugin settings | 30 minutes |
| `templates/sea_paywall/purchase_options.html` | Purchase buttons template | 1-2 hours |
| `templates/sea_paywall/processing.html` | Payment processing page | 30 minutes |
| `templates/sea_paywall/success.html` | Payment success confirmation | 30 minutes |
| `templates/sea_paywall/admin_index.html` | Admin dashboard | 2-3 hours |
| `static/sea_paywall/css/paywall.css` | Minimal styling for paywall elements | 1 hour |

**Core patches (the fork):**

| File | Change | Effort |
|---|---|---|
| `src/journal/views.py` | Add access check to 5 view functions (~30 lines total) | 2 hours |
| Theme `templates/journal/article.html` | Wrap content in `{% if has_access %}`, add paywall fallback | 2-3 hours |

**Not in Janeway (separate concern):**

| Component | Purpose | Effort |
|---|---|---|
| WP plugin (`sea-janeway-sync`) | Push membership changes to Janeway API | 2-3 days |

### Estimated total effort

| Component | Estimate |
|---|---|
| Plugin models, access logic, views | 3-4 days |
| Stripe integration (checkout, webhooks, refunds) | 2-3 days |
| Core view patches + template changes | 1-2 days |
| WP membership sync API endpoint | 1 day |
| Admin/management interface | 1-2 days |
| Testing (unit + integration + manual) | 2-3 days |
| Content migration from OJS | 5-8 days |
| Theme customisation (based on clean or material theme) | 3-5 days |
| **Total** | **18-28 working days (4-6 weeks)** |

### Dependencies

| Dependency | Notes |
|---|---|
| `stripe` Python library | Only new pip dependency |
| `python-dateutil` | Likely already installed (Django dependency) |
| Stripe account | Need: API keys, webhook endpoint configured, products/prices created |
| Janeway instance | Docker or native install on a server |
| PostgreSQL | Janeway's recommended database |
| Redis | For caching (Janeway 1.7+ replaces Memcached) |

### What to test

1. **Access control matrix**: Verify every combination of user state x content state:
   - Anonymous user + no purchase = paywall shown, no download links, no article content
   - Anonymous user + session-based purchase = full access
   - Logged-in member = full access
   - Logged-in non-member + no purchase = paywall shown
   - Logged-in non-member + article purchase = access to that article only
   - Logged-in non-member + issue purchase = access to all articles in that issue
   - Staff/editor = always full access
   - Refunded purchase = access revoked

2. **Stripe flow**: Complete purchase end-to-end in Stripe test mode. Verify webhook arrives. Verify refund revoking works.

3. **Edge cases**: User buys article, then same article appears in a new issue. User buys issue, then article is added to that issue later. Browser closed during Stripe redirect (webhook must still grant access).

4. **Upgrade resilience**: Apply a Janeway upgrade on a staging instance. Verify the core patches merge cleanly. Verify the plugin still loads.

### Deployment considerations

- **Stripe webhook URL must be publicly accessible** with valid HTTPS.
- **Stripe webhook signing secret** must be stored securely (not in database if possible -- environment variable or Django settings).
- **Session backend** matters for guest purchases. If using cookie sessions, the session data (and therefore guest purchase access) is lost when the user clears cookies. Database-backed sessions are more reliable.
- **CSRF exemption** is required for the Stripe webhook endpoint (Stripe cannot send Django CSRF tokens).
- **The plugin must be in `INSTALLED_APPS`** for migrations to run. Janeway's `plugin_installed_apps.load_plugin_apps()` handles this automatically for plugins in `src/plugins/`.

---

## 8. Honest Assessment

### What is straightforward Django work

- The Purchase and MemberAccess models: standard Django ORM.
- The Stripe Checkout integration: well-documented, many examples exist, the `stripe` library is mature.
- The REST API for WP sync: standard DRF, almost identical to what you would build for OJS Push-sync.
- The plugin structure: follows existing patterns (APC plugin is a good template).
- The webhook handling: standard Stripe webhook pattern with signature verification.

### What is painful

1. **The core view patches are the permanent wound.** Every Janeway upgrade requires checking five functions in `src/journal/views.py`. This is manageable but never goes away. You are signing up for this forever.

2. **Content migration is unpredictable.** The OAI scraper may or may not handle 35 years of back issues gracefully. The only way to know is to try it on a staging instance. Budget time for manual cleanup.

3. **Guest purchases (no account) are architecturally awkward.** Session-based access works but is fragile -- cleared cookies mean lost access. The alternative (requiring account creation before purchase) adds friction. Neither option is great.

4. **Theme rebuilding is real work.** SEA presumably has a customised OJS theme. Recreating that look in a Janeway theme (Foundation 6 CSS framework, Django templates) is several days of front-end work.

5. **The `download_galley()` view has no decorators.** It does its own inline `get_object_or_404` checks. Your paywall patch is a code block injected after the article lookup but before the file serving. This is less clean than wrapping with a decorator, but adding decorators would change the function signature and increase merge conflict risk.

6. **Testing the full access control matrix is tedious but critical.** A single missing check means an article is either wrongly paywalled (lost revenue) or wrongly open (leaked content). There are at least 5 URL entry points to an article's content, and each one must enforce the paywall.

### Comparison to the Push-sync OJS approach

The paywall plugin for Janeway is approximately the same total effort as "OJS 3.5 upgrade + two custom plugins" -- but the risks are different:

- **OJS path risk**: the 3.5 upgrade breaks things in unknown ways. If it goes badly, you have spent weeks and still do not have a working system.
- **Janeway path risk**: the content migration is tedious and the paywall fork is permanent. But the technical components (Django, Stripe, REST APIs) are well-understood and unlikely to produce surprises.

The Janeway path trades "unknown unknowns" (OJS upgrade) for "known knowns" (Django development + content migration).
