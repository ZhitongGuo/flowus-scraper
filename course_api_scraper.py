#!/usr/bin/env python3
"""Scrape course pages from FlowUS via API, extracting Python code from multi-language tabs.

1. Navigate to root course page with Playwright to discover sub-page UUIDs
2. Fetch each sub-page via API to get raw JSON block data
3. Extract Python code from tab groups (type 27/28)
4. Match to existing course markdown files and replace JavaScript code
"""

import asyncio
import json
import re
import sys
import urllib.request
from pathlib import Path

from playwright.async_api import async_playwright
from token_extractor import get_best_token

VAULT = Path.home() / "vaults" / "97-coding-leetcode"


def extract_text(block_data: dict) -> str:
    segs = block_data.get("segments", [])
    return "".join(s.get("text", "") for s in segs)


def find_python_code_blocks(blocks: dict, root_id: str) -> list[dict]:
    """Walk block tree and find Python code blocks inside tab groups.

    Returns list of dicts: {code, context, tab_label}
    """
    results = []
    all_code = []

    def walk(block_id, in_tab=False, tab_label="", depth=0):
        b = blocks.get(block_id, {})
        btype = b.get("type")
        bdata = b.get("data", {})
        text = extract_text(bdata)
        sub_ids = b.get("subNodes", [])

        if btype == 27:  # tab group
            for child_id in sub_ids:
                walk(child_id, in_tab=True, tab_label="", depth=depth + 1)
            return

        if btype == 28:  # synced/embedded block (tab item)
            cp = bdata.get("collectionProperties", {})
            label = ""
            for cpid, cpval in cp.items():
                t = extract_text(cpval)
                if t.strip():
                    label = t.strip()
                    break
            for child_id in sub_ids:
                walk(child_id, in_tab=True, tab_label=label, depth=depth + 1)
            return

        if btype == 6:  # code block
            lang = bdata.get("language", "").lower()
            code_entry = {
                "code": text,
                "language": lang,
                "in_tab": in_tab,
                "tab_label": tab_label,
                "block_id": block_id,
            }
            all_code.append(code_entry)
            if in_tab and ("python" in tab_label.lower() or lang == "python"):
                results.append(code_entry)

        for child_id in sub_ids:
            walk(child_id, in_tab=in_tab, tab_label=tab_label, depth=depth + 1)

    root = blocks.get(root_id, {})
    for child_id in root.get("subNodes", []):
        walk(child_id)

    return results, all_code


async def discover_course_pages(root_url: str, token: str) -> list[dict]:
    """Navigate root course page and discover all sub-page UUIDs."""
    pw = await async_playwright().start()
    browser = await pw.chromium.launch(headless=True)
    ctx = await browser.new_context()
    page = await ctx.new_page()
    page.set_default_timeout(30000)

    # Auth
    await page.goto("https://flowus.cn/product", wait_until="commit")
    await page.wait_for_timeout(2000)
    await page.evaluate(f'localStorage.setItem("token", "{token}")')

    # Collect API data
    all_blocks = {}

    async def on_response(response):
        url = response.url
        if "/api/docs/" in url and response.status == 200:
            try:
                body = await response.json()
                blocks = body.get("data", {}).get("blocks", {})
                if blocks:
                    all_blocks.update(blocks)
            except:
                pass

    page.on("response", on_response)

    print(f"Navigating to {root_url}...")
    await page.goto(root_url, wait_until="domcontentloaded")
    await page.wait_for_timeout(8000)

    # Click expand/load-more buttons
    for _ in range(10):
        try:
            for text_label in ["加载更多", "Load more"]:
                btn = page.locator(f'text="{text_label}"').first
                if await btn.is_visible(timeout=500):
                    await btn.click()
                    await page.wait_for_timeout(1500)
        except:
            break

    await page.wait_for_timeout(3000)

    title = await page.title()
    print(f"Page title: {title}")
    print(f"Total blocks discovered: {len(all_blocks)}")

    # Find all page-like blocks (sub-pages)
    pages = []
    for bid, b in all_blocks.items():
        btype = b.get("type")
        bdata = b.get("data", {})
        text = extract_text(bdata)
        if text.strip():
            pages.append({"id": bid, "type": btype, "title": text.strip()[:100]})

    await ctx.close()
    await browser.close()
    await pw.stop()

    return pages, all_blocks


