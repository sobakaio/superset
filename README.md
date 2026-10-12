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
- MCP dependency: `fastmcp==3.1.0` (the Superset 6.1.0 MCP extra pin)
- Reports browser: Playwright with Chromium

PostgreSQL 16.15 is used because 16.x is the newest PostgreSQL branch listed as
supported by the Superset 6.1.0 metadata-database documentation. PostgreSQL 18.6
is the newest upstream PostgreSQL release as of 2026-08-24, but it is not listed
as supported for Superset 6.1.0 metadata storage.

## AWS host requirements

The baseline AWS host for this single-node deployment is:

- EC2 `m7i.large` — 2 vCPU and 8 GiB RAM;
- Ubuntu Server 24.04 LTS, amd64;
- 64 GB EBS `gp3` volume mounted as the root/data filesystem;
- Docker Engine with Docker Compose v2 and `openssl`.

The 8 GiB profile is suitable for a small or moderate internal BI deployment
with one reports worker at concurrency 1. The reports container reserves a 2 GiB
shared-memory segment for Chromium, so monitor RAM and swap pressure before
increasing `REPORTS_WORKER_CONCURRENCY`, Celery concurrency, or web worker
counts. Increase the instance size if dashboards, SQL Lab, or reports are
consistently busy.

The 64 GB volume contains the operating system, Docker image layers, metadata
PostgreSQL, Redis AOF data, Superset runtime state, and backups. Keep at least
20% free space; store long-term PostgreSQL backups in encrypted, versioned S3
storage rather than relying only on the instance disk. The Compose bind mounts
are under `/opt/docker/apache-superset` as described below.

Recommended security-group rules:

- TCP/22: only from the administrative network or bastion/gateway;
- TCP/8088: only from the external reverse-proxy host/security group;
- TCP/5008: only from the reverse-proxy host/security group for MCP;
- TCP/5432 and TCP/6379: no inbound public access;
- outbound TCP/5432: allow to the PostgreSQL RDS security group;
- normal outbound HTTPS/DNS: allow for package/image pulls and integrations.

Do not expose Superset or MCP directly to the internet. TLS terminates on the
separate reverse proxy; the proxy forwards Superset to port 8088 and MCP to
`/mcp` on port 5008. If the RDS database is reachable only through a private
network, place the EC2 host in the appropriate VPC/subnets and security groups.

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
Also set `SUPERSET_PUBLIC_URL` and `MCP_SERVICE_URL` to the public URLs served by
the reverse proxy. `MCP_SERVICE_URL` is the public base URL without `/mcp`. For
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

<details>
<summary>Changing <code>.env</code> after the first start</summary>

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
- MCP listener and public URL: `MCP_BIND_ADDRESS`, `MCP_PORT`,
  `MCP_SERVICE_HOST`, `MCP_SERVICE_PORT`, and `MCP_SERVICE_URL`.
- MCP JWT policy: `MCP_JWT_ISSUER`, `MCP_JWT_AUDIENCE`, and
  `MCP_REQUIRED_SCOPES`. Keep `MCP_AUTH_ENABLED=true` and
  `MCP_RBAC_ENABLED=true` in production.
- Embedded dashboards: `SUPERSET_EMBED_ALLOWED_ORIGINS`, `GUEST_ROLE_NAME`, and
  `GUEST_TOKEN_JWT_EXP_SECONDS`. Keep the generated
  `GUEST_TOKEN_JWT_SECRET`; it signs Superset guest tokens and must not be
  exposed to a browser or committed to Git.

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

</details>

<details>
<summary>External reverse proxy and direct HTTP testing</summary>

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
MCP_SERVICE_URL=http://YOUR_SERVER_IP:5008
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

</details>

## Embedded dashboards

The deployment enables the official Superset Embedded SDK flow. It uses a
short-lived guest token issued by the host application's backend; it does not
make dashboards public or allow the frontend to call Superset's guest-token API
directly.

Before enabling embedding for a dashboard, configure all of the following:

1. Set `SUPERSET_EMBED_ALLOWED_ORIGINS` to a comma-separated list of exact
   scheme/host/port origins that may frame Superset. Do not use wildcards or an
   empty allowlist. The example includes only local development origins.
2. Configure the dashboard's own embedded settings with the same exact allowed
   origins and record its generated embedded UUID. This is dashboard-specific.
3. Create the configured `GUEST_ROLE_NAME` with only the required viewer and
   dashboard-interaction permissions. Do not use `Public`, Gamma, or Admin as
   the guest role.
