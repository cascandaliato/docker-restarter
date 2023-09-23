import time

import docker

DEPENDS_ON = "com.docker.compose.depends_on"

docker_client = docker.from_env()
events = docker_client.events(decode=True, filters={"type": "container"})
dependers = {}


def get_dependee(depends_on):
    return depends_on.split(":")[0]


for container_name in docker_client.containers.list():
    if (
        depends_on := docker_client.containers.get(container_name.name)
        .attrs["Config"]["Labels"]
        .get(DEPENDS_ON, "")
    ):
        dependers.setdefault(get_dependee(depends_on), set()).add(container_name.name)

for dependee in dependers.keys():
    for depender in dependers[dependee]:
        print(f"Container {depender} depends on service {dependee}")

for event in events:
    container_name = event["Actor"]["Attributes"]["name"]
    match event["status"]:
        case "start":
            service = event["Actor"]["Attributes"]["com.docker.compose.service"]
            if service in dependers:
                time.sleep(10)
                depender = event["Actor"]["Attributes"]["name"]
                print(
                    f'Restarting the following container(s) in 10 seconds because container {container_name} (re)started: {", ".join(sorted(dependers[service]))}'
                )
                for depender in dependers[service]:
                    docker_client.containers.get(depender).restart()

            if depends_on := event["Actor"]["Attributes"][DEPENDS_ON]:
                dependers.setdefault(get_dependee(depends_on), set()).add(
                    container_name
                )
        case "destroy":
            if depends_on := event["Actor"]["Attributes"][DEPENDS_ON]:
                dependee = get_dependee(depends_on)
                dependers[dependee].remove(container_name)
                if not dependers[dependee]:
                    del dependers[dependee]
