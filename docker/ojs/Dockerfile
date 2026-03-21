FROM pkpofficial/ojs:3_5_0-3

# Add envsubst for config templating (PKP image is Debian-based)
USER root
RUN mkdir -p /var/lib/apt/lists/partial && apt-get update \
    && apt-get install -y --no-install-recommends gettext-base default-mysql-client jq \
    && rm -rf /var/lib/apt/lists/* \
    && mkdir -p /var/www/files/temp && chown www-data:www-data /var/www/files/temp

# Core patches (OJS bugs that can't wait for upstream fixes)
COPY docker/ojs/patches/ /opt/ojs-patches/
RUN php /opt/ojs-patches/crossref-error-details.php \
    && php /opt/ojs-patches/skip-remote-schema-validation.php

# Branding assets (logo, favicon, homepage image, sidebar banners) — applied by setup-ojs.sh
COPY docker/ojs/branding/ /opt/ojs-branding/