4. Create a least-privilege service account that can request guest tokens. The
   host backend must authorize its current user and select a dashboard from a
   server-side allowlist before requesting a token. Never accept an arbitrary
   dashboard UUID from the browser.

`GUEST_TOKEN_JWT_SECRET` is generated by `init-env.sh` for new deployments. On
an existing deployment, generate a new independent random value and place it
only in its protected `.env` before recreating Superset services. Guest tokens
default to five minutes (`GUEST_TOKEN_JWT_EXP_SECONDS=300`).

Superset's global CSRF protection remains enabled. The deployment exempts only
`superset.security.api.guest_token`, because the host application's backend
uses a JWT bearer token rather than a browser session to request a guest token.
Do not add broader CSRF exemptions or disable CSRF globally. Restrict this
endpoint at the reverse proxy to the host application's backend once its
network path is known.

The default `SESSION_COOKIE_SAMESITE=Lax` is intentionally retained. The
guest-token SDK flow should not need a cross-site Superset session cookie; only
change it after a browser test demonstrates a concrete requirement.

<details>
<summary>Bootstrap the Embed guest role</summary>

The configured `GUEST_ROLE_NAME` needs the read-only UI and API permissions
that Superset uses for embedded dashboards. This includes chart data requests,
native dashboard-filter state, and time-range selectors. It does **not** grant
direct database, schema, datasource, SQL Lab, user-management, or
configuration access.

Run this after `superset init`. It is idempotent: it creates `Embed` when
needed and adds any absent permissions on subsequent runs. The command refuses
to proceed if the `Public` role has direct data-access permissions, since that
would make guest embedding unsafe. Superset installations without the optional
annotation-layer API deliberately skip that one permission.

```bash
cd /opt/docker/apache-superset

docker compose exec -T superset-web superset shell <<'PY'
exec(r'''
from superset import db, security_manager

ROLE_NAME = "Embed"
OPTIONAL_ABSENT = {
    ("can_read", "AnnotationLayerRestApi"),
}

role = security_manager.find_role(ROLE_NAME)
if role is None:
    role = security_manager.add_role(ROLE_NAME)
    db.session.flush()
    print(f"Created role: {ROLE_NAME}")

public_role = security_manager.find_role("Public")
if public_role is None:
    raise RuntimeError("Public role was not found")

data_access_permissions = {
    "database_access",
    "schema_access",
    "datasource_access",
    "all_database_access",
    "all_datasource_access",
    "all_query_access",
}

public_data_access = sorted(
    (pv.permission.name, pv.view_menu.name)
    for pv in public_role.permissions
    if pv.permission.name in data_access_permissions
)
if public_data_access:
    raise RuntimeError(
        "Public role has data-access permissions; refusing to continue: "
        f"{public_data_access}"
    )

required = sorted(security_manager.PUBLIC_ROLE_PERMISSIONS)
missing = [
    (permission_name, view_name)
    for permission_name, view_name in required
    if security_manager.find_permission_view_menu(permission_name, view_name) is None
]

unexpected_missing = sorted(set(missing) - OPTIONAL_ABSENT)
if unexpected_missing:
    raise RuntimeError(
        "Unexpected missing permissions; run `superset init` and investigate: "
        f"{unexpected_missing}"
    )

for permission_name, view_name in required:
    if (permission_name, view_name) in OPTIONAL_ABSENT:
        print(
            "Skipped unavailable optional permission: "
            f"{permission_name} on {view_name}"
        )
        continue

    pv = security_manager.find_permission_view_menu(permission_name, view_name)
    if pv not in role.permissions:
        security_manager.add_permission_role(role, pv)
        print(f"Granted: {permission_name} on {view_name}")
    else:
        print(f"Already present: {permission_name} on {view_name}")

db.session.commit()

print()
print(f"Role ready: {role.name}")
print(f"Permissions: {len(role.permissions)}")
print("Public data access: none")
print("Range selectors and native dashboard filters: enabled")
''')
PY
```

Do not add `database_access`, `schema_access`, `datasource_access`, or SQL Lab
permissions to this role. Access to an embedded dashboard and its data must be
scoped by the short-lived guest token issued by the host application's backend.

</details>

<details>
<summary>Bootstrap the guest-token issuer role and service account</summary>

The host application's backend, not its browser frontend, requests guest
tokens from Superset. Create a dedicated issuer role with exactly the
`can_grant_guest_token` permission:

```bash
cd /opt/docker/apache-superset

docker compose exec -T superset-web superset shell <<'PY'
exec(r'''
from superset import db, security_manager

ROLE_NAME = "Kaktus-Embed-Token-Issuer"
REQUIRED = (
    ("can_grant_guest_token", "SecurityRestApi"),
)

role = security_manager.find_role(ROLE_NAME)
if role is None:
    role = security_manager.add_role(ROLE_NAME)
    db.session.flush()
    print(f"Created role: {ROLE_NAME}")

for permission_name, view_name in REQUIRED:
    pv = security_manager.find_permission_view_menu(permission_name, view_name)
    if pv is None:
        raise RuntimeError(
            f"Missing permission: {permission_name} on {view_name}. "
            "Run `superset init` and investigate."
        )

    if pv not in role.permissions:
        security_manager.add_permission_role(role, pv)
        print(f"Granted: {permission_name} on {view_name}")
    else:
        print(f"Already present: {permission_name} on {view_name}")

db.session.commit()

actual = sorted(
    (pv.permission.name, pv.view_menu.name)
    for pv in role.permissions
)
print()
print(f"Role ready: {role.name}")
print(f"Permissions: {actual}")
''')
PY
```

Create a dedicated database-authenticated Superset user (for example,
`kaktus-embed`) with a randomly generated password stored only in the host
application's secret manager. Assign it only the
`Kaktus-Embed-Token-Issuer` role; do not assign `Admin`, `Embed`, `Manager`,
Gamma, database access, or SQL Lab permissions. If the deployment assigns the
`Public` role automatically, keep that role free of all data-access
permissions.

The issuer role needs only `can_grant_guest_token` on `SecurityRestApi`. Do not
grant `can_read` merely to obtain a CSRF token: this deployment exempts only
the backend-only guest-token endpoint from CSRF while keeping global CSRF
protection enabled.

In Superset 6.1 the issuer permission allows requesting a token for an
existing embedded dashboard UUID; it does not restrict issuance to dashboards
the issuer can view. Therefore the host backend must keep a server-side
allowlist mapping every permitted embed location to its dashboard UUID. In
Superset 6.1 guest tokens do not accept an explicit dataset allowlist: the
guest can access only datasets actually used by charts in the authorized
dashboard. Never accept a UUID, RLS clause, or token parameters from the
browser.

</details>

## MCP server

The stack includes `superset-mcp` as a separate service, as required by the
Superset MCP deployment model. It listens on TCP/5008 and exposes the
Streamable HTTP endpoint at `/mcp`; it does not share the web process on 8088.
The service uses the same metadata database, Superset secret, Redis instance,
and RBAC permissions as the web application. Redis database 5 is reserved for
the MCP event store so sessions, Celery, caches, and MCP events remain isolated.

MCP is built into the custom image with the Superset 6.1-compatible
`fastmcp==3.1.0` pin. Build and start it with the rest of the stack:

```bash
docker compose config --quiet
docker compose build --pull
docker compose up -d
docker compose ps superset-mcp
docker compose logs --tail=200 superset-mcp
```

The Compose healthcheck verifies that the MCP process is listening on its port.
For an authenticated application-level check, connect an MCP client and call
the built-in `health_check` tool; the MCP service intentionally has no
unauthenticated HTTP health endpoint.

### Reverse proxy requirements

Expose only the reverse proxy publicly. Route the public `/mcp` path to
`http://<docker-host-private-ip>:5008/mcp` and preserve the `Authorization`
header. The proxy must support long-lived Streamable HTTP connections, disable
response buffering for this location, and use a generous read timeout (for
example, 3600 seconds). Restrict inbound TCP/5008 on the Docker host or AWS
security group to the reverse-proxy host. The MCP URL clients use is the
public URL, for example `https://superset.example.com/mcp`.

### Authentication and Superset users

JWT authentication is enabled by default. `init-env.sh` generates a random
`MCP_JWT_SECRET` and stores it only in `.env` (mode 600). The default mode is
HS256, with issuer `superset-mcp`, audience `superset-mcp`, and required scope
`superset:mcp`. Every token must contain:

- `sub`: an existing Superset username (not the RDS database username);
- `iss`: the configured `MCP_JWT_ISSUER`;
- `aud`: the configured `MCP_JWT_AUDIENCE`;
- `scope`: including `superset:mcp`;
- a short-lived `exp`.

Create a dedicated least-privilege Superset service user for MCP:

