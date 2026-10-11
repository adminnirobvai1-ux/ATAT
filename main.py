import os
import json
import logging
import sqlite3
import subprocess
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

# আপনার বটের টোকেন
BOT_TOKEN = "8864547814:AAETisKJt1oten1ZhyQt1z20w8Ho3J2UAg8"

# ডিরেক্টরি পাথ
DATA_DIR = "/app" if os.path.exists("/app") else "."
os.makedirs(DATA_DIR, exist_ok=True)

DB_PATH = os.path.join(DATA_DIR, "backtest_history.db")
LOGIC_PATH = os.path.join(DATA_DIR, "logic.js")
RUNNER_PATH = os.path.join(DATA_DIR, "runner.js")

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# ডাটাবেস সেটআপ
def setup_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(
        """CREATE TABLE IF NOT EXISTS history 
                 (issue_number TEXT PRIMARY KEY, date TEXT, predicted_size TEXT, actual_size TEXT, status TEXT)"""
    )
    conn.commit()
    conn.close()

# জাভাস্ক্রিপ্ট রানার ফাইল তৈরি
def create_js_runner():
    runner_code = """
const logic = require('./logic.js');
if (typeof logic.getMarketPrediction === 'function') {
    logic.getMarketPrediction('30s')
        .then(res => console.log(JSON.stringify(res)))
        .catch(err => { console.error(err); process.exit(1); });
} else {
    console.log(JSON.stringify({}));
}
"""
    with open(RUNNER_PATH, "w", encoding="utf-8") as f:
        f.write(runner_code)

# Node.js দিয়ে JS কোড এক্সিকিউট করা
def run_js_prediction():
    if not os.path.exists(LOGIC_PATH):
        return None
    try:
        result = subprocess.run(
            ["node", "runner.js"],
            cwd=DATA_DIR,
            capture_output=True,
            text=True,
            timeout=15,
        )
        if result.returncode == 0 and result.stdout.strip():
            return json.loads(result.stdout)
        else:
            logger.error(f"JS Error: {result.stderr}")
            return None
    except Exception as e:
        logger.error(f"Execution Error: {e}")
        return None

# ব্যাকগ্রাউন্ড মার্কেট মনিটর জব
async def check_market_job(context: ContextTypes.DEFAULT_TYPE):
    try:
        data = run_js_prediction()
        if not data or "recentContext" not in data or "target" not in data:
            return

        recent_issue = data.get("recentContext", {}).get("lastIssueNumber")
        actual_size = data.get("recentContext", {}).get("lastDrawnSize")
        next_issue = data.get("target", {}).get("issueNumber")
        predicted_size = data.get("prediction", {}).get("size")
        today_date = datetime.now().strftime("%Y-%m-%d")

        if not recent_issue or not next_issue:
            return

        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()

        # পেন্ডিং প্রেডিকশন আপডেট
        c.execute("SELECT predicted_size, status FROM history WHERE issue_number=?", (recent_issue,))
        row = c.fetchone()
        if row and row[1] == "PENDING":
            is_win = (row[0] == actual_size)
            status = "WIN 🟢" if is_win else "LOSS 🔴"
            c.execute(
                "UPDATE history SET actual_size=?, status=? WHERE issue_number=?",
                (actual_size, status, recent_issue),
            )
            conn.commit()

        # নতুন প্রেডিকশন এন্ট্রি
        c.execute("SELECT issue_number FROM history WHERE issue_number=?", (next_issue,))
        if not c.fetchone():
            c.execute(
                "INSERT INTO history (issue_number, date, predicted_size, actual_size, status) VALUES (?, ?, ?, ?, ?)",
                (next_issue, today_date, predicted_size, "WAITING", "PENDING"),
            )
            conn.commit()

        conn.close()
    except Exception as e:
        logger.error(f"Background Job Error: {e}")

# ব্যাকটেস্ট স্ট্যাটস
def get_stats(target_date=None):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    if target_date:
        c.execute(
            "SELECT status FROM history WHERE status != 'PENDING' AND date=? ORDER BY issue_number ASC",
            (target_date,),
        )
    else:
        c.execute("SELECT status FROM history WHERE status != 'PENDING' ORDER BY issue_number ASC")

    results = c.fetchall()
    conn.close()

    total = len(results)
    wins = sum(1 for r in results if "WIN" in r[0])
    losses = total - wins
    win_rate = (wins / total * 100) if total > 0 else 0

    max_win_streak = 0
    max_loss_streak = 0
    current_win_streak = 0
    current_loss_streak = 0

    for r in results:
        if "WIN" in r[0]:
            current_win_streak += 1
            current_loss_streak = 0
            if current_win_streak > max_win_streak:
                max_win_streak = current_win_streak
        else:
            current_loss_streak += 1
            current_win_streak = 0
            if current_loss_streak > max_loss_streak:
                max_loss_streak = current_loss_streak

    date_label = f"Date: {target_date}" if target_date else "Overall All-Time"

    return (
        f"📊 *Backtest Statistics ({date_label})*\n\n"
        f"Total Trades: `{total}`\n"
        f"🟢 Total Wins: `{wins}`\n"
        f"🔴 Total Losses: `{losses}`\n"
        f"🏆 Win Rate: `{win_rate:.2f}%`\n\n"
        f"🔥 *Streak Pattern:*\n"
        f"Highest Consecutive Wins: `{max_win_streak}` times\n"
        f"Highest Consecutive Losses: `{max_loss_streak}` times"
    )

