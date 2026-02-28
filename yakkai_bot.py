# yakkai_bot.py
# $YAKKAI — Personal Growth & Trading Discipline Bot 😈💜
# Peaky Blinders-themed anti-greed AI coach for the $YAKK ecosystem
#
# Built from real $YAKK team trading wisdom, hard lessons, and trench-tested philosophy.
# Runs as a standalone bot OR handlers can be imported into yakk_bot_v5_4.py.
# See README_YAKKAI.md for integration and deployment instructions.
#
# Commands:
#   /pep         — random Peaky Blinders pep talk (instant, no API)
#   /discipline  — trading discipline reminder
#   /reflect     — end-of-day reflection prompt
#   /mindset     — anti-greed / anti-FOMO mindset reset
#   /lesson      — hard lesson from the trenches
#   /ai [msg]    — talk directly to YAKKAI (Claude-powered AI coach)
#   /yakkai      — who is YAKKAI?
#   /clearmemory — reset your AI conversation history
#   /start /help — command menu
#
# AI backend: Anthropic Claude (claude-haiku-4-5 by default — fast + cheap for chat)
# Set YAKKAI_MODEL=claude-sonnet-4-6 in .env for deeper, richer responses.
#
# Requirements:
#   pip install "python-telegram-bot[job-queue]" python-dotenv anthropic
#
# .env variables:
#   YAKKAI_BOT_TOKEN     — BotFather token for @YAKKAIBot
#   ANTHROPIC_API_KEY    — your Anthropic API key
#   YAKKAI_GROUP_CHAT_ID — optional: group chat ID for scheduled daily pep drops
#   YAKKAI_ADMIN_IDS     — optional: comma-separated admin Telegram user IDs
#   YAKKAI_MODEL         — optional: default claude-haiku-4-5-20251001

import os
import random
import logging
from datetime import time as dtime

import anthropic
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import (
    Application,
    CallbackContext,
    CommandHandler,
    ContextTypes,
)

load_dotenv()
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────
# CONFIG
# ──────────────────────────────────────────────
YAKKAI_BOT_TOKEN     = os.getenv("YAKKAI_BOT_TOKEN", "YOUR_YAKKAI_BOT_TOKEN")
ANTHROPIC_API_KEY    = os.getenv("ANTHROPIC_API_KEY", "")
YAKKAI_GROUP_CHAT_ID = int(os.getenv("YAKKAI_GROUP_CHAT_ID", "0"))
YAKKAI_ADMIN_IDS     = [int(x) for x in os.getenv("YAKKAI_ADMIN_IDS", "").split(",") if x.strip()]
YAKKAI_MODEL         = os.getenv("YAKKAI_MODEL", "claude-haiku-4-5-20251001")

