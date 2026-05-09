import logging
import os
from datetime import datetime, time
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
    ContextTypes,
    JobQueue,
)
from feeds import RSS_FEEDS, CATEGORY_LABELS, ALL_CATEGORIES
from fetcher import fetch_category, search_articles, format_article
from storage import (
    get_subscriptions,
    add_subscription,
    remove_subscription,
    set_digest,
    get_user,
    get_all_digest_users,
)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

ITEMS_PER_PAGE = 5


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    text = (
        f"👋 Welcome, <b>{user.first_name}</b>!\n\n"
        "I'm your personal <b>News Bot</b> — I fetch the latest headlines from trusted RSS sources.\n\n"
        "<b>What I can do:</b>\n"
        "📰 /news — Get latest headlines\n"
        "🗂 /categories — Browse news by category\n"
        "🔔 /subscribe — Subscribe to topics\n"
        "📋 /mysubs — View your subscriptions\n"
        "⏰ /digest — Set up a daily news digest\n"
        "🔍 /search &lt;keyword&gt; — Search news\n"
        "❓ /help — Show all commands\n\n"
        "Let's get started — try /news!"
    )
    await update.message.reply_text(text, parse_mode="HTML")


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = (
        "<b>📖 Available Commands</b>\n\n"
        "/start — Welcome message\n"
        "/news — Latest headlines (general)\n"
        "/categories — Pick a category to read\n"
        "/subscribe — Subscribe to categories\n"
        "/mysubs — View &amp; manage subscriptions\n"
        "/digest — Configure daily digest time\n"
        "/search &lt;keyword&gt; — Search across all feeds\n"
        "/top — Top stories from all categories\n"
        "/help — This help message"
    )
    await update.message.reply_text(text, parse_mode="HTML")


async def news(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = await update.message.reply_text("⏳ Fetching latest news...")
    articles = fetch_category(RSS_FEEDS["general"], limit_per_feed=3)
    if not articles:
        await msg.edit_text("😕 Couldn't fetch news right now. Please try again later.")
        return
    response = "<b>📰 Latest Headlines</b>\n\n"
    for i, article in enumerate(articles[:5], 1):
        response += format_article(article, i) + "\n\n"
    await msg.edit_text(response, parse_mode="HTML", disable_web_page_preview=True)


async def top(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = await update.message.reply_text("⏳ Fetching top stories from all categories...")
    lines = ["<b>🌟 Top Stories Across All Categories</b>\n"]
    for category in ["world", "tech", "business", "science"]:
        articles = fetch_category(RSS_FEEDS[category], limit_per_feed=1)
        if articles:
            a = articles[0]
            label = CATEGORY_LABELS[category]
            lines.append(f"<b>{label}</b>")
            lines.append(f'• <a href="{a["link"]}">{a["title"]}</a>')
            lines.append("")
    if len(lines) <= 1:
        await msg.edit_text("😕 Couldn't fetch stories right now. Try again later.")
        return
    await msg.edit_text("\n".join(lines), parse_mode="HTML", disable_web_page_preview=True)


async def categories(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    keyboard = []
    row = []
    for i, cat in enumerate(ALL_CATEGORIES):
        row.append(InlineKeyboardButton(CATEGORY_LABELS[cat], callback_data=f"cat:{cat}"))
        if len(row) == 2:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        "🗂 <b>Select a category:</b>",
        parse_mode="HTML",
        reply_markup=reply_markup,
    )


async def category_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    category = query.data.split(":", 1)[1]
    await query.edit_message_text(f"⏳ Fetching {CATEGORY_LABELS.get(category, category)} news...")
    articles = fetch_category(RSS_FEEDS.get(category, []), limit_per_feed=3)
    if not articles:
        await query.edit_message_text("😕 Couldn't fetch news for this category. Try again later.")
        return
    response = f"<b>{CATEGORY_LABELS.get(category, category)} News</b>\n\n"
    for i, article in enumerate(articles[:5], 1):
        response += format_article(article, i) + "\n\n"
    await query.edit_message_text(response, parse_mode="HTML", disable_web_page_preview=True)


async def subscribe(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    subs = get_subscriptions(user_id)
    keyboard = []
    row = []
    for i, cat in enumerate(ALL_CATEGORIES):
        label = CATEGORY_LABELS[cat]
        checked = "✅ " if cat in subs else ""
        row.append(InlineKeyboardButton(f"{checked}{label}", callback_data=f"sub:{cat}"))
        if len(row) == 2:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)
    keyboard.append([InlineKeyboardButton("✔️ Done", callback_data="sub:done")])
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        "🔔 <b>Select categories to subscribe to:</b>\n"
        "Tap a category to toggle subscription (✅ = subscribed).",
        parse_mode="HTML",
        reply_markup=reply_markup,
    )


async def subscribe_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    action = query.data.split(":", 1)[1]

    if action == "done":
        subs = get_subscriptions(user_id)
        if subs:
            labels = [CATEGORY_LABELS[s] for s in subs]
            await query.edit_message_text(
                f"✅ Subscribed to: {', '.join(labels)}\n\nUse /mysubs to manage your subscriptions.",
                parse_mode="HTML",
            )
        else:
            await query.edit_message_text("You have no active subscriptions. Use /subscribe to add some.")
        return

    category = action
    subs = get_subscriptions(user_id)
    if category in subs:
        remove_subscription(user_id, category)
    else:
        add_subscription(user_id, category)

    subs = get_subscriptions(user_id)
    keyboard = []
    row = []
    for i, cat in enumerate(ALL_CATEGORIES):
        label = CATEGORY_LABELS[cat]
        checked = "✅ " if cat in subs else ""
        row.append(InlineKeyboardButton(f"{checked}{label}", callback_data=f"sub:{cat}"))
        if len(row) == 2:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)
    keyboard.append([InlineKeyboardButton("✔️ Done", callback_data="sub:done")])
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_reply_markup(reply_markup=reply_markup)


