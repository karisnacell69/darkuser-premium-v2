#!/usr/bin/env python3
"""\    DarkUser Telegram SSH+UDP Manager (Premium)
Full-featured Telegram bot to manage SSH & UDP users on the local VPS.
Commands (admin only):
  /create <username> <days> [password]
  /renew <username> <days>
  /expire <username>
  /lock <username>
  /unlock <username>
  /delete <username>
  /list
  /info <username>
  /payload <type> <host> <port> [username]
  /exec <cmd>
Note: Run as root (needs to call useradd/chpasswd/chage/deluser)
"""
import os
import shlex
import subprocess
import secrets
import string
import logging
from datetime import datetime, timedelta
from pathlib import Path
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes, MessageHandler, filters

TOKEN = os.getenv('DARKUSER_BOT_TOKEN', 'REPLACE_WITH_YOUR_TOKEN')
ADMIN_ID = int(os.getenv('DARKUSER_ADMIN_ID', '0'))

TRACK_DIR = Path('/etc/darkuser_bot')
TRACK_DIR.mkdir(parents=True, exist_ok=True)
USERS_CSV = TRACK_DIR / 'users.csv'
LOG_FILE = Path('/var/log/darkuser-bot.log')

logging.basicConfig(level=logging.INFO, filename=str(LOG_FILE), format='%(asctime)s %(levelname)s %(message)s')
logger = logging.getLogger('darkuser_bot')

def is_admin(user_id: int) -> bool:
    return user_id == ADMIN_ID

def gen_password(length: int = 12) -> str:
    alphabet = string.ascii_letters + string.digits + '!@#$%_-.' 
    return ''.join(secrets.choice(alphabet) for _ in range(length))

def run_shell(cmd: str, capture_output: bool = False):
    logger.info('Run shell: %s', cmd)
    args = shlex.split(cmd)
    res = subprocess.run(args, capture_output=capture_output, text=True)
    return res

def record_user(username: str, password: str, expires: str, status: str = 'active'):
    now = datetime.utcnow().isoformat()
    line = f"{username},{password},{expires},{status},{now}\n"
    with USERS_CSV.open('a') as f:
        f.write(line)

def read_users():
    if not USERS_CSV.exists():
        return []
    out = []
    for line in USERS_CSV.read_text().splitlines():
        if not line.strip():
            continue
        parts = line.split(',')
        out.append({'username': parts[0], 'password': parts[1], 'expires': parts[2], 'status': parts[3], 'created': parts[4]})
    return out

def overwrite_users(list_of_dicts):
    lines = []
    for u in list_of_dicts:
        lines.append(f"{u['username']},{u['password']},{u['expires']},{u.get('status','active')},{u.get('created',datetime.utcnow().isoformat())}")
    USERS_CSV.write_text('\n'.join(lines) + ('\n' if lines else ''))

def set_expiry(username: str, days: int):
    if days <= 0:
        res = run_shell(f'chage -E -1 {username}')
    else:
        expire_date = (datetime.utcnow() + timedelta(days=days)).strftime('%Y-%m-%d')
        res = run_shell(f'chage -E {expire_date} {username}')
    return res.returncode == 0

def lock_user(username: str):
    return run_shell(f'passwd -l {username}').returncode == 0

def unlock_user(username: str):
    return run_shell(f'passwd -u {username}').returncode == 0

def expire_now(username: str):
    y = (datetime.utcnow() - timedelta(days=1)).strftime('%Y-%m-%d')
    return run_shell(f'chage -E {y} {username}').returncode == 0

def generate_payload(payload_type: str, host: str, port: str, username: str = '') -> str:
    host = host.strip()
    port = port.strip()
    if payload_type == 'ssh-ws':
        return (f"GET /ws HTTP/1.1\r\nHost: {host}:{port}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n\r\n")
    if payload_type == 'ssh-wss':
        return (f"GET /ws HTTP/1.1\r\nHost: {host}\r\nUser-Agent: Mozilla/5.0\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n\r\n")
    if payload_type == 'raw-http':
        return (f"CONNECT {host}:{port} HTTP/1.1\r\nHost: {host}\r\n\r\n")
    if payload_type == 'udp-badvpn':
        return (f"BadVPN UDPGW server: {host}\nPorts: e.g. 7100,7200,7300\nClient: use udp2raw+udpgw or similar to forward UDP via TCP/WS to the server on these ports.")
    return 'Unknown payload type. Supported: ssh-ws, ssh-wss, raw-http, udp-badvpn'

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_admin(uid):
        await update.message.reply_text('Unauthorized.')
        return
    text = ('DarkUser SSH+UDP Panel\n'
            '/create <username> <days> [password]\n'
            '/renew <username> <days>\n'
            '/expire <username>\n'
            '/lock <username>\n'
            '/unlock <username>\n'
            '/delete <username>\n'
            '/list\n'
            '/info <username>\n'
            '/payload <type> <host> <port> [username]\n'
            '/exec <cmd>\n')
    await update.message.reply_text(text)

