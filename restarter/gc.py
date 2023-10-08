import logging
import threading
import time

import restarter.config as config
from restarter.workers import lock as workers_lock


def gc(workers):
    while True:
        start = time.time()
        logging.info(f"Garbage collection... Starting")

        logging.info(f"Number of threads: {threading.active_count()}")
        with workers_lock.w_locked():
            for name in list(workers.keys()):
                with workers[name].lock:
                    if workers[name].work.empty() and workers[name].done.is_set():
                        logging.info(
                            f"Worker for container {name} is not required anymore."
                        )
                        workers[name].work.put(None)
                        del workers[name]

        logging.info(f"Garbage collection... Done ({round(time.time() - start, 1)}s)")
        time.sleep(config.global_settings[config.GlobalSetting.GC_EVERY_SECONDS])
