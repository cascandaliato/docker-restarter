import logging
import os
import sys
from collections import ChainMap
from enum import Enum

_PREFIX = "restarter"


def _env(setting, default):
    return os.environ.get(f"{_PREFIX.upper()}_{setting.upper()}", default)


def _parse_policy(policy):
    return sorted(
        [Policy(p.strip().lower()) for p in policy.split(",")], key=lambda p: p.value
    )


def _to_bool(s):
    return s.strip().lower() in ["yes", "true"]


class Policy(Enum):
    DEPENDENCY = "dependency"
    UNHEALTHY = "unhealthy"


class GlobalSetting(Enum):
    CHECK_EVERY_SECONDS = (int, 60)
    GC_EVERY_SECONDS = (int, 300)


class Setting(Enum):
    ENABLE = (2, _to_bool, "yes")
    DEPENDS_ON = (3, str, "")
    NETWORK_MODE = (6, str, "")
    POLICY = (7, str, "dependency,unhealthy")


global_settings = {}
for setting in GlobalSetting:
    type_, default = setting.value
    global_settings[setting] = type_(_env(setting.name, default))

defaults = {}
for setting in Setting:
    _, type_, default = setting.value
    if setting == Setting.POLICY:
        defaults[setting] = _parse_policy(type_(_env(setting.name, default)))
    else:
        defaults[setting] = type_(_env(setting.name, default))


def from_labels(labels):
    config = {}
    for setting in Setting:
        key = f"{_PREFIX}.{setting.name.lower()}"
        if key in labels:
            config[setting] = setting.value[1](labels[key])
            if setting == Setting.POLICY:
                config[setting] = _parse_policy(config[setting])
    return ChainMap(config, defaults)


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
    vars = [var for var in os.environ if var.startswith(f"{_PREFIX.upper()}_")]
    for var in sorted(vars):
        message += f"\n  {var} = {os.environ[var]}"
    if not vars:
        message += "\n  -"
    logging.info(message)
