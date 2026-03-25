wip

`app.py`
```py
app = create_app()

@app.get("/")
async def index(req):
    return "<h1>Hello</h1>"

if __name__ == "__main__":
    serve(app)
```

```bash
python app.py
```