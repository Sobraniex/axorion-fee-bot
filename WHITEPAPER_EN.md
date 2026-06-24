# Axorion Fee Bot — Whitepaper (Plain English)

*A Telegram bot that tells you the second a Solana wallet collects its fees.*

---

## 1. The problem (in one sentence)

If you have a memecoin or a project on Solana, there's usually a **wallet that
collects fees**. Today, the only way to know it got paid is to keep refreshing a
block explorer like a maniac. That's annoying and you miss things.

## 2. What this bot does

You give the bot a wallet address. From then on, **the moment that wallet
receives SOL, your phone buzzes** with a Telegram message like:

> 💰 **Creator fee collected** (pump.fun · PumpSwap AMM)
> Wallet `4Nd1…x9Qd` just received **0.842000 SOL**
> [View transaction](https://solscan.io)

That's the whole product. No dashboard to log into, no refreshing. The
notification comes to you.

## 3. How it works (no tech background needed)

Think of the Solana blockchain as a giant public bank statement that anyone can
read. Every payment is written down forever, and it's all public.

The bot does what a careful assistant would do:

1. **You hand it a wallet address** (by tapping a button or pasting it).
2. **Every ~30 seconds**, the bot peeks at that wallet's latest activity on the
   public Solana record.
3. For each new transaction, it asks two questions:
   - *"Is this the pump.fun 'collect creator fee' action?"* (it recognises the
     exact instruction — both for newer "graduated" coins on PumpSwap and older
     bonding-curve coins), **and**
   - *"Did this wallet's SOL go up as a result?"*
   - Both yes → a creator fee was just claimed.
   - Otherwise → stay quiet (a normal buy, sell, or random deposit is ignored).
4. If a fee was claimed, it **sends you a Telegram message** with the exact
   amount and a link to the receipt (the transaction on Solscan).

There's no guessing and no insider access. It reads the public record and
recognises the specific "collect creator fee" action.

## 4. Using it — there are buttons!

You don't need to memorise commands. Send `/start` and you get a menu:

- **➕ Track a wallet** — tap it, then paste the wallet address.
- **📋 My wallets** — see everything you're watching; each one has a **🗑 Remove**
  button.
- **❓ Help** — a short explainer.

Prefer typing? Power-user commands still work:
`/track <wallet>`, `/untrack <wallet>`, `/list`.

## 5. What it does NOT do (honesty section)

- It does **not** touch your money or your keys. It only *reads* public data. It
  literally cannot move funds — it never asks for a private key.
- It does **not** predict anything. It reports what already happened.
- It fires **only on real pump.fun creator-fee claims** — the actual
  `collect_coin_creator_fee` / `collect_creator_fee` instruction. Ordinary
  deposits, buys, and sells do not trigger an alert.

## 6. Settings you can tune

- **Quiet down small amounts:** set a minimum, e.g. only ping me for fees above
  0.1 SOL.
- **Check faster or slower:** the 30-second interval is adjustable.
- **Reliability:** the bot reads Solana through an "RPC" (the doorway to the
  blockchain). The free public doorway is slow and crowded; plugging in a free
  account from a provider like Helius makes alerts fast and dependable.

## 7. Under the hood (one paragraph for the curious)

Written in Python. It uses Telegram's official bot library for the chat and
buttons, and talks to Solana with two standard read-only calls:
`getSignaturesForAddress` (what just happened to this wallet?) and
`getTransaction` (give me the details). The balance-difference logic is a single
small function — and it's covered by automated tests so it can't silently break.

## 8. Is it safe / does it work?

The core "did a fee land?" logic is **unit-tested and was checked live against
the real Solana mainnet** — it correctly reads wallet activity and computes the
SOL change. The Telegram side runs on Telegram's own infrastructure once you
plug in a bot token (free, from @BotFather).

---

*This document is intentionally non-technical. The full source code and setup
guide live in the same repository.*
