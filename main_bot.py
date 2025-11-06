import asyncio
import logging
import re
import aiosqlite
import os
from dotenv import load_dotenv  # ✅ NEW: for environment variables
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandObject
from aiogram.types import Message, InlineKeyboardButton, InlineKeyboardMarkup, CallbackQuery
from datetime import datetime, timedelta

# ==========================
# CONFIG
# ==========================
logging.basicConfig(level=logging.INFO)
load_dotenv()  # ✅ Load .env file

BOT_TOKEN = os.getenv("BOT_TOKEN")  # ✅ Loaded securely from .env file
if not BOT_TOKEN:
    raise ValueError("❌ BOT_TOKEN not found! Please set it in your .env file.")

DB_PATH = "db.sqlite3"
MIN_WITHDRAWAL = 2
GROUP_USERNAME = "ffesportschallenges"
ADMIN_IDS = [7139153880]  # replace with your Telegram ID

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

# ==========================
# START (with referral tracking)
# ==========================
@dp.message(Command("start"))
async def start_cmd(m: Message, command: CommandObject):
    await init_db()
    referrer_id = None
    if command.args and command.args.isdigit():
        referrer_id = int(command.args)

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT OR IGNORE INTO users (tg_id, username, created_at)
            VALUES (?, ?, ?)
        """, (m.from_user.id, m.from_user.username, datetime.utcnow().isoformat()))
        if referrer_id and referrer_id != m.from_user.id:
            cur = await db.execute("SELECT referrer_id FROM users WHERE tg_id=?", (m.from_user.id,))
            row = await cur.fetchone()
            if row and (row[0] is None):
                await db.execute("UPDATE users SET referrer_id=? WHERE tg_id=?", (referrer_id, m.from_user.id))
        await db.commit()

    ref_link = f"https://t.me/share_and_earn_money_bot?start={m.from_user.id}"
    await m.answer(
        f"👋 Welcome to *FREE FIRE ESPORTS BOT!*\n\n"
        f"Join our group to continue and claim your ₹2 Welcome Bonus 💰\n\n"
        f"👥 Invite friends & earn!\n"
        f"🔗 Your referral link: [Click Here]({ref_link})",
        reply_markup=join_buttons(),
        parse_mode="Markdown",
        disable_web_page_preview=True
    )

# ==========================
# CHECK JOIN & GIVE BONUSES
# ==========================
@dp.callback_query(F.data == "check_join")
async def check_group_join(callback: CallbackQuery):
    user_id = callback.from_user.id
    try:
        member = await bot.get_chat_member(f"@{GROUP_USERNAME}", user_id)
        status = getattr(member, "status", None)
    except Exception:
        status = None

    if status not in ["member", "administrator", "creator"]:
        await callback.message.answer(
            "❌ Please join the group first and try again!\n"
            f"👉 [Join Group](https://t.me/{GROUP_USERNAME})",
            parse_mode="Markdown"
        )
        return

    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("""
            SELECT balance, got_welcome_bonus, joined_group, referrer_id, ref_bonus_given
            FROM users WHERE tg_id=?
        """, (user_id,))
        row = await cur.fetchone()

        balance = row[0] if row else 0
        got_welcome = int(row[1]) if row else 0
        joined = int(row[2]) if row else 0
        referrer_id = row[3] if row else None
        ref_bonus_given = int(row[4]) if row else 0

        if not joined:
            await db.execute("UPDATE users SET joined_group=1 WHERE tg_id=?", (user_id,))
        if not got_welcome:
            await db.execute(
                "UPDATE users SET balance = balance + 2, got_welcome_bonus=1 WHERE tg_id=?", (user_id,)
            )
            balance += 2
            await callback.message.answer(f"🎉 Welcome bonus ₹2 added! Your new balance: ₹{balance}")

        if (referrer_id is not None) and (referrer_id != user_id) and (ref_bonus_given == 0):
            await db.execute(
                "UPDATE users SET balance = balance + 1, total_referrals = total_referrals + 1 WHERE tg_id=?",
                (referrer_id,)
            )
            await db.execute(
                "UPDATE users SET balance = balance + 0.5, ref_bonus_given=1 WHERE tg_id=?", (user_id,)
            )
            try:
                await bot.send_message(referrer_id, f"🎉 You got ₹1 referral bonus for inviting @{callback.from_user.username or user_id}!")
            except Exception:
                pass
        await db.commit()

    await callback.message.answer(
        "✅ You’re verified and ready to go!\n\n"
        "Use these commands:\n"
        "💰 /balance – Check wallet\n"
        "🎁 /daily – Daily ₹1 bonus\n"
        "💳 /bindupi – Link UPI\n"
        "📤 /withdraw <amount> – Request payout"
    )

# ==========================
# DAILY BONUS (FIXED)
# ==========================
@dp.message(Command("daily"))
async def daily_bonus(m: Message):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT OR IGNORE INTO users (tg_id, username, created_at) VALUES (?, ?, ?)",
                         (m.from_user.id, m.from_user.username, datetime.utcnow().isoformat()))
        await db.commit()

        cur = await db.execute("SELECT balance, last_bonus_date FROM users WHERE tg_id=?", (m.from_user.id,))
        row = await cur.fetchone()
        now = datetime.utcnow()

        balance = row[0] if row else 0
        if row and row[1]:
            try:
                last_bonus = datetime.fromisoformat(row[1])
            except Exception:
                last_bonus = datetime.utcnow() - timedelta(days=1)
            if now - last_bonus < timedelta(hours=24):
                await m.answer("⏰ You’ve already claimed your daily bonus today. Come back later!")
                return

        await db.execute("UPDATE users SET balance = balance + 1, last_bonus_date=? WHERE tg_id=?",
                         (now.isoformat(), m.from_user.id))
        await db.commit()

        new_balance = balance + 1
        await m.answer(f"🎁 ₹1 daily bonus added! Your new balance: ₹{new_balance}")

# ==========================
# BIND UPI (FIXED)
# ==========================
@dp.message(Command("bindupi"))
async def on_bind_upi(m: Message):
    parts = m.text.split(maxsplit=1)
    if len(parts) < 2:
        await m.answer("Usage: /bindupi your_upi@bank")
        return

    upi = parts[1].strip()
    if not re.match(r"^[0-9A-Za-z.\-_]+@[A-Za-z]{2,}$", upi):
        await m.answer("❌ Invalid UPI ID format. Example: 9876543210@paytm")
        return

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT OR IGNORE INTO users (tg_id, username, created_at) VALUES (?, ?, ?)",
                         (m.from_user.id, m.from_user.username, datetime.utcnow().isoformat()))
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
        f"🔗 *Your Referral Link:* [Click Here]({ref_link})",
        parse_mode="Markdown",
        disable_web_page_preview=True
    )

# ==========================
# WITHDRAW (with admin confirm)
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
        await m.answer("❌ Please link your UPI first with /bindupi.")
        return

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO withdrawals (user_id, amount, upi_id, status, created_at) VALUES (?, ?, ?, ?, ?)",
            (m.from_user.id, amount, upi_id, "pending", datetime.utcnow().isoformat())
        )
        await db.execute("UPDATE users SET balance = balance - ? WHERE tg_id=?", (amount, m.from_user.id))
        await db.commit()

        cur = await db.execute("SELECT last_insert_rowid()")
        withdrawal_id = (await cur.fetchone())[0]

    await m.answer("✅ Withdrawal request submitted!")

    # notify admin with confirm button
    for adm in ADMIN_IDS:
        markup = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="✅ Confirm Payment", callback_data=f"confirm_{withdrawal_id}")
        ]])
        await bot.send_message(
            adm,
            f"💸 *New Withdrawal Request*\n\n"
            f"👤 User: @{m.from_user.username or m.from_user.id}\n"
            f"💰 Amount: ₹{amount}\n"
            f"🏦 UPI: `{upi_id}`\n"
            f"🆔 Request ID: #{withdrawal_id}",
            parse_mode="Markdown",
            reply_markup=markup
        )

# ==========================
# ADMIN CONFIRM BUTTON
# ==========================
@dp.callback_query(F.data.startswith("confirm_"))
async def confirm_payment(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("⛔ Not authorized!", show_alert=True)
        return

    withdrawal_id = int(callback.data.split("_")[1])
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT user_id, amount, upi_id, status FROM withdrawals WHERE id=?", (withdrawal_id,))
        row = await cur.fetchone()

        if not row:
            await callback.message.answer("❌ Withdrawal not found.")
            return

        user_id, amount, upi_id, status = row
        if status != "pending":
            await callback.message.answer("⚠️ Already processed.")
            return

        await db.execute("UPDATE withdrawals SET status='completed' WHERE id=?", (withdrawal_id,))
        await db.commit()

    await callback.message.edit_text(
        f"✅ Payment Confirmed!\n\nWithdrawal #{withdrawal_id} marked as *completed.*",
        parse_mode="Markdown"
    )

    try:
        await bot.send_message(user_id, f"💰 Your withdrawal of ₹{amount} has been successfully processed!")
    except:
        pass

# ==========================
# MAIN
# ==========================
async def main():
    print("🚀 Bot is starting...")
    await init_db()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
