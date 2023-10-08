import queue

from restarter.rwlock import RWLock


# Keeps only the most recent element
class CoalescingQueue(queue.Queue):
    def __init__(self):
        self._lock = RWLock()
        super().__init__()

    def put(self, item):
        try:
            self.get(block=False)
        except queue.Empty:
            pass
        super().put(item)
