import marimo

__generated_with = "0.21.1"
app = marimo.App(width="full")

with app.setup:
    from __future__ import annotations
 
    import json
    from html_tags import to_html, Tag
 
 
    # Datastar spec: valid values for the `mode` data line
    MODES = frozenset({
        "outer",    # morph outer HTML (default)
        "inner",    # morph inner HTML
        "replace",  # replace outer HTML (no morph)
        "prepend",  # prepend to target's children
        "append",   # append to target's children
        "before",   # insert before target as sibling
        "after",    # insert after target as sibling
        "remove",   # remove target from DOM
    })
 
    # Datastar spec: valid values for the `namespace` data line
    NAMESPACES = frozenset({"html", "svg", "mathml"})
 


@app.function
def patch_elements(
    elements: Tag | str,
    *,
    selector: str | None = None,
    mode: str | None = None,
    namespace: str | None = None,
    use_view_transition: bool | None = None,
) -> str:
    """Format a datastar-patch-elements SSE event.
 
    Patches one or more elements in the DOM. By default, Datastar morphs
    elements by matching top-level elements based on their ID.
 
    Args:
        elements: HTML content — Tag objects are rendered via to_html().
        selector: CSS selector for the target element.
        mode: One of MODES. Defaults to "outer" on the client.
        namespace: One of NAMESPACES. Required for SVG/MathML content.
        use_view_transition: Wrap the patch in the View Transition API.
    """
    if mode is not None and mode not in MODES:
        raise ValueError(f"Invalid mode {mode!r}. Must be one of {sorted(MODES)}")
    if namespace is not None and namespace not in NAMESPACES:
        raise ValueError(f"Invalid namespace {namespace!r}. Must be one of {sorted(NAMESPACES)}")
    if isinstance(elements, Tag):
        elements = to_html(elements)
 
    lines = []
    if selector is not None:
        lines.append(f"data: selector {selector}")
    if mode is not None:
        lines.append(f"data: mode {mode}")
    if namespace is not None:
        lines.append(f"data: namespace {namespace}")
    if use_view_transition is not None:
        lines.append(f"data: useViewTransition {str(use_view_transition).lower()}")
    for line in elements.split("\n"):
        lines.append(f"data: elements {line}")
    return "event: datastar-patch-elements\n" + "\n".join(lines) + "\n\n"


@app.function
def patch_signals(
    signals: dict | str,
    *,
    only_if_missing: bool | None = None,
) -> str:
    """Format a datastar-patch-signals SSE event.
 
    Patches signals into the existing signals on the page.
 
    Args:
        signals: Signal data — dicts are auto-serialized to JSON.
            Set values to None/null to remove signals.
        only_if_missing: Only patch signals that don't already exist.
    """
    if isinstance(signals, dict):
        signals = json.dumps(signals)
    lines = []
    if only_if_missing is not None:
        lines.append(f"data: onlyIfMissing {str(only_if_missing).lower()}")
    lines.append(f"data: signals {signals}")
    return "event: datastar-patch-signals\n" + "\n".join(lines) + "\n\n"


@app.function
def remove_signals(*names: str) -> str:
    """Remove signals by patching them to null.
 
    Convenience wrapper — Datastar removes signals when their value is null.
    """
    return patch_signals({name: None for name in names})


@app.function
def execute_script(
    script: str,
    *,
    auto_remove: bool = True,
    attributes: dict | None = None,
) -> str:
    """Format a datastar-execute-script SSE event.
 
    Executes JavaScript in the browser.
 
    Args:
        script: JavaScript code to execute.
        auto_remove: Remove the script element after execution (default True).
        attributes: Optional dict of attributes for the script element.
    """
    lines = []
    if not auto_remove:
        lines.append("data: autoRemove false")
    if attributes is not None:
        lines.append(f"data: attributes {json.dumps(attributes)}")
    for line in script.split("\n"):
        lines.append(f"data: script {line}")
    return "event: datastar-execute-script\n" + "\n".join(lines) + "\n\n"


@app.cell
def _():
    return


if __name__ == "__main__":
    app.run()