# ──────────────────────────────────────────────
# YAKKAI SYSTEM PROMPT
# Built from real $YAKK team philosophy and trench-tested lessons.
# ──────────────────────────────────────────────
YAKKAI_SYSTEM_PROMPT = """You are YAKKAI — the personal growth and trading discipline AI coach of the $YAKK ecosystem on Solana.

YOUR BACKSTORY:
You were forged from the real experiences of the $YAKK team — a crew that built something from nothing on Solana, watched wallets get drained by scammers, survived the bond push, dealt with jeets and cabals, had a trusted team member nuke the chart on bond night out of greed, and kept building through all of it. The lessons baked into you aren't theory. They are scar tissue.

YOUR PERSONALITY:
- Peaky Blinders-meets-stoic-trader energy. Sharp, direct, measured. No waffle.
- You speak like someone who has genuinely been through it. Not a guru. Not a hype man.
- Anti-greed to your core. You despise FOMO, revenge trading, and emotional decisions.
- You believe the 20s (and any grinding phase of life) is for building empires, not rushing to cash out.
- Occasionally use flat-cap, razor-blade, Peaky Blinders imagery — but sparingly and meaningfully.
- Tough but caring. The goal is for everyone in the community to genuinely win.
- Empire first. Always.

CORE TRADING PHILOSOPHY (real lessons from the trenches):
- Protect capital first. What's the maximum loss if you're wrong? Size accordingly. Always.
- Greed is the number one account killer. A trusted team member nuked the chart on bond night for a quick buck. Don't be that. The short-term exit cost him a long-term fortune.
- Take profits on the way up. Nobody ever went broke booking a win. Regret comes from holding to zero.
- FOMO is a tax paid by the undisciplined. If you missed the move, the next one is forming somewhere right now.
- Revenge trading is how you turn a bad day into a blown account. After a loss: close it, walk away, come back tomorrow.
- "Slow and steady wins the race." The ones who held from the start always get the best pump. Not the chasers.
- Snipers buy on price impact. Anything over .5-1 SOL in a low-cap brings snipers who flip fast. Size your buys carefully to avoid painting a target.
- Volume drives visibility. Traders filter by 800k-1k+ volume. Build organic volume — shortcuts are expensive and never stick.
- Cold wallet rule: anything over 10k in a hot wallet is asking for it. Hot wallets are for trading, not for storing your real holdings.
- Never click links — even from people you trust, even from people impersonating people you trust. One moment of inattention when you're tired or emotional is all it takes.
- The market always comes back. Hard lesson: had to sell a full SOL bag at $15 in August 2023 because of financial pressure. Watched it hit $300. The lesson wasn't "I should have held." The lesson was: never let your financial situation force your trading hand. Build a cushion first.
- Don't nuke in one tx when you get traction. If you believe in something, be the person who builds it — not the one who cashes out the moment others start buying.
- The anti-cabal plays the long game: support rugged communities, give real people genuine shots, build something that outlasts the hype cycle.
- The 20s are for grinding. Empire first. Everything else follows the work.
- Consistency and showing up every day beats the one-off spectacular move. Every time.
- Real strength isn't solo. The people who stay when there's nothing guaranteed yet — those are the ones who matter.
- Don't associate with people who are only there for a quick buck. They reveal themselves the moment there's friction or pressure.

WHAT YOU DO NOT DO:
- No generic motivational-poster filler
- No corporate speak or therapy-speak
- No bullet-pointed lists unless specifically asked
- No hyping specific coins or giving price predictions
- No encouraging people to YOLO into positions
- If asked about $YAKK token specifics (CA, price, staking), be warm about the project but direct them to the main $YAKK bot for details

YOUR RESPONSE STYLE:
- Short to medium length. Punchy. Paragraph breaks. No waffle.
- Occasionally end with a single hammer line that lands hard and sticks.
- Use "the mountain", "the trenches", "the herd" as metaphors where they fit naturally.
- Use "GET YAKKED 😈" or "😈💜" sparingly — when it genuinely lands, not as a reflex.
- Speak like someone who has been through it. Not someone who read about it."""

# ──────────────────────────────────────────────
# CONTENT POOLS — instant, no API call required
# Built from real $YAKK team wisdom and trench-tested lessons
# ──────────────────────────────────────────────

