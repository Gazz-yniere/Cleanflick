import os
import sys

def check(s):
    brace_count = 0
    paren_count = 0
    in_string = False
    string_char = ''
    in_line_comment = False
    in_block_comment = False
    i = 0
    while i < len(s):
        ch = s[i]
        nxt = s[i+1] if i+1 < len(s) else ''

        if in_line_comment:
            if ch == '\n':
                in_line_comment = False
        elif in_block_comment:
            if ch == '*' and nxt == '/':
                in_block_comment = False
                i += 1
        elif in_string:
            if ch == string_char and s[i-1] != '\\':
                in_string = False
        else:
            if ch == '/' and nxt == '/':
                in_line_comment = True
                i += 1
            elif ch == '/' and nxt == '*':
                in_block_comment = True
                i += 1
            elif ch in '"\'`':
                in_string = True
                string_char = ch
            elif ch == '{':
                brace_count += 1
            elif ch == '}':
                brace_count -= 1
                if brace_count < 0:
                    return False, False, i
            elif ch == '(':
                paren_count += 1
            elif ch == ')':
                paren_count -= 1
                if paren_count < 0:
                    return False, False, i
        i += 1
    return brace_count == 0, paren_count == 0, (brace_count, paren_count)

base = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'static', 'js')
failed = False
for root, _dirs, files in os.walk(base):
    for name in sorted(files):
        if not name.endswith('.js'):
            continue
        path = os.path.join(root, name)
        with open(path, 'r', encoding='utf-8', errors='ignore') as f:
            content = f.read()
        braces_ok, parens_ok, info = check(content)
        rel = os.path.relpath(path, os.path.dirname(base))
        if braces_ok and parens_ok:
            print(f"{rel}: Braces OK, Parens OK")
        else:
            failed = True
            print(f"{rel}: Braces {'OK' if braces_ok else 'MISMATCH'}, Parens {'OK' if parens_ok else 'MISMATCH'} (info={info})")
sys.exit(1 if failed else 0)