async def create(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_admin(uid):
        await update.message.reply_text('Unauthorized.')
        return
    args = context.args
    if len(args) < 2:
        await update.message.reply_text('Usage: /create <username> <days> [password]')
        return
    username = args[0]
    try:
        days = int(args[1])
    except ValueError:
        await update.message.reply_text('Days must be an integer.')
        return
    password = args[2] if len(args) >= 3 else gen_password()
    if subprocess.run(['id', username], capture_output=True).returncode == 0:
        await update.message.reply_text(f'User {username} already exists.')
        return
    res = run_shell(f'useradd -m -s /bin/bash {username}')
    if res.returncode != 0:
        await update.message.reply_text(f'Failed to create user: {res.stderr}')
        return
    p = run_shell(f'echo {shlex.quote(username)}:{shlex.quote(password)} | chpasswd')
    if p.returncode != 0:
        await update.message.reply_text('Failed to set password.')
        return
    if days > 0:
        expire_date = (datetime.utcnow() + timedelta(days=days)).strftime('%Y-%m-%d')
        run_shell(f'chage -E {expire_date} {username}')
        expires = expire_date
    else:
        expires = 'never'
    record_user(username, password, expires, 'active')
    ip = run_shell('curl -s https://ifconfig.me', capture_output=True).stdout.strip() or ''
    port = '22'
    try:
        conf = Path('/etc/ssh/sshd_config').read_text()
        for line in conf.splitlines():
            if line.strip().startswith('Port'):
                port = line.split()[1]
                break
    except Exception:
        pass
    reply = (f'✅ User created\nUsername: {username}\nPassword: {password}\nExpires: {expires}\nHost/IP: {ip or "<not-detected>"}\nPort: {port}')
    await update.message.reply_text(reply)

async def renew(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_admin(uid):
        await update.message.reply_text('Unauthorized.')
        return
    if len(context.args) < 2:
        await update.message.reply_text('Usage: /renew <username> <days>')
        return
    username = context.args[0]
    try:
        days = int(context.args[1])
    except ValueError:
        await update.message.reply_text('Days must be integer.')
        return
    users = read_users()
    target = next((u for u in users if u['username'] == username), None)
    if not target:
        await update.message.reply_text('User not tracked.')
        return
    if target['expires'] == 'never':
        new_expiry = (datetime.utcnow() + timedelta(days=days)).strftime('%Y-%m-%d')
    else:
        try:
            cur = datetime.strptime(target['expires'], '%Y-%m-%d')
        except Exception:
            cur = datetime.utcnow()
        new_expiry = (cur + timedelta(days=days)).strftime('%Y-%m-%d')
    run_shell(f'chage -E {new_expiry} {username}')
    target['expires'] = new_expiry
    target['status'] = 'active'
    overwrite_users(users)
    await update.message.reply_text(f'♻️ User {username} renewed for {days} days. New expiry: {new_expiry}')

async def expire_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_admin(uid):
        await update.message.reply_text('Unauthorized.')
        return
    if len(context.args) < 1:
        await update.message.reply_text('Usage: /expire <username>')
        return
    username = context.args[0]
    ok = expire_now(username)
    if not ok:
        await update.message.reply_text('Failed to expire user or user does not exist.')
        return
    users = read_users()
    for u in users:
        if u['username'] == username:
            u['status'] = 'expired'
    overwrite_users(users)
    await update.message.reply_text(f'⚠️ User {username} expired (disabled).')

async def lock(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_admin(uid):
        await update.message.reply_text('Unauthorized.')
        return
    if len(context.args) < 1:
        await update.message.reply_text('Usage: /lock <username>')
        return
    username = context.args[0]
    ok = lock_user(username)
    if not ok:
        await update.message.reply_text('Failed to lock user or user does not exist.')
        return
    users = read_users()
    for u in users:
        if u['username'] == username:
            u['status'] = 'locked'
    overwrite_users(users)
    await update.message.reply_text(f'🔒 User {username} locked.')

async def unlock(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_admin(uid):
        await update.message.reply_text('Unauthorized.')
        return
    if len(context.args) < 1:
        await update.message.reply_text('Usage: /unlock <username>')
        return
    username = context.args[0]
    ok = unlock_user(username)
    if not ok:
        await update.message.reply_text('Failed to unlock user or user does not exist.')
        return
    users = read_users()
    for u in users:
        if u['username'] == username:
            u['status'] = 'active'
    overwrite_users(users)
    await update.message.reply_text(f'🔓 User {username} unlocked.')

async def delete_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_admin(uid):
        await update.message.reply_text('Unauthorized.')
        return
    if len(context.args) < 1:
        await update.message.reply_text('Usage: /delete <username>')
        return
    username = context.args[0]
    if subprocess.run(['id', username], capture_output=True).returncode != 0:
        await update.message.reply_text('User does not exist.')
        return
    run_shell(f'deluser --remove-home {username}')
    users = read_users()
    users = [u for u in users if u['username'] != username]
    overwrite_users(users)
    await update.message.reply_text(f'User {username} deleted.')

async def list_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_admin(uid):
        await update.message.reply_text('Unauthorized.')
        return
    users = read_users()
    if not users:
        await update.message.reply_text('No users tracked yet.')
        return
    msg = 'Tracked users:\n'
    for u in users:
        msg += f"{u['username']} | expires: {u['expires']} | status: {u.get('status','active')}\n"
    await update.message.reply_text(msg)

async def info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_admin(uid):
        await update.message.reply_text('Unauthorized.')
        return
    if not context.args:
        await update.message.reply_text('Usage: /info <username>')
        return
    username = context.args[0]
    res = run_shell(f'chage -l {username}', capture_output=True)
    if res.returncode != 0:
        await update.message.reply_text('Failed to get info or user does not exist.')
        return
    users = read_users()
    pw = next((u['password'] for u in users if u['username'] == username), '<unknown>')
    status = next((u['status'] for u in users if u['username'] == username), '<unknown>')
    await update.message.reply_text(f'Info for {username}:\nPassword: {pw}\nStatus: {status}\n{res.stdout}')

async def payload_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_admin(uid):
        await update.message.reply_text('Unauthorized.')
        return
    args = context.args
    if len(args) < 3:
        await update.message.reply_text('Usage: /payload <type> <host> <port> [username]')
        return
    ptype = args[0]
    host = args[1]
    port = args[2]
    username = args[3] if len(args) >= 4 else ''
    text = generate_payload(ptype, host, port, username)
    if len(text) > 3500:
        text = text[:3500] + '\n...[truncated]'
    await update.message.reply_text(f'Payload ({ptype}):\n{text}')

async def exec_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_admin(uid):
        await update.message.reply_text('Unauthorized.')
        return
    if not context.args:
        await update.message.reply_text('Usage: /exec <command>')
        return
    cmd = ' '.join(context.args)
    res = run_shell(cmd, capture_output=True)
    out = res.stdout or res.stderr or 'No output.'
    if len(out) > 3500:
        out = out[:3500] + '\n...[truncated]'
    await update.message.reply_text(f'Command exit {res.returncode}\nOutput:\n{out}')

async def unknown(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text('Unknown command. Use /start to see commands.')

def main():
    if TOKEN == 'REPLACE_WITH_YOUR_TOKEN' or ADMIN_ID == 0:
        logger.error('Set DARKUSER_BOT_TOKEN and DARKUSER_ADMIN_ID env vars.')
        return
    if os.geteuid() != 0:
        logger.error('This bot must run as root (sudo).')
        return
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler('start', start))
    app.add_handler(CommandHandler('create', create))
    app.add_handler(CommandHandler('renew', renew))
    app.add_handler(CommandHandler('expire', expire_cmd))
    app.add_handler(CommandHandler('lock', lock))
    app.add_handler(CommandHandler('unlock', unlock))
    app.add_handler(CommandHandler('delete', delete_user))
    app.add_handler(CommandHandler('list', list_users))
    app.add_handler(CommandHandler('info', info))
    app.add_handler(CommandHandler('payload', payload_cmd))
    app.add_handler(CommandHandler('exec', exec_cmd))
    app.add_handler(MessageHandler(filters.COMMAND, unknown))
    logger.info('DarkUser Bot started (admin: %s)', ADMIN_ID)
    app.run_polling()

if __name__ == '__main__':
    main()
