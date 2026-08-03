# Deployment

Four containers: `rcu-service` (all computer vision, FastAPI), `laravel`
(uploads, catalog table, admin UI), `nginx`, and `mysql` for the application
database. The recognition service is **not** published — it is reachable only
on the internal network, because it has no TLS and its shared-secret header is
not a substitute for one.

The repository carries no photos, no build artefacts and no catalogue files.
All three are supplied per-deployment and mounted from the host.

---

## 1. Prerequisites

```bash
docker --version          # 24+ ; Compose v2 or v5
docker compose version
git --version
```

Sizing: the two images are ~1.6 GB. The service plateaus at ~370–480 MB RSS,
and a catalog build peaks at ~700 MB per parallel job on top of that. Below
about 1.5 GB free, a build and a running service will contend.

## 2. Clone

```bash
sudo mkdir -p /var/www && cd /var/www
git clone https://github.com/mSnus/rcu-detector.git
cd rcu-detector
mkdir -p work            # build artefacts; gitignored, must exist before `up`
```

## 3. Configure

```bash
cp .env.example .env
```

Fill in every blank. Compose refuses to start with any of them unset, which is
deliberate — it fails loudly rather than booting with an empty password.

Generate the secrets:

```bash
echo "RCU_INTERNAL_TOKEN=$(openssl rand -hex 24)"
echo "DB_PASSWORD=$(openssl rand -base64 18 | tr -d '/+=')"
echo "DB_ROOT_PASSWORD=$(openssl rand -base64 18 | tr -d '/+=')"
echo "APP_KEY=base64:$(openssl rand -base64 32)"
```

`APP_KEY` is generated with `openssl`, not `php artisan key:generate`, on
purpose: on a fresh clone the required variables are not yet set, so
`docker compose run --rm laravel …` aborts before it can reach artisan. Laravel
wants 32 random bytes base64-encoded, which is exactly what the line above
produces.

Then set the paths:

| variable | meaning |
|---|---|
| `RCU_FILES_DIR` | host directory holding the catalogue photos, mounted read-only |
| `LEGACY_DB_HOST` | `host.docker.internal` if the catalogue DB is on this host, otherwise its address |
| `LEGACY_DB_USERNAME` / `_PASSWORD` | a **read-only** account — see step 6 |
| `RCU_ITEM_URL` | link template back to the source item page, `{id}` is the node id |
| `HTTP_BIND` / `HTTP_PORT` | keep bound to `127.0.0.1` and put a reverse proxy in front for TLS |

Nothing in this stack terminates TLS.

## 4. Build and start

```bash
docker compose up -d --build
docker compose ps                    # all four healthy/running
docker compose exec laravel php artisan migrate --force
```

MySQL's first boot initialises its data directory and can outrun the
healthcheck's retry budget, so `laravel` may report a failed dependency on the
very first `up`. Wait for `mysql` to report healthy and run `docker compose up
-d` once more.

## 5. Photos

Point `RCU_FILES_DIR` at the directory of catalogue photos. Before extracting
anything, check that they decode consistently:

```bash
docker compose exec rcu-service python scripts/check_decode.py --dir /data/files
```

This asserts that the build path and the query path decode every image
identically, and that decoding is repeatable. It matters: a JPEG missing its
end-of-image marker decodes into a partly uninitialised buffer, which differs
between runs and silently corrupts fingerprints. Run it over every new drop.

## 6. Legacy catalogue database

The import reads product metadata over a separate read-only connection.

If that database is on the Docker host there are two ways to reach it, and the
socket is the better one.

**Never set `LEGACY_DB_HOST=localhost`.** To the mysql PDO driver `localhost`
means "use a Unix socket, ignore the port", so it looks for a socket *inside
the Laravel container*, where there is none. The error is `[2002] No such file
or directory` naming `Port: 3306` that was never dialled. Use
`host.docker.internal` for TCP, or `LEGACY_DB_SOCKET` for the socket.

### Over the host's socket (preferred)

Nothing restarts, nothing is exposed to the network, and MySQL's startup does
not come to depend on a Docker interface existing.

```ini
# .env
LEGACY_DB_SOCKET=/var/run/mysqld/mysqld.sock
LEGACY_DB_SOCKET_DIR=/var/run/mysqld     # host path, if not the default
```

The connection then arrives as `localhost`, so it needs the ordinary local
grant — *not* the subnet grant below:

```sql
CREATE USER 'rcud_usr'@'localhost' IDENTIFIED BY '...';
GRANT SELECT ON rcud.* TO 'rcud_usr'@'localhost';
FLUSH PRIVILEGES;
```

### Over TCP

`LEGACY_DB_HOST=host.docker.internal` resolves to the host, but expect two
refusals in sequence, which mean different things:

- `[2002] Connection refused` — MySQL binds `127.0.0.1` only. Confirm with
  `ss -ltnp | grep 3306`. Fixing it means `bind-address = 127.0.0.1,172.28.0.1`
  and a restart of a live database, and it couples mysqld's startup to the
  Docker network: if `172.28.0.1` is absent at boot, **mysqld will not start**.
  `0.0.0.0` avoids that but then the port needs firewalling off any public
  interface. This is why the socket is preferred.
