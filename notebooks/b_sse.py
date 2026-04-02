import marimo

__generated_with = "0.22.0"
app = marimo.App(app_title="")

with app.setup:

    import json
    from html_tags import to_html


@app.cell
def _():
    import marimo as mo

    return (mo,)


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    # Package: py-sse
    ## Module: .sse
    >Datastar SSE event formatting.

    - Pure functions — no I/O, no framework dependency.
    - Each returns a ready-to-send SSE event string.
    """)
    return


@app.function
def patch_elements(
    elements: str,  # essentially the Tag()s from html_tags
    *,
    selector: str | None = None,
    mode: str | None = None,
    namespace: str | None = None,
    use_view_transition: bool | None = None,
) -> str:
    """Format a datastar-patch-elements SSE event."""
    if hasattr(elements, '__html__'):
        elements = elements.__html__()
    lines = []
    if selector is not None:    lines.append(f"data: selector {selector}")
    if mode is not None:        lines.append(f"data: mode {mode}")
    if namespace is not None:   lines.append(f"data: namespace {namespace}")
    if use_view_transition is not None:
        lines.append(f"data: useViewTransition {str(use_view_transition).lower()}")
    for line in elements.split("\n"):
        lines.append(f"data: elements {line}")
    return "event: datastar-patch-elements\n" + "\n".join(lines) + "\n\n"


@app.function
def patch_signals(signals: dict | str, *, only_if_missing: bool | None = None) -> str:
    """Format a datastar-patch-signals SSE event."""
    if isinstance(signals, dict):
        signals = json.dumps(signals)
    lines = []
    if only_if_missing is not None:
        lines.append(f"data: onlyIfMissing {str(only_if_missing).lower()}")
    lines.append(f"data: signals {signals}")
    return "event: datastar-patch-signals\n" + "\n".join(lines) + "\n\n"


@app.function
def remove_signals(*names: str) -> str:
    """Remove signals by patching them to null."""
    return patch_signals({n: None for n in names})


@app.function
def execute_script(script: str, *, auto_remove: bool = True, attributes: dict | None = None) -> str:
    """Format a datastar-execute-script SSE event."""
    lines = []
    if not auto_remove:         lines.append("data: autoRemove false")
    if attributes is not None:  lines.append(f"data: attributes {json.dumps(attributes)}")
    for line in script.split("\n"):
        lines.append(f"data: script {line}")
    return "event: datastar-execute-script\n" + "\n".join(lines) + "\n\n"


if __name__ == "__main__":
    app.run()
