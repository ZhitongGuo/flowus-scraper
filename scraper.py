#!/usr/bin/env python3
"""Scrape FlowUS pages to markdown files using Playwright browser automation.

FlowUS is a Chinese Notion-like SPA that renders entirely client-side,
so we use Playwright to inject auth tokens and extract rendered content.
"""

from __future__ import annotations

import argparse
import asyncio
import re
import sys
import urllib.parse
from pathlib import Path

from playwright.async_api import async_playwright, Page, Browser

from token_extractor import get_best_token, DEFAULT_LEVELDB_PATH


# Rate limiting
MAX_CONCURRENT = 3
_semaphore: asyncio.Semaphore | None = None


def get_semaphore() -> asyncio.Semaphore:
    global _semaphore
    if _semaphore is None:
        _semaphore = asyncio.Semaphore(MAX_CONCURRENT)
    return _semaphore


def sanitize_filename(name: str) -> str:
    """Remove characters that are invalid in file names."""
    name = re.sub(r'[<>:"/\\|?*]', "", name)
    name = name.strip(". ")
    return name[:200] if name else "untitled"


def clean_page_text(text: str) -> str:
    """Strip sidebar navigation and clean up extracted page text.

    FlowUS pages include sidebar nav at the top. We use the "Add comment"
    marker (评论区) to find where actual content ends, and strip everything
    after it. We also strip repeated header lines from the top.
    """
    lines = text.split("\n")

    # Find "Add comment" or similar markers that indicate end of content
    cutoff_markers = ["Add comment", "添加评论", "评论"]
    cutoff = len(lines)
    for i, line in enumerate(lines):
        stripped = line.strip()
        if any(stripped == marker for marker in cutoff_markers):
            cutoff = i
            break

    lines = lines[:cutoff]

    # Strip leading blank lines
    while lines and not lines[0].strip():
        lines.pop(0)

    return "\n".join(lines).strip()


async def create_authenticated_page(browser: Browser, token: str) -> Page:
    """Create a new browser page with FlowUS authentication."""
    context = await browser.new_context()
    page = await context.new_page()

    # Navigate to FlowUS to establish origin, then inject token
    await page.goto("https://flowus.cn/product", wait_until="domcontentloaded")
    await page.evaluate(f'localStorage.setItem("token", "{token}")')

    return page


