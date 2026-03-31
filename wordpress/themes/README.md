# Custom Themes

Custom WordPress themes (proprietary, not distributable via wpackagist).
Place them here as subdirectories. They are gitignored and deployed via rsync.

## Required themes

| Theme | Directory name | Notes |
|-------|---------------|-------|
| SEAcomm | `seacomm` | Custom Gantry 5 theme for SEA community site |
| Helium | `g5_helium` | Gantry 5 base theme (dependency of seacomm) |

## How to get them

Pull from the live WP server:

```bash
WP_ROOT="community.existentialanalysis.org.uk"
scp -r -P 722 -i ~/.ssh/hetzner existent@community.existentialanalysis.org.uk:$WP_ROOT/wp-content/themes/seacomm wordpress/themes/
scp -r -P 722 -i ~/.ssh/hetzner existent@community.existentialanalysis.org.uk:$WP_ROOT/wp-content/themes/g5_helium wordpress/themes/
```

Then optimize images (reduces ~33MB to ~19MB):

```bash
find wordpress/themes/seacomm/custom/images -name "*.png" -exec pngquant --quality=65-80 --speed 1 --force --ext .png {} \;
```

## How they're deployed

- **Staging/prod:** `scripts/infra/deploy.sh` rsyncs `wordpress/themes/` to `wordpress/web/app/themes/` on the VPS
- **Local dev:** `scripts/wp/setup-wp.sh` copies them into the Bedrock theme path on container start
- **Gantry 5** plugin is installed via Composer (`wpackagist-plugin/gantry5`)
