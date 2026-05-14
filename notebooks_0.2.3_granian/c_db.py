import marimo

__generated_with = "0.23.6"
app = marimo.App()

with app.setup:
    """SQLite helpers and the `Changes` primitive that bridges SQLite's
    update_hook to asyncio waiters, so `@app.stream` can re-render on each write.

    `Changes` does one thing: turn DB writes into a coalesced asyncio signal.
    No rendering, no caching, no broadcasting. The route handler decides what
    to render on each tick.
    """
    import asyncio
    import logging

    import apsw
    import apsw.bestpractice
    import apsw.ext

    log = logging.getLogger(__name__)
    apsw.bestpractice.apply(apsw.bestpractice.recommended)
    apsw.ext.log_sqlite()



@app.cell
def _():
    import marimo as mo

    return


@app.function
def create_db(path: str) -> apsw.Connection:
    "Open a SQLite connection with WAL + apsw best practices."
    conn = apsw.Connection(path)
    conn.pragma("journal_mode", "wal")
    return conn


@app.function
def migrate(conn: apsw.Connection, schema_sql: str) -> None:
    "Apply schema idempotently in one transaction."
    with conn: conn.execute(schema_sql)


@app.function
def query(conn: apsw.Connection, sql: str, bindings: tuple = (), *, limit: int = 1000) -> list:
    "Run a SELECT and return up to `limit` rows as tuples."
    rows = []
    for row in conn.execute(sql, bindings):
        rows.append(row)
        if len(rows) >= limit: break
    return rows


@app.function
def write(conn: apsw.Connection, fn, *args):
    "Run fn(conn, *args) in a transaction; returns fn's result."
    with conn: return fn(conn, *args)


@app.class_definition
class Changes:
    """Bridges SQLite update_hook to asyncio waiters.

    Each DB write wakes all current waiters once. Multiple writes within the
    same event-loop tick coalesce into a single wake (cheap; avoids thundering
    herd during transactions).

    Must be constructed inside or with a reference to the loop that will own
    the waiters (typically inside `on_init(loop)` of the RSGI app).
    """
    def __init__(self, db: apsw.Connection, loop: asyncio.AbstractEventLoop):
        self._db = db
        self._loop = loop
        self._event = None       # bound lazily on first wait()
        self._pending = False
        db.set_update_hook(self._on_write)

    # ── SQLite-side, called from apsw's thread ─────────────
    def _on_write(self, *_):
        self._loop.call_soon_threadsafe(self._schedule)

    # ── loop-side, called via call_soon_threadsafe ─────────
    def _schedule(self):
        if self._pending: return
        self._pending = True
        self._loop.call_soon(self._swap)

    def _swap(self):
        self._pending = False
        if self._event is None: return        # nobody waiting yet
        old, self._event = self._event, asyncio.Event()
        old.set()

    async def wait(self):
        """Async iterator: yields once per coalesced DB change.

        Usage in a stream handler:

            async for _ in changes.wait():
                yield patch_elements(render_view())
        """
        if self._event is None:
            self._event = asyncio.Event()
        try:
            while True:
                ev = self._event
                await ev.wait()
                yield
        except (asyncio.CancelledError, GeneratorExit):
            pass

    def close(self):
        "Detach the update_hook. Safe to call multiple times."
        try: self._db.set_update_hook(None)
        except Exception: pass


@app.cell
def _():
    return


if __name__ == "__main__":
    app.run()
