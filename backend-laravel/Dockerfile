FROM php:8.2-fpm-bookworm

RUN apt-get update && apt-get install -y --no-install-recommends \
        git unzip libzip-dev libicu-dev \
    && docker-php-ext-install -j"$(nproc)" pdo_mysql zip intl opcache \
    && rm -rf /var/lib/apt/lists/*

COPY --from=composer:2 /usr/bin/composer /usr/bin/composer

# Above the application's own limit -- see the file. The image default is 2M,
# which is under any phone photograph.
COPY docker/uploads.ini /usr/local/etc/php/conf.d/zz-uploads.ini

WORKDIR /srv/app

COPY composer.json composer.lock ./
RUN composer install --no-dev --no-scripts --no-autoloader --prefer-dist --no-interaction

COPY . .
RUN mkdir -p storage/framework/cache storage/framework/sessions \
             storage/framework/views storage/logs bootstrap/cache \
    && composer dump-autoload --optimize --no-dev \
    && chown -R www-data:www-data storage bootstrap/cache

USER www-data

CMD ["php-fpm"]
