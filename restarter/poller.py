import logging
import time
from collections import defaultdict
from datetime import datetime

import restarter.compose as compose
import restarter.config as config
import restarter.docker_utils as docker_utils


def cstr(container):
    return f"{container.name} ({container.id[:12]})"


def check_containers(signal, workers):
    while True:
        signal.get()
        start = time.time()
        logging.info(f"Checking containers... Starting")

        containers = docker_utils.list_with_retry(all=True)

        containers_idx = defaultdict(dict)
        for container in containers:
            containers_idx["id"][container.id] = container
            containers_idx["name"][container.name] = container
            if service := container.labels.get(compose.SERVICE, None):
                containers_idx["service"][service] = container

        to_be_restarted = set()
        for container in containers:
            settings = config.from_labels(
                container.id, container.name, container.labels
            )
            if not settings[config.Setting.ENABLE]:
                continue

            if config.Policy.UNHEALTHY in settings[
                config.Setting.POLICY
            ] and docker_utils.is_unhealthy(container):
                logging.info(f"Container {cstr(container)} is in unhealthy state.")
                to_be_restarted.add(container.name)

            if config.Policy.DEPENDENCY in settings[config.Setting.POLICY]:
                dependencies = set()

                if (
                    network_mode := container.attrs["HostConfig"].get("NetworkMode", "")
                ).startswith("container:"):
                    dependency_id = network_mode.split(":")[1]
                    if dependency_id in containers_idx["id"]:
                        dependencies.add(containers_idx["id"][dependency_id])

                for depends_on in container.labels.get(compose.DEPENDS_ON, "").split(
                    ","
                ):
                    if not depends_on:
                        continue
                    service = depends_on.split(":")[0]
                    if service in containers_idx["service"]:
                        dependencies.add(containers_idx["service"][service])

                for depends_on in settings[config.Setting.DEPENDS_ON].split(",") + [
                    settings[config.Setting.NETWORK_MODE]
                ]:
                    if not depends_on:
                        continue
                    if depends_on.startswith("container:"):
                        dependency_name = depends_on.split(":")[1]
                        if dependency_name in containers_idx["name"]:
                            dependencies.add(containers_idx["name"][dependency_name])
                    elif depends_on.startswith("service:"):
                        service = depends_on.split(":")[1]
                        if service in containers_idx["service"]:
                            dependencies.add(containers_idx["service"][service])
                    # in a docker-compose context, assume the dependency is a service
                    elif container.labels.get(compose.SERVICE, ""):
                        if depends_on in containers_idx["service"]:
                            dependencies.add(containers_idx["service"][depends_on])
                    # outside of a docker-compose context, assume the dependency is a container name
                    else:
                        if depends_on in containers_idx["name"]:
                            dependencies.add(containers_idx["name"][depends_on])

                started_at = docker_utils.started_at(container)
                for dependency in dependencies:
                    dependency_started_at = docker_utils.started_at(dependency)
                    if (
                        docker_utils.is_unhealthy(dependency)
                        or dependency.attrs["State"]["Status"] != "running"
                    ):
                        logging.info(
                            f"Container {cstr(dependency)} is in unhealthy state or not running and container {cstr(container)} depends on it."
                        )
                        to_be_restarted.add(dependency.name)
                    elif started_at <= dependency_started_at:
                        logging.info(
                            f"Container {cstr(container)} has been started before its dependency {cstr(dependency)}."
                        )
                        to_be_restarted.add(container.name)

        now = time.time()
        for container_name in to_be_restarted:
            with workers.lock:
                workers[container_name].work.set(now)

        logging.info(f"Checking containers... Done ({round(time.time() - start, 1)}s)")

        time.sleep(
            config.global_settings[config.GlobalSetting.CHECK_MIN_FREQUENCY_SECONDS]
        )
