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
BOT_TOKEN = "8864547814:AAGgeivOOIPFCAltcR8TrOD1p-Wq1xCIe_c"

# Persistent Volume ডিরেক্টরি সেটআপ
DATA_DIR = "/app" if os.path.exists("/app") else "."
os.makedirs(DATA_DIR, exist_ok=True)

DB_PATH = os.path.join(DATA_DIR, "backtest_history.db")
LOGIC_PATH = os.path.join(DATA_DIR, "logic.js")
RUNNER_PATH = os.path.join(DATA_DIR, "runner.js")

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# ডাটাবেস সেটআপ (হিস্ট্রি এবং সাবস্ক্রাইবার টেবিল)
def setup_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(
        """CREATE TABLE IF NOT EXISTS history 
           (issue_number TEXT PRIMARY KEY, date TEXT, predicted_size TEXT, actual_size TEXT, status TEXT)"""
    )
    c.execute(
        """CREATE TABLE IF NOT EXISTS subscribers 
           (chat_id INTEGER PRIMARY KEY)"""
    )
    conn.commit()
    conn.close()

# সাবস্ক্রাইবার হ্যান্ডলার
def add_subscriber(chat_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("INSERT OR IGNORE INTO subscribers (chat_id) VALUES (?)", (chat_id,))
    conn.commit()
    conn.close()

def get_subscribers():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT chat_id FROM subscribers")
    rows = c.fetchall()
    conn.close()
    return [r[0] for r in rows]

# জাভাস্ক্রিপ্ট রানার তৈরি
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

# ব্যাকগ্রাউন্ড জব: প্রতি ৫ সেকেন্ডে মার্কেট ট্র্যাক করবে ও নতুন মেসেজ পাঠাবে
async def check_market_job(context: ContextTypes.DEFAULT_TYPE):
    try:
        data = run_js_prediction()
        if not data or "recentContext" not in data or "target" not in data:
            return

        recent_issue = str(data["recentContext"].get("lastIssueNumber", ""))
        actual_size = str(data["recentContext"].get("lastDrawnSize", ""))
        actual_num = data["recentContext"].get("lastDrawnNumber", "")

        next_issue = str(data["target"].get("issueNumber", ""))
        predicted_size = str(data["prediction"].get("size", ""))
        predicted_color = str(data["prediction"].get("color", ""))
        recommended_nums = data["prediction"].get("recommendedNumbers", [])
        conf = data["prediction"].get("confidenceScore", 0)
        today_date = datetime.now().strftime("%Y-%m-%d")

        if not recent_issue or not next_issue:
            return

        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()

        # ১. আগের পেন্ডিং প্রেডিকশন চেক ও WIN/LOSS আপডেট
        c.execute("SELECT predicted_size, status FROM history WHERE issue_number=?", (recent_issue,))
        row = c.fetchone()

        result_message = None
        if row and row[1] == "PENDING":
            is_win = (row[0] == actual_size)
            status = "WIN 🟢" if is_win else "LOSS 🔴"
            c.execute(
                "UPDATE history SET actual_size=?, status=? WHERE issue_number=?",
                (actual_size, status, recent_issue),
            )
            conn.commit()

            result_message = (
                f"📢 *DRAW RESULT*\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"🎲 Issue: `{recent_issue}`\n"
                f"📊 Result: *{actual_size}* (Number: `{actual_num}`)\n"
                f"🎯 Predicted Was: *{row[0]}*\n"
                f"⚡ Verdict: *{status}*"
            )

        # ২. নতুন প্রেডিকশন ডাটাবেসে সেভ করা
        c.execute("SELECT issue_number FROM history WHERE issue_number=?", (next_issue,))
        next_row = c.fetchone()

        new_pred_message = None
        if not next_row:
            c.execute(
                "INSERT INTO history (issue_number, date, predicted_size, actual_size, status) VALUES (?, ?, ?, ?, ?)",
                (next_issue, today_date, predicted_size, "WAITING", "PENDING"),
            )
            conn.commit()

            nums_str = ", ".join(map(str, recommended_nums)) if recommended_nums else "N/A"
            new_pred_message = (
                f"🎯 *NEW PREDICTION (WinGo 30s)*\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"🆔 Target Issue: `{next_issue}`\n"
                f"🔮 Prediction: *{predicted_size}*\n"
                f"🎨 Color: *{predicted_color}*\n"
                f"🔢 Numbers: `{nums_str}`\n"
                f"📈 Confidence: `{conf}%`\n"
                f"━━━━━━━━━━━━━━━━━━"
            )

        conn.close()

        # ৩. সব ইউজারের কাছে মেসেজ পাঠানো (এডিট ছাড়া সরাসরি নতুন মেসেজ)
        subscribers = get_subscribers()
        if subscribers:
            broadcast_payload = ""
            if result_message and new_pred_message:
                broadcast_payload = f"{result_message}\n\n{new_pred_message}"
            elif result_message:
                broadcast_payload = result_message
            elif new_pred_message:
                broadcast_payload = new_pred_message

            if broadcast_payload:
                for chat_id in subscribers:
                    try:
                        await context.bot.send_message(
                            chat_id=chat_id,
                            text=broadcast_payload,
                            parse_mode="Markdown",
                        )
                    except Exception as err:
                        logger.warning(f"Failed to push message to {chat_id}: {err}")

    except Exception as e:
        logger.error(f"Background Job Error: {e}")

# স্ট্যাটিস্টিক্স ফাংশন
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

# /start হ্যান্ডলার (ইউজারকে সাবস্ক্রাইবার হিসেবে যুক্ত করে)
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    add_subscriber(chat_id)

    keyboard = [
        [InlineKeyboardButton("📈 Overall Backtest", callback_data="history_all")],
        [InlineKeyboardButton("📅 Today's Backtest", callback_data="history_today")],
        [InlineKeyboardButton("🗓 Yesterday's Backtest", callback_data="history_yesterday")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    welcome_text = (
        "🤖 *WinGo 30s Live Prediction Bot Started!*\n\n"
        "✅ আপনি লাইভ সিগন্যালে যুক্ত হয়েছেন!\n"
        "এখন থেকে প্রতি ৩০ সেকেন্ড পরপর রেজাল্ট (WIN 🟢 / LOSS 🔴) এবং নতুন প্রেডিকশন আপনার ইনবক্সে নতুন মেসেজ আকারে অটোমেটিক পাঠানো হবে।"
    )
    await update.message.reply_text(welcome_text, reply_markup=reply_markup, parse_mode="Markdown")

    # বর্তমান রানিং প্রেডিকশন সাথে সাথে একবার পাঠানো
    data = run_js_prediction()
    if data and "target" in data and "prediction" in data:
        nums_str = ", ".join(map(str, data["prediction"].get("recommendedNumbers", [])))
        instant_msg = (
            f"🎯 *Current Target Issue:* `{data['target']['issueNumber']}`\n"
            f"🔮 *Prediction:* *{data['prediction']['size']}*\n"
            f"🎨 *Color:* *{data['prediction']['color']}*\n"
            f"🔢 *Numbers:* `{nums_str}`\n"
            f"📈 *Confidence:* `{data['prediction']['confidenceScore']}%`"
        )
        await update.message.reply_text(instant_msg, parse_mode="Markdown")

# বাটন হ্যান্ডলার
async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "history_all":
        msg = get_stats(target_date=None)
        await query.message.reply_text(text=msg, parse_mode="Markdown")

    elif query.data == "history_today":
        today = datetime.now().strftime("%Y-%m-%d")
        msg = get_stats(target_date=today)
        await query.message.reply_text(text=msg, parse_mode="Markdown")

    elif query.data == "history_yesterday":
        yesterday = (datetime.now() - timedelta(1)).strftime("%Y-%m-%d")
        msg = get_stats(target_date=yesterday)
        await query.message.reply_text(text=msg, parse_mode="Markdown")

# নির্দিষ্ট তারিখ দেখার কমান্ড
async def check_date(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("⚠️ Please provide a date. Example: `/date 2026-10-10`", parse_mode="Markdown")
        return
    target_date = context.args[0]
    msg = get_stats(target_date=target_date)
    await update.message.reply_text(msg, parse_mode="Markdown")

# JS ফাইল আপলোড হ্যান্ডলার
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

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("date", check_date))
    app.add_handler(CallbackQueryHandler(button))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))

    if app.job_queue:
        # প্রতি ৫ সেকেন্ড পরপর ব্যাকগ্রাউন্ডে চেক করবে
        app.job_queue.run_repeating(check_market_job, interval=5, first=1)
    else:
        logger.warning("JobQueue is not initialized! Ensure python-telegram-bot[job-queue] is installed.")

    print(f"Bot started successfully. Data dir: {DATA_DIR}")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
