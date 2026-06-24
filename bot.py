"""
Solana fee-wallet notifier — a Telegram bot.

Give the bot a Solana wallet address. The bot watches that wallet, and whenever
the wallet RECEIVES SOL (i.e. it "takes the fee"), everyone subscribed to that
wallet gets a Telegram notification with the amount and a Solscan link.

Two ways to use it:
  • Tap buttons (normie-friendly menu) — just send /start.
  • Type slash commands (for power users): /track, /untrack, /list, /help.

Detection is simple and chain-honest: for every new transaction that touches a
tracked wallet, we compute the wallet's net SOL balance change
(postBalance - preBalance). A positive change = the wallet got paid = fee taken.
"""

import json
import logging
import os
from pathlib import Path

import httpx
from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

load_dotenv()

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
RPC_URL = os.environ.get("SOLANA_RPC_URL", "https://api.mainnet-beta.solana.com").strip()
POLL_INTERVAL = int(os.environ.get("POLL_INTERVAL", "30"))
MIN_SOL = float(os.environ.get("MIN_SOL", "0"))

LAMPORTS_PER_SOL = 1_000_000_000
DB_PATH = Path(__file__).with_name("subscriptions.json")

# --- pump.fun creator-fee detection --------------------------------------- #
# A "creator fee claim" is a transaction that invokes one of pump.fun's
# programs with its creator-fee-collect instruction. We fingerprint those
# instructions by program ID + the 8-byte Anchor discriminator (the unique
# hash prefix of the instruction name). This is what makes the bot fire on
# REAL fee claims instead of any random SOL deposit.
PUMP_BONDING_PROGRAM = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"
PUMP_AMM_PROGRAM = "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA"
PUMP_PROGRAMS = {PUMP_BONDING_PROGRAM, PUMP_AMM_PROGRAM}

# discriminator bytes -> human label
#   collect_coin_creator_fee  (PumpSwap AMM, migrated coins)
#   collect_creator_fee       (bonding curve, pre-migration coins)
FEE_DISCRIMINATORS = {
    bytes.fromhex("a039592ab58b2b42"): "PumpSwap AMM",
    bytes.fromhex("1416567bc61cdb84"): "bonding curve",
}

_B58_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
_B58_INDEX = {c: i for i, c in enumerate(_B58_ALPHABET)}


def b58decode(s: str) -> bytes:
    """Minimal base58 decode (no external dependency)."""
    n = 0
    for ch in s:
        n = n * 58 + _B58_INDEX[ch]
    body = n.to_bytes((n.bit_length() + 7) // 8, "big") if n else b""
    pad = len(s) - len(s.lstrip("1"))
    return b"\x00" * pad + body

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s", level=logging.INFO
)
log = logging.getLogger("sol-fee-bot")


# --------------------------------------------------------------------------- #
# Tiny JSON "database"
#
# Shape:
# {
#   "<wallet>": {
#       "subscribers": [chat_id, ...],
#       "last_sig": "<most recent signature we've already processed>"
#   }
# }
# --------------------------------------------------------------------------- #
def load_db() -> dict:
    if DB_PATH.exists():
        return json.loads(DB_PATH.read_text())
    return {}


def save_db(db: dict) -> None:
    DB_PATH.write_text(json.dumps(db, indent=2))


def wallets_for_chat(db: dict, chat_id: int) -> list:
    return [w for w, info in db.items() if chat_id in info["subscribers"]]


def short(wallet: str) -> str:
    return f"{wallet[:4]}…{wallet[-4:]}"


def looks_like_wallet(s: str) -> bool:
    # Base58, 32–44 chars. Good enough to reject obvious junk.
    return 32 <= len(s) <= 44 and s.isalnum() and all(c not in "0OIl" for c in s)


# --------------------------------------------------------------------------- #
# Subscription logic (pure-ish, so it's easy to test)
# --------------------------------------------------------------------------- #
def add_subscription(chat_id: int, wallet: str) -> str:
    db = load_db()
    entry = db.setdefault(wallet, {"subscribers": [], "last_sig": None})
    if chat_id in entry["subscribers"]:
        return "already"
    entry["subscribers"].append(chat_id)
    save_db(db)
    return "added"


def remove_subscription(chat_id: int, wallet: str) -> str:
    db = load_db()
    entry = db.get(wallet)
    if not entry or chat_id not in entry["subscribers"]:
        return "missing"
    entry["subscribers"].remove(chat_id)
    if not entry["subscribers"]:
        db.pop(wallet)  # nobody cares anymore — stop polling it
    save_db(db)
    return "removed"