1. Open `Settings -> User Management -> + User` in Superset.
2. Create a service account, for example `mcp-agent`, with a random password.
3. Create a custom role named `MCP-Agent` and assign it to the service user.
4. Run the idempotent command below from the Docker host. It adds the model,
   SQL Lab, database, and data-access permissions used by the MCP tools without
   granting user, role, or database-connection administration.

```bash
cd /opt/docker/apache-superset

docker compose exec -T superset-web superset shell <<'PY'
from superset import db, security_manager
from superset.models.core import Database

ROLE_NAME = "MCP-Agent"
DATABASE_NAME = "PostgreSQL"  # Superset connection name, not the RDS DB name

# Permissions used by the MCP chart, dashboard, dataset, database, and SQL tools.
MODEL_PERMISSIONS = (
    ("can_read", "Chart"),
    ("can_write", "Chart"),
    ("can_read", "Dashboard"),
    ("can_write", "Dashboard"),
    ("can_read", "Database"),
    ("can_read", "Dataset"),
    ("can_write", "Dataset"),
    ("can_read", "SavedQuery"),
    ("can_write", "SavedQuery"),
    ("can_read", "SQLLab"),
    ("can_write", "SQLLab"),
    ("can_execute_sql_query", "SQLLab"),
)

role = security_manager.find_role(ROLE_NAME)
if role is None:
    raise RuntimeError(
        f"Role {ROLE_NAME!r} does not exist. Create it in Superset first."
    )

database = (
    db.session.query(Database)
    .filter(Database.database_name == DATABASE_NAME)
    .one_or_none()
)
if database is None:
    raise RuntimeError(
        f"Superset database connection {DATABASE_NAME!r} was not found."
    )

def grant(permission_name, view_name):
    permission_view = security_manager.find_permission_view_menu(
        permission_name, view_name
    )
    if permission_view is None:
        permission_view = security_manager.add_permission_view_menu(
            permission_name, view_name
        )
    if permission_view not in role.permissions:
        security_manager.add_permission_role(role, permission_view)
        print(f"Granted: {permission_name} on {view_name}")
    else:
        print(f"Already present: {permission_name} on {view_name}")

for permission_name, view_name in MODEL_PERMISSIONS:
    grant(permission_name, view_name)

# Grants access to all current and future datasets belonging to this connection.
grant("database_access", database.perm)

db.session.commit()
print(f"Role ready: {role.name}; database access: {database.database_name}")
PY
```

The command is safe to run repeatedly. It does not remove existing permissions.
`DATABASE_NAME` is the Superset connection name shown under **Settings →
Database Connections**; it is not the PostgreSQL/RDS database name such as
`kaktus_rds`. The `database_access` permission covers the connection's datasets,
so individual `datasource_access` entries are not required for this setup.
`can_write` on `Dataset` changes only Superset metadata: it allows the MCP user
to create or edit virtual-dataset definitions. It does not grant `INSERT`,
`UPDATE`, `DELETE`, DDL, or any other write access in the RDS source. Those SQL
rights are controlled independently by the database user configured in the
Superset connection; use a read-only RDS user such as `superset_ro`.
If the service user currently also has `Gamma`, remove `Gamma` after verifying
the MCP workflow so the account retains only the intended custom role.

The MCP tools bundled with Superset 6.1.0 do not expose Alert/Report creation
tools. Enabling Alerts and Reports in the stack does not add those tools to MCP;
their separate UI/API permissions should only be added if that workflow is
explicitly enabled.

Use the exact service username as the JWT `sub` claim. Do not use the `admin`
account or the RDS database username. RBAC is enforced by Superset, so the
service user can only perform operations allowed by its Superset roles.

#### Superset 6.1.0 fixed-user workaround

The upstream 6.1.0 MCP implementation validates the JWT but does not yet
resolve its `sub` claim into the Superset `g.user` context. For a single MCP
agent, set the existing least-privilege username in `.env`:

```dotenv
MCP_DEV_USERNAME=mcp-agent
```

This keeps JWT authentication and RBAC enabled, but all authenticated MCP
calls execute as that one configured Superset user. It is suitable for one
service account only; do not use it for multiple independent users. After
changing it, rebuild and recreate only the MCP service:

```bash
docker compose build superset-mcp
docker compose up -d --force-recreate superset-mcp
```

Remove `MCP_DEV_USERNAME` when moving to a Superset version with JWT-to-user
resolution, so the JWT `sub` claim can identify each user independently.

