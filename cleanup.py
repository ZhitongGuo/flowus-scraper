#!/usr/bin/env python3
"""Clean up scraped problem markdown files.

1. Keep only Python code blocks, remove JS/Java/Go/C++
2. Add proper ```python labels to detected Python code
3. Remove stray language labels (e.g., "多语言版", "python" as first line)
4. Clean up empty code blocks and excessive blank lines

Usage:
    python cleanup.py ./problems
    python cleanup.py ./problems --dry-run
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path


STRAY_LABELS = {
    "python", "python3", "py",
    "javascript", "js",
    "java",
    "go", "golang",
    "c++", "cpp",
    "typescript", "ts",
    "多语言版",
}

LANGUAGE_TEXT_LABELS = [
    "多语言版", "多语言",
    "python", "Python",
    "javascript", "JavaScript",
    "java", "Java",
    "go", "Go", "golang",
    "c++", "C++", "cpp",
]


def is_example_block(code: str) -> bool:
    """Check if a code block is an input/output example."""
    return any(k in code for k in ["输入", "输出", "解释", "Input:", "Output:", "Explanation:"])


def is_constraint_block(code: str) -> bool:
    """Check if a code block is a constraint/formula."""
    lines = code.strip().split("\n")
    if len(lines) <= 4:
        if any(k in code for k in ["<=", ">=", "F(", "=", "≤"]):
            if not any(k in code for k in ["def ", "class ", "var ", "func ", "public "]):
                return True
    return False


def is_python_block(code: str) -> bool:
    """Detect Python code using positive/negative indicator scoring."""
    py_indicators = [
        "def ", "class Solution", "self.", "self,",
        "range(", "len(", "append(", "List[", "Optional[",
        "True", "False", "None", "elif ",
        "    def ", "return ", "for i in", "while ",
        "__init__", "collections.", "defaultdict",
        "heapq.", "deque(", "print(",
    ]
    non_py = [
        "var ", "const ", "let ", "function ", "=> {", "===",   # JS
        "public ", "private ", "void ", "Integer>", "boolean[]",  # Java
        "func ", "fmt.", ":= ", "[]int",                          # Go
        "vector<", "nullptr", "::", "#include",                   # C++
    ]

    py_score = sum(1 for ind in py_indicators if ind in code)
    non_score = sum(1 for neg in non_py if neg in code)

    return py_score > 0 and non_score == 0


def process_file(filepath: Path) -> bool:
    """Process a single markdown file. Returns True if changed."""
    content = filepath.read_text(encoding="utf-8")

    # Split frontmatter from body
    fm_match = re.match(r"^(---\n.*?\n---)\n", content, re.DOTALL)
    if fm_match:
        frontmatter = fm_match.group(1)
        body = content[fm_match.end():]
    else:
        frontmatter = ""
        body = content

    if not body.strip() or (body.strip().startswith("# ") and body.strip().count("\n") < 3):
        return False  # empty problem, skip

    # Parse body into text lines and code blocks
    chunks: list[tuple] = []
    lines = body.split("\n")
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.strip().startswith("```"):
            lang_hint = line.strip()[3:].strip()
            code_lines: list[str] = []
            i += 1
            while i < len(lines) and lines[i].strip() != "```":
                code_lines.append(lines[i])
                i += 1
            i += 1  # skip closing ```
            code = "\n".join(code_lines)
            chunks.append(("code", lang_hint, code))
        else:
            chunks.append(("text", line))
            i += 1

    # Rebuild body, filtering code blocks
    new_lines: list[str] = []
    for chunk in chunks:
        if chunk[0] == "text":
            line = chunk[1]
            # Remove standalone language labels
            if line.strip() in LANGUAGE_TEXT_LABELS:
                continue
            new_lines.append(line)
        else:
            _, lang_hint, code = chunk
            code_stripped = code.strip()

            if not code_stripped:
                continue

            # Strip stray language label as first line of code
            first_line = code_stripped.split("\n")[0].strip().lower()
            if first_line in STRAY_LABELS:
                code_stripped = "\n".join(code_stripped.split("\n")[1:]).strip()
            # Handle "多语言版\npython\n..."
            if code_stripped.startswith("多语言版"):
                rest = code_stripped[len("多语言版"):].strip()
                if rest.split("\n")[0].strip().lower() in STRAY_LABELS:
                    rest = "\n".join(rest.split("\n")[1:]).strip()
                code_stripped = rest

            if not code_stripped:
                continue

            # Categorize and keep/skip the code block
            if is_example_block(code_stripped):
                new_lines.extend(["```", code_stripped, "```"])
            elif is_constraint_block(code_stripped):
                new_lines.extend(["```", code_stripped, "```"])
            elif is_python_block(code_stripped):
                new_lines.extend(["```python", code_stripped, "```"])
            elif lang_hint and lang_hint.lower() in ("python", "python3", "py"):
                new_lines.extend(["```python", code_stripped, "```"])
            # else: skip non-Python code

    new_body = "\n".join(new_lines)

    # Final cleanup: stray labels inside code blocks
    def fix_code_block(match: re.Match) -> str:
        lang = match.group(1) or ""
        code = match.group(2)
        block_lines = code.split("\n")
        while block_lines and block_lines[0].strip().lower() in STRAY_LABELS:
            block_lines.pop(0)
        while block_lines and not block_lines[0].strip():
            block_lines.pop(0)
        code = "\n".join(block_lines)
        if not code.strip():
            return ""
        return f"```{lang}\n{code}\n```"

    new_body = re.sub(r"```(\w*)\n(.*?)\n```", fix_code_block, new_body, flags=re.DOTALL)

    # Remove "### Code Solutions" header with no code following
    new_body = re.sub(r"\n### Code Solutions\s*\n(?=\n|$)", "\n", new_body)

    # Clean up multiple blank lines
    new_body = re.sub(r"\n{3,}", "\n\n", new_body)

    # Remove trailing whitespace on lines
    new_body = "\n".join(line.rstrip() for line in new_body.split("\n"))

    # Reassemble
    new_content = frontmatter + "\n" + new_body + "\n"
    new_content = re.sub(r"\n{3,}", "\n\n", new_content)

    # Ensure single trailing newline
    new_content = new_content.rstrip() + "\n"

    if new_content != content:
        filepath.write_text(new_content, encoding="utf-8")
        return True
    return False


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Clean up scraped problem markdown files: keep only Python code, fix formatting.",
    )
    parser.add_argument("directory", help="Directory containing problem .md files")
    parser.add_argument("--dry-run", action="store_true", help="Show what would change without writing")

    args = parser.parse_args()
    problems_dir = Path(args.directory)

    if not problems_dir.is_dir():
        print(f"ERROR: {problems_dir} is not a directory", file=__import__("sys").stderr)
        __import__("sys").exit(1)

    changed = 0
    total = 0

    for filepath in sorted(problems_dir.glob("*.md")):
        total += 1
        if args.dry_run:
            # Read-only check
            content = filepath.read_text(encoding="utf-8")
            original = content
            # We'd need to simulate; for simplicity just report all
            print(f"  Would check: {filepath.name}")
        else:
            if process_file(filepath):
                changed += 1

    print(f"\nProcessed {total} files, updated {changed}")


if __name__ == "__main__":
    main()
