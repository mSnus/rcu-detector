FROM php:8.2-fpm-bookworm

# gd is for the support form on /try, which stores a downscaled copy of the
# customer's photograph. Image *I/O*, not computer vision -- recognition
# still never happens in PHP. Without it the copy silently falls back to the
# original bytes, so support would receive 40 MP phone photographs and nothing
# would report it; the build asserts the extension below rather than leaving
# that to be discovered by a full inbox.
RUN apt-get update && apt-get install -y --no-install-recommends \
        git unzip libzip-dev libicu-dev libjpeg-dev libpng-dev libfreetype-dev \
    && docker-php-ext-configure gd --with-jpeg --with-freetype \
    && docker-php-ext-install -j"$(nproc)" pdo_mysql zip intl opcache gd \
    && php -r 'exit(function_exists("imagecreatefromstring") ? 0 : 1);' \
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
