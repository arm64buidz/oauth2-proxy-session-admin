# oauth2-proxy-session-admin

A lightweight FastAPI service that provides a **web UI and REST API for managing oauth2-proxy sessions** stored in Redis. Designed to work alongside [oauth2-proxy-authz](https://github.com/arm64buidz/oauth2-proxy-authz), [Pocket ID](https://github.com/pocket-id/pocket-id), and oauth2-proxy as part of a self-hosted SSO stack.

> **Dependency:** This container requires [oauth2-proxy-authz](https://github.com/arm64buidz/oauth2-proxy-authz) to be running first. It reads from the same Redis instance and will not function without it.

---

## Dashboard

![Session Manager dashboard showing active sessions with user details, device info, expiry timers, and block/revoke controls](screenshot.png)

---

## How It Works

The session admin UI is exposed on the same `auth.` subdomain as the rest of the stack — protected by both oauth2-proxy authentication and an `administrator` group check via authz.

```
Browser → auth.example.duckdns.org/admin-portal
              └─▶ Traefik
                    ├─▶ oauth2-auth (must be logged in)
                    ├─▶ authz-group-admin (must be in 'administrator' group)
                    └─▶ session-admin:8080
```

Sessions written to Redis by oauth2-proxy are read and displayed in the UI. Revoking a session takes effect immediately — the user's next request will be rejected by oauth2-proxy.

---

## Prerequisites

- [oauth2-proxy-authz](https://github.com/arm64buidz/oauth2-proxy-authz) — **required**
- [oauth2-proxy](https://github.com/oauth2-proxy/oauth2-proxy)
- [Pocket ID](https://github.com/pocket-id/pocket-id)
- [Redis](https://redis.io/) — must be the same instance used by oauth2-proxy and oauth2-proxy-authz
- [Traefik](https://traefik.io/)

---

## Quick Start

### 1. Clone the repo

```bash
git clone https://github.com/arm64buidz/oauth2-proxy-session-admin.git
cd oauth2-proxy-session-admin
```

### 2. Configure your environment

```bash
cp .env.example .env
```

| Variable          | Description                                                            | Example                             |
|-------------------|------------------------------------------------------------------------|-------------------------------------|
| `REDIS_URL`       | Redis connection string — must match oauth2-proxy's Redis instance     | `redis://oauth2-proxy-redis:6379`   |
| `SESSION_PREFIX`  | Key prefix oauth2-proxy uses when writing sessions to Redis            | `_oauth2_proxy-`                    |
| `CORS_ORIGINS`    | Allowed CORS origin — should match your Pocket ID / auth subdomain URL | `https://auth.example.duckdns.org`  |

### 3. Start the stack

Ensure `oauth2-proxy-authz` and Redis are healthy first. The example `docker-compose.yaml` handles this with `depends_on`.

```bash
docker compose up -d
```

---

## Traefik Integration

The session admin service runs on port `8080` and is served under the same `auth.` subdomain as Pocket ID. Traefik routes to it by path prefix at a higher priority than the Pocket ID catch-all router.

```yaml
http:
  routers:
    session_admin_router:
      rule: "Host(`auth.example.duckdns.org`) && (PathPrefix(`/admin-portal`) || PathPrefix(`/api/sessions`) || PathPrefix(`/api/stats`))"
      service: session_admin_service
      priority: 250
      middlewares:
        - oauth2-auth         # must be authenticated
        - authz-group-admin   # must be in 'administrator' group
      tls:
        certResolver: duckdnsresolver

  services:
    session_admin_service:
      loadBalancer:
        servers:
          - url: http://session-admin:8080
```

See `example-traefik.yaml` for the full working configuration including the oauth2 callback router and certificate resolver setup.

---

## Available Paths

| Path               | Description                        |
|--------------------|------------------------------------|
| `/admin-portal`    | Web UI                             |
| `/api/sessions`    | REST API — list/revoke sessions    |
| `/api/stats`       | REST API — session statistics      |

All paths require authentication and membership in the `administrator` group.

---

## Service Ports

| Service                      | Port   |
|------------------------------|--------|
| Pocket ID                    | `1411` |
| oauth2-proxy                 | `4180` |
| oauth2-proxy-authz           | `8080` |
| oauth2-proxy-session-admin   | `8080` |

---

## Related Projects

- [oauth2-proxy-authz](https://github.com/arm64buidz/oauth2-proxy-authz) — group-based authorization sidecar (**required**)
- [oauth2-proxy](https://github.com/oauth2-proxy/oauth2-proxy)
- [Pocket ID](https://github.com/pocket-id/pocket-id)

---

## License

MIT
