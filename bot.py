"""
Solana fee-wallet notifier — a Telegram bot.

A user gives the bot a Solana wallet address. The bot watches that wallet, and
whenever the wallet RECEIVES SOL (i.e. it "takes the fee"), everyone subscribed
to that wallet gets a Telegram notification with the amount and a Solscan link.

Detection is intentionally simple and chain-honest: for every new transaction
that touches a tracked wallet, we compute the wallet's net SOL balance change
(postBalance - preBalance). A positive change = the wallet got paid = fee taken.

Commands:
  /start            – intro
  /track <wallet>   – start watching a wallet in this chat
  /untrack <wallet> – stop watching it here
  /list             – wallets watched in this chat
  /help             – help
"""

import asyncio
import json
import logging
import os
from pathlib import Path

import httpx
from dotenv import load_dotenv
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
)

load_dotenv()

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
RPC_URL = os.environ.get("SOLANA_RPC_URL", "https://api.mainnet-beta.solana.com").strip()
POLL_INTERVAL = int(os.environ.get("POLL_INTERVAL", "30"))
MIN_SOL = float(os.environ.get("MIN_SOL", "0"))

LAMPORTS_PER_SOL = 1_000_000_000
DB_PATH = Path(__file__).with_name("subscriptions.json")

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


async def get_sol_delta(client, signature, wallet):
    """
    Net SOL change for `wallet` in this transaction.
    Positive  -> wallet received SOL (took a fee / got paid).
    """
    tx = await rpc(
        client,
        "getTransaction",
        [signature, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}],
    )
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

            # First time we see this wallet: just set a baseline, don't replay history.
            if last_seen is None:
                info["last_sig"] = sigs[0]["signature"]
                continue

            # Process oldest -> newest so notifications arrive in order.
            for s in reversed(fresh):
                if s.get("err"):  # skip failed txs
                    continue
                try:
                    delta = await get_sol_delta(client, s["signature"], wallet)
                except Exception as e:  # noqa: BLE001
                    log.warning("tx fetch failed %s: %s", s["signature"], e)
                    continue

                if delta > MIN_SOL and delta > 0:
                    await notify(context, info["subscribers"], wallet, delta, s["signature"])

            info["last_sig"] = sigs[0]["signature"]

    save_db(db)


async def notify(context, subscribers, wallet, amount, signature):
    short = f"{wallet[:4]}…{wallet[-4:]}"
    text = (
        f"💰 *Fee collected*\n"
        f"Wallet `{short}` just received *{amount:.6f} SOL*\n\n"
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
# Command handlers
# --------------------------------------------------------------------------- #
def looks_like_wallet(s: str) -> bool:
    # Base58, 32–44 chars. Good enough to reject obvious junk.
    return 32 <= len(s) <= 44 and all(
        c not in "0OIl" for c in s
    ) and s.isalnum()


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 I watch Solana wallets and ping you when one *receives* SOL "
        "(i.e. takes a fee).\n\n"
        "Use `/track <wallet_address>` to start.\n"
        "`/help` for everything else.",
        parse_mode=ParseMode.MARKDOWN,
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "*Commands*\n"
        "`/track <wallet>` – watch a wallet in this chat\n"
        "`/untrack <wallet>` – stop watching it here\n"
        "`/list` – wallets watched in this chat\n",
        parse_mode=ParseMode.MARKDOWN,
    )


async def cmd_track(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: `/track <wallet_address>`",
                                        parse_mode=ParseMode.MARKDOWN)
        return
    wallet = context.args[0].strip()
    if not looks_like_wallet(wallet):
        await update.message.reply_text("That doesn't look like a Solana address. 🤔")
        return

    chat_id = update.effective_chat.id
    db = load_db()
    entry = db.setdefault(wallet, {"subscribers": [], "last_sig": None})
    if chat_id in entry["subscribers"]:
        await update.message.reply_text("Already watching that wallet here. ✅")
        return
    entry["subscribers"].append(chat_id)
    save_db(db)
    await update.message.reply_text(
        f"🔭 Now watching `{wallet[:4]}…{wallet[-4:]}`.\n"
        "I'll ping you when it receives SOL.",
        parse_mode=ParseMode.MARKDOWN,
    )


async def cmd_untrack(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: `/untrack <wallet_address>`",
                                        parse_mode=ParseMode.MARKDOWN)
        return
    wallet = context.args[0].strip()
    chat_id = update.effective_chat.id
    db = load_db()
    entry = db.get(wallet)
    if not entry or chat_id not in entry["subscribers"]:
        await update.message.reply_text("You weren't watching that one here.")
        return
    entry["subscribers"].remove(chat_id)
    if not entry["subscribers"]:
        db.pop(wallet)  # nobody cares anymore — stop polling it
    save_db(db)
    await update.message.reply_text("🛑 Stopped watching it here.")


async def cmd_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    db = load_db()
    mine = [w for w, info in db.items() if chat_id in info["subscribers"]]
    if not mine:
        await update.message.reply_text("You're not watching any wallets here yet.")
        return
    lines = "\n".join(f"• `{w}`" for w in mine)
    await update.message.reply_text(f"*Watching here:*\n{lines}",
                                    parse_mode=ParseMode.MARKDOWN)


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #
def main():
    if not TOKEN:
        raise SystemExit("Set TELEGRAM_BOT_TOKEN in your environment / .env file.")

    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("track", cmd_track))
    app.add_handler(CommandHandler("untrack", cmd_untrack))
    app.add_handler(CommandHandler("list", cmd_list))

    # Poll wallets on a repeating job.
    app.job_queue.run_repeating(poll_wallets, interval=POLL_INTERVAL, first=5)

    log.info("Bot started. Polling every %ss against %s", POLL_INTERVAL, RPC_URL)
    app.run_polling()


if __name__ == "__main__":
    main()
