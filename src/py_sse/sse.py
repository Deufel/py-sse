import json

"""Datastar SSE event formatters.

    Each function returns a string in `text/event-stream` framing, ready for
    `transport.send_str(...)` or to yield from an `@app.stream` handler.
    """

def patch_elements(
    elements:str,              # HTML to patch in; an html_tags node (has __html__) also works
    *,
    selector:str|None=None,    # CSS target; defaults to matching by element id
    mode:str|None=None,        # morph mode, e.g. outer / inner / append
    namespace:str|None=None,   # optional namespace for the patch
    use_view_transition:bool|None=None, # wrap the patch in a View Transition
)->str:                        # framed datastar-patch-elements event
    "Format a datastar-patch-elements SSE event."
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

def patch_signals(
    signals:dict|str,            # signals to patch; a dict is JSON-encoded for you
    *,
    only_if_missing:bool|None=None, # only set signals not already present
)->str:                          # framed datastar-patch-signals event
    "Format a datastar-patch-signals SSE event."
    if isinstance(signals, dict):
        signals = json.dumps(signals)
    lines = []
    if only_if_missing is not None:
        lines.append(f"data: onlyIfMissing {str(only_if_missing).lower()}")
    lines.append(f"data: signals {signals}")
    return "event: datastar-patch-signals\n" + "\n".join(lines) + "\n\n"

def remove_signals(
    *names:str, # signal names to remove
)->str:         # framed datastar-patch-signals event setting each to null
    "Remove signals by patching them to null."
    return patch_signals({n: None for n in names})

def execute_script(
    script:str,                  # JS source to run in the browser
    *,
    auto_remove:bool=True,       # remove the injected <script> after it runs
    attributes:dict|None=None,   # extra attributes for the injected <script>
)->str:                          # framed datastar-execute-script event
    "Format a datastar-execute-script SSE event."
    lines = []
    if not auto_remove:         lines.append("data: autoRemove false")
    if attributes is not None:  lines.append(f"data: attributes {json.dumps(attributes)}")
    for line in script.split("\n"):
        lines.append(f"data: script {line}")
    return "event: datastar-execute-script\n" + "\n".join(lines) + "\n\n"

def redirect(
    url:str, # destination to send the browser to
)->str:      # framed datastar-execute-script event that navigates
    "Redirect the browser to `url` via a patched script (setTimeout wraps a Firefox history quirk)."
    return execute_script(f"setTimeout(() => window.location = {json.dumps(url)}, 0)")
