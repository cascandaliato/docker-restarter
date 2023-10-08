# TODO
# [ ] stopped/paused container and on-failure/always/unless-stopped policies
# [ ] scope
#     use concatenation of project, config_files and service
#       'com.docker.compose.project': 'restarter',
#       'com.docker.compose.project.config_files': '.../docker-compose.yml',
#       'com.docker.compose.project.working_dir': '.../restarter',
#       'com.docker.compose.service': 'vpn',
# [ ] print diff of container.attrs before/after being recreated
# State.Status == exited and State.ExitCode < 125 https://docs.docker.com/engine/reference/run/#exit-status
# maybe consider OOMKilled: true, and exclude dead:true too
# "State": {
#     "Status": "exited",
#     "Running": false,
#     "Paused": false,
#     "Restarting": false,
#     "OOMKilled": false,
#     "Dead": false,
#     "Pid": 0,
#     "ExitCode": 3,
#     "Error": "",
#     "StartedAt": "2023-10-08T14:08:06.584696474Z",
#     "FinishedAt": "2023-10-08T14:08:09.585841566Z"
# },

import functools
import logging
import queue
import random
import threading
import time
from collections import defaultdict
from datetime import datetime

import restarter.compose as compose
import restarter.config as config
import restarter.docker_utils as docker_utils
from restarter.rwlock import RWLock
from restarter.worker import Workers


def excepthook(args):
    errors.put(args)


errors = queue.Queue()
threading.excepthook = excepthook

logging.basicConfig(format="[%(threadName)s] %(message)s", level=logging.INFO)

workers_lock = RWLock()
workers = Workers()


def timed(*, message):
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            start = time.time()
            logging.info(f"{message}... Starting")
            result = func(*args, **kwargs)
            duration = time.time() - start
            logging.info(f"{message}... Done ({round(duration, 1)}s)")
            return result

        return wrapper

    return decorator


def repeat(*, every_seconds):
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            while True:
                func(*args, **kwargs)
                time.sleep(every_seconds)

        return wrapper

    return decorator


def check_containers():
    containers = docker_utils.list_with_retry(all=True)

    containers_idx = defaultdict(dict)
    for container in containers:
        containers_idx["id"][container.id] = container
        containers_idx["name"][container.name] = container
        if service := container.labels.get(compose.COMPOSE_SERVICE, None):
            containers_idx["service"][service] = container

    to_be_restarted = set()
    for container in containers:
        settings = config.for_container(container.id)
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
            workers[container_name].work.put(now)


@repeat(
    every_seconds=config.global_settings[config.GlobalSetting.GC_EVERY_SECONDS],
)
@timed(message="Periodic garbage collection")
def gc():
    logging.info(f"Number of threads: {threading.active_count()}")
    with workers_lock.w_locked():
        for name in list(workers.keys()):
            with workers[name].lock:
                if workers[name].work.empty() and workers[name].done.is_set():
                    logging.info(
                        f"Worker for container {name} is not required anymore."
                    )
                    workers[name].work.put(None)
                    del workers[name]


MONITORED_EVENTS = ("start", "health_status: unhealthy", "die")


def events():
    for event in docker_utils.client.events(
        decode=True,
        filters={"type": "container"},
    ):
        status = event["status"]
        if status not in MONITORED_EVENTS:
            continue
        with workers_lock.r_locked():
            # TODO: get settings, check if enabled
            name = event["Actor"]["Attributes"]["name"]
            workers[name].recent_status.append(status)

            logging.info(
                f'Received a "{status}" event for container {name}. Triggering a full check.'
            )
            timed(message="Ad-hoc containers check")(check_containers)()


logging.info("docker-restarter https://github.com/cascandaliato/docker-restarter")
config.dump_env_variables()
config.dump(config.global_settings, "Global settings:")
config.dump(config.defaults, "Defaults:")

threading.Thread(name="events", target=events, daemon=True).start()
threading.Thread(
    name="poller",
    target=repeat(
        every_seconds=config.global_settings[config.GlobalSetting.CHECK_EVERY_SECONDS]
    )(timed(message="Periodic containers check")(check_containers)),
    daemon=True,
).start()
time.sleep(random.randrange(10))
threading.Thread(name="gc", target=gc, daemon=True).start()

error = errors.get()

if error.thread:
    logging.info(f"Thread: {error.thread}")
logging.info(f"Exception Type: {error.exc_type}")
if error.exc_value:
    logging.info(f"Exception Value: {error.exc_value}")
if error.exc_traceback:
    import traceback

    traceback.print_tb(error.exc_traceback)

logging.info("Exiting...")
