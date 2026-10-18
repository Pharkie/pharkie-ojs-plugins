# Archive Mode Plugin

Marks the journal as an archived copy once it has moved to a new home. For
the SEA that is Harbour cut-over: OJS stays up for a few days only, as the
landing page for the journal's DOIs until Crossref holds the new addresses,
and nobody should sign in, register, reset a password or buy anything on it.

Plugin slug: `archiveMode`. Repo folder: `plugins/archive-mode/`, mounted at
`/var/www/html/plugins/generic/archiveMode` (`docker-compose.yml`).

## What it does, when on

- **A bar at the top of every reader-facing page**, the PDF and HTML galley
  viewers and OJS's own pages (the closed message, search) included: this
  website is no longer live and is an archive of the journal for reading
  only; the journal has moved; don't sign in, register, buy or reset a
  password here. Its link goes
  to the same article or issue on the new site when `map.json` knows it, to
  the matching section (issues, about, author guidelines, editorial board,
  search) when it does not, and to the journal's home otherwise. It is in the
  HTML from the server, so it shows with JavaScript off. It is not sticky.
  OJS's 404 for an unknown article is a bare one-line response with no page
  around it, so it has no bar.
- **Closed routes show one message instead** (`ArchiveModeHandler`), GET or
  POST, and do nothing else:

  | Route | Why |
  |---|---|
  | `login/*` except `signOut`, `signOutAsUser` | sign-in, lost password, reset, change password, sign in as; the site-level `/index/login` too |
  | `user/*` | register, activate, profile, subscriptions and their payments |
  | `payment/*` except Stripe's `return` and `webhook` | any payment |
  | `about/subscriptions` | "Non-members can purchase individual articles or issues" |
  | `about/submissions` | "Login or Register to make a submission" (stock OJS, signed out) |
  | `information/readers` | its only content is how to register |
  | `information/authors` | stock OJS: register or log in to submit (the SEA's says to email the editors) |
  | `submitanarticle` | the SEA's "Submit an Article" static page, in its menu and footer |
  | `advertisers` | the SEA's "For Advertisers" static page: advert rates and booking |

  Stripe's two callbacks stay open because they only complete a payment that
  was already taken before the switch.
- **Payments read as off** for every other page, in memory for that request
  (never saved): `paymentsEnabled` false and the three fees 0, on the
  request's journal and on the template's (a second copy, which the galley
  buttons print the price from). So no price and no purchase step appears
  even if the journal's fees are still set. A reader without access who clicks a
  padlocked galley is sent to sign in or to About > Subscriptions, both
  closed, and the message links to the article they wanted. Journal managers
  and the site admin are left out of this, because a settings form they open
  would save the in-memory value.
- **The sidebar loses its offers**, in memory for that request: every
  custom block (Custom Block Manager: the SEA's "BOOK NOW" event banners and
  its "For Advertisers" link, all written for the live site), and OJS's own
  "Make a Submission" and "Subscription" blocks if a journal has them. The
  Information block stays, with For Librarians only (For Readers and For
  Authors are closed). Reading blocks (Browse, Language and the like) stay
  as they are.
  Nothing is saved: the journal's `sidebar` setting is untouched, and staff
  see the sidebar as it is, for the same reason as payments.
- **Register and Login leave the header** for everyone but staff.
- **Two other plugins stand down on the same value**: WP-OJS Subscription API
  adds none of its sign-in, reset or profile messages, no reset handler and no
  "Manage your membership" footer; Inline HTML Galley shows no "log in or buy"
  box.

Reading is unchanged: the home page, issues, articles, open galleys (viewer
and download), search and OAI-PMH. Anyone already signed in stays signed in,
and can do nothing a signed-out reader cannot.

## The switch

One value, `OJS_ARCHIVE_MOVED_TO` in `/opt/pharkie-ojs-plugins/.env`: the URL
of the journal's new home. `entrypoint.sh` writes it into `config.inc.php` as
`[archive] moved_to` each time the container starts. Empty, the plugin adds no
hooks and the site is exactly as before.

### Before the day, once (nothing visible changes)

```bash
ssh sea-live
cd /opt/pharkie-ojs-plugins && git pull
C="docker compose -f docker-compose.yml -f docker-compose.staging.yml"
$C up -d ojs     # recreates OJS with the plugin's bind mount
$C exec -T ojs-db bash -c 'mariadb -u$MYSQL_USER -p$MYSQL_PASSWORD $MYSQL_DATABASE' < plugins/archive-mode/install.sql
$C exec -T ojs php -r 'require_once("/var/www/html/tools/bootstrap.php"); Illuminate\Support\Facades\Cache::flush();'
```

[`install.sql`](install.sql) registers and enables the plugin for the whole
install (it is a site plugin, so the site-level `/index/login` and
`/index/user/register` are covered too), and is safe to run again. The cache
flush is the one `setup-ojs.sh` does after its own SQL. The plugin is inert
while the value is empty: the site looks exactly as before. `setup-ojs.sh`
runs the same file on a dev or staging rebuild.

**The article and issue map** (optional; without it every page links to the
journal's home or section). Write it from Harbour's database, on the box, any
time after Harbour's journal import:

```bash
docker exec harbour-harbour-db-1 psql -U harbour -d harbour -Atc "
  select json_build_object(
    'articles', (select coalesce(json_object_agg(substring(source_ref from 9), slug), '{}'::json)
                 from journal_articles where source_ref like 'ojs:sub:%' and published_at is not null),
    'issues',   (select coalesce(json_object_agg(substring(source_ref from 11), slug), '{}'::json)
                 from journal_issues where source_ref like 'ojs:issue:%' and published_at is not null))
" > /opt/pharkie-ojs-plugins/plugins/archive-mode/map.json
python3 -c "import json; d=json.load(open('/opt/pharkie-ojs-plugins/plugins/archive-mode/map.json')); print(len(d['articles']), 'articles,', len(d['issues']), 'issues')"
```

Harbour keys each imported article on its OJS submission id
(`ojs:sub:<id>`) and each issue on its OJS issue id (`ojs:issue:<id>`), the
same ids in OJS's addresses (`/ea/article/view/<id>`, `/ea/issue/view/<id>`).
Only published Harbour pages are listed, so no link goes to a 404. The file is
gitignored, read on each request, and a missing or broken one falls back to
the journal's home. A slug that is not lowercase letters, digits and hyphens
is ignored.

### On the day

```bash
ssh sea-live
cd /opt/pharkie-ojs-plugins
echo 'OJS_ARCHIVE_MOVED_TO=https://existentialanalysis.org.uk/journal' >> .env
docker compose -f docker-compose.yml -f docker-compose.staging.yml up -d ojs
```

**Proves it** (signed out):

```bash
curl -s https://journal.existentialanalysis.org.uk/ea | grep -c 'archive-mode-bar"'        # 1
curl -s https://journal.existentialanalysis.org.uk/ea/login | grep -c 'archive-mode-closed__link'   # 1
curl -s -o /dev/null -w '%{http_code}\n' -X POST https://journal.existentialanalysis.org.uk/ea/payment/plugin/StripePayment/webhook   # 400: the webhook still answers
```

### The way back

Delete the `OJS_ARCHIVE_MOVED_TO` line (or leave it empty) and run the same
`up -d ojs`. Nothing was written to the database, so nothing else needs
undoing.

## Where the links go

`ArchiveModeRoutes::destination`, with `moved_to` as the base:

| OJS page | Link |
|---|---|
| `article/view/<id>/…` or `article/download/<id>/…`, in the map | `<moved_to>/<slug>` "Read this article on the SEA's website" |
| `issue/view/<id>/…` or `issue/download/<id>/…`, in the map | `<moved_to>/issues/<slug>` "See this issue on the SEA's website" |
| any other `issue/*` | `<moved_to>/issues` |
| `about` | `<moved_to>/about` |
| `about/submissions`, `information/authors`, `submitanarticle` (all closed) | `<moved_to>/for-contributors` |
| `about/editorialMasthead`, `editorialTeam` | `<moved_to>/editorial-board` |
| `search` with words | `<moved_to>?q=<words>` |
| anything else | `<moved_to>` "Go to the journal on the SEA's website" |

These follow Harbour's own routes (`src/pages/journal/`) and its redirect map
(`src/lib/redirects.ts`), which is what `journal.` 301s into once OJS retires.
On a closed page reached by OJS sending a reader to sign in, the link follows
the page they were after (OJS's `source` parameter).

## Monitoring

With archive mode on, the smoke test's OJS password login (`smoke-test.sh`,
I3) fails, as it should. The daily read-only browser check accepts the closed
page in place of the sign-in form (`live-readonly.spec.ts`). The hourly
monitor's Stripe webhook check still passes.

## Tests

```bash
php plugins/archive-mode/tests/routes-test.php
```

Runs without OJS: which routes close, which sidebar blocks go, where each
page links, how `source` is read, and how the bar goes into a page.

## Uninstall

It goes with OJS. Before that, the way back above turns it off.
