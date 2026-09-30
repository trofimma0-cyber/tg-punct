"""Модуль поиска фактов и информации в интернете (DuckDuckGo & Wikipedia).
Работает без платных API ключей, быстро и надёжно.
"""
import html
import logging
import urllib.parse
import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

SEARCH_PREFIXES = (
    "!инфо", "!поиск", "!гугл", "!факт", "!вики",
    "!info", "!google", "!fact", "!wiki"
)


def extract_search_query(text: str) -> tuple[bool, str]:
    """Проверяет, начинается ли текст с поискового префикса.
    Возвращает (is_search, clean_query)."""
    if not text:
        return False, ""
    t = text.strip()
    for prefix in SEARCH_PREFIXES:
        if t.lower().startswith(prefix):
            query = t[len(prefix):].strip()
            return True, query
    return False, ""


_client = None


def _get_client() -> httpx.Client:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.Client(
            timeout=7.0,
            follow_redirects=True,
            limits=httpx.Limits(max_keepalive_connections=5, max_connections=10),
        )
    return _client


def search_duckduckgo(query: str, max_results: int = 1) -> list[dict]:
    url = "https://html.duckduckgo.com/html/"
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        )
    }
    data = {"q": query}
    try:
        client = _get_client()
        resp = client.post(url, data=data, headers=headers)
        if resp.status_code != 200:
            return []
        soup = BeautifulSoup(resp.text, "html.parser")
        snippets = soup.select(".result__snippet")
        titles = soup.select(".result__title")
        urls = soup.select(".result__url")

        results = []
        for i in range(min(len(snippets), max_results)):
            t = titles[i].get_text(strip=True) if i < len(titles) else ""
            s = snippets[i].get_text(strip=True) if i < len(snippets) else ""
            u = urls[i].get_text(strip=True) if i < len(urls) else ""
            if s:
                results.append({"title": t, "snippet": s, "url": u})
        return results
    except Exception as e:
        logger.warning("DuckDuckGo search error: %s", e)
        return []


def search_wikipedia_summary(query: str) -> dict | None:
    url = (
        "https://ru.wikipedia.org/w/api.php?action=query&list=search"
        f"&srsearch={urllib.parse.quote(query)}&format=json&utf8="
    )
    headers = {"User-Agent": "TGPunctBot/1.0 (contact: admin@tgpunct.local)"}
    try:
        client = _get_client()
        resp = client.get(url, headers=headers)
        if resp.status_code == 200:
            data = resp.json()
            items = data.get("query", {}).get("search", [])
            if items:
                title = items[0]["title"]
                extract_url = (
                    "https://ru.wikipedia.org/w/api.php?action=query&prop=extracts"
                    f"&exintro=1&explaintext=1&titles={urllib.parse.quote(title)}&format=json&utf8="
                )
                ex_resp = client.get(extract_url, headers=headers)
                if ex_resp.status_code == 200:
                    pages = ex_resp.json().get("query", {}).get("pages", {})
                    for pid, pdata in pages.items():
                        extract = pdata.get("extract", "")
                        if extract and len(extract) > 40:
                            return {
                                "title": title,
                                "text": extract[:450],
                                "url": f"ru.wikipedia.org/wiki/{urllib.parse.quote(title)}"
                            }
    except Exception as e:
        logger.warning("Wikipedia search error: %s", e)
    return None


def get_fact_response(query: str) -> str:
    """Выполняет поиск фактов и возвращает красиво оформленный Telegram HTML-текст."""
    query = query.strip()
    if not query:
        return "⚠️ <b>Поисковый запрос пуст.</b>\nПример: <code>!инфо статья 159 УК РФ</code>"

    esc_query = html.escape(query)
    # Сначала пробуем DuckDuckGo (быстрый и релевантный ответ на вопросы и статьи)
    ddg = search_duckduckgo(query)
    if ddg and ddg[0].get("snippet"):
        res = ddg[0]
        title = html.escape(res.get("title") or query)
        snippet = html.escape(res.get("snippet") or "")
        url_val = html.escape(res.get("url") or "")
        url_line = f"\n\n🔗 <i>Источник: {url_val}</i>" if url_val else ""
        return (
            f"🌐 <b>Поиск:</b> <i>«{esc_query}»</i>\n\n"
            f"💡 <b>{title}:</b>\n"
            f"{snippet}"
            f"{url_line}"
        )

    # Резервный поиск через Wikipedia
    wiki = search_wikipedia_summary(query)
    if wiki:
        title = html.escape(wiki.get("title") or query)
        text_val = html.escape(wiki.get("text") or "")
        url_val = html.escape(wiki.get("url") or "")
        return (
            f"🌐 <b>Поиск:</b> <i>«{esc_query}»</i>\n\n"
            f"💡 <b>{title}:</b>\n"
            f"{text_val}...\n\n"
            f"🔗 <i>Источник: {url_val}</i>"
        )

    return (
        f"🌐 <b>Поиск:</b> <i>«{esc_query}»</i>\n\n"
        "😕 По данному запросу не удалось найти краткий ответ. Попробуйте уточнить формулировку."
    )
