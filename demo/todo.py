import marimo

__generated_with = "0.21.1"
app = marimo.App(width="full")

with app.setup:
    import hashlib, os, apsw

    from py_sse import create_app, serve, static

    from html_tags import setup_tags, to_html
    setup_tags()
    Html, Head, Body, Main, Nav, Div, P, Span, A, H1, H2, H3, Strong, Button, Style, Script, Meta, Input, Title, Link = Html, Head, Body, Main, Nav, Div, P, Span, A, H1, H2, H3, Strong, Button, Style, Script, Meta, Input, Title, Link

    LOGO = """<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="lucide lucide-stamp-icon lucide-stamp"><path d="M14 13V8.5C14 7 15 7 15 5a3 3 0 0 0-6 0c0 2 1 2 1 3.5V13"/><path d="M20 15.5a2.5 2.5 0 0 0-2.5-2.5h-11A2.5 2.5 0 0 0 4 15.5V17a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1z"/><path d="M5 22h14"/></svg>"""


    app = create_app()
    static(app, "/static", "./static")


@app.cell
def _():
    import marimo as mo

    return (mo,)


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    # To Do
    ## Database
    """)
    return


@app.function
# ── passwords ──────────────────────────────────────────────────────────────

def hash_password(pwd: str) -> str:
    salt = os.urandom(16)
    key  = hashlib.pbkdf2_hmac("sha256", pwd.encode(), salt, 260_000)
    return salt.hex() + ":" + key.hex()


@app.function
def verify_password(pwd: str, stored: str) -> bool:
    salt, key = stored.split(":")
    return hashlib.pbkdf2_hmac("sha256", pwd.encode(), bytes.fromhex(salt), 260_000).hex() == key


@app.function
# ── users ──────────────────────────────────────────────────────────────────

def create_user(db, username: str, pwd: str) -> int:
    db.execute("INSERT INTO users (username, password_hash) VALUES (?,?)", (username, hash_password(pwd)))
    return db.last_insert_rowid()


@app.function
def get_user(db, username: str):
    return next(db.execute("SELECT * FROM users WHERE username=?", (username,)), None)


@app.function
def login(db, username: str, pwd: str):
    u = get_user(db, username)
    return u if u and verify_password(pwd, u[2]) else None


@app.function
# ── lists ──────────────────────────────────────────────────────────────────

def create_list(db, user_id: int, name: str) -> int:
    db.execute("INSERT INTO lists (user_id, name) VALUES (?,?)", (user_id, name))
    return db.last_insert_rowid()


@app.function
def get_lists(db, user_id: int):
    return db.execute("SELECT * FROM lists WHERE user_id=? ORDER BY created_at", (user_id,)).fetchall()


@app.function
def delete_list(db, list_id: int, user_id: int):
    db.execute("DELETE FROM lists WHERE id=? AND user_id=?", (list_id, user_id))


@app.function
# ── items ──────────────────────────────────────────────────────────────────

def create_item(db, list_id: int, text: str, time_budget: int | None = None) -> int:
    db.execute("INSERT INTO items (list_id, text, time_budget) VALUES (?,?,?)", (list_id, text, time_budget))
    return db.last_insert_rowid()


@app.function
def get_items(db, list_id: int):
    return db.execute("SELECT * FROM items WHERE list_id=? ORDER BY created_at", (list_id,)).fetchall()


@app.function
def toggle_done(db, item_id: int):
    db.execute("UPDATE items SET done = NOT done WHERE id=?", (item_id,))


@app.function
def delete_item(db, item_id: int):
    db.execute("DELETE FROM items WHERE id=?", (item_id,))


@app.function
# ── time entries ───────────────────────────────────────────────────────────

def start_tracking(db, item_id: int) -> int:
    db.execute("INSERT INTO time_entries (item_id) VALUES (?)", (item_id,))
    return db.last_insert_rowid()


@app.function
def stop_tracking(db, item_id: int):
    db.execute("UPDATE time_entries SET stopped_at=unixepoch() WHERE item_id=? AND stopped_at IS NULL", (item_id,))


@app.function
def get_time_entries(db, item_id: int):
    return db.execute("SELECT * FROM time_entries WHERE item_id=? ORDER BY started_at", (item_id,)).fetchall()


@app.function
def total_seconds(db, item_id: int) -> int:
    row = next(db.execute("""
        SELECT COALESCE(SUM(COALESCE(stopped_at, unixepoch()) - started_at), 0)
        FROM time_entries WHERE item_id=?""", (item_id,)))
    return row[0]


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## Render
    """)
    return


@app.function
def render_home(user):
    auth_btn = (
        A({"href": "/lists", "class": "btn fill"}, "Continue")
        if user else
        Div({"class": "row"},
            A({"href": "/login",  "class": "btn"}, "Log in"),
            A({"href": "/signup", "class": "btn fill"}, "Sign up"))
    )
    return Html(
        Head(
            Meta({"charset": "UTF-8"}),
            Meta({"name": "viewport", "content": "width=device-width, initial-scale=1.0"}),
            Title("DONE"),
            Link({"rel": "stylesheet", "href": "/static/style.css"}),

        Body(
            Nav({"class": "split ai-center", "style": "padding: var(--space-1) var(--space-2);"},
                A({"href": "/", "class": "row ai-center", "style": "--_gap: 0.5rem"},
                    Span({"style": "color: currentColor"}, LOGO),
                    Strong("DONE")),
                auth_btn),
            Main({"class": "stack ai-center jc-center", "style": "min-height: 80vh"},
                H1("DONE"),
                P({"style": "font-style: italic; opacity: 0.6"}, "~get shit done~"))))
    )


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## Router
    """)
    return


@app.function
@app.get("/")
async def home(req):
    return to_html(render_home(req.get("user")))


@app.cell
def _():
    #| raw

    if __name__ == "__main__":
        serve(app, port=8000)
    return


if __name__ == "__main__":
    app.run()
