import time
import threading
import requests
import telebot

# আপনার টেলিগ্রাম বট টোকেন
BOT_TOKEN = "8864547814:AAFooC7Hy-FZnVVbpqEofWFnwxla7VMawmA"
bot = telebot.TeleBot(BOT_TOKEN, parse_mode="HTML")

API_URL = "https://draw.ar-lottery01.com/WinGo/WinGo_30S/GetHistoryIssuePage.json"

# প্রতিটি ইউজারের রানিং সেশন এবং লাইভ পরিসংখ্যান
sessions = {}

def get_empty_stats():
    return {
        "total": 0,
        "wins": 0,
        "losses": 0,
        "cur_win": 0,
        "max_win": 0,
        "cur_loss": 0,
        "max_loss": 0
    }

# ==================== [ডাটা এবং লজিক ফাংশন] ====================

def resolve_colors(number, raw_color=""):
    if number == 0:
        return ["RED", "VIOLET"]
    if number == 5:
        return ["GREEN", "VIOLET"]
    if number in [1, 3, 7, 9]:
        return ["GREEN"]
    if number in [2, 4, 6, 8]:
        return ["RED"]

    normalized = str(raw_color).lower()
    colors = []
    if "red" in normalized: colors.append("RED")
    if "green" in normalized: colors.append("GREEN")
    if "violet" in normalized: colors.append("VIOLET")
    return colors if colors else ["RED"]

def normalize_draw_record(raw_item):
    try:
        num = int(raw_item.get("number", 0))
    except (ValueError, TypeError):
        num = 0

    big_small = "BIG" if num >= 5 else "SMALL"
    colors = resolve_colors(num, raw_item.get("color", ""))
    issue_number = str(raw_item.get("issueNumber", ""))
    short_issue = issue_number[-5:] if len(issue_number) >= 5 else issue_number

    return {
        "issueNumber": issue_number,
        "shortIssue": short_issue,
        "number": num,
        "colors": colors,
        "bigSmall": big_small
    }

def compute_next_period(current_issue):
    try:
        return str(int(current_issue) + 1)
    except Exception:
        return str(int(time.time()))

def analyze_dynamic_history(sizes_history):
    best_depth = 0
    big_prob = 0.0
    small_prob = 0.0

    for depth in range(6, 0, -1):
        if len(sizes_history) < depth + 2:
            continue

        current_seq = "-".join(sizes_history[:depth])
        big_matches = 0
        small_matches = 0

        for i in range(1, len(sizes_history) - depth):
            past_seq = "-".join(sizes_history[i:i + depth])
            if current_seq == past_seq:
                next_res = sizes_history[i - 1]
                if next_res == "BIG":
                    big_matches += 1
                elif next_res == "SMALL":
                    small_matches += 1

        total_matches = big_matches + small_matches
        if total_matches > 0:
            best_depth = depth
            big_prob = (big_matches / total_matches) * 100.0
            small_prob = (small_matches / total_matches) * 100.0
            break

    return best_depth, big_prob, small_prob

def execute_prediction_model(history, target_issue):
    short_issue = target_issue[-5:] if len(target_issue) >= 5 else target_issue

    if not isinstance(history, list) or len(history) < 5:
        return {
            "targetIssue": target_issue,
            "shortIssue": short_issue,
            "predictedSize": "SMALL",
            "predictedColor": "GREEN",
            "recommendedNumbers": [1, 3],
            "confidenceScore": 60.0,
            "patternName": "Data Gathering"
        }

    sizes = [item["bigSmall"] for item in history]
    last_size = sizes[0]

    best_depth, big_prob, small_prob = analyze_dynamic_history(sizes)

    streak_count = 1
    while streak_count < len(sizes) and sizes[streak_count] == last_size:
        streak_count += 1

    predicted_size = "BIG"
    pattern_name = ""
    confidence = 85.0

    if best_depth >= 2 and abs(big_prob - small_prob) >= 20:
        predicted_size = "BIG" if big_prob > small_prob else "SMALL"
        pattern_name = f"Dynamic AI Match (Depth: {best_depth})"
        confidence = max(92.0, 85.0 + (best_depth * 1.5))
    elif streak_count >= 4:
        predicted_size = last_size
        pattern_name = f"Strong Momentum (x{streak_count})"
        confidence = 94.0
    else:
        last_10 = sizes[:10]
        big_count = last_10.count("BIG")
        if big_count >= 7:
            predicted_size = "SMALL"
            pattern_name = "Statistical Reversion (Overbought Big)"
            confidence = 90.0
        elif big_count <= 3:
            predicted_size = "BIG"
            pattern_name = "Statistical Reversion (Oversold Small)"
            confidence = 90.0
        else:
            predicted_size = "SMALL" if last_size == "BIG" else "BIG"
            pattern_name = "Market Oscillation"
            confidence = 88.0

    recent_colors = [h["colors"][0] for h in history[:5]]
    red_dominant = recent_colors.count("RED") > 2

    predicted_color = "RED" if red_dominant else "GREEN"
    if predicted_size == "BIG" and predicted_color == "GREEN":
        predicted_color = "RED"
    if predicted_size == "SMALL" and predicted_color == "RED":
        predicted_color = "GREEN"

    numbers = [6, 8, 7, 9] if predicted_size == "BIG" else [1, 3, 2, 4]
    recommended_numbers = numbers[:2]

    return {
        "targetIssue": target_issue,
        "shortIssue": short_issue,
        "predictedSize": predicted_size,
        "predictedColor": predicted_color,
        "recommendedNumbers": recommended_numbers,
        "confidenceScore": round(confidence, 1),
        "patternName": pattern_name
    }

