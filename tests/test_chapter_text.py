"""get_chapter_text: chapter-map building, request parsing, and end-to-end EPUB
extraction through the real MCP tool layer (respx-mocked BookLore API).
"""

from __future__ import annotations

import io

import httpx
import pytest
from ebooklib import epub
from fastmcp import Client

import server
from server import BookLoreError, ChapterMapEntry, _build_chapter_map, _parse_chapter_request

BASE = "http://booklore.test"


@pytest.fixture
def authed(respx_mock):
    respx_mock.post(f"{BASE}/api/v1/auth/login").mock(
        return_value=httpx.Response(200, json={"accessToken": "acc1", "refreshToken": "ref1"})
    )
    server.client = server.BookLoreClient(BASE, "tester", "secret")
    server._chapter_map_cache.clear()
    return respx_mock


def _make_epub_bytes() -> bytes:
    """A small conventional-header EPUB: title page, 3 numbered chapters, and an
    "About the Author" back-matter page — mirrors a clean bind-up like Animorphs #1."""
    book = epub.EpubBook()
    book.set_identifier("test-book-1")
    book.set_title("The Invasion")
    book.set_language("en")

    title_page = epub.EpubHtml(title="Title Page", file_name="title.xhtml", lang="en")
    title_page.content = "<html><body><p>The Invasion</p><p>Copyright 1996.</p></body></html>"

    chapters = []
    for n in range(1, 4):
        c = epub.EpubHtml(title=f"Chapter {n}", file_name=f"chap{n}.xhtml", lang="en")
        c.content = f"<html><body><p>Chapter {n}</p><p>Text of chapter {n}.</p></body></html>"
        chapters.append(c)

    about = epub.EpubHtml(title="About the Author", file_name="about.xhtml", lang="en")
    about.content = "<html><body><p>About the Author</p><p>K.A. Applegate.</p></body></html>"

    for item in [title_page, *chapters, about]:
        book.add_item(item)
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    book.spine = ["nav", title_page, *chapters, about]

    buf = io.BytesIO()
    epub.write_epub(buf, book)
    return buf.getvalue()


def _book_record(book_id: int, book_type: str, **primary_overrides) -> dict:
    primary = {"id": 100 + book_id, "bookType": book_type, "fileSizeKb": 123, **primary_overrides}
    return {
        "id": book_id,
        "title": "The Invasion",
        "metadata": {"title": "The Invasion"},
        "primaryFile": primary,
    }


# ---- pure helpers ------------------------------------------------------------


def test_build_chapter_map_merges_front_and_back_matter():
    segments = [
        ("title", "The Invasion\nCopyright 1996."),
        ("chap1", "Chapter 1\nText of chapter 1."),
        ("chap2", "Chapter 2\nText of chapter 2."),
        ("chap3", "Chapter 3\nText of chapter 3."),
        ("about", "About the Author\nK.A. Applegate."),
    ]
    chapters = _build_chapter_map(segments)
    assert [c.number for c in chapters] == [1, 2, 3]
    assert [c.heading for c in chapters] == ["Chapter 1", "Chapter 2", "Chapter 3"]
    assert chapters[0].segment_ids == ["title", "chap1"]
    assert chapters[1].segment_ids == ["chap2"]
    assert chapters[2].segment_ids == ["chap3", "about"]


def test_build_chapter_map_no_headings_collapses_to_one_chapter():
    segments = [("a", "Just some prose."), ("b", "More prose, no headings.")]
    chapters = _build_chapter_map(segments)
    assert chapters == [ChapterMapEntry(number=1, heading="", segment_ids=["a", "b"])]


def test_build_chapter_map_matches_bare_number_and_uppercase():
    segments = [("a", "1\nfirst"), ("b", "CHAPTER 2\nsecond")]
    chapters = _build_chapter_map(segments)
    assert [c.heading for c in chapters] == ["1", "CHAPTER 2"]


@pytest.mark.parametrize(
    "chapter,expected",
    [(1, [1]), (5, [5]), ([1, 3], [1, 2, 3]), ([2, 2], [2])],
)
def test_parse_chapter_request_valid(chapter, expected):
    assert _parse_chapter_request(chapter) == expected


@pytest.mark.parametrize("chapter", [0, [3, 1], [0, 2], "1", [1, 2, 3]])
def test_parse_chapter_request_invalid(chapter):
    with pytest.raises(BookLoreError):
        _parse_chapter_request(chapter)


# ---- end-to-end (EPUB) --------------------------------------------------------


