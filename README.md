# Apache Superset 6.1.0 on Docker Compose

Single-host deployment with pinned Superset, PostgreSQL metadata storage, Redis,
separate SQL and reports workers, Playwright/Chromium, and a single Celery beat.
A reverse proxy is intentionally not included.
Persistent data is stored in host directories below
`/opt/docker/apache-superset`, not in Docker-managed volumes.

## Pinned versions

- Apache Superset: `6.1.0`
- PostgreSQL metadata database: `16.15-bookworm`
- Redis: `7.4.11-bookworm`
- PostgreSQL Python driver: `psycopg2-binary==2.9.9`
- Reports browser: Playwright with Chromium

PostgreSQL 16.15 is used because 16.x is the newest PostgreSQL branch listed as
supported by the Superset 6.1.0 metadata-database documentation. PostgreSQL 18.6
is the newest upstream PostgreSQL release as of 2026-08-24, but it is not listed
as supported for Superset 6.1.0 metadata storage.

## First start

Requirements: Docker Engine with Docker Compose v2 and `openssl`.

Create the persistent directories before starting the stack. The UID/GID values
match the users in the pinned container images:

```bash
sudo install -d -m 0750 -o 999 -g 999 /opt/docker/apache-superset/postgres
sudo install -d -m 0750 -o 999 -g 999 /opt/docker/apache-superset/redis
sudo install -d -m 0750 -o 1000 -g 1000 /opt/docker/apache-superset/superset
sudo install -d -m 0750 -o "$(id -u)" -g "$(id -g)" /opt/docker/apache-superset/backups
```

Compose uses long bind-mount syntax with `create_host_path: false`, so it will
fail clearly if a directory is absent instead of silently creating a root-owned
directory.

```bash
chmod +x init-env.sh
./init-env.sh
```

Review `.env`, especially the administrator email and sizing values. The script
creates cryptographically random values for every secret and sets file mode 600.
Also set `SUPERSET_PUBLIC_URL` to the HTTPS URL served by the reverse proxy. For
email delivery, fill in `SMTP_*`; for Slack delivery, set `SLACK_API_TOKEN`.
Webhook notifications are enabled and require HTTPS endpoints.

Validate and start the stack:

```bash
docker compose config --quiet
docker compose build --pull
docker compose up -d
docker compose ps
```

The initialization container applies metadata migrations, creates the initial
administrator, and synchronizes roles and permissions before the web and worker
containers start.

Check readiness:

```bash
curl --fail http://127.0.0.1:8088/health
```

Expected response: `OK`.

## Changing `.env` after the first start

Changing `.env` does not update the environment of running containers. After a
permitted change, validate the resolved Compose configuration and recreate the
stack:

```bash
docker compose config --quiet
docker compose up -d --force-recreate
docker compose ps
curl --fail http://127.0.0.1:8088/health
```

`docker compose restart` is not sufficient because it restarts containers with
their existing environment. An environment-only change does not require
`docker compose build`. Recreating the stack preserves all bind-mounted data,
but causes a brief interruption while the services become healthy.

The following values can normally be changed and then applied with the command
above:

- Public address and notifications: `SUPERSET_PUBLIC_URL`, `SMTP_HOST`,
  `SMTP_PORT`, `SMTP_STARTTLS`, `SMTP_SSL`, `SMTP_SSL_SERVER_AUTH`, `SMTP_USER`,
  `SMTP_PASSWORD`, `SMTP_MAIL_FROM`, and `SLACK_API_TOKEN`.
- Alert/report behavior: `ALERT_REPORTS_NOTIFICATION_DRY_RUN`,
  `SCREENSHOT_LOCATE_WAIT`, and `SCREENSHOT_LOAD_WAIT`.
- Runtime sizing: `SERVER_WORKER_AMOUNT`, `SERVER_THREADS_AMOUNT`,
  `GUNICORN_TIMEOUT`, `GUNICORN_KEEPALIVE`, `WORKER_MAX_REQUESTS`,
  `WORKER_MAX_REQUESTS_JITTER`, `CELERY_WORKER_CONCURRENCY`, and
  `REPORTS_WORKER_CONCURRENCY`.
