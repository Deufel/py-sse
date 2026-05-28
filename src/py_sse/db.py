import asyncio
import logging
import apsw
import apsw.bestpractice
import apsw.ext

log = logging.getLogger(__name__)

"""SQLite helpers and the `Changes` primitive that bridges SQLite's update_hook
    to asyncio waiters, so `@app.stream` can re-render on each write.

    `Changes` does one thing: turn DB writes into a coalesced asyncio signal. No
    rendering, no caching, no broadcasting — the route handler decides what to
    render on each tick.
    """
apsw.bestpractice.apply(apsw.bestpractice.recommended)
apsw.ext.log_sqlite()

def create_db(
    path:str, # path to the SQLite file (created if absent)
)->apsw.Connection: # an open WAL-mode connection
    "Open a SQLite connection with WAL + apsw best practices."
    conn = apsw.Connection(path)
    conn.pragma("journal_mode", "wal")
    return conn

def migrate(
    conn:apsw.Connection, # target connection
    schema_sql:str,       # one or more DDL statements
)->None:
    "Apply schema idempotently in one transaction."
    with conn: conn.execute(schema_sql)

def query(
    conn:apsw.Connection,  # connection to read from
    sql:str,               # a SELECT statement
    bindings:tuple=(),     # parameters bound to `?` placeholders
    *,
    limit:int=1000,        # stop after this many rows
)->list:                   # rows as tuples
    "Run a SELECT and return up to `limit` rows as tuples."
    rows = []
    for row in conn.execute(sql, bindings):
        rows.append(row)
        if len(rows) >= limit: break
    return rows

def write(
    conn:apsw.Connection, # connection to write to
    fn,                   # called as fn(conn, *args) inside the transaction
    *args,                # extra positional args forwarded to fn
):                        # returns whatever fn returns
    "Run fn(conn, *args) in a transaction; returns fn's result."
    with conn: return fn(conn, *args)

class Changes:
    "Bridge SQLite's update_hook to asyncio waiters; coalesces writes within a tick into one wake."
    def __init__(self,
        db:apsw.Connection,             # connection whose writes to observe
        loop:asyncio.AbstractEventLoop, # loop that will own the waiters (pass on_init's loop)
    ):
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
        "Async iterator: yields once per coalesced DB change."
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
