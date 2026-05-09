import feedparser
import re
from typing import List, Dict, Optional
from datetime import datetime

MAX_ARTICLES = 5
REQUEST_TIMEOUT = 10


def clean_html(text: str) -> str:
    clean = re.sub(r"<[^>]+>", "", text or "")
    clean = re.sub(r"\s+", " ", clean).strip()
    return clean


def fetch_feed(url: str, limit: int = MAX_ARTICLES) -> List[Dict]:
    try:
        feed = feedparser.parse(url, request_headers={"User-Agent": "TelegramNewsBot/1.0"})
        articles = []
        for entry in feed.entries[:limit]:
            title = clean_html(entry.get("title", "No title"))
            summary = clean_html(entry.get("summary", entry.get("description", "")))
            link = entry.get("link", "")
            pub_date = ""
            if entry.get("published_parsed"):
                try:
                    dt = datetime(*entry.published_parsed[:6])
                    pub_date = dt.strftime("%b %d, %Y %H:%M UTC")
                except Exception:
                    pass
            articles.append({
                "title": title,
                "summary": summary[:300] + "..." if len(summary) > 300 else summary,
                "link": link,
                "pub_date": pub_date,
                "source": feed.feed.get("title", "Unknown"),
            })
        return articles
    except Exception as e:
        return []


def fetch_category(feeds: List[Dict], limit_per_feed: int = 3) -> List[Dict]:
    all_articles = []
    for feed_info in feeds:
        articles = fetch_feed(feed_info["url"], limit=limit_per_feed)
        for a in articles:
            a["feed_name"] = feed_info["name"]
        all_articles.extend(articles)
    return all_articles


def search_articles(feeds_map: Dict, query: str, max_results: int = 10) -> List[Dict]:
    query_lower = query.lower()
    results = []
    for category, feeds in feeds_map.items():
        for feed_info in feeds:
            articles = fetch_feed(feed_info["url"], limit=10)
            for a in articles:
                if query_lower in a["title"].lower() or query_lower in a["summary"].lower():
                    a["category"] = category
                    a["feed_name"] = feed_info["name"]
                    results.append(a)
                    if len(results) >= max_results:
                        return results
    return results


def format_article(article: Dict, index: Optional[int] = None) -> str:
    prefix = f"{index}. " if index is not None else ""
    lines = [f"{prefix}<b>{article['title']}</b>"]
    if article.get("pub_date"):
        lines.append(f"<i>🕐 {article['pub_date']}</i>")
    if article.get("summary"):
        lines.append(article["summary"])
    if article.get("link"):
        lines.append(f'<a href="{article["link"]}">Read more →</a>')
    return "\n".join(lines)
