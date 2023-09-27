import threading

from restarter.config import Setting, with_defaults


class Container:
    _timer: threading.Timer | None
    _retries_count: int = 0

    def __init__(self, *, id: str, name: str, service: str, config, docker_client):
        self.id = id
        self.name = name
        self.service = service
        self._config = with_defaults(config)
        self._docker_client = docker_client

    def __lt__(self, other):
        return self.name < other.name

    def __hash__(self):
        return hash(self.name)

    def __eq__(self, other):
        return self.name == other.name

    def __repr__(self):
        return f"{self.name} (id {self.id[:12]}, service {self.service})"

    def _restart(self):
        try:
            if c := self._docker_client.containers.get(self.id):
                print(f"Restarting container {self}")
                c.restart()
            else:
                raise Exception(f"Cannot find container {self}")
        except Exception as e:
            print(f"Could not restart container {self} because of error: {e}")

    def restart(self):
        self._retries_count += 1

        if self._retries_count > self._config[Setting.MAX_RETRIES]:
            print(
                "Container {self} exceeded the number of maximum restarts ({self._config[Setting.MAX_RETRIES]}) so it won't be restarted"
            )
            return

        delay = self._config[Setting.AFTER_SECONDS]
        if self._config[Setting.BACKOFF] == "linear":
            delay *= self._retries_count
        elif self._config[Setting.BACKOFF] == "exponential":
            delay *= 2 ** (self._retries_count - 1)

        print(
            f"Restarting container {self} in {self._config[Setting.AFTER_SECONDS]} seconds"
        )
        self._timer = threading.Timer(delay, self._restart)
        self._timer.daemon = False
        self._timer.start()


class Service(set):
    def __init__(self, iter=(), *, name: str):
        super().__init__(iter)
        self.name = name

    def restart(self):
        for container in self:
            container.restart()
