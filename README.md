# pump.fun creator-fee notifier (Telegram bot)

Give the bot a creator's Solana wallet address. Whenever that wallet **claims
its pump.fun creator fees**, everyone subscribed to it gets a Telegram ping with
the SOL amount and a Solscan link.

**Normie-friendly:** it has tap **buttons** (➕ Track, 📋 My wallets, 🗑 Remove) —
no need to memorise commands. See the plain-language whitepapers:
[English](WHITEPAPER_EN.md) · [Slovenščina](WHITEPAPER_SL.md).

## How it works

A background job polls each tracked wallet's recent transactions. For every new
transaction it looks for a **pump.fun creator-fee-collect instruction**, matched
precisely by program ID + the 8-byte Anchor discriminator:

| Coin type            | Program ID                                     | Instruction                |
|----------------------|------------------------------------------------|----------------------------|
| Migrated (PumpSwap)  | `pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA`  | `collect_coin_creator_fee` |
| Bonding curve        | `6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P`  | `collect_creator_fee`      |

When such an instruction is present **and** the watched wallet's SOL balance
went up, that's a real creator-fee claim → notification fires. Plain transfers,
buys, and sells are ignored.

## Tests

```bash
python test_bot.py
```
Covers address validation, the SOL-delta math, **pump.fun claim detection for
both AMM and bonding-curve coins**, that plain transfers / non-claim pump trades
are correctly ignored, base58 decoding, subscription add/remove, and that the
buttons + commands are wired up. The detection was also validated against real
claims on Solana mainnet.

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

**Specifically a pump.fun creator-fee claim** — a transaction that calls
`collect_coin_creator_fee` (PumpSwap AMM) or `collect_creator_fee` (bonding
curve) and credits SOL to the watched wallet. Random deposits, buys, and sells
do **not** trigger a notification. Set `MIN_SOL` to also ignore dust claims.