- Listener and general runtime settings: `SUPERSET_BIND_ADDRESS`,
  `SUPERSET_PORT`, `SESSION_LIFETIME_HOURS`, `SUPERSET_LOG_LEVEL`, and `TZ`.

Keep `ENABLE_PROXY_FIX=true` when using the documented reverse-proxy setup and
keep `SESSION_COOKIE_SECURE=true` for HTTPS. These two values are technically
reloadable, but weakening them is not recommended in production.

Some values need special handling:

- `REDIS_PASSWORD` can be changed, but Redis and every Superset service must be
  recreated together. The full-stack command above does that. Expect queued and
  cached Redis state to be unavailable during the restart.
- `POSTGRES_PASSWORD` must first be changed for the existing PostgreSQL role;
  changing only `.env` makes Superset use a password that PostgreSQL does not
  recognize. Update the database role and `.env` as one maintenance operation,
  then recreate the complete stack.
- `SUPERSET_SECRET_KEY` must not be replaced directly. It encrypts database
  connection credentials and other secrets stored in the metadata database.
  Rotate it only with Superset's documented `PREVIOUS_SECRET_KEY` and
  `superset re-encrypt-secrets` procedure, after taking a metadata backup.
- `ADMIN_USERNAME`, `ADMIN_PASSWORD`, `ADMIN_EMAIL`, `ADMIN_FIRSTNAME`, and
  `ADMIN_LASTNAME` are initial-administrator inputs. Editing them does not
  modify an existing account; change that user through Superset's user
  administration instead.
- `POSTGRES_DB` and `POSTGRES_USER` describe the already initialized metadata
  database. Changing them requires a deliberate database migration or rename,
  not only a Compose restart.
- `DATA_ROOT` may be changed only while the stack is stopped and after all
  `postgres`, `redis`, and `superset` data has been copied to the corresponding
  directories under the new root with the documented ownership.
- `COMPOSE_PROJECT_NAME` identifies the Compose stack. Changing it can create a
  second set of containers and network, so keep it unchanged for an existing
  deployment.

Do not delete and regenerate `.env` after the first start: `init-env.sh` creates
new values for `SUPERSET_SECRET_KEY`, `POSTGRES_PASSWORD`, `REDIS_PASSWORD`, and
`ADMIN_PASSWORD`. Keep the active `.env` in a secure backup together with the
metadata-database backup.

## External reverse proxy

Proxy to `http://<docker-host-private-ip>:8088` and pass at least:

- `Host`
- `X-Forwarded-For`
- `X-Forwarded-Proto: https`

Terminate TLS at the proxy. Restrict inbound TCP/8088 on the Docker host to the
proxy host/security group only. `ENABLE_PROXY_FIX=true` and secure session
cookies are already configured. Direct HTTP login is intentionally unsupported
while `SESSION_COOKIE_SECURE=true`.

The same public URL must be stored in `SUPERSET_PUBLIC_URL` in `.env`; it is used
for links placed into alert and report notifications. The reports worker renders
charts through the internal URL `http://superset-web:8088/`.

### Direct HTTP access without a reverse proxy

For local testing, or when accessing the Docker host directly over plain HTTP,
set the following values in `.env`:

```dotenv
SESSION_COOKIE_SECURE=false
SUPERSET_PUBLIC_URL=http://YOUR_SERVER_IP:8088
```

Replace `YOUR_SERVER_IP` with the address used by the browser, then apply the
change by recreating the stack:

```bash
docker compose up -d --force-recreate
```

Clear the browser cookies for that address, or use a private window, before
trying to sign in again. With `SESSION_COOKIE_SECURE=true`, browsers do not send
the Superset session cookie over plain HTTP, so login cannot persist.

Direct HTTP is intended only for local or otherwise trusted testing. After an
HTTPS reverse proxy is configured, set `SESSION_COOKIE_SECURE=true`, change
`SUPERSET_PUBLIC_URL` to the public `https://` URL, and recreate the stack again.

## Alerts and Reports

