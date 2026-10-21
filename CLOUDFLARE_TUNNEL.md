# Cloudflare Tunnel for Immich

This directory already runs Immich on the Docker Compose network named by the
`immich` project. The `docker-compose.cloudflare.yml` override adds only the
`cloudflared` connector.

## Cloudflare dashboard setup

1. In Cloudflare Zero Trust, go to `Networks` > `Tunnels`.
2. Create a tunnel, choose Docker as the environment, and copy the tunnel token.
3. Add a public hostname route for your Immich hostname.
4. Set the route service URL to:

```text
http://immich-server:2283
```

Use the Docker service name above, not `localhost`, because `cloudflared` runs
inside the Compose network.

## Local setup

Add the token to `.env`:

```env
CLOUDFLARE_TUNNEL_TOKEN=replace_with_cloudflare_tunnel_token
```

Start or update the connector:

```bash
sudo systemctl restart immich.service
```

Check status and logs:

```bash
docker compose -f docker-compose.yml -f docker-compose.cloudflare.yml ps
docker compose -f docker-compose.yml -f docker-compose.cloudflare.yml logs -f cloudflared
```

Immich itself remains available locally at:

```text
http://localhost:2283
```
