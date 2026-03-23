import json
from html_tags.core import Tag, to_html

def patch_elements(
    elements: Tag | str,  # HTML content - Tag objects or raw strings
    selector: str | None = None,  # CSS selector for target element
    mode: str | None = None,  # outer, inner, prepend, append, before, after, remove
    namespace: str | None = None,  # html, svg, mathml
    use_view_transition: bool | None = None  # wrap in View Transition API
) -> str:  # formatted SSE event string
    "Format a datastar-patch-elements SSE event"
    if isinstance(elements, Tag): elements = to_html(elements)
    lines = []
    if selector: lines.append(f'data: selector {selector}')
    if mode: lines.append(f'data: mode {mode}')
    if namespace: lines.append(f'data: namespace {namespace}')
    if use_view_transition is not None: lines.append(f'data: useViewTransition {str(use_view_transition).lower()}')
    for line in elements.split('\n'): lines.append(f'data: elements {line}')
    return 'event: datastar-patch-elements\n' + '\n'.join(lines) + '\n\n'

def patch_signals(
    signals: dict | str,  # signal data - dicts auto-serialized to JSON
    only_if_missing: bool | None = None  # only patch if signal doesn't exist
) -> str:  # formatted SSE event string
    "Format a datastar-patch-signals SSE event"
    if isinstance(signals, dict): signals = json.dumps(signals)
    lines = []
    if only_if_missing is not None: lines.append(f'data: onlyIfMissing {str(only_if_missing).lower()}')
    lines.append(f'data: signals {signals}')
    return 'event: datastar-patch-signals\n' + '\n'.join(lines) + '\n\n'

def execute_script(
    script: str,  # JavaScript to execute
    auto_remove: bool = True  # remove script element after execution
) -> str:  # formatted SSE event string
    "Format a datastar-execute-script SSE event"
    lines = []
    if not auto_remove: lines.append('data: autoRemove false')
    for line in script.split('\n'): lines.append(f'data: script {line}')
    return 'event: datastar-execute-script\n' + '\n'.join(lines) + '\n\n'
