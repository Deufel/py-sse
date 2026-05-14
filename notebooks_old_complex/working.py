import marimo

__generated_with = "0.23.6"
app = marimo.App()

with app.setup:

    from b_sse import patch_elements, patch_signals, remove_signals, execute_script
    from a_app import create_app, signals
    from c_mserver import serve_background, stop_background

    from html_tags import h

    import requests, asyncio
    from dataclasses import dataclass, field

    from html_tags import render as h_render

    DATASTAR = 'https://cdn.jsdelivr.net/gh/starfederation/datastar@v1.0.1/bundles/datastar.js'


@app.cell
def _():
    import marimo as mo

    return (mo,)


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    # DB as Cache Test
    """)
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## Dataclass
    """)
    return


@app.class_definition
@dataclass
class PatchElements:
    "datastar-patch-elements SSE event"
    elements: str
    selector: str|None = None
    mode: str|None = None
    namespace: str|None = None
    use_view_transition: bool|None = None
    def render(self): return patch_elements(self.elements, selector=self.selector, mode=self.mode, namespace=self.namespace, use_view_transition=self.use_view_transition)


@app.class_definition
@dataclass
class PatchSignals:
    "datastar-patch-signals SSE event"
    signals: dict|str
    only_if_missing: bool|None = None
    def render(self): return patch_signals(self.signals, only_if_missing=self.only_if_missing)


@app.class_definition
@dataclass
class RemoveSignals:
    "datastar-remove-signals SSE event"
    names: tuple
    def render(self): return remove_signals(*self.names)


@app.class_definition
@dataclass
class ExecuteScript:
    "datastar-execute-script SSE event"
    script: str
    auto_remove: bool = True
    attributes: dict|None = None
    def render(self): return execute_script(self.script, auto_remove=self.auto_remove, attributes=self.attributes)


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## APSW Database Cache + other.../ pubsub
    """)
    return


@app.cell
def _():

    import logging, apsw, apsw.bestpractice, apsw.ext

    from typing import Any, Callable

    log = logging.getLogger(__name__)
    apsw.bestpractice.apply(apsw.bestpractice.recommended)
    apsw.ext.log_sqlite()

    def create_db(path):
        "Open a SQLite connection with WAL + best practices"
        conn = apsw.Connection(path)
        conn.pragma("journal_mode", "wal")
        return conn

    def migrate(conn, schema_sql):
        "Apply schema idempotently"
        with conn: conn.execute(schema_sql)

    def query(conn, sql, bindings=(), *, limit=1000):
        "Run a SELECT and return up to limit rows as tuples"
        rows = []
        for row in conn.execute(sql, bindings):
            rows.append(row)
            if len(rows) >= limit: break
        return rows

    def write(conn, fn, *args):
        "Run fn(conn, *args) in a transaction"
        with conn: return fn(conn, *args)

    @dataclass
    class Raw:
        "Pre-rendered SSE event"
        body: str
        def render(self): return self.body


    class Broadcaster:
        def __init__(self, render_fn):
            self._render_fn = render_fn
            self._cached = None
            self._event = None       # bind lazily
            self._pending = False
            self._loop = None        # capture on first notify

        def _ensure_event(self):
            if self._event is None:
                self._event = asyncio.Event()

        def _do_notify(self):
            self._pending = False
            try:
                ev = self._render_fn()
                self._cached = Raw(ev.render() if ev is not None else "")
            except Exception:
                log.exception("Broadcaster: render_fn raised")
                self._cached = None
            self._ensure_event()
            old, self._event = self._event, asyncio.Event()
            old.set()

        def _notify(self):
            # Called via call_soon_threadsafe from update_hook; always on loop thread.
            if self._pending: return
            self._pending = True
            self._loop.call_soon(self._do_notify)

        def current(self): return self._cached

        async def subscribe(self):
            self._ensure_event()
            try:
                while True:
                    ev = self._event
                    await ev.wait()
                    yield self._cached
            except (asyncio.CancelledError, GeneratorExit):
                pass

    class DbRelay:
        def __init__(self, conn, loop, render_fn=None):
            self._conn = conn
            self._loop = loop
            self.broadcaster = Broadcaster(render_fn or (lambda: None))
            self.broadcaster._loop = loop      # explicit
            conn.set_update_hook(self._on_db_write)

        def _on_db_write(self, op_type, db_name, table_name, rowid):
            self._loop.call_soon_threadsafe(self.broadcaster._notify)

    class DbRelay:
        "Bridge SQLite update_hook to asyncio subscribers"
        def __init__(self, conn, loop, render_fn=None):
            self._conn, self._loop = conn, loop
            self.broadcaster = Broadcaster(render_fn or (lambda: None))
            self.broadcaster._loop = loop          # ← only new line
            conn.set_update_hook(self._on_db_write)

        def _on_db_write(self, op_type, db_name, table_name, rowid):
            self._loop.call_soon_threadsafe(self.broadcaster._notify)

        def set_render_fn(self, render_fn): self.broadcaster._render_fn = render_fn

        def close(self): self._conn.set_update_hook(None)

    def create_db_relay(conn, loop, render_fn=None):
        "Create a DbRelay wired to conn's update_hook"
        return DbRelay(conn, loop, render_fn)


    return create_db, create_db_relay, log, migrate, query, write


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    # Cache 2.0
    """)
    return


