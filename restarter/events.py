import restarter.docker_utils as docker_utils
import restarter.workers as workers
import logging

_MONITORED_EVENTS = ("start", "health_status: unhealthy", "die")


def handler(signal):
    for event in docker_utils.client.events(
        decode=True,
        filters={"type": "container"},
    ):
        status = event["status"]
        if status not in _MONITORED_EVENTS:
            continue
        with workers.lock.r_locked():
            name = event["Actor"]["Attributes"]["name"]
            id = event["id"]
            logging.info(
                f'Received a "{status}" event for container {name} ({id[:12]}).'
            )
            signal.set()
