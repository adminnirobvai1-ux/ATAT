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

# ডিরেক্টরি পাথ সেটআপ
DATA_DIR = "/app" if os.path.exists("/app") else "."
os.makedirs(DATA_DIR, exist_ok=True)

DB_PATH = os.path.join(DATA_DIR, "backtest_history.db")
LOGIC_PATH = os.path.join(DATA_DIR, "logic.js")
RUNNER_PATH = os.path.join(DATA_DIR, "runner.js")

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# ডাটাবেস প্রস্তুত করা
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

# ডিফল্ট logic.js এবং runner.js তৈরি করা (যাতে আপলোড ছাড়াও রান করে)
def ensure_js_files():
    default_logic = """
const WINGO_MARKETS = {
  "30s": {
    id: "30s",
    name: "WinGo 30S",
    apiUrl: "https://draw.ar-lottery01.com/WinGo/WinGo_30S/GetHistoryIssuePage.json"
  }
};

function resolveColors(number, rawColorStr = "") {
  if (number === 0) return ["RED", "VIOLET"];
  if (number === 5) return ["GREEN", "VIOLET"];
  if ([1, 3, 7, 9].includes(number)) return ["GREEN"];
  if ([2, 4, 6, 8].includes(number)) return ["RED"];
  const normalized = String(rawColorStr).toLowerCase();
  const colors = [];
  if (normalized.includes("red")) colors.push("RED");
  if (normalized.includes("green")) colors.push("GREEN");
  if (normalized.includes("violet")) colors.push("VIOLET");
  return colors.length > 0 ? colors : ["RED"];
}

function normalizeDrawRecord(rawItem) {
  const num = parseInt(rawItem.number, 10);
  const number = isNaN(num) ? 0 : num;
  const bigSmall = number >= 5 ? "BIG" : "SMALL";
  const colors = resolveColors(number, rawItem.color);
  const issueNumber = String(rawItem.issueNumber || "");
  return {
    issueNumber,
    shortIssue: issueNumber.length >= 5 ? issueNumber.slice(-5) : issueNumber,
    number,
    colors,
    bigSmall
  };
}

function computeNextPeriod(currentIssue) {
  try {
    return (BigInt(currentIssue) + 1n).toString();
  } catch {
    const parsed = parseInt(currentIssue, 10);
    return isNaN(parsed) ? `${Date.now()}` : (parsed + 1).toString();
  }
}

function analyzeDynamicHistory(sizesHistory) {
  let bestDepth = 0, bigProbability = 0, smallProbability = 0;
  for (let depth = 6; depth >= 1; depth--) {
    if (sizesHistory.length < depth + 2) continue;
    const currentSequence = sizesHistory.slice(0, depth).join("-");
    let bigMatches = 0, smallMatches = 0;
    for (let i = 1; i < sizesHistory.length - depth; i++) {
      const pastSequence = sizesHistory.slice(i, i + depth).join("-");
      if (currentSequence === pastSequence) {
        const nextResult = sizesHistory[i - 1]; 
        if (nextResult === "BIG") bigMatches++;
        if (nextResult === "SMALL") smallMatches++;
      }
    }
    if (bigMatches > 0 || smallMatches > 0) {
      bestDepth = depth;
      const totalMatches = bigMatches + smallMatches;
      bigProbability = (bigMatches / totalMatches) * 100;
      smallProbability = (smallMatches / totalMatches) * 100;
      break;
    }
  }
  return { bestDepth, bigProbability, smallProbability };
}

function executePredictionModel(history, targetIssue) {
  const shortIssue = targetIssue.length >= 5 ? targetIssue.slice(-5) : targetIssue;
  if (!Array.isArray(history) || history.length < 5) {
    return {
      targetIssue, shortIssue, predictedSize: "SMALL", predictedColor: "GREEN",
      recommendedNumbers: [1, 3], confidenceScore: 60.0
    };
  }

  const sizes = history.map((item) => item.bigSmall);
  const lastSize = sizes[0];
  let predictedSize = "BIG";
  let confidence = 85.0;

  const dynamicAnalysis = analyzeDynamicHistory(sizes);
  let streakCount = 1;
  while (streakCount < sizes.length && sizes[streakCount] === lastSize) {
    streakCount++;
  }

  if (dynamicAnalysis.bestDepth >= 2 && Math.abs(dynamicAnalysis.bigProbability - dynamicAnalysis.smallProbability) >= 20) {
    predictedSize = dynamicAnalysis.bigProbability > dynamicAnalysis.smallProbability ? "BIG" : "SMALL";
    confidence = Math.max(92.0, 85 + (dynamicAnalysis.bestDepth * 1.5));
  } else if (streakCount >= 4) {
    predictedSize = lastSize;
    confidence = 94.0;
  } else {
    const last10Sizes = sizes.slice(0, 10);
    const bigCount = last10Sizes.filter(s => s === "BIG").length;
    if (bigCount >= 7) {
      predictedSize = "SMALL";
      confidence = 90.0;
    } else if (bigCount <= 3) {
      predictedSize = "BIG";
      confidence = 90.0;
    } else {
      predictedSize = lastSize === "BIG" ? "SMALL" : "BIG";
      confidence = 88.0;
    }
  }

  const recentColors = history.slice(0, 5).map(h => h.colors[0]);
  const isRedDominant = recentColors.filter(c => c === "RED").length > 2;
  let predictedColor = isRedDominant ? "RED" : "GREEN";
  if (predictedSize === "BIG" && predictedColor === "GREEN") predictedColor = "RED";
  if (predictedSize === "SMALL" && predictedColor === "RED") predictedColor = "GREEN";

  let recommendedNumbers = predictedSize === "BIG" ? [6, 8, 7, 9] : [1, 3, 2, 4];
  recommendedNumbers = recommendedNumbers.slice(0, 2);

  return {
    targetIssue,
    shortIssue,
    predictedSize,
    predictedColor,
    recommendedNumbers,
    confidenceScore: Number(confidence.toFixed(1))
  };
}

async function getMarketPrediction() {
  const market = WINGO_MARKETS["30s"];
  const response = await fetch(market.apiUrl, {
    headers: { Accept: "application/json, text/plain, */*" }
  });
  if (!response.ok) throw new Error(`API failed: ${response.status}`);
  const json = await response.json();
  const rawList = json?.data?.list;
  if (!Array.isArray(rawList) || rawList.length === 0) throw new Error("No data returned");

  const history = rawList.map(normalizeDrawRecord);
  const latestDraw = history[0];
  const targetIssue = computeNextPeriod(latestDraw.issueNumber);
  const prediction = executePredictionModel(history, targetIssue);

  return {
    success: true,
    target: { issueNumber: prediction.targetIssue, shortIssue: prediction.shortIssue },
    prediction: {
      size: prediction.predictedSize,
      color: prediction.predictedColor,
      recommendedNumbers: prediction.recommendedNumbers,
      confidenceScore: prediction.confidenceScore
    },
    recentContext: {
      lastIssueNumber: latestDraw.issueNumber,
      lastDrawnSize: latestDraw.bigSmall,
      lastDrawnNumber: latestDraw.number
    }
  };
}

module.exports = { getMarketPrediction };
"""
    if not os.path.exists(LOGIC_PATH):
        with open(LOGIC_PATH, "w", encoding="utf-8") as f:
            f.write(default_logic.strip())
        logger.info("Default logic.js initialized.")

    runner_code = """
const logic = require('./logic.js');
if (typeof logic.getMarketPrediction === 'function') {
    logic.getMarketPrediction()
        .then(res => console.log(JSON.stringify(res)))
        .catch(err => { console.error(err); process.exit(1); });
} else {
    console.log(JSON.stringify({}));
}
"""
    with open(RUNNER_PATH, "w", encoding="utf-8") as f:
        f.write(runner_code.strip())

