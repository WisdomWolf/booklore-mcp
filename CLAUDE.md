# booklore-mcp: Chapter Text Extraction Extension

## Context

This is a fork of an existing MCP server (`willianpaixao/booklore-mcp`) that wraps a
self-hosted BookLore instance. The upstream server already handles library search,
metadata, shelves, and reading progress. It does **not** expose book content — no
tool currently returns actual chapter/page text.

**Your first task, before writing any code:** inspect the existing repo structure and
identify the language/framework (Python + FastMCP, or Node/TypeScript — unconfirmed
at fork time), the existing HTTP client/auth pattern, and how tools are currently
registered. Match the new tool to existing conventions rather than introducing a new
pattern.

## Goal

Add one new MCP tool, `get_chapter_text`, that downloads a book's file from BookLore,
extracts the text of one or more chapters, and returns clean text — so a parent AI
session can quiz a kid on specific chapters without manually uploading files.

## Tool spec

**Name:** `get_chapter_text`

**Input:**
- `book_id: str` — required
- `chapter: int | [int, int]` — single chapter number, or `[start, end]` inclusive range

**Output:**
```
{
  "title": str,
  "chapters": [
    { "number": int, "heading": str, "text": str }
  ],
  "warnings": [str]   // e.g. "chapter mapping uncertain", "PDF fallback used"
}
```

## Implementation steps

1. **Reuse existing auth/HTTP client** from the fork — don't build a second client.
   Call BookLore's file-download endpoint for `book_id`, save to a temp path.
2. **EPUB parsing (primary path):**
   - Use `ebooklib` to iterate the spine in order
   - Strip HTML per spine item with `BeautifulSoup` (or equivalent in the fork's
     language)
   - Build a chapter map: a spine item counts as a chapter start if its text opens
     with a heading matching `Chapter \d+`, `CHAPTER \d+`, or a bare `^\d+$` line
   - Merge non-matching docs (title pages, copyright pages, "About the Author") into
     the adjacent chapter rather than counting them as their own chapter
   - **Cache the chapter map per `book_id`** (in-memory or on disk) so repeated calls
     don't re-parse the whole EPUB
3. **PDF fallback:** for PDF-only entries, use `pypdf` (or fork's existing PDF lib if
   any). Chapter boundaries from page numbers are unreliable — return a `warnings`
   entry rather than silently guessing.
4. **Reject audiobook entries** (multi-file M4B/M4A) with a clear error — text
   extraction doesn't apply.
5. **Clean up** the temp downloaded file after parsing. Don't persist raw book files
   to disk beyond the request.
6. Return only the requested chapter(s), trimmed of BookLore-added front/back matter.

## Testing

- Start with a book that has clean, conventional chapter headers (e.g. *Animorphs
  #1: The Invasion*) to validate the chapter map before touching messier entries
  (bind-ups, omnibus editions, graphic novel adaptations).
- Write a unit test that asserts chapter count and headings for at least one known
  book, so future BookLore API changes don't silently break extraction.
- Manually verify: request `chapter: [1,3]` on a multi-chapter book and confirm text
  boundaries don't bleed between chapters.

## Non-goals

- No new auth mechanism — reuse what's there.
- No UI/web frontend changes.
- No caching of full book text long-term — chapter *map* caching is fine, raw text
  should be fetched fresh or cached with a short TTL.

## Out of scope but worth flagging in the PR description

- BookLore's API is internal/unversioned and may change between releases — note the
  BookLore version this was tested against.
- If the upstream project has a CONTRIBUTING.md or test/lint commands, follow those
  conventions exactly (run existing test suite before opening a PR).