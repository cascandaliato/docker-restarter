import math
import queue
import threading


def excepthook(args):
    errors.put(args)


errors = queue.Queue()
threading.excepthook = excepthook


import functools
import logging
import queue
import random
import sys
import threading
import time
from collections import defaultdict, deque
from contextlib import contextmanager
from datetime import datetime

import docker

import restarter.config as config
import restarter.docker_utils as docker_utils

print("hello")
logging.basicConfig(format="[%(threadName)s] %(message)s", level=logging.INFO)


# Keeps only the most recent element
class CoalescingQueue(queue.Queue):
    def __init__(self):
        self._lock = RWLock()
        super().__init__()

    def put(self, item):
        try:
            self.get(block=False)
        except queue.Empty:
            pass
        super().put(item)


# Source: https://gist.github.com/tylerneylon/a7ff6017b7a1f9a506cf75aa23eacfd6
class RWLock:
    def __init__(self):
        self.w_lock = threading.Lock()
        self.num_r_lock = threading.Lock()
        self.num_r = 0

    def r_acquire(self):
        self.num_r_lock.acquire()
        self.num_r += 1
        if self.num_r == 1:
            self.w_lock.acquire()
        self.num_r_lock.release()

    def r_release(self):
        self.num_r_lock.acquire()
        self.num_r -= 1
        if self.num_r == 0:
            self.w_lock.release()
        self.num_r_lock.release()

    @contextmanager
    def r_locked(self):
        try:
            self.r_acquire()
            yield
        finally:
            self.r_release()

    def w_acquire(self):
        self.w_lock.acquire()

    def w_release(self):
        self.w_lock.release()

    @contextmanager
    def w_locked(self):
        try:
            self.w_acquire()
            yield
        finally:
            self.w_release()


class CannotRestartError(Exception):
    pass


class Worker:
    def __init__(self, name):
        self.name = name
        self.lock = threading.Lock()
        self.work = CoalescingQueue()
        self.done = threading.Event()
        self.recent_status = deque([None, None], maxlen=2)
        threading.Thread(name=f"worker-{name}", target=self._work, daemon=True).start()
        logging.info(f"Worker created for container {name}")

    def _work(self):
        while True:
            # Don't block on the work queue to allow GC to (reliably) assert whether
            # (1) the work queue is empty (the read-write lock shared with the containers poller and the events handler)
            # (2) and there is no work in progress (the lock below and the `done` Event)
            while True:
                time.sleep(1)
                with self.lock:
                    try:
                        work_timestamp = self.work.get_nowait()
                    except queue.Empty:
                        continue
                    else:
                        self.done.clear()
                break
            if work_timestamp is None:
                logging.info(f"Worker for container {self.name} is shutting down.")
                self.done.set()
                return

            wait = max(math.ceil(work_timestamp + 10 - time.time()), 0)
            if wait:
                logging.info(f"Waiting {wait} seconds before taking any action.")
                time.sleep(wait)

            try:
                try:
                    container = docker_utils.client.containers.get(self.name)
                except docker.errors.NotFound:
                    raise CannotRestartError(
                        f"Container {self.name} doesn't exist anymore."
                    )

                settings = config.from_labels(container.labels)
                started_at = datetime.fromisoformat(
                    container.attrs["State"]["StartedAt"]
                ).timestamp()
                if started_at > work_timestamp:
                    raise CannotRestartError(
                        f"Container {self.name} has already been restarted."
                    )

                network_mode = container.attrs["HostConfig"].get("NetworkMode", "")
                if not network_mode.startswith("container:"):
                    try:
                        logging.info(f"Restarting container {self.name}.")
                        container.restart()
                    except Exception as err:
                        raise CannotRestartError(
                            f"Failed to restart container {self.name}. Error: {err}"
                        )
                else:
                    dependency_id = network_mode.split(":")[1]
                    dependency = None
                    try:
                        dependency = docker_utils.client.containers.get(dependency_id)
                    except docker.errors.NotFound:
                        pass
                    if dependency:
                        logging.info(f"Restarting container {self.name}.")
                        try:
                            container.restart()
                        except Exception as err:
                            raise CannotRestartError(
                                f"Failed to restart container {self.name}. Error: {err}"
                            )
                    else:
                        parent = None
                        restarter_network_mode = settings[config.Setting.NETWORK_MODE]
                        if not restarter_network_mode:
                            raise CannotRestartError(
                                f"Label {RESTARTER_NETWORK_MODE} is required in order to recreate component {self.name}."
                            )
                        if restarter_network_mode.lower().startswith("container:"):
                            dependency_name = restarter_network_mode.split(":")[1]
                            try:
                                parent = docker_utils.client.containers.get(
                                    dependency_name
                                )
                            except docker.errors.NotFound:
                                pass
                        elif restarter_network_mode.lower().startswith("service:"):
                            service = restarter_network_mode.split(":")[1]
                            for p in docker_utils.list_with_retry():
                                if p.labels.get(COMPOSE_SERVICE, "") == service:
                                    parent = p
                                    break
                        elif container.labels.get(COMPOSE_SERVICE, ""):
                            service = restarter_network_mode
                            for p in docker_utils.list_with_retry():
                                if p.labels.get(COMPOSE_SERVICE, "") == service:
                                    parent = p
                                    break
                        else:
                            dependency_name = restarter_network_mode
                            try:
                                parent = docker_utils.client.containers.get(
                                    dependency_name
                                )
                            except docker.errors.NotFound:
                                pass

                        if not parent:
                            raise CannotRestartError(
                                f"Could not find any container matching {RESTARTER_NETWORK_MODE}={restarter_network_mode}."
                            )

                        run_args = docker_utils.get_container_run_args(
                            container, parent.id
                        )

                        try:
                            logging.info(f"Removing container {self.name}.")
                            container.remove(force=True)
                        except docker.errors.NotFound:
                            raise CannotRestartError(
                                f"Container {self.name} doesn't exist anymore."
                            )

                        logging.info(f"Recreating container {self.name}.")
                        docker_utils.client.containers.run(**run_args)
            except CannotRestartError as err:
                logging.info(
                    f"Can't/won't restart container {self.name}. Reason: {err}"
                )
                continue
            finally:
                self.done.set()


