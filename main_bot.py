import asyncio
import logging
import re
import aiosqlite
import aiohttp
import os
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandObject
from aiogram.types import Message, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from datetime import datetime, timedelta

# ==============================
# CONFIG
# ==============================
logging.basicConfig(level=logging.INFO)
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("❌ BOT_TOKEN not found in .env file")

bot = Bot(BOT_TOKEN)
dp = Dispatcher()

DB_PATH = "db.sqlite3"
MIN_WITHDRAWAL = 10
GROUP_USERNAME = "ffesportschallenges"
ADMIN_IDS = [7139153880]

# ==============================
# STATES
# ==============================
class BindUPI(StatesGroup):
    waiting_for_upi = State()

class Withdraw(StatesGroup):
    waiting_for_amount = State()
    confirm_withdraw = State()

# ==============================
# DATABASE INIT
# ==============================
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

# ==============================
# INLINE BUTTONS
# ==============================
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

# ==============================
# START
# ==============================
@dp.message(Command("start"))
async def start_cmd(m: Message, command: CommandObject):
    await init_db()
    referrer_id = int(command.args) if command.args and command.args.isdigit() else None

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT OR IGNORE INTO users (tg_id, username)
            VALUES (?, ?)
        """, (m.from_user.id, m.from_user.username))
        if referrer_id and referrer_id != m.from_user.id:
            cur = await db.execute("SELECT referrer_id FROM users WHERE tg_id=?", (m.from_user.id,))
            row = await cur.fetchone()
            if row and not row[0]:
                await db.execute("UPDATE users SET referrer_id=? WHERE tg_id=?", (referrer_id, m.from_user.id))
        await db.commit()

    ref_link = f"https://t.me/share_and_earn_money_bot?start={m.from_user.id}"
    await m.answer(
        f"👋 Welcome to *FREE FIRE ESPORTS BOT!*\n\n"
        f"Join our group and claim ₹2 Welcome Bonus 💰\n\n"
        f"👥 Invite friends & earn ₹1 per invite!\n"
        f"🔗 Referral link: [Click Here]({ref_link})",
        parse_mode="Markdown",
        disable_web_page_preview=True,
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="🚀 Join Group", url=f"https://t.me/{GROUP_USERNAME}")],
                [InlineKeyboardButton(text="✅ Continue", callback_data="verify_join")]
            ]
        )
    )

# ==============================
# GROUP VERIFICATION + BONUS
# ==============================
@dp.callback_query(F.data == "verify_join")
async def verify_join(callback: CallbackQuery):
    user_id = callback.from_user.id

    # IP check
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get("https://api.ipify.org?format=json") as r:
                ip = (await r.json()).get("ip")
    except:
        ip = f"local-{user_id}"

    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT balance, got_welcome_bonus, verified_ip, referrer_id FROM users WHERE tg_id=?", (user_id,))
        row = await cur.fetchone()
        balance, got_bonus, old_ip, referrer_id = row or (0, 0, None, None)

        cur2 = await db.execute("SELECT tg_id FROM users WHERE verified_ip=? AND tg_id!=?", (ip, user_id))
        if await cur2.fetchone():
            await callback.message.answer("⚠️ This device/IP already verified by another account.")
            return

        if got_bonus:
            await callback.message.answer("✅ You are already verified!", reply_markup=main_menu())
            return

        # Add welcome bonus
        await db.execute("UPDATE users SET balance = balance + 2, got_welcome_bonus=1, verified_ip=? WHERE tg_id=?", (ip, user_id))
        balance += 2

        # Referral bonus
        if referrer_id and referrer_id != user_id:
            await db.execute("UPDATE users SET balance = balance + 1, total_referrals = total_referrals + 1 WHERE tg_id=?", (referrer_id,))
        await db.commit()

    await callback.message.answer(f"🎉 ₹2 Welcome Bonus added! Your balance: ₹{balance}", reply_markup=main_menu())

# ==============================
# /DAILY BONUS
# ==============================
@dp.message(Command("daily"))
async def daily_bonus(m: Message):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT balance, last_bonus_date FROM users WHERE tg_id=?", (m.from_user.id,))
        row = await cur.fetchone()
        if not row:
            await m.answer("Use /start first.")
            return
        balance, last_date = row
        now = datetime.utcnow()
        if last_date and now - datetime.fromisoformat(last_date) < timedelta(hours=24):
            await m.answer("⏰ Daily bonus already claimed!")
            return
        await db.execute("UPDATE users SET balance = balance + 1, last_bonus_date=? WHERE tg_id=?", (now.isoformat(), m.from_user.id))
        await db.commit()
    await m.answer(f"🎁 ₹1 daily bonus added! New balance: ₹{balance + 1}")

# ==============================
# /BINDUPI (Interactive)
# ==============================
@dp.message(Command("bindupi"))
async def bind_upi_start(m: Message, state: FSMContext):
    await state.set_state(BindUPI.waiting_for_upi)
    await m.answer("💳 Please enter your UPI ID (e.g. 9876543210@paytm)")

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

# ==============================
# /BALANCE
# ==============================
@dp.message(Command("balance"))
async def balance_cmd(m: Message):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT balance, total_referrals, upi_id FROM users WHERE tg_id=?", (m.from_user.id,))
        row = await cur.fetchone()
        if not row:
            await m.answer("Use /start first.")
            return
        balance, refs, upi = row
    await m.answer(
        f"💰 Balance: ₹{balance}\n👥 Referrals: {refs}\n🏦 UPI: {upi or 'Not set'}",
        parse_mode="Markdown"
    )

# ==============================
# /WITHDRAW (Interactive)
# ==============================
@dp.message(Command("withdraw"))
async def withdraw_start(m: Message, state: FSMContext):
    await state.set_state(Withdraw.waiting_for_amount)
    await m.answer(f"💸 Enter amount to withdraw (min ₹{MIN_WITHDRAWAL})")

@dp.message(Withdraw.waiting_for_amount)
async def withdraw_process(m: Message, state: FSMContext):
    if not m.text.isdigit():
        await m.answer("❌ Invalid amount.")
        return
    amount = int(m.text)
    if amount < MIN_WITHDRAWAL:
        await m.answer(f"❌ Minimum withdrawal ₹{MIN_WITHDRAWAL}")
        return

    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT balance, upi_id FROM users WHERE tg_id=?", (m.from_user.id,))
        row = await cur.fetchone()
    if not row or not row[1]:
        await m.answer("⚠️ Please set your UPI first with /bindupi")
        await state.clear()
        return

    balance, upi = row
    if amount > balance:
        await m.answer("❌ Not enough balance.")
        await state.clear()
        return

    await state.update_data(amount=amount, upi=upi)
    confirm = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Confirm", callback_data="confirm_withdraw"),
         InlineKeyboardButton(text="❌ Cancel", callback_data="cancel_withdraw")]
    ])
    await m.answer(f"💰 Confirm withdrawal:\n🏦 `{upi}`\n💵 ₹{amount}", parse_mode="Markdown", reply_markup=confirm)
    await state.set_state(Withdraw.confirm_withdraw)

@dp.callback_query(F.data == "confirm_withdraw")
async def confirm_withdraw(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    amount = data["amount"]
    upi = data["upi"]
    user_id = callback.from_user.id

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET balance = balance - ? WHERE tg_id=?", (amount, user_id))
        await db.execute("""
            INSERT INTO withdrawals (user_id, amount, upi_id, status, created_at)
            VALUES (?, ?, ?, 'pending', ?)
        """, (user_id, amount, upi, datetime.utcnow().isoformat()))
        await db.commit()
        cur = await db.execute("SELECT last_insert_rowid()")
        wid = (await cur.fetchone())[0]

    await callback.message.edit_text("✅ Withdrawal submitted!")

    for admin in ADMIN_IDS:
        markup = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Approve", callback_data=f"approve_{wid}"),
             InlineKeyboardButton(text="❌ Reject", callback_data=f"reject_{wid}")]
        ])
        await bot.send_message(admin, f"💸 New Withdrawal\n\nUser: @{callback.from_user.username}\nUPI: `{upi}`\n₹{amount}", parse_mode="Markdown", reply_markup=markup)

    await state.clear()

@dp.callback_query(F.data == "cancel_withdraw")
async def cancel_withdraw(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text("❌ Withdrawal cancelled.")

# ==============================
# ADMIN ACTIONS
# ==============================
@dp.callback_query(F.data.startswith("approve_"))
async def approve(callback: CallbackQuery):
    wid = int(callback.data.split("_")[1])
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT user_id, amount, upi_id FROM withdrawals WHERE id=?", (wid,))
        row = await cur.fetchone()
        if not row:
            return await callback.message.answer("❌ Not found.")
        uid, amount, upi = row
        await db.execute("UPDATE withdrawals SET status='approved' WHERE id=?", (wid,))
        await db.commit()
    await callback.message.edit_text(f"✅ Withdrawal #{wid} approved.")
    await bot.send_message(uid, f"✅ Your withdrawal ₹{amount} to `{upi}` approved.", parse_mode="Markdown")

@dp.callback_query(F.data.startswith("reject_"))
async def reject(callback: CallbackQuery):
    wid = int(callback.data.split("_")[1])
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT user_id, amount, upi_id FROM withdrawals WHERE id=?", (wid,))
        row = await cur.fetchone()
        if not row:
            return await callback.message.answer("❌ Not found.")
        uid, amount, upi = row
        await db.execute("UPDATE withdrawals SET status='rejected' WHERE id=?", (wid,))
        await db.commit()
    await callback.message.edit_text(f"❌ Withdrawal #{wid} rejected.")
    await bot.send_message(uid, f"❌ Your withdrawal ₹{amount} to `{upi}` was rejected.", parse_mode="Markdown")

# ==============================
# MAIN
# ==============================
async def main():
    print("🚀 Bot is running...")
    await init_db()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