# --------------------------------------------------------------------------- #
# Solana RPC helpers
# --------------------------------------------------------------------------- #
async def rpc(client: httpx.AsyncClient, method: str, params: list):
    resp = await client.post(
        RPC_URL,
        json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    if "error" in data:
        raise RuntimeError(f"RPC {method} error: {data['error']}")
    return data["result"]


async def get_recent_signatures(client, wallet, limit=25):
    """Newest-first list of confirmed signatures touching the wallet."""
    return await rpc(client, "getSignaturesForAddress", [wallet, {"limit": limit}])


def sol_delta_from_tx(tx: dict, wallet: str) -> float:
    """
    Pure function: net SOL change for `wallet` given a getTransaction result.
    Positive -> wallet received SOL. Separated out so it can be unit-tested.
    """
    if not tx or not tx.get("meta"):
        return 0.0
    keys = tx["transaction"]["message"]["accountKeys"]
    pre = tx["meta"]["preBalances"]
    post = tx["meta"]["postBalances"]
    for i, key in enumerate(keys):
        pubkey = key["pubkey"] if isinstance(key, dict) else key
        if pubkey == wallet and i < len(pre) and i < len(post):
            return (post[i] - pre[i]) / LAMPORTS_PER_SOL
    return 0.0


def _iter_instructions(tx: dict):
    """Every instruction in the tx — top-level plus inner (CPI) instructions."""
    msg = tx.get("transaction", {}).get("message", {})
    for ix in msg.get("instructions", []) or []:
        yield ix
    for inner in tx.get("meta", {}).get("innerInstructions", []) or []:
        for ix in inner.get("instructions", []) or []:
            yield ix


def creator_fee_claim_kind(tx: dict):
    """
    Return 'PumpSwap AMM' / 'bonding curve' if this tx contains a pump.fun
    creator-fee-collect instruction, else None. Pure function (testable).
    """
    if not tx:
        return None
    for ix in _iter_instructions(tx):
        if ix.get("programId") not in PUMP_PROGRAMS:
            continue
        data = ix.get("data")
        if not data:
            continue
        try:
            disc = b58decode(data)[:8]
        except Exception:  # noqa: BLE001 — malformed data, just skip
            continue
        if disc in FEE_DISCRIMINATORS:
            return FEE_DISCRIMINATORS[disc]
    return None


def detect_fee_claim(tx: dict, wallet: str):
    """
    (amount_sol, kind) if `tx` is a pump.fun creator-fee claim that paid SOL to
    `wallet`; otherwise (0.0, None). Pure function (testable, no network).
    """
    kind = creator_fee_claim_kind(tx)
    if not kind:
        return 0.0, None
    return sol_delta_from_tx(tx, wallet), kind


async def get_tx(client, signature):
    return await rpc(
        client,
        "getTransaction",
        [signature, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}],
    )


# --------------------------------------------------------------------------- #
# Background poller (runs on the bot's job queue)
# --------------------------------------------------------------------------- #
async def poll_wallets(context: ContextTypes.DEFAULT_TYPE):
    db = load_db()
    if not db:
        return

    async with httpx.AsyncClient() as client:
        for wallet, info in db.items():
            try:
                sigs = await get_recent_signatures(client, wallet)
            except Exception as e:  # noqa: BLE001 — keep the loop alive
                log.warning("sig fetch failed for %s: %s", wallet, e)
                continue
            if not sigs:
                continue

            last_seen = info.get("last_sig")
            fresh = []
            for s in sigs:  # newest first
                if s["signature"] == last_seen:
                    break
                fresh.append(s)

            # First time we see this wallet: set a baseline, don't replay history.
            if last_seen is None:
                info["last_sig"] = sigs[0]["signature"]
                continue

            # Process oldest -> newest so notifications arrive in order.
            for s in reversed(fresh):
                if s.get("err"):  # skip failed txs
                    continue
                try:
                    tx = await get_tx(client, s["signature"])
                except Exception as e:  # noqa: BLE001
                    log.warning("tx fetch failed %s: %s", s["signature"], e)
                    continue
                amount, kind = detect_fee_claim(tx, wallet)
                if kind and amount > MIN_SOL and amount > 0:
                    await notify(context, info["subscribers"], wallet, amount,
                                 s["signature"], kind)

            info["last_sig"] = sigs[0]["signature"]

    save_db(db)


async def notify(context, subscribers, wallet, amount, signature, kind):
    text = (
        f"💰 *Creator fee collected* (pump.fun · {kind})\n"
        f"Wallet `{short(wallet)}` just received *{amount:.6f} SOL*\n\n"
        f"[View transaction](https://solscan.io/tx/{signature})"
    )
    for chat_id in subscribers:
        try:
            await context.bot.send_message(
                chat_id=chat_id,
                text=text,
                parse_mode=ParseMode.MARKDOWN,
                disable_web_page_preview=True,
            )
        except Exception as e:  # noqa: BLE001
            log.warning("send to %s failed: %s", chat_id, e)


# --------------------------------------------------------------------------- #
# UI — buttons
# --------------------------------------------------------------------------- #
def main_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("➕ Track a wallet", callback_data="track")],
            [InlineKeyboardButton("📋 My wallets", callback_data="list")],
            [InlineKeyboardButton("❓ Help", callback_data="help")],
        ]
    )


