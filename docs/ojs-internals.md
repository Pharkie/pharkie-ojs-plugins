# OJS Internals Reference

Research notes on OJS's native API, database schema, and PHP internals. These informed the design of the `wpojs-subscription-api` plugin but are not needed to use it — see [ojs-sync-plugin-api.md](ojs-sync-plugin-api.md) for the plugin's API reference.

## Native OJS REST API (stock, pre-plugin)

The native OJS REST API (documented in [swagger-source.json](https://github.com/pkp/ojs/blob/main/docs/dev/swagger-source.json)) covers 37 endpoint categories. **Subscriptions are not among them** — that's why the custom plugin was necessary.

### Native user endpoints

| Method | Endpoint | Notes |
|---|---|---|
| `GET` | `/api/v1/users` | List/search users. Supports `searchPhrase` param (searches name + email) |
| `GET` | `/api/v1/users/{userId}` | Get single user |
| `PUT` | `/api/v1/users/{userId}/endRole/{userGroupId}` | End a role assignment |

Minimum role: Journal Manager, Editor, or Subeditor.

### What else the native API covers

Submissions, issues, contexts, DOIs, email templates, announcements, stats, institutions, sections, categories, highlights, navigation menus, user groups. No subscriptions, no user creation, no passwords.

## Native OJS Authentication

| Method | Details |
|---|---|
| **Bearer token** | `Authorization: Bearer <token>` header |
| **Query param** | `?apiToken=<token>` (fallback for Apache header stripping) |
| **Session cookie** | Works same-domain only, requires CSRF token for writes |

### Setup requirements
- `api_key_secret` must be set in `config.inc.php` `[security]` section
- Token generated per-user: User Profile > API Key > Enable > Save
- Full control requires Site Administrator role
- **Apache + PHP-FPM:** Add `CGIPassAuth on` to `.htaccess` or the Authorization header gets stripped silently

### Known issues
- If Authorization header contains an invalid JWT, some versions throw a fatal PHP error instead of a 401 ([pkp-lib#6563](https://github.com/pkp/pkp-lib/issues/6563))
- Slim Application errors reported on some 3.4 deployments

## Author Database Schema

### No central author entity

OJS stores authors **per-article, not centrally**. The `authors` table has one row per author per publication — if "Emmy van Deurzen" has 20 articles, that's 20 separate rows with different `author_id` values, each linked to one `publication_id`. There is no shared author profile or deduplication.

```
publications (1) ──< (many) authors
```

**`authors`**
| Column | Type | Notes |
|---|---|---|
| `author_id` | BIGINT PK | Auto-increment, unique per row |
| `email` | VARCHAR(90) | Stored per-row, not deduplicated |
| `publication_id` | BIGINT | FK to publications — one article |
| `include_in_browse` | SMALLINT | Default 1 |
| `seq` | DOUBLE | Display order within article |
| `user_group_id` | BIGINT | |

Author names are stored in `author_settings` (locale-specific `givenname`, `familyname`).

### Consequences

- **Author browse/search matches by exact string.** "Emmy van Deurzen" and "E. van Deurzen" appear as different authors. There is no fuzzy matching or alias support.
- **No "other articles by this author" linking** beyond string matching. No ORCID-based linking in core (plugin exists but is optional).
- **Backfill implication:** author name consistency across 30 years of issues depends entirely on us normalising names before import. A dedicated author registry (`authors.json`) maps every extracted name variant to a canonical form. This is more robust than anything OJS offers natively.

## Subscription Database Schema

### Tables

**`subscriptions`** (core)
| Column | Type | Notes |
|---|---|---|
| `subscription_id` | BIGINT PK | Auto-increment |
| `journal_id` | BIGINT | FK to journals |
| `user_id` | BIGINT | FK to users |
| `type_id` | BIGINT | FK to subscription_types |
| `date_start` | DATE | |
| `date_end` | TIMESTAMP | Null if non-expiring |
| `status` | TINYINT | Default 1 (active) |
| `membership` | VARCHAR(40) | External membership ID |
| `reference_number` | VARCHAR(40) | External reference |
| `notes` | TEXT | Admin notes |

**`subscription_types`**
| Column | Type | Notes |
|---|---|---|
| `type_id` | BIGINT PK | Auto-increment |
| `journal_id` | BIGINT | |
| `cost` | FLOAT | Price |
| `currency_code_alpha` | VARCHAR(3) | ISO 4217 (e.g. GBP) |
| `non_expiring` | TINYINT | 0 or 1 |
| `duration` | SMALLINT | Months |
| `format` | SMALLINT | 1=online, 16=print, 17=both |
| `institutional` | TINYINT | 0=individual, 1=institutional |
| `membership` | TINYINT | Requires membership ID? |
| `disable_public_display` | TINYINT | |
| `seq` | FLOAT | Display order |

**`subscription_type_settings`** — localized name/description per type

**`institutional_subscriptions`** — extra fields: `institution_name`, `mailing_address`, `domain`

**`institutional_subscription_ip`** — IP ranges: `ip_string`, `ip_start`, `ip_end`

### Status constants

```
1 = SUBSCRIPTION_STATUS_ACTIVE
2 = SUBSCRIPTION_STATUS_NEEDS_INFORMATION
3 = SUBSCRIPTION_STATUS_NEEDS_APPROVAL
4 = SUBSCRIPTION_STATUS_AWAITING_MANUAL_PAYMENT
5 = SUBSCRIPTION_STATUS_AWAITING_ONLINE_PAYMENT
16 = SUBSCRIPTION_STATUS_OTHER
```

### Relationships

```
subscription_types (1) ──< (many) subscriptions
users (1) ──< (many) subscriptions
subscriptions (1) ──< (0..1) institutional_subscriptions
institutional_subscriptions (1) ──< (many) institutional_subscription_ip
```

## Internal PHP Classes

### User management (pkp-lib)

OJS 3.5 uses the Repository pattern for users. All accessible from plugin context via `APP\facades\Repo`.

**Finding user:**
```php
use APP\facades\Repo;

$user = Repo::user()->getByEmail('member@example.com');       // returns ?User, skips disabled
$user = Repo::user()->getByEmail('member@example.com', true); // includes disabled accounts
$user = Repo::user()->get($userId);                            // by ID
```

**Creating user programmatically:**
```php
use APP\facades\Repo;
use PKP\security\Validation;
use PKP\core\Core;

$user = Repo::user()->newDataObject();
$user->setUsername(Validation::suggestUsername($firstName, $lastName)); // generates unique username
$user->setEmail('member@example.com');
$user->setGivenName('Jane', 'en');     // locale required
$user->setFamilyName('Smith', 'en');
$user->setPassword(Validation::encryptCredentials($username, bin2hex(random_bytes(16)))); // random password
$user->setDateRegistered(Core::getCurrentDate());
$user->setDateValidated(Core::getCurrentDate()); // marks email as verified — MUST set or OJS treats as unverified
$user->setMustChangePassword(true);              // force password set on first login
$user->setDisabled(false);

$userId = Repo::user()->add($user); // fires User::add hook

// Assign Reader role:
$userGroup = Repo::userGroup()->getByRoleIds([Role::ROLE_ID_READER], $contextId)->first();
Repo::userGroup()->assignUserToGroup(userId: $userId, userGroupId: $userGroup->getId());
```

Required fields: `userName`, `email`, `dateRegistered`. Strongly recommended: `givenName`, `familyName`, `dateValidated`, `password`.

**Updating user email:**
```php
$user = Repo::user()->getByEmail('old@example.com', true);
Repo::user()->edit($user, ['email' => 'new@example.com']);
```

**Caveat:** OJS does not enforce email uniqueness in `edit()` — the plugin must check for duplicates before calling this.

**Deleting / GDPR erasure:**

OJS has no native anonymise method. Options:
- `Repo::user()->delete($user)` — hard delete, but orphans submission records. Only safe for accounts with no submission history.
- `APP\user\Repository::mergeUsers($oldId, $ghostId)` — transfers all assets to a ghost account, then deletes the old user. The OJS-native approach.
- Blank PII via `edit()` — set name/email to anonymised values. Simplest for our case (sync-created accounts have no submission history).

**Password reset token:**
```php
use PKP\security\Validation;

$hash = Validation::generatePasswordResetHash($userId); // HMAC-SHA256, expiry from config
// Default expiry: 7 days. Controlled by `password_reset_timeout` in the `[security]` section of config.inc.php (value in days).
// Note: not used by our plugin — WP password hashes are synced directly, so members don't need password reset.
```

Token auto-invalidates if user logs in (hash includes `dateLastLogin`).

**Sending password reset email:**
```php
use PKP\mail\mailables\PasswordResetRequested;
use Illuminate\Support\Facades\Mail;

$template = Repo::emailTemplate()->getByKey($contextId, PasswordResetRequested::getEmailTemplateKey());
$mailable = (new PasswordResetRequested($site))
    ->recipients($user)    // internally generates reset token + URL
    ->from($site->getLocalizedContactEmail(), $site->getLocalizedContactName())
    ->body($template->getLocalizedData('body'))
    ->subject($template->getLocalizedData('subject'));
Mail::send($mailable);
```

The `PasswordResetRequested` mailable uses the `PASSWORD_RESET_CONFIRM` email template and injects `{$passwordResetUrl}` automatically.

### Subscription management (OJS-specific)

All in `/classes/subscription/`. Uses DAO pattern (not Repository/Service).

### DAOs (full CRUD)

**`IndividualSubscriptionDAO`**
- `insertObject($subscription)` — create
- `updateObject($subscription)` — update
- `getById($subscriptionId)` — read
- `getByUserIdForJournal($userId, $journalId)` — find by user
- `deleteById($subscriptionId)` — delete
- `renewSubscription($subscription)` — extend dates
- `getByDateEnd($dateEnd, $journalId)` — find expiring subs

**`InstitutionalSubscriptionDAO`** — same pattern + `isValidInstitutionalSubscription()` (domain/IP validation)

**`SubscriptionTypeDAO`** — CRUD for subscription type configuration

### Creating a subscription programmatically

```php
use APP\subscription\IndividualSubscription;
use PKP\db\DAORegistry;

$dao = DAORegistry::getDAO('IndividualSubscriptionDAO');

$sub = new IndividualSubscription();
$sub->setJournalId($journalId);
$sub->setUserId($userId);
$sub->setTypeId($typeId);
$sub->setStatus(1); // ACTIVE
$sub->setDateStart('2026-01-01');
$sub->setDateEnd('2026-12-31');
$sub->setMembership('EXT-12345');     // optional
$sub->setReferenceNumber('REF-001'); // optional
$sub->setNotes('Synced from WP');    // optional

$dao->insertObject($sub);
```

## OJS 3.5 Plugin API Extensibility

OJS 3.5 restored plugin-registered API endpoints via [pkp-lib #9434](https://github.com/pkp/pkp-lib/issues/9434).

### Pattern for custom endpoints (3.5+)

```php
Hook::add('APIHandler::endpoints::plugin', function($hookName, $apiRouter) {
    $apiRouter->registerPluginApiControllers([
        new SubscriptionApiController(),
    ]);
    return Hook::CONTINUE;
});
```

Example plugin: [touhidurabir/apiExample](https://github.com/touhidurabir/apiExample)

### Breaking changes in 3.5
- Slim → Laravel routing (all plugins must use Laravel patterns)
- `.inc.php` suffixes no longer supported
- Non-namespaced plugins no longer supported
- Some DAOs replaced with Eloquent models (but subscription DAOs are unchanged)
- New `app_key` config required
- Vue 2 → Vue 3 in frontend

### OJS 3.4 plugin API
Much harder. No clean hook for registering API endpoints. Would need `LoadHandler` hook or page handler pattern.

## Key References

- [OJS swagger spec](https://github.com/pkp/ojs/blob/main/docs/dev/swagger-source.json)
- [OJS subscription classes](https://github.com/pkp/ojs/tree/main/classes/subscription)
- [OJS API v1 directory](https://github.com/pkp/ojs/tree/main/api/v1)
- [Subscription SSO plugin](https://github.com/asmecher/subscriptionSSO)
- [API Example plugin (3.5+)](https://github.com/touhidurabir/apiExample)
- [pkp-lib #9434 — plugin API extensibility](https://github.com/pkp/pkp-lib/issues/9434)
- [PKP forum — subscription API options](https://forum.pkp.sfu.ca/t/are-there-api-or-other-options-for-subscription-management-available-in-ojs-3-3/86106)
- [OJS 3.5 release notes](https://github.com/pkp/ojs/blob/stable-3_5_0/docs/release-notes/README-3.5.0)
- [OJS config template](https://github.com/pkp/ojs/blob/main/config.TEMPLATE.inc.php)