class Workers(dict[str, Worker], RWLock):
    def __init__(self):
        self._lock = RWLock()
        dict.__init__(self)
        RWLock.__init__(self)

    def __getitem__(self, name):
        if name not in self:
            with self.w_locked():
                if name not in self:
                    self[name] = Worker(name)
        return super().__getitem__(name)


# CONSTANTS
RESTARTER_DEPENDS_ON = "restarter.depends_on"
RESTARTER_NETWORK_MODE = "restarter.network_mode"
COMPOSE_CONFIG_FILES = "com.docker.compose.project.config_files"
COMPOSE_DEPENDS_ON = "com.docker.compose.depends_on"
COMPOSE_PROJECT = "com.docker.compose.project"
COMPOSE_SERVICE = "com.docker.compose.service"
COMPOSE_WORKING_DIR = "com.docker.compose.project.working_dir"

# globals
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
        if service := container.labels.get(COMPOSE_SERVICE, None):
            containers_idx["service"][service] = container

    to_be_restarted = set()
    for container in containers:
        settings = config.from_labels(container.labels)
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

            for depends_on in container.labels.get(COMPOSE_DEPENDS_ON, "").split(","):
                if not depends_on:
                    continue
                service = depends_on.split(":")[0]
                if service in containers_idx["service"]:
                    dependencies.add(containers_idx["service"][service])

            for depends_on in container.labels.get(RESTARTER_DEPENDS_ON, "").split(
                ","
            ) + [container.labels.get(RESTARTER_NETWORK_MODE, "")]:
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
                elif container.labels.get(COMPOSE_SERVICE, ""):
                    if depends_on in containers_idx["service"]:
                        dependencies.add(containers_idx["service"][depends_on])
                else:
                    if depends_on in containers_idx["name"]:
                        dependencies.add(containers_idx["name"][depends_on])

            started_at = datetime.fromisoformat(
                container.attrs["State"]["StartedAt"]
            ).timestamp()
            for dependency in dependencies:
                if (
                    dependency.attrs["State"].get("Health", {}).get("Status", "")
                    == "unhealthy"
                    or dependency.attrs["State"]["Status"] != "running"
                ):
                    logging.info(
                        f"Container {dependency.name} is in unhealthy state or not running and container {container.name} depends on it."
                    )
                    to_be_restarted.add(dependency.name)
                    to_be_restarted.add(container.name)

                dependency_started_at = datetime.fromisoformat(
                    dependency.attrs["State"]["StartedAt"]
                ).timestamp()
                if started_at <= dependency_started_at:
                    logging.info(
                        f"Container {container.name} has been started before its dependency {dependency.name}."
                    )
                    to_be_restarted.add(container.name)

    timestamp = time.time()
    for container_name in to_be_restarted:
        with workers_lock.r_locked():
            workers[container_name].work.put(timestamp)


@repeat(
    every_seconds=config.global_settings[config.GlobalSetting.GC_EVERY_SECONDS],
)
@timed(message="Periodic garbage collection")
def gc():
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
