import logging
import threading
import time

import restarter.config as config


def gc(workers):
    while True:
        start = time.time()
        logging.info(f"Garbage collection... Starting")
        active_threads = sorted(t.name for t in threading.enumerate())
        logging.info(
            f"Active threads ({len(active_threads)}): {', '.join(active_threads)}."
        )
        with workers.lock:
            for name in list(workers.keys()):
                worker = workers[name]
                with worker.lock:
                    if worker.work.empty() and worker.done.is_set():
                        logging.info(
                            f"Worker for container {name} is not required anymore."
                        )
                        worker.work.put(None)  # tell the thread to shutdown
                        del workers[name]

        logging.info(f"Garbage collection... Done ({round(time.time() - start, 1)}s)")
        time.sleep(config.global_settings[config.GlobalSetting.GC_EVERY_SECONDS])
