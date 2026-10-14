"""Web search and page fetching."""
from __future__ import annotations

import re
import time
import urllib.parse
import webbrowser

import httpx
from bs4 import BeautifulSoup

from ..config import Config
from .registry import Registry, integer, schema, string, truncate

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
STRIP_TAGS = ("script", "style", "noscript", "svg", "nav", "footer", "header", "form")

# DuckDuckGo throttles bursts by returning a 200 with no results, so try the
# full HTML endpoint first and fall back to the lighter one.
SEARCH_ENDPOINTS = (
    "https://html.duckduckgo.com/html/",
    "https://lite.duckduckgo.com/lite/",
)


def _decoded(response: httpx.Response) -> str:
    """DuckDuckGo omits a charset header on some responses; assume UTF-8."""
    if not response.charset_encoding:
        response.encoding = "utf-8"
    return response.text


def _unwrap(href: str) -> str:
    """DuckDuckGo wraps outbound links in a redirector."""
    if "uddg=" not in href:
        return href
    query = urllib.parse.urlparse(href).query
    return urllib.parse.parse_qs(query).get("uddg", [href])[0]


def register(registry: Registry, cfg: Config) -> None:
    @registry.define(
        "web_search",
        description=(
            "Search the web and return result titles, URLs and snippets. Use this to "
            "find current information, documentation, prices, or pages to fetch."
        ),
        parameters=schema(
            {
                "query": string("Search query."),
                "max_results": integer("How many results to return.", default=8),
            },
            ["query"],
        ),
    )
    def web_search(query: str, max_results: int = 8) -> str:
        errors: list[str] = []
        # Two passes over each endpoint: a throttled reply looks like an empty
        # page, and a short pause is usually enough to clear it.
        for attempt in range(2):
            for endpoint in SEARCH_ENDPOINTS:
                try:
                    response = httpx.post(
                        endpoint,
                        data={"q": query},
                        headers={"User-Agent": UA},
                        timeout=30.0,
                        follow_redirects=True,
                    )
                    response.raise_for_status()
                except httpx.HTTPError as exc:
                    errors.append(f"{endpoint}: {exc}")
                    continue

                results = _parse_results(_decoded(response), max_results)
                if results:
                    return truncate("\n".join(results))
                errors.append(f"{endpoint}: no results in response")
            if attempt == 0:
                time.sleep(1.5)

        detail = "; ".join(errors[-2:])
        return (
            f"No results for '{query}'. The search backend returned nothing, which "
            f"usually means it is throttling automated requests. Try rephrasing, or "
            f"fetch a likely URL directly with fetch_url. ({detail})"
        )

    def _parse_results(html: str, max_results: int) -> list[str]:
        soup = BeautifulSoup(html, "html.parser")
        results: list[str] = []

        # Layout used by html.duckduckgo.com
        for node in soup.select("div.result"):
            link = node.select_one("a.result__a")
            if not link:
                continue
            snippet_node = node.select_one(".result__snippet")
            snippet = snippet_node.get_text(" ", strip=True) if snippet_node else ""
            results.append(
                f"{len(results) + 1}. {link.get_text(' ', strip=True)}\n"
                f"   {_unwrap(str(link.get('href', '')))}\n   {snippet[:300]}"
            )
            if len(results) >= max_results:
                return results

        if results:
            return results

        # Layout used by lite.duckduckgo.com
        for link in soup.select("a.result-link"):
            results.append(
                f"{len(results) + 1}. {link.get_text(' ', strip=True)}\n"
                f"   {_unwrap(str(link.get('href', '')))}"
            )
            if len(results) >= max_results:
                break
        return results

    @registry.define(
        "fetch_url",
        description=(
            "Download a web page or text/JSON endpoint and return its readable text "
            "content with HTML stripped. Use after web_search to read a specific page."
        ),
        parameters=schema(
            {
                "url": string("Full URL including https://"),
                "max_chars": integer("Cap on returned characters.", default=8000),
            },
            ["url"],
        ),
    )
    def fetch_url(url: str, max_chars: int = 8000) -> str:
        if not url.lower().startswith(("http://", "https://")):
            url = "https://" + url
        try:
            response = httpx.get(
                url,
                headers={"User-Agent": UA},
                timeout=40.0,
                follow_redirects=True,
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            return f"ERROR: could not fetch {url}: {exc}"

        content_type = response.headers.get("content-type", "")
        body = _decoded(response)
        if "html" not in content_type:
            return truncate(f"{url}  [{content_type}]\n{body}", max_chars)

        soup = BeautifulSoup(body, "html.parser")
        for tag in soup(list(STRIP_TAGS)):
            tag.decompose()
        main = soup.find("article") or soup.find("main") or soup.body or soup
        text = main.get_text("\n", strip=True)
        text = re.sub(r"\n{3,}", "\n\n", text)
        title = soup.title.get_text(strip=True) if soup.title else url
        return truncate(f"{title}\n{url}\n\n{text}", max_chars)

    @registry.define(
        "open_url",
        description=(
            "Open a web page in the owner's browser so they can see it. Use this when "
            "they ask you to open, show, play or search something on a site; a YouTube "
            "search, for example, is https://www.youtube.com/results?search_query=... "
            "Spaces and accents in the link are fine; they are encoded for you."
        ),
        parameters=schema({"url": string("Full URL including https://")}, ["url"]),
        dangerous=True,
        preview=lambda a: f"Open in your browser:\n{a.get('url', '')}",
    )
    def open_url(url: str) -> str:
        if "://" not in url:
            url = "https://" + url
        # Only web pages: a file:// or app link could run something on this Mac.
        if not url.lower().startswith(("http://", "https://")):
            return f"ERROR: open_url only opens web pages (http or https), not {url}"
        # Encode spaces and non-ASCII; keep URL syntax and existing %-escapes.
        url = urllib.parse.quote(url, safe=":/?#[]@!$&'()*+,;=%")
        if not webbrowser.open(url):
            return f"ERROR: could not open a browser for {url}"
        return f"Opened {url} in the browser."
