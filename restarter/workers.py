import logging
import math
import queue
import sys
import threading
import time
from collections import deque

import docker

import restarter.compose as compose
import restarter.config as config
import restarter.docker_utils as docker_utils
from restarter.docker_utils import cstr
from restarter.signal import Signal


class Worker:
    def __init__(self, name):
        self.name = name
        self.lock = threading.Lock()
        self.work = Signal()
        self.done = threading.Event()
        self.restart_count = 0
        self.recent_status = deque([None, None], maxlen=2)
        threading.Thread(name=f"worker-{name}", target=self._work, daemon=True).start()
        logging.info(f"Worker created for container {name}")

    def _work(self):
        while True:
            # Don't block on the work signal (get_nowait) to allow the GC to periodically check whether
            # (1) the work queue is empty
            # (2) and there is no work in progress
            request = sys.maxsize
            while True:
                time.sleep(1)
                with self.lock:
                    try:
                        request = self.work.get_nowait()
                        if request is None:
                            logging.info(
                                f"Worker for container {self.name} is shutting down."
                            )
                            self.done.set()
                            return
                    except queue.Empty:
                        continue
                    else:
                        self.done.clear()
                        break
            try:
                # pass the container along with the work payload (?)
                try:
                    container = docker_utils.client.containers.get(self.name)
                except docker.errors.NotFound as err:
                    raise docker_utils.CannotRestartError(
                        f"Container {self.name} doesn't exist anymore."
                    ) from err

                started_at = docker_utils.started_at(container)
                if started_at > request:
                    raise docker_utils.CannotRestartError(
                        f"Container {cstr(container)} has already been restarted."
                    )

                settings = config.from_labels(
                    container.id, container.name, container.labels
                )
                self.restart_count += 1
                if self.restart_count > settings[config.Setting.MAX_RETRIES]:
                    raise docker_utils.CannotRestartError(
                        f"Container {cstr(container)} has reached the maximum number of restart attempts ({settings[config.Setting.MAX_RETRIES]})."
                    )
                restart_count_str = self.restart_count
                if settings[config.Setting.MAX_RETRIES] < sys.maxsize:
                    restart_count_str += f" of {settings[config.Setting.MAX_RETRIES]}"
                logging.info(
                    f"Attempt #{restart_count_str} for container {cstr(container)}."
                )

                delay = settings[config.Setting.SECONDS_BETWEEN_RETRIES]
                match settings[config.Setting.BACKOFF]:
                    case "linear":
                        delay = min(
                            delay * self.restart_count,
                            settings[config.Setting.BACKOFF_MAX_SECONDS],
                        )
                    case "exponential":
                        delay = min(
                            delay * 2**self.restart_count,
                            settings[config.Setting.BACKOFF_MAX_SECONDS],
                        )
                wait = max(math.ceil(started_at + delay - time.time()), 0)
                if wait:
                    logging.info(
                        f"Waiting {wait} seconds before taking any action on container {cstr(container)}."
                    )
                    time.sleep(wait)

                network_mode = container.attrs["HostConfig"].get("NetworkMode", "")
                if not network_mode.startswith("container:"):
                    try:
                        logging.info(f"Restarting container {cstr(container)}.")
                        container.restart()
                    except Exception as err:
                        raise docker_utils.CannotRestartError(
                            f"Failed to restart container {cstr(container)}. Error: {err}"
                        ) from err
                else:
                    dependency_id = network_mode.split(":")[1]
                    dependency = None
                    try:
                        dependency = docker_utils.client.containers.get(dependency_id)
                    except docker.errors.NotFound:
                        pass
                    if dependency:
                        logging.info(f"Restarting container {cstr(container)}.")
                        try:
                            container.restart()
                        except Exception as err:
                            raise docker_utils.CannotRestartError(
                                f"Failed to restart container {cstr(container)}. Error: {err}"
                            ) from err
                    else:
                        # look for new parent via our network_mode label
                        parent = None
                        restarter_network_mode = settings[config.Setting.NETWORK_MODE]
                        if not restarter_network_mode:
                            raise docker_utils.CannotRestartError(
                                f"Label {config.to_label(config.Setting.NETWORK_MODE)} is required in order to recreate component {cstr(container)}."
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
                                if p.labels.get(compose.SERVICE, "") == service:
                                    parent = p
                                    break
                        elif container.labels.get(compose.SERVICE, ""):
                            service = restarter_network_mode
                            for p in docker_utils.list_with_retry():
                                if p.labels.get(compose.SERVICE, "") == service:
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
                            raise docker_utils.CannotRestartError(
                                f"Could not find any container matching {config.to_label(config.Setting.NETWORK_MODE)}={restarter_network_mode}."
                            )

                        run_args = docker_utils.get_container_run_args(
                            container, parent.id
                        )

                        try:
                            logging.info(f"Removing container {cstr(container)}.")
                            container.remove(force=True)
                        except docker.errors.NotFound as err:
                            raise docker_utils.CannotRestartError(
                                f"Container {cstr(container)} doesn't exist anymore."
                            ) from err

                        try:
                            logging.info(f"Recreating container {cstr(container)}.")
                            docker_utils.client.containers.run(**run_args)
                        except docker.errors.APIError as err:
                            if "Conflict. The container name" in str(
                                err
                            ) and "is already in use by container" in str(err):
                                raise docker_utils.CannotRestartError(
                                    f"Container {cstr(container)} has already been restarted by an external program. Error: {err}"
                                ) from err
                            else:
                                raise  # TODO: die here?

            except docker_utils.CannotRestartError as err:
                logging.info(
                    f"Can't/won't restart container {self.name}. Reason: {err}"
                )
                continue
            finally:
                self.done.set()


class Workers(dict[str, Worker]):
    def __init__(self):
        self.lock = threading.Lock()
        # self._lock = threading.Lock()  # TODO: is this necessary?
        dict.__init__(self)

    def __getitem__(self, name):
        if name not in self:
            # with self._lock:
            if name not in self:
                self[name] = Worker(name)
        return super().__getitem__(name)
