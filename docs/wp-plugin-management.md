# WP Plugin Management

WordPress uses [Bedrock](https://roots.io/bedrock/), which manages plugins via Composer instead of the WP admin installer. Plugins not in `wordpress/composer.json` won't exist after a fresh deploy — `composer install` is what populates the plugins directory.

## Adding a free plugin (from wordpress.org)

Add to `wordpress/composer.json`:

```json
"require": {
    "wpackagist-plugin/plugin-name": "^1.0",
    ...
}
```

Then run `composer update` inside the WP container:

```bash
ssh your-server "cd /opt/wp-ojs-sync && \
  docker compose -f docker-compose.yml -f docker-compose.staging.yml \
  exec wp composer update --no-dev --working-dir=/var/www/html"
```

Or commit the updated `composer.json` + `composer.lock` and `git pull` on the VPS.

## Adding a paid plugin (not on wpackagist)

Paid plugins can't be pulled from a public registry. Instead:

1. Drop the plugin folder in `wordpress/paid-plugins/`
2. Add a `path` repository in `wordpress/composer.json`:
   ```json
   "repositories": [
       {
           "type": "path",
           "url": "paid-plugins/my-paid-plugin",
           "options": { "symlink": false }
       }
   ]
   ```
3. Add the `require` entry:
   ```json
   "require": {
       "vendor/my-paid-plugin": "1.0.0",
       ...
   }
   ```
4. `rsync` the paid plugins to the VPS (the deploy script does this automatically if they exist locally)

See the existing entries for WooCommerce Subscriptions and WooCommerce Memberships as examples.

## Important for production

Any plugin active on the live WP site must be added to `composer.json` before production deployment. If it's missing, the plugin won't be installed and anything depending on it will break. Audit the live plugin list against `composer.json` before going live.