Generate a one-hour JWT on the Docker host. Run this from the stack directory;
the command reads `MCP_JWT_SECRET` inside the container and never prints it:

```bash
cd /opt/docker/apache-superset
docker compose exec -T -e MCP_USER=mcp-agent superset-mcp python -c '
import os, time, jwt
now = int(time.time())
claims = {"sub": os.environ["MCP_USER"], "iss": os.environ["MCP_JWT_ISSUER"],
          "aud": os.environ["MCP_JWT_AUDIENCE"], "scope": "superset:mcp",
          "iat": now, "exp": now + 3600}
print(jwt.encode(claims, os.environ["MCP_JWT_SECRET"], algorithm="HS256"))
'
```

Copy the resulting token to the MCP client as a bearer token. No container
rebuild or restart is required after creating the Superset user or generating
the token. Generate a new token after it expires; changing the JWT secret,
issuer, audience, or required scopes invalidates existing tokens.

For a real production deployment with an identity provider, prefer RS256:
set `MCP_JWT_ALGORITHM=RS256` and configure either `MCP_JWKS_URI` or
`MCP_JWT_PUBLIC_KEY`. Keep `MCP_AUTH_ENABLED=true`; the documented no-auth
development mode must not be exposed to a network.

<details>
<summary>Existing <code>.env</code> migration after MCP was added</summary>

### Existing `.env` migration

If this stack was created before MCP was added, do not delete `.env` and do not
regenerate the existing Superset, PostgreSQL, Redis, or administrator secrets.
Add the following values manually, using a new random value for the JWT secret:

```dotenv
MCP_BIND_ADDRESS=0.0.0.0
MCP_PORT=5008
MCP_SERVICE_HOST=0.0.0.0
MCP_SERVICE_PORT=5008
MCP_SERVICE_URL=https://superset.example.com
MCP_AUTH_ENABLED=true
MCP_JWT_ALGORITHM=HS256
MCP_JWT_SECRET=<output of: openssl rand -hex 32>
MCP_JWT_ISSUER=superset-mcp
MCP_JWT_AUDIENCE=superset-mcp
MCP_REQUIRED_SCOPES=superset:mcp
MCP_RBAC_ENABLED=true
MCP_STORE_REDIS_DB=5
```

Then rebuild and recreate the stack:

```bash
chmod 600 .env
docker compose config --quiet
docker compose build --pull
docker compose up -d --force-recreate
docker compose ps superset-mcp
```

Changing `MCP_JWT_SECRET`, issuer, audience, or required scopes invalidates
existing MCP tokens. The Superset account and its RBAC role remain unchanged.

</details>

<details>
<summary>Alerts and Reports</summary>

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

</details>

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

<details>
<summary>Persistence and backup</summary>

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

</details>

<details>
<summary>Operations and upgrades</summary>

## Operations

View logs:

```bash
docker compose logs -f --tail=200 \
  superset-web superset-mcp superset-worker superset-reports-worker superset-beat
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

</details>

<details>
<summary>Bootstrap a Manager role</summary>

## Manager role

The following idempotent runbook creates or updates a non-administrative
`Manager` role. It can create and edit dashboards, charts, datasets, saved
queries, SQL Lab queries, and alerts/reports; it can use exactly one existing
database connection but cannot administer database connections, users, roles,
or groups. It does not remove any existing role permissions.

Pass the display name of the existing Superset database connection through the
shell environment. This value is deliberately not stored in the repository.

```bash
printf 'Existing Superset database connection name: ' >&2
IFS= read -r DATABASE_NAME
export DATABASE_NAME

docker compose exec -T -e DATABASE_NAME superset-web superset shell <<'PY'
import os

from superset import db, security_manager
from superset.models.core import Database

ROLE_NAME = "Manager"
DATABASE_NAME = os.getenv("DATABASE_NAME", "").strip()

if not DATABASE_NAME:
    raise RuntimeError("DATABASE_NAME must name an existing Superset connection")

