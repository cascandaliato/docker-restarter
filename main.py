import os
import threading
from collections import defaultdict

import docker
from restarter.config import from_labels

from restarter.models import Container, Service


# Docker Compose labels
DEPENDS_ON = "com.docker.compose.depends_on"
SERVICE = "com.docker.compose.service"


class keydefaultdict(defaultdict):
    def __missing__(self, key):
        if self.default_factory:
            self[key] = self.default_factory(key)
            return self[key]


def update_dependencies(event, verbose=False):
    attributes = event["Actor"]["Attributes"]
    if attributes["name"] == "cb":
        print(from_labels(attributes))
    container = Container(
        name=attributes["name"],
        id=event["id"],
        service=attributes[SERVICE],
        config=from_labels(attributes),
        docker_client=docker_client,
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

    monitored_services: keydefaultdict = keydefaultdict(lambda name: Service(name=name))
    for container in docker_client.containers.list(
        filters={"label": [DEPENDS_ON, SERVICE]}
    ):
        c = docker_client.containers.get(container.name)  # type: ignore
        if not c or not c.attrs["Config"]["Labels"][DEPENDS_ON]:  # type: ignore
            continue

        labels = c.attrs["Config"]["Labels"]  # type: ignore
        # print(from_labels(c.attrs["Config"]["Labels"]))
        monitored_services[get_service_name(labels[DEPENDS_ON])].add(
            Container(
                name=container.name,  # type: ignore
                id=container.id,  # type: ignore
                service=labels[SERVICE],
                config=from_labels(c.attrs["Config"]["Labels"]),
                docker_client=docker_client,
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
        container = Container(
            name=attributes["name"],
            id=event["id"],
            service=service,
            config={},
            docker_client=docker_client,
        )
        if event["status"] == "start" and service in monitored_services:
            print(f"Container {container} restarted")
            monitored_services[service].restart()
