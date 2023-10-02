# docker-restarter 🐋♻️

### Sample usage

`docker-compose.yml`

```yaml
services:
  restarter:
    image: ghcr.io/cascandaliato/docker-restarter
    container_name: restarter
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock:ro

  # dummy service that could crash and restart or get replaced (e.g. when updated by watchtower)
  vpn:
    container_name: vpn
    image: alpine
    command: sleep infinite

  # dummy service that depends on the crashing service
  # this service needs:
  #   - a label "restarter.depends_on_service" pointing to the same service specified in network_mode
  #   - a healthcheck similar to the one below
  torrent:
    container_name: torrent
    image: alpine
    command: sleep infinite
    network_mode: service:vpn
    labels:
      restarter.depends_on_service: vpn
    healthcheck:
      test: "curl -sf http://ipinfo.io/ip  || exit 1"
      interval: 1m
      timeout: 0s
      retries: 0
```

Start the full stack with
```
docker compose up -d
```

If you restart `vpn`, for example
```
docker restart vpn
```
or
```
docker rm -f vpn; docker compose up -d vpn
```
then `docker-restarter` will restart the container `torrent`:
```bash
$ docker logs -f restarter
Container torrent (id 4547ccd5d4ea) successfully removed
tarted new container torrent (id 40fb83eee4b2)
```