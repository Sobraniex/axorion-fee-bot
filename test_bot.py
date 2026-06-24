"""
Automated tests for the bot's pure logic.

These run with NO Telegram token and NO network — they verify the parts that
hold the bot together: address validation, the SOL-delta math that decides
"was a fee collected?", and add/remove subscription bookkeeping.

Run:  python -m pytest test_bot.py -v   (or just: python test_bot.py)
"""

import importlib
import os
import tempfile

# Point the JSON db at a throwaway file before importing the bot.
_tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
_tmp.close()
os.environ["TELEGRAM_BOT_TOKEN"] = "test:token"

import bot  # noqa: E402
bot.DB_PATH = __import__("pathlib").Path(_tmp.name)
if bot.DB_PATH.exists():
    bot.DB_PATH.unlink()


def test_valid_address_passes():
    assert bot.looks_like_wallet("GThUX1Atko4tqhN2NaiTazWSeFWMuiUvfFnyJyUghFMJ")


def test_junk_address_rejected():
    assert not bot.looks_like_wallet("hello")
    assert not bot.looks_like_wallet("0x1234567890abcdef")        # EVM, not Solana
    assert not bot.looks_like_wallet("not a wallet at all!!!")


def test_fee_received_is_positive_delta():
    # Wallet sits at index 1; balance goes 1.0 -> 1.5 SOL = +0.5 received.
    tx = {
        "transaction": {"message": {"accountKeys": [
            {"pubkey": "Sender"}, {"pubkey": "FeeWallet"}]}},
        "meta": {
            "preBalances": [2_000_000_000, 1_000_000_000],
            "postBalances": [1_500_000_000, 1_500_000_000],
        },
    }
    assert bot.sol_delta_from_tx(tx, "FeeWallet") == 0.5


def test_outgoing_is_negative_delta():
    tx = {
        "transaction": {"message": {"accountKeys": [{"pubkey": "FeeWallet"}]}},
        "meta": {"preBalances": [1_000_000_000], "postBalances": [900_000_000]},
    }
    assert bot.sol_delta_from_tx(tx, "FeeWallet") == -0.1


def test_unrelated_wallet_is_zero():
    tx = {
        "transaction": {"message": {"accountKeys": [{"pubkey": "Someone"}]}},
        "meta": {"preBalances": [1_000_000_000], "postBalances": [2_000_000_000]},
    }
    assert bot.sol_delta_from_tx(tx, "FeeWallet") == 0.0


def _b58encode(b: bytes) -> str:
    """Inverse of bot.b58decode — used to build fake instruction data in tests."""
    n = int.from_bytes(b, "big")
    out = ""
    while n:
        n, r = divmod(n, 58)
        out = bot._B58_ALPHABET[r] + out
    pad = len(b) - len(b.lstrip(b"\x00"))
    return "1" * pad + out


def _pump_tx(discriminator_hex: str, program: str, wallet: str,
             pre_lamports: int, post_lamports: int) -> dict:
    data = _b58encode(bytes.fromhex(discriminator_hex) + b"\x00" * 4)
    return {
        "transaction": {"message": {
            "accountKeys": [{"pubkey": wallet}],
            "instructions": [{"programId": program, "accounts": [], "data": data}],
        }},
        "meta": {
            "preBalances": [pre_lamports], "postBalances": [post_lamports],
            "innerInstructions": [],
        },
    }


def test_detects_pumpswap_amm_creator_fee_claim():
    wallet = "GThUX1Atko4tqhN2NaiTazWSeFWMuiUvfFnyJyUghFMJ"
    tx = _pump_tx("a039592ab58b2b42", bot.PUMP_AMM_PROGRAM, wallet,
                  1_000_000_000, 1_700_000_000)  # +0.7 SOL
    amount, kind = bot.detect_fee_claim(tx, wallet)
    assert kind == "PumpSwap AMM"
    assert amount == 0.7


def test_detects_bonding_curve_creator_fee_claim():
    wallet = "GThUX1Atko4tqhN2NaiTazWSeFWMuiUvfFnyJyUghFMJ"
    tx = _pump_tx("1416567bc61cdb84", bot.PUMP_BONDING_PROGRAM, wallet,
                  1_000_000_000, 1_250_000_000)  # +0.25 SOL
    amount, kind = bot.detect_fee_claim(tx, wallet)
    assert kind == "bonding curve"
    assert amount == 0.25


def test_detects_bonding_curve_v2_creator_fee_claim():
    wallet = "GThUX1Atko4tqhN2NaiTazWSeFWMuiUvfFnyJyUghFMJ"
    tx = _pump_tx("cf118af204221338", bot.PUMP_BONDING_PROGRAM, wallet,
                  1_000_000_000, 1_100_000_000)  # +0.1 SOL, collect_creator_fee_v2
    amount, kind = bot.detect_fee_claim(tx, wallet)
    assert kind == "bonding curve"
    assert amount == 0.1


def test_ignores_plain_sol_transfer_not_a_fee_claim():
    # SOL arrives, but NO pump.fun fee instruction -> must NOT fire.
    wallet = "GThUX1Atko4tqhN2NaiTazWSeFWMuiUvfFnyJyUghFMJ"
    tx = {
        "transaction": {"message": {
            "accountKeys": [{"pubkey": wallet}],
            "instructions": [{"programId": "11111111111111111111111111111111",
                              "parsed": {"type": "transfer"}, "program": "system"}],
        }},
        "meta": {"preBalances": [1_000_000_000], "postBalances": [9_000_000_000],
                 "innerInstructions": []},
    }
    amount, kind = bot.detect_fee_claim(tx, wallet)
    assert kind is None and amount == 0.0


def test_ignores_pump_trade_with_wrong_discriminator():
    # A pump.fun program call that is NOT a fee-collect (e.g. a buy) -> ignore.
    wallet = "GThUX1Atko4tqhN2NaiTazWSeFWMuiUvfFnyJyUghFMJ"
    tx = _pump_tx("deadbeefdeadbeef", bot.PUMP_AMM_PROGRAM, wallet,
                  1_000_000_000, 1_500_000_000)
    amount, kind = bot.detect_fee_claim(tx, wallet)
    assert kind is None


def test_b58_roundtrip():
    blob = bytes.fromhex("a039592ab58b2b42") + b"\x01\x02\x03"
    assert bot.b58decode(_b58encode(blob)) == blob


def test_subscribe_then_list_then_remove():
    chat = 12345
    wallet = "GThUX1Atko4tqhN2NaiTazWSeFWMuiUvfFnyJyUghFMJ"

    assert bot.add_subscription(chat, wallet) == "added"
    assert bot.add_subscription(chat, wallet) == "already"        # idempotent
    assert wallet in bot.wallets_for_chat(bot.load_db(), chat)

    assert bot.remove_subscription(chat, wallet) == "removed"
    assert bot.remove_subscription(chat, wallet) == "missing"
    assert wallet not in bot.wallets_for_chat(bot.load_db(), chat)


def test_build_app_wires_buttons_and_commands():
    # Proves the bot constructs and the button handler is registered.
    app = bot.build_app()
    from telegram.ext import CallbackQueryHandler, CommandHandler

    kinds = [type(h) for group in app.handlers.values() for h in group]
    assert CallbackQueryHandler in kinds, "buttons not wired up"
    assert kinds.count(CommandHandler) >= 5, "missing slash commands"


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    passed = 0
    for fn in fns:
        fn()
        print(f"  ✓ {fn.__name__}")
        passed += 1
    print(f"\n{passed}/{len(fns)} tests passed ✅")
