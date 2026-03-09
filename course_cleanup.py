#!/usr/bin/env python3
"""Clean up scraped course document markdown files.

Fixes common issues from FlowUS scraping:
1. Wrap bare code (preceded by language labels like "JS") in code blocks
2. Remove "多语言版" labels
3. Clean up metadata noise at top (chapter/section/emoji blocks)
4. Remove duplicate TOC at bottom of each file
5. Remove zero-width spaces and empty inline elements
6. Convert standalone section labels to proper markdown headings

Usage:
    python course_cleanup.py ~/vaults/97-coding-leetcode
    python course_cleanup.py ~/vaults/97-coding-leetcode --dry-run
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path


# Language labels that precede bare code blocks
LANG_LABELS = {
    "JS", "js", "JavaScript", "javascript",
    "Python", "python", "python3", "py",
    "Java", "java",
    "Go", "go", "golang",
    "C++", "c++", "cpp", "C",
    "TypeScript", "typescript", "ts",
}

# Map language labels to markdown code fence languages
LANG_MAP = {
    "js": "javascript", "JS": "javascript", "JavaScript": "javascript", "javascript": "javascript",
    "python": "python", "Python": "python", "python3": "python", "py": "python",
    "java": "java", "Java": "java",
    "go": "go", "Go": "go", "golang": "go",
    "c++": "cpp", "C++": "cpp", "cpp": "cpp", "C": "c",
    "typescript": "typescript", "TypeScript": "typescript", "ts": "typescript",
}

# Lines that indicate the start of metadata noise block
METADATA_START_PATTERNS = [
    "🔥 课程例题",
    "🚀 秒杀题目",
]

# Lines that are part of the metadata block
METADATA_PATTERNS = [
    re.compile(r"^🔥\s*课程例题"),
    re.compile(r"^🚀\s*秒杀题目"),
    re.compile(r"^章（CHAPTER）"),
    re.compile(r"^节（SECTION）"),
    re.compile(r"^🌕$"),
    re.compile(r"^🧑‍💻$"),
    re.compile(r"^视频$"),
    re.compile(r"^课程视频$"),
    re.compile(r"^序$"),
    re.compile(r"^\d{1,3}$"),  # just a number (article sequence)
    re.compile(r"^C\d+\s"),   # chapter label like "C1 广搜：队列搜索"
    re.compile(r"^S\d+\s"),   # section label like "S1 队列与广度优先搜索"
]

# Patterns for lines to always remove
REMOVE_PATTERNS = [
    re.compile(r"^多语言版$"),
    re.compile(r"^多语言版本$"),
    re.compile(r"^多语言$"),
    re.compile(r"^逐行讲解$"),
    re.compile(r"^可视讲解$"),
]

# Zero-width space and similar invisible characters
ZWS_PATTERN = re.compile(r"[\u200b\u200c\u200d\ufeff]+")


def starts_with_cjk(text: str) -> bool:
    """Check if text starts with CJK characters (ignoring whitespace)."""
    stripped = text.strip()
    if not stripped:
        return False
    return bool(re.match(r"[\u4e00-\u9fff\u3400-\u4dbf\uf900-\ufaff]", stripped))


def is_prose(line: str) -> bool:
    """Check if a line is clearly Chinese prose, not code."""
    stripped = line.strip()
    if not stripped:
        return False
    # Starts with CJK characters and doesn't start with code indentation
    if starts_with_cjk(stripped):
        return True
    # Starts with emoji followed by text
    if re.match(r"^[\U0001f300-\U0001f9ff]", stripped):
        return True
    return False


def is_code_like(line: str) -> bool:
    """Heuristic: does this line look like code?"""
    stripped = line.strip()
    if not stripped:
        return True  # blank lines inside code blocks are fine

    # Lines that start with Chinese text are prose, not code
    if is_prose(line):
        return False

    # Lines that are just emoji or very short non-code
    if len(stripped) <= 2 and stripped not in ("}", "{", ");", "*/"):
        return False

    code_indicators = [
        stripped.startswith(("var ", "const ", "let ", "function ", "if(", "if (", "for(", "for (",
                            "while(", "while (", "return ", "class ", "def ", "import ", "from ",
                            "//", "/*", "* ", "*/", "#include", "public ", "private ",
                            "self.", "self,", "print(", "console.", "else{", "else {",
                            "} else", "};", "}", "{", "]", ")", "=>", ".push(",
                            ".shift(", ".pop(", ".length", "new Array", "new Map",
                            "Math.", "async ", "await ", "try{", "try {", "catch(",
                            "throw ", "switch(", "case ", "break", "continue",
                            "/**", " * ", "package ", "func ", "fmt.",
                            "ArrayList", "HashMap", "vector<", "nullptr",
                            "range(", "len(", "append(", "List[", "Optional[",
                            "True", "False", "None", "elif ",
                            "heapq.", "deque(", "collections.", "defaultdict",
                            )),
        stripped.endswith((";", "{", "};", ");", ")")),
        re.match(r"^\s+(var|const|let|if|for|while|return|def|self)", line) is not None,
        re.match(r"^\s+\w+\.\w+\(", line) is not None,  # method calls with indentation
        re.match(r"^\s*\w+\(", stripped) is not None,  # function calls like dfs(root)
    ]
    return any(code_indicators)


def is_still_code(line: str) -> bool:
    """More permissive check for continuing inside a code block.

    Once we know we're in code, allow lines with Chinese comments (e.g., //注释).
    """
    stripped = line.strip()
    if not stripped:
        return True  # blank lines within code

    # If it looks like code (including lines with Chinese in comments), keep it
    if is_code_like(line):
        return True

    # Lines that start with Chinese are definitely the end of code
    if is_prose(line):
        return False

    # Indented lines with code operators are code (even with Chinese comments)
    if line.startswith((" ", "\t")) and re.search(r"[+\-*/=<>!&|;{}\[\]()]", stripped):
        return True

    # Lines with // comments (even with Chinese after) are code
    if "//" in stripped and not starts_with_cjk(stripped):
        return True

    # Inside a code block, non-prose lines with code-like characters are code
    if not starts_with_cjk(stripped):
        if re.match(r"^[\w\s\[\]\(\)\{\}=<>+\-*/,.:;!&|^~%@#$?\"\'`\\]+$", stripped):
            return True

    return False


def is_code_start(line: str) -> bool:
    """Check if a line is likely the start of a code block (without language label)."""
    stripped = line.strip()
    if is_prose(line):
        return False
    # Strong indicators of code block start
    return bool(re.match(
        r"^(var |const |let |function |def |class |import |from |"
        r"/\*\*|// |#include|public |private |package |func )",
        stripped,
    ))


def _scan_code_extent(lines: list[str], code_start: int) -> int:
    """From code_start, scan forward to find the end of contiguous code lines.

    Uses the more permissive `is_still_code` since we've already determined
    we're inside a code block.

    Returns the index one past the last code line.
    """
    code_end = code_start
    while code_end < len(lines):
        next_line = lines[code_end]
        next_stripped = next_line.strip()

        # Hard stop conditions
        if next_stripped in LANG_LABELS and code_end > code_start:
            break
        if next_stripped in ("多语言版", "多语言", "逐行讲解", "可视讲解"):
            break
        if next_stripped.startswith("##"):
            break

        # Non-code line after a blank line signals end of code block
        if (code_end > code_start + 2 and not next_stripped
                and code_end + 1 < len(lines)
                and lines[code_end + 1].strip()
                and not is_still_code(lines[code_end + 1])):
            break

        if is_still_code(next_line):
            code_end += 1
        else:
            break

    # Trim trailing blank lines
    while code_end > code_start and not lines[code_end - 1].strip():
        code_end -= 1

    return code_end


def detect_language_from_code(code_lines: list[str]) -> str:
    """Guess the language of a code block from its content."""
    text = "\n".join(code_lines)
    # JS indicators
    if any(k in text for k in ["var ", "const ", "let ", "function ", "=> {", "===", ".push(", "Math."]):
        return "javascript"
    # Python indicators
    if any(k in text for k in ["def ", "self.", "self,", "range(", "List[", "elif "]):
        return "python"
    # Java
    if any(k in text for k in ["public class", "public static", "ArrayList<", "HashMap<"]):
        return "java"
    # Go
    if any(k in text for k in ["func ", "fmt.", ":= ", "package "]) and "def " not in text:
        return "go"
    # C++
    if any(k in text for k in ["vector<", "#include", "nullptr", "::", "cout"]):
        return "cpp"
    return ""


def detect_bare_code_blocks(lines: list[str]) -> list[tuple[int, int, str]]:
    """Find code blocks that aren't wrapped in ``` fences.

    Returns list of (start_line, end_line, language) tuples.
    Start is the first line to replace, end is the last code line index (inclusive).
    """
    blocks = []
    i = 0
    while i < len(lines):
        stripped = lines[i].strip()

        # Case 1: Language label followed by code
        if stripped in LANG_LABELS:
            lang = LANG_MAP.get(stripped, stripped.lower())
            code_start = i + 1
            code_end = _scan_code_extent(lines, code_start)

            if code_end > code_start:
                blocks.append((i, code_end - 1, lang))
                i = code_end
                continue

        # Case 2: Code starts without a language label
        elif is_code_start(stripped):
            code_end = _scan_code_extent(lines, i)

            if code_end > i + 1:  # at least 2 lines of code
                code_lines = lines[i:code_end]
                lang = detect_language_from_code(code_lines)
                blocks.append((i, code_end - 1, lang))
                i = code_end
                continue

        i += 1

    return blocks


def is_in_code_fence(lines: list[str], line_idx: int) -> bool:
    """Check if a line index is inside a ``` code fence."""
    in_fence = False
    for i in range(line_idx):
        if lines[i].strip().startswith("```"):
            in_fence = not in_fence
    return in_fence


