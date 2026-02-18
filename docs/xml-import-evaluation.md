# XML Import Evaluation: Stopgap for Member Sync?

Last updated: 2026-02-18

**Context:** Developer proposed using OJS's built-in XML user import as a temporary workaround — export WP members to XML, import into OJS periodically — while the full push-sync system is built or a platform decision is made.

---

## What OJS XML import actually does

The **Users XML Plugin** (`Tools > Import/Export > Users XML Plugin`) ships with every OJS 3.x release including 3.4. It:

- Creates new user accounts in the OJS `users` table
- Assigns users to journal roles (Reader, Author, etc.)
- Skips existing users (matched by username or email) without updating their profile data
- Can optionally send a notification email to newly created users

It is available via the OJS admin UI or command line:
```
php tools/importExport.php UserImportExportPlugin import users.xml [journalPath] [adminUsername]
```

## What it does NOT do

**It does not create subscription records.** The XML import has no subscription-related fields in its schema ([pkp-users.xsd](https://github.com/pkp/pkp-lib/blob/main/plugins/importexport/users/pkp-users.xsd)). There is no separate subscription XML import in any OJS version. PKP has confirmed they have no plans to build one ([forum thread](https://forum.pkp.sfu.ca/t/ojs3-bulk-import-subscriptions/62294)).

This is the critical gap. The OJS paywall checks the `subscriptions` table, not user roles. A user with a Reader role but no subscription record is still blocked from paywalled content.

## What a working stopgap would actually require

For each sync cycle:

1. **Export active members from WooCommerce** — needs a WP export plugin to produce the right format
2. **Transform to OJS XML format** — must include username, email, name, password handling, role assignments, and correct locale attributes
3. **Import XML into OJS** — creates user accounts (or skips existing ones)
4. **Separately create subscription records** for every imported user — no XML or bulk UI exists for this

Step 4 is the problem. Options:

| Approach | Viability |
|---|---|
| Manual creation in OJS admin UI | Impractical for ~500 users. Ongoing maintenance burden. |
| Direct SQL INSERT into `subscriptions` table | Bypasses OJS validation. Fragile. Needs OJS `user_id` from step 3. |
| PHP script using OJS `IndividualSubscriptionDAO` | Viable but you're essentially writing a mini version of the OJS plugin. |

For ongoing sync, you'd also need to:

- **Expire subscriptions** when members lapse — no XML or bulk method exists
- **Handle email changes** — XML import skips existing users entirely (no profile updates)
- **Detect new vs. lapsed members** — requires comparing WP state against OJS state each time

## Comparison

| Concern | XML import stopgap | Push-sync (the plan) |
|---|---|---|
| Creates OJS user accounts | Yes (XML import) | Yes (plugin endpoint) |
| Creates subscriptions | No — must be done separately | Yes (plugin endpoint) |
| Expires lapsed members | No mechanism | Yes (automated via WCS hooks) |
| Handles email changes | No (skips existing users) | Yes (email change hook) |
| Ongoing sync after launch | Manual re-export + re-import + manual subscription work | Automatic on WCS events |
| Dev effort for first run | Medium (XML transform + subscription workaround) | Higher (two plugins) |
| Dev effort for ongoing runs | High (repeated manual process, no expiry) | Near-zero (automated) |

## Assessment

The XML import solves only the easiest part of the problem (user account creation) and leaves the hardest part unaddressed (subscription records, expiry, ongoing sync).

Even as a stopgap, you'd need a separate mechanism for subscriptions — most likely a PHP script using OJS's `IndividualSubscriptionDAO`. At that point you're writing a throwaway version of part of the OJS plugin, plus accepting that expiry and ongoing sync remain manual. The effort may not save much compared to building the real solution.

## Key question to resolve

Does the developer have a plan for step 4 (subscription creation)? If they're assuming the XML import handles subscriptions, that assumption is incorrect and should be flagged before investing time in the WP export plugin.

## Sources

- [OJS XML import sample file](https://github.com/pkp/ojs/blob/main/plugins/importexport/users/sample.xml)
- [OJS XML import schema (pkp-users.xsd)](https://github.com/pkp/pkp-lib/blob/main/plugins/importexport/users/pkp-users.xsd)
- [PKP Forum: OJS3 Bulk import subscriptions (confirmed: doesn't exist)](https://forum.pkp.sfu.ca/t/ojs3-bulk-import-subscriptions/62294)
- [PKP Forum: Subscription management API options (confirmed: none)](https://forum.pkp.sfu.ca/t/are-there-api-or-other-options-for-subscription-management-available-in-ojs-3-3/86106)
- [OJS subscription DB schema and DAO classes](../docs/ojs-api.md)
