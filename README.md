# docker-restarter 🐋♻️

### Sample usage

`docker-compose.yml`

```yaml
services:
  restarter:
    image: ghcr.io/cascandaliato/docker-restarter
    container_name: restarter
    init: true
    # environment:
    #   RESTARTER_CHECK_EVERY_SECONDS: 60
    #   RESTARTER_GC_EVERY_SECONDS: 300
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock:ro

  # will restart every two minutes
  vpn:
    image: alpine
    container_name: vpn
    command: sleep 120
    init: true
    restart: always

  torrent:
    container_name: torrent
    image: alpine
    init: true
    command: sh -c 'apk add curl; sleep infinite'
    network_mode: &vpn_network service:vpn
    labels:
      restarter.network_mode: *vpn_network
    healthcheck:
      test: "curl -sf http://ipinfo.io/ip  || exit 1"
      interval: 10s
      start_period: 1s
```

In the example above, `docker-restarter` will restart containers `vpn` and `torrent` in the following cases:
  - `vpn` restarts (crash, `docker restart`, etc.);
  - `vpn` gets replaced with a new instance (`watchtower` update), in this case `torrent` will be recreated to refer the most recent instance of `vpn`;
  - `torrent` becomes `unhealthy`.

  ### Settings

  The following settings are available as labels defined at container level:

  | Setting | Description | Default value | Valid values |
  | ------- | ----------- | ------------- | -------------|
  | `restarter.enable` | Enable automated restarts. | `yes` | `yes`, `no`, `true`, `false` |
  | `restarter.network_mode` | This setting should match the container `network_mode` as defined in `docker-compose.yaml`. Required to recreate a child container (`torrent`) if the parent container (`vpn`) gets replaced, e.g. after a `watchtower` update. | not set | `service:<service_name>`, for example `service:vpn` |
| `restarter.policy` | List of scenarios in which the container should be restarted. | `dependency,unhealthy` | Comma-separated list of any combination of the following: `dependency` (restart if the service defined via `restarter.network_mode` restarts), `unhealthy` (restart if this container becomes unhealthy). |

These settings can be set at global level via similarly named environment variables:
  - `RESTARTER_ENABLE` (by default `docker-restarter` is enabled on all containers)
  - `RESTARTER_NETWORK_MODE` (_not very useful_)
  - `RESTARTER_POLICY`

_Note: all values are case-insensitive._