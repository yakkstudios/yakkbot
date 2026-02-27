# yakk_bot_v5_3.py
# $YAKK Telegram Bot 😈💜  v5.2
#
# v5.2 changes — X Feed Forwarder:
#   - Polls configured X/Twitter accounts every 10 minutes via JobQueue
#   - Forwards new tweets (text + media + link) to GROUP_CHAT_ID automatically
#   - Persists last-seen tweet ID per account in x_feeds_last.json (survives restarts)
#   - /xfeeds  — shows all followed accounts + last forwarded tweet info
#   - /addxfeed @handle  — admin-only: add an account to follow
#   - /removexfeed @handle  — admin-only: remove an account
#   - Rate-limit safe: minimum 10-minute poll interval (Twitter free tier: 1 req/15min
#     per endpoint; we stagger each account call with a short sleep to stay safe)
#   - Full logging for every forwarded tweet
#
# v5.1 changes:
#   1. Gen II -> Gen III throughout (upcoming NFT collection, WL logic unchanged)
#   2. "yak" -> "yakk" spelling consistency in all user-facing strings
#   3. /roast command added (10 savage yakk-themed roasts)
#
# Requirements:
#   pip install "python-telegram-bot[job-queue]" python-dotenv aiohttp tweepy
#
# .env variables:
#   BOT_TOKEN           — from @BotFather
#   GROUP_CHAT_ID       — negative integer, e.g. -1001234567890
#   ADMIN_IDS           — comma-separated Telegram user IDs, e.g. 123456,789012
#   X_BEARER_TOKEN      — Twitter/X API v2 Bearer Token (free read-only tier)
#   X_MAIN_ACCOUNTS     — comma-separated handles, e.g. @YAKK,@YAKKStudios,@shyfts_
#
# Twitter/X free tier notes:
#   - Free tier allows ~500k tweet reads/month, 1 request per 15 min per endpoint.
#   - We poll each account individually using get_users_tweets() (user timeline).
#   - A 5-second stagger between each account call avoids burst rate errors.
#   - Poll interval is set to 600 seconds (10 min). With ≤5 accounts this is safe.
#   - If you add many accounts, consider increasing XFEED_POLL_INTERVAL_SECONDS.

import os
import json
import asyncio
import random
import logging
import hashlib
from collections import defaultdict, deque
from datetime import datetime, time as dtime
from pathlib import Path

