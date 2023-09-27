def get_self_id():
    with open("/proc/self/mountinfo") as file:
        while line := file.readline().strip():
            if "/docker/containers/" in line:
                return line.split("/docker/containers/")[-1].split("/")[0]
