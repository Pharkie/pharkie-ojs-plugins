# Caddy vhost snippets

One `*.caddy` file per project. These are `import`ed by `docker/caddy/Caddyfile`
and served by the shared Caddy on the box. On deploy they are synced to the
host drop-in dir `/opt/caddy-conf.d/`, where other projects also drop their own
snippets. Caddy runs with `--watch`, so changes auto-reload.

This directory holds **pharkie-ojs-plugins' own** vhosts (wp, ojs, umami). Other
projects keep their snippets in their own repos.

See [`docs/shared-caddy.md`](../../../docs/shared-caddy.md) for the full convention.
