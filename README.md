# FlowUS Scraper

Scrape pages from [FlowUS](https://flowus.cn) to markdown files, suitable for Obsidian vaults.

FlowUS is a Chinese Notion-like platform that renders entirely client-side (SPA), so scraping requires browser automation via Playwright.

## Tools

| Script | Purpose |
|--------|---------|
| `scraper.py` | Scrape individual pages or recursively scrape page trees |
| `problem_scraper.py` | Bulk-scrape records from FlowUS collection (database) views via API |
| `cleanup.py` | Clean up scraped markdown: keep only Python code, fix formatting |
| `api_discover.py` | Discover FlowUS API structure by intercepting network requests |
| `token_extractor.py` | Extract JWT auth tokens from the FlowUS desktop app |

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

### Scrape recursively (page + all sub-pages)

```bash
python scraper.py --url "https://flowus.cn/<page-id>" --output ./output --recursive --obsidian
```

### Bulk-scrape a collection database

FlowUS databases (collections) store hundreds of records that can each be expanded into full pages. The `problem_scraper.py` uses the FlowUS JSON API directly, which is ~50x faster than rendering each page in a browser.

```bash
# Step 1: Discover the API structure and save the collection JSON
python api_discover.py --url "https://flowus.cn/<collection-page-id>" --output ./api_data

# Step 2: Bulk-scrape all records from the collection
python problem_scraper.py --collection-data ./api_data/flowus_api_docs_<id>.json --output ./problems
```

### Clean up scraped markdown

After scraping, clean up formatting: keep only Python code blocks, remove JS/Java/Go/C++, strip stray language labels.

```bash
python cleanup.py ./problems
python cleanup.py ./problems --dry-run  # preview without writing
```

### Extract token manually

```bash
python token_extractor.py
```

## Token Discovery

The scraper automatically finds your FlowUS JWT token from the desktop app's local storage at:

```
~/Library/Application Support/FlowUs/Partitions/main/Local Storage/leveldb/
```

You can also pass `--token` directly if you have it (e.g., from browser DevTools > Application > Local Storage > `token`).

## How It Works

### Page scraping (`scraper.py`)
1. Extracts JWT token from FlowUS desktop app's LevelDB
2. Injects token into headless Chromium via Playwright
3. Navigates to each page, waits for SPA rendering, extracts text
4. Writes markdown with optional YAML frontmatter

### Collection scraping (`problem_scraper.py`)
1. Reads a pre-fetched collection JSON (from `/api/docs/{page_id}`)
2. Parses the collection schema to extract metadata (level, difficulty, tags, etc.)
3. Fetches each record via `/api/docs/{record_id}` using authenticated browser context
4. Renders the FlowUS block tree to markdown (handles 15+ block types)
5. Writes files with YAML frontmatter including all metadata

### Block types
FlowUS uses a block-based document model. Key block types:

| Type | Name | Rendering |
|------|------|-----------|
| 0, 1 | Text / Paragraph | Plain text or heading (based on `level`) |
| 3 | Bulleted list | `- item` |
| 4 | Numbered list | `- item` (markdown) |
| 5 | Quote | `> text` |
| 6 | Code block | `` ```lang ... ``` `` |
| 7 | Heading / Divider | `## text` or `---` |
| 8 | Toggle | `**text**` |
| 18 | Collection (database) | Skipped (structural) |
| 22 | Image | `![alt](url)` |
| 25 | Callout / Example | Code block |
| 27 | Tab group | `### Code Solutions` |
| 28 | Synced / Embedded | Section header from properties |

## Notes

- Rate limited to 3-5 concurrent requests to be respectful to FlowUS servers
- Pages with "Load more" buttons are automatically expanded
- Collection scraping via API is ~50x faster than page-by-page browser rendering
- JWT tokens expire after ~30 days; re-open the FlowUS desktop app to refresh
