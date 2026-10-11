import requests
import time
from datetime import datetime, timedelta, timezone
import sys

# ================= কনফিগারেশন =================
BOT_TOKEN = "8864547814:AAETcz_4N_l23cniISqg01RLCkq_lhRC9K4"
CHAT_ID = "8707571669"
API_URL = "https://draw.ar-lottery01.com/WinGo/WinGo_30S/GetHistoryIssuePage.json"

# ================= টেলিগ্রাম সেন্ডার =================
def send_telegram_message(text):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": text
    }
    try:
        requests.post(url, json=payload, timeout=5)
        print(f"[Telegram Sent] {text}")
    except Exception as e:
        print(f"[Telegram Error] মেসেজ পাঠানো সম্ভব হয়নি: {e}")

# ================= জাভাস্ক্রিপ্ট ভিত্তিক প্রেডিকশন ইঞ্জিন =================

def resolve_colors(number, raw_color_str=""):
    """ড্র হিস্ট্রি থেকে নাম্বার ভিত্তিক কালার নির্ধারণ"""
    if number == 0:
        return ["RED", "VIOLET"]
    if number == 5:
        return ["GREEN", "VIOLET"]
    if number in [1, 3, 7, 9]:
        return ["GREEN"]
    if number in [2, 4, 6, 8]:
        return ["RED"]

    normalized = str(raw_color_str).lower()
    colors = []
    if "red" in normalized:
        colors.append("RED")
    if "green" in normalized:
        colors.append("GREEN")
    if "violet" in normalized:
        colors.append("VIOLET")
    return colors if colors else ["RED"]

def normalize_draw_record(raw_item):
    """API রেসপন্স ডাটা স্ট্যান্ডার্ডাইজেশন"""
    try:
        num = int(raw_item.get("number", raw_item.get("result", 0)))
    except (ValueError, TypeError):
        num = 0
        
    big_small = "BIG" if num >= 5 else "SMALL"
    colors = resolve_colors(num, raw_item.get("color", ""))
    issue_number = str(raw_item.get("issueNumber", raw_item.get("issue", "")))

    return {
        "issueNumber": issue_number,
        "shortIssue": issue_number[-5:] if len(issue_number) >= 5 else issue_number,
        "number": num,
        "colors": colors,
        "bigSmall": big_small
    }

def compute_next_period(current_issue):
    """পরবর্তী পিরিয়ড নাম্বার ক্যালকুলেটর"""
    try:
        return str(int(current_issue) + 1)
    except (ValueError, TypeError):
        return str(int(time.time()))

def analyze_dynamic_history(sizes_history):
    """ডায়নামিক এন-গ্রাম প্যাটার্ন অ্যানালাইসিস (১ থেকে ৬ লেভেল ডেপথ)"""
    best_depth = 0
    big_probability = 0.0
    small_probability = 0.0

    for depth in range(6, 0, -1):
        if len(sizes_history) < depth + 2:
            continue

        current_sequence = "-".join(sizes_history[0:depth])
        big_matches = 0
        small_matches = 0

        # অতীতের হিস্ট্রিতে একই সিকোয়েন্স স্ক্যান করা
        for i in range(1, len(sizes_history) - depth):
            past_sequence = "-".join(sizes_history[i:i + depth])
            if current_sequence == past_sequence:
                next_result = sizes_history[i - 1]
                if next_result == "BIG":
                    big_matches += 1
                elif next_result == "SMALL":
                    small_matches += 1

        if big_matches > 0 or small_matches > 0:
            best_depth = depth
            total_matches = big_matches + small_matches
            big_probability = (big_matches / total_matches) * 100
            small_probability = (small_matches / total_matches) * 100
            break

    return {
        "bestDepth": best_depth,
        "bigProbability": big_probability,
        "smallProbability": small_probability
    }

