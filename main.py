import time

import docker

from restarter.docker import get_container_run_args

DEPENDS_ON = "restarter.depends_on_service"

if __name__ == "__main__":
    docker_client = docker.from_env()

    for event in docker_client.events(
        decode=True,
        filters={"type": "container"},
    ):
        if (
            DEPENDS_ON not in event["Actor"].get("Attributes", {})
            or event["status"] != "health_status: unhealthy"
        ):
            continue

        try:
            container = docker_client.containers.get(event["id"])
        except:
            print(
                f"Received an event for container {name} (id {id[:12]}) but the container doesn't exist anymore"
            )
            continue

        name, id = event["Actor"]["Attributes"]["name"], event["id"]
        print(event["status"], name, id)
        parent_service = event["Actor"]["Attributes"][DEPENDS_ON]

        restarted = False
        while not restarted:
            try:
                parent_container = docker_client.containers.list(
                    filters={"label": f"com.docker.compose.service={parent_service}"}
                )
                if len(parent_container) == 0:
                    raise Exception(
                        f"Could not find any running container providing service {parent_service}"
                    )
                else:
                    parent_container = parent_container[0]

                run_args = get_container_run_args(container, parent_container.id)

                deleted = False
                try:
                    container.remove(force=True)
                    deleted = True
                except:
                    pass
                if deleted:
                    print(f"Container {name} (id {id[:12]}) successfully removed")

                docker_client.containers.run(**run_args)
                new_container = docker_client.containers.get(name)
                print(f"Started new container {name} (id {new_container.id[:12]})")
                restarted = True
            except Exception as err:
                print(
                    f"Attempt to restart container {name} (id {id[:12]}) failed. Retrying in 30 seconds. Error: {err}"
                )
                time.sleep(30)