async def scrape_page(page: Page, url: str, wait_seconds: float = 3.0) -> dict:
    """Scrape a single FlowUS page. Returns dict with title, text, sub_links."""
    await page.goto(url, wait_until="domcontentloaded")
    await page.wait_for_timeout(int(wait_seconds * 1000))

    # Click "Load more" buttons if present
    for _ in range(20):
        try:
            load_more = page.locator('text="Load more"').first
            if await load_more.is_visible(timeout=500):
                await load_more.click()
                await page.wait_for_timeout(1000)
            else:
                break
        except Exception:
            break

    # Also try Chinese variant
    for _ in range(20):
        try:
            load_more = page.locator('text="加载更多"').first
            if await load_more.is_visible(timeout=500):
                await load_more.click()
                await page.wait_for_timeout(1000)
            else:
                break
        except Exception:
            break

    title = await page.title()
    title = title.replace(" - FlowUs 息流", "").strip()

    body_text = await page.inner_text("body")
    text = clean_page_text(body_text)

    # Extract sub-page links
    sub_links = []
    links = await page.query_selector_all("a[href]")
    for link in links:
        href = await link.get_attribute("href")
        if href and "/share/" not in href:
            # FlowUS sub-page links are UUIDs
            if re.search(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", href or ""):
                full_url = urllib.parse.urljoin("https://flowus.cn", href)
                if full_url not in [s["url"] for s in sub_links]:
                    link_text = (await link.inner_text()).strip()
                    sub_links.append({"url": full_url, "title": link_text or "Untitled"})

    return {"title": title, "text": text, "sub_links": sub_links}


def make_frontmatter(title: str, source_url: str, tags: list[str] | None = None) -> str:
    """Generate YAML frontmatter."""
    lines = ["---", f'title: "{title}"', f'source: "{source_url}"']
    if tags:
        lines.append(f'tags: [{", ".join(tags)}]')
    lines.append("---")
    return "\n".join(lines)


async def scrape_single(
    url: str,
    output_dir: Path,
    token: str,
    obsidian: bool = False,
    browser: Browser | None = None,
    _owned_browser: bool = False,
) -> Path:
    """Scrape a single page and write to markdown."""
    should_close = False
    pw = None

    if browser is None:
        pw = await async_playwright().start()
        browser = await pw.chromium.launch(headless=True)
        should_close = True

    try:
        async with get_semaphore():
            page = await create_authenticated_page(browser, token)
            data = await scrape_page(page, url)
            await page.context.close()

        filename = sanitize_filename(data["title"]) + ".md"
        output_dir.mkdir(parents=True, exist_ok=True)
        filepath = output_dir / filename

        content = ""
        if obsidian:
            content += make_frontmatter(data["title"], url) + "\n\n"
        content += data["text"]

        filepath.write_text(content, encoding="utf-8")
        print(f"  Saved: {filepath}")
        return filepath

    finally:
        if should_close:
            await browser.close()
            if pw:
                await pw.stop()


async def scrape_recursive(
    url: str,
    output_dir: Path,
    token: str,
    obsidian: bool = False,
    max_depth: int = 5,
) -> list[Path]:
    """Scrape a page and all its sub-pages recursively."""
    visited: set[str] = set()
    results: list[Path] = []

    pw = await async_playwright().start()
    browser = await pw.chromium.launch(headless=True)

    try:
        async def _crawl(page_url: str, depth: int, parent_dir: Path) -> None:
            if page_url in visited or depth > max_depth:
                return
            visited.add(page_url)

            async with get_semaphore():
                page = await create_authenticated_page(browser, token)
                data = await scrape_page(page, page_url)
                await page.context.close()

            safe_title = sanitize_filename(data["title"])
            filename = safe_title + ".md"
            parent_dir.mkdir(parents=True, exist_ok=True)
            filepath = parent_dir / filename

            content = ""
            if obsidian:
                content += make_frontmatter(data["title"], page_url) + "\n\n"
            content += data["text"]

            filepath.write_text(content, encoding="utf-8")
            print(f"  [{len(results) + 1}] {filepath}")
            results.append(filepath)

            # Recurse into sub-pages
            if data["sub_links"]:
                sub_dir = parent_dir / safe_title if depth > 0 else parent_dir
                tasks = []
                for link in data["sub_links"]:
                    if link["url"] not in visited:
                        tasks.append(_crawl(link["url"], depth + 1, sub_dir))

                # Process in batches to respect concurrency
                for i in range(0, len(tasks), MAX_CONCURRENT):
                    batch = tasks[i : i + MAX_CONCURRENT]
                    await asyncio.gather(*batch)

        await _crawl(url, 0, output_dir)

    finally:
        await browser.close()
        await pw.stop()

    return results


async def async_main(args: argparse.Namespace) -> None:
    # Resolve token
    token = args.token
    if not token:
        print("Extracting token from FlowUS desktop app...")
        leveldb_path = Path(args.leveldb) if args.leveldb else None
        token = get_best_token(leveldb_path)
        if not token:
            print("ERROR: No valid FlowUS token found. Pass --token or open the FlowUS desktop app first.")
            sys.exit(1)
        print("  Token found.")

    output_dir = Path(args.output)

    if args.recursive:
        print(f"Scraping recursively: {args.url}")
        files = await scrape_recursive(args.url, output_dir, token, obsidian=args.obsidian)
        print(f"\nDone. Scraped {len(files)} page(s) to {output_dir}")
    else:
        print(f"Scraping: {args.url}")
        filepath = await scrape_single(args.url, output_dir, token, obsidian=args.obsidian)
        print(f"\nDone. Saved to {filepath}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Scrape FlowUS pages to markdown files.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--url", required=True, help="FlowUS page URL to scrape")
    parser.add_argument("--output", default="./output", help="Output directory (default: ./output)")
    parser.add_argument("--token", help="FlowUS JWT token (auto-extracted from desktop app if not provided)")
    parser.add_argument("--leveldb", help="Custom path to FlowUS LevelDB directory")
    parser.add_argument("--recursive", action="store_true", help="Also scrape all sub-pages")
    parser.add_argument("--obsidian", action="store_true", help="Add YAML frontmatter for Obsidian")

    args = parser.parse_args()
    asyncio.run(async_main(args))


if __name__ == "__main__":
    main()
