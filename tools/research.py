import httpx
from bs4 import BeautifulSoup
from urllib.parse import quote_plus


HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 "
        "(X11; Linux x86_64) "
        "AppleWebKit/537.36 "
        "(KHTML, like Gecko) "
        "Chrome/120.0 Safari/537.36 "
        "CompanyResearchAgent/0.2"
    )
}


async def search_web(
    query: str,
    max_results: int = 5,
) -> list[dict]:
    """
    Lightweight DuckDuckGo HTML search.

    Returns:
    [
        {
            "title": "...",
            "url": "...",
            "snippet": "..."
        }
    ]

    Search failure is non-fatal.
    """
    url = (
        "https://html.duckduckgo.com/html/"
        f"?q={quote_plus(query)}"
    )

    try:
        async with httpx.AsyncClient(
            headers=HEADERS,
            follow_redirects=True,
            timeout=20,
        ) as client:
            response = await client.get(url)

        if response.status_code >= 400:
            return [
                {
                    "title": "",
                    "url": "",
                    "snippet": "",
                    "error": (
                        f"Search failed with HTTP "
                        f"{response.status_code}"
                    ),
                }
            ]

        soup = BeautifulSoup(
            response.text,
            "html.parser",
        )

        results = []

        for item in soup.select(".result"):
            title_el = item.select_one(
                ".result__a"
            )

            snippet_el = item.select_one(
                ".result__snippet"
            )

            if not title_el:
                continue

            result_url = title_el.get("href")

            if not result_url:
                continue

            results.append(
                {
                    "title": title_el.get_text(
                        " ",
                        strip=True,
                    ),
                    "url": result_url,
                    "snippet": (
                        snippet_el.get_text(
                            " ",
                            strip=True,
                        )
                        if snippet_el
                        else ""
                    ),
                }
            )

            if len(results) >= max_results:
                break

        return results

    except httpx.RequestError as exc:
        return [
            {
                "title": "",
                "url": "",
                "snippet": "",
                "error": (
                    f"Search request failed: "
                    f"{type(exc).__name__}: {exc}"
                ),
            }
        ]


async def fetch_page(
    url: str,
    max_chars: int = 12000,
) -> dict:
    """
    Fetch and extract readable text from an HTML page.

    A failed source MUST NOT crash the Research Agent.

    Returns:
    {
        "ok": bool,
        "url": str,
        "status_code": int | None,
        "title": str,
        "content_type": str,
        "text": str,
        "error": str | None
    }
    """

    try:
        async with httpx.AsyncClient(
            headers=HEADERS,
            follow_redirects=True,
            timeout=30,
        ) as client:
            response = await client.get(url)

    except httpx.TimeoutException:
        return {
            "ok": False,
            "url": url,
            "status_code": None,
            "title": "",
            "content_type": "",
            "text": "",
            "error": "Request timed out",
        }

    except httpx.RequestError as exc:
        return {
            "ok": False,
            "url": url,
            "status_code": None,
            "title": "",
            "content_type": "",
            "text": "",
            "error": (
                f"Request failed: "
                f"{type(exc).__name__}: {exc}"
            ),
        }

    final_url = str(response.url)

    content_type = (
        response.headers
        .get("content-type", "")
        .lower()
    )

    if response.status_code >= 400:
        return {
            "ok": False,
            "url": final_url,
            "status_code": response.status_code,
            "title": "",
            "content_type": content_type,
            "text": "",
            "error": (
                f"HTTP {response.status_code}"
            ),
        }

    # PDF handling will be added later.
    # For now we fail safely instead of trying
    # to parse binary PDF content as HTML.
    if (
        "application/pdf" in content_type
        or final_url.lower().endswith(".pdf")
    ):
        return {
            "ok": False,
            "url": final_url,
            "status_code": response.status_code,
            "title": "",
            "content_type": content_type,
            "text": "",
            "error": (
                "PDF source detected. "
                "PDF reading is not supported "
                "by the current research reader."
            ),
        }

    # Avoid treating arbitrary binary content
    # as an HTML/text document.
    supported_content = (
        "text/html" in content_type
        or "text/plain" in content_type
        or "application/xhtml+xml"
        in content_type
        or content_type == ""
    )

    if not supported_content:
        return {
            "ok": False,
            "url": final_url,
            "status_code": response.status_code,
            "title": "",
            "content_type": content_type,
            "text": "",
            "error": (
                "Unsupported content type: "
                f"{content_type}"
            ),
        }

    try:
        soup = BeautifulSoup(
            response.text,
            "html.parser",
        )

        for element in soup(
            [
                "script",
                "style",
                "nav",
                "footer",
                "header",
                "aside",
                "noscript",
                "svg",
            ]
        ):
            element.decompose()

        title = ""

        if soup.title and soup.title.string:
            title = soup.title.string.strip()

        text = "\n".join(
            line.strip()
            for line in soup.get_text(
                "\n"
            ).splitlines()
            if line.strip()
        )

        if not text:
            return {
                "ok": False,
                "url": final_url,
                "status_code": response.status_code,
                "title": title,
                "content_type": content_type,
                "text": "",
                "error": (
                    "Page was fetched but "
                    "no readable text was extracted."
                ),
            }

        return {
            "ok": True,
            "url": final_url,
            "status_code": response.status_code,
            "title": title,
            "content_type": content_type,
            "text": text[:max_chars],
            "error": None,
        }

    except Exception as exc:
        return {
            "ok": False,
            "url": final_url,
            "status_code": response.status_code,
            "title": "",
            "content_type": content_type,
            "text": "",
            "error": (
                "Page parsing failed: "
                f"{type(exc).__name__}: {exc}"
            ),
        }
