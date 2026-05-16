import marimo

__generated_with = "0.23.6"
app = marimo.App()

with app.setup:

    import atexit, os, threading
    import apsw
    from a_server import Changes


@app.class_definition
class Database:
    """SQLite (APSW) wrapper. Per-thread connections. Notification of
    changes is fired by `execute()` itself, after SQLite has committed,
    by calling `changes.notify()`.

    The set of subscribers is implicit: it's the set of threads currently
    parked in `changes.wait()`. A dropped connection ends its handler
    thread, which ends the subscription. No registry, no list.

    Why notify from execute() and not from an APSW update_hook:
        update_hook fires *during* the write, before commit. A reader
        woken by the hook will see the pre-commit snapshot and render
        the stale state — the "one transaction behind" symptom.
        Notifying after execute() returns guarantees the commit has
        landed before any reader wakes.

    Handler code:
        db.execute("INSERT INTO msgs ...", (...))   # writes (auto-notify)
        db.changes.wait(timeout=15)                  # readers park
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
            self._tls.conn = conn
        return conn

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
        cur = self._conn().execute(sql, params)
        # Notify after execute returns: SQLite has committed by now, so
        # any reader we wake will see the new state. Reads also notify,
        # which is wasteful but harmless — waiters wake, re-render the
        # same thing, sleep again.
        self.changes.notify()
        return cur

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