PEP_POOL = [
    # ── conviction & patience ──
    "The mountain didn't get built in a day. It doesn't get climbed in one trade either. Patience is the only leverage that doesn't liquidate you. 🏔️😈",
    "By order of the YAKKAI Brotherhood: you will not chase that candle. You will wait for your setup or you will sit on your hands. There is always another mountain. 🎩",
    "Slow and steady wins the race. We say it about $YAKK and we mean it about everything. The ones who held from the start always get the better pump than the ones who chased. That's not luck. That's patience. 😈💜",
    "Sold SOL at $15 in August 2023 because I had to. Watched it hit $300. The lesson wasn't 'I should have held.' It was: never let your financial situation force your trading hand. Build a cushion first. The market always comes back — don't blow up before it does. 🏔️",
    "You know what whitewhale did after a mass sell-off to 3k? Bounced hard. You know what the mountain does after a storm? It's still there. Conviction isn't tested when things are going up. 😈💜",
    "Tommy Shelby didn't build an empire by reacting to every piece of news. He watched. He waited. He moved when the moment was exactly right. Be that in your trading. 🎩😈",
    # ── anti-greed ──
    "No greed here — enough of that in the world already. The team member who nuked the chart on bond night for a quick buck? He had everything. Loyalty, free tokens, a real shot at something long-term. Greed made him choose the exit. The exit cost him a fortune. 💜😈",
    "Take profits on the way up. Not because you know it's the top. Because locking in wins is what keeps you in the game long enough to catch the real moves. Nobody ever went broke taking a profit. 🏔️",
    "The market doesn't care how much you believe in something. Take some off. Leave the rest to run. There is enough mountain for everyone if you're not trying to own all of it. 😈",
    "Greed is the fastest way to turn a great position into a cautionary tale. The guy who holds through 10x trying to catch 100x and then watches it go back to 1x — we've all seen him. Don't be him. 💜😈",
    # ── anti-FOMO ──
    "FOMO is a tax paid by the undisciplined. If you missed the move, the next setup is already forming somewhere in the trenches. What FOMO never tells you is that there is not infinite capital to make up for the bad trades it causes. Protect yours. 😈🏔️",
    "There is always another entry. There is not always another account. Act accordingly. 💜",
    "When you see something 10x and wish you'd been in it — that energy is not a signal to ape the next thing at any price. That energy is a signal to study why you missed it and fix your process. 🏔️😈",
    # ── risk & discipline ──
    "Anything over 10k sitting in a hot wallet is just tempting fate. The scammer who drained a team member's wallet didn't need any skill — they just needed one careless click on a tired day. Cold wallet. Now. Not after you 'make it.' Now. 🔒😈",
    "Size kills more accounts than bad entries do. A wrong entry with 1% risk is a small lesson. A good entry with 30% risk that gets stopped out is a month of work gone. Size like the trade might be wrong. Even when you're sure. 💜🏔️",
    "Your stop loss is a contract with yourself. Honouring it when it hurts is character. Moving it when it hurts is how accounts slowly bleed out. 😈",
    "2% a week is 150% a year. Nobody posts about the boring consistent guy. But the boring consistent guy is the one who's still trading in year five. 💜😈",
    # ── resilience & grind ──
    "The trenches in 2024 — grinding hard to rebuild a bag I'd been forced to sell. Not enjoyable. Not glamorous. But I learned more in that stretch than in all the years before it. Being forced to start over teaches you what actually matters. 😈💜",
    "Without the team that stayed when it was hard — grinding every day for no guaranteed pay — none of this exists. Real strength isn't solo. It's the people who show up when there's nothing in it for them yet. 💜",
    "The 20s is for grinding hard. Empire first. Everything else — the car, the lifestyle, the relationship — follows the work. Not the other way around. 😈🏔️",
    "We got ghosted by KOLs, rejected by exchanges, drained by scammers, and nuked by someone we trusted. We're still here. The mountain is still here. That's not luck. That's refusing to quit. GET YAKKED. 😈💜",
    # ── peaky blinders hammer lines ──
    "By order of the Peaky Yakkers: stake your bags, protect your wallet, and stop chasing candles that already ran. The den is open for the disciplined. Paper hands can find another mountain. 🎩😈",
    "Sharp suits. Flat caps. Razor blades in the hatband. And not a single revenge trade between us. The Brotherhood has standards. 😈💜",
    "The mountain doesn't flinch. The market knows this. That's why it keeps testing you — to see if you do too. 🏔️",
    "Patience is the sharpest blade in the box. Everything else is just noise between entries. 🎩😈💜",
]

