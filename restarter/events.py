import restarter.docker_utils as docker_utils
import logging

_MONITORED_EVENTS = ("start", "health_status: unhealthy", "die")


def handler(check_containers):
    for event in docker_utils.client.events(
        decode=True,
        filters={"type": "container"},
    ):
        status = event["status"]
        if status not in _MONITORED_EVENTS:
            continue
        name, id = event["Actor"]["Attributes"]["name"], event["id"]
        logging.info(f'Received event "{status}" for container {name} ({id[:12]}).')
        check_containers.set()
