10,000 ft view
py-sse is the thinnest possible layer between raw ASGI and a Datastar-powered web app. No framework dependency — just Python, the ASGI protocol, and html-tags for HTML/SSE generation.

It gives you:

Routing — path → async handler
Request — parsed headers, cookies, query, body
Response — HTML, text, redirect, cookies, SSE stream
Relay — in-process pub/sub so commands can notify streams


```
py-sse/
  request.py   — parse scope/body into a Request object
  response.py  — build HTTP responses (html, text, redirect, cookie)
  stream.py    — SSE streaming + connection lifecycle
  relay.py     — pub/sub event bus
  router.py    — path→handler dispatch + the ASGI callable
```

The rule: no module imports from a peer. request.py doesn't know about response.py. relay.py doesn't know about stream.py. The router is the only module that wires them together.
