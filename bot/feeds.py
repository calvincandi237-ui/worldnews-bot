RSS_FEEDS = {
    "world": [
        {"name": "BBC World", "url": "http://feeds.bbci.co.uk/news/world/rss.xml"},
        {"name": "Reuters World", "url": "https://feeds.reuters.com/Reuters/worldNews"},
        {"name": "AP World", "url": "https://rsshub.app/apnews/topics/world-news"},
    ],
    "tech": [
        {"name": "TechCrunch", "url": "https://techcrunch.com/feed/"},
        {"name": "The Verge", "url": "https://www.theverge.com/rss/index.xml"},
        {"name": "Ars Technica", "url": "http://feeds.arstechnica.com/arstechnica/index"},
    ],
    "business": [
        {"name": "Reuters Business", "url": "https://feeds.reuters.com/reuters/businessNews"},
        {"name": "BBC Business", "url": "http://feeds.bbci.co.uk/news/business/rss.xml"},
        {"name": "CNBC", "url": "https://www.cnbc.com/id/10001147/device/rss/rss.html"},
    ],
    "science": [
        {"name": "Science Daily", "url": "https://www.sciencedaily.com/rss/all.xml"},
        {"name": "NASA", "url": "https://www.nasa.gov/rss/dyn/breaking_news.rss"},
        {"name": "BBC Science", "url": "http://feeds.bbci.co.uk/news/science_and_environment/rss.xml"},
    ],
    "sports": [
        {"name": "BBC Sport", "url": "http://feeds.bbci.co.uk/sport/rss.xml"},
        {"name": "ESPN", "url": "https://www.espn.com/espn/rss/news"},
        {"name": "Sky Sports", "url": "https://www.skysports.com/rss/12040"},
    ],
    "health": [
        {"name": "BBC Health", "url": "http://feeds.bbci.co.uk/news/health/rss.xml"},
        {"name": "WHO News", "url": "https://www.who.int/rss-feeds/news-english.xml"},
        {"name": "Medical News Today", "url": "https://www.medicalnewstoday.com/rss"},
    ],
    "entertainment": [
        {"name": "BBC Entertainment", "url": "http://feeds.bbci.co.uk/news/entertainment_and_arts/rss.xml"},
        {"name": "Variety", "url": "https://variety.com/feed/"},
        {"name": "Hollywood Reporter", "url": "https://www.hollywoodreporter.com/feed/"},
    ],
    "general": [
        {"name": "BBC Top Stories", "url": "http://feeds.bbci.co.uk/news/rss.xml"},
        {"name": "Reuters Top News", "url": "https://feeds.reuters.com/reuters/topNews"},
        {"name": "Google News", "url": "https://news.google.com/rss"},
    ],
}

CATEGORY_LABELS = {
    "world": "🌍 World",
    "tech": "💻 Technology",
    "business": "📈 Business",
    "science": "🔬 Science",
    "sports": "⚽ Sports",
    "health": "🏥 Health",
    "entertainment": "🎬 Entertainment",
    "general": "📰 General",
}

ALL_CATEGORIES = list(RSS_FEEDS.keys())
