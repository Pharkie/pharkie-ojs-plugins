# Pinned to the patch release, not php:8.2-apache: a floating tag builds from
# whatever copy of the base the box has cached. 8.2.35 fixes PHP2Shell
# (CVE-2026-109618, CVE-2026-109619).
FROM php:8.2.35-apache-trixie

# PHP extensions required by WordPress + WooCommerce
RUN apt-get update && apt-get install -y --no-install-recommends \
      libfreetype6-dev libjpeg62-turbo-dev libpng-dev \
      libzip-dev libicu-dev libxml2-dev \
      unzip git default-mysql-client \
    && docker-php-ext-configure gd --with-freetype --with-jpeg \
    && docker-php-ext-install -j$(nproc) \
      gd mysqli pdo_mysql zip intl xml opcache bcmath \
    && rm -rf /var/lib/apt/lists/*

# Composer
COPY --from=composer:2 /usr/bin/composer /usr/bin/composer

# WP-CLI
RUN curl -sO https://raw.githubusercontent.com/wp-cli/builds/gh-pages/phar/wp-cli.phar \
    && chmod +x wp-cli.phar && mv wp-cli.phar /usr/local/bin/wp

# Apache: enable rewrite, set DocumentRoot to Bedrock's web/, allow .htaccess
RUN a2enmod rewrite \
    && echo "ServerName localhost" >> /etc/apache2/apache2.conf
ENV APACHE_DOCUMENT_ROOT=/var/www/html/web
RUN sed -ri -e 's!/var/www/html!${APACHE_DOCUMENT_ROOT}!g' \
    /etc/apache2/sites-available/*.conf /etc/apache2/apache2.conf \
    && sed -ri -e 's/AllowOverride None/AllowOverride All/g' \
    /etc/apache2/apache2.conf

# PHP2Shell hardening from the advisory (sansec.io/research/php2shell): one of
# the two bugs is in the session upload-progress handler, which nothing here
# uses. Kept off after the patch too. expose_php Off drops the X-Powered-By
# header, which names the exact PHP version.
RUN printf 'session.upload_progress.enabled = 0\nexpose_php = Off\n' > "$PHP_INI_DIR/conf.d/zz-hardening.ini"

WORKDIR /var/www/html
