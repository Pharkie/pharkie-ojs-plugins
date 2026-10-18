# Caddy vhost snippets

One `*.caddy` file per project, served by the box's shared Caddy, which is not
part of this repo: on the SEA box it is Harbour's `harbour-infra` stack. Copy
them by hand to the host drop-in dir `/opt/caddy-conf.d/`, where other projects
also drop their own snippets. Caddy runs with `--watch`, so changes auto-reload.

This directory holds **pharkie-ojs-plugins' own** vhosts (wp, ojs). Other
projects keep their snippets in their own repos, Umami's included (Harbour's).

See [`docs/shared-caddy.md`](../../../docs/shared-caddy.md) for the full convention.
