with open('static/app.js', 'r', encoding='utf-8', errors='ignore') as f:
    content = f.read()

def check_braces(s):
    brace_count = 0
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
                    return False, i
        i += 1
    return brace_count == 0, brace_count

ok, count = check_braces(content)
print(f"Braces: {'OK' if ok else 'MISMATCH'} (count={count})")

# Check parens
paren_count = 0
in_string = False
string_char = ''
in_line_comment = False
in_block_comment = False
i = 0
paren_ok = True
while i < len(content):
    ch = content[i]
    nxt = content[i+1] if i+1 < len(content) else ''
    
    if in_line_comment:
        if ch == '\n':
            in_line_comment = False
    elif in_block_comment:
        if ch == '*' and nxt == '/':
            in_block_comment = False
            i += 1
    elif in_string:
        if ch == string_char and content[i-1] != '\\':
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
        elif ch == '(':
            paren_count += 1
        elif ch == ')':
            paren_count -= 1
            if paren_count < 0:
                print(f'Negative paren at {i}: ...{content[max(0,i-50):i+50]}...')
                paren_ok = False
                break
    i += 1
print(f"Parens: {'OK' if paren_ok else 'MISMATCH'} (count={paren_count})")