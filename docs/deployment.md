# Deployment

Run Company Core behind HTTPS. SQLite is suitable for a single application process; use one
worker or migrate the stores before horizontal scaling.

## Native service

```bash
make setup
.venv/bin/uvicorn app.api:app --host 127.0.0.1 --port 8787
```

Use systemd, Supervisor, or another process manager to keep the command running. Put Caddy,
Nginx, or Cloudflare Tunnel in front of `http://127.0.0.1:8787`.

## Docker Compose

Create local secrets before starting Compose:

```bash
make init
docker compose up --build -d
docker compose logs -f company-core
```

The Compose service persists `data/`, `projects/`, and `config/` on the host. If OmniRoute or Ollama
runs outside the container, do not assume `127.0.0.1` reaches the host. Use a private reachable host
address, `host.docker.internal` where supported, or put both services on the same Compose network
and use the model service name. Do not expose an unauthenticated model endpoint publicly.

## Cloudflare Tunnel

Create a remotely managed tunnel in Cloudflare Zero Trust and add a public hostname whose service
is `http://localhost:8787`. Install one connector service per host; one connector can route several
hostnames to different local services. Store the tunnel token outside this repository.

Example ingress for a locally managed tunnel:

```yaml
tunnel: YOUR_TUNNEL_ID
credentials-file: /secure/path/YOUR_TUNNEL_ID.json
ingress:
  - hostname: sales.example.com
    service: http://localhost:8787
  - hostname: calendar.example.com
    service: http://localhost:3000
  - service: http_status:404
```

Set `COMPANY_PUBLIC_URL=https://sales.example.com` and restart the application. Protect operator
routes with strong dashboard credentials and, where appropriate, Cloudflare Access.