def execute_prediction_model(history, target_issue):
    """মূল ডিসিশন মেকিং মডেল"""
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

    # ১. ডায়নামিক হিস্ট্রি অ্যানালাইসিস
    dynamic_analysis = analyze_dynamic_history(sizes)

    # ২. কারেন্ট স্ট্রিক (টানা আসা রেজাল্ট) চেক
    streak_count = 1
    while streak_count < len(sizes) and sizes[streak_count] == last_size:
        streak_count += 1

    # ৩. প্যাটার্ন ও ডিসিশন সিলেকশন
    if dynamic_analysis["bestDepth"] >= 2 and abs(dynamic_analysis["bigProbability"] - dynamic_analysis["smallProbability"]) >= 20:
        predicted_size = "BIG" if dynamic_analysis["bigProbability"] > dynamic_analysis["smallProbability"] else "SMALL"
        pattern_name = f"Dynamic AI Match (Depth: {dynamic_analysis['bestDepth']})"
        confidence = max(92.0, 85 + (dynamic_analysis["bestDepth"] * 1.5))
    elif streak_count >= 4:
        predicted_size = last_size
        pattern_name = f"Strong Momentum (x{streak_count})"
        confidence = 94.0
    else:
        # স্ট্যাটিস্টিক্যাল ব্যালেন্সিং (Mean Reversion)
        last10_sizes = sizes[:10]
        big_count = last10_sizes.count("BIG")

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

    # কালার এবং নাম্বার ফিল্টারিং
    recent_colors = [h["colors"][0] for h in history[:5]]
    is_red_dominant = recent_colors.count("RED") > 2

    predicted_color = "RED" if is_red_dominant else "GREEN"
    if predicted_size == "BIG" and predicted_color == "GREEN":
        predicted_color = "RED"
    if predicted_size == "SMALL" and predicted_color == "RED":
        predicted_color = "GREEN"

    recommended_numbers = [6, 8] if predicted_size == "BIG" else [1, 3]

    return {
        "targetIssue": target_issue,
        "shortIssue": short_issue,
        "predictedSize": predicted_size,
        "predictedColor": predicted_color,
        "recommendedNumbers": recommended_numbers,
        "confidenceScore": round(confidence, 1),
        "patternName": pattern_name
    }

# ================= API থেকে ডাটা ফেচার =================
def fetch_history_records():
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Content-Type": "application/json"
    }
    payload = {"pageNumber": 1, "pageSize": 50}
    
    try:
        res = requests.post(API_URL, json=payload, headers=headers, timeout=4)
        if res.status_code != 200:
            res = requests.get(API_URL, headers=headers, timeout=4)
            
        data = res.json()
        
        def extract_list(d):
            if isinstance(d, list) and len(d) > 0 and isinstance(d[0], dict) and ('issueNumber' in d[0] or 'issue' in d[0]):
                return d
            elif isinstance(d, dict):
                for v in d.values():
                    found = extract_list(v)
                    if found: 
                        return found
            return None

        raw_list = extract_list(data)
        if raw_list:
            return [normalize_draw_record(item) for item in raw_list]
    except Exception:
        pass
    return []

# ================= মেইন রানার (কন্টিনিউয়াস রিট্রাই ইঞ্জিন) =================
def main():
    start_time = datetime.now()
    last_hour_notified = 0
    
    last_processed_issue = None
    target_predicted_issue = None
    pending_prediction = None
    
    print("\n[+] WinGo 30s Dynamic Engine Started (Scanning every 1s)...")
    send_telegram_message("WinGo 30s Dynamic Engine Started!")

    while True:
        try:
            # প্রতি সেকেন্ডে লাইভ ড্র চেক
            history = fetch_history_records()
            
            if history:
                latest_draw = history[0]
                curr_issue = latest_draw["issueNumber"]
                curr_num = latest_draw["number"]
                
                # ১. টার্গেট করা ড্র শেষ হয়েছে কিনা যাচাই
                if target_predicted_issue and curr_issue == target_predicted_issue:
                    bd_timezone = timezone(timedelta(hours=6))
                    current_time = datetime.now(bd_timezone).strftime("%I:%M:%S %p")
                    
                    actual_is_big = (curr_num >= 5)
                    predicted_is_big = (pending_prediction["predictedSize"] == "BIG")
                    
                    status = "win" if (actual_is_big == predicted_is_big) else "loss"
                    
                    # টেলিগ্রামে ফলাফল পাঠানো
                    message = f"{current_time} / {curr_issue},{curr_num} / {status}"
                    send_telegram_message(message)
                    
                    # টার্গেট সম্পন্ন হওয়ায় রিসেট
                    target_predicted_issue = None

                # ২. নতুন পিরিয়ড এলে পরবর্তী ড্রয়ের জন্য প্রেডিকশন তৈরি
                if curr_issue != last_processed_issue:
                    last_processed_issue = curr_issue
                    
                    next_issue = compute_next_period(curr_issue)
                    pred = execute_prediction_model(history, next_issue)
                    
                    pending_prediction = pred
                    target_predicted_issue = next_issue
                    
                    print(f"[*] New Target: {next_issue} | Predict: {pred['predictedSize']} ({pred['predictedColor']}) | Pattern: {pred['patternName']} ({pred['confidenceScore']}%)")

            # আপটাইম নোটিফিকেশন (প্রতি ১ ঘণ্টা পর পর)
            uptime = datetime.now() - start_time
            hours_passed = int(uptime.total_seconds() // 3600)
            if hours_passed > last_hour_notified:
                send_telegram_message("complete one hour")
                last_hour_notified = hours_passed

        except Exception as e:
            print(f"[Main Loop Error] {e}")

        # প্রতি ১ সেকেন্ড পরপর রিট্রাই/চেক
        time.sleep(1)

if __name__ == "__main__":
    main()
        
