import os
import json
import hashlib
import re
import time
import threading
import feedparser
import requests
from bs4 import BeautifulSoup
from google import genai as genai_client
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
from flask import Flask
from datetime import datetime
import pytz

# ── Config ───────────────────────────────────────────────────────────────────
TELEGRAM_TOKEN   = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHANNEL = os.environ["TELEGRAM_CHANNEL"]
GEMINI_API_KEY   = os.environ["GEMINI_API_KEY"]
OWNER_ID         = int(os.environ["OWNER_ID"])
TIMEZONE         = "Europe/Madrid"
MIN_SCORE        = 5

client = genai_client.Client(api_key=GEMINI_API_KEY)
MODEL  = "gemini-2.0-flash-lite"

SCHEDULE_HOURS = [8, 10, 13, 16, 19, 21]

RSS_FEEDS = [
    "https://feeds.bbci.co.uk/news/world/europe/rss.xml",
    "https://feeds.bbci.co.uk/news/world/us_and_canada/rss.xml",
    "https://rss.nytimes.com/services/xml/rss/nyt/World.xml",
    "https://news.google.com/rss/search?q=Ukraine+politics&hl=en&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=corruption+europe+usa&hl=en&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=artificial+intelligence+robotics&hl=en&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=celebrity+scandal+politics&hl=en&gl=US&ceid=US:en",
]

TOPIC_CONTEXT = (
    "Relevant topics: European/US/Canadian/Ukrainian politics, government corruption, "
    "celebrity and media figures, AI and neural networks, social media platforms, robotics."
)

HASHES_FILE    = "data/posted_hashes.json"
POST_LOG_FILE  = "data/post_log.json"
POST_LOG_MAX   = 50       # entries kept on disk
GEMINI_RETRIES = 3
GEMINI_RETRY_DELAY = 10

STOPWORDS = {"the", "a", "an", "in", "on", "at", "to", "for", "of", "and",
             "or", "but", "is", "was", "are", "were", "be", "been", "has",
             "have", "had", "it", "its", "that", "this", "with", "from"}


# ── State ─────────────────────────────────────────────────────────────────────
posted_url_hashes: set   = set()   # MD5 of URL
posted_title_hashes: set = set()   # MD5 of normalized title
is_paused: bool          = False
start_time: datetime     = datetime.utcnow()

# Daily counters (reset at midnight Spain time)
stats = {
    "seen_today":     0,
    "posted_today":   0,
    "rejected_today": 0,
    "gemini_errors":  0,
}

# Yesterday's snapshot (populated at midnight before reset)
yesterday_stats: dict = {}

# Post history log (last POST_LOG_MAX entries, persisted to disk)
post_log: list = []


# ── Persistent hash storage ───────────────────────────────────────────────────
def _hashes_path() -> str:
    os.makedirs(os.path.dirname(HASHES_FILE), exist_ok=True)
    return HASHES_FILE


def load_hashes() -> None:
    global posted_url_hashes, posted_title_hashes
    path = _hashes_path()
    if not os.path.exists(path):
        print("[STORAGE] No existing hash file — starting fresh.")
        return
    try:
        with open(path) as f:
            data = json.load(f)
        posted_url_hashes   = set(data.get("urls", []))
        posted_title_hashes = set(data.get("titles", []))
        print(f"[STORAGE] Loaded {len(posted_url_hashes)} URL hashes, "
              f"{len(posted_title_hashes)} title hashes.")
    except Exception as e:
        print(f"[STORAGE ERROR] Could not load hashes: {e}")


def save_hashes() -> None:
    try:
        with open(_hashes_path(), "w") as f:
            json.dump({
                "urls":   list(posted_url_hashes),
                "titles": list(posted_title_hashes),
            }, f)
    except Exception as e:
        print(f"[STORAGE ERROR] Could not save hashes: {e}")


# ── Post log persistence ─────────────────────────────────────────────────────
def load_post_log() -> None:
    global post_log
    path = POST_LOG_FILE
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if not os.path.exists(path):
        return
    try:
        with open(path) as f:
            post_log = json.load(f)
        print(f"[STORAGE] Loaded {len(post_log)} post log entries.")
    except Exception as e:
        print(f"[STORAGE ERROR] Could not load post log: {e}")


def save_post_log() -> None:
    try:
        with open(POST_LOG_FILE, "w") as f:
            json.dump(post_log[-POST_LOG_MAX:], f, indent=2)
    except Exception as e:
        print(f"[STORAGE ERROR] Could not save post log: {e}")