import aiohttp
import tweepy
from dotenv import load_dotenv
from telegram import Update, ChatPermissions, InputMediaPhoto
from telegram.ext import (
    Application,
    CallbackContext,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
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
BOT_TOKEN     = os.getenv("BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")
GROUP_CHAT_ID = int(os.getenv("GROUP_CHAT_ID", "0"))
ADMIN_IDS     = [int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip()]

# $YAKK token on Solana
YAKK_CA         = "aDDUr8puCnmW2AyzrCmkxktMiRnmBSQ4a8GGhjEpump"
DEXSCREENER_URL = f"https://api.dexscreener.com/latest/dex/tokens/{YAKK_CA}"

# ── StakePoint / Staking Config ──────────────────
STAKEPOINT_POOL_URL = "https://stakepoint.app/pool/cmlig86wa0000l704q75kx8i6"
STAKEPOINT_SITE_URL = "https://yakkstudios.com/stakepoint"
NFT_WL_MIN_STAKE    = 250_000  # minimum $YAKK staked for WL NFT mint eligibility

# Regulatory-safe disclaimer — appended to all APR/reward mentions
STAKE_DISCLAIMER = (
    "⚠️ APR is variable and changes with pool activity. "
    "Rewards shown on site — check live before staking. "
    "Not financial advice. DYOR. GET YAKKED. 😈"
)

# ── Moderation ───────────────────────────────────
RUG_KEYWORDS           = ["rug", "rugpull", "scam", "honeypot", "exit", "dev ran"]
SPAM_KEYWORDS          = ["t.me/", "http://", "https://", "telegram.me"]
SPAM_WHITELIST_DOMAINS = [
    "twitter.com", "x.com", "dexscreener.com", "coingecko.com",
    "stakepoint.app", "yakkstudios.com",
]

# Anti-copypasta rolling cache settings
COPYPASTA_CACHE_SIZE = 50  # number of recent message hashes to remember per chat
COPYPASTA_MIN_LEN    = 20  # ignore messages shorter than this (reactions, "gm", etc.)

# ──────────────────────────────────────────────
# X FEED FORWARDER CONFIG  (v5.2)
# ──────────────────────────────────────────────

# Twitter/X API v2 Bearer Token — free read-only tier
X_BEARER_TOKEN = os.getenv("X_BEARER_TOKEN", "")

# Comma-separated handles from .env, e.g. "@YAKK,@YAKKStudios,@shyfts_"
# Parsed into a plain list of lowercase handles without @ for storage/lookups.
_raw_accounts = os.getenv("X_MAIN_ACCOUNTS", "")
X_MAIN_ACCOUNTS_DEFAULT: list[str] = [
    h.strip().lstrip("@").lower()
    for h in _raw_accounts.split(",")
    if h.strip()
]

# JSON file that persists {handle: last_tweet_id_str} across restarts
XFEED_STATE_FILE = Path("x_feeds_last.json")

# Seconds between full poll cycles (all accounts). 600 = 10 minutes.
# Each account gets one API call per cycle; staggered by XFEED_STAGGER_SECONDS.
XFEED_POLL_INTERVAL_SECONDS = 600

# Seconds to sleep between individual account API calls within one cycle.
# Prevents hitting burst limits on the free tier.
XFEED_STAGGER_SECONDS = 5

# Number of tweets to fetch per account per poll (max 5 keeps requests light).
XFEED_TWEETS_PER_POLL = 5

# ──────────────────────────────────────────────
# X FEED STATE  (in-memory + persisted to JSON)
# ──────────────────────────────────────────────

# {handle_lowercase: last_tweet_id_str | None}
# None means "never seen" — on first run we store latest ID without posting
# (to avoid flooding the group with a backlog of old tweets).
xfeed_state: dict[str, str | None] = {}

# {handle_lowercase: datetime of last successful forward | None}
xfeed_last_posted: dict[str, datetime | None] = {}


def _xfeed_load_state() -> None:
    """Load persisted last-seen tweet IDs from JSON file."""
    global xfeed_state
    if XFEED_STATE_FILE.exists():
        try:
            data = json.loads(XFEED_STATE_FILE.read_text(encoding="utf-8"))
            xfeed_state.update(data)
            logger.info(f"X feed state loaded: {xfeed_state}")
        except Exception as exc:
            logger.error(f"Failed to load x_feeds_last.json: {exc}")


def _xfeed_save_state() -> None:
    """Persist last-seen tweet IDs to JSON file."""
    try:
        XFEED_STATE_FILE.write_text(
            json.dumps(xfeed_state, indent=2), encoding="utf-8"
        )
    except Exception as exc:
        logger.error(f"Failed to save x_feeds_last.json: {exc}")


def _xfeed_get_accounts() -> list[str]:
    """
    Return the current list of followed handles (lowercase, no @).
    Accounts not yet in xfeed_state are seeded with None.
    """
    # Merge defaults into state dict if not already present
    for h in X_MAIN_ACCOUNTS_DEFAULT:
        if h not in xfeed_state:
            xfeed_state[h] = None
    return list(xfeed_state.keys())


def _make_tweepy_client() -> tweepy.Client | None:
    """Create a Tweepy v2 client. Returns None if bearer token missing."""
    if not X_BEARER_TOKEN:
        logger.warning("X_BEARER_TOKEN not set — X feed forwarder disabled.")
        return None
    return tweepy.Client(bearer_token=X_BEARER_TOKEN, wait_on_rate_limit=False)


# ──────────────────────────────────────────────
# RUNTIME STATE  (in-memory; resets on restart)
# ──────────────────────────────────────────────
raid_leaderboard:  dict[int, int] = defaultdict(int)   # user_id → xp
user_display_names: dict[int, str] = {}                 # user_id → display name

copypasta_cache:  dict[int, deque] = defaultdict(lambda: deque(maxlen=COPYPASTA_CACHE_SIZE))
copypasta_counts: dict[tuple, int] = defaultdict(int)   # (chat_id, hash) → count

last_price_snapshot: dict = {}  # {"price": float, "timestamp": datetime}

# ──────────────────────────────────────────────
# CONTENT POOLS
# ──────────────────────────────────────────────
WELCOME_MESSAGES = [
    (
        "😈💜 YO! A new degen just scaled the mountain!\n\n"
        "Welcome to the YAKK herd, {name}!\n\n"
        "The yakk has survived -40°C and a 90% market correction.\n"
        "You're in the right place.\n\n"
        "🏔️ Drop your best yakk meme. GET YAKKED. 😈\n"
        "🐾 Check pinned posts for $YAKK lore.\n"
        "💜 Pink fur. Gold drip. Diamond hooves. LFG."
    ),
    (
        "🏔️ THE MOUNTAIN STIRS.\n\n"
        "{name} has arrived at base camp.\n\n"
        "Pink fur? ✅  Silver horns? ✅  Yellow eyes? ✅\n"
        "Ready to hold through bloodbaths? 👀\n\n"
        "Welcome to $YAKK, degen. The herd is watching. 😈💜\nGET YAKKED. 😈"
    ),
    (
        "💜 A new yakk has entered the chat.\n\n"
        "gm {name}! You found us.\n\n"
        "Rule 1: The mountain doesn't rug.\n"
        "Rule 2: We meme together or we die alone.\n"
        "Rule 3: Pink yakks only. 😈\n\n"
        "Post your best yakk art, meme, or galaxy-brain take. YAKK Studios is watching. 👁️\nGET YAKKED. 😈"
    ),
]

YAK_FACTS = [
    "😈 Yakks can survive temperatures as low as -40°C. Your portfolio dropping 40% is nothing to them.",
    "🏔️ Wild yakks can run at 25 mph uphill. That's faster than your stop loss triggers.",
    "💜 Yakk wool (called 'khullu') is softer than cashmere and 3x warmer. Just like $YAKK holders — soft hands? EXPELLED.",
    "😈 Yakks communicate through grunt-like sounds. This Telegram group is basically the same thing.",
    "🏔️ A yakk's lungs are 3x the size of a normal cow's. They were built for altitude. $YAKK was built for all-time highs.",
    "💛 Yakk milk is naturally pink-tinged. It was always the pink yakk. It was always $YAKK.",
    "🐾 Yakks have been domesticated for over 10,000 years. Longer than Bitcoin. Longer than your altcoin thesis.",
    "😈 In Tibetan culture, yakks are sacred animals. In the YAKK herd, diamond hooves are sacred.",
    "🏔️ Wild yakk herds can number in the thousands. The $YAKK herd is just getting started.",
    "💜 Yakks are expert mountain climbers. A bear market is just Tuesday to them.",
]

MEME_FORMATS = [
    "POV: you bought $YAKK at the bottom 😈📈\n[yakk standing on a mountain made of green candles]",
    "average $YAKK holder:\n- woke up at 4am for the raid ✅\n- forgot to eat ✅\n- checked chart 47 times ✅\n- still bullish ✅\n😈💜",
    "them: 'it's just a yakk meme coin'\nthe mountain: 🏔️\nthe yakk: 😈\nour bags: 📈\nus: 💜",
    "my portfolio:\n🔴 other coins\n💜 $YAKK\n\n(the yakk does not explain itself)",
    "chart analyst: 'bearish divergence on the 4H—'\nyakk: *grazes peacefully at 6,000 metres* 😈",
    "the yakk doesn't time the market.\nthe market times the yakk. 🏔️",
]

ROAST_POOL = [
    "you call that a bag? my pink yakk's gold drip tail has more liquidity than your whole portfolio. GET YAKKED. 😈",
    "bro your chart looks like my yakk after a -90% dip... still standing tall tho. the yakk recovered. did you? 💜",
    "you entered the group and didn't drop a meme? the mountain is DISAPPOINTED. GET YAKKED or get grazed. 😈🏔️",
    "your stop loss is tighter than your conviction. the yakk has no stop loss. the yakk IS the stop loss. 😈",
    "checked your entry price lately? don't worry, the yakk checked it for you. it's giving 'bought the top' energy. 💜😈",
    "you've been in this group for how long and still haven't staked? the mountain is filing a formal complaint. 🔒😈",
    "your portfolio is red. your hands are paper. your yakk is embarrassed. fix one of those three. 😈💜",
    "i've seen stronger conviction from a frog in a bear market. and $FWOG lives in a POND. GET YAKKED. 😈",
    "average degen: panic sells at -20%, buys back at +50%, wonders why bags are light. be the yakk, not the degen. 🏔️",
    "your wallet address is giving 'never gonna make it'. the yakk says this with love. STAKE UP. GET YAKKED. 😈💜",
]

RAID_TEMPLATES = [
    (
        "😈 HERD ALERT — RAID TIME 💜\n\n"
        "Target: {target}\n\n"
        "🏔️ The mountain moves TOGETHER.\n"
        "1️⃣ Like the post\n"
        "2️⃣ Repost/RT\n"
        "3️⃣ Drop a yakk emoji in the comments: 😈\n\n"
        "⏱️ 10 minutes. GO GO GO.\n"
        "Report back when done. Yakk honor is on the line. 💜\n\n"
        "Type /done when you've completed the raid to earn Raid XP! 🏅"
    ),
    (
        "⚔️ $YAKK RAID INCOMING ⚔️\n\n"
        "👉 {target}\n\n"
        "MISSION:\n"
        "✅ Like\n"
        "✅ Repost\n"
        "✅ Comment GET YAKKED 😈 or 'the mountain sends its regards'\n\n"
        "Yakks don't retreat. MOVE. 🏔️\n\n"
        "Type /done after raiding to log your Raid XP! 🏅"
    ),
]

# ── Staking messages (rotating, same core info) ──
_STAKE_MESSAGES = [
    (
        "🔒😈💜 LOCK IN THE MOUNTAIN 💜😈🔒\n\n"
        "The $YAKK staking pool is LIVE on StakePoint (Solana).\n\n"
        "🏆 What you get:\n"
        "• $YAKK reflections dripping into your wallet passively\n"
        "• $SPT reward tokens on top — double the drip\n"
        "• Check current APR live on the site (it spikes hard 🔥)\n\n"
        "🎖️ NFT STAKING — COMING SOON:\n"
        "Gen III sharp-suited traders, Peaky Blinders crews & mafia dons incoming.\n"
        "WL mint eligibility: Genesis or Gen II hold + min {min_stake:,} $YAKK staked.\n"
        "Public access via StakePoint for exclusive perks & drops.\n\n"
        "💰 Stake now → earn while you hold:\n"
        "🔗 {pool}\n"
        "🌐 {site}\n\n"
        "{disclaimer}"
    ),
    (
        "😈💎🏔️ DIAMOND HOOVES. PASSIVE DRIP. 🏔️💎😈\n\n"
        "Why just hold $YAKK when the mountain can PAY you?\n\n"
        "Stake on StakePoint → earn $YAKK reflections + $SPT rewards.\n"
        "The pool is live. The APR is on the site. Go check it. 👀\n\n"
        "🔮 NFT STAKING INCOMING:\n"
        "Gen III collection drops are near — cyber overlords, mythic beasts, regal thrones.\n"
        "Want WL access? Stack {min_stake:,}+ $YAKK in the pool and hold Genesis.\n"
        "No stake, no throne. Simple math. 😈\n\n"
        "🔗 Pool: {pool}\n"
        "🌐 Info: {site}\n\n"
        "{disclaimer}"
    ),
    (
        "💜🔥 THE PINK CULT EARNS WHILE IT SLEEPS 🔥💜\n\n"
        "Staking is live. Rewards are flowing.\n\n"
        "📊 $YAKK staking on StakePoint:\n"
        "→ Earn $YAKK reflections (auto-compound that bag)\n"
        "→ Earn $SPT tokens (bonus drip for the degens)\n"
        "→ APR varies — check site for live rate\n\n"
        "👑 NFT STAKING PERKS (soon™):\n"
        "Gen III NFT collection incoming. WL mint = Genesis hold + {min_stake:,} $YAKK staked.\n"
        "Public stakers get exclusive perks & early drops on StakePoint.\n"
        "Stack now. Throne later. 🏔️\n\n"
        "🔗 {pool}\n"
        "🌐 {site}\n\n"
        "{disclaimer}"
    ),
]

# ──────────────────────────────────────────────
# /prompt — fixed core descriptor + scene pool
# ──────────────────────────────────────────────
YAKK_CORE_DESCRIPTOR = (
    "a massive vibrant pink yakk with dense hot-pink fur, "
    "sharp silver-white curved horns, glowing yellow eyes with a dramatic black mask, "
    "jet-black hooves, and a shimmering gold-drip tail"
)

# Each scene's template uses {yakk} which is replaced with YAKK_CORE_DESCRIPTOR
PROMPT_SCENES = [
    {
        "label": "🏔️ Summit King",
        "template": (
            "Cinematic matte painting, {yakk} standing triumphant on a Himalayan peak at golden hour, "
            "storm clouds parting behind it, god-rays cutting through mist, epic scale, "
            "coins and runes glowing beneath its hooves, ultra-detailed fur texture, "
            "8K resolution quality --ar 16:9 --style raw --v 6"
        ),
    },
    {
        "label": "⚔️ War vs $LUCI",
        "template": (
            "Epic fantasy battle scene, {yakk} charging across a hellish battlefield toward a shadowy demonic figure "
            "made of red flames labeled '$LUCI', the yakk's eyes blazing, pink fur bristling with energy, "
            "dark red and purple sky splitting open, lightning strikes, cinematic oil painting style, "
            "dramatic action composition --ar 16:9 --style raw --chaos 20"
        ),
    },
    {
        "label": "🐸 Meme War vs $FWOG",
        "template": (
            "Chaotic anime battle scene, {yakk} towering over a giant panicking cartoon frog wearing a '$FWOG' jersey, "
            "the yakk stomping forward with pink lightning aura, the frog fleeing in exaggerated cartoon terror, "
            "neon Tokyo night backdrop, graffiti text 'THE MOUNTAIN WINS' in the background, "
            "hyper-detailed action manga aesthetic, vibrant colours --ar 16:9 --v 6"
        ),
    },
    {
        "label": "🐧 Penguin Collab",
        "template": (
            "Cozy but epic digital painting, {yakk} and a chubby pudgy penguin in matching pink outfits "
            "standing side by side on a snowy summit, northern lights overhead, both holding phones showing green charts, "
            "best-friends energy meets crypto degen culture, warm pastel palette against deep blue night sky, "
            "Studio Ghibli aesthetic meets crypto art --ar 16:9 --style raw"
        ),
    },
    {
        "label": "💜 Pink Cult Raid",
        "template": (
            "Cinematic propaganda poster, {yakk} leading an army of smaller pink yakks charging down a mountain toward a city skyline, "
            "each yakk wearing a cult robe and holding a pink banner with $YAKK logo, "
            "dramatic dawn light, dust clouds rising, retro propaganda art style meets modern crypto aesthetic, "
            "gold and deep purple palette, 'THE PINK HERD ARRIVES' text banner --ar 2:3 --style raw"
        ),
    },
    {
        "label": "🌆 Cyberpunk Degen",
        "template": (
            "Cyberpunk street art mural, {yakk} as a crypto degen with laser eyes and a gold chain, "
            "diamond hooves, holding a cracked phone screen showing 100x gains, "
            "neon-soaked rainy alley backdrop, glitch effects, holographic $YAKK logos floating in the air, "
            "graffiti and cyberpunk aesthetic, purple and pink neon palette --ar 1:1 --style raw"
        ),
    },
    {
        "label": "🤝 Mountain Pact Alliance",
        "template": (
            "Epic wide-angle fantasy illustration, {yakk} leading a ragtag band of underdog crypto animals "
            "(a small frog, a glowing dust sprite, a tiny penguin) up a massive glowing mountain, "
            "dawn breaking behind the summit, each character wearing matching adventure gear, "
            "Lord of the Rings scale composition, painterly cinematic quality, "
            "gold and teal colour palette, triumph energy --ar 16:9 --style raw"
        ),
    },
    # v3.0 staking / NFT scenes
    {
        "label": "🔒 DeFi Stake Pool",
        "template": (
            "Cinematic sci-fi digital painting, {yakk} standing at the edge of a vast glowing DeFi staking pool "
            "filled with swirling liquid light and floating reward orbs in pink and gold, "
            "holographic APR percentage readouts hovering in the air, locked token chests submerged in the pool, "
            "$SPT reward crystals raining down from the sky, deep purple and electric gold palette, "
            "epic futuristic atmosphere, ultra-detailed reflections --ar 16:9 --style raw --v 6"
        ),
    },
    {
        "label": "👑 NFT Throne Guardian",
        "template": (
            "Dark fantasy throne room illustration, {yakk} seated on a towering icy obsidian throne engraved with $YAKK runes, "
            "surrounding it are glowing Gen III NFT beast silhouettes — a cyber overlord, a regal throne guardian, a mythic beast — "
            "all bowing in reverence, golden chains connecting them to a central glowing WL mint portal, "
            "dramatic candlelight meets neon glow, cinematic wide shot, ultra-detailed fur and ice textures, "
            "deep indigo and molten gold palette --ar 16:9 --style raw --chaos 10"
        ),
    },
    {
        "label": "🤖 Cyber Overlord Degen",
        "template": (
            "Cyberpunk concept art, {yakk} as a cyber overlord degen in a sleek black exosuit with pink neon trim and gold drip accents, "
            "holographic staking dashboards floating around it showing locked $YAKK bags and $SPT reward flows, "
            "diamond hooves on a reflective black floor, silver horns pulsing with data streams, "
            "yellow eyes scanning reward metrics, megacity backdrop through rain-slicked glass, "
            "dark cyberpunk aesthetic meets meme coin chaos, ultra-detailed --ar 16:9 --style raw --v 6"
        ),
    },
]

# ──────────────────────────────────────────────
# COMMAND HANDLERS
# ──────────────────────────────────────────────

async def handle_new_chat_member(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Welcome new members."""
    for member in update.message.new_chat_members:
        if member.is_bot:
            continue
        name = member.first_name or "degen"
        await update.message.reply_text(random.choice(WELCOME_MESSAGES).format(name=name))


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "😈 $YAKK Bot v5.2 is LIVE on the mountain. GET YAKKED. 😈\n\n"
        "Commands:\n"
        "/raid [target] — coordinate a herd raid\n"
        "/done — log raid completion (+10 Raid XP)\n"
        "/leaderboard — top 10 raid warriors\n"
        "/stake — staking pool info + NFT perks teaser\n"
        "/fact — drop a random yakk fact\n"
        "/meme — generate a yakk meme text\n"
        "/prompt — get a Midjourney art prompt\n"
        "/price — check $YAKK price\n"
        "/roast [@user] — savage yakk-themed roast 😈\n"
        "/xfeeds — show followed X accounts + last post info\n"
        "/addxfeed @handle — [admin] follow a new X account\n"
        "/removexfeed @handle — [admin] unfollow an X account\n"
        "/yakk — for the vibes\n"
        "/help — show this menu\n\n"
        "💜 The mountain awaits. 🏔️"
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await cmd_start(update, context)


async def cmd_raid(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Post a raid call. Initiator earns +5 XP."""
    if not context.args:
        await update.message.reply_text(
            "😈 Usage: /raid [target]\nExample: /raid https://x.com/SomePost"
        )
        return
    user   = update.message.from_user
    target = " ".join(context.args)
    _add_xp(user, xp=5)
    await update.message.reply_text(random.choice(RAID_TEMPLATES).format(target=target))


async def cmd_done(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """User reports raid done — +10 XP."""
    user = update.message.from_user
    _add_xp(user, xp=10)
    xp = raid_leaderboard[user.id]
    responses = [
        f"✅ GET YAKKED! 😈 +10 Raid XP for {user.first_name}! Total: {xp} XP",
        f"🏔️ The mountain acknowledges {user.first_name}. +10 XP. Total: {xp} XP. Keep climbing.",
        f"💜 {user.first_name} just earned their hooves. GET YAKKED. 😈 +10 Raid XP. Total: {xp} XP",
        f"⚔️ Raid complete! +10 XP logged for {user.first_name}. Total: {xp} XP. The herd salutes you.",
    ]
    await update.message.reply_text(random.choice(responses))


async def cmd_leaderboard(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show top-10 raiders."""
    if not raid_leaderboard:
        await update.message.reply_text(
            "🏔️ The leaderboard is empty — no raids logged yet.\n"
            "Use /raid [link] to start a raid and /done when finished! 😈"
        )
        return
    sorted_board = sorted(raid_leaderboard.items(), key=lambda x: x[1], reverse=True)[:10]
    medals = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]
    lines  = ["😈💜 $YAKK RAID LEADERBOARD 💜😈 — GET YAKKED.", "🏔️ Top Warriors of the Mountain:\n"]
    for i, (uid, xp) in enumerate(sorted_board):
        name = user_display_names.get(uid, f"Degen #{uid}")
        lines.append(f"{medals[i]} {name} — {xp} Raid XP")
    lines.append("\n⚔️ Use /raid + /done to earn XP and claim your throne.")
    await update.message.reply_text("\n".join(lines))


async def cmd_stake(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """StakePoint pool info with NFT tease and disclaimer."""
    msg = random.choice(_STAKE_MESSAGES).format(
        pool=STAKEPOINT_POOL_URL,
        site=STAKEPOINT_SITE_URL,
        min_stake=NFT_WL_MIN_STAKE,
        disclaimer=STAKE_DISCLAIMER,
    )
    await update.message.reply_text(msg)


async def cmd_fact(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(f"😈 YAKK FACT:\n\n{random.choice(YAK_FACTS)}\n\nGET YAKKED. 😈")


async def cmd_meme(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        f"💜 FRESH FROM THE MOUNTAIN:\n\n{random.choice(MEME_FORMATS)}\n\nGET YAKKED. 😈\n\n#YAKK #pinkfur #alphadegen"
    )


async def cmd_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Midjourney prompt: fixed core descriptor injected into a random scene."""
    scene  = random.choice(PROMPT_SCENES)
    prompt = scene["template"].format(yakk=YAKK_CORE_DESCRIPTOR)
    await update.message.reply_text(
        f"🎨 YAKK STUDIOS — {scene['label']}\n\n"
        f"`{prompt}`\n\n"
        "Drop this into Midjourney, generate your art, and post it here for the herd to vote on! 😈💜\n"
        "Best art gets pinned. YAKK Studios is watching. 👁️\nGET YAKKED. 😈",
        parse_mode="Markdown",
    )


async def cmd_price(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """On-demand price check from DexScreener + stake CTA."""
    price, change, url = await _fetch_yakk_price()
    if price is None:
        await update.message.reply_text(
            "😈 Couldn't reach DexScreener right now. The mountain is unfazed. Try again soon."
        )
        return
    change_str = f"+{change:.2f}%" if change >= 0 else f"{change:.2f}%"
    emoji = "📈🟢" if change >= 0 else "📉🔴"
    vibe  = "The mountain pumps. GET YAKKED. 😈💜" if change >= 0 else "Dip = discount. The yakk is unbothered. GET YAKKED. 😈🏔️"
    await update.message.reply_text(
        f"😈 $YAKK PRICE CHECK {emoji}\n\n"
        f"Price: ${price:.8f}\n"
        f"5min change: {change_str}\n\n"
        f"🔗 Chart: {url}\n\n"
        f"{vibe}\n\n"
        f"💰 Stake your $YAKK for passive drip while you wait:\n"
        f"🔗 {STAKEPOINT_POOL_URL}\n\n"
        f"{STAKE_DISCLAIMER}"
    )


async def cmd_yakk(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    responses = [
        "😈 YAKK.", "💜 the mountain stirs.", "🏔️ we are so back.",
        "😈 pink fur. gold drip. no rugs.", "💜 YAKKED and based.",
        "GET YAKKED. 😈", "😈 GET YAKKED. the mountain demands it. 💜",
        "you just got YAKKED. 😈💜", "GET YAKKED or get off the mountain. 🏔️",
    ]
    await update.message.reply_text(random.choice(responses))


async def cmd_roast(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Savage yakk-themed roast. /roast or /roast @user."""
    user = update.message.from_user
    # If a target username/name given, roast them; otherwise roast the sender
    if context.args:
        target_name = " ".join(context.args).lstrip("@")
    else:
        target_name = user.first_name or "degen"
    roast = random.choice(ROAST_POOL)
    await update.message.reply_text(
        f"😈 YAKK ROAST — {target_name}, this one's for you:\n\n{roast}"
    )


# ──────────────────────────────────────────────
# X FEED COMMANDS  (v5.2)
# ──────────────────────────────────────────────

async def cmd_xfeeds(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show all followed X accounts and their last-forward info."""
    accounts = _xfeed_get_accounts()
    if not accounts:
        await update.message.reply_text(
            "😈 No X accounts followed yet.\n"
            "Admins: use /addxfeed @handle to add one."
        )
        return

    token_status = "✅ connected" if X_BEARER_TOKEN else "❌ X_BEARER_TOKEN missing in .env"
    lines = [
        f"🐦 $YAKK X FEED FORWARDER 😈",
        f"API status: {token_status}",
        f"Poll interval: every {XFEED_POLL_INTERVAL_SECONDS // 60} minutes\n",
        "Followed accounts:",
    ]
    for handle in sorted(accounts):
        last_id   = xfeed_state.get(handle)
        last_post = xfeed_last_posted.get(handle)
        last_str  = last_post.strftime("%Y-%m-%d %H:%M UTC") if last_post else "not yet forwarded"
        id_str    = f"tweet #{last_id}" if last_id else "baseline not set yet"
        lines.append(f"  @{handle}  —  {last_str}  ({id_str})")

    lines.append("\nAdmins: /addxfeed @handle  |  /removexfeed @handle")
    await update.message.reply_text("\n".join(lines))


async def cmd_addxfeed(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Admin-only: add an X account to the follow list."""
    user = update.message.from_user
    if user.id not in ADMIN_IDS:
        await update.message.reply_text("😈 Admins only. The mountain decides who follows who.")
        return
    if not context.args:
        await update.message.reply_text("Usage: /addxfeed @handle")
        return

    handle = context.args[0].lstrip("@").lower().strip()
    if not handle:
        await update.message.reply_text("😈 Invalid handle.")
        return

    if handle in xfeed_state:
        await update.message.reply_text(f"😈 @{handle} is already being followed.")
        return

    # Seed with None — on first poll we'll store the latest tweet ID without posting
    # (avoids flooding the group with old tweets on first run)
    xfeed_state[handle] = None
    xfeed_last_posted[handle] = None
    _xfeed_save_state()

    logger.info(f"X feed: added @{handle} by admin {user.id}")
    await update.message.reply_text(
        f"✅ Now following @{handle} on X.\n"
        f"First poll will baseline the latest tweet (no backlog flood). 😈"
    )


async def cmd_removexfeed(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Admin-only: remove an X account from the follow list."""
    user = update.message.from_user
    if user.id not in ADMIN_IDS:
        await update.message.reply_text("😈 Admins only. The mountain decides who follows who.")
        return
    if not context.args:
        await update.message.reply_text("Usage: /removexfeed @handle")
        return

    handle = context.args[0].lstrip("@").lower().strip()
    if handle not in xfeed_state:
        await update.message.reply_text(f"😈 @{handle} isn't in the follow list.")
        return

    del xfeed_state[handle]
    xfeed_last_posted.pop(handle, None)
    _xfeed_save_state()

    logger.info(f"X feed: removed @{handle} by admin {user.id}")
    await update.message.reply_text(f"✅ Unfollowed @{handle}. The mountain moves on. 😈")


# ──────────────────────────────────────────────
# MODERATION
# ──────────────────────────────────────────────

async def moderate_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Anti-copypasta + rug keywords + spam link filter."""
    if not update.message or not update.message.text:
        return

    text       = update.message.text
    text_lower = text.lower()
    user       = update.message.from_user
    chat_id    = update.effective_chat.id

    if user.id in ADMIN_IDS:
        return

    # ── Anti-copypasta ────────────────────────────
    if len(text) >= COPYPASTA_MIN_LEN:
        msg_hash  = hashlib.md5(text.strip().lower().encode()).hexdigest()
        cache_key = (chat_id, msg_hash)
        cache     = copypasta_cache[chat_id]

        copypasta_counts[cache_key] += 1

        # Decrement count for the hash about to be evicted
        if len(cache) == COPYPASTA_CACHE_SIZE:
            old_key = (chat_id, cache[0])
            copypasta_counts[old_key] = max(0, copypasta_counts[old_key] - 1)

        cache.append(msg_hash)

        if copypasta_counts[cache_key] >= 3:
            await update.message.delete()
            handle = user.username or user.first_name
            await context.bot.send_message(
                chat_id=chat_id,
                text=random.choice([
                    f"🚫 @{handle} the yakk has seen that message before. And before that. Copy-paste shill = instant delete. GET YAKKED. 😈",
                    f"😈 @{handle} your clipboard is not a personality. The mountain is not impressed. Deleted.",
                    f"💜 @{handle} repetition is for price announcements, not spam. Clean it up. 🏔️",
                ]),
            )
            return

    # ── Rug / scam keywords ───────────────────────
    if any(kw in text_lower for kw in RUG_KEYWORDS):
        await update.message.delete()
        warnings = context.bot_data.setdefault("warnings", {})
        warnings[user.id] = warnings.get(user.id, 0) + 1
        await context.bot.send_message(
            chat_id=chat_id,
            text=random.choice([
                f"⚠️ @{user.username or user.first_name} — rug talk doesn't fly on the mountain.\nWarning {warnings[user.id]}/3. 😈",
                f"🏔️ @{user.username or user.first_name} — the yakk deleted your negativity. Warning {warnings[user.id]}/3.",
            ]),
        )
        if warnings[user.id] >= 3:
            await context.bot.restrict_chat_member(
                chat_id=chat_id,
                user_id=user.id,
                permissions=ChatPermissions(can_send_messages=False),
            )
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"😈 {user.first_name} has been silenced by the mountain. The herd is cleansed. 💜",
            )
        return

    # ── Spam links ────────────────────────────────
    if any(kw in text_lower for kw in SPAM_KEYWORDS):
        if not any(domain in text_lower for domain in SPAM_WHITELIST_DOMAINS):
            await update.message.delete()
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"🚫 @{user.username or user.first_name} — unsolicited links get trampled. No promo without permission. 😈",
            )


# ──────────────────────────────────────────────
# PRICE FETCHING
# ──────────────────────────────────────────────

async def _fetch_yakk_price() -> tuple:
    """
    Fetch $YAKK price from DexScreener.
    Returns (price_usd: float, price_change_5m: float, dex_url: str)
    or (None, 0.0, "") on any error.
    """
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                DEXSCREENER_URL, timeout=aiohttp.ClientTimeout(total=10)
            ) as resp:
                if resp.status != 200:
                    logger.warning(f"DexScreener HTTP {resp.status}")
                    return None, 0.0, ""
                data = await resp.json()

        pairs = data.get("pairs")
        if not pairs:
            logger.warning("DexScreener returned no pairs for $YAKK CA")
            return None, 0.0, ""

        # Pick the highest-liquidity pair
        pair    = max(pairs, key=lambda p: float(p.get("liquidity", {}).get("usd", 0) or 0))
        price   = float(pair.get("priceUsd", 0))
        change  = float(pair.get("priceChange", {}).get("m5", 0) or 0)
        dex_url = pair.get("url", f"https://dexscreener.com/solana/{YAKK_CA}")
        return price, change, dex_url

    except Exception as exc:
        logger.error(f"Price fetch failed: {exc}")
        return None, 0.0, ""


# ──────────────────────────────────────────────
# X FEED FORWARDER — CORE LOGIC  (v5.2)
# ──────────────────────────────────────────────

async def _forward_tweet(bot, handle: str, tweet, media_urls: list[str]) -> None:
    """
    Send a single tweet to GROUP_CHAT_ID.
    - If the tweet has 1+ images: send as photo(s) with caption.
    - Otherwise: send as plain text message.
    Always includes the direct tweet link.
    """
    tweet_url = f"https://x.com/{handle}/status/{tweet.id}"
    caption   = (
        f"🐦 New post from @{handle} on X:\n\n"
        f"{tweet.text}\n\n"
        f"🔗 {tweet_url}"
    )

    # Telegram caption max length is 1024 chars; truncate gracefully
    if len(caption) > 1024:
        max_text = 1024 - len(f"\n\n🔗 {tweet_url}") - 30
        caption  = (
            f"🐦 New post from @{handle} on X:\n\n"
            f"{tweet.text[:max_text]}...\n\n"
            f"🔗 {tweet_url}"
        )

    try:
        if media_urls:
            if len(media_urls) == 1:
                # Single image — send_photo with caption
                await bot.send_photo(
                    chat_id=GROUP_CHAT_ID,
                    photo=media_urls[0],
                    caption=caption,
                )
            else:
                # Multiple images — send as media group; caption on first item only
                media_group = [
                    InputMediaPhoto(media=url, caption=caption if i == 0 else None)
                    for i, url in enumerate(media_urls[:10])  # Telegram max 10
                ]
                await bot.send_media_group(chat_id=GROUP_CHAT_ID, media=media_group)
        else:
            # Text-only tweet
            await bot.send_message(chat_id=GROUP_CHAT_ID, text=caption)

        logger.info(f"X feed: forwarded tweet {tweet.id} from @{handle} — {tweet_url}")

    except Exception as exc:
        logger.error(f"X feed: failed to forward tweet {tweet.id} from @{handle}: {exc}")


def _resolve_media_urls(tweet, includes: dict) -> list[str]:
    """
    Extract photo URLs from the tweet's media attachments.
    Uses the 'includes' dict returned by the Tweepy expansions response.
    Only returns photos (not videos/GIFs — those can't be sent as InputMediaPhoto).
    """
    if not includes or "media" not in includes:
        return []

    # Build a lookup: media_key → media object
    media_lookup = {m.media_key: m for m in includes["media"]}

    urls = []
    attachments = getattr(tweet, "attachments", None) or {}
    media_keys  = attachments.get("media_keys", []) if isinstance(attachments, dict) else []

    # For Tweepy Response objects, attachments is a dict-like data field
    if hasattr(tweet, "data") and isinstance(tweet.data, dict):
        media_keys = tweet.data.get("attachments", {}).get("media_keys", [])

    for key in media_keys:
        media_obj = media_lookup.get(key)
        if media_obj and media_obj.type == "photo":
            url = getattr(media_obj, "url", None)
            if url:
                urls.append(url)

    return urls


async def _poll_single_account(bot, client: tweepy.Client, handle: str) -> None:
    """
    Poll one X account for new tweets. Called from the JobQueue job.

    Flow:
    1. Resolve the handle to a numeric user ID (cached implicitly by Tweepy).
    2. Fetch up to XFEED_TWEETS_PER_POLL recent tweets, since last seen ID.
    3. If this is the first run (last_id is None): store baseline, don't post.
    4. Otherwise: forward any new tweets oldest-first, update state, save JSON.
    """
    global xfeed_state, xfeed_last_posted

    try:
        # Step 1: resolve handle → user ID
        user_resp = client.get_user(username=handle, user_fields=["id"])
        if not user_resp or not user_resp.data:
            logger.warning(f"X feed: could not resolve user @{handle}")
            return
        user_id = user_resp.data.id

        last_id = xfeed_state.get(handle)  # str | None

        # Step 2: fetch recent tweets
        # expansions=attachments.media_keys lets us retrieve photo URLs
        fetch_kwargs: dict = dict(
            id=user_id,
            max_results=XFEED_TWEETS_PER_POLL,
            tweet_fields=["id", "text", "attachments", "created_at"],
            expansions=["attachments.media_keys"],
            media_fields=["type", "url", "media_key"],
            exclude=["retweets", "replies"],  # only original tweets + quotes
        )
        if last_id:
            fetch_kwargs["since_id"] = last_id

        response = client.get_users_tweets(**fetch_kwargs)

        if not response or not response.data:
            logger.info(f"X feed: no new tweets for @{handle}")
            return

        tweets   = response.data      # list of Tweet objects, newest first
        includes = response.includes or {}

        # Step 3: first-run baseline — store latest ID, do NOT post
        if last_id is None:
            newest_id = str(tweets[0].id)
            xfeed_state[handle] = newest_id
            _xfeed_save_state()
            logger.info(
                f"X feed: baseline set for @{handle} — latest tweet ID {newest_id} "
                f"(no posts forwarded on first run)"
            )
            return

        # Step 4: forward new tweets oldest-first (reverse the newest-first list)
        new_tweets = list(reversed(tweets))
        for tweet in new_tweets:
            media_urls = _resolve_media_urls(tweet, includes)
            await _forward_tweet(bot, handle, tweet, media_urls)

        # Update state to the newest tweet ID we just processed
        newest_id            = str(tweets[0].id)
        xfeed_state[handle]  = newest_id
        xfeed_last_posted[handle] = datetime.utcnow()
        _xfeed_save_state()

    except tweepy.TooManyRequests:
        logger.warning(f"X feed: rate limited on @{handle} — will retry next cycle")
    except tweepy.Unauthorized:
        logger.error("X feed: bearer token unauthorized — check X_BEARER_TOKEN in .env")
    except tweepy.Forbidden as exc:
        logger.error(f"X feed: forbidden for @{handle} (protected account?): {exc}")
    except tweepy.TwitterServerError as exc:
        logger.error(f"X feed: Twitter server error for @{handle}: {exc}")
    except Exception as exc:
        logger.error(f"X feed: unexpected error polling @{handle}: {exc}", exc_info=True)


async def job_xfeed_poll(context: CallbackContext) -> None:
    """
    JobQueue callback — runs every XFEED_POLL_INTERVAL_SECONDS.
    Iterates over all followed accounts, polling each one with a stagger delay
    to avoid bursting the free-tier rate limit.
    """
    if GROUP_CHAT_ID == 0:
        return

    client = _make_tweepy_client()
    if client is None:
        return  # Bearer token not configured — already warned at startup

    accounts = _xfeed_get_accounts()
    if not accounts:
        return

    logger.info(f"X feed: starting poll cycle for {len(accounts)} account(s)")

    for i, handle in enumerate(accounts):
        await _poll_single_account(context.bot, client, handle)
        # Stagger between accounts (skip delay after the last one)
        if i < len(accounts) - 1:
            await asyncio.sleep(XFEED_STAGGER_SECONDS)

    logger.info("X feed: poll cycle complete")


# ──────────────────────────────────────────────
# SCHEDULED JOB CALLBACKS
# All must match signature: async def name(context: CallbackContext)
# Use context.bot to send messages.
# ──────────────────────────────────────────────

async def job_daily_meme_drop(context: CallbackContext) -> None:
    """9:00 AM UTC — random fact / meme / prompt drop."""
    if GROUP_CHAT_ID == 0:
        return

    content_type = random.choice(["fact", "meme", "prompt"])

    if content_type == "fact":
        text = f"🌄 MORNING YAKK FACT 😈\n\n{random.choice(YAK_FACTS)}\n\nGET YAKKED. 😈\n\n#YAKK #gm"
    elif content_type == "meme":
        text = f"💜 DAILY MEME DROP:\n\n{random.choice(MEME_FORMATS)}\n\n#YAKK"
    else:
        scene  = random.choice(PROMPT_SCENES)
        prompt = scene["template"].format(yakk=YAKK_CORE_DESCRIPTOR)
        text   = (
            f"🎨 YAKK STUDIOS DAILY PROMPT — {scene['label']}:\n\n"
            f"`{prompt}`\n\n"
            "Generate in Midjourney and drop it here. Best gets featured. GET YAKKED. 😈💜"
        )

    await context.bot.send_message(chat_id=GROUP_CHAT_ID, text=text, parse_mode="Markdown")
    logger.info(f"Daily meme drop sent: {content_type}")


async def job_daily_stake_reminder(context: CallbackContext) -> None:
    """12:00 PM UTC — rotating stake hype + pool link + disclaimer."""
    if GROUP_CHAT_ID == 0:
        return

    stake_reminder_variants = [
        (
            "🔒💜 MIDDAY STAKE CHECK 💜🔒\n\n"
            "pink cult — are your $YAKK bags locked in?\n\n"
            "Staking rewards are FLOWING right now 🌊\n"
            "→ $YAKK reflections hitting wallets\n"
            "→ $SPT dripping on top\n"
            "→ APR fluctuates — check live on the site 👀\n\n"
            "🔮 NFT staking drops incoming for the locked beasts.\n"
            f"Get {NFT_WL_MIN_STAKE:,}+ staked now or miss the WL throne. 👑\n\n"
            f"🔗 {STAKEPOINT_POOL_URL}\n"
            f"🌐 {STAKEPOINT_SITE_URL}\n\n"
            f"{STAKE_DISCLAIMER}"
        ),
        (
            "😈🏔️ THE MOUNTAIN PAYS DIVIDENDS 🏔️😈\n\n"
            "Every hour you're not staked is an hour the mountain didn't pay you.\n\n"
            "Live now on StakePoint:\n"
            "💰 $YAKK reflection rewards\n"
            "💎 $SPT bonus drip\n"
            "👑 NFT staking + WL perks incoming\n\n"
            "Lock in. Earn. Ascend. 😈💜\n\n"
            f"🔗 {STAKEPOINT_POOL_URL}\n"
            f"🌐 {STAKEPOINT_SITE_URL}\n\n"
            f"{STAKE_DISCLAIMER}"
        ),
        (
            "💜🔥 PASSIVE INCOME IS A PINK YAKK CONCEPT 🔥💜\n\n"
            "You hold $YAKK. You stake $YAKK. $YAKK pays you. The cycle is complete.\n\n"
            "Check current APR at StakePoint — it spikes. Hard. 🔥\n"
            "NFT Gen III holders: staking perks + exclusive drops are coming for you.\n"
            f"WL mint requires {NFT_WL_MIN_STAKE:,}+ $YAKK staked + Genesis hold.\n\n"
            "No stake = no throne. The yakk doesn't negotiate. 😈\n\n"
            f"🔗 {STAKEPOINT_POOL_URL}\n"
            f"🌐 {STAKEPOINT_SITE_URL}\n\n"
            f"{STAKE_DISCLAIMER}"
        ),
    ]

    await context.bot.send_message(
        chat_id=GROUP_CHAT_ID, text=random.choice(stake_reminder_variants)
    )
    logger.info("Daily stake reminder sent")


async def job_weekly_raid_reminder(context: CallbackContext) -> None:
    """Fridays 2:00 PM UTC — raid sync + stake CTA."""
    if GROUP_CHAT_ID == 0:
        return
    await context.bot.send_message(
        chat_id=GROUP_CHAT_ID,
        text=(
            "😈 WEEKLY RAID SYNC 💜\n\n"
            "It's raid day, herd.\n\n"
            "Find a $YAKK post on X and use /raid [link] to coordinate.\n"
            "Type /done after raiding to log your Raid XP. 🏅\n"
            "Check /leaderboard to see your rank. 🏔️\n\n"
            "💰 While you wait between raids — are your bags staked?\n"
            f"Reflections + $SPT rewards running live: {STAKEPOINT_POOL_URL}\n\n"
            "GET YAKKED. 😈\n\n#YAKK #raidtime #stakeandraid"
        ),
    )
    logger.info("Weekly raid reminder sent")


async def job_check_price_alert(context: CallbackContext) -> None:
    """Every 15 min — compare price to last snapshot; alert if ±10%."""
    global last_price_snapshot

    price, _, dex_url = await _fetch_yakk_price()
    if price is None or price == 0:
        logger.warning("Price alert job: fetch returned nothing, skipping.")
        return

    now = datetime.utcnow()

    if not last_price_snapshot:
        last_price_snapshot = {"price": price, "timestamp": now}
        logger.info(f"Price baseline set: ${price:.8f}")
        return

    prev_price = last_price_snapshot["price"]
    pct_change = ((price - prev_price) / prev_price) * 100
    logger.info(f"Price check: ${price:.8f} ({pct_change:+.2f}% vs snapshot)")

    if abs(pct_change) >= 10 and GROUP_CHAT_ID != 0:
        if pct_change > 0:
            banner = "😈📈🚀💜🏔️🔥🟢💎" * 3
            alert  = (
                f"{banner}\n\n"
                f"🚨 $YAKK IS PUMPING 🚨\n\n"
                f"⬆️ +{pct_change:.1f}% in 15 minutes\n"
                f"Price: ${price:.8f}\n\n"
                f"THE MOUNTAIN IS ASCENDING 🏔️💜\n"
                f"HERD ASSEMBLE. SHARE THIS. RAID EVERYTHING.\nGET YAKKED. 😈\n\n"
                f"🔗 {dex_url}\n\n"
                f"{banner}"
            )
        else:
            banner = "😈📉💜🏔️🐻🔴❄️⛰️" * 3
            alert  = (
                f"{banner}\n\n"
                f"⚠️ $YAKK DIP ALERT ⚠️\n\n"
                f"⬇️ {pct_change:.1f}% in 15 minutes\n"
                f"Price: ${price:.8f}\n\n"
                f"😈 The yakk has survived -40°C. It has survived this before.\n"
                f"DIP = DISCOUNT. The mountain does not flinch.\n"
                f"Diamond hooves only. 💜 GET YAKKED. 😈\n\n"
                f"🔗 {dex_url}\n\n"
                f"{banner}"
            )
        await context.bot.send_message(chat_id=GROUP_CHAT_ID, text=alert)
        logger.info(f"Price alert sent: {'UP' if pct_change > 0 else 'DOWN'} {pct_change:+.1f}%")

    last_price_snapshot = {"price": price, "timestamp": now}


# ──────────────────────────────────────────────
# HELPERS
# ──────────────────────────────────────────────

def _add_xp(user, xp: int) -> None:
    """Add raid XP and cache display name."""
    raid_leaderboard[user.id] += xp
    user_display_names[user.id] = user.username or user.first_name or f"Degen#{user.id}"


# ──────────────────────────────────────────────
# MAIN
# ──────────────────────────────────────────────

def main() -> None:
    # Load X feed state from disk before anything else
    _xfeed_load_state()
    # Seed any accounts from .env that aren't already in state
    _xfeed_get_accounts()

    # Build app — JobQueue is enabled automatically when the [job-queue] extra is installed
    app = Application.builder().token(BOT_TOKEN).build()

    # ── Command handlers ──────────────────────────
    app.add_handler(CommandHandler("start",        cmd_start))
    app.add_handler(CommandHandler("help",         cmd_help))
    app.add_handler(CommandHandler("raid",         cmd_raid))
    app.add_handler(CommandHandler("done",         cmd_done))
    app.add_handler(CommandHandler("leaderboard",  cmd_leaderboard))
    app.add_handler(CommandHandler("stake",        cmd_stake))
    app.add_handler(CommandHandler("fact",         cmd_fact))
    app.add_handler(CommandHandler("meme",         cmd_meme))
    app.add_handler(CommandHandler("prompt",       cmd_prompt))
    app.add_handler(CommandHandler("price",        cmd_price))
    app.add_handler(CommandHandler("yakk",         cmd_yakk))
    app.add_handler(CommandHandler("roast",        cmd_roast))
    # X Feed commands (v5.2)
    app.add_handler(CommandHandler("xfeeds",       cmd_xfeeds))
    app.add_handler(CommandHandler("addxfeed",     cmd_addxfeed))
    app.add_handler(CommandHandler("removexfeed",  cmd_removexfeed))

    # ── Message handlers ──────────────────────────
    app.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, handle_new_chat_member))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, moderate_message))

    # ── Scheduled jobs via JobQueue ───────────────
    # PTB's JobQueue runs inside the same event loop as run_polling — no race condition.
    jq = app.job_queue

    # Daily meme drop — 9:00 AM UTC every day
    jq.run_daily(
        job_daily_meme_drop,
        time=dtime(hour=9, minute=0),
        name="daily_meme_drop",
    )

    # Daily stake reminder — 12:00 PM UTC every day
    jq.run_daily(
        job_daily_stake_reminder,
        time=dtime(hour=12, minute=0),
        name="daily_stake_reminder",
    )

    # Weekly raid reminder — Fridays 2:00 PM UTC
    # days=(4,) → 0=Mon … 4=Fri  (matches Python's weekday convention)
    jq.run_daily(
        job_weekly_raid_reminder,
        time=dtime(hour=14, minute=0),
        days=(4,),
        name="weekly_raid_reminder",
    )

    # Price alert — every 15 minutes
    jq.run_repeating(
        job_check_price_alert,
        interval=900,   # seconds
        first=60,       # first run 60s after startup to let baseline establish
        name="price_alert",
    )

    # X Feed Forwarder — every 10 minutes (v5.2)
    # first=90 gives the bot time to fully start before the first API call
    if X_BEARER_TOKEN:
        jq.run_repeating(
            job_xfeed_poll,
            interval=XFEED_POLL_INTERVAL_SECONDS,
            first=90,
            name="xfeed_poll",
        )
        logger.info(
            f"X feed forwarder scheduled: {len(_xfeed_get_accounts())} account(s), "
            f"every {XFEED_POLL_INTERVAL_SECONDS // 60} min"
        )
    else:
        logger.warning(
            "X_BEARER_TOKEN not set — X feed forwarder will NOT run. "
            "Add X_BEARER_TOKEN to your .env to enable it."
        )

    print("😈💜 $YAKK Bot v5.2 is running. Pink fur. Gold drip. Staking live. X feeds active. GET YAKKED. 😈🏔️")
    logger.info("$YAKK Bot v5.2 started — GET YAKKED. 😈 JobQueue active, polling started.")

    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