Alerts and Reports are enabled at startup, with notification dry-run disabled.
The stack contains two isolated Celery queues and workers:

- `superset-worker` consumes `default` and `sql_lab` tasks.
- `superset-reports-worker` consumes only the `email_reports` queue and is the
  only image containing Playwright/Chromium.

The single `superset-beat` instance runs `reports.scheduler` every minute and
prunes report logs daily. Do not start a second beat instance, or notifications
can be delivered more than once.

Available delivery methods:

- Email after valid `SMTP_*` values are supplied in `.env`.
- Slack after `SLACK_API_TOKEN` is supplied and the Superset app is invited to
  the destination channel.
- HTTPS webhook without a global service credential.

The reports worker defaults to concurrency 1 because browser rendering is
CPU/RAM intensive. Increase `REPORTS_WORKER_CONCURRENCY` only after observing
actual resource usage. PNG/PDF screenshots may take up to the configured
`SCREENSHOT_LOAD_WAIT`.

## Connect the PostgreSQL RDS source

1. Give the Docker host network access to the RDS endpoint on TCP/5432.
2. Create a dedicated RDS user with only the required `CONNECT`, `USAGE`, and
   `SELECT` privileges. Do not reuse the application owner or metadata user.
3. In Superset, open **Settings → Database Connections → + Database** and add the
   RDS PostgreSQL connection.
4. Require TLS in the connection, for example:

```text
postgresql+psycopg2://readonly_user:URL_ENCODED_PASSWORD@your-rds-endpoint:5432/database?sslmode=require
```

Superset manages data-source connections through its UI and encrypts their
credentials in the metadata database using `SUPERSET_SECRET_KEY`. The RDS source
credential is therefore not a Docker runtime secret and is not included in the
stack's `.env` file.

For long SQL Lab queries, enable **Asynchronous Query Execution** in that database
connection after verifying the Celery worker is healthy.

## Persistence and backup

Host bind mounts hold the persistent state:

- `/opt/docker/apache-superset/postgres` — metadata PostgreSQL
- `/opt/docker/apache-superset/redis` — Redis AOF/cache state
- `/opt/docker/apache-superset/superset` — Superset runtime state and beat schedule
- `/opt/docker/apache-superset/backups` — recommended logical backup location

The PostgreSQL directory is the critical one: it contains users, dashboards,
charts, datasets, and encrypted connection details. `DATA_ROOT` in `.env` can
move all three mounted directories together before the first start.

Example logical backup:

```bash
docker compose exec -T postgres sh -c \
  'PGPASSWORD="$POSTGRES_PASSWORD" pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' \
  > "/opt/docker/apache-superset/backups/superset-$(date +%F).dump"
```

Copy backups off the EC2 instance, preferably to a versioned and encrypted S3
bucket. Also configure EBS snapshots. Test restores periodically.

## Operations

View logs:

```bash
docker compose logs -f --tail=200 \
  superset-web superset-worker superset-reports-worker superset-beat
```

Stop without deleting data:

```bash
docker compose down
```

`docker compose down`, including `--volumes`, does not remove these bind-mounted
host directories. Deleting data requires a separate explicit filesystem action.

Before upgrading, back up PostgreSQL, read the Superset `UPDATING.md`, pin the new
image tag in both `Dockerfile` and `docker-compose.yml`, rebuild, and let
`superset-init` run `superset db upgrade` and `superset init`.

## Official references

- [Superset 6.1.0 installation methods](https://superset.apache.org/admin-docs/6.1.0/installation/installation-methods/)
- [Superset 6.1.0 Docker builds](https://superset.apache.org/admin-docs/6.1.0/installation/docker-builds/)
- [Superset 6.1.0 configuration](https://superset.apache.org/admin-docs/6.1.0/configuration/configuring-superset/)
- [Superset 6.1.0 caching](https://superset.apache.org/admin-docs/6.1.0/configuration/cache/)
- [Superset 6.1.0 async queries](https://superset.apache.org/admin-docs/6.1.0/configuration/async-queries-celery/)
- [Superset production security guide](https://superset.apache.org/admin-docs/6.1.0/security/securing_superset/)
