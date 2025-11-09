import asyncio
import logging
import re
import aiosqlite
import aiohttp
import os
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandObject
from aiogram.types import Message, InlineKeyboardButton, InlineKeyboardMarkup, CallbackQuery
from datetime import datetime, timedelta

# ==========================
# CONFIG
# ==========================
logging.basicConfig(level=logging.INFO)
load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("❌ BOT_TOKEN not found! Please set it in your .env file.")

DB_PATH = "db.sqlite3"
MIN_WITHDRAWAL = 10
GROUP_USERNAME = "ffesportschallenges"
ADMIN_IDS = [7139153880]

bot = Bot(BOT_TOKEN)
dp = Dispatcher()

# ==========================
# DATABASE INIT
# ==========================
async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
        CREATE TABLE IF NOT EXISTS users (
            tg_id INTEGER PRIMARY KEY,
            username TEXT,
            referrer_id INTEGER,
            balance REAL DEFAULT 0,
            total_referrals INTEGER DEFAULT 0,
            created_at TEXT,
            verified_ip TEXT,
            upi_id TEXT,
            last_bonus_date TEXT,
            joined_group INTEGER DEFAULT 0,
            got_welcome_bonus INTEGER DEFAULT 0,
            ref_bonus_given INTEGER DEFAULT 0
        )
        """)
        await db.execute("""
        CREATE TABLE IF NOT EXISTS withdrawals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            amount INTEGER,
            upi_id TEXT,
            status TEXT,
            created_at TEXT
        )
        """)
        await db.commit()

# ==========================
# INLINE BUTTONS
# ==========================
def join_buttons():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🚀 Join Group", url=f"https://t.me/{GROUP_USERNAME}")],
        [InlineKeyboardButton(text="✅ Continue", callback_data="check_join")]
    ])

def main_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="💰 Balance", callback_data="cmd_balance"),
            InlineKeyboardButton(text="🎁 Daily Bonus", callback_data="cmd_daily")
        ],
        [
            InlineKeyboardButton(text="💳 Bind UPI", callback_data="cmd_bindupi"),
            InlineKeyboardButton(text="📤 Withdraw", callback_data="cmd_withdraw")
        ]
    ])

# ==========================
# START
# ==========================
@dp.message(Command("start"))
async def start_cmd(m: Message, command: CommandObject):
    await init_db()
    referrer_id = int(command.args) if command.args and command.args.isdigit() else None

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT OR IGNORE INTO users (tg_id, username, created_at)
            VALUES (?, ?, ?)
        """, (m.from_user.id, m.from_user.username, datetime.utcnow().isoformat()))

        if referrer_id and referrer_id != m.from_user.id:
            cur = await db.execute("SELECT referrer_id FROM users WHERE tg_id=?", (m.from_user.id,))
            row = await cur.fetchone()
            if row and not row[0]:
                await db.execute("UPDATE users SET referrer_id=? WHERE tg_id=?", (referrer_id, m.from_user.id))
        await db.commit()

    ref_link = f"https://t.me/share_and_earn_money_bot?start={m.from_user.id}"
    await m.answer(
        f"👋 Welcome to *FREE FIRE ESPORTS BOT!*\n\n"
        f"Join our group to continue and claim your ₹2 Welcome Bonus 💰\n\n"
        f"👥 Invite friends & earn ₹1 per invite!\n"
        f"🔗 Your referral link: [Click Here]({ref_link})",
        reply_markup=join_buttons(),
        parse_mode="Markdown",
        disable_web_page_preview=True
    )

# ==========================
# CHECK JOIN & VERIFY
# ==========================
@dp.callback_query(F.data == "check_join")
async def check_group_join(callback: CallbackQuery):
    user_id = callback.from_user.id

    # 🔍 Get IP for device verification
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get("https://api.ipify.org?format=json") as resp:
                data = await resp.json()
                user_ip = data.get("ip", f"local-{user_id}")
    except:
        user_ip = f"local-{user_id}"

    # 🧩 Check if user is in group
    try:
        member = await bot.get_chat_member(f"@{GROUP_USERNAME}", user_id)
        if getattr(member, "status", None) not in ["member", "administrator", "creator"]:
            raise Exception("not joined")
    except:
        await callback.message.answer(
            "❌ Please join the group first!\n"
            f"👉 [Join Group](https://t.me/{GROUP_USERNAME})",
            parse_mode="Markdown"
        )
        return

    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("""
            SELECT balance, got_welcome_bonus, referrer_id, ref_bonus_given, verified_ip
            FROM users WHERE tg_id=?
        """, (user_id,))
        row = await cur.fetchone()

        balance = row[0] if row else 0
        got_bonus = int(row[1]) if row else 0
        referrer_id = row[2] if row else None
        ref_bonus_given = int(row[3]) if row else 0
        verified_ip = row[4] if row else None

        # 🚫 Block multi-account on same IP
        cur2 = await db.execute("SELECT tg_id FROM users WHERE verified_ip=? AND tg_id!=?", (user_ip, user_id))
        if await cur2.fetchone():
            await callback.message.answer("⚠️ This device/IP is already verified with another account. Bonus denied.")
            return

        # 🟢 Already verified → still show buttons
        if got_bonus:
            await callback.message.answer(
                "✅ You’re already verified and received your welcome bonus earlier.",
                reply_markup=main_menu()
            )
            return

        # 🎁 First-time verification
        await db.execute("""
            UPDATE users SET balance = balance + 2, got_welcome_bonus=1, verified_ip=?, joined_group=1 WHERE tg_id=?
        """, (user_ip, user_id))
        balance += 2
        await callback.message.answer(f"🎉 Welcome bonus ₹2 added! Your new balance: ₹{balance}")

        # 🎯 Give referral bonus
        if referrer_id and referrer_id != user_id and ref_bonus_given == 0:
            await db.execute("""
                UPDATE users SET balance = balance + 1, total_referrals = total_referrals + 1 WHERE tg_id=?
            """, (referrer_id,))
            await db.execute("UPDATE users SET ref_bonus_given=1 WHERE tg_id=?", (user_id,))
            try:
                await bot.send_message(referrer_id, f"🎉 You earned ₹1 for inviting @{callback.from_user.username or user_id}!")
            except:
                pass
        await db.commit()

    await callback.message.answer(
        "✅ You’re verified and ready to go!\n\nUse these quick access buttons 👇",
        reply_markup=main_menu()
    )

