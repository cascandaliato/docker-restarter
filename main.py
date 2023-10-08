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

import logging
import queue
import threading
import time

import restarter.config as config
import restarter.events as events
import restarter.gc as gc
import restarter.poller as poller
from restarter.signal import Signal
from restarter.workers import Workers


def excepthook(args):
    errors.put(args)


errors = queue.Queue()
threading.excepthook = excepthook

logging.basicConfig(format="[%(threadName)s] %(message)s", level=logging.INFO)


def request_check_containers(signal):
    while True:
        signal.set()
        time.sleep(
            config.global_settings[config.GlobalSetting.CHECK_MAX_FREQUENCY_SECONDS]
        )


logging.info("docker-restarter https://github.com/cascandaliato/docker-restarter")
config.dump_env_variables()
config.dump(config.global_settings, "Global settings:")
config.dump(config.defaults, "Defaults:")

workers = Workers()
check_containers_signal = Signal()
threading.Thread(
    name="events-handler",
    target=events.handler,
    args=[check_containers_signal],
    daemon=True,
).start()
threading.Thread(
    name="poller",
    target=poller.check_containers,
    args=[check_containers_signal, workers],
    daemon=True,
).start()
threading.Thread(
    name="poller-timer",
    target=request_check_containers,
    args=[check_containers_signal],
    daemon=True,
).start()
threading.Thread(name="gc", target=gc.gc, args=[workers], daemon=True).start()

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
