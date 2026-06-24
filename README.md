# Solana fee-wallet notifier (Telegram bot)

Give the bot a Solana wallet address. Whenever that wallet **receives SOL**
(i.e. "takes the fee"), everyone subscribed to it gets a Telegram ping with the
amount and a Solscan link.

## How it works

A background job polls each tracked wallet's recent transactions. For every new
transaction it computes the wallet's net SOL change (`postBalance - preBalance`).
A positive change means the wallet got paid → notification fires.

## Setup

1. **Create a bot** — message [@BotFather](https://t.me/BotFather) on Telegram,
   send `/newbot`, copy the token.

2. **Install + configure**
   ```bash
   cd sol-fee-bot
   python3 -m venv venv && source venv/bin/activate
   pip install -r requirements.txt
   cp .env.example .env        # then edit .env, paste your bot token
   ```

3. **Run**
   ```bash
   python bot.py
   ```

4. **Use it** — in Telegram, message your bot:
   ```
   /track 4Nd1mY...the_fee_wallet...x9Qd
   ```
   That's it. You'll get a message the moment that wallet collects a fee.

## Commands

| Command            | What it does                          |
|--------------------|---------------------------------------|
| `/track <wallet>`  | Watch a wallet in this chat           |
| `/untrack <wallet>`| Stop watching it here                 |
| `/list`            | Wallets watched in this chat          |
| `/help`            | Help                                  |

## Notes / tuning

- **RPC endpoint:** the default public `api.mainnet-beta.solana.com` is heavily
  rate-limited. For real use, drop a free [Helius](https://helius.dev) /
  QuickNode / Alchemy URL into `SOLANA_RPC_URL`.
- **Noise control:** set `MIN_SOL` in `.env` to ignore tiny inflows.
- **`POLL_INTERVAL`:** seconds between checks (default 30). Lower = faster
  alerts but more RPC calls.
- **Storage:** subscriptions live in `subscriptions.json` next to the script.

## What counts as "a fee"?

Right now: **any SOL the wallet receives** (above `MIN_SOL`). This is the
robust, chain-agnostic definition and covers pump.fun creator-fee claims, since
those land SOL in the wallet. If you later want to *only* fire on a specific
pump.fun "collect creator fee" instruction (and ignore other deposits), that's a
targeted upgrade to `get_sol_delta` — say the word.