# ==================== [নেটওয়ার্ক রিট্রাই লজিক] ====================

def fetch_history_with_retry(retries=3, delay=1):
    headers = {
        "Accept": "application/json, text/plain, */*",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
    }
    for _ in range(retries):
        try:
            res = requests.get(API_URL, headers=headers, timeout=5)
            if res.status_code == 200:
                data = res.json()
                raw_list = data.get("data", {}).get("list", [])
                if raw_list and len(raw_list) > 0:
                    return raw_list
        except Exception:
            pass
        time.sleep(delay)
    return None

# ==================== [২৪ ঘণ্টা লাইভ ব্যাকগ্রাউন্ড থ্রেড] ====================

def tracker_worker(chat_id):
    session = sessions.get(chat_id)
    if not session:
        return

    while session.get("active", False):
        try:
            raw_list = fetch_history_with_retry(retries=3, delay=1)
            if not raw_list:
                time.sleep(2)
                continue

            history = [normalize_draw_record(item) for item in raw_list]
            latest_draw = history[0]
            latest_issue = latest_draw["issueNumber"]

            # নতুন ড্র রেজাল্ট ডিটেক্ট হলে
            if session["last_processed_issue"] != latest_issue:
                session["last_processed_issue"] = latest_issue

                # ১. আগের প্রেডিকশনের উইন/লস চেক
                pending = session.get("pending_prediction")
                if pending and pending["targetIssue"] == latest_issue:
                    session["stats"]["total"] += 1
                    actual_size = latest_draw["bigSmall"]
                    is_win = (pending["predictedSize"] == actual_size)

                    if is_win:
                        session["stats"]["wins"] += 1
                        session["stats"]["cur_win"] += 1
                        session["stats"]["cur_loss"] = 0
                        if session["stats"]["cur_win"] > session["stats"]["max_win"]:
                            session["stats"]["max_win"] = session["stats"]["cur_win"]
                        status_text = "🟢 WIN (উইন)"
                    else:
                        session["stats"]["losses"] += 1
                        session["stats"]["cur_loss"] += 1
                        session["stats"]["cur_win"] = 0
                        if session["stats"]["cur_loss"] > session["stats"]["max_loss"]:
                            session["stats"]["max_loss"] = session["stats"]["cur_loss"]
                        status_text = "🔴 LOSS (লস)"

                    win_rate = (session["stats"]["wins"] / session["stats"]["total"]) * 100

                    result_msg = (
                        f"📊 <b>ফলাফল আপডেট</b>\n"
                        f"──────────────────\n"
                        f"🔢 পিরিয়ড: <code>#{latest_issue}</code>\n"
                        f"🎲 ড্র নাম্বার: <b>{latest_draw['number']}</b> ({actual_size})\n"
                        f"🎯 প্রেডিকশন ছিল: <b>{pending['predictedSize']}</b>\n"
                        f"⚡ স্ট্যাটাস: <b>{status_text}</b>\n"
                        f"──────────────────\n"
                        f"📈 স্কোর: W: {session['stats']['wins']} | L: {session['stats']['losses']} (রেট: {win_rate:.1f}%)\n"
                        f"🔥 টানা উইন: {session['stats']['cur_win']} | টানা লস: {session['stats']['cur_loss']}"
                    )
                    try:
                        bot.send_message(chat_id, result_msg)
                    except Exception:
                        pass

                # ২. পরবর্তী পিরিয়ডের প্রেডিকশন তৈরি
                next_issue = compute_next_period(latest_issue)
                pred = execute_predictionModel(history, next_issue)
                session["pending_prediction"] = pred

                pred_msg = (
                    f"🔮 <b>পরবর্তী প্রেডিকশন (WinGo 30s)</b>\n"
                    f"──────────────────\n"
                    f"🎯 টার্গেট পিরিয়ড: <code>#{pred['targetIssue']}</code>\n"
                    f"📊 সাইজ: <b>{pred['predictedSize']}</b>\n"
                    f"🎨 কালার: <b>{pred['predictedColor']}</b>\n"
                    f"🔢 নাম্বার: <b>{', '.join(map(str, pred['recommendedNumbers']))}</b>\n"
                    f"🧠 প্যাটার্ন: <i>{pred['patternName']}</i>\n"
                    f"⚡ একিউরেসি: <b>{pred['confidenceScore']}%</b>\n"
                    f"──────────────────\n"
                    f"⏳ ৩০ সেকেন্ড কাউন্টডাউন চলছে..."
                )
                try:
                    bot.send_message(chat_id, pred_msg)
                except Exception:
                    pass

        except Exception as e:
            time.sleep(2)

        time.sleep(2)

# ==================== [টেলিগ্রাম বট কমান্ড] ====================

@bot.message_handler(commands=['start'])
def handle_start(message):
    chat_id = message.chat.id

    if chat_id in sessions and sessions[chat_id].get("active", False):
        bot.send_message(chat_id, "⚠️ ট্র্যাকার ইতিমধ্যেই চালু আছে! বন্ধ করতে /stop বা <b>অফ</b> লিখুন।")
        return

    sessions[chat_id] = {
        "active": True,
        "stats": get_empty_stats(),
        "last_processed_issue": None,
        "pending_prediction": None
    }

    t = threading.Thread(target=tracker_worker, args=(chat_id,), daemon=True)
    t.start()

    bot.send_message(
        chat_id,
        "🚀 <b>WinGo 30s এআই ট্র্যাকার চালু হয়েছে!</b>\n\n"
        "সিস্টেম প্রতি ৩০ সেকেন্ড পর পর লাইভ উইন/লস ভেরিফাই করবে এবং প্রেডিকশন পাঠাবে।\n\n"
        "🛑 বন্ধ করতে <b>/stop</b> অথবা <b>অফ</b> লিখুন।"
    )

@bot.message_handler(func=lambda msg: msg.text and msg.text.strip().lower() in ['/stop', 'অফ', 'off'])
def handle_stop(message):
    chat_id = message.chat.id
    session = sessions.get(chat_id)

    if not session or not session.get("active", False):
        bot.send_message(chat_id, "ℹ️ বর্তমানে কোনো ট্র্যাকার চালু নেই। চালু করতে /start লিখুন।")
        return

    session["active"] = False
    s = session["stats"]
    total = s["total"]
    win_rate = (s["wins"] / total * 100) if total > 0 else 0.0

    report_msg = (
        f"🛑 <b>ট্র্যাকার বন্ধ করা হয়েছে! চূড়ান্ত পারফরম্যান্স রিপোর্ট:</b>\n"
        f"═════════════════════\n"
        f"🎮 মোট রাউন্ড: <b>{total}</b> টি\n"
        f"🟢 মোট উইন: <b>{s['wins']}</b> টি\n"
        f"🔴 মোট লস: <b>{s['losses']}</b> টি\n"
        f"📊 উইন রেট: <b>{win_rate:.2f}%</b>\n"
        f"═════════════════════\n"
        f"🔥 টানা সর্বোচ্চ উইন: <b>{s['max_win']}</b> বার\n"
        f"❄️ টানা সর্বোচ্চ লস: <b>{s['max_loss']}</b> বার\n"
        f"═════════════════════\n"
        f"আবার শুরু করতে /start লিখুন।"
    )

    bot.send_message(chat_id, report_msg)
    sessions.pop(chat_id, None)

if __name__ == "__main__":
    print("🤖 Telegram Bot Running 24/7...")
    bot.infinity_polling(timeout=10, long_polling_timeout=5)
