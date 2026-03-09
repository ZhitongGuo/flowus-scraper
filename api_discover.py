#!/usr/bin/env python3
"""Discover FlowUS API structure by intercepting network requests.

Navigates to a FlowUS page with Playwright, intercepts all /api/ calls,
and saves large JSON responses to disk. Useful for understanding the
block structure of collections/databases before bulk scraping.

Usage:
    python api_discover.py --url "https://flowus.cn/<page-id>"
    python api_discover.py --url "https://flowus.cn/<page-id>" --token "eyJ..."
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from playwright.async_api import async_playwright

from token_extractor import get_best_token


async def async_main(args: argparse.Namespace) -> None:
    token = args.token
    if not token:
        print("Extracting token from FlowUS desktop app...")
        token = get_best_token()
        if not token:
            print("ERROR: No valid FlowUS token found. Pass --token.", file=sys.stderr)
            sys.exit(1)
        print("  Token found.")

    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    pw = await async_playwright().start()
    browser = await pw.chromium.launch(headless=True)
    context = await browser.new_context()
    page = await context.new_page()
    page.set_default_timeout(60000)

    # Authenticate
    await page.goto("https://flowus.cn/product", wait_until="commit")
    await page.wait_for_timeout(3000)
    await page.evaluate(f'localStorage.setItem("token", "{token}")')

    # Intercept API responses
    api_responses: list[dict] = []

    async def handle_response(response):
        url = response.url
        if "/api/" not in url:
            return
        try:
            body = await response.text()
            info = {
                "url": url,
                "status": response.status,
                "body_len": len(body),
            }
            api_responses.append(info)

            # Save large JSON responses
            if len(body) > 1000 and response.status == 200:
                try:
                    data = json.loads(body)
                    safe_name = url.split("/api/")[-1].replace("/", "_")[:50]
                    out_path = output_dir / f"flowus_api_{safe_name}.json"
                    out_path.write_text(
                        json.dumps(data, ensure_ascii=False, indent=2),
                        encoding="utf-8",
                    )
                    info["saved_to"] = str(out_path)
                except (json.JSONDecodeError, ValueError):
                    pass
        except Exception:
            api_responses.append({"url": url, "status": response.status, "error": "failed to read"})

    page.on("response", handle_response)

    print(f"Navigating to {args.url} ...")
    await page.goto(args.url, wait_until="commit")
    await page.wait_for_timeout(8000)

    # Click "Load more" buttons
    for _ in range(20):
        try:
            for text in ["Load more", "加载更多"]:
                btn = page.locator(f'text="{text}"').first
                if await btn.is_visible(timeout=500):
                    await btn.click()
                    await page.wait_for_timeout(1500)
        except Exception:
            break

    await page.wait_for_timeout(3000)

    print(f"\nCaptured {len(api_responses)} API calls:")
    for r in api_responses:
        status = r.get("status", "?")
        url_short = r["url"][:120]
        size = r.get("body_len", "?")
        saved = f" -> {r['saved_to']}" if r.get("saved_to") else ""
        print(f"  [{status}] {url_short} ({size} bytes){saved}")

    # Save summary
    summary_path = output_dir / "api_calls_summary.json"
    summary_path.write_text(
        json.dumps(api_responses, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"\nSummary saved to {summary_path}")

    await context.close()
    await browser.close()
    await pw.stop()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Discover FlowUS API structure by intercepting network requests.",
    )
    parser.add_argument("--url", required=True, help="FlowUS page URL to analyze")
    parser.add_argument("--output", default="./api_data", help="Output directory for JSON files")
    parser.add_argument("--token", help="FlowUS JWT token (auto-extracted if not provided)")

    args = parser.parse_args()
    asyncio.run(async_main(args))


if __name__ == "__main__":
    main()