# JS এক্সিকিউশন
def run_js_prediction():
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
            logger.error(f"JS Run Error: {result.stderr}")
            return None
    except Exception as e:
        logger.error(f"Subprocess Execution Error: {e}")
        return None

# ব্যাকগ্রাউন্ড মার্কেট মনিটর জব
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

        # ১. আগের পেন্ডিং ইস্যু WIN/LOSS যাচাই করা
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
                f"📢 *WIN-GO 30s RESULT*\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"🎲 Issue: `{recent_issue}`\n"
                f"📊 Result: *{actual_size}* (No: `{actual_num}`)\n"
                f"🎯 Predicted Was: *{row[0]}*\n"
                f"⚡ Result Verdict: *{status}*"
            )

        # ২. নতুন ইস্যু এন্ট্রি করা
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
                f"🎯 *NEW SIGNAL (WinGo 30s)*\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"🆔 Period: `{next_issue}`\n"
                f"🔮 Prediction: *{predicted_size}*\n"
                f"🎨 Color: *{predicted_color}*\n"
                f"🔢 Numbers: `{nums_str}`\n"
                f"📈 Confidence: `{conf}%`\n"
                f"━━━━━━━━━━━━━━━━━━"
            )

        conn.close()

        # ৩. সব ইউজারের কাছে নতুন মেসেজ পাঠানো (ব্রডকাস্ট)
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
                        logger.warning(f"Error sending to {chat_id}: {err}")

    except Exception as e:
        logger.error(f"Background Job Error: {e}")