def find_metadata_block(lines: list[str]) -> tuple[int, int] | None:
    """Find the metadata noise block after the title.

    Returns (start, end) line indices, or None if not found.
    """
    # Find the title line
    title_idx = None
    for i, line in enumerate(lines):
        if line.strip().startswith("# ") and not line.strip().startswith("## "):
            title_idx = i
            break

    if title_idx is None:
        return None

    # Look for metadata block starting after title
    meta_start = None
    for i in range(title_idx + 1, min(title_idx + 5, len(lines))):
        stripped = lines[i].strip()
        if any(p.match(stripped) for p in METADATA_PATTERNS) or stripped in ("🔥", "🚀"):
            meta_start = i
            break

    if meta_start is None:
        return None

    # Find the end of the metadata block
    meta_end = meta_start
    i = meta_start
    while i < len(lines):
        stripped = lines[i].strip()

        # The metadata block ends when we hit "课程内容" or a real content heading
        if stripped == "课程内容":
            meta_end = i  # include this line
            break

        if any(p.match(stripped) for p in METADATA_PATTERNS):
            meta_end = i
            i += 1
            continue

        # Problem references with emojis
        if stripped.startswith(("🔥", "🚀")):
            meta_end = i
            i += 1
            continue

        # Problem titles (e.g., "509. 斐波那契数" or "207. 课程表｜")
        if re.match(r"^\d+\.\s", stripped) or stripped.startswith("LCP "):
            meta_end = i
            i += 1
            continue

        # Empty lines within metadata
        if not stripped:
            meta_end = i
            i += 1
            continue

        # Short lines that look like they're part of metadata
        if len(stripped) <= 2:
            meta_end = i
            i += 1
            continue

        break

    return (meta_start, meta_end)