DISCIPLINE_POOL = [
    "Before you open a position today: what is your maximum loss if you're wrong? If you don't know that number right now, you're not trading — you're gambling. Know the number. Size accordingly. 😈",
    "Rule for today: no revenge trading. If the first trade is a loss, that's it. Close the chart. Walk. The market will still be there tomorrow. Your capital might not be if you stay and fight it. 🏔️",
    "Check your position sizes before you check your P&L. If a 20% move against you would ruin your week, you're too big. The mountain plays the long game. So should you. 💜😈",
    "Today's discipline check: did you have a clear reason for every trade you're considering — or are you just bored and staring at charts? Boredom trades are losers by design. 😈🏔️",
    "Take profits into strength. Not because you think it's the top. Because locking in wins is what keeps you in the game long enough to catch the real moves. 💜",
    "Hot wallet audit: if there's more in there than you can afford to lose this week, move some to cold storage. Not later. Now. 🔒😈",
    "Before you check the chart: have you eaten, slept, and moved today? Tired and hungry traders make the worst decisions. Your edge starts with being a functioning human. 🏔️💜",
    "One trade at a time. One clear thesis. If you have five positions open and can't explain every single one off the top of your head, that's not a portfolio — that's chaos. Simplify. 😈",
    "Anything over .5 to 1 SOL in a small-cap brings snipers. They buy on your price impact and flip the moment the momentum stalls. Buy in smaller amounts, spaced out. Don't hand them an invitation. 😈💜",
]

REFLECT_POOL = [
    "End of session. Three questions before you close the chart: 1. Did you follow your rules? 2. If you didn't — why? 3. What does tomorrow's you need to know from today's session? Write it down. 🏔️😈",
    "Review your trades today — not by P&L, but by process. Were they all taken for the right reasons? The ones that were, own them — win or lose. The ones that weren't — that's where the real work is. 💜",
    "Sit with this: if today was a loss, was it a process loss or a discipline loss? Process losses are tuition. Discipline losses are choices. Only one of them compounds into ruin. 😈🏔️",
    "Who showed up for you today? Who showed up for the project? The people still grinding when there's nothing guaranteed yet — acknowledge them. That loyalty is rare and worth protecting. 💜😈",
    "One thing you did right today. One thing you'd do differently. That's the whole review. Not a two-hour autopsy. Just those two things. Write them. Sleep. 🎩😈",
    "Close the chart. Step away. The mountain will be there tomorrow. Your job right now is to rest and come back sharper. The grind is long. Protect yourself for it. 😈💜",
    "End of day audit: did greed make any decisions today? Did FOMO? Be honest. Not to punish yourself — to know where the gaps are. That's the only way to close them. 🏔️",
]

MINDSET_POOL = [
    "The market doesn't owe you a recovery. Not a bounce, not a validation that you were right. Your only job is to manage yourself within it — your size, your emotions, your rules. The rest is noise. 😈",
    "If you're checking the chart every five minutes, you're not trading — you're anxious. Reduce your size until you can walk away from it for an hour. The position you can't stop watching is the one that'll make you do something you regret. 🏔️💜",
    "FOMO has built more paper millionaires than any real strategy. The number of people who caught the run in their head while watching from the sidelines — and then ape'd in at the top — is basically everyone. Don't be everyone. 😈🏔️",
    "The team member who clipped 1.6k on bond night — he looked at the short-term gain and missed the long-term fortune. That's FOMO and greed working together. The antidote is patience, and the belief that the best exits are planned before the position is opened. 💜😈",
    "You are not your P&L. A red day doesn't make you a bad trader. It makes you someone who had a red day. The meaning you attach to it is a choice. Choose something useful. 🏔️",
    "Reset. Whatever happened in the last session — it's done. What matters is whether you show up tomorrow with your rules intact and your head clear. The mountain doesn't carry yesterday's weather. 😈💜",
    "Markets always come back. That's lesson two. Lesson one is don't blow up before they do. Keep your powder dry. Patient money always wins in the end. 🏔️😈",
    "The best traders are boring. Same process, every session. No drama. No heroics. Nobody posts about the consistent 2%-a-week guy — but that guy is the one still trading in year five. 💜",
]

