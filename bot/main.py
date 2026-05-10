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
from datetime import datetime
import pytz

# ── Config ──────────────────────────────────────────────
TELEGRAM_TOKEN   = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHANNEL = os.environ["TELEGRAM_CHANNEL"]  # @yourchannel
GEMINI_API_KEY   = os.environ["GEMINI_API_KEY"]
OWNER_ID         = int(os.environ["OWNER_ID"])     # ваш Telegram ID
TIMEZONE         = "Europe/Moscow"

client = genai_client.Client(api_key=GEMINI_API_KEY)
MODEL  = "gemini-1.5-flash"

# ── State ────────────────────────────────────────────────
posted_hashes: set = set()
is_paused: bool    = False
interval_hours: int = 1

RSS_FEEDS = [
    "https://feeds.bbci.co.uk/news/world/rss.xml",
    "https://rss.cnn.com/rss/edition_world.rss",
    "https://news.google.com/rss/search?q=artificial+intelligence&hl=en&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=health+science&hl=en&gl=US&ceid=US:en",
]

# ── Helpers ──────────────────────────────────────────────
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
            for entry in feed.entries[:8]:
                title   = entry.get("title", "").strip()
                summary = entry.get("summary", "").strip()
                link    = entry.get("link", "").strip()
                if not title or not link:
                    continue
                h = hashlib.md5(title.encode()).hexdigest()
                if h not in posted_hashes:
                    articles.append({"title": title, "summary": summary,
                                     "link": link, "hash": h})
        except Exception as e:
            print(f"Feed error: {e}")
    return articles


def score_articles(articles: list[dict]) -> list[dict]:
    if not articles:
        return []
    titles = "\n".join(f"{i+1}. {a['title']}" for i, a in enumerate(articles))
    prompt = (
        "Rate each headline by global importance 1-10. "
        "Return ONLY a JSON array, no markdown:\n"
        '[{"index":1,"score":8}, ...]\n\n'
        f"Headlines:\n{titles}"
    )
    try:
        resp   = client.models.generate_content(model=MODEL, contents=prompt)
        text   = resp.text.strip().lstrip("```json").rstrip("```").strip()
        scores = sorted(json.loads(text), key=lambda x: x["score"], reverse=True)
        return [articles[s["index"] - 1] for s in scores[:3]
                if 0 < s["index"] <= len(articles)]
    except Exception as e:
        print(f"Scoring error: {e}")
        return articles[:3]


def rewrite_article(article: dict) -> str | None:
    prompt = (
        "Rewrite this news as a Telegram post. Rules:\n"
        "- Max 180 words\n"
        "- Start with one relevant emoji\n"
        "- Factual, engaging tone\n"
        "- English\n"
        "- Last line: just the URL (no label)\n\n"
        f"Title: {article['title']}\n"
        f"Summary: {article['summary']}\n"
        f"URL: {article['link']}"
    )
    try:
        return client.models.generate_content(model=MODEL, contents=prompt).text.strip()
    except Exception as e:
        print(f"Rewrite error: {e}")
        return None


async def post_articles(bot, articles: list[dict]) -> int:
    count = 0
    for article in articles:
        text = rewrite_article(article)
        if not text:
            continue
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
            count += 1
            print(f"Posted: {article['title'][:60]}")
        except Exception as e:
            print(f"Telegram error: {e}")
    return count


# ── Scheduled job ────────────────────────────────────────
async def news_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    tz  = pytz.timezone(TIMEZONE)
    now = datetime.now(tz)
    if is_paused or not (7 <= now.hour < 23):
        print(f"Skipped: paused={is_paused}, time={now.strftime('%H:%M')}")
        return
    print(f"Job running: {now.strftime('%H:%M')}")
    articles = fetch_news()
    top      = score_articles(articles)
    await post_articles(context.bot, top)


# ── Commands (только для владельца) ─────────────────────
def owner_only(func):
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_user.id != OWNER_ID:
            return
        await func(update, context)
    return wrapper


@owner_only
async def cmd_pause(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global is_paused
    is_paused = True
    await update.message.reply_text("⏸ Постинг приостановлен")


@owner_only
async def cmd_resume(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global is_paused
    is_paused = False
    await update.message.reply_text("▶️ Постинг возобновлён")


@owner_only
async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tz  = pytz.timezone(TIMEZONE)
    now = datetime.now(tz)
    status = "⏸ Пауза" if is_paused else "✅ Активен"
    await update.message.reply_text(
        f"Статус: {status}\n"
        f"Интервал: {interval_hours} ч\n"
        f"Время: {now.strftime('%H:%M')}\n"
        f"Опубликовано сессий: {len(posted_hashes)}"
    )


@owner_only
async def cmd_interval(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global interval_hours
    try:
        hours = int(context.args[0])
        if not 1 <= hours <= 12:
            raise ValueError
        interval_hours = hours
        for job in context.job_queue.get_jobs_by_name("news"):
            job.schedule_removal()
        context.job_queue.run_repeating(
            news_job, interval=hours * 3600, name="news"
        )
        await update.message.reply_text(f"⏱ Интервал изменён: {hours} ч")
    except (ValueError, IndexError):
        await update.message.reply_text("Использование: /interval 2  (1–12)")


@owner_only
async def cmd_post_now(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔄 Запускаю постинг...")
    articles = fetch_news()
    top      = score_articles(articles)
    count    = await post_articles(context.bot, top)
    await update.message.reply_text(f"✅ Опубликовано: {count} новости")


@owner_only
async def cmd_clearhashes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global posted_hashes
    count = len(posted_hashes)
    posted_hashes = set()
    await update.message.reply_text(f"🗑 Очищено {count} хэшей. Теперь все статьи снова доступны.")


@owner_only
async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "/pause        — остановить постинг\n"
        "/resume       — возобновить\n"
        "/status       — текущий статус\n"
        "/interval N   — сменить интервал (часы)\n"
        "/postnow      — опубликовать сейчас\n"
        "/clearhashes  — сбросить список опубликованных\n"
        "/help         — эта справка"
    )


# ── Flask keepalive ──────────────────────────────────────
flask_app = Flask(__name__)

@flask_app.route("/")
def home():
    return "Bot is running ✅", 200

def run_flask():
    flask_app.run(host="0.0.0.0", port=5000)


# ── Entry point ──────────────────────────────────────────
def main():
    threading.Thread(target=run_flask, daemon=True).start()

    app = Application.builder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("pause",    cmd_pause))
    app.add_handler(CommandHandler("resume",   cmd_resume))
    app.add_handler(CommandHandler("status",   cmd_status))
    app.add_handler(CommandHandler("interval", cmd_interval))
    app.add_handler(CommandHandler("postnow",     cmd_post_now))
    app.add_handler(CommandHandler("clearhashes", cmd_clearhashes))
    app.add_handler(CommandHandler("help",        cmd_help))

    app.job_queue.run_repeating(news_job, interval=interval_hours * 3600,
                                first=10, name="news")

    print("Bot started. Polling...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
