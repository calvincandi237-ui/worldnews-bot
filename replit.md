# Telegram News Bot

A Python Telegram bot that delivers news from RSS feeds with on-demand headlines, category browsing, topic subscriptions, keyword search, and scheduled daily digests.

## Run & Operate

- `cd bot && python bot.py` — run the Telegram bot (managed via the "Telegram News Bot" workflow)
- Required env: `TELEGRAM_BOT_TOKEN` — Telegram bot token from @BotFather

## Stack

- Python 3.11
- python-telegram-bot 21.x (with APScheduler job queue)
- feedparser — RSS feed parsing
- JSON file storage for user subscriptions/settings

## Where things live

- `bot/bot.py` — main bot logic, command handlers, callback handlers
- `bot/feeds.py` — RSS feed URLs organized by category
- `bot/fetcher.py` — RSS fetching, parsing, formatting
- `bot/storage.py` — user subscription and digest persistence (JSON)
- `bot/data/users.json` — user data storage (auto-created)
- `bot/requirements.txt` — Python dependencies

## Product

Users can interact with the bot via:
- `/news` — latest general headlines
- `/top` — top story from each category
- `/categories` — browse by topic (World, Tech, Business, Science, Sports, Health, Entertainment, General)
- `/subscribe` — toggle category subscriptions via inline buttons
- `/mysubs` — view and remove subscriptions
- `/digest` — set a daily digest time (UTC hour via inline buttons) or disable it
- `/search <keyword>` — search across all RSS feeds

## Architecture decisions

- RSS-only: no external API keys required; feeds from BBC, Reuters, TechCrunch, NASA, ESPN, etc.
- APScheduler job runs every hour and sends digests to users whose configured hour matches UTC now
- User data stored as flat JSON for simplicity (no database needed)
- Inline keyboard buttons for all category/subscription/digest flows
- feedparser handles malformed RSS gracefully

## User preferences

- Bot token: @NFU23_bot

## Gotchas

- Digest times are in UTC
- Some RSS feeds (Reuters) may occasionally be unreliable; the bot silently skips failed feeds
- Always restart the workflow after code changes
