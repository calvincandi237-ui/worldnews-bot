import os
import json
import hashlib
import threading
import feedparser
import requests
from bs4 import BeautifulSoup
from google import genai as genai_client
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
from flask import Flask
from datetime import datetime, time
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

# ── Posting schedule (Moscow time hours) ─────────────────────────────────────
SCHEDULE_HOURS_MSK = [8, 10, 13, 16, 19, 21]

# ── RSS Feeds ─────────────────────────────────────────────────────────────────
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

# ── State ─────────────────────────────────────────────────────────────────────
posted_hashes: set  = set()
is_paused: bool     = False


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
                h = hashlib.md5(title.encode()).hexdigest()
                if h not in posted_hashes:
                    articles.append({"title": title, "summary": summary[:400],
                                     "link": link, "hash": h})
                    new_count += 1
            print(f"[FEED] {feed_url[:70]} → new={new_count}")
        except Exception as e:
            print(f"[FEED ERROR] {feed_url[:70]} → {e}")
    print(f"[FETCH] Total new articles: {len(articles)}")
    return articles


def score_and_pick(articles: list[dict]) -> dict | None:
    """Ask Gemini to score all articles and return the best one if score >= MIN_SCORE."""
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
    try:
        resp  = client.models.generate_content(model=MODEL, contents=prompt)
        text  = resp.text.strip().lstrip("```json").lstrip("```").rstrip("```").strip()
        print(f"[SCORE] Gemini response: {text[:200]}")
        scores = sorted(json.loads(text), key=lambda x: x["score"], reverse=True)
        best   = scores[0]
        print(f"[SCORE] Best: index={best['index']}, score={best['score']}")
        if best["score"] < MIN_SCORE:
            print(f"[SCORE] Score {best['score']} < {MIN_SCORE}, skipping slot.")
            return None
        return articles[best["index"] - 1]
    except Exception as e:
        print(f"[SCORE ERROR] {e}")
        return None


def format_post(article: dict) -> str | None:
    """Ask Gemini to format a single article into the required post structure."""
    prompt = (
        "Write a Telegram channel post about this news article. Follow this structure exactly:\n\n"
        "1. HOOK — one punchy opening sentence that grabs attention. Start with a phrase like "
        "'Right now...', 'This could change...', 'A court just ruled...', 'Breaking:' etc.\n"
        "2. BACKGROUND — 2-3 sentences: what happened before this, who the key players are.\n"
        "3. CONFLICT — 1-2 sentences stating the core tension: what each side claims or wants.\n"
        "4. WHY IT MATTERS — exactly one sentence starting with '💡 Why it matters:'\n"
        "5. HASHTAGS — 3 to 5 relevant hashtags on the last line before the URL.\n"
        "6. URL — the source link on its own line, no label.\n\n"
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
    try:
        result = client.models.generate_content(model=MODEL, contents=prompt).text.strip()
        print(f"[FORMAT] Post ready for: {article['title'][:60]}")
        return result
    except Exception as e:
        print(f"[FORMAT ERROR] {e}")
        return None


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
        posted_hashes.add(article["hash"])
        print(f"[POST] ✅ {article['title'][:70]}")
        return True
    except Exception as e:
        print(f"[POST ERROR] {e}")
        return False


# ── Scheduled job ─────────────────────────────────────────────────────────────
async def news_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    tz  = pytz.timezone(TIMEZONE)
    now = datetime.now(tz)
    print(f"[JOB] Triggered at {now.strftime('%H:%M')} MSK, paused={is_paused}")

    if is_paused:
        print("[JOB] Paused — skipping.")
        return
    if now.hour not in SCHEDULE_HOURS_MSK:
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
    tz     = pytz.timezone(TIMEZONE)
    now    = datetime.now(tz)
    status = "⏸ Paused" if is_paused else "✅ Active"
    slots  = ", ".join(f"{h:02d}:00" for h in SCHEDULE_HOURS_MSK)
    await update.message.reply_text(
        f"Status: {status}\n"
        f"Time (MSK): {now.strftime('%H:%M')}\n"
        f"Schedule: {slots}\n"
        f"Min score: {MIN_SCORE}/10\n"
        f"Hashes tracked: {len(posted_hashes)}"
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
        await update.message.reply_text("❌ Found article but failed to post.")


@owner_only
async def cmd_clearhashes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global posted_hashes
    count = len(posted_hashes)
    posted_hashes = set()
    await update.message.reply_text(f"🗑 Cleared {count} hashes. All articles are fresh again.")


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
    slots = ", ".join(f"{h:02d}:00" for h in SCHEDULE_HOURS_MSK)
    await update.message.reply_text(
        "📋 Bot commands (owner only):\n\n"
        "/pause       — stop auto-posting\n"
        "/resume      — resume auto-posting\n"
        "/status      — show current status\n"
        "/postnow     — fetch & post best article immediately\n"
        "/clearhashes — reset seen articles list\n"
        "/pintip      — pin the 'send us a tip' message\n"
        "/help        — this message\n\n"
        f"📅 Schedule (MSK): {slots}\n"
        f"⭐ Min quality score: {MIN_SCORE}/10"
    )


# ── Flask keepalive ───────────────────────────────────────────────────────────
flask_app = Flask(__name__)

@flask_app.route("/")
def home():
    tz  = pytz.timezone(TIMEZONE)
    now = datetime.now(tz)
    return (
        f"Bot running ✅ | {now.strftime('%H:%M MSK')} | "
        f"paused={is_paused} | hashes={len(posted_hashes)}"
    ), 200

def run_flask():
    flask_app.run(host="0.0.0.0", port=5000)


# ── Entry point ───────────────────────────────────────────────────────────────
def main():
    threading.Thread(target=run_flask, daemon=True).start()

    app = Application.builder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("pause",       cmd_pause))
    app.add_handler(CommandHandler("resume",      cmd_resume))
    app.add_handler(CommandHandler("status",      cmd_status))
    app.add_handler(CommandHandler("postnow",     cmd_postnow))
    app.add_handler(CommandHandler("clearhashes", cmd_clearhashes))
    app.add_handler(CommandHandler("pintip",      cmd_pin_tip))
    app.add_handler(CommandHandler("help",        cmd_help))

    # Run job every hour — the job itself checks whether the current hour is a scheduled slot
    app.job_queue.run_repeating(news_job, interval=3600, first=10, name="news")

    print("Bot started. Polling...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