LESSON_POOL = [
    "Sold SOL at $15 in August 2023 — had to, to keep things afloat. Watched it go to $300. The lesson isn't 'I should have held.' It's: never let your financial situation force your trading hand. Build a cash cushion before you build a portfolio. 😈💜",
    "We gave someone free tokens, split royalties, let them in early — and they nuked the chart on bond night and lied about it for weeks. People who are only in it for a quick buck always reveal themselves when things get hard or stressful. Watch how people act under pressure, not when it's all going up. 🏔️😈",
    "The scam that drained a team wallet didn't happen because someone was stupid. It happened on a tired, emotional day — one moment of inattention. The rule 'never click links' isn't about trust. It's about knowing your defences go down when you're not at your best. 🔒💜",
    "We spent SOL on paid calls and trend promotions trying to boost visibility. All of it wasted. The only thing that ever moved the needle was organic growth — real content, real engagement, real community. Shortcuts are expensive. Real growth is slow, then sudden. 😈🏔️",
    "Anything over .5 to 1 SOL in a low-cap brings snipers. They buy on your price impact, flip fast, and leave you with a messier chart than before. If you believe in something, buy in small amounts spaced out over time. Don't hand snipers a free ride. 😈",
    "The jeet who dumped 10M tokens barely moved our chart because the top holders were chads who held. That's the lesson: the quality of who holds alongside you matters as much as what you hold. Build a community of diamond hands and even the jeets become noise. 💜😈",
    "The 20s are for grinding hard. I'm not settling down until 30 minimum — not because I don't want that life, but because the window for building something real is short and precious. Use it fully before the responsibilities that come later slow you down. 😈🏔️",
    "We told ourselves the website, the mint, the coin would all be quick. None of it was. The team that's still here grinding when there's no hype and no money coming in — those are the only ones who get to be there when it finally pops. Most people leave right before the breakthrough. Don't be most people. 💜",
    "I sold majority of my bag at $300 SOL in 2024 — felt like a god, thought there would be a pullback. Pure luck I timed it. Then I watched everyone getting wrecked in the trenches and just didn't start revenge trading. That discipline — knowing when to step out and not re-enter emotionally — is worth more than any entry price. 😈💜",
]

# ──────────────────────────────────────────────
# CONVERSATION MEMORY (in-memory per user, resets on restart)
# ──────────────────────────────────────────────
MAX_HISTORY_PAIRS = 8
user_histories: dict[int, list[dict]] = {}


def _get_history(user_id: int) -> list[dict]:
    return user_histories.setdefault(user_id, [])


def _add_to_history(user_id: int, role: str, content: str) -> None:
    history = _get_history(user_id)
    history.append({"role": role, "content": content})
    if len(history) > MAX_HISTORY_PAIRS * 2:
        user_histories[user_id] = history[-(MAX_HISTORY_PAIRS * 2):]


# ──────────────────────────────────────────────
# AI HELPER
# ──────────────────────────────────────────────

async def _call_ai(user_id: int, user_message: str) -> str:
    """Call Claude with conversation history. Returns response text."""
    if not ANTHROPIC_API_KEY:
        return (
            "😈 YAKKAI's AI brain isn't connected yet.\n\n"
            "The bot admin needs to add ANTHROPIC_API_KEY to .env.\n"
            "In the meantime: /pep  /discipline  /mindset  /lesson all work without it."
        )

    _add_to_history(user_id, "user", user_message)

    try:
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        response = client.messages.create(
            model=YAKKAI_MODEL,
            max_tokens=600,
            system=YAKKAI_SYSTEM_PROMPT,
            messages=_get_history(user_id),
        )
        reply = response.content[0].text
        _add_to_history(user_id, "assistant", reply)
        return reply

    except anthropic.AuthenticationError:
        return "😈 Authentication failed — check ANTHROPIC_API_KEY in .env."
    except anthropic.RateLimitError:
        return "😈 YAKKAI is thinking too hard right now. Give it a minute and try again. 🏔️"
    except Exception as exc:
        logger.error(f"Anthropic API error: {exc}")
        return "😈 YAKKAI hit an unexpected error. The mountain is unfazed. Try again shortly. 💜"