def append_post_log(article: dict) -> None:
    tz  = pytz.timezone(TIMEZONE)
    now = datetime.now(tz)
    post_log.append({
        "title": article["title"],
        "url":   article["link"],
        "ts":    now.strftime("%Y-%m-%d %H:%M"),
    })
    if len(post_log) > POST_LOG_MAX:
        del post_log[:-POST_LOG_MAX]
    save_post_log()


# ── Deduplication ─────────────────────────────────────────────────────────────
def normalize_title(title: str) -> str:
    title = title.lower()
    title = re.sub(r"[^\w\s]", "", title)
    words = [w for w in title.split() if w not in STOPWORDS]
    return " ".join(words)


def is_duplicate(url: str, title: str) -> bool:
    url_hash   = hashlib.md5(url.encode()).hexdigest()
    title_hash = hashlib.md5(normalize_title(title).encode()).hexdigest()
    return url_hash in posted_url_hashes or title_hash in posted_title_hashes


def mark_seen(url: str, title: str) -> None:
    posted_url_hashes.add(hashlib.md5(url.encode()).hexdigest())
    posted_title_hashes.add(hashlib.md5(normalize_title(title).encode()).hexdigest())
    save_hashes()


# ── Gemini with retry ─────────────────────────────────────────────────────────
def gemini_call(prompt: str, label: str) -> str | None:
    for attempt in range(1, GEMINI_RETRIES + 1):
        try:
            resp = client.models.generate_content(model=MODEL, contents=prompt)
            return resp.text.strip()
        except Exception as e:
            stats["gemini_errors"] += 1
            print(f"[{label}] Attempt {attempt}/{GEMINI_RETRIES} failed: {e}")
            if attempt < GEMINI_RETRIES:
                print(f"[{label}] Retrying in {GEMINI_RETRY_DELAY}s...")
                time.sleep(GEMINI_RETRY_DELAY)
    print(f"[{label}] All retries exhausted.")
    return None


# ── Helpers ───────────────────────────────────────────────────────────────────
def clean_html(text: str) -> str:
    return BeautifulSoup(text or "", "html.parser").get_text(" ", strip=True)


def get_og_image(url: str) -> str | None:
    try:
        r = requests.get(url, timeout=6, headers={"User-Agent": "Mozilla/5.0"})
        soup = BeautifulSoup(r.text, "html.parser")
        tag = soup.find("meta", property="og:image")
        return tag["content"] if tag else None
    except Exception:
        return None


def next_slot_time() -> str:
    tz  = pytz.timezone(TIMEZONE)
    now = datetime.now(tz)
    for h in SCHEDULE_HOURS:
        if h > now.hour or (h == now.hour and now.minute == 0):
            return f"{h:02d}:00"
    return f"{SCHEDULE_HOURS[0]:02d}:00 (tomorrow)"


# ── Fetch ─────────────────────────────────────────────────────────────────────
def fetch_news() -> list[dict]:
    articles = []
    for feed_url in RSS_FEEDS:
        try:
            feed = feedparser.parse(feed_url)
            new_count = 0
            for entry in feed.entries[:10]:
                title   = clean_html(entry.get("title", ""))
                summary = clean_html(entry.get("summary", entry.get("description", "")))
                link    = entry.get("link", "").strip()
                if not title or not link:
                    continue
                stats["seen_today"] += 1
                if is_duplicate(link, title):
                    continue
                articles.append({"title": title, "summary": summary[:400], "link": link})
                new_count += 1
            print(f"[FEED] {feed_url[:70]} → new={new_count}")
        except Exception as e:
            print(f"[FEED ERROR] {feed_url[:70]} → {e}")
    print(f"[FETCH] Total new articles: {len(articles)}")
    return articles


# ── Score ─────────────────────────────────────────────────────────────────────
def score_and_pick(articles: list[dict]) -> dict | None:
    if not articles:
        print("[SCORE] No articles to score.")
        return None

    titles = "\n".join(f"{i+1}. {a['title']}" for i, a in enumerate(articles))
    prompt = (
        f"{TOPIC_CONTEXT}\n\n"
        "Score each headline for relevance to those topics AND global importance (1-10).\n"
        "Return ONLY a JSON array, no markdown, no explanation:\n"
        '[{"index":1,"score":8}, ...]\n\n'
        f"Headlines:\n{titles}"
    )
    result = gemini_call(prompt, "SCORE")
    if result is None:
        return None
    try:
        text   = result.lstrip("```json").lstrip("```").rstrip("```").strip()
        print(f"[SCORE] Gemini: {text[:200]}")
        scores = sorted(json.loads(text), key=lambda x: x["score"], reverse=True)
        best   = scores[0]
        print(f"[SCORE] Best: index={best['index']}, score={best['score']}")
        if best["score"] < MIN_SCORE:
            stats["rejected_today"] += 1
            print(f"[SCORE] Score {best['score']} < {MIN_SCORE} — slot skipped.")
            return None
        return articles[best["index"] - 1]
    except Exception as e:
        print(f"[SCORE PARSE ERROR] {e}")
        return None


