import asyncio
import logging
import re
import aiosqlite
import aiohttp
import os
from datetime import datetime, timedelta
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandObject
from aiogram.types import Message, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext

# ==========================
# CONFIG
# ==========================
logging.basicConfig(level=logging.INFO)
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("❌ BOT_TOKEN not found in .env file")

bot = Bot(BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

DB_PATH = "db.sqlite3"
MIN_WITHDRAWAL = 10
GROUP_USERNAME = "ffesportschallenges"
ADMIN_IDS = [7139153880]

# ==========================
# STATES
# ==========================
class BindUPI(StatesGroup):
    waiting_for_upi = State()

class Withdraw(StatesGroup):
    waiting_for_amount = State()
    confirm_withdraw = State()

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
            verified_ip TEXT,
            upi_id TEXT,
            last_bonus_date TEXT,
            got_welcome_bonus INTEGER DEFAULT 0
        )
        """)
        await db.execute("""
        CREATE TABLE IF NOT EXISTS withdrawals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            amount REAL,
            upi_id TEXT,
            status TEXT,
            created_at TEXT
        )
        """)
        await db.commit()

# Utility: ensure user exists
async def ensure_user(tg_id, username):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR IGNORE INTO users (tg_id, username) VALUES (?, ?)",
            (tg_id, username)
        )
        await db.commit()

# ==========================
# BUTTONS (Updated)
# ==========================
def main_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="💰 Balance", callback_data="cmd_balance"),
            InlineKeyboardButton(text="🎁 Daily Bonus", callback_data="cmd_daily")
        ],
        [
            InlineKeyboardButton(text="💳 Bind UPI", callback_data="cmd_bindupi"),
            InlineKeyboardButton(text="📤 Withdraw", callback_data="cmd_withdraw")
        ],
        [
            InlineKeyboardButton(text="👥 Referral", callback_data="cmd_referral")
        ]
    ])

# ==========================
# START + REFERRAL
# ==========================
@dp.message(Command("start"))
async def start_cmd(m: Message, command: CommandObject):
    await init_db()
    await ensure_user(m.from_user.id, m.from_user.username)

    referrer_id = int(command.args) if command.args and command.args.isdigit() else None
    if referrer_id and referrer_id != m.from_user.id:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("UPDATE users SET referrer_id=? WHERE tg_id=?", (referrer_id, m.from_user.id))
            await db.commit()

    ref_link = f"https://t.me/share_and_earn_money_bot?start={m.from_user.id}"
    await m.answer(
        f"👋 Welcome to *FREE FIRE ESPORTS BOT!*\n\n"
        f"Join our group and claim ₹2 Welcome Bonus 💰\n\n"
        f"👥 Invite friends & earn ₹1 per invite!\n"
        f"🔗 Your referral link:({ref_link})",
        parse_mode="Markdown",
        disable_web_page_preview=True,
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="🚀 Join Group", url=f"https://t.me/{GROUP_USERNAME}")],
                [InlineKeyboardButton(text="✅ Continue", callback_data="verify_join")]
            ]
        )
    )

# ==========================
# VERIFY + BONUS
# ==========================
@dp.callback_query(F.data == "verify_join")
async def verify_join(callback: CallbackQuery):
    user_id = callback.from_user.id
    username = callback.from_user.username
    await ensure_user(user_id, username)

    try:
        async with aiohttp.ClientSession() as s:
            async with s.get("https://api.ipify.org?format=json") as r:
                ip = (await r.json()).get("ip")
    except:
        ip = f"local-{user_id}"

    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT balance, got_welcome_bonus, verified_ip, referrer_id FROM users WHERE tg_id=?", (user_id,))
        row = await cur.fetchone()
        balance, got_bonus, old_ip, referrer_id = row or (0, 0, None, None)

        cur2 = await db.execute("SELECT tg_id FROM users WHERE verified_ip=? AND tg_id!=?", (ip, user_id))
        if await cur2.fetchone():
            await callback.message.answer("⚠️ This device/IP is already verified with another account.")
            return

        if got_bonus:
            await callback.message.answer("✅ You’re already verified and have your bonus.", reply_markup=main_menu())
            return

        await db.execute("UPDATE users SET balance = balance + 2, got_welcome_bonus=1, verified_ip=? WHERE tg_id=?", (ip, user_id))
        balance += 2

        if referrer_id:
            await db.execute("UPDATE users SET balance = balance + 1, total_referrals = total_referrals + 1 WHERE tg_id=?", (referrer_id,))
            try:
                await bot.send_message(referrer_id, f"🎉 You earned ₹1 for inviting @{callback.from_user.username or user_id}!")
            except:
                pass
        await db.commit()

    await callback.message.answer(f"🎉 ₹2 Welcome Bonus added! Balance: ₹{balance}", reply_markup=main_menu())

# ==========================
# MENU CALLBACK HANDLERS
# ==========================
@dp.callback_query(F.data.startswith("cmd_"))
async def handle_buttons(callback: CallbackQuery, state: FSMContext):
    await ensure_user(callback.from_user.id, callback.from_user.username)
    data = callback.data.replace("cmd_", "")
    if data == "balance":
        await show_balance(callback.message)
    elif data == "daily":
        await daily_bonus(callback.message)
    elif data == "bindupi":
        await bind_upi_start(callback.message, state)
    elif data == "withdraw":
        await withdraw_start(callback.message, state)
    elif data == "referral":
        await show_referrals(callback.message)

# ==========================
# BALANCE
# ==========================
async def show_balance(m: Message):
    await ensure_user(m.from_user.id, m.from_user.username)
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT balance, total_referrals, upi_id FROM users WHERE tg_id=?", (m.from_user.id,))
        row = await cur.fetchone()
        balance, refs, upi = row or (0, 0, None)
    await m.answer(f"💰 Balance: ₹{balance}\n👥 Referrals: {refs}\n🏦 UPI: {upi or 'Not set'}", parse_mode="Markdown")

# ==========================
# REFERRALS
# ==========================
async def show_referrals(m: Message):
    await ensure_user(m.from_user.id, m.from_user.username)
    ref_link = f"https://t.me/share_and_earn_money_bot?start={m.from_user.id}"
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT total_referrals FROM users WHERE tg_id=?", (m.from_user.id,))
        row = await cur.fetchone()
        total_refs = row[0] if row else 0
    await m.answer(
        f"👥 *Your Referral Info*\n\n"
        f"💸 Total Invites: {total_refs}\n"
        f"🔗 Invite Link: [Click Here]({ref_link})\n\n"
        f"Invite friends and earn ₹1 each!",
        parse_mode="Markdown",
        disable_web_page_preview=True
    )

# ==========================
# DAILY BONUS
# ==========================
async def daily_bonus(m: Message):
    await ensure_user(m.from_user.id, m.from_user.username)
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT balance, last_bonus_date FROM users WHERE tg_id=?", (m.from_user.id,))
        row = await cur.fetchone()
        balance, last = row or (0, None)
        now = datetime.utcnow()
        if last and now - datetime.fromisoformat(last) < timedelta(hours=24):
            await m.answer("⏰ Already claimed daily bonus today.")
            return
        await db.execute("UPDATE users SET balance = balance + 1, last_bonus_date=? WHERE tg_id=?", (now.isoformat(), m.from_user.id))
        await db.commit()
    await m.answer(f"🎁 ₹1 daily bonus added! New balance: ₹{balance + 1}")

# ==========================
# BINDUPI (FSM)
# ==========================
@dp.message(Command("bindupi"))
async def bind_upi_start(m: Message, state: FSMContext):
    await ensure_user(m.from_user.id, m.from_user.username)
    await state.set_state(BindUPI.waiting_for_upi)
    await m.answer("💳 Enter your UPI ID (example: 9876543210@paytm)")

@dp.message(BindUPI.waiting_for_upi)
async def save_upi(m: Message, state: FSMContext):
    upi = m.text.strip()
    if not re.match(r"^[0-9A-Za-z.\-_]+@[A-Za-z]{2,}$", upi):
        await m.answer("❌ Invalid UPI ID format.")
        return
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET upi_id=? WHERE tg_id=?", (upi, m.from_user.id))
        await db.commit()
    await state.clear()
    await m.answer(f"✅ UPI bound successfully:\n`{upi}`", parse_mode="Markdown")

# ==========================
# WITHDRAW (FSM)
# ==========================
@dp.message(Command("withdraw"))
async def withdraw_start(m: Message, state: FSMContext):
    await ensure_user(m.from_user.id, m.from_user.username)
    await state.set_state(Withdraw.waiting_for_amount)
    await m.answer(f"💸 Enter amount to withdraw (min ₹{MIN_WITHDRAWAL})")

@dp.message(Withdraw.waiting_for_amount)
async def withdraw_process(m: Message, state: FSMContext):
    await ensure_user(m.from_user.id, m.from_user.username)
    if not m.text.isdigit():
        await m.answer("❌ Invalid amount.")
        return
    amount = int(m.text)
    if amount < MIN_WITHDRAWAL:
        await m.answer(f"❌ Minimum withdrawal ₹{MIN_WITHDRAWAL}.")
        return
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT balance, upi_id FROM users WHERE tg_id=?", (m.from_user.id,))
        row = await cur.fetchone()
    if not row or not row[1]:
        await m.answer("⚠️ Please bind your UPI using /bindupi first.")
        await state.clear()
        return
    balance, upi = row
    if amount > balance:
        await m.answer("❌ Insufficient balance.")
        await state.clear()
        return
    await state.update_data(amount=amount, upi=upi)
    confirm = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Confirm", callback_data="confirm_withdraw"),
         InlineKeyboardButton(text="❌ Cancel", callback_data="cancel_withdraw")]
    ])
    await m.answer(f"💰 Withdraw ₹{amount} to `{upi}`?", parse_mode="Markdown", reply_markup=confirm)
    await state.set_state(Withdraw.confirm_withdraw)

@dp.callback_query(F.data == "confirm_withdraw")
async def confirm_withdraw(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    amount = data["amount"]
    upi = data["upi"]
    user_id = callback.from_user.id
    await ensure_user(user_id, callback.from_user.username)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET balance = balance - ? WHERE tg_id=?", (amount, user_id))
        await db.execute("""
            INSERT INTO withdrawals (user_id, amount, upi_id, status, created_at)
            VALUES (?, ?, ?, 'pending', ?)
        """, (user_id, amount, upi, datetime.utcnow().isoformat()))
        await db.commit()
    await callback.message.edit_text("✅ Withdrawal request submitted!")
    await state.clear()

# ==========================
# MAIN
# ==========================
async def main():
    print("🚀 Bot is running...")
    await init_db()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
