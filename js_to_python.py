#!/usr/bin/env python3
"""Convert JavaScript code blocks in course documents to Python.

Reads all course markdown files, extracts ```javascript blocks,
converts them to Python, and writes them back as ```python blocks.
"""

import os
import re
import sys
from pathlib import Path

VAULT = Path(os.path.expanduser("~/vaults/97-coding-leetcode"))


def clean_scraping_artifacts(js_code: str) -> str:
    """Remove FlowUS copy-paste artifacts: 'JavaScript', 'Copy', interleaved line numbers."""
    lines = js_code.split("\n")
    result = []
    i = 0
    # Skip leading "JavaScript" and "Copy" lines
    while i < len(lines) and lines[i].strip() in ("JavaScript", "Copy", ""):
        i += 1
    # Remove interleaved line numbers: a line that is ONLY a number,
    # followed by a line that looks like actual code
    while i < len(lines):
        stripped = lines[i].strip()
        if stripped.isdigit() and i + 1 < len(lines):
            # This is a line number artifact — skip it
            i += 1
            continue
        result.append(lines[i])
        i += 1
    return "\n".join(result)


def js_to_python(js_code: str) -> str:
    """Convert JavaScript algorithm code to Python."""
    # First clean scraping artifacts
    js_code = clean_scraping_artifacts(js_code)

    lines = js_code.split("\n")
    result = []

    i = 0
    while i < len(lines):
        line = lines[i]
        converted = convert_line(line, lines, i)
        if converted is not None:
            result.append(converted)
        i += 1

    output = "\n".join(result)
    output = post_process(output)
    return output


def get_indent(line: str) -> str:
    """Get leading whitespace."""
    return line[: len(line) - len(line.lstrip())]


def extract_inline_comment(s: str) -> tuple[str, str]:
    """Split a line into (code, comment) at the first // outside a string.

    Returns (code_part, comment_part) where comment_part includes the '  # '.
    """
    in_str = None
    for ci, ch in enumerate(s):
        if ch in ('"', "'", "`") and (ci == 0 or s[ci - 1] != "\\"):
            if in_str == ch:
                in_str = None
            elif in_str is None:
                in_str = ch
        elif ch == "/" and ci + 1 < len(s) and s[ci + 1] == "/" and in_str is None:
            code = s[:ci].rstrip()
            comment = "  #" + s[ci + 2 :]
            return code, comment
    return s, ""


def _make_range(var: str, start: str, end: str, op: str, step: str) -> str:
    """Build a range() expression from for-loop parts."""
    if step == "++" and op == "<":
        return f"range({start}, {end})"
    elif step == "++" and op == "<=":
        return f"range({start}, {end} + 1)"
    elif step == "--" and op == ">":
        return f"range({start}, {end}, -1)"
    elif step == "--" and op == ">=":
        return f"range({start}, {end} - 1, -1)"
    elif step.startswith("+="):
        step_val = step[2:].strip()
        if op == "<":
            return f"range({start}, {end}, {step_val})"
        elif op == "<=":
            return f"range({start}, {end} + 1, {step_val})"
    return f"range({start}, {end})  # {op}, {step}"