- `Host '172.28.0.x' is not allowed to connect` — reached MySQL, login refused.
  Containers arrive from the bridge subnet and a grant to `'user'@'localhost'`
  does not cover them:

```sql
CREATE USER 'rcud_usr'@'172.28.%' IDENTIFIED BY '...';
GRANT SELECT ON rcud.* TO 'rcud_usr'@'172.28.%';
FLUSH PRIVILEGES;
```

Substitute the real database name. `rcud` is this file's placeholder, and a
grant written against it is silently useless — `SHOW GRANTS FOR 'rcud_usr'@...`
is the check.

`SELECT` only, on either route. Nothing here writes to that schema, and a
migration pointed at the connection would be destructive.

The `internal` network pins `172.28.0.0/16` deliberately. Compose otherwise
allocates from the shared 172.17–172.31 pool in creation order, so a stack
recreated while another project holds the range moves subnet and a grant
written against the old one fails silently. Change the subnet and the grant
together, or neither.

Confirm before building anything:

```bash
docker compose exec laravel php artisan rcu:import-catalog --legacy --dry-run
```

## 7. Build the catalog

Extraction is a batch job, not a service. It runs in three steps, and the first
one is not optional on the legacy catalogue:

```bash
docker compose exec -T laravel php artisan rcu:legacy-manifest --out=- > work/primary.txt
docker compose --profile build run --rm extract --manifest /data/work/primary.txt --jobs 4
docker compose exec laravel php artisan rcu:import-catalog --legacy --prune --reindex
```

**`files/` is not a directory of remotes.** Roughly a third of it is
replacement-model promo banners (`Zamena_*`) and scanned instruction sheets,
hung off the same products at `delta >= 1`. Only the database says which is
which, and the extraction container has no database — hence the manifest.
Extracting without one indexes an instruction sheet as if it were a remote, so
it can be returned as a match, and every such image imports as a record with no
title and no `model_id`, indistinguishable from a genuine metadata miss.

Measured over the 138-image sample drop, extracted both ways:

| | fingerprints | unmatched |
|---|---|---|
| whole directory | 165 | 56 (34%) |
| `--manifest` | 109 | **0** |

The manifest goes out over **stdout** (`--out=-`), because the Laravel
container mounts `work/` read-only — it reads build artefacts, it does not
produce them. Every diagnostic goes to stderr, so the redirect above captures
the list and nothing else; run it without redirecting to see what was excluded
and which catalogued photographs are missing from disk.

The manifest and the import must come from the same read of the catalogue. Both
go through `App\Support\LegacyCatalog::primaryPhotos()` so they cannot define
"the product's photo" differently; regenerate the manifest if the catalogue
changed under a part-finished build.

`--jobs` is bounded by memory, not cores — allow ~1 GB per job. Measured
throughput single-process: **6.6 s per image** on the original photo set and
**8.0 s** over 138 legacy images (18m31s), which is the more representative
figure. That is roughly 22 h for 10k images and ~4.6 days for 50k, so choose
`--jobs` deliberately and consider a first run on a few thousand before
committing to the whole catalogue.

The token index and the `rcu_fingerprints` table must come from the *same*
extraction run. When they drift, matching still "works" but returns record ids
that resolve to no row, which reads as a database fault and is not. The import
command warns when the two counts disagree.

## 8. Verify

```bash
curl -s -m 5 http://127.0.0.1:8600/health        # must FAIL: service is unpublished
docker compose exec laravel php -r 'echo file_get_contents("http://rcu-service:8600/health");'
curl -s http://127.0.0.1:8080/api/identify -F photo=@some-remote.jpg
```

A successful identify returns `confidence`, ranked `candidates`, and a
`catalog` object per candidate resolving the record to its catalogue row. A
`catalog` of `null` means the index and the table came from different runs.

## Updating

```bash
cd /var/www/rcu-detector
git pull
docker compose up -d --build
docker compose exec laravel php artisan migrate --force
```

Rebuild the catalog only if extraction changed; a schema change does not
invalidate fingerprints.

---

## Notes

**Tests do not run in the production image.** It is built
`composer install --no-dev`, so phpunit is absent by design. Run the suite on a
development checkout.

**Host log growth.** Two things on a Docker host grow without limit unless
configured, and either will fill a disk long before the application does:

- container logs — set `log-opts` `max-size`/`max-file` in
  `/etc/docker/daemon.json`. The default `json-file` driver has no cap, and the
  setting applies only to containers created after `systemctl restart docker`;
- systemd's journal defaults to `min(10% of /var, 4G)`. That is a cap, not a
  target: it fills to it and stays there. Set `SystemMaxUse` in
  `/etc/systemd/journald.conf` if that is more history than you want.

**What is deliberately not in the image.** Photos, `work/` build artefacts and
the catalogue `files/` directory are mounted from the host. `work/` is the only
shared writable volume and is regenerated by the build profile.
