import threading
from collections import defaultdict
from dataclasses import dataclass

import docker

# Docker Compose labels
DEPENDS_ON = "com.docker.compose.depends_on"
SERVICE = "com.docker.compose.service"


@dataclass(frozen=True, kw_only=True, order=True, repr=False)
class Container:
    name: str
    id: str
    service: str | None

    def __repr__(self) -> str:
        return f"{self.name} (id {self.id[:12]}, service {self.service})"


class Service:
    def __init__(self, name: str):
        self.name: str = name
        self._containers: set[Container] = set()
        self._lock: threading.Lock = threading.Lock()
        self._timer: threading.Timer | None = None

    def add(self, container: Container) -> None:
        with self._lock:
            self._containers.add(container)

    def remove(self, container: Container) -> None:
        with self._lock:
            self._containers.discard(container)

    def __iter__(self):
        with self._lock:
            yield from sorted(self._containers)

    def __bool__(self):
        with self._lock:
            return len(self._containers) > 0

    def _restart(self):
        for container in self:
            print(f"Restarting container {container}")
            try:
                if c := docker_client.containers.get(container.id):
                    c.restart()  # type: ignore
                else:
                    raise Exception(f"Cannot find container {container}")
            except Exception as e:
                print(f"Could not restart container {container} because of error: {e}")

    def restart(self):
        if self._timer and not self._timer.finished.is_set():
            print(
                f"Cancelling the most recently scheduled restart of containers depending on service {self.name}"
            )
            self._timer.cancel()

        print(
            f"The following containers depend on service {self.name} and will be restarted in 30 seconds:"
        )
        for container in self:
            print(f"  {container}")
        self._timer = threading.Timer(30, self._restart)
        self._timer.daemon = False
        self._timer.start()

    def __contains__(self, container: Container):
        return container in self._containers


class keydefaultdict(defaultdict):
    def __missing__(self, key):
        if self.default_factory:
            self[key] = self.default_factory(key)  # type: ignore
            return self[key]


def update_dependencies(event, verbose=False):
    attributes = event["Actor"]["Attributes"]
    container = Container(
        name=attributes["name"], id=event["id"], service=attributes[SERVICE]
    )
    if not attributes[DEPENDS_ON]:
        return
    depends_on = get_service_name(attributes[DEPENDS_ON])
    match event["status"]:
        case "start":
            if (
                verbose
                and depends_on not in monitored_services
                or container not in monitored_services[depends_on]
            ):
                print(
                    f"Adding container {container} as a dependency of service {depends_on}"
                )
            monitored_services[depends_on].add(container)
        case "destroy":
            if depends_on in monitored_services:
                if (
                    verbose
                    and depends_on in monitored_services
                    and container in monitored_services[depends_on]
                ):
                    print(
                        f"Removing container {container} as a dependency of service {depends_on}"
                    )
                monitored_services[depends_on].remove(container)
                if not monitored_services[depends_on]:
                    del monitored_services[depends_on]


def get_service_name(depends_on: str) -> str:
    return depends_on.split(":")[0]


def load_initial_events():
    for event in events:
        initial_events.append(event)
        if containers_loaded.is_set():
            return


if __name__ == "__main__":
    docker_client = docker.from_env()

    containers_loaded, initial_events_stored = threading.Event(), threading.Event()
    initial_events = []
    events = docker_client.events(
        decode=True,
        filters={"type": "container", "label": [DEPENDS_ON, SERVICE]},
    )

    initial_events_thread = threading.Thread(target=load_initial_events, daemon=True)
    initial_events_thread.start()

    monitored_services: keydefaultdict = keydefaultdict(Service)  # type: ignore
    for container in docker_client.containers.list(
        filters={"label": [DEPENDS_ON, SERVICE]}
    ):
        c = docker_client.containers.get(container.name)  # type: ignore
        if not c or not c.attrs["Config"]["Labels"][DEPENDS_ON]:  # type: ignore
            continue

        labels = c.attrs["Config"]["Labels"]  # type: ignore
        monitored_services[get_service_name(labels[DEPENDS_ON])].add(
            Container(
                name=container.name,  # type: ignore
                id=container.id,  # type: ignore
                service=labels[SERVICE],
            )
        )
    containers_loaded.set()
    initial_events_thread.join()

    for event in initial_events:
        update_dependencies(event)

    print("Initialization completed")

    for _, service in sorted(monitored_services.items()):
        print(f"Containers depending on service {service.name}:")
        for container in service:
            print(f"  {container}")

    for event in events:
        update_dependencies(event, verbose=True)

        attributes = event["Actor"]["Attributes"]
        service = attributes[SERVICE]
        container = Container(name=attributes["name"], id=event["id"], service=service)
        if event["status"] == "start" and service in monitored_services:
            print(f"Container {container} restarted")
            monitored_services[service].restart()
