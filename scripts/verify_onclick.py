import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
html = open(os.path.join(ROOT, 'templates', 'index.html'), encoding='utf-8').read()

called = set(re.findall(r'onclick="([a-zA-Z_]\w*)\(', html))
called |= set(re.findall(r"onkeydown=\"if\(event\.key==='Enter'\) ([a-zA-Z_]\w*)\(", html))

defs = set()
for dirpath, _d, files in os.walk(os.path.join(ROOT, 'static', 'js')):
    for n in files:
        if n.endswith('.js'):
            src = open(os.path.join(dirpath, n), encoding='utf-8').read()
            defs |= set(re.findall(r'function\s+([a-zA-Z_]\w*)', src))

missing = [c for c in sorted(called) if c not in defs]
print('Fonctions appelées dans le HTML :', len(called))
print('Non définies dans static/js :', missing if missing else 'AUCUNE')