# ==========================
# DAILY BONUS
# ==========================
@dp.message(Command("daily"))
async def daily_bonus(m: Message):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT balance, last_bonus_date FROM users WHERE tg_id=?", (m.from_user.id,))
        row = await cur.fetchone()

        if not row:
            await m.answer("Use /start first.")
            return

        balance, last_bonus = row
        now = datetime.utcnow()

        if last_bonus and now - datetime.fromisoformat(last_bonus) < timedelta(hours=24):
            await m.answer("⏰ You’ve already claimed your daily bonus today.")
            return

        await db.execute("UPDATE users SET balance = balance + 1, last_bonus_date=? WHERE tg_id=?",
                         (now.isoformat(), m.from_user.id))
        await db.commit()

    await m.answer(f"🎁 ₹1 daily bonus added! Your new balance: ₹{balance + 1}")

# ==========================
# BIND UPI
# ==========================
@dp.message(Command("bindupi"))
async def on_bind_upi(m: Message):
    parts = m.text.split(maxsplit=1)
    if len(parts) < 2:
        await m.answer("Usage: /bindupi your_upi@bank")
        return

    upi = parts[1].strip()
    if not re.match(r"^[0-9A-Za-z.\-_]+@[A-Za-z]{2,}$", upi):
        await m.answer("❌ Invalid UPI ID format.")
        return

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET upi_id=? WHERE tg_id=?", (upi, m.from_user.id))
        await db.commit()

    await m.answer(f"✅ UPI saved successfully:\n`{upi}`", parse_mode="Markdown")

# ==========================
# BALANCE
# ==========================
@dp.message(Command("balance"))
async def on_balance(m: Message):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT balance, total_referrals, upi_id FROM users WHERE tg_id=?", (m.from_user.id,))
        row = await cur.fetchone()

    if not row:
        await m.answer("Use /start first.")
        return

    balance, total_referrals, upi_id = row
    ref_link = f"https://t.me/share_and_earn_money_bot?start={m.from_user.id}"
    await m.answer(
        f"💰 Balance: ₹{balance}\n"
        f"👥 Referrals: {total_referrals}\n"
        f"🏦 UPI: {upi_id or 'Not set (use /bindupi)'}\n\n"
        f"🔗 Referral Link: [Click Here]({ref_link})",
        parse_mode="Markdown"
    )

# ==========================
# WITHDRAW
# ==========================
@dp.message(Command("withdraw"))
async def on_withdraw(m: Message):
    parts = m.text.split()
    if len(parts) < 2 or not parts[1].isdigit():
        await m.answer(f"Usage: /withdraw <amount> (min ₹{MIN_WITHDRAWAL})")
        return

    amount = int(parts[1])
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT balance, upi_id FROM users WHERE tg_id=?", (m.from_user.id,))
        row = await cur.fetchone()

    if not row:
        await m.answer("Use /start first.")
        return

    balance, upi_id = row
    if amount < MIN_WITHDRAWAL:
        await m.answer(f"❌ Minimum withdrawal is ₹{MIN_WITHDRAWAL}.")
        return
    if amount > balance:
        await m.answer("❌ Amount exceeds your balance.")
        return
    if not upi_id:
        await m.answer("❌ Please link your UPI first using /bindupi.")
        return

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO withdrawals (user_id, amount, upi_id, status, created_at)
            VALUES (?, ?, ?, 'pending', ?)
        """, (m.from_user.id, amount, upi_id, datetime.utcnow().isoformat()))
        await db.execute("UPDATE users SET balance = balance - ? WHERE tg_id=?", (amount, m.from_user.id))
        await db.commit()

    await m.answer("✅ Withdrawal request submitted!")

# ==========================
# MAIN
# ==========================
async def main():
    print("🚀 Bot is starting...")
    await init_db()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