# ব্যাকটেস্ট রিপোর্ট
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
    curr_win = 0
    curr_loss = 0

    for r in results:
        if "WIN" in r[0]:
            curr_win += 1
            curr_loss = 0
            if curr_win > max_win_streak:
                max_win_streak = curr_win
        else:
            curr_loss += 1
            curr_win = 0
            if curr_loss > max_loss_streak:
                max_loss_streak = curr_loss

    date_label = f"Date: {target_date}" if target_date else "All-Time Overall"

    return (
        f"📊 *Backtest Statistics ({date_label})*\n\n"
        f"Total Trades: `{total}`\n"
        f"🟢 Total Wins: `{wins}`\n"
        f"🔴 Total Losses: `{losses}`\n"
        f"🏆 Win Rate: `{win_rate:.2f}%`\n\n"
        f"🔥 *Streak Pattern:*\n"
        f"Highest Win Streak: `{max_win_streak}` times\n"
        f"Highest Loss Streak: `{max_loss_streak}` times"
    )

# /start হ্যান্ডলার
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
        "🤖 *WinGo 30s Live Bot Activated!*\n\n"
        "✅ আপনার আইডি লাইভ সিগন্যালে যুক্ত হয়েছে।\n"
        "এখন থেকে প্রতি ৩০ সেকেন্ড পরপর ফলাফল এবং নতুন প্রেডিকশন নতুন মেসেজ হিসেবে অটোমেটিক পাঠানো হবে।"
    )
    await update.message.reply_text(welcome_text, reply_markup=reply_markup, parse_mode="Markdown")

    # ইনস্ট্যান্ট বর্তমান প্রেডিকশন
    data = run_js_prediction()
    if data and "target" in data and "prediction" in data:
        nums_str = ", ".join(map(str, data["prediction"].get("recommendedNumbers", [])))
        instant_msg = (
            f"🎯 *Current Target Period:* `{data['target']['issueNumber']}`\n"
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

# তারিখ চেক
async def check_date(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("⚠️ তারিখ উল্লেখ করুন। যেমন: `/date 2026-10-10`", parse_mode="Markdown")
        return
    target_date = context.args[0]
    msg = get_stats(target_date=target_date)
    await update.message.reply_text(msg, parse_mode="Markdown")

# ফাইল আপলোড হ্যান্ডলার
async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    doc = update.message.document
    if doc.file_name and doc.file_name.endswith(".js"):
        file = await context.bot.get_file(doc.file_id)
        await file.download_to_drive(LOGIC_PATH)
        await update.message.reply_text("✅ নতুন JavaScript লজিক ফাইল সেভ হয়েছে! বট এখন এই লজিকে সিগন্যাল দেবে।")
    else:
        await update.message.reply_text("⚠️ অনুগ্রহ করে একটি বৈধ `.js` ফাইল সেন্ড করুন।")

# এরর হ্যান্ডলার (টেলিগ্রাম এরর লগে প্রিন্ট করবে)
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.error("Exception while handling an update:", exc_info=context.error)

def main():
    setup_db()
    ensure_js_files()

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("date", check_date))
    app.add_handler(CallbackQueryHandler(button))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    app.add_error_handler(error_handler)

    if app.job_queue:
        app.job_queue.run_repeating(check_market_job, interval=5, first=1)
    else:
        logger.warning("JobQueue পাওয়া যায়নি!")

    print(f"Bot started. Persistent Directory: {DATA_DIR}")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
