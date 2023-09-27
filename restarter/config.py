import math
import os
from enum import Enum
import sys

from restarter.utils import keydefaultdict

_PREFIX = "restarter"


def _env(setting, default):
    return os.environ.get(f"${_PREFIX.upper()}_{setting.upper()}", default)


def _to_bool(s):
    return s.strip().lower() in ["yes", "true"]


class GlobalSetting(Enum):
    SCOPE = (str, "all-container")
    ENABLED_BY_DEFAULT = (_to_bool, "yes")


class Setting(Enum):
    POLICY = (str, "unhealthy,dependency")
    AFTER_SECONDS = (int, 30)
    MAX_RETRIES = (int, sys.maxsize)
    BACKOFF = (str, "no")
    BACKOFF_MAX_SECONDS = (int, 10 * 60)
    ENABLED = (_to_bool, "yes")


globals = {}
for enum_ in [GlobalSetting, Setting]:
    for setting in enum_:
        type_, default = setting.value
        globals[setting] = type_(_env(setting.name, default))


def with_defaults(config):
    def get(key):
        if key == Setting.ENABLED:
            return config.get(key, globals[Setting.ENABLED_BY_DEFAULT])
        return config.get(key, globals[key])

    return keydefaultdict(get)


def from_labels(labels):
    config = {}
    for setting in Setting:
        key = f"{_PREFIX}.{setting.name.lower()}"
        if key in labels:
            config[setting] = setting.value[0](labels[key])
    return config
