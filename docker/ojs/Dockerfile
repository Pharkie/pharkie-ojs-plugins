# Build stage: install Stripe PHP SDK
FROM composer:2 AS stripe-deps
COPY plugins/stripe-payment/composer.json plugins/stripe-payment/composer.lock /app/
WORKDIR /app
RUN composer install --no-dev --no-interaction --optimize-autoloader

# Build stage: a patched PHP 8.3 for the PKP image below.
#
# pkpofficial/ojs:3_5_0-3 is the official php:8.3-apache image (Debian 13) with
# PHP 8.3.29, which has PHP2Shell (CVE-2026-109618, CVE-2026-109619:
# unauthenticated RCE from a multipart upload, fixed in 8.3.36). PKP has
# published no patched image. So this stage takes the official image at the
# fixed version and builds the extensions PKP's image adds (opcache and sodium
# come with the base), with PKP's own copy of install-php-extensions, so the
# module list matches PKP's exactly; the final stage lays the result
# over PKP's PHP. Same Debian, same PHP minor (extension API 20230831), same
# paths: OJS and its config are untouched, and the bind-mounted
# php-overrides.ini applies as before. Drop this stage when PKP ships a 3.5
# image on PHP >= 8.3.36.
FROM php:8.3.36-apache-trixie AS php-patched
COPY --from=pkpofficial/ojs:3_5_0-3 /usr/local/bin/install-php-extensions /usr/local/bin/
RUN install-php-extensions bcmath ftp gd gettext intl mysqli pdo_mysql pdo_pgsql pgsql xsl zip

FROM pkpofficial/ojs:3_5_0-3

USER root

# PHP 8.3.36 over the base's 8.3.29 (see the php-patched stage): the CLI, the
# extensions and their .ini files, the headers, Apache's mod_php, and the
# matching source that docker-php-ext-install builds against.
COPY --from=php-patched /usr/local/ /usr/local/
COPY --from=php-patched /usr/lib/apache2/modules/libphp.so /usr/lib/apache2/modules/libphp.so
COPY --from=php-patched /usr/src/php.tar.xz /usr/src/php.tar.xz.asc /usr/src/
ENV PHP_VERSION=8.3.36 \
    PHP_URL=https://www.php.net/distributions/php-8.3.36.tar.xz \
    PHP_ASC_URL=https://www.php.net/distributions/php-8.3.36.tar.xz.asc \
    PHP_SHA256=dd54693aa334c5c4d72e9aee96b082c2473750a18a6c1d105789c93a3b1692fd
RUN php -v | grep -q '^PHP 8\.3\.36 ' \
    && php -m | grep -qx intl && php -m | grep -qx gd && php -m | grep -qx pdo_mysql

# Add envsubst for config templating (PKP image is Debian-based)
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