async def test_get_chapter_text_epub_range_no_bleed(authed):
    epub_bytes = _make_epub_bytes()
    authed.get(f"{BASE}/api/v1/books/1").mock(
        return_value=httpx.Response(200, json=_book_record(1, "EPUB"))
    )
    authed.get(f"{BASE}/api/v1/books/1/download").mock(
        return_value=httpx.Response(
            200, content=epub_bytes, headers={"content-type": "application/octet-stream"}
        )
    )

    async with Client(server.mcp) as c:
        result = await c.call_tool("get_chapter_text", {"book_id": 1, "chapter": [1, 3]})

    data = result.data
    assert data["title"] == "The Invasion"
    assert [ch["number"] for ch in data["chapters"]] == [1, 2, 3]
    assert [ch["heading"] for ch in data["chapters"]] == ["Chapter 1", "Chapter 2", "Chapter 3"]
    assert data["warnings"] == []

    ch1, ch2, ch3 = data["chapters"]
    assert "Text of chapter 1." in ch1["text"]
    assert "The Invasion" in ch1["text"]  # merged title page
    assert "Text of chapter 2" not in ch1["text"]
    assert "Text of chapter 2." in ch2["text"]
    assert "Text of chapter 1" not in ch2["text"] and "Text of chapter 3" not in ch2["text"]
    assert "Text of chapter 3." in ch3["text"]
    assert "About the Author" in ch3["text"]  # merged back matter
    assert "Text of chapter 2" not in ch3["text"]


async def test_get_chapter_text_single_chapter(authed):
    epub_bytes = _make_epub_bytes()
    authed.get(f"{BASE}/api/v1/books/1").mock(
        return_value=httpx.Response(200, json=_book_record(1, "EPUB"))
    )
    authed.get(f"{BASE}/api/v1/books/1/download").mock(
        return_value=httpx.Response(200, content=epub_bytes)
    )

    async with Client(server.mcp) as c:
        result = await c.call_tool("get_chapter_text", {"book_id": 1, "chapter": 2})

    assert len(result.data["chapters"]) == 1
    assert result.data["chapters"][0]["number"] == 2
    assert result.data["chapters"][0]["heading"] == "Chapter 2"


async def test_get_chapter_text_out_of_range_raises(authed):
    epub_bytes = _make_epub_bytes()
    authed.get(f"{BASE}/api/v1/books/1").mock(
        return_value=httpx.Response(200, json=_book_record(1, "EPUB"))
    )
    authed.get(f"{BASE}/api/v1/books/1/download").mock(
        return_value=httpx.Response(200, content=epub_bytes)
    )

    async with Client(server.mcp) as c:
        with pytest.raises(Exception, match="out of range"):
            await c.call_tool("get_chapter_text", {"book_id": 1, "chapter": 99})


async def test_get_chapter_text_rejects_audiobook(authed):
    authed.get(f"{BASE}/api/v1/books/1").mock(
        return_value=httpx.Response(200, json=_book_record(1, "AUDIOBOOK", extension="mp3"))
    )

    async with Client(server.mcp) as c:
        with pytest.raises(Exception, match="audiobook"):
            await c.call_tool("get_chapter_text", {"book_id": 1, "chapter": 1})


async def test_get_chapter_text_no_primary_file_raises(authed):
    authed.get(f"{BASE}/api/v1/books/1").mock(
        return_value=httpx.Response(200, json={"id": 1, "title": "Physical Book"})
    )

    async with Client(server.mcp) as c:
        with pytest.raises(Exception, match="no downloadable file"):
            await c.call_tool("get_chapter_text", {"book_id": 1, "chapter": 1})


async def test_get_chapter_text_caches_chapter_map(authed, monkeypatch):
    epub_bytes = _make_epub_bytes()
    authed.get(f"{BASE}/api/v1/books/1").mock(
        return_value=httpx.Response(200, json=_book_record(1, "EPUB"))
    )
    download_route = authed.get(f"{BASE}/api/v1/books/1/download").mock(
        return_value=httpx.Response(200, content=epub_bytes)
    )

    calls = 0
    real_build = server._build_chapter_map

    def counting_build(segments):
        nonlocal calls
        calls += 1
        return real_build(segments)

    monkeypatch.setattr(server, "_build_chapter_map", counting_build)

    async with Client(server.mcp) as c:
        await c.call_tool("get_chapter_text", {"book_id": 1, "chapter": 1})
        await c.call_tool("get_chapter_text", {"book_id": 1, "chapter": 2})

    # The file is re-downloaded each call (no long-term raw-text caching)...
    assert download_route.call_count == 2
    # ...but the chapter map is only built once and reused on the second call.
    assert calls == 1