@app.cell
def _(log):
    class Cache:
        """Bridges SQLite update_hook to asyncio subscribers.
        Holds the most recently rendered view as a string, ready for zero-copy send.
        """
        def __init__(self, db, loop, render_fn):
            self._db, self._loop = db, loop
            self._render = render_fn
            self._rendered = None         # str — the cached, pre-formatted SSE event
            self._event = None
            self._pending = False
            db.set_update_hook(self._on_write)

        def _on_write(self, *_):
            if self._pending: return
            self._pending = True
            self._loop.call_soon_threadsafe(self._do_render)

        def _do_render(self):
            self._pending = False
            try:
                self._rendered = self._render()    # returns a string, already SSE-formatted
            except Exception:
                log.exception("Cache render failed")
                return
            if self._event is None: return
            old, self._event = self._event, asyncio.Event()
            old.set()

        @property
        def current(self): return self._rendered

        async def changes(self):
            if self._event is None: self._event = asyncio.Event()
            try:
                while True:
                    ev = self._event
                    await ev.wait()
                    yield self._rendered
            except (asyncio.CancelledError, GeneratorExit): pass

        def close(self): self._db.set_update_hook(None)

    return


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    # Test application
    """)
    return


@app.cell
def _():


    return


@app.cell
def _():
    return


@app.cell
def _(create_db, create_db_relay, migrate, query, write):
    SCHEMA = "CREATE TABLE IF NOT EXISTS msgs (id INTEGER PRIMARY KEY, txt TEXT NOT NULL, ts REAL NOT NULL DEFAULT (unixepoch('now','subsec')))"

    db = create_db('chat.db')
    migrate(db, SCHEMA)

    def render_feed():
        rows = query(db, "SELECT txt FROM msgs ORDER BY id DESC LIMIT 50")
        return PatchElements(h_render(h.div(*[h.p(r[0]) for r in rows], id='msgs')))


    def startup(loop):
        global relay
        relay = create_db_relay(db, loop, render_feed)
        relay.broadcaster._notify()

    app = create_app(on_init=startup)

    @app.get('/')
    async def index(req):
        page = h.html(
            h.head(h.title('db chat'), h.script(type='module', src=DATASTAR)),
            h.body({'data-init': "@get('/feed')"},
                h.h1('db chat'),
                h.div(id='msgs'),
                h.form({'data-on:submit__prevent': "@post('/say'); $text=''"},
                    h.input({'data-bind:text': True}, name='text', placeholder='say something'),
                    h.button('send', type='submit'))))
        return h_render(page)

    @app.post('/say')
    async def say(req):
        s = await signals(req)
        if s.get('text'): write(db, lambda c: c.execute("INSERT INTO msgs(txt) VALUES(?)", (s['text'],)))
        return None

    @app.get('/feed')
    async def feed(req):
        cur = relay.broadcaster.current()
        if cur: yield cur
        async for ev in relay.broadcaster.subscribe():
            if ev: yield ev

    srv = serve_background(app, host='127.0.0.1', port=8000)



    #stop_background(srv)
    return (srv,)


@app.cell
def _(srv):
    stop_background(srv)
    return


@app.cell
def _():
    return


if __name__ == "__main__":
    app.run()
