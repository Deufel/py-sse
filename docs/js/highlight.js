const PY_TOKENS = [
  ['py-decorator', /@[\w.]+/g],
  ['py-defname', /(?<=def )\w+/g],
  ['py-classname', /(?<=class )\w+/g],
  ['py-keyword', /\b(?:def|class|return|if|elif|else|for|while|import|from|as|with|try|except|finally|raise|yield|async|await|pass|break|continue|in|is|not|and|or|lambda|None|True|False|self)\b/g],
  ['py-builtin', /\b(?:print|len|range|list|dict|set|tuple|int|str|float|bool|isinstance|hasattr|getattr|setattr|enumerate|zip|map|filter|any|all|sorted|reversed|super|type|open|next)\b/g],
  ['py-call', /\b\w+(?=\()/g],
  ['py-number', /\b\d[\d_]*(?:\.\d[\d_]*)?(?:e[+-]?\d+)?\b/g],
  ['py-operator', /->|==|!=|<=|>=|\*\*|\/\/|[+\-*/%=<>&|^~]/g],
  ['py-punctuation', /[{}\(\)\[\]:,;]/g],
  ['py-fstring', /f(?='|\")/g],
  ['py-type', /(?<=: )\w+/g],
  ['py-string', /"""[\s\S]*?"""|\'\'\'[\s\S]*?\'\'\'|"[^"\n]*"|\'[^\'\n]*\'/g],
  ['py-comment', /#.*/g]
];

function highlightAll(root = document) {
  if (!CSS?.highlights) return;
  for (const [name] of PY_TOKENS) CSS.highlights.delete(name);
  root.querySelectorAll('pre code').forEach(code => {
    const textNodes = [];
    code.querySelectorAll('.code-line').forEach(line => {
        if (line.firstChild?.nodeType === Node.TEXT_NODE) textNodes.push(line.firstChild);
    });
    if (!textNodes.length) return;

    for (const [name, pattern] of PY_TOKENS) {
      const highlight = CSS.highlights.get(name) ?? new Highlight();
      for (const node of textNodes) {
        const src = node.textContent;
        const re = new RegExp(pattern.source, pattern.flags);
        for (const match of src.matchAll(re)) {
          const range = new Range();
          range.setStart(node, match.index);
          range.setEnd(node, match.index + match[0].length);
          highlight.add(range);
        }
      }
      if (highlight.size > 0) CSS.highlights.set(name, highlight);
    }
  });
}

// Inject highlight styles
const style = document.createElement('style');
style.textContent = `::highlight(py-decorator) { color: #f78c6c; }
::highlight(py-defname) { color: #82aaff; }
::highlight(py-classname) { color: #ffcb6b; }
::highlight(py-keyword) { color: #c792ea; }
::highlight(py-builtin) { color: #82aaff; }
::highlight(py-call) { color: #82aaff; }
::highlight(py-number) { color: #f78c6c; }
::highlight(py-operator) { color: #89ddff; }
::highlight(py-punctuation) { color: #89ddff; }
::highlight(py-fstring) { color: #c3e88d; }
::highlight(py-type) { color: #ffcb6b; }
::highlight(py-string) { color: #c3e88d; }
::highlight(py-comment) { color: #546e7a; }`;
document.head.appendChild(style);

if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', () => highlightAll());
else highlightAll();