def convert_line(line: str, all_lines: list[str], idx: int) -> str | None:
    """Convert a single JS line to Python."""
    indent = get_indent(line)
    stripped = line.strip()

    # Skip empty lines
    if not stripped:
        return ""

    # === Standalone comments ===
    if stripped.startswith("//"):
        return indent + "#" + stripped[2:]
    if stripped.startswith("/*"):
        return indent + "# " + stripped[2:].strip()
    if stripped.startswith("*/"):
        return None
    if stripped.startswith("* "):
        return indent + "# " + stripped[2:]
    if stripped.startswith("*"):
        return indent + "# " + stripped[1:].strip()

    # Skip standalone braces
    if stripped in ("{", "}", "};", ");"):
        return None

    # === Extract inline comment BEFORE pattern matching ===
    s, inline_comment = extract_inline_comment(stripped)

    # Variable declarations — strip var/const/let
    had_decl = bool(re.match(r"^(var|const|let)\s+", s))
    s = re.sub(r"^(var|const|let)\s+", "", s)

    # Remove trailing semicolons
    if s.endswith(";"):
        s = s[:-1]

    # Bare declaration: let x → x = None
    if had_decl and re.match(r"^\w+$", s) and "=" not in s:
        return indent + f"{s} = None" + inline_comment

    # Remove trailing brace that opens a block
    opens_block = s.endswith("{")
    if opens_block:
        s = s[:-1].rstrip()

    # === Class definitions ===

    # class Name { → class Name:
    m = re.match(r"^class\s+(\w+)(?:\s+extends\s+(\w+))?\s*$", s)
    if m:
        name = m.group(1)
        parent = m.group(2)
        if parent:
            return indent + f"class {name}({parent}):" + inline_comment
        return indent + f"class {name}:" + inline_comment

    # constructor(params) { → def __init__(self, params):
    m = re.match(r"^constructor\s*\(([^)]*)\)\s*$", s)
    if m:
        params = convert_params(m.group(1))
        if params:
            return indent + f"def __init__(self, {params}):" + inline_comment
        return indent + "def __init__(self):" + inline_comment

    # Class method: methodName(params) { (standalone, not a call — must open a block)
    # Exclude JS keywords that take parens (if, while, for, switch, etc.)
    _JS_KEYWORDS = {"if", "else", "while", "for", "switch", "catch", "return", "throw", "new", "delete", "typeof"}
    if opens_block:
        m = re.match(r"^(\w+)\s*\(([^)]*)\)\s*$", s)
        if m and m.group(1) not in _JS_KEYWORDS:
            fname, params = m.groups()
            params = convert_params(params)
            if params:
                return indent + f"def {fname}(self, {params}):" + inline_comment
            return indent + f"def {fname}(self):" + inline_comment

    # === Empty function body: fn = (x) => {} ===
    m = re.match(r"^(\w+)\s*=\s*(?:function\s*)?\(([^)]*)\)\s*(?:=>)?\s*\{\}\s*$", s)
    if m:
        fname, params = m.groups()
        params = convert_params(params)
        return indent + f"def {fname}({params}):" + inline_comment + "\n" + indent + "    pass"

    # === Function declarations ===

    # var fn = function(x) { or fn = (x) => {
    m = re.match(r"^(\w+)\s*=\s*(?:function\s*)?\(([^)]*)\)\s*(?:=>)?\s*$", s)
    if m:
        fname, params = m.groups()
        params = convert_params(params)
        return indent + f"def {fname}({params}):" + inline_comment

    # const fn = (x) => expr  (arrow with inline body)
    m = re.match(r"^(\w+)\s*=\s*\(([^)]*)\)\s*=>\s*(.+)$", s)
    if m:
        fname, params, body = m.groups()
        params = convert_params(params)
        body = convert_expr(body.strip())
        if body:
            return indent + f"def {fname}({params}):" + inline_comment + "\n" + indent + f"    return {body}"
        return indent + f"def {fname}({params}):" + inline_comment

    # function name(params) {
    m = re.match(r"^function\s+(\w+)\s*\(([^)]*)\)\s*$", s)
    if m:
        fname, params = m.groups()
        params = convert_params(params)
        return indent + f"def {fname}({params}):" + inline_comment

    # Arrow: const fn = (params) =>
    m = re.match(r"^(\w+)\s*=\s*\(([^)]*)\)\s*=>\s*$", s)
    if m:
        fname, params = m.groups()
        params = convert_params(params)
        return indent + f"def {fname}({params}):" + inline_comment

    # === if / elif / else ===

    # Prefix decrement/increment in if condition:
    # if(--expr === 0) → expr -= 1\nif expr == 0:
    m = re.match(r"^if\s*\(--(.+?)\s*(===?|!==?|>=?|<=?)\s*(.+?)\)\s*(.*)", s)
    if m:
        var, op, val, rest = m.groups()
        var_c = convert_expr(var)
        op_c = op.replace("===", "==").replace("!==", "!=")
        val_c = convert_expr(val)
        rest = rest.strip()
        prefix = indent + f"{var_c} -= 1\n"
        if rest:
            rest_c = convert_expr(rest)
            return prefix + indent + f"if {var_c} {op_c} {val_c}:" + inline_comment + "\n" + indent + f"    {rest_c}"
        return prefix + indent + f"if {var_c} {op_c} {val_c}:" + inline_comment

    m = re.match(r"^if\s*\(\+\+(.+?)\s*(===?|!==?|>=?|<=?)\s*(.+?)\)\s*(.*)", s)
    if m:
        var, op, val, rest = m.groups()
        var_c = convert_expr(var)
        op_c = op.replace("===", "==").replace("!==", "!=")
        val_c = convert_expr(val)
        rest = rest.strip()
        prefix = indent + f"{var_c} += 1\n"
        if rest:
            rest_c = convert_expr(rest)
            return prefix + indent + f"if {var_c} {op_c} {val_c}:" + inline_comment + "\n" + indent + f"    {rest_c}"
        return prefix + indent + f"if {var_c} {op_c} {val_c}:" + inline_comment

    # if(cond) return expr
    m = re.match(r"^if\s*\((.+?)\)\s+return\s+(.+)$", s)
    if m:
        cond = convert_expr(m.group(1))
        ret = convert_expr(m.group(2))
        return indent + f"if {cond}:" + inline_comment + "\n" + indent + f"    return {ret}"

    # if(cond) return
    m = re.match(r"^if\s*\((.+?)\)\s+return$", s)
    if m:
        cond = convert_expr(m.group(1))
        return indent + f"if {cond}:" + inline_comment + "\n" + indent + "    return"

    # if(cond) continue
    m = re.match(r"^if\s*\((.+?)\)\s+continue$", s)
    if m:
        cond = convert_expr(m.group(1))
        return indent + f"if {cond}:" + inline_comment + "\n" + indent + "    continue"

    # if(cond) break
    m = re.match(r"^if\s*\((.+?)\)\s+break$", s)
    if m:
        cond = convert_expr(m.group(1))
        return indent + f"if {cond}:" + inline_comment + "\n" + indent + "    break"

    # Generic inline if: if(cond) statement
    m = re.match(r"^if\s*\((.+?)\)\s+(?!{)(\S.+)$", s)
    if m:
        first_word = m.group(2).split()[0]
        if first_word not in ("return", "continue", "break"):
            cond = convert_expr(m.group(1))
            body = convert_expr(m.group(2))
            return indent + f"if {cond}:" + inline_comment + "\n" + indent + f"    {body}"

    # if(cond) {  (block form)
    m = re.match(r"^if\s*\((.+)\)\s*$", s)
    if m:
        cond = convert_expr(m.group(1))
        return indent + f"if {cond}:" + inline_comment

    # Inline else if(cond) statement (e.g. else if(x) return y)
    m = re.match(r"^(?:}\s*)?else\s+if\s*\((.+?)\)\s+(?!\{)(\S.+)$", s)
    if m:
        cond = convert_expr(m.group(1))
        body = convert_expr(m.group(2))
        # Handle return specially
        if body.startswith("return "):
            return indent + f"elif {cond}:" + inline_comment + "\n" + indent + f"    {body}"
        return indent + f"elif {cond}:" + inline_comment + "\n" + indent + f"    {body}"

    # else if(cond) { — block form, with optional leading }
    m = re.match(r"^(?:}\s*)?else\s+if\s*\((.+)\)\s*\{?\s*$", s)
    if m:
        cond = convert_expr(m.group(1))
        return indent + f"elif {cond}:" + inline_comment

    # else statement (inline)
    m = re.match(r"^(?:}\s*)?else\s+(?!if)(?!\{)(\S.+)$", s)
    if m:
        body = convert_expr(m.group(1))
        return indent + f"else:" + inline_comment + "\n" + indent + f"    {body}"

    # else { or else
    if s in ("else", "} else", "} else {"):
        return indent + "else:" + inline_comment

    # === Inline while: while(cond) expr ===
    m = re.match(r"^while\s*\((.+?)\)\s+(\w.+)$", s)
    if m and not m.group(2).strip().startswith("{"):
        cond = convert_expr(m.group(1))
        body = convert_expr(m.group(2))
        return indent + f"while {cond}:" + inline_comment + "\n" + indent + f"    {body}"

    # === for loops ===

    # Inline for(let i = 0; i < n; i++) statement
    m = re.match(
        r"^for\s*\(\s*(?:let|var|const)?\s*(\w+)\s*=\s*(\S+)\s*;\s*\1\s*([<>=!]+)\s*(\S+)\s*;\s*\1(\+\+|--|\+=\s*\d+|-=\s*\d+)\s*\)\s+(?!\{)(\S.+)$",
        s,
    )
    if m:
        var, start, op, end, step, body = m.groups()
        start_c = convert_expr(start)
        end_c = convert_expr(end)
        body_c = convert_expr(body)
        range_expr = _make_range(var, start_c, end_c, op, step)
        return indent + f"for {var} in {range_expr}:" + inline_comment + "\n" + indent + f"    {body_c}"

    # Inline for(let x of arr) statement
    m = re.match(r"^for\s*\(\s*(?:let|var|const)\s+(\w+)\s+of\s+(.+?)\)\s+(?!\{)(\S.+)$", s)
    if m:
        var, iterable, body = m.groups()
        iterable = convert_expr(iterable)
        body = convert_expr(body)
        return indent + f"for {var} in {iterable}:" + inline_comment + "\n" + indent + f"    {body}"

    # Inline for(let [a,b] of arr) statement
    m = re.match(r"^for\s*\(\s*(?:let|var|const)\s+\[([^\]]+)\]\s+of\s+(.+?)\)\s+(?!\{)(\S.+)$", s)
    if m:
        vars_str, iterable, body = m.groups()
        iterable = convert_expr(iterable)
        body = convert_expr(body)
        return indent + f"for {vars_str} in {iterable}:" + inline_comment + "\n" + indent + f"    {body}"

    # Block for(let i = 0; i < n; i++) {
    m = re.match(
        r"^for\s*\(\s*(?:let|var|const)?\s*(\w+)\s*=\s*(\S+)\s*;\s*\1\s*([<>=!]+)\s*(\S+)\s*;\s*\1(\+\+|--|\+=\s*\d+|-=\s*\d+)\s*\)\s*$",
        s,
    )
    if m:
        var, start, op, end, step = m.groups()
        start = convert_expr(start)
        end = convert_expr(end)
        range_expr = _make_range(var, start, end, op, step)
        return indent + f"for {var} in {range_expr}:" + inline_comment

    # for(let x of arr)
    m = re.match(r"^for\s*\(\s*(?:let|var|const)\s+(\w+)\s+of\s+(.+)\)\s*$", s)
    if m:
        var, iterable = m.groups()
        iterable = convert_expr(iterable)
        return indent + f"for {var} in {iterable}:" + inline_comment

    # for(let [a,b] of arr)
    m = re.match(r"^for\s*\(\s*(?:let|var|const)\s+\[([^\]]+)\]\s+of\s+(.+)\)\s*$", s)
    if m:
        vars_str, iterable = m.groups()
        iterable = convert_expr(iterable)
        return indent + f"for {vars_str} in {iterable}:" + inline_comment

    # while(cond) {
    m = re.match(r"^while\s*\((.+)\)\s*$", s)
    if m:
        cond = convert_expr(m.group(1))
        return indent + f"while {cond}:" + inline_comment

    # === return ===
    m = re.match(r"^return\s+(.+)$", s)
    if m:
        return indent + f"return {convert_expr(m.group(1))}" + inline_comment
    if s == "return":
        return indent + "return" + inline_comment

    # === console.log ===
    m = re.match(r"^console\.log\((.+)\)$", s)
    if m:
        return indent + f"print({convert_expr(m.group(1))})" + inline_comment

    # === Fallback: general expression conversion ===
    s = convert_expr(s)
    return indent + s + inline_comment


def convert_params(params: str) -> str:
    """Convert JS function parameters to Python."""
    params = re.sub(r":\s*\w+", "", params)
    return params.strip()


def convert_expr(expr: str) -> str:
    """Convert a JavaScript expression to Python."""
    if not expr:
        return expr

    e = expr.strip()

    # Remove trailing semicolons
    if e.endswith(";"):
        e = e[:-1]

    # Boolean values
    e = re.sub(r"\btrue\b", "True", e)
    e = re.sub(r"\bfalse\b", "False", e)
    e = re.sub(r"\bnull\b", "None", e)
    e = re.sub(r"\bundefined\b", "None", e)

    # Logical operators
    e = re.sub(r"\s&&\s", " and ", e)
    e = re.sub(r"\s\|\|\s", " or ", e)
    e = e.replace("&&", " and ")
    e = e.replace("||", " or ")

    # Strict equality (do this BEFORE ! operator)
    e = e.replace("===", "==")
    e = e.replace("!==", "!=")

    # this. -> self.
    e = re.sub(r"\bthis\.", "self.", e)

    # Not operator: !x -> not x, !(x) -> not (x)
    # But NOT !==  (already handled above)
    e = re.sub(r"!(\w)", r"not \1", e)
    e = re.sub(r"!\(", r"not (", e)

    # new Array(n).fill(0).map(()=>new Array()) -> [[] for _ in range(n)]
    e = re.sub(r"new Array\(([^)]+)\)\.fill\([^)]*\)\.map\(\s*\(\)\s*=>\s*new Array\(\)\s*\)", r"[[] for _ in range(\1)]", e)
    # new Array(n).fill(x) -> [x] * (n)
    e = re.sub(r"new Array\(([^)]+)\)\.fill\(([^)]+)\)", lambda m: f"[{m.group(2)}] * ({m.group(1)})" if not m.group(1).isalnum() else f"[{m.group(2)}] * {m.group(1)}", e)
    # new Array(n) -> [0] * n
    e = re.sub(r"new Array\(([^)]+)\)", lambda m: f"[0] * ({m.group(1)})" if not m.group(1).isalnum() else f"[0] * {m.group(1)}", e)

    # .length -> len()
    e = re.sub(r"(\w+(?:\.\w+)*(?:\[[^\]]*\])*)\.length", r"len(\1)", e)

    # Array methods
    e = re.sub(r"\.push\(", ".append(", e)
    e = re.sub(r"\.shift\(\)", ".pop(0)", e)
    e = re.sub(r"\.unshift\((.+?)\)", r".insert(0, \1)", e)
    e = re.sub(r"\.slice\(\)", "[:]", e)  # .slice() no args = copy
    e = re.sub(r"\.slice\(([^,]+),\s*([^)]+)\)", r"[\1:\2]", e)
    e = re.sub(r"\.slice\(([^)]+)\)", r"[\1:]", e)
    e = re.sub(r"\.concat\((.+)\)", r" + \1", e)

    # Math functions
    e = e.replace("Math.max", "max")
    e = e.replace("Math.min", "min")
    e = e.replace("Math.floor", "int")
    e = e.replace("Math.ceil", "math.ceil")
    e = e.replace("Math.abs", "abs")
    e = e.replace("Math.random()", "random.random()")
    e = e.replace("Math.PI", "math.pi")
    e = e.replace("Math.log2", "math.log2")
    e = e.replace("Math.pow", "pow")
    e = e.replace("Infinity", "float('inf')")
    e = e.replace("-float('inf')", "float('-inf')")

    # parseInt -> int
    e = re.sub(r"parseInt\((.+?)\)", r"int(\1)", e)

    # Ternary: handle assignment context first: x = cond ? a : b
    # Use (?<!=)=(?!=) to match single = (assignment) not == or !=
    m = re.match(r"^(.+?)(?<!=)=(?!=)\s*(.+?)\s*\?\s*(.+?)\s*:\s*(.+)$", e)
    if m:
        lhs, cond, true_val, false_val = m.groups()
        if "?" not in true_val and "?" not in false_val:
            cond = convert_expr(cond.strip())
            true_val = convert_expr(true_val)
            false_val = convert_expr(false_val)
            e = f"{lhs.rstrip()} = {true_val} if {cond} else {false_val}"

    # Ternary standalone: a ? b : c -> b if a else c
    # Check there's no assignment (single =) in the condition part
    if "?" in e and not re.search(r"(?<![=!<>])=(?!=)", e.split("?")[0]):
        m = re.match(r"^(.+?)\s*\?\s*(.+?)\s*:\s*(.+)$", e)
        if m:
            cond, true_val, false_val = m.groups()
            if "?" not in true_val and "?" not in false_val:
                cond = convert_expr(cond)
                true_val = convert_expr(true_val)
                false_val = convert_expr(false_val)
                e = f"{true_val} if {cond} else {false_val}"

    # Destructuring: [a, b] = [c, d] -> a, b = c, d
    m = re.match(r"^\[(.+?)\]\s*=\s*\[(.+)\]$", e)
    if m:
        lhs, rhs = m.groups()
        e = f"{lhs} = {rhs}"

    # Destructuring: [a, b] = expr -> a, b = expr
    m = re.match(r"^\[([^\]]+)\]\s*=\s*(.+)$", e)
    if m:
        vars_str, val = m.groups()
        e = f"{vars_str} = {val}"

    # Template literals
    e = re.sub(r"`([^`]*)`", lambda m: convert_template_literal(m.group(1)), e)

    # ++ / -- operators (handle arr[i]++ and simple var++)
    e = re.sub(r"(\w+(?:\[[^\]]+\])*)\+\+", r"\1 += 1", e)
    e = re.sub(r"(\w+(?:\[[^\]]+\])*)--", r"\1 -= 1", e)
    e = re.sub(r"\+\+(\w+(?:\[[^\]]+\])*)", r"\1 += 1", e)
    e = re.sub(r"--(\w+(?:\[[^\]]+\])*)", r"\1 -= 1", e)

    return e


def convert_template_literal(s: str) -> str:
    """Convert JS template literal content to Python f-string."""
    s = re.sub(r"\$\{(.+?)\}", r"{\1}", s)
    return f'f"{s}"'


def post_process(code: str) -> str:
    """Clean up the converted Python code."""
    lines = code.split("\n")
    result = []
    for line in lines:
        if line is None:
            continue
        # Collapse multiple consecutive empty lines
        if not line.strip() and result and not result[-1].strip():
            continue
        result.append(line)

    # Remove trailing empty lines
    while result and not result[-1].strip():
        result.pop()

    return "\n".join(result)


def process_file(filepath: Path, dry_run: bool = False) -> int:
    """Process a single markdown file, converting JS blocks to Python."""
    content = filepath.read_text(encoding="utf-8")

    def replace_block(m):
        js_code = m.group(1)
        py_code = js_to_python(js_code)
        return f"```python\n{py_code}\n```"

    new_content = re.sub(
        r"```javascript\n(.*?)\n```",
        replace_block,
        content,
        flags=re.DOTALL,
    )

    if new_content != content:
        changes = len(re.findall(r"```javascript", content))
        if not dry_run:
            filepath.write_text(new_content, encoding="utf-8")
        return changes
    return 0


def test_conversion():
    """Extract all JS blocks and show conversions for review."""
    total = 0
    for cdir in [VAULT / f"C{i}" for i in range(5)]:
        if not cdir.exists():
            continue
        for md in sorted(cdir.rglob("*.md")):
            content = md.read_text(encoding="utf-8")
            blocks = re.findall(r"```javascript\n(.*?)\n```", content, re.DOTALL)
            for i, block in enumerate(blocks):
                total += 1
                rel = md.relative_to(VAULT)
                print(f"\n{'='*60}")
                print(f"[{total}] {rel} block #{i+1}")
                print(f"{'='*60}")
                print("--- JS ---")
                print(block)
                print("--- Python ---")
                print(js_to_python(block))


def main():
    if "--test" in sys.argv:
        test_conversion()
        return

    dry_run = "--dry-run" in sys.argv

    total_blocks = 0
    files_changed = 0

    for cdir in [VAULT / f"C{i}" for i in range(5)]:
        if not cdir.exists():
            continue
        for md in sorted(cdir.rglob("*.md")):
            changes = process_file(md, dry_run=dry_run)
            if changes:
                files_changed += 1
                total_blocks += changes
                rel = md.relative_to(VAULT)
                prefix = "[DRY RUN] " if dry_run else ""
                print(f"{prefix}{rel}: {changes} blocks converted")

    print(f"\n{'[DRY RUN] ' if dry_run else ''}Total: {total_blocks} blocks in {files_changed} files")


if __name__ == "__main__":
    main()