def extract_problem_refs(lines: list[str], start: int, end: int) -> list[str]:
    """Extract problem references from the metadata block."""
    problems = []
    for i in range(start, end + 1):
        stripped = lines[i].strip()
        # Match problem patterns like "509. 斐波那契数" or "LCP 28. 采购方案"
        m = re.match(r"^(?:🔥\s*|🚀\s*)?(\d+\.\s.+?)(?:｜|$)", stripped)
        if m:
            problems.append(m.group(1).strip())
        m2 = re.match(r"^(?:🔥\s*|🚀\s*)?((LCP|面试题|剑指)\s.+?)(?:｜|$)", stripped)
        if m2:
            problems.append(m2.group(1).strip())
    return problems


def find_trailing_toc(lines: list[str]) -> int | None:
    """Find the start of a trailing table of contents.

    The TOC is a block at the very end that duplicates section headings.
    Only matches the actual TOC block (typically 5-15 lines of section names),
    not content above it.

    Returns the start index, or None if no TOC found.
    """
    if len(lines) < 5:
        return None

    # Work backwards from the end
    i = len(lines) - 1

    # Skip trailing blank lines
    while i >= 0 and not lines[i].strip():
        i -= 1

    if i < 3:
        return None

    # The TOC is specifically the section heading list at the very bottom.
    # It consists of short Chinese text lines (section names), no code.
    # We scan backwards but STOP as soon as we hit something that's not
    # a plausible TOC entry (code, long text, numbered items, etc.)
    toc_start = None
    toc_count = 0
    max_toc_lines = 20  # TOC shouldn't be more than ~20 lines

    while i >= 0 and toc_count < max_toc_lines:
        stripped = lines[i].strip()

        if not stripped:
            # Allow blank lines within the TOC
            if toc_count >= 2:
                i -= 1
                continue
            break

        # TOC entries are Chinese section headings: short, no code syntax
        if (len(stripped) < 30
                and not stripped.startswith(("#", "```", "-", ">", "!", "[", "\t"))
                and not is_code_like(lines[i])
                and not stripped.isdigit()  # single numbers are often content, not TOC
                and not re.match(r"^\d+$", stripped)  # numbers
                ):
            toc_count += 1
            toc_start = i
            i -= 1
        else:
            break

    # Need at least 3 entries to be a real TOC
    if toc_count >= 3:
        return toc_start

    return None


