import marimo

__generated_with = "0.23.6"
app = marimo.App()

with app.setup:

    from b_sse import patch_elements, patch_signals, remove_signals, execute_script
    from a_app import create_app
    from c_mserver import serve_background, stop_background

    from html_tags import h

    import requests, asyncio
    from dataclasses import dataclass, field

    from html_tags import render as h_render

    DATASTAR = 'https://cdn.jsdelivr.net/gh/starfederation/datastar@v0.0.1/bundles/datastar.js'


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
        "Render once per write, fan out the cached event to all subscribers"
        def __init__(self, render_fn):
            self._render_fn,self._cached,self._event,self._pending = render_fn,None,asyncio.Event(),False

        def _do_notify(self):
            self._pending = False
            try:
                ev = self._render_fn()
                self._cached = Raw(ev.render() if ev is not None else "")
            except Exception:
                log.exception("Broadcaster: render_fn raised")
                self._cached = None
            old,self._event = self._event,asyncio.Event()
            old.set()

        def _notify(self):
            if self._pending: return
            self._pending = True
            asyncio.get_event_loop().call_soon(self._do_notify)

        def current(self): return self._cached

        async def subscribe(self):
            "Yield the cached event on every DB write"
            try:
                while True:
                    await self._event.wait()
                    yield self._cached
            except (asyncio.CancelledError, GeneratorExit): pass

    class DbRelay:
        "Bridge SQLite update_hook to asyncio subscribers"
        def __init__(self, conn, loop, render_fn=None):
            self._conn,self._loop = conn,loop
            self.broadcaster = Broadcaster(render_fn or (lambda: None))
            conn.set_update_hook(self._on_db_write)

        def _on_db_write(self, op_type, db_name, table_name, rowid):
            self._loop.call_soon_threadsafe(self.broadcaster._notify)

        def set_render_fn(self, render_fn): self.broadcaster._render_fn = render_fn

        def close(self): self._conn.set_update_hook(None)

    def create_db_relay(conn, loop, render_fn=None):
        "Create a DbRelay wired to conn's update_hook"
        return DbRelay(conn, loop, render_fn)


    return Broadcaster, DbRelay


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
def _(Broadcaster, DbRelay, signals):
    cursors = {}

    def render_cursors():
        dots = [h.div({'style': f'position:absolute;left:{x}px;top:{y}px;width:8px;height:8px;border-radius:50%;background:#f33;pointer-events:none'}) for x,y in cursors.values()]
        return PatchElements(h_render(h.div(*dots, id='board')))

    def startup(loop):
        global relay
        relay = DbRelay.__new__(DbRelay)
        relay.broadcaster = Broadcaster(render_cursors)

    app = create_app(on_init=startup)

    @app.get('/')
    async def index(req):
        page = h.html(
            h.head(h.title('cursors'), h.script(type='module', src=DATASTAR)),
            h.body({'data-init': "@get('/stream')", 'data-on:mousemove__throttle.50ms': "@post('/move', {contentType:'json', filterSignals:{include:/^(x|y)$/}})"},
                h.div(id='board', style='position:fixed;inset:0')))
        return h_render(page)

    @app.post('/move')
    async def move(req):
        s = await signals(req)
        cursors[req['client']] = (s.get('x',0), s.get('y',0))
        relay.broadcaster._notify()
        return None

    @app.get('/stream')
    async def stream(req):
        cur = relay.broadcaster.current()
        if cur: yield cur
        async for ev in relay.broadcaster.subscribe():
            if ev: yield ev

    srv = serve_background(app, host='127.0.0.1', port=8000)


    input("wait")
    stop_background(srv)
    return


@app.cell
def _():
    return


if __name__ == "__main__":
    app.run()
