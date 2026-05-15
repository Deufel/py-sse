import marimo

__generated_with = "0.23.6"
app = marimo.App()

with app.setup:
    import atexit, os, threading
    import apsw

    from a_server import Changes


@app.cell
def _():
    import marimo as mo

    return


@app.class_definition
class Database:
    """SQLite (APSW) wrapper. Per-thread connections. Every connection
    gets an update_hook installed at open time, and the hook does exactly
    one thing: call `changes.notify()`.

    The result: any thread that writes to this DB wakes every thread
    currently parked in `changes.wait()`. No table-topic mapping, no
    subscriber registry, no writer thread, no queue. The set of
    subscribers is implicit — it's whichever threads happen to be
    parked at the moment.

    Handler code stays trivial:
        db.execute("INSERT INTO msgs ...", (...))   # writes
        changes.wait(timeout=15)                    # readers park
    """

    def __init__(self, path, schema="", changes=None,
                 dev_mode=False, remove_on_exit=False, busy_timeout=5000):
        self.path = path
        self.schema = schema or ""
        self.changes = changes or Changes()
        self.dev_mode = dev_mode
        self.remove_on_exit = remove_on_exit or dev_mode
        self.busy_timeout = busy_timeout
        self._tls = threading.local()
        # Apply schema once at startup on a throwaway connection so a
        # cold DB is ready before the first request lands.
        if self.schema:
            self._init_schema()
        if self.remove_on_exit:
            atexit.register(self.cleanup)

    def _conn(self):
        conn = getattr(self._tls, "conn", None)
        if conn is None:
            conn = apsw.Connection(self.path)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute(f"PRAGMA busy_timeout={int(self.busy_timeout)}")
            # The one line that makes everything work: when this
            # connection commits a write, ring the bell. APSW fires the
            # update_hook on the connection that performed the write,
            # which is always this one for this thread, so installing
            # the hook here covers every write this thread will ever do.
            conn.set_update_hook(self._on_change)
            self._tls.conn = conn
        return conn

    def _on_change(self, *_):
        # APSW passes (op, dbname, table, rowid); we don't care which.
        # Any change is a change. Notify everyone parked on changes.wait().
        self.changes.notify()

    def _init_schema(self):
        conn = apsw.Connection(self.path)
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            for sql in self.schema.strip().split(";"):
                sql = sql.strip()
                if sql:
                    conn.execute(sql)
        finally:
            conn.close()

    def conn(self):
        return self._conn()

    def execute(self, sql, params=()):
        return self._conn().execute(sql, params)

    def one(self, sql, params=()):
        return self.execute(sql, params).fetchone()

    def all(self, sql, params=()):
        return self.execute(sql, params).fetchall()

    def transaction(self):
        return self._conn().transaction()

    def cleanup(self):
        try:
            conn = getattr(self._tls, "conn", None)
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass
                self._tls.conn = None
        finally:
            if self.remove_on_exit and self.path:
                for suf in ("", "-wal", "-shm"):
                    p = self.path + suf
                    try:
                        if os.path.exists(p):
                            os.remove(p)
                    except Exception:
                        pass


@app.cell
def _():
    return


if __name__ == "__main__":
    app.run()
