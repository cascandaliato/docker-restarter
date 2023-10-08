import queue


class Signal(queue.Queue):
    def set(self, payload=None):
        try:
            self.get(block=False)
        except queue.Empty:
            pass
        super().put(payload)