def fetch_page_blocks(uuid: str, token: str) -> dict:
    """Fetch page blocks via API."""
    req = urllib.request.Request(
        f"https://flowus.cn/api/docs/{uuid}",
        headers={"Authorization": f"Bearer {token}", "User-Agent": "Mozilla/5.0"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read())
            if data.get("code") == 200:
                return data.get("data", {}).get("blocks", {})
    except Exception as e:
        print(f"  Error fetching {uuid}: {e}")
    return {}


async def main():
    token = get_best_token()
    if not token:
        print("ERROR: No FlowUS token found", file=sys.stderr)
        sys.exit(1)

    root_url = "https://flowus.cn/b6c9fcac-96b2-4c38-b535-851e11c7bdbb"
    root_uuid = "b6c9fcac-96b2-4c38-b535-851e11c7bdbb"

    # Step 1: Discover structure
    pages, blocks = await discover_course_pages(root_url, token)

    print(f"\nDiscovered {len(pages)} blocks with text")

    # Show structure
    for p in pages[:20]:
        print(f"  [{p['type']}] {p['id'][:12]}... {p['title'][:60]}")

    # Save raw data for analysis
    out_path = Path("/tmp/flowus_course_blocks.json")
    serializable = {}
    for bid, b in blocks.items():
        serializable[bid] = {
            "type": b.get("type"),
            "data": b.get("data", {}),
            "subNodes": b.get("subNodes", []),
        }
    out_path.write_text(json.dumps(serializable, ensure_ascii=False, indent=2))
    print(f"\nSaved {len(blocks)} blocks to {out_path}")

    # Step 2: Find sub-pages that are course articles
    # Look for blocks that reference sub-pages (type 19 = page reference, or blocks with UUIDs)
    sub_page_uuids = set()
    for bid, b in blocks.items():
        btype = b.get("type")
        # Collect all block IDs - we'll try to fetch them as pages
        if btype in (0, 7, 19):  # text, heading, page ref
            bdata = b.get("data", {})
            segs = bdata.get("segments", [])
            for seg in segs:
                ref = seg.get("blockId") or seg.get("pageId")
                if ref and ref != root_uuid:
                    sub_page_uuids.add(ref)

    # Also look for sub-pages in subNodes of section blocks
    for bid, b in blocks.items():
        for child_id in b.get("subNodes", []):
            if child_id not in blocks:  # not loaded = likely a sub-page
                sub_page_uuids.add(child_id)

    print(f"\nFound {len(sub_page_uuids)} potential sub-page UUIDs")

    # Step 3: Try to fetch each as a page and look for Python code
    found_python = 0
    for i, uuid in enumerate(sorted(sub_page_uuids)):
        page_blocks = fetch_page_blocks(uuid, token)
        if not page_blocks:
            continue

        root_block = page_blocks.get(uuid, {})
        title = extract_text(root_block.get("data", {}))
        if not title:
            continue

        python_code, all_code = find_python_code_blocks(page_blocks, uuid)

        tab_langs = set()
        for c in all_code:
            if c["in_tab"]:
                tab_langs.add(c["tab_label"])

        if python_code:
            print(f"  [{i+1}] {title[:60]}: {len(python_code)} Python blocks (tabs: {tab_langs})")
            found_python += 1
        elif all_code:
            print(f"  [{i+1}] {title[:60]}: {len(all_code)} code blocks, no Python (tabs: {tab_langs})")

    print(f"\nPages with Python code: {found_python}")


if __name__ == "__main__":
    asyncio.run(main())