def process_course_file(filepath: Path, dry_run: bool = False) -> tuple[bool, list[str]]:
    """Process a single course document. Returns (changed, changes_made)."""
    content = filepath.read_text(encoding="utf-8")
    changes: list[str] = []

    # Split frontmatter from body
    fm_match = re.match(r"^(---\n.*?\n---)\n", content, re.DOTALL)
    if fm_match:
        frontmatter = fm_match.group(1)
        body = content[fm_match.end():]
    else:
        frontmatter = ""
        body = content

    lines = body.split("\n")

    # 1. Remove zero-width spaces
    new_lines = []
    for line in lines:
        cleaned = ZWS_PATTERN.sub("", line)
        # Also remove standalone emoji markers that are just formatting artifacts
        if cleaned.strip() in ("​​", "​​​​", ""):
            if not line.strip():
                new_lines.append("")
            # else: skip zero-width-only lines
            continue
        new_lines.append(cleaned)
    if new_lines != lines:
        changes.append("removed zero-width spaces")
    lines = new_lines

    # 2. Remove metadata noise block
    meta_range = find_metadata_block(lines)
    if meta_range:
        start, end = meta_range
        # Extract problem references before removing
        prob_refs = extract_problem_refs(lines, start, end)
        lines = lines[:start] + lines[end + 1:]
        changes.append(f"removed metadata block (lines {start}-{end})")

    # 3. Remove trailing TOC
    toc_start = find_trailing_toc(lines)
    if toc_start is not None:
        lines = lines[:toc_start]
        changes.append(f"removed trailing TOC (from line {toc_start})")

    # 4. Remove "多语言版" and similar labels
    filtered = []
    for line in lines:
        stripped = line.strip()
        if any(p.match(stripped) for p in REMOVE_PATTERNS):
            changes.append(f"removed '{stripped}'") if f"removed '{stripped}'" not in changes else None
            continue
        filtered.append(line)
    lines = filtered

    # 5. Wrap bare code in code fences
    # First, identify which lines are inside existing code fences
    in_fence = False
    fence_map: list[bool] = []
    for line in lines:
        if line.strip().startswith("```"):
            in_fence = not in_fence
        fence_map.append(in_fence)

    # Find bare code blocks (only outside fences)
    bare_blocks = detect_bare_code_blocks(lines)
    # Filter to only those outside fences
    bare_blocks = [
        (s, e, lang) for s, e, lang in bare_blocks
        if not fence_map[s]
    ]

    if bare_blocks:
        changes.append(f"wrapped {len(bare_blocks)} bare code block(s)")
        # Build new lines with code fences inserted
        # Work in reverse to preserve indices
        for start, end, lang in reversed(bare_blocks):
            # The language label line becomes the opening fence
            code_content = lines[start + 1:end + 1]
            # Trim leading/trailing blank lines from code
            while code_content and not code_content[0].strip():
                code_content.pop(0)
            while code_content and not code_content[-1].strip():
                code_content.pop()

            replacement = [f"```{lang}"] + code_content + ["```"]
            lines[start:end + 1] = replacement

    # 6. Clean up formatting
    body = "\n".join(lines)

    # Remove multiple consecutive blank lines
    body = re.sub(r"\n{3,}", "\n\n", body)

    # Remove trailing whitespace on lines
    body = "\n".join(line.rstrip() for line in body.split("\n"))

    # Reassemble
    new_content = frontmatter + "\n" + body + "\n"
    new_content = re.sub(r"\n{3,}", "\n\n", new_content)
    new_content = new_content.rstrip() + "\n"

    changed = new_content != content
    if changed and not dry_run:
        filepath.write_text(new_content, encoding="utf-8")

    return changed, changes


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Clean up scraped course document markdown files.",
    )
    parser.add_argument("directory", help="Root directory of the Obsidian vault")
    parser.add_argument("--dry-run", action="store_true", help="Preview changes without writing")

    args = parser.parse_args()
    root = Path(args.directory)

    if not root.is_dir():
        print(f"ERROR: {root} is not a directory", file=sys.stderr)
        sys.exit(1)

    # Find all course documents (C0-C4 chapters, excluding problems/)
    course_files = []
    for chapter_dir in sorted(root.glob("C*")):
        if chapter_dir.is_dir():
            course_files.extend(sorted(chapter_dir.rglob("*.md")))

    # Also process top-level non-problem files
    for f in sorted(root.glob("*.md")):
        if f.name not in ("README.md",):
            course_files.append(f)

    # Process problem list files
    problems_dir = root / "题目"
    if problems_dir.is_dir():
        for f in sorted(problems_dir.glob("*.md")):
            if f.parent.name != "problems":
                course_files.append(f)

    print(f"Found {len(course_files)} course document(s)")

    changed_count = 0
    for filepath in course_files:
        rel_path = filepath.relative_to(root)
        was_changed, changes = process_course_file(filepath, dry_run=args.dry_run)
        if was_changed:
            changed_count += 1
            prefix = "[DRY RUN] " if args.dry_run else ""
            print(f"  {prefix}{rel_path}: {', '.join(changes[:3])}")

    action = "Would update" if args.dry_run else "Updated"
    print(f"\n{action} {changed_count} of {len(course_files)} files")


if __name__ == "__main__":
    main()