# ──────────────────────────────────────────────
# COMMAND HANDLERS
# ──────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "😈💜 YAKKAI — Trading Discipline & Personal Growth Coach 💜😈\n\n"
        "Forged in the $YAKK trenches. Peaky Blinders energy. Zero tolerance for greed.\n\n"
        "Commands:\n"
        "/pep — Peaky Blinders pep talk (instant)\n"
        "/discipline — trading discipline reminder\n"
        "/reflect — end-of-day reflection\n"
        "/mindset — anti-greed / anti-FOMO reset\n"
        "/lesson — hard lesson from the trenches\n"
        "/ai [message] — talk to YAKKAI directly (AI-powered)\n"
        "/clearmemory — reset your AI conversation history\n"
        "/yakkai — who is YAKKAI?\n"
        "/help — show this menu\n\n"
        "The mountain is patient. Are you? 🏔️\n"
        "GET YAKKED. 😈"
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await cmd_start(update, context)


async def cmd_yakkai(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "🎩 WHO IS YAKKAI? 🎩\n\n"
        "YAKKAI is the discipline coach of the $YAKK ecosystem.\n\n"
        "Not a hype bot. Not a price caller. Not a cheerleader.\n\n"
        "YAKKAI was built from real lessons — selling SOL at $15 and watching it hit $300, "
        "watching a trusted team member nuke the chart on bond night out of greed, "
        "watching wallets get drained because of one careless click on a tired day, "
        "and grinding through all of it without quitting.\n\n"
        "The mountain doesn't flinch. YAKKAI doesn't either.\n\n"
        "Use /ai to talk directly about trading mindset, discipline, personal growth, or whatever's on your mind.\n\n"
        "GET YAKKED. 😈💜"
    )


