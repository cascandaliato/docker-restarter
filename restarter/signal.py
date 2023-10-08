import queue

from restarter.rwlock import RWLock


class Signal(queue.Queue):
    def __init__(self):
        self._lock = RWLock()
        super().__init__()

    def set(self, payload=None):
        try:
            self.get(block=False)
        except queue.Empty:
            pass
        super().put(payload)
