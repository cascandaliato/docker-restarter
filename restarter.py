import time

import docker

DEPENDS_ON = "com.docker.compose.depends_on"

docker_client = docker.from_env()
events = docker_client.events(decode=True, filters={"type": "container"})
dependers = {}


def get_dependee(depends_on):
    return depends_on.split(":")[0]


for container in docker_client.containers.list():
    if (
        depends_on := docker_client.containers.get(container.name)
        .attrs["Config"]["Labels"]
        .get(DEPENDS_ON, "")
    ):
        dependers.setdefault(get_dependee(depends_on), set()).add((container.id, container.name))

for dependee in dependers.keys():
    for id, name in dependers[dependee]:
        print(f"Container {name} (id {id}) depends on service {dependee}")

for event in events:
    if event['status'].startswith('exec'): continue
    id, name = event['id'], event["Actor"]["Attributes"]["name"]
    print(f'Event {event["status"]} for container {name} (id {id})')
    match event["status"]:
        case "start":
            service = event["Actor"]["Attributes"].get("com.docker.compose.service", None)
            if service in dependers:
                print(
                    f'Restarting the following container(s) in 10 seconds because container {name} (id {id}) (re)started: {", ".join(sorted(b + " (id " + a + ")" for a, b in dependers[service]))}'
                )
                time.sleep(10)
                for cid, cname in dependers[service]:
                    print(f'Restarting container {cname} (id {cid})')
                    docker_client.containers.get(cid).restart()

            if depends_on := event["Actor"]["Attributes"].get(DEPENDS_ON, None):
                if (id, name) in dependers.setdefault(get_dependee(depends_on)): continue
                print(f'Container {name} (id {id}) depends on service {get_dependee(depends_on)}')
                dependers.setdefault(get_dependee(depends_on), set()).add(
                    (id, name)
                )
        case "destroy":
            if depends_on := event["Actor"]["Attributes"].get(DEPENDS_ON, None):
                dependee = get_dependee(depends_on)
                print(f'Removing dependency of container {name} (id {id}) from service {dependee}')
                dependers.setdefault(dependee, set()).discard((id, name))
                if not dependers[dependee]:
                    del dependers[dependee]