async def mysubs(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    subs = get_subscriptions(user_id)
    if not subs:
        await update.message.reply_text(
            "You have no subscriptions yet.\nUse /subscribe to pick your topics!"
        )
        return
    labels = [CATEGORY_LABELS[s] for s in subs]
    keyboard = [[InlineKeyboardButton(f"❌ Remove {CATEGORY_LABELS[s]}", callback_data=f"unsub:{s}")] for s in subs]
    reply_markup = InlineKeyboardMarkup(keyboard)
    text = "📋 <b>Your Subscriptions:</b>\n" + "\n".join(f"• {l}" for l in labels)
    await update.message.reply_text(text, parse_mode="HTML", reply_markup=reply_markup)


async def unsub_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    category = query.data.split(":", 1)[1]
    remove_subscription(user_id, category)
    subs = get_subscriptions(user_id)
    if not subs:
        await query.edit_message_text("You have unsubscribed from all categories.")
        return
    labels = [CATEGORY_LABELS[s] for s in subs]
    keyboard = [[InlineKeyboardButton(f"❌ Remove {CATEGORY_LABELS[s]}", callback_data=f"unsub:{s}")] for s in subs]
    reply_markup = InlineKeyboardMarkup(keyboard)
    text = "📋 <b>Your Subscriptions:</b>\n" + "\n".join(f"• {l}" for l in labels)
    await query.edit_message_text(text, parse_mode="HTML", reply_markup=reply_markup)


async def digest(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    keyboard = []
    row = []
    for h in range(0, 24):
        label = f"{h:02d}:00"
        row.append(InlineKeyboardButton(label, callback_data=f"digest:{h}"))
        if len(row) == 4:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)
    keyboard.append([InlineKeyboardButton("🚫 Disable Digest", callback_data="digest:disable")])
    reply_markup = InlineKeyboardMarkup(keyboard)
    user_info = get_user(update.effective_user.id)
    if user_info.get("digest_enabled") and user_info.get("digest_hour") is not None:
        current = f"Currently: every day at {user_info['digest_hour']:02d}:00 UTC\n\n"
    else:
        current = "Currently: disabled\n\n"
    await update.message.reply_text(
        f"⏰ <b>Daily Digest Settings</b>\n\n{current}"
        "Select what time (UTC) you want your daily news digest:",
        parse_mode="HTML",
        reply_markup=reply_markup,
    )


async def digest_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    action = query.data.split(":", 1)[1]
    if action == "disable":
        set_digest(user_id, 0, False)
        await query.edit_message_text("🚫 Daily digest disabled.")
        return
    hour = int(action)
    set_digest(user_id, hour, True)
    await query.edit_message_text(
        f"✅ Daily digest set for <b>{hour:02d}:00 UTC</b> every day.\n\n"
        "Make sure you have some subscriptions set up via /subscribe!",
        parse_mode="HTML",
    )


async def search(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.message.reply_text("Usage: /search <keyword>\nExample: /search climate")
        return
    query_str = " ".join(context.args)
    msg = await update.message.reply_text(f'🔍 Searching for "<b>{query_str}</b>"...', parse_mode="HTML")
    results = search_articles(RSS_FEEDS, query_str, max_results=5)
    if not results:
        await msg.edit_text(
            f'😕 No results found for "<b>{query_str}</b>".\nTry a different keyword.',
            parse_mode="HTML",
        )
        return
    response = f'🔍 <b>Results for "{query_str}":</b>\n\n'
    for i, article in enumerate(results, 1):
        cat_label = CATEGORY_LABELS.get(article.get("category", ""), "")
        if cat_label:
            response += f"<i>{cat_label}</i>\n"
        response += format_article(article, i) + "\n\n"
    await msg.edit_text(response, parse_mode="HTML", disable_web_page_preview=True)


async def send_digest(context: ContextTypes.DEFAULT_TYPE) -> None:
    now_hour = datetime.utcnow().hour
    users = get_all_digest_users()
    for user_id, info in users.items():
        if info.get("digest_hour") != now_hour:
            continue
        subs = info.get("subscriptions", [])
        if not subs:
            subs = ["general"]
        try:
            lines = [f"<b>☀️ Your Daily News Digest — {datetime.utcnow().strftime('%b %d, %Y')}</b>\n"]
            for category in subs[:4]:
                articles = fetch_category(RSS_FEEDS.get(category, []), limit_per_feed=2)
                if articles:
                    lines.append(f"<b>{CATEGORY_LABELS.get(category, category)}</b>")
                    for a in articles[:2]:
                        lines.append(f'• <a href="{a["link"]}">{a["title"]}</a>')
                    lines.append("")
            if len(lines) > 1:
                await context.bot.send_message(
                    chat_id=user_id,
                    text="\n".join(lines),
                    parse_mode="HTML",
                    disable_web_page_preview=True,
                )
        except Exception as e:
            logger.error(f"Failed to send digest to {user_id}: {e}")


async def unknown(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "I didn't understand that. Use /help to see all available commands."
    )


def main() -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN environment variable not set")

    app = Application.builder().token(token).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("news", news))
    app.add_handler(CommandHandler("top", top))
    app.add_handler(CommandHandler("categories", categories))
    app.add_handler(CommandHandler("subscribe", subscribe))
    app.add_handler(CommandHandler("mysubs", mysubs))
    app.add_handler(CommandHandler("digest", digest))
    app.add_handler(CommandHandler("search", search))

    app.add_handler(CallbackQueryHandler(category_callback, pattern=r"^cat:"))
    app.add_handler(CallbackQueryHandler(subscribe_callback, pattern=r"^sub:"))
    app.add_handler(CallbackQueryHandler(unsub_callback, pattern=r"^unsub:"))
    app.add_handler(CallbackQueryHandler(digest_callback, pattern=r"^digest:"))

    app.add_handler(MessageHandler(filters.COMMAND, unknown))

    job_queue = app.job_queue
    job_queue.run_repeating(send_digest, interval=3600, first=10)

    logger.info("Bot is starting...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