# ── Format ────────────────────────────────────────────────────────────────────
def format_post(article: dict) -> str | None:
    prompt = (
        "Write a Telegram channel post about this news article. Follow this structure exactly:\n\n"
        "1. HOOK — one punchy opening sentence that grabs attention. Start with a phrase like "
        "'Right now...', 'This could change...', 'A court just ruled...', 'Breaking:' etc.\n"
        "2. BACKGROUND — 2-3 sentences: what happened before this, who the key players are.\n"
        "3. CONFLICT — 1-2 sentences stating the core tension: what each side claims or wants.\n"
        "4. WHY IT MATTERS — exactly one sentence starting with '💡 Why it matters:'\n"
        "5. HASHTAGS — 3 to 5 relevant hashtags on their own line.\n"
        "6. URL — the source link on its own final line, no label.\n\n"
        "Style rules:\n"
        "- Tone: smart but simple — like explaining to a smart friend over coffee\n"
        "- No corporate jargon, no passive voice, no buzzwords\n"
        "- Under 220 words total\n"
        "- No bold, no markdown symbols, no bullet points\n"
        "- Do NOT include a headline or title at the top\n\n"
        f"Article title: {article['title']}\n"
        f"Article summary: {article['summary']}\n"
        f"URL: {article['link']}"
    )
    result = gemini_call(prompt, "FORMAT")
    if result:
        print(f"[FORMAT] Post ready for: {article['title'][:60]}")
    return result


# ── Send ──────────────────────────────────────────────────────────────────────
async def send_article(bot, article: dict) -> bool:
    text = format_post(article)
    if not text:
        return False
    image_url = get_og_image(article["link"])
    try:
        if image_url:
            await bot.send_photo(
                chat_id=TELEGRAM_CHANNEL,
                photo=image_url,
                caption=text,
            )
        else:
            await bot.send_message(
                chat_id=TELEGRAM_CHANNEL,
                text=text,
                disable_web_page_preview=False,
            )
        mark_seen(article["link"], article["title"])
        append_post_log(article)
        stats["posted_today"] += 1
        print(f"[POST] ✅ {article['title'][:70]}")
        return True
    except Exception as e:
        print(f"[POST ERROR] {e}")
        return False


# ── Daily stats reset ─────────────────────────────────────────────────────────
async def reset_daily_stats(context: ContextTypes.DEFAULT_TYPE) -> None:
    global yesterday_stats
    tz  = pytz.timezone(TIMEZONE)
    now = datetime.now(tz)
    # Snapshot today before wiping
    yesterday_stats = dict(stats)
    yesterday_stats["date"] = now.strftime("%A, %B %d")
    stats["seen_today"]     = 0
    stats["posted_today"]   = 0
    stats["rejected_today"] = 0
    stats["gemini_errors"]  = 0
    print(f"[STATS] Daily counters reset at {now.strftime('%H:%M')} Spain time.")


# ── Daily morning report ──────────────────────────────────────────────────────
async def send_morning_report(context: ContextTypes.DEFAULT_TYPE) -> None:
    tz  = pytz.timezone(TIMEZONE)
    now = datetime.now(tz)
    if not yesterday_stats:
        print("[REPORT] No yesterday stats yet — skipping morning report.")
        return
    date  = yesterday_stats.get("date", "yesterday")
    text  = (
        f"☀️ Good morning! Here's yesterday's summary ({date}):\n\n"
        f"📰 Articles seen:      {yesterday_stats.get('seen_today', 0)}\n"
        f"✅ Posts published:    {yesterday_stats.get('posted_today', 0)}\n"
        f"❌ Rejected (<{MIN_SCORE}/10): {yesterday_stats.get('rejected_today', 0)}\n"
        f"⚠️ Gemini errors:     {yesterday_stats.get('gemini_errors', 0)}\n\n"
        f"Next post today: {next_slot_time()}"
    )
    try:
        await context.bot.send_message(chat_id=OWNER_ID, text=text)
        print(f"[REPORT] Morning report sent at {now.strftime('%H:%M')} Spain time.")
    except Exception as e:
        print(f"[REPORT ERROR] {e}")


