# docker-restarter 🔄️

### Sample usage

`docker-compose.yml`

```yaml
services:
  restarter:
    image: ghcr.io/cascandaliato/docker-restarter    
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock:ro

  # dummy service that crashes every 5 seconds
  vpn:
    image: alpine
    command: sleep 5

  # dummy service that depends on another service
  torrent:
    image: alpine
    command: sleep infinite
    container_name: torrent
    depends_on:
      - vpn
```

Command line:
```bash
$ docker compose up -d
...
$ docker logs -f restarter
Container torrent depends on service vpn
Restarting the following container(s) in 10 seconds because container vpn (re)started: torrent
...
```
