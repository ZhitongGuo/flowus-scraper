# FlowUS Scraper

Scrape pages from [FlowUS](https://flowus.cn) to markdown files, suitable for Obsidian vaults.

FlowUS is a Chinese Notion-like platform that renders entirely client-side (SPA), so scraping requires browser automation via Playwright.

## How It Works

1. Extracts your JWT auth token from the FlowUS desktop app's local storage (LevelDB)
2. Injects the token into a headless Chromium browser via Playwright
3. Navigates to each page, waits for SPA rendering, extracts text
4. Writes clean markdown with YAML frontmatter

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
```

## Usage

### Scrape a single page

```bash
python scraper.py --url "https://flowus.cn/<page-id>" --output ./output
```

### Scrape a page and all its sub-pages (recursive)

```bash
python scraper.py --url "https://flowus.cn/<page-id>" --output ./output --recursive
```

### Scrape with a custom token

```bash
python scraper.py --url "https://flowus.cn/<page-id>" --token "eyJ..." --output ./output
```

### Generate Obsidian vault structure

```bash
python scraper.py --url "https://flowus.cn/<page-id>" --output ./vault --obsidian --recursive
```

## Token Discovery

The scraper automatically finds your FlowUS JWT token from the desktop app's local storage at:

```
~/Library/Application Support/FlowUs/Partitions/main/Local Storage/leveldb/
```

You can also pass `--token` directly if you have it.

## Notes

- Rate limited to 3 concurrent pages to be respectful to FlowUS servers
- Pages with "Load more" buttons are automatically expanded
- Sidebar navigation is stripped from output
- Content is plain text (FlowUS renders rich content client-side, so complex formatting may be lost)