REQUIRED = (
    # SQL Editor state
    ("can_get", "TabStateView"),
    ("can_post", "TabStateView"),
    ("can_put", "TabStateView"),
    ("can_activate", "TabStateView"),
    ("can_migrate_query", "TabStateView"),
    ("can_delete", "TabStateView"),
    ("can_delete_query", "TabStateView"),
    # Charts, dashboards, and datasets
    ("can_read", "Chart"),
    ("can_write", "Chart"),
    ("can_read", "Dashboard"),
    ("can_write", "Dashboard"),
    ("can_read", "Dataset"),
    ("can_write", "Dataset"),
    ("can_duplicate", "Dataset"),
    ("can_get_or_create_dataset", "Dataset"),
    # SQL Lab and saved queries
    ("can_read", "SQLLab"),
    ("can_write", "SQLLab"),
    ("can_execute_sql_query", "SQLLab"),
    ("can_get_results", "SQLLab"),
    ("can_read", "Query"),
    ("can_write", "Query"),
    ("can_read", "SavedQuery"),
    ("can_write", "SavedQuery"),
    # Database and datasource metadata
    ("can_read", "Database"),
    ("can_read", "Explore"),
    ("can_get", "Datasource"),
    ("can_samples", "Datasource"),
    ("can_get_column_values", "Datasource"),
    ("can_external_metadata", "Datasource"),
    # Superset features
    ("can_explore", "Superset"),
    ("can_explore_json", "Superset"),
    ("can_dashboard", "Superset"),
    ("can_sqllab", "Superset"),
    ("can_sqllab_history", "Superset"),
    # Alerts, activity, profile, and dashboard time range
    ("can_read", "ReportSchedule"),
    ("can_write", "ReportSchedule"),
    ("can_recent_activity", "Log"),
    ("can_read", "user"),
    ("can_time_range", "Api"),
    ("can_this_form_get", "ResetMyPasswordView"),
    ("can_this_form_post", "ResetMyPasswordView"),
    ("can_this_form_get", "UserInfoEditView"),
    ("can_this_form_post", "UserInfoEditView"),
    ("can_userinfo", "UserDBModelView"),
    ("resetmypassword", "UserDBModelView"),
)

MENUS = (
    "Alerts", "Alerts & Report", "Charts", "Dashboards", "Data",
    "Databases", "Datasets", "Manage", "Query Search", "SQL Editor",
    "SQL Lab", "Saved Queries",
)

role = security_manager.find_role(ROLE_NAME)
if role is None:
    role = security_manager.add_role(ROLE_NAME)
    db.session.flush()
    print(f"Created role: {ROLE_NAME}")

database = (
    db.session.query(Database)
    .filter(Database.database_name == DATABASE_NAME)
    .one_or_none()
)
if database is None:
    raise RuntimeError(f"Superset connection {DATABASE_NAME!r} was not found")

def grant(permission_name, view_name):
    pv = security_manager.find_permission_view_menu(permission_name, view_name)
    if pv is None:
        raise RuntimeError(
            f"Missing Superset permission {permission_name!r} on {view_name!r}; "
            "run `superset init` after reviewing the installed version"
        )
    if pv not in role.permissions:
        security_manager.add_permission_role(role, pv)
        print(f"Granted: {permission_name} on {view_name}")
    else:
        print(f"Already present: {permission_name} on {view_name}")

for permission_name, view_name in REQUIRED:
    grant(permission_name, view_name)
for menu_name in MENUS:
    grant("menu_access", menu_name)
grant("database_access", database.perm)

db.session.commit()
print(f"Role ready: {role.name}; permissions: {len(role.permissions)}")
print(f"Database access granted: {database.database_name}")
print("No permissions were removed")
PY
```

The script intentionally fails if a required permission is absent instead of
creating a synthetic permission/view pair. Review the installed Superset version
and run `superset init` separately if permissions need to be synchronized.

</details>

<details>
<summary>Official references</summary>

## Official references

- [Superset 6.1.0 installation methods](https://superset.apache.org/admin-docs/6.1.0/installation/installation-methods/)
- [Superset 6.1.0 Docker builds](https://superset.apache.org/admin-docs/6.1.0/installation/docker-builds/)
- [Superset 6.1.0 configuration](https://superset.apache.org/admin-docs/6.1.0/configuration/configuring-superset/)
- [Superset 6.1.0 caching](https://superset.apache.org/admin-docs/6.1.0/configuration/cache/)
- [Superset 6.1.0 async queries](https://superset.apache.org/admin-docs/6.1.0/configuration/async-queries-celery/)
- [Superset production security guide](https://superset.apache.org/admin-docs/6.1.0/security/securing_superset/)
- [Superset MCP server configuration](https://superset.apache.org/admin-docs/configuration/mcp-server/)
- [Superset 6.1.0 using AI/MCP](https://superset.apache.org/user-docs/6.1.0/using-superset/using-ai-with-superset/)

</details>
