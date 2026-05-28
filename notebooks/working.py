import marimo

__generated_with = "0.23.6"
app = marimo.App()

with app.setup:
    from a_app import create_app, signals
    from b_sse import patch_elements
    from c_db import create_db, migrate, query, write, Changes
    from d_mserver import serve_background, stop_background

    from html_tags import h
    from html_tags import render as h_render

    DATASTAR = 'https://cdn.jsdelivr.net/gh/starfederation/datastar@v1.0.1/bundles/datastar.js'


@app.cell
def _():
    import marimo as mo

    return (mo,)


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    # DB-as-cache chat demo

    One SQLite table, one `Changes` primitive, two routes.
    Re-renders the feed on every write. No `Broadcaster`, no `Raw`,
    no wrapper dataclasses — `@app.stream` handles SSE framing.
    """)
    return


@app.cell
def _():
    SCHEMA = """
    CREATE TABLE IF NOT EXISTS msgs (
        id  INTEGER PRIMARY KEY,
        txt TEXT NOT NULL,
        ts  REAL NOT NULL DEFAULT (unixepoch('now', 'subsec'))
    )
    """

    db = create_db('chat.db')
    migrate(db, SCHEMA)
    return (db,)


@app.cell
def _(db):
    # Defensive cleanup in case prior run didn't tear down (marimo re-runs).
    try: changes.close()
    except NameError: pass
    try: stop_background(srv)
    except NameError: pass

    def startup(loop):
        global changes
        changes = Changes(db, loop)

    def shutdown(loop):
        try: changes.close()
        except Exception: pass

    chat_app = create_app(on_init=startup, on_del=shutdown)

    @chat_app.get('/')
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

    @chat_app.post('/say')
    async def say(req):
        s = await signals(req)
        if s.get('text'):
            write(db, lambda c: c.execute("INSERT INTO msgs(txt) VALUES(?)", (s['text'],)))

    @chat_app.stream('/feed', on=lambda: changes)
    def feed(req):
        rows = query(db, "SELECT txt FROM msgs ORDER BY id DESC LIMIT 50")
        return h.div(*[h.p(r[0]) for r in rows], id='msgs')

    srv = serve_background(chat_app, host='127.0.0.1', port=8000)
    try:
        input("server running on :8000 — press Enter to stop")
    finally:
        try: changes.close()
        except Exception: pass
        stop_background(srv)
    return changes, srv


@app.cell
def _():
    return


if __name__ == "__main__":
    app.run()