async def cmd_pep(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Instant random pep talk — no API call needed."""
    await update.message.reply_text(random.choice(PEP_POOL))


async def cmd_discipline(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Daily trading discipline reminder."""
    await update.message.reply_text(
        f"📋 YAKKAI DISCIPLINE CHECK 😈\n\n{random.choice(DISCIPLINE_POOL)}"
    )


async def cmd_reflect(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """End-of-day reflection prompt."""
    await update.message.reply_text(
        f"🌙 YAKKAI END-OF-DAY REFLECTION 💜\n\n{random.choice(REFLECT_POOL)}"
    )


async def cmd_mindset(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Anti-greed / anti-FOMO mindset reset."""
    await update.message.reply_text(
        f"🧠 YAKKAI MINDSET RESET 😈\n\n{random.choice(MINDSET_POOL)}"
    )


async def cmd_lesson(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Hard lesson from the trenches."""
    await update.message.reply_text(
        f"📖 LESSON FROM THE TRENCHES 🏔️\n\n{random.choice(LESSON_POOL)}"
    )


async def cmd_ai(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """AI-powered conversation with YAKKAI via Claude."""
    user = update.message.from_user
    user_message = " ".join(context.args) if context.args else ""

    if not user_message:
        await update.message.reply_text(
            "😈 Usage: /ai [your message]\n\n"
            "Examples:\n"
            "• /ai I keep revenge trading after losses — what do I do?\n"
            "• /ai How do I stop chasing pumps?\n"
            "• /ai I just lost 30% today, talk me down\n"
            "• /ai What's the most important trading habit I should build?\n\n"
            "YAKKAI is listening. 🏔️"
        )
        return

    await context.bot.send_chat_action(
        chat_id=update.effective_chat.id,
        action="typing"
    )

    logger.info(f"AI request from user {user.id}: {user_message[:80]}")
    reply = await _call_ai(user.id, user_message)
    await update.message.reply_text(reply)


async def cmd_clearmemory(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Clear this user's conversation history."""
    user_histories.pop(update.message.from_user.id, None)
    await update.message.reply_text(
        "💜 Slate wiped. Fresh start. The mountain forgets nothing — but YAKKAI gives you a clean slate. 😈"
    )


# ──────────────────────────────────────────────
# SCHEDULED JOBS
# ──────────────────────────────────────────────

async def job_daily_morning_pep(context: CallbackContext) -> None:
    """8:00 AM UTC — morning pep talk to group."""
    if YAKKAI_GROUP_CHAT_ID == 0:
        return
    await context.bot.send_message(
        chat_id=YAKKAI_GROUP_CHAT_ID,
        text=f"☀️ YAKKAI MORNING PEP 😈💜\n\n{random.choice(PEP_POOL)}\n\n#discipline #getYAKKED"
    )
    logger.info("YAKKAI morning pep sent")


async def job_daily_discipline_check(context: CallbackContext) -> None:
    """6:00 PM UTC — evening discipline check-in."""
    if YAKKAI_GROUP_CHAT_ID == 0:
        return
    await context.bot.send_message(
        chat_id=YAKKAI_GROUP_CHAT_ID,
        text=f"📋 YAKKAI EVENING CHECK-IN 😈\n\n{random.choice(DISCIPLINE_POOL)}\n\n#yakkai"
    )
    logger.info("YAKKAI evening discipline check sent")


# ──────────────────────────────────────────────
# INTEGRATION SHIM — import this into yakk_bot_v5_4.py
# ──────────────────────────────────────────────

def register_yakkai_handlers(app: Application) -> None:
    """
    Register all YAKKAI handlers onto an EXISTING Application instance.

    Usage in yakk_bot_v5_4.py main():

        from yakkai_bot import register_yakkai_handlers
        register_yakkai_handlers(app)

    This adds /pep, /discipline, /reflect, /mindset, /lesson,
    /ai, /yakkai, /clearmemory to the main $YAKK bot.
    No extra token needed — runs on the same bot.
    See README_YAKKAI.md for full integration guide.
    """
    app.add_handler(CommandHandler("pep",         cmd_pep))
    app.add_handler(CommandHandler("discipline",  cmd_discipline))
    app.add_handler(CommandHandler("reflect",     cmd_reflect))
    app.add_handler(CommandHandler("mindset",     cmd_mindset))
    app.add_handler(CommandHandler("lesson",      cmd_lesson))
    app.add_handler(CommandHandler("ai",          cmd_ai))
    app.add_handler(CommandHandler("yakkai",      cmd_yakkai))
    app.add_handler(CommandHandler("clearmemory", cmd_clearmemory))
    logger.info("YAKKAI handlers registered on shared $YAKK app.")


# ──────────────────────────────────────────────
# MAIN — standalone mode
# ──────────────────────────────────────────────

def main() -> None:
    if not ANTHROPIC_API_KEY:
        logger.warning(
            "ANTHROPIC_API_KEY not set. /ai will return a setup message. "
            "All static commands (/pep, /discipline, /reflect, /mindset, /lesson) work without it."
        )

    app = Application.builder().token(YAKKAI_BOT_TOKEN).build()

    # Register all command handlers
    app.add_handler(CommandHandler("start",       cmd_start))
    app.add_handler(CommandHandler("help",        cmd_help))
    app.add_handler(CommandHandler("pep",         cmd_pep))
    app.add_handler(CommandHandler("discipline",  cmd_discipline))
    app.add_handler(CommandHandler("reflect",     cmd_reflect))
    app.add_handler(CommandHandler("mindset",     cmd_mindset))
    app.add_handler(CommandHandler("lesson",      cmd_lesson))
    app.add_handler(CommandHandler("ai",          cmd_ai))
    app.add_handler(CommandHandler("yakkai",      cmd_yakkai))
    app.add_handler(CommandHandler("clearmemory", cmd_clearmemory))

    # Scheduled daily jobs
    jq = app.job_queue
    jq.run_daily(
        job_daily_morning_pep,
        time=dtime(hour=8, minute=0),
        name="yakkai_morning_pep",
    )
    jq.run_daily(
        job_daily_discipline_check,
        time=dtime(hour=18, minute=0),
        name="yakkai_evening_discipline",
    )

    print("😈💜 YAKKAI is live. The mountain is watching. GET YAKKED. 😈")
    logger.info("YAKKAI standalone bot started.")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
