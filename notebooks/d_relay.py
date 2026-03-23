import marimo

__generated_with = "0.21.1"
app = marimo.App()

with app.setup:
    import asyncio
    from fnmatch import fnmatch
    import threading



@app.class_definition
class Relay:
    __slots__ = ('_subs', '_lock')

    def __init__(self):
        self._subs = []
        self._lock = threading.Lock()

    def publish(self, topic, data):
        with self._lock:
            targets = [(p, q) for p, q in self._subs if fnmatch(topic, p)]
        loop = asyncio.get_event_loop()
        for _, queue in targets:
            loop.call_soon_threadsafe(queue.put_nowait, (topic, data))

    async def subscribe(self, pattern):
        queue = asyncio.Queue()
        with self._lock:
            self._subs.append((pattern, queue))
        try:
            while True:
                yield await queue.get()
        except (asyncio.CancelledError, GeneratorExit):
            pass
        finally:
            with self._lock:
                self._subs.remove((pattern, queue))


@app.cell
def _():
    return


if __name__ == "__main__":
    app.run()
