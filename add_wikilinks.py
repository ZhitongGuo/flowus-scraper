#!/usr/bin/env python3
"""Convert plain text problem/article references in course docs to Obsidian wikilinks.

Handles three emoji types:
  🔥 / 🚀 → LeetCode problem references → [[题目/problems/NNN. 题目名|NNN. 题目名]]
  🧊     → Course article references    → [[path/to/article|article title]]

Pattern: emoji on its own line, followed by a line starting with the reference text.
"""

import os
import re
import sys
from pathlib import Path

VAULT = Path(os.path.expanduser("~/vaults/97-coding-leetcode"))
PROBLEMS_DIR = VAULT / "题目" / "problems"
COURSE_DIRS = [VAULT / f"C{i}" for i in range(5)]

# Emoji types
PROBLEM_EMOJIS = {"🔥", "🚀"}
ARTICLE_EMOJI = "🧊"


def build_problem_index():
    """Build mapping: problem number -> filename (without .md).
    Also stores all names for prefix matching non-numeric problems."""
    index = {}
    all_names = []
    for f in PROBLEMS_DIR.iterdir():
        if f.suffix == ".md":
            name = f.stem  # e.g. "509. 斐波那契数"
            all_names.append(name)
            m = re.match(r"^(\d+)\.", name)
            if m:
                index[m.group(1)] = name
    # Sort by length descending for longest-prefix-first matching
    index["__all_names__"] = sorted(all_names, key=len, reverse=True)
    return index


def build_article_index():
    """Build mapping: article title prefix -> relative path from vault root (without .md)."""
    index = {}
    for cdir in COURSE_DIRS:
        if not cdir.exists():
            continue
        for md in cdir.rglob("*.md"):
            stem = md.stem  # e.g. "A1 - L1 拓扑排序序列上的先序数据传输"
            rel = md.relative_to(VAULT).with_suffix("")  # e.g. C0/S2.../A1 - L1 ...
            # Extract the title part after "A\d+ - " for matching
            # The 🧊 reference text looks like "A1｜L1 拓扑排序序列上的先序数据传输"
            # while the filename is "A1 - L1 拓扑排序序列上的先序数据传输"
            # Build a normalized key for matching
            index[stem] = str(rel)
    return index


def normalize_article_ref(text):
    """Normalize article reference text to match filename format.

    Reference: "A2｜L2 重叠性：组合问题重叠性分析"
    Filename:  "A2 - L2 重叠性 - 组合问题重叠性分析"
    """
    # Replace fullwidth ｜ with " - "
    text = re.sub(r"｜", " - ", text)
    # Also handle regular |
    text = re.sub(r"\|", " - ", text)
    # Replace fullwidth colon ：with " - "
    text = re.sub(r"：", " - ", text)
    return text.strip()


def extract_problem_number(line):
    """Extract LeetCode problem number from line like '509. 斐波那契数 的计算过程...'"""
    m = re.match(r"^(\d+)\.\s", line)
    if m:
        return m.group(1)
    return None


def extract_problem_title(line, problem_index):
    """Extract the full problem title from the line by matching against known problems."""
    num = extract_problem_number(line)
    if num and num in problem_index:
        return problem_index[num]
    # Handle non-numeric problem names like "面试题 08.14. 布尔运算"
    # Try matching against all known problem names by prefix (longest first)
    for name in problem_index.get("__all_names__", []):
        if line.startswith(name):
            return name
    return None


def find_article_match(text, article_index):
    """Find matching article for a 🧊 reference."""
    normalized = normalize_article_ref(text)
    # Try exact match first
    if normalized in article_index:
        return article_index[normalized], normalized
    # Try prefix match (reference might have trailing chars or be truncated)
    best_match = None
    best_len = 0
    for stem, rel_path in article_index.items():
        if stem.startswith(normalized) or normalized.startswith(stem):
            # Prefer the longest matching stem
            if len(stem) > best_len:
                best_match = (rel_path, stem)
                best_len = len(stem)
    return best_match if best_match else (None, None)


def process_file(filepath, problem_index, article_index, dry_run=False):
    """Process a single course document."""
    with open(filepath, "r", encoding="utf-8") as f:
        lines = f.readlines()

    changes = []
    i = 0
    new_lines = []

    while i < len(lines):
        line = lines[i].rstrip("\n")
        stripped = line.strip()

        # Check if this is an emoji line on its own
        if stripped in PROBLEM_EMOJIS and i + 1 < len(lines):
            emoji = stripped
            next_line = lines[i + 1].rstrip("\n")
            next_stripped = next_line.strip()

            # Try to extract problem reference
            title = extract_problem_title(next_stripped, problem_index)
            if title:
                # Build wikilink
                wikilink = f"[[题目/problems/{title}|{title}]]"

                # Check what follows the problem number+title in the text
                # e.g. "509. 斐波那契数 的计算过程..." → need to keep " 的计算过程..."
                remaining = next_stripped[len(title):]

                # Combine emoji + wikilink + remaining text on one line
                # Drop the standalone emoji line, merge into one
                new_line = f"{emoji} {wikilink}{remaining}"
                changes.append((i + 1, next_stripped, new_line))
                new_lines.append(new_line + "\n")
                i += 2
                continue

        elif stripped == ARTICLE_EMOJI and i + 1 < len(lines):
            next_line = lines[i + 1].rstrip("\n")
            next_stripped = next_line.strip()

            # Try to match article reference
            # Extract the article ID prefix like "A1｜L1 ..." or "A5｜L2 ..."
            m = re.match(r"^(A\d+[｜|]L\d+\s+.+?)(?:这篇文章|中我们|中求|中接触|中提到|$)", next_stripped)
            if m:
                ref_text = m.group(1).strip()
                rel_path, stem = find_article_match(ref_text, article_index)
                if rel_path:
                    wikilink = f"[[{rel_path}|{ref_text}]]"
                    # Keep the suffix after the reference
                    suffix_start = m.end(1)
                    remaining = next_stripped[suffix_start:]
                    # Drop the standalone emoji line, merge into one
                    new_line = f"{ARTICLE_EMOJI} {wikilink}{remaining}"
                    changes.append((i + 1, next_stripped, new_line))
                    new_lines.append(new_line + "\n")
                    i += 2
                    continue

        new_lines.append(lines[i])
        i += 1

    if changes and not dry_run:
        with open(filepath, "w", encoding="utf-8") as f:
            f.writelines(new_lines)

    return changes


def main():
    dry_run = "--dry-run" in sys.argv

    problem_index = build_problem_index()
    article_index = build_article_index()

    print(f"Problem index: {len(problem_index)} entries")
    print(f"Article index: {len(article_index)} entries")
    print()

    total_changes = 0
    files_changed = 0

    for cdir in COURSE_DIRS:
        if not cdir.exists():
            continue
        for md in sorted(cdir.rglob("*.md")):
            changes = process_file(md, problem_index, article_index, dry_run=dry_run)
            if changes:
                files_changed += 1
                rel = md.relative_to(VAULT)
                print(f"{'[DRY RUN] ' if dry_run else ''}{rel}: {len(changes)} link(s)")
                for lineno, old, new in changes:
                    print(f"  L{lineno}: {old}")
                    print(f"      → {new}")
                total_changes += len(changes)

    print(f"\n{'[DRY RUN] ' if dry_run else ''}Total: {total_changes} links in {files_changed} files")


if __name__ == "__main__":
    main()
