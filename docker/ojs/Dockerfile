# Build stage: install Stripe PHP SDK
FROM composer:2 AS stripe-deps
COPY plugins/stripe-payment/composer.json plugins/stripe-payment/composer.lock /app/
WORKDIR /app
RUN composer install --no-dev --no-interaction --optimize-autoloader

FROM pkpofficial/ojs:3_5_0-3

# Add envsubst for config templating (PKP image is Debian-based)
USER root
RUN mkdir -p /var/lib/apt/lists/partial && apt-get update \
    && apt-get install -y --no-install-recommends gettext-base default-mysql-client jq \
    && rm -rf /var/lib/apt/lists/* \
    && mkdir -p /var/www/files/temp && chown www-data:www-data /var/www/files/temp

# Stripe plugin vendor deps (from build stage)
COPY --from=stripe-deps /app/vendor/ /opt/stripe-vendor/

# Core patches (OJS bugs that can't wait for upstream fixes)
COPY docker/ojs/patches/ /opt/ojs-patches/
RUN php /opt/ojs-patches/crossref-error-details.php \
    && php /opt/ojs-patches/skip-remote-schema-validation.php \
    && php /opt/ojs-patches/recommend-by-similarity.php \
    && php /opt/ojs-patches/citation-style-dropdown.php \
    && php /opt/ojs-patches/recommend-by-author.php

# Branding assets (logo, favicon, homepage image, sidebar banners) — applied by setup-ojs.sh
COPY docker/ojs/branding/ /opt/ojs-branding/
