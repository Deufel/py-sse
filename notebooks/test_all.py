import marimo

__generated_with = "0.21.1"
app = marimo.App()

with app.setup:
    from e_router import Router
    from b_response import Response
    from c_stream import Stream
    import httpx
    from f_sse  import patch_elements, patch_signals, execute_script

    from html_tags import setup_tags, Tag

    setup_tags()


@app.cell
def _():

    router = Router()

    @router.get("/")
    async def home(req, send):
        res = Response(send)
        await res.html("<h1>Hello from py-sse!</h1>")

    @router.get("/health")
    async def health(req, send):
        res = Response(send)
        await res.text("ok")


    return


@app.cell
def _(Div):


    class TestPatchElements:
        def test_string_input(self):
            result = patch_elements("<div>hello</div>")
            assert result.startswith("event: datastar-patch-elements\n")
            assert "data: elements <div>hello</div>" in result
            assert result.endswith("\n\n")

        def test_tag_input(self):
            result = patch_elements(Div({"id": "msg"}, "hello"))
            assert "data: elements <div id=\"msg\">hello</div>" in result

        def test_selector(self):
            result = patch_elements("<div>hi</div>", selector="#main")
            assert "data: selector #main" in result

        def test_mode(self):
            result = patch_elements("<div>hi</div>", mode="inner")
            assert "data: mode inner" in result

        def test_namespace(self):
            result = patch_elements("<svg></svg>", namespace="svg")
            assert "data: namespace svg" in result

        def test_view_transition(self):
            result = patch_elements("<div>hi</div>", use_view_transition=True)
            assert "data: useViewTransition true" in result
            result = patch_elements("<div>hi</div>", use_view_transition=False)
            assert "data: useViewTransition false" in result

        def test_multiline(self):
            result = patch_elements("<div>\n<span>hi</span>\n</div>")
            assert "data: elements <div>" in result
            assert "data: elements <span>hi</span>" in result
            assert "data: elements </div>" in result

        def test_all_options(self):
            result = patch_elements("<div>hi</div>", selector="#x", mode="append", namespace="html", use_view_transition=True)
            lines = result.split("\n")
            assert lines[0] == "event: datastar-patch-elements"
            assert "data: selector #x" in result
            assert "data: mode append" in result
            assert "data: namespace html" in result
            assert "data: useViewTransition true" in result


    class TestPatchSignals:
        def test_dict_input(self):
            result = patch_signals({"count": 1})
            assert "event: datastar-patch-signals\n" in result
            assert 'data: signals {"count": 1}' in result

        def test_string_input(self):
            result = patch_signals('{"count": 1}')
            assert 'data: signals {"count": 1}' in result

        def test_only_if_missing_true(self):
            result = patch_signals({"x": 1}, only_if_missing=True)
            assert "data: onlyIfMissing true" in result

        def test_only_if_missing_false(self):
            result = patch_signals({"x": 1}, only_if_missing=False)
            assert "data: onlyIfMissing false" in result

        def test_nested_dict(self):
            result = patch_signals({"user": {"name": "fox", "score": 42}})
            assert "data: signals " in result
            assert '"user"' in result
            assert '"name"' in result


    class TestExecuteScript:
        def test_basic(self):
            result = execute_script("alert('hi')")
            assert result.startswith("event: datastar-execute-script\n")
            assert "data: script alert('hi')" in result
            assert result.endswith("\n\n")

        def test_auto_remove_default(self):
            result = execute_script("alert('hi')")
            assert "autoRemove" not in result

        def test_auto_remove_false(self):
            result = execute_script("alert('hi')", auto_remove=False)
            assert "data: autoRemove false" in result

        def test_multiline_script(self):
            result = execute_script("let x = 1;\nalert(x);")
            assert "data: script let x = 1;" in result
            assert "data: script alert(x);" in result



    return


@app.cell
def _():
    return


if __name__ == "__main__":
    app.run()