# /start হ্যান্ডলার
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("🎯 Current Prediction", callback_data="current_pred")],
        [InlineKeyboardButton("📈 Overall Backtest", callback_data="history_all")],
        [InlineKeyboardButton("📅 Today's Backtest", callback_data="history_today")],
        [InlineKeyboardButton("🗓 Yesterday's Backtest", callback_data="history_yesterday")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    welcome_text = (
        "🤖 *WinGo 30s Advanced Bot (24/7)*\n\n"
        "Send me a `.js` file to update logic.\n"
        "To check a specific date, send:\n`/date YYYY-MM-DD`"
    )
    await update.message.reply_text(welcome_text, reply_markup=reply_markup, parse_mode="Markdown")

# বাটন হ্যান্ডলার
async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    keyboard_back = [[InlineKeyboardButton("🔙 Back to Menu", callback_data="back")]]

    if query.data == "current_pred":
        data = run_js_prediction()
        if data and "target" in data and "prediction" in data:
            target = data["target"].get("issueNumber", "N/A")
            size = data["prediction"].get("size", "N/A")
            conf = data["prediction"].get("confidenceScore", "N/A")
            msg = f"🎯 *Target Issue:* `{target}`\n🔮 *Prediction:* `{size}`\n📈 *Confidence:* `{conf}%`"
        else:
            msg = "⚠️ Error generating prediction or JS logic file missing."
        await query.edit_message_text(text=msg, reply_markup=InlineKeyboardMarkup(keyboard_back), parse_mode="Markdown")

    elif query.data == "history_all":
        msg = get_stats(target_date=None)
        await query.edit_message_text(text=msg, reply_markup=InlineKeyboardMarkup(keyboard_back), parse_mode="Markdown")

    elif query.data == "history_today":
        today = datetime.now().strftime("%Y-%m-%d")
        msg = get_stats(target_date=today)
        await query.edit_message_text(text=msg, reply_markup=InlineKeyboardMarkup(keyboard_back), parse_mode="Markdown")

    elif query.data == "history_yesterday":
        yesterday = (datetime.now() - timedelta(1)).strftime("%Y-%m-%d")
        msg = get_stats(target_date=yesterday)
        await query.edit_message_text(text=msg, reply_markup=InlineKeyboardMarkup(keyboard_back), parse_mode="Markdown")

    elif query.data == "back":
        keyboard = [
            [InlineKeyboardButton("🎯 Current Prediction", callback_data="current_pred")],
            [InlineKeyboardButton("📈 Overall Backtest", callback_data="history_all")],
            [InlineKeyboardButton("📅 Today's Backtest", callback_data="history_today")],
            [InlineKeyboardButton("🗓 Yesterday's Backtest", callback_data="history_yesterday")],
        ]
        await query.edit_message_text(text="🤖 *Main Menu*", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

# /date কমান্ড
async def check_date(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("⚠️ Please provide a date. Example: `/date 2026-10-10`", parse_mode="Markdown")
        return
    target_date = context.args[0]
    msg = get_stats(target_date=target_date)
    await update.message.reply_text(msg, parse_mode="Markdown")

# JS ফাইল হ্যান্ডলার
async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    doc = update.message.document
    if doc.file_name and doc.file_name.endswith(".js"):
        file = await context.bot.get_file(doc.file_id)
        await file.download_to_drive(LOGIC_PATH)
        await update.message.reply_text("✅ Success! New JavaScript logic imported. Running 24/7 on new logic.")
    else:
        await update.message.reply_text("⚠️ Please upload a valid `.js` file.")

def main():
    setup_db()
    create_js_runner()

    if not os.path.exists(LOGIC_PATH):
        with open(LOGIC_PATH, "w", encoding="utf-8") as f:
            f.write("// Default logic\nmodule.exports = { getMarketPrediction: async () => ({}) };")

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("date", check_date))
    app.add_handler(CallbackQueryHandler(button))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))

    if app.job_queue:
        app.job_queue.run_repeating(check_market_job, interval=10, first=1)
    else:
        logger.warning("JobQueue is not initialized! Check python-telegram-bot[job-queue]")

    print(f"Bot started successfully. Data dir: {DATA_DIR}")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
