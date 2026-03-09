#!/usr/bin/env python3
"""Scrape individual problem pages from a FlowUS collection (database) via API.

FlowUS collections store records as blocks with type 18. Each record has a UUID
that can be fetched via /api/docs/{uuid} to get the full page content as JSON.
This is much faster than Playwright page rendering (~30s for 300 records vs ~25min).

Usage:
    python problem_scraper.py --collection-data collection.json --output ./problems
    python problem_scraper.py --collection-data collection.json --output ./problems --token "eyJ..."
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from pathlib import Path

from playwright.async_api import async_playwright

from token_extractor import get_best_token


MAX_CONCURRENT = 5
BATCH_SIZE = 20


def sanitize_filename(name: str) -> str:
    """Remove characters that are invalid in file names."""
    name = re.sub(r'[<>:"/\\|?*]', "", name)
    name = name.strip(". ")
    return name[:200] if name else "untitled"


def extract_text(segments: list) -> str:
    """Extract plain text from FlowUS segment arrays."""
    text = ""
    for seg in segments:
        if isinstance(seg, dict):
            text += seg.get("text", "")
        elif isinstance(seg, list) and len(seg) > 0:
            text += str(seg[0])
    return text


def get_prop(col_props: dict, col_id: str) -> str:
    """Get a collection property value by column ID."""
    val = col_props.get(col_id, [])
    if not val:
        return ""
    return extract_text(val).strip()


def render_blocks_to_md(root_id: str, all_blocks: dict) -> str:
    """Render a FlowUS block tree to markdown.

    Block type reference:
        0  - text/paragraph
        1  - paragraph
        3  - bulleted list
        4  - numbered list
        5  - quote
        6  - code block
        7  - heading / divider
        8  - toggle
        9  - callout
        18 - collection (database)
        19 - collection view page
        22 - image
        25 - callout/example box
        27 - tab group
        28 - synced/embedded block (e.g., code solution tabs)
    """
    lines: list[str] = []

    def render(block_id: str, depth: int = 0) -> None:
        block = all_blocks.get(block_id)
        if not block:
            return

        btype = block.get("type", 0)
        bdata = block.get("data", {})
        segments = bdata.get("segments", [])
        text = extract_text(segments)
        sub_ids = block.get("subNodes", [])
        level = bdata.get("level", 0)

        if btype == 18:
            return  # skip collection root
        elif btype in (0, 1):  # text / paragraph
            if level and level > 0:
                lines.append("#" * min(level + 1, 6) + " " + text)
            elif text.strip():
                lines.append(text)
            else:
                lines.append("")
        elif btype == 3:  # bulleted list
            lines.append("  " * depth + "- " + text)
        elif btype == 4:  # numbered list
            lines.append("  " * depth + "- " + text)
        elif btype == 5:  # quote
            for qline in text.split("\n"):
                lines.append("> " + qline)
        elif btype == 6:  # code block
            lang = bdata.get("language", "")
            lines.append(f"```{lang}")
            lines.append(text)
            lines.append("```")
        elif btype == 7:  # heading / divider
            if text.strip():
                lines.append(f"\n## {text}")
            else:
                lines.append("---")
        elif btype == 8:  # toggle
            lines.append(f"\n**{text}**")
        elif btype == 9:  # callout
            for cline in text.split("\n"):
                lines.append("> " + cline)
        elif btype == 22:  # image
            url = bdata.get("url", bdata.get("source", ""))
            if url:
                lines.append(f"![{text}]({url})")
        elif btype == 25:  # callout/example box
            lines.append("```")
            lines.append(text)
            lines.append("```")
        elif btype == 27:  # tab group
            lines.append("\n### Code Solutions")
        elif btype == 28:  # synced/embedded block
            cp = bdata.get("collectionProperties", {})
            for cpid, cpval in cp.items():
                t = extract_text(cpval)
                if t.strip():
                    lines.append(f"\n#### {t.strip()}")
                    break
        elif btype == 19:  # collection view page
            pass
        else:
            if text.strip():
                lines.append(text)

        for child_id in sub_ids:
            render(child_id, depth + 1)

    root = all_blocks.get(root_id, {})
    for child_id in root.get("subNodes", []):
        render(child_id)

    return "\n".join(lines)


def parse_collection_data(
    api_data: dict,
    collection_id: str,
    schema_map: dict[str, str] | None = None,
) -> list[dict]:
    """Parse collection records from a FlowUS API response.

    Args:
        api_data: The full JSON response from /api/docs/{collection_page_id}.
        collection_id: UUID of the collection block (type 18).
        schema_map: Optional mapping of {column_id: column_name} for metadata.
            If not provided, only title and leetcode_url are extracted.

    Returns:
        List of record dicts with keys: id, title, leetcode_url, and any
        schema_map values.
    """
    blocks = api_data["data"]["blocks"]
    collection = blocks[collection_id]
    record_ids = collection["subNodes"]

    records = []
    for rid in record_ids:
        record = blocks.get(rid, {})
        if not record:
            continue

        title = record.get("title", "").strip()
        if not title:
            title = extract_text(record.get("data", {}).get("segments", []))
        title = title.strip()
        if not title:
            continue

        rec_data = record.get("data", {})
        col_props = rec_data.get("collectionProperties", {})

        # Extract LeetCode URL from segments
        leetcode_url = ""
        for seg in rec_data.get("segments", []):
            if isinstance(seg, dict) and seg.get("url"):
                leetcode_url = seg["url"]
                break

        rec = {"id": rid, "title": title, "leetcode_url": leetcode_url}

        # Extract schema-mapped properties
        if schema_map:
            for col_id, col_name in schema_map.items():
                rec[col_name] = get_prop(col_props, col_id)

        records.append(rec)

    return records


async def fetch_and_render(
    context,
    record_id: str,
    metadata: dict,
    idx: int,
    total: int,
    output_dir: Path,
    semaphore: asyncio.Semaphore,
) -> bool:
    """Fetch a single problem page via API and write to markdown."""
    async with semaphore:
        title = metadata["title"]
        try:
            api_page = await context.new_page()
            api_page.set_default_timeout(30000)

            url = f"https://flowus.cn/api/docs/{record_id}"
            resp = await api_page.goto(url)

            if not resp or resp.status != 200:
                print(f"  [{idx}/{total}] FAIL (HTTP {resp.status if resp else '?'}): {title}", flush=True)
                await api_page.close()
                return False

            body = await api_page.inner_text("body")
            await api_page.close()

            data = json.loads(body)
            doc_blocks = data.get("data", {}).get("blocks", {})
            content = render_blocks_to_md(record_id, doc_blocks)

            # Build markdown file with YAML frontmatter
            md_parts = ["---"]
            md_parts.append(f'title: "{title}"')
            if metadata.get("leetcode_url"):
                md_parts.append(f'leetcode: "{metadata["leetcode_url"]}"')
            md_parts.append(f'source: "https://flowus.cn/{record_id}"')
            for key in ("level", "difficulty", "importance", "tags"):
                if metadata.get(key):
                    if key == "tags":
                        md_parts.append(f"tags: [{metadata[key]}]")
                    else:
                        md_parts.append(f'{key}: "{metadata[key]}"')
            md_parts.append("---")
            md_parts.append("")
            md_parts.append(f"# {title}")
            md_parts.append("")
            if content.strip():
                md_parts.append(content)

            md_text = "\n".join(md_parts)
            filename = sanitize_filename(title) + ".md"
            filepath = output_dir / filename
            filepath.write_text(md_text, encoding="utf-8")

            print(f"  [{idx}/{total}] {title} ({len(content)} chars)", flush=True)
            return True

        except Exception as e:
            print(f"  [{idx}/{total}] FAIL: {title} - {e}", file=sys.stderr, flush=True)
            return False


async def async_main(args: argparse.Namespace) -> None:
    # Load collection data
    with open(args.collection_data) as f:
        api_data = json.load(f)

    # Find the collection block (type 18)
    blocks = api_data["data"]["blocks"]
    collection_id = None
    for bid, block in blocks.items():
        if block.get("type") == 18:
            collection_id = bid
            break

    if not collection_id:
        print("ERROR: No collection (type 18) block found in the data.", file=sys.stderr)
        sys.exit(1)

    # Build schema map from collection
    collection = blocks[collection_id]
    schema = collection.get("data", {}).get("schema", {})

    # Auto-detect common column names
    schema_map: dict[str, str] = {}
    diff_map = {"E": "Easy", "M": "Medium", "H": "Hard"}
    diff_col_id = None

    for col_id, col_info in schema.items():
        col_name = col_info.get("name", "").strip()
        lower = col_name.lower()
        if "等级" in col_name or lower == "level":
            schema_map[col_id] = "level"
        elif "难度" in col_name or lower == "difficulty":
            schema_map[col_id] = "difficulty"
            diff_col_id = col_id
        elif "重要" in col_name or lower == "importance":
            schema_map[col_id] = "importance"
        elif "标签" in col_name or lower == "tags":
            schema_map[col_id] = "tags"

    records = parse_collection_data(api_data, collection_id, schema_map)

    # Map difficulty codes
    if diff_col_id:
        for rec in records:
            if rec.get("difficulty") in diff_map:
                rec["difficulty"] = diff_map[rec["difficulty"]]

    print(f"Found {len(records)} records to scrape", flush=True)

    # Resolve token
    token = args.token
    if not token:
        print("Extracting token from FlowUS desktop app...")
        token = get_best_token()
        if not token:
            print("ERROR: No valid FlowUS token found. Pass --token.", file=sys.stderr)
            sys.exit(1)
        print("  Token found.")

    # Set up authenticated browser context
    pw = await async_playwright().start()
    browser = await pw.chromium.launch(headless=True)
    context = await browser.new_context()

    page = await context.new_page()
    page.set_default_timeout(60000)
    await page.goto("https://flowus.cn/product", wait_until="commit")
    await page.wait_for_timeout(2000)
    await page.evaluate(f'localStorage.setItem("token", "{token}")')
    await context.add_cookies([{
        "name": "token", "value": token,
        "domain": "flowus.cn", "path": "/",
    }])
    await page.close()

    # Fetch all records
    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)
    semaphore = asyncio.Semaphore(MAX_CONCURRENT)
    total = len(records)
    success = 0
    failed = 0

    for i in range(0, total, BATCH_SIZE):
        batch = records[i:i + BATCH_SIZE]
        batch_num = i // BATCH_SIZE + 1
        print(f"\nBatch {batch_num} ({i+1}-{min(i+BATCH_SIZE, total)} of {total}):", flush=True)

        tasks = [
            fetch_and_render(context, rec["id"], rec, i + j + 1, total, output_dir, semaphore)
            for j, rec in enumerate(batch)
        ]
        results = await asyncio.gather(*tasks)
        success += sum(1 for r in results if r)
        failed += sum(1 for r in results if not r)

    await context.close()
    await browser.close()
    await pw.stop()

    print(f"\n{'='*50}", flush=True)
    print(f"Done! Success: {success}, Failed: {failed}", flush=True)
    print(f"Output: {output_dir}", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Scrape problem pages from a FlowUS collection database via API.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
example workflow:
  1. First, scrape the collection page to get the full API response:
     python scraper.py --url "https://flowus.cn/<page-id>" --output ./data

  2. Or fetch it directly via the API discovery script:
     python api_discover.py --url "https://flowus.cn/<page-id>" --token "eyJ..."

  3. Then scrape all individual records:
     python problem_scraper.py --collection-data data/collection.json --output ./problems
""",
    )
    parser.add_argument(
        "--collection-data", required=True,
        help="Path to the JSON file from /api/docs/{page_id} containing the collection",
    )
    parser.add_argument("--output", default="./problems", help="Output directory (default: ./problems)")
    parser.add_argument("--token", help="FlowUS JWT token (auto-extracted if not provided)")

    args = parser.parse_args()
    asyncio.run(async_main(args))


if __name__ == "__main__":
    main()
