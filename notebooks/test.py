import marimo

__generated_with = "0.23.6"
app = marimo.App()

with app.setup:
    
    # Import from the dev module names (a_app, b_sse, ...). The builder rewrites
    # these to the package paths on build; tests run against the dev layout.
    # This cell has imports, so the test runner skips it — it only feeds the
    # functions-under-test into the test cells below via marimo's dataflow.
    from a_app import create_signer
    from b_sse import (
        patch_elements,
        patch_signals,
        remove_signals,
        execute_script,
        redirect,
    )
    from c_db import create_db, migrate, query, write
    from e_ngrok import load_env


@app.cell
def _():
    import marimo as mo

    return (mo,)


@app.cell
def _():
    # Testing
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## SSE event formatters (b_sse).
    > Pure string functions — assert on framing.
    """)
    return


@app.cell
def _():
    def test_patch_elements_basic():
        out = patch_elements("<div>hi</div>")
        assert out.startswith("event: datastar-patch-elements\n")
        assert "data: elements <div>hi</div>" in out
        assert out.endswith("\n\n")

    def test_patch_elements_prefixes_each_line():
        out = patch_elements("<ul>\n<li>a</li>\n</ul>")
        assert out.count("data: elements ") == 3

    def test_patch_elements_emits_options():
        out = patch_elements(
            "<p>x</p>", selector="#app", mode="append", use_view_transition=True
        )
        assert "data: selector #app" in out
        assert "data: mode append" in out
        assert "data: useViewTransition true" in out

    def test_patch_elements_accepts_html_object():
        class Node:
            def __html__(self):
                return "<b>hi</b>"

        assert "data: elements <b>hi</b>" in patch_elements(Node())

    def test_patch_signals_dict_is_json_encoded():
        out = patch_signals({"count": 5})
        assert out.startswith("event: datastar-patch-signals\n")
        assert 'data: signals {"count": 5}' in out

    def test_patch_signals_only_if_missing():
        assert "data: onlyIfMissing true" in patch_signals({"x": 1}, only_if_missing=True)

    def test_remove_signals_sets_nulls():
        out = remove_signals("a", "b")
        assert "event: datastar-patch-signals" in out
        assert '"a": null' in out and '"b": null' in out

    def test_execute_script_omits_autoremove_by_default():
        out = execute_script("console.log(1)")
        assert out.startswith("event: datastar-execute-script\n")
        assert "data: script console.log(1)" in out
        assert "autoRemove" not in out

    def test_execute_script_can_disable_autoremove():
        assert "data: autoRemove false" in execute_script("x()", auto_remove=False)

    def test_redirect_wraps_location_in_settimeout():
        out = redirect("/login")
        assert "event: datastar-execute-script" in out
        assert "window.location" in out and '"/login"' in out

    return


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## HMAC cookie signer + Set-Cookie serialization (a_app).
    > Note: _serialize_cookie is imported locally inside its tests — marimo treats
     leading-underscore names as cell-private, so they can't flow in as cell params.
    """)
    return


@app.cell
def _():
    def test_signer_roundtrip():
        s = create_signer("secret")
        assert s.unsign(s.sign("user42")) == "user42"

    def test_signer_rejects_tamper():
        s = create_signer("secret")
        tok = s.sign("user42")
        bad = tok[:-1] + ("0" if tok[-1] != "0" else "1")
        assert s.unsign(bad) is None

    def test_signer_rejects_wrong_secret():
        a, b = create_signer("aaa"), create_signer("bbb")
        assert b.unsign(a.sign("x")) is None

    def test_signer_honours_expiry():
        import time

        s = create_signer("secret")
        old = s.sign("x", ts=time.time() - 10_000)
        assert s.unsign(old, max_age=3600) is None
        assert s.unsign(old, max_age=None) == "x"

    def test_signer_malformed_returns_none():
        s = create_signer("secret")
        assert s.unsign("") is None
        assert s.unsign("only.two") is None

    def test_serialize_cookie_bool_flags():
        from a_app import internal_serialize_cookie

        assert (
            internal_serialize_cookie("s", "v", {"secure": True, "httponly": True})
            == "s=v; secure; httponly"
        )
        assert "secure" not in internal_serialize_cookie("s", "v", {"secure": False})

    def test_serialize_cookie_underscores_become_dashes():
        from a_app import internal_serialize_cookie

        assert "max-age=60" in internal_serialize_cookie("s", "v", {"max_age": 60})

    return


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## SQLite helpers (c_db).
    > tmp_path is a pytest built-in fixture, resolved per test.
    """)
    return


@app.cell
def _():
    def test_db_insert_select_roundtrip(tmp_path):
        db = create_db(str(tmp_path / "t.db"))
        migrate(db, "CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)")
        write(db, lambda c: c.execute("INSERT INTO t(v) VALUES(?)", ("hello",)))
        assert query(db, "SELECT v FROM t") == [("hello",)]

    def test_query_respects_limit(tmp_path):
        db = create_db(str(tmp_path / "t.db"))
        migrate(db, "CREATE TABLE n (i INTEGER)")
        write(db, lambda c: c.executemany("INSERT INTO n(i) VALUES(?)", [(i,) for i in range(10)]))
        assert len(query(db, "SELECT i FROM n", limit=3)) == 3

    def test_write_returns_fn_result(tmp_path):
        db = create_db(str(tmp_path / "t.db"))
        assert write(db, lambda c: 42) == 42

    def test_create_db_uses_wal(tmp_path):
        db = create_db(str(tmp_path / "t.db"))
        assert db.pragma("journal_mode").lower() == "wal"

    return


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## .env loader (e_ngrok).
    > monkeypatch keeps os.environ changes isolated per test.
    """)
    return


@app.cell
def _():
    def test_load_env_reads_keys(tmp_path, monkeypatch):
        import os

        monkeypatch.delenv("PYSSE_T1", raising=False)
        p = tmp_path / ".env"
        p.write_text("PYSSE_T1=hello\n# a comment\n\nPYSSE_T2 = spaced \n")
        load_env(str(p))
        assert os.environ["PYSSE_T1"] == "hello"
        assert os.environ["PYSSE_T2"] == "spaced"

    def test_load_env_missing_file_is_noop(tmp_path):
        load_env(str(tmp_path / "nope.env"))  # must not raise

    def test_load_env_does_not_override_existing(tmp_path, monkeypatch):
        import os

        monkeypatch.setenv("PYSSE_T3", "from-shell")
        (tmp_path / ".env").write_text("PYSSE_T3=from-file\n")
        load_env(str(tmp_path / ".env"))
        assert os.environ["PYSSE_T3"] == "from-shell"  # setdefault: shell wins

    return


if __name__ == "__main__":
    app.run()