# ── Scheduled job ─────────────────────────────────────────────────────────────
async def news_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    tz  = pytz.timezone(TIMEZONE)
    now = datetime.now(tz)
    print(f"[JOB] Triggered at {now.strftime('%H:%M')} Spain time, paused={is_paused}")

    if is_paused:
        print("[JOB] Paused — skipping.")
        return
    if now.hour not in SCHEDULE_HOURS:
        print(f"[JOB] {now.hour}:xx is not a scheduled slot — skipping.")
        return

    articles = fetch_news()
    best     = score_and_pick(articles)
    if best is None:
        print("[JOB] No qualifying article this slot.")
        return
    await send_article(context.bot, best)


# ── Owner-only decorator ──────────────────────────────────────────────────────
def owner_only(func):
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_user.id != OWNER_ID:
            return
        await func(update, context)
    return wrapper


# ── Commands ──────────────────────────────────────────────────────────────────
@owner_only
async def cmd_pause(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global is_paused
    is_paused = True
    await update.message.reply_text("⏸ Posting paused.")


@owner_only
async def cmd_resume(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global is_paused
    is_paused = False
    await update.message.reply_text("▶️ Posting resumed.")


@owner_only
async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tz      = pytz.timezone(TIMEZONE)
    now     = datetime.now(tz)
    uptime  = datetime.utcnow() - start_time
    hours, remainder = divmod(int(uptime.total_seconds()), 3600)
    minutes = remainder // 60
    status  = "⏸ Paused" if is_paused else "✅ Active"
    slots   = ", ".join(f"{h:02d}:00" for h in SCHEDULE_HOURS)

    await update.message.reply_text(
        f"📊 Bot Status\n"
        f"{'─'*24}\n"
        f"Status:          {status}\n"
        f"Uptime:          {hours}h {minutes}m\n"
        f"Time (Spain):    {now.strftime('%H:%M')}\n"
        f"Next post:       {next_slot_time()}\n"
        f"{'─'*24}\n"
        f"Today's stats:\n"
        f"  Articles seen:     {stats['seen_today']}\n"
        f"  Articles posted:   {stats['posted_today']}\n"
        f"  Rejected (<{MIN_SCORE}/10): {stats['rejected_today']}\n"
        f"  Gemini errors:     {stats['gemini_errors']}\n"
        f"{'─'*24}\n"
        f"Schedule: {slots}\n"
        f"Min score: {MIN_SCORE}/10\n"
        f"Hashes on disk: {len(posted_url_hashes)}"
    )


@owner_only
async def cmd_postnow(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔄 Fetching and scoring articles...")
    articles = fetch_news()
    best     = score_and_pick(articles)
    if best is None:
        await update.message.reply_text(
            f"😕 No article scored {MIN_SCORE}+. Nothing posted."
        )
        return
    success = await send_article(context.bot, best)
    if success:
        await update.message.reply_text(f"✅ Posted:\n{best['title']}")
    else:
        await update.message.reply_text("❌ Found article but Gemini formatting failed.")


@owner_only
async def cmd_clearhashes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global posted_url_hashes, posted_title_hashes
    count = len(posted_url_hashes)
    posted_url_hashes   = set()
    posted_title_hashes = set()
    save_hashes()
    await update.message.reply_text(f"🗑 Cleared {count} hashes. All articles are fresh again.")


@owner_only
async def cmd_logs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not post_log:
        await update.message.reply_text("No posts yet this session.")
        return
    entries = post_log[-10:][::-1]  # last 10, newest first
    lines = ["📋 Last posted articles (newest first):\n"]
    for i, e in enumerate(entries, 1):
        lines.append(f"{i}. [{e['ts']}]\n   {e['title']}\n   {e['url']}\n")
    await update.message.reply_text("\n".join(lines), disable_web_page_preview=True)


@owner_only
async def cmd_search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: /search <keyword>\nExample: /search Ukraine")
        return
    query = " ".join(context.args).lower()
    matches = [e for e in post_log if query in e["title"].lower() or query in e["url"].lower()]
    if not matches:
        await update.message.reply_text(f'No posts found matching "{query}".')
        return
    entries = matches[-10:][::-1]
    lines = [f'🔍 Posts matching "{query}" ({len(matches)} total, showing last {len(entries)}):\n']
    for i, e in enumerate(entries, 1):
        lines.append(f"{i}. [{e['ts']}]\n   {e['title']}\n   {e['url']}\n")
    await update.message.reply_text("\n".join(lines), disable_web_page_preview=True)


@owner_only
async def cmd_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not yesterday_stats:
        await update.message.reply_text(
            "No yesterday data yet — the first report will arrive tomorrow morning at 08:00."
        )
        return
    date = yesterday_stats.get("date", "yesterday")
    text = (
        f"☀️ Yesterday's summary ({date}):\n\n"
        f"📰 Articles seen:      {yesterday_stats.get('seen_today', 0)}\n"
        f"✅ Posts published:    {yesterday_stats.get('posted_today', 0)}\n"
        f"❌ Rejected (<{MIN_SCORE}/10): {yesterday_stats.get('rejected_today', 0)}\n"
        f"⚠️ Gemini errors:     {yesterday_stats.get('gemini_errors', 0)}\n\n"
        f"Next post today: {next_slot_time()}"
    )
    await update.message.reply_text(text)


@owner_only
async def cmd_pin_tip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = await context.bot.send_message(
        chat_id=TELEGRAM_CHANNEL,
        text=(
            "📩 Have a tip or story idea?\n\n"
            "Reply to any post or send us a message — "
            "your suggestions shape what we cover next!"
        ),
    )
    await context.bot.pin_chat_message(
        chat_id=TELEGRAM_CHANNEL,
        message_id=msg.message_id,
        disable_notification=True,
    )
    await update.message.reply_text("✅ Tip message pinned to the channel.")


@owner_only
async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    slots = ", ".join(f"{h:02d}:00" for h in SCHEDULE_HOURS)
    await update.message.reply_text(
        "📋 Bot commands (owner only):\n\n"
        "/pause       — stop auto-posting\n"
        "/resume      — resume auto-posting\n"
        "/status      — detailed stats & status\n"
        "/postnow     — fetch & post best article now\n"
        "/clearhashes — reset seen articles list\n"
        "/pintip      — pin the tip message to channel\n"
        "/logs           — show last 10 posted articles\n"
        "/search <kw>    — search post history by keyword\n"
        "/report         — show yesterday's stats on demand\n"
        "/help        — this message\n\n"
        f"📅 Schedule (Spain): {slots}\n"
        f"⭐ Min quality score: {MIN_SCORE}/10\n"
        f"🔁 Gemini retries: {GEMINI_RETRIES}x with {GEMINI_RETRY_DELAY}s delay"
    )


# ── Flask keepalive ───────────────────────────────────────────────────────────
flask_app = Flask(__name__)

@flask_app.route("/")
def home():
    tz  = pytz.timezone(TIMEZONE)
    now = datetime.now(tz)
    return (
        f"Bot running ✅ | {now.strftime('%H:%M Spain')} | "
        f"paused={is_paused} | posted={stats['posted_today']} | "
        f"hashes={len(posted_url_hashes)}"
    ), 200

def run_flask():
    flask_app.run(host="0.0.0.0", port=5000)


# ── Entry point ───────────────────────────────────────────────────────────────
def main():
    load_hashes()
    load_post_log()
    threading.Thread(target=run_flask, daemon=True).start()

    app = Application.builder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("pause",       cmd_pause))
    app.add_handler(CommandHandler("resume",      cmd_resume))
    app.add_handler(CommandHandler("status",      cmd_status))
    app.add_handler(CommandHandler("postnow",     cmd_postnow))
    app.add_handler(CommandHandler("clearhashes", cmd_clearhashes))
    app.add_handler(CommandHandler("pintip",      cmd_pin_tip))
    app.add_handler(CommandHandler("logs",        cmd_logs))
    app.add_handler(CommandHandler("search",      cmd_search))
    app.add_handler(CommandHandler("report",      cmd_report))
    app.add_handler(CommandHandler("help",        cmd_help))

    app.job_queue.run_repeating(news_job, interval=3600, first=10, name="news")

    tz = pytz.timezone(TIMEZONE)
    # Reset stats at midnight Spain time
    midnight  = datetime.now(tz).replace(hour=0,  minute=0, second=0, microsecond=0)
    # Morning report at 08:00 Spain time
    morning   = datetime.now(tz).replace(hour=8,  minute=0, second=0, microsecond=0)
    app.job_queue.run_daily(reset_daily_stats,  time=midnight.timetz(), name="daily_reset")
    app.job_queue.run_daily(send_morning_report, time=morning.timetz(),  name="morning_report")

    print("Bot started. Polling...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
