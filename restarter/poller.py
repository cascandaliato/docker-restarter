import logging
import time
from collections import defaultdict
from datetime import datetime

import restarter.compose as compose
import restarter.config as config
import restarter.docker_utils as docker_utils
from restarter.workers import lock as workers_lock


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
            if service := container.labels.get(compose.COMPOSE_SERVICE, None):
                containers_idx["service"][service] = container

        to_be_restarted = set()
        for container in containers:
            settings = config.from_labels(
                container.id, container.name, container.labels
            )
            if not settings[config.Setting.ENABLE]:
                continue

            if (
                config.Policy.UNHEALTHY in settings[config.Setting.POLICY]
                and container.attrs["State"].get("Health", {}).get("Status", "")
                == "unhealthy"
            ):
                logging.info(f"Container {container.name} is in unhealthy state.")
                to_be_restarted.add(container.name)

            if config.Policy.DEPENDENCY in settings[config.Setting.POLICY]:
                dependencies = set()

                if (
                    network_mode := container.attrs["HostConfig"].get("NetworkMode", "")
                ).startswith("container:"):
                    dependency_id = network_mode.split(":")[1]
                    if dependency_id in containers_idx["id"]:
                        dependencies.add(containers_idx["id"][dependency_id])

                # TODO: distinguish between service_started, service_healthy, service_completed_successfully
                # "com.docker.compose.depends_on": "restarter:service_started:false,vpn2:service_started:false",
                for depends_on in container.labels.get(
                    compose.COMPOSE_DEPENDS_ON, ""
                ).split(","):
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
                    elif container.labels.get(compose.COMPOSE_SERVICE, ""):
                        if depends_on in containers_idx["service"]:
                            dependencies.add(containers_idx["service"][depends_on])
                    else:
                        if depends_on in containers_idx["name"]:
                            dependencies.add(containers_idx["name"][depends_on])

                started_at = datetime.fromisoformat(
                    container.attrs["State"]["StartedAt"]
                ).timestamp()
                for dependency in dependencies:
                    dependency_started_at = datetime.fromisoformat(
                        dependency.attrs["State"]["StartedAt"]
                    ).timestamp()
                    if (
                        dependency.attrs["State"].get("Health", {}).get("Status", "")
                        == "unhealthy"
                        or dependency.attrs["State"]["Status"] != "running"
                    ):
                        logging.info(
                            f"Container {dependency.name} is in unhealthy state or not running and container {container.name} depends on it."
                        )
                        to_be_restarted.add(dependency.name)
                        # to_be_restarted.add(container.name)
                    elif started_at <= dependency_started_at:
                        logging.info(
                            f"Container {container.name} has been started before its dependency {dependency.name}."
                        )
                        to_be_restarted.add(container.name)

        now = time.time()
        for container_name in to_be_restarted:
            with workers_lock.r_locked():
                workers[container_name].work.set(now)

        logging.info(f"Checking containers... Done ({round(time.time() - start, 1)}s)")

        time.sleep(
            config.global_settings[config.GlobalSetting.CHECK_MIN_FREQUENCY_SECONDS]
        )
