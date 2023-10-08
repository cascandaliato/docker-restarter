import logging
import time

import docker
from docker.types import DeviceRequest, LogConfig, Mount, Ulimit


def get_container_run_args(container, parent_id):
    image = container.image

    run_args = {
        "image": image.id,
        "command": container.attrs["Config"]["Cmd"],
        "auto_remove": False,
        "blkio_weight_device": container.attrs["HostConfig"]["BlkioWeightDevice"],
        "blkio_weight": container.attrs["HostConfig"]["BlkioWeight"],
        "cap_add": container.attrs["HostConfig"]["CapAdd"],
        "cap_drop": container.attrs["HostConfig"]["CapDrop"],
        "cgroup_parent": container.attrs["HostConfig"]["CgroupParent"],
        "cgroupns": container.attrs["HostConfig"]["CgroupnsMode"],
        "cpu_count": container.attrs["HostConfig"]["CpuCount"],
        "cpu_percent": container.attrs["HostConfig"]["CpuPercent"],
        "cpu_period": container.attrs["HostConfig"]["CpuPeriod"],
        "cpu_quota": container.attrs["HostConfig"]["CpuQuota"],
        "cpu_rt_period": container.attrs["HostConfig"]["CpuRealtimePeriod"],
        "cpu_rt_runtime": container.attrs["HostConfig"]["CpuRealtimeRuntime"],
        "cpu_shares": container.attrs["HostConfig"]["CpuShares"],
        "cpuset_cpus": container.attrs["HostConfig"]["CpusetCpus"],
        "cpuset_mems": container.attrs["HostConfig"]["CpusetMems"],
        "detach": True,
        "device_cgroup_rules": container.attrs["HostConfig"]["DeviceCgroupRules"],
        "device_read_bps": container.attrs["HostConfig"]["BlkioDeviceReadBps"],
        "device_read_iops": container.attrs["HostConfig"]["BlkioDeviceReadIOps"],
        "device_write_bps": container.attrs["HostConfig"]["BlkioDeviceWriteBps"],
        "device_write_iops": container.attrs["HostConfig"]["BlkioDeviceWriteIOps"],
        "devices": [
            f'{d["PathOnHost"]}:{d["PathInContainer"]}:{d["CgroupPermissions"]}'
            for d in container.attrs["HostConfig"]["Devices"] or []
        ],
        "device_requests": container.attrs["HostConfig"]["DeviceRequests"],
        "dns": container.attrs["HostConfig"]["Dns"],
        "dns_opt": container.attrs["HostConfig"]["DnsOptions"],
        "dns_search": container.attrs["HostConfig"]["DnsSearch"],
        "domainname": container.attrs["Config"]["Domainname"],
        "entrypoint": container.attrs["Config"]["Entrypoint"],
        "environment": container.attrs["Config"]["Env"],
        "group_add": container.attrs["HostConfig"]["GroupAdd"],
        "hostname": container.attrs["Config"]["Hostname"],
        "init": container.attrs["HostConfig"].get("Init", None),
        "ipc_mode": container.attrs["HostConfig"]["IpcMode"],
        "isolation": container.attrs["HostConfig"]["Isolation"],
        "labels": container.attrs["Config"]["Labels"],
        "mac_address": container.attrs["NetworkSettings"]["MacAddress"],
        "mem_limit": container.attrs["HostConfig"]["Memory"],
        "mem_reservation": container.attrs["HostConfig"]["MemoryReservation"],
        "mem_swappiness": container.attrs["HostConfig"]["MemorySwappiness"],
        "memswap_limit": container.attrs["HostConfig"]["MemorySwap"],
        "name": container.name,
        "nano_cpus": container.attrs["HostConfig"]["NanoCpus"],
        # "network": None, # incompatible with network_mode
        "network_disabled": container.attrs["Config"].get("NetworkDisabled", False),
        "network_mode": container.attrs["HostConfig"]["NetworkMode"],
        # "network_driver_opt": None, # incompatible with network_mode
        "oom_kill_disable": container.attrs["HostConfig"]["OomKillDisable"],
        "oom_score_adj": container.attrs["HostConfig"]["OomScoreAdj"],
        "pid_mode": container.attrs["HostConfig"]["PidMode"],
        "pids_limit": container.attrs["HostConfig"]["PidsLimit"],
        "platform": container.attrs["Platform"],
        "privileged": container.attrs["HostConfig"]["Privileged"],
        "publish_all_ports": container.attrs["HostConfig"]["PublishAllPorts"],
        "read_only": container.attrs["HostConfig"]["ReadonlyRootfs"],
        "restart_policy": container.attrs["HostConfig"]["RestartPolicy"],
        "security_opt": container.attrs["HostConfig"]["SecurityOpt"],
        "shm_size": container.attrs["HostConfig"]["ShmSize"],
        "stdin_open": container.attrs["Config"]["OpenStdin"],
        "stdout": container.attrs["Config"]["AttachStdout"],
        "stderr": container.attrs["Config"]["AttachStderr"],
        "stop_signal": container.attrs["Config"].get("StopSignal", None),
        "tty": container.attrs["Config"]["Tty"],
        # "use_config_proxy": False,
        "user": container.attrs["Config"]["User"],
        "userns_mode": container.attrs["HostConfig"]["UsernsMode"],
        "uts_mode": container.attrs["HostConfig"]["UTSMode"],
        "version": "auto",
        "volume_driver": container.attrs["HostConfig"]["VolumeDriver"],
        "volumes_from": container.attrs["HostConfig"]["VolumesFrom"],
        "working_dir": container.attrs["Config"]["WorkingDir"],
    }

    if device_requests := container.attrs["HostConfig"].get("DeviceRequests", None):
        run_args["device_requests"] = [DeviceRequest(**dr) for dr in device_requests]

    if extra_hosts := container.attrs["HostConfig"].get("ExtraHosts", None):
        run_args["extra_hosts"] = {
            eh.split(":")[0]: eh.split(":")[1] for eh in extra_hosts
        }

    if healtcheck := container.attrs["Config"].get("Healthcheck", None):
        run_args["healthcheck"] = {}
        for field in ["test", "interval", "timeout", "retries", "start_period"]:
            source = "".join(t.capitalize() for t in field.split("_"))
            if source in healtcheck:
                run_args["healthcheck"][field] = healtcheck[source]

    # [ "/linked:/linker/alias" ] to { "linked": "alias" }
    if links := container.attrs["HostConfig"].get("Links", None):
        run_args = {l.split(":")[0][1:]: l.split(":")[1].split("/")[2] for l in links}

    if container.attrs["HostConfig"].get("LogConfig", None):
        log_config = {}
        for field in ["type", "config"]:
            if (source := field.capitalize()) in container.attrs["HostConfig"][
                "LogConfig"
            ]:
                log_config[field] = container.attrs["HostConfig"]["LogConfig"][source]
        if log_config:
            run_args["log_config"] = LogConfig(**log_config)

    if ports := container.attrs["HostConfig"]["PortBindings"]:
        run_args["ports"] = {
            k: [(b["HostIp"], b["HostPort"]) for b in v] for k, v in ports.items()
        }

    # https://github.com/docker/docker-py/blob/c38656dc7894363f32317affecc3e4279e1163f8/docker/types/services.py#L217
    if mounts := container.attrs["HostConfig"].get("Mounts", None):
        mounts_arg = []
        for mount in mounts:
            mount_args = {}
            for field in ["target", "source", "type", "read_only", "consistency"]:
                source = "".join(w.capitalize() for w in field.split("_"))
                if source in mount:
                    mount_args[field] = mount[source]
            if "source" not in mount_args:
                mount_args["source"] = None

            if "BindOptions" in mount and "Propagation" in mount["BindOptions"]:
                mount_args["propagation"] = mount["BindOptions"]["Propagation"]

            if "VolumeOptions" in mount:
                for field in ["no_copy", "labels", "driver_config"]:
                    source = "".join(w.capitalize() for w in field.split("_"))
                    if source in mount["VolumeOptions"]:
                        mount_args[field] = mount["VolumeOptions"][source]

            if "TmpfsOptions" in mount:
                for source, destination in [
                    ("Mode", "tmpfs_mode"),
                    ("SizeBytes", "tmpfs_size"),
                ]:
                    if source in mount["TmpfsOptions"]:
                        mount_args[destination] = int(mount["TmpfsOptions"][source])

            mounts_arg.append(Mount(**mount_args))
        run_args["mounts"] = mounts_arg

    if sysctls := container.attrs["HostConfig"].get("Sysctls", None):
        run_args["sysctls"] = sysctls

    if tmpfs := container.attrs["HostConfig"].get("Tmpfs", None):
        run_args["tmpfs"] = tmpfs

    if ulimits := container.attrs["HostConfig"].get("Ulimits", None):
        run_args["ulimits"] = [
            Ulimit(**{k.lower(): v for k, v in ulimit.items()}) for ulimit in ulimits
        ]

    # https://github.com/containrrr/watchtower/blob/9b28fbc24ddad49b29146fe48fad78cac02838e4/pkg/container/container.go
    # https://github.com/containrrr/watchtower/blob/9b28fbc24ddad49b29146fe48fad78cac02838e4/internal/util/util.go
    if container.attrs["Config"]["WorkingDir"] == image.attrs["Config"]["WorkingDir"]:
        run_args["working_dir"] = ""

    if container.attrs["HostConfig"]["NetworkMode"].startswith("container:"):
        run_args["hostname"] = ""
        run_args["network_mode"] = f"container:{parent_id}"

    if container.attrs["Config"]["Entrypoint"] == image.attrs["Config"]["Entrypoint"]:
        run_args["entrypoint"] = None
        if container.attrs["Config"]["Cmd"] == image.attrs["Config"]["Cmd"]:
            run_args["command"] = None

    run_args["environment"] = [
        v
        for v in container.attrs["Config"].get("Env", [])
        if v not in image.attrs["Config"].get("Env", [])
    ]

    container_labels = container.attrs["Config"]["Labels"] or {}
    image_labels = image.attrs["Config"]["Labels"] or {}
    run_args["labels"] = {
        k: v
        for k, v in container_labels.items()
        if k not in image_labels or image_labels[k] != v
    }

    if binds := container.attrs["HostConfig"].get("Binds", None):
        run_args["volumes"] = binds

    for v in image.attrs["Config"].get("Volumes", {}) or {}:
        for i in range(len(run_args.get("mounts", []))):
            if run_args["mounts"][i]["Target"] == v:
                del run_args["mounts"][i]
                break

    # // subtract ports exposed in image from container
    # for k := range config.ExposedPorts {
    # 	if _, ok := imageConfig.ExposedPorts[k]; ok {
    # 		delete(config.ExposedPorts, k)
    # 	}
    # }
    # for p := range c.containerInfo.HostConfig.PortBindings {
    # 	config.ExposedPorts[p] = struct{}{}
    # }

    return run_args


client = docker.from_env()


def list_with_retry(*args, **kwargs):
    while True:
        try:
            return client.containers.list(*args, **kwargs)
        except docker.errors.NotFound as err:
            logging.info(
                f"Failed to retrieve containers. Retrying in one second. Error: {err}"
            )
            time.sleep(1)
