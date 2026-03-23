import marimo

__generated_with = "0.21.1"
app = marimo.App()


@app.cell(hide_code=True)
def _():
    return


@app.class_definition
class Response:
    __slots__ = ('_send', '_headers')

    def __init__(self, send):
        self._send = send
        self._headers = []

    def cookie(self, name, value, **opts):
        parts = [f"{name}={value}"]
        for k, v in opts.items():
            k = k.replace("_", "-")
            if isinstance(v, bool):
                if v: parts.append(k)
            else: parts.append(f"{k}={v}")
        self._headers.append([b"set-cookie", "; ".join(parts).encode()])
        return self

    async def _send_response(self, status, content_type, body):
        headers = [[b"content-type", content_type]] + self._headers
        await self._send({"type": "http.response.start", "status": status, "headers": headers})
        await self._send({"type": "http.response.body", "body": body.encode() if isinstance(body, str) else body})

    async def html(self, body, status=200):
        await self._send_response(status, b"text/html; charset=utf-8", body)

    async def text(self, body, status=200):
        await self._send_response(status, b"text/plain; charset=utf-8", body)

    async def redirect(self, url, status=302):
        headers = [[b"location", url.encode()]] + self._headers
        await self._send({"type": "http.response.start", "status": status, "headers": headers})
        await self._send({"type": "http.response.body", "body": b""})


@app.cell
def _():
    import marimo as mo

    return


if __name__ == "__main__":
    app.run()
