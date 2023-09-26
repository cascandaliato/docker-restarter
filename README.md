# docker-restarter 🔄️

### Sample usage

`docker-compose.yml`

```yaml
services:
  restarter:
    image: ghcr.io/cascandaliato/docker-restarter    
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock:ro

  # dummy service that crashes every 60 seconds
  vpn:
    image: alpine
    command: sleep 60
    container_name: vpn

  # dummy service that depends on the crashing service
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
Initialization completed
Containers depending on service vpn:
  torrent (id 7bc6728bfd54, service torrent)
... after 60 seconds ...
Container vpn (id ad5903f2be77, service vpn) restarted
The following containers depend on service vpn and will be restarted in 30 seconds:
  torrent (id 7bc6728bfd54, service torrent)
... after 30 seconds ...
Restarting container torrent (id 7bc6728bfd54, service torrent)
```