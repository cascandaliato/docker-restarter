import logging
import os
import sys
from collections import ChainMap
from enum import Enum
from functools import lru_cache

import docker

import restarter.docker_utils as docker_utils

_PREFIX = "restarter"


def _env(setting, default):
    for name, value in os.environ.items():
        if name.strip().upper() == f"{_PREFIX.upper()}_{setting.upper()}":
            return value.strip()
    return default


def _parse_policy(policy):
    return sorted(
        [Policy(p.strip().lower()) for p in policy.split(",")], key=lambda p: p.value
    )


def _to_bool(s):
    return s.strip().lower() in ["yes", "true"]


class Policy(Enum):
    # ALWAYS = "always"
    DEPENDENCY = "dependency"
    # ON_FAILURE = "on-failure"
    UNHEALTHY = "unhealthy"
    # UNLESS_STOPPED = "unless-stopped"


class GlobalSetting(Enum):
    CHECK_EVERY_SECONDS = (0, int, 60)
    GC_EVERY_SECONDS = (1, int, 300)
    DEBOUNCE_SECONDS = (2, int, 10)
    # SCOPE = (str, "all-containers")  # all-containers, compose-project


class Setting(Enum):
    BACKOFF = (0, str, "no")
    BACKOFF_MAX_SECONDS = (1, int, 10 * 60)
    ENABLE = (2, _to_bool, "yes")
    DEPENDS_ON = (3, str, "")
    MAX_RETRIES = (5, int, sys.maxsize)
    NETWORK_MODE = (6, str, "")
    POLICY = (7, _parse_policy, "dependency,unhealthy")
    SECONDS_BETWEEN_RETRIES = (8, int, 60)


global_settings = {}
for setting in GlobalSetting:
    _, type_, default = setting.value
    global_settings[setting] = type_(_env(setting.name, default))

defaults = {}
for setting in Setting:
    _, type_, default = setting.value
    defaults[setting] = type_(_env(setting.name, default))


def _from_labels(labels):
    config = {}
    for setting in Setting:
        setting_name = f"{_PREFIX}.{setting.name.lower()}"
        for label, value in labels.items():
            if label.strip().lower() == setting_name:
                config[setting] = setting.value[1](value.strip())
    return ChainMap(config, defaults)


@lru_cache
def for_container(id):
    try:
        container = docker_utils.client.containers.get(id)
        settings = _from_labels(container.labels)
        dump(settings, f"Container {container.name} ({container.id[:12]}) settings:")
        return settings
    except docker.errors.NotFound:
        raise docker_utils.CannotRestartError(
            f"Container id {id} doesn't exist anymore."
        )


_SORTED_SETTINGS = sorted(
    [s for enum_ in [GlobalSetting, Setting] for s in enum_], key=lambda s: s.name
)


def dump(settings, message):
    for setting in _SORTED_SETTINGS:
        if setting in settings:
            if setting == Setting.POLICY:
                message += f"\n  {setting.name.lower()} = {', '.join(p.value for p in settings[setting])}"
            else:
                value = settings[setting]
                if isinstance(value, bool):
                    value = "yes" if value else "no"
                elif isinstance(value, str) and not value:
                    value = "<empty>"
                elif isinstance(value, int) and value == sys.maxsize:
                    value = "unlimited"
                message += f"\n  {setting.name.lower()} = {value}"
    logging.info(message)


def dump_env_variables():
    message = "Environment variables:"
    env_vars = [var for var in os.environ if var.startswith(f"{_PREFIX.upper()}_")]
    for var in sorted(env_vars):
        message += f"\n  {var} = {os.environ[var]}"
    if not env_vars:
        message += "\n  -"
    logging.info(message)
