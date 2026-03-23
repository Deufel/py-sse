import marimo

__generated_with = "0.21.1"
app = marimo.App()

with app.setup:
    from __future__ import annotations
 
    import asyncio
    import threading
    from fnmatch import fnmatch
 


@app.function
def create_relay():
    """Create a pub/sub relay for broadcasting events.
 
    Returns a namespace with `publish` and `subscribe` callables.
 
    Usage:
        relay = create_relay()
 
        # In a command handler (write side):
        relay.publish("todo.created", todo_item)
 
        # In an SSE stream (read side):
        async for topic, data in relay.subscribe("todo.*"):
            yield patch_elements(render_todo(data))
 
    publish() is thread-safe and synchronous — safe to call from
    background threads, sync handlers, or database hooks.
 
    subscribe() is an async generator that yields (topic, data) tuples.
    It cleans up automatically when the consumer is cancelled or closed.
    """
    subs: list[tuple[str, asyncio.Queue]] = []
    lock = threading.Lock()
 
    def publish(topic: str, data):
        """Publish an event to all matching subscribers.
 
        Thread-safe. Non-blocking. If a subscriber's queue is full,
        the event is silently dropped for that subscriber — the publisher
        is never blocked or crashed by a slow consumer.
        """
        with lock:
            targets = [(p, q) for p, q in subs if fnmatch(topic, p)]
        for _, queue in targets:
            try:
                queue.put_nowait((topic, data))
            except Exception:
                pass
 
    async def subscribe(pattern: str):
        """Subscribe to events matching a glob pattern.
 
        Yields (topic, data) tuples. Cleans up on cancellation.
        Pattern uses fnmatch syntax: * matches everything,
        ? matches one character, [seq] matches any char in seq.
        """
        queue = asyncio.Queue()
        with lock:
            subs.append((pattern, queue))
        try:
            while True:
                yield await queue.get()
        except (asyncio.CancelledError, GeneratorExit):
            pass
        finally:
            with lock:
                try:
                    subs.remove((pattern, queue))
                except ValueError:
                    pass
 
    # Return as a simple namespace — attribute access, not dict indexing
    class _Relay:
        """Thin namespace. Not a class hierarchy — just attribute access."""
        __slots__ = ("publish", "subscribe")
    relay = _Relay()
    relay.publish = publish
    relay.subscribe = subscribe
    return relay


@app.cell
def _():
    return


if __name__ == "__main__":
    app.run()
