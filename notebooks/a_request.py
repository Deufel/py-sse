import marimo

__generated_with = "0.21.1"
app = marimo.App()

with app.setup:
    from urllib.parse import parse_qs


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    response.py needs to do four things via ASGI send:

    1. Send HTML — status 200, text/html content type
    2. Send text — status 200, text/plain content type
    3. Redirect — status 302, Location header
    4. Set cookies — set-cookie headers attached to any response
    """)
    return


@app.class_definition
class Request:
    __slots__ = ('path', 'method', 'headers', 'query', 'cookies', '_receive')

    def __init__(self, scope, receive):
        self.path = scope["path"]
        self.method = scope.get("method", "GET")
        self._receive = receive
        # Parse headers
        self.headers = {k.decode(): v.decode() for k, v in scope.get("headers", [])}
        # Parse query params
        qs = scope.get("query_string", b"").decode()
        self.query = {k: v[0] if len(v) == 1 else v for k, v in parse_qs(qs).items()}
        # Parse cookies
        raw = self.headers.get("cookie", "")
        self.cookies = dict(pair.strip().split("=", 1) for pair in raw.split(";") if "=" in pair)

    async def body(self):
        chunks = []
        while True:
            msg = await self._receive()
            chunks.append(msg.get("body", b""))
            if not msg.get("more_body"): break
        return b"".join(chunks)

    async def json(self):
        import json
        return json.loads(await self.body())


@app.cell
def _():
    return


@app.cell
def _():
    import marimo as mo

    return (mo,)


if __name__ == "__main__":
    app.run()
