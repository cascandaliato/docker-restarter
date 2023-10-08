COMPOSE_CONFIG_FILES = "com.docker.compose.project.config_files"
COMPOSE_DEPENDS_ON = "com.docker.compose.depends_on"
COMPOSE_PROJECT = "com.docker.compose.project"
COMPOSE_SERVICE = "com.docker.compose.service"
COMPOSE_WORKING_DIR = "com.docker.compose.project.working_dir"

# def get_self_compose_project(docker_util.client):
#     labels = docker_util.client.containers.get(get_self_id()).attrs["Config"]["Labels"]
#     return {k: labels[k] for k in [PROJECT, CONFIG_FILES, WORKING_DIR]}