WELCOME = (
    "👋 *pump.fun Creator-Fee Notifier*\n\n"
    "I watch creator wallets and ping you the moment one *claims its pump.fun "
    "creator fees*.\n\n"
    "Tap a button below, or type `/track <wallet>`."
)

HELP_TEXT = (
    "*How it works*\n"
    "1. Give me a creator's wallet address.\n"
    "2. I check it every ~30s.\n"
    "3. When that wallet collects pump.fun creator fees, you get a 💰 alert "
    "with the SOL amount + a link.\n\n"
    "I detect the real *collect creator fee* instruction (both bonding-curve "
    "and PumpSwap AMM coins) — not random deposits.\n\n"
    "*Buttons*\n"
    "➕ Track a wallet — then paste the address\n"
    "📋 My wallets — see / remove what you watch\n\n"
    "*Commands (optional)*\n"
    "`/track <wallet>`  `/untrack <wallet>`  `/list`"
)


# --------------------------------------------------------------------------- #
# Command handlers
# --------------------------------------------------------------------------- #
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        WELCOME, parse_mode=ParseMode.MARKDOWN, reply_markup=main_menu()
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(HELP_TEXT, parse_mode=ParseMode.MARKDOWN)


async def cmd_track(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        context.user_data["awaiting_wallet"] = True
        await update.message.reply_text("Paste the Solana wallet address you want to watch 👇")
        return
    await do_track(update.effective_chat.id, context.args[0].strip(),
                   lambda t, **k: update.message.reply_text(t, **k))


async def cmd_untrack(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: `/untrack <wallet>`", parse_mode=ParseMode.MARKDOWN)
        return
    res = remove_subscription(update.effective_chat.id, context.args[0].strip())
    msg = {"removed": "🛑 Stopped watching it.", "missing": "You weren't watching that one here."}[res]
    await update.message.reply_text(msg)


async def cmd_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await send_wallet_list(update.effective_chat.id,
                           lambda t, **k: update.message.reply_text(t, **k))


# --------------------------------------------------------------------------- #
# Shared helpers used by both buttons and commands
# --------------------------------------------------------------------------- #
async def do_track(chat_id, wallet, reply):
    if not looks_like_wallet(wallet):
        await reply("That doesn't look like a Solana address. 🤔 Try again.")
        return
    res = add_subscription(chat_id, wallet)
    if res == "already":
        await reply("Already watching that wallet. ✅")
    else:
        await reply(f"🔭 Now watching `{short(wallet)}`.\nI'll ping you when it receives SOL.",
                    parse_mode=ParseMode.MARKDOWN)


async def send_wallet_list(chat_id, reply):
    db = load_db()
    mine = wallets_for_chat(db, chat_id)
    if not mine:
        await reply("You're not watching any wallets yet.\nTap ➕ to add one.",
                    reply_markup=main_menu())
        return
    rows = [[InlineKeyboardButton(f"🗑 Remove {short(w)}", callback_data=f"rm:{w}")] for w in mine]
    rows.append([InlineKeyboardButton("➕ Track another", callback_data="track")])
    await reply("*Wallets you're watching:*", parse_mode=ParseMode.MARKDOWN,
                reply_markup=InlineKeyboardMarkup(rows))


# --------------------------------------------------------------------------- #
# Button (callback) + free-text handlers
# --------------------------------------------------------------------------- #
async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    chat_id = query.message.chat.id

    if data == "track":
        context.user_data["awaiting_wallet"] = True
        await query.message.reply_text("Paste the Solana wallet address you want to watch 👇")
    elif data == "list":
        await send_wallet_list(chat_id, query.message.reply_text)
    elif data == "help":
        await query.message.reply_text(HELP_TEXT, parse_mode=ParseMode.MARKDOWN)
    elif data.startswith("rm:"):
        wallet = data[3:]
        remove_subscription(chat_id, wallet)
        await query.edit_message_text(f"🛑 Stopped watching `{short(wallet)}`.",
                                      parse_mode=ParseMode.MARKDOWN)


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Catches a pasted address right after the user taps ➕ Track."""
    if context.user_data.get("awaiting_wallet"):
        context.user_data["awaiting_wallet"] = False
        await do_track(update.effective_chat.id, update.message.text.strip(),
                       update.message.reply_text)
    else:
        await update.message.reply_text("Tap a button or send /start.", reply_markup=main_menu())


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #
def build_app() -> Application:
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("track", cmd_track))
    app.add_handler(CommandHandler("untrack", cmd_untrack))
    app.add_handler(CommandHandler("list", cmd_list))
    app.add_handler(CallbackQueryHandler(on_button))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    app.job_queue.run_repeating(poll_wallets, interval=POLL_INTERVAL, first=5)
    return app


def main():
    if not TOKEN:
        raise SystemExit("Set TELEGRAM_BOT_TOKEN in your environment / .env file.")
    app = build_app()
    log.info("Bot started. Polling every %ss against %s", POLL_INTERVAL, RPC_URL)
    app.run_polling()


if __name__ == "__main__":
    main()
