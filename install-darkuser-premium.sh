#!/usr/bin/env bash
# DarkUser Premium v2 Installer
# Ubuntu 18.04 - 24.04 compatible
# Installs: openssh, dropbear, stunnel, nginx (ws proxy), certbot, badvpn (udpgw)
# Sets up: systemd services, firewall, cron job for expiry cleanup
set -euo pipefail
IFS=$'\n\t'

DOMAIN="${DOMAIN:-your.domain.tld}"
EMAIL="${EMAIL:-admin@${DOMAIN}}"

if [ "$EUID" -ne 0 ]; then
  echo "Run as root."
  exit 1
fi

echo "[*] Updating system..."
apt-get update -y
apt-get upgrade -y

echo "[*] Installing prerequisites..."
apt-get install -y curl wget git lsb-release apt-transport-https ca-certificates software-properties-common build-essential cmake

echo "[*] Installing core services..."
apt-get install -y openssh-server dropbear nginx certbot python3-certbot-nginx stunnel4

systemctl enable --now ssh
systemctl enable --now dropbear
systemctl enable --now nginx
systemctl enable --now stunnel4

echo "[*] Configure Dropbear ports..."
sed -i 's/NO_START=1/NO_START=0/' /etc/default/dropbear || true
grep -q "DROPBEAR_PORT" /etc/default/dropbear || echo 'DROPBEAR_PORT=143' >> /etc/default/dropbear
grep -q "DROPBEAR_EXTRA_ARGS" /etc/default/dropbear || echo 'DROPBEAR_EXTRA_ARGS="-p 109"' >> /etc/default/dropbear
systemctl restart dropbear || true

echo "[*] Configure stunnel (wrapper for TLS)..."
mkdir -p /etc/stunnel
cat > /etc/stunnel/stunnel.conf <<'EOF'
pid = /var/run/stunnel.pid
foreground = no
setuid = root
setgid = root
[ssh]
accept = 127.0.0.1:4443
connect = 127.0.0.1:22
EOF
systemctl restart stunnel4 || true

echo "[*] Configure Nginx for WebSocket proxy (ws -> localhost:22)..."
cat > /etc/nginx/sites-available/darkuser_ws.conf <<'EOF'
server {
    listen 80;
    server_name __DOMAIN_PLACEHOLDER__;
    location /ws {
        proxy_pass http://127.0.0.1:22;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "Upgrade";
        proxy_set_header Host $host;
        proxy_connect_timeout 7d;
        proxy_send_timeout 7d;
        proxy_read_timeout 7d;
    }
    location / {
        default_type text/plain;
        return 200 'DarkUser WS proxy';
    }
}
EOF
sed -i "s|__DOMAIN_PLACEHOLDER__|${DOMAIN}|g" /etc/nginx/sites-available/darkuser_ws.conf
ln -sf /etc/nginx/sites-available/darkuser_ws.conf /etc/nginx/sites-enabled/darkuser_ws.conf
systemctl reload nginx || true

echo "[*] Build and install BadVPN (udpgw)..."
tmpdir=$(mktemp -d)
cd "$tmpdir"
apt-get install -y git cmake build-essential
if [ -d badvpn ]; then rm -rf badvpn; fi
git clone https://github.com/ambrop72/badvpn.git
cd badvpn
cmake -DBUILD_NOTHING_BY_DEFAULT=1 -DBUILD_UDPGW=1 .
make -j$(nproc) install || true
cd /
rm -rf "$tmpdir"

echo "[*] Create systemd template for badvpn@.service..."
cat > /etc/systemd/system/badvpn@.service <<'EOF'
[Unit]
Description=BadVPN UDPGW instance %i
After=network.target

[Service]
Type=simple
ExecStart=/usr/local/bin/badvpn-udpgw --listen-addr 127.0.0.1:%i --max-clients 1024 --max-connections-for-client 16
Restart=on-failure
User=root

[Install]
WantedBy=multi-user.target
EOF

for p in 7100 7200 7300; do
  systemctl daemon-reload
  systemctl enable --now badvpn@${p}.service || true
done

echo "[*] Open firewall ports (ufw if available)..."
if command -v ufw >/dev/null 2>&1; then
  ufw allow 22
  ufw allow 80
  ufw allow 443
  ufw allow 143
  ufw allow 109
  ufw allow 7100:7300/tcp
  ufw --force enable || true
else
  # fallback using iptables (not persistent)
  iptables -I INPUT -p tcp --dport 22 -j ACCEPT || true
  iptables -I INPUT -p tcp --dport 80 -j ACCEPT || true
  iptables -I INPUT -p tcp --dport 443 -j ACCEPT || true
  iptables -I INPUT -p tcp --dport 143 -j ACCEPT || true
  iptables -I INPUT -p tcp --dport 109 -j ACCEPT || true
fi

if [ -n "${DOMAIN}" ] && [ "${DOMAIN}" != "your.domain.tld" ]; then
  echo "[*] Attempting to obtain Let's Encrypt certificate for ${DOMAIN}..."
  certbot --nginx -d "${DOMAIN}" --non-interactive --agree-tos -m "${EMAIL}" || echo "certbot failed - check DNS or logs"
  # create SSL nginx conf if cert obtained
  if [ -d /etc/letsencrypt/live/${DOMAIN} ]; then
    cat > /etc/nginx/sites-available/darkuser_ws_ssl.conf <<'EOF'
server {
    listen 443 ssl http2;
    server_name __DOMAIN_PLACEHOLDER__;
    ssl_certificate /etc/letsencrypt/live/__DOMAIN_PLACEHOLDER__/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/__DOMAIN_PLACEHOLDER__/privkey.pem;
    location /ws {
        proxy_pass http://127.0.0.1:22;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "Upgrade";
        proxy_set_header Host $host;
        proxy_connect_timeout 7d;
        proxy_send_timeout 7d;
        proxy_read_timeout 7d;
    }
}
EOF
    sed -i "s|__DOMAIN_PLACEHOLDER__|${DOMAIN}|g" /etc/nginx/sites-available/darkuser_ws_ssl.conf
    ln -sf /etc/nginx/sites-available/darkuser_ws_ssl.conf /etc/nginx/sites-enabled/darkuser_ws_ssl.conf
    systemctl reload nginx || true
  fi
else
  echo "[*] DOMAIN not set or left default; skipping certbot step."
fi

echo "[*] Install complete. Creating banner..."
cat > /etc/issue.net <<'EOF'
############################################################
#                       DarkUser                           #
#  GitHub: https://github.com/karisnacell69                #
############################################################
EOF
if ! grep -Eq "^Banner\s+/etc/issue.net" /etc/ssh/sshd_config; then
  echo "Banner /etc/issue.net" >> /etc/ssh/sshd_config
  systemctl reload ssh
fi

echo "[*] Setting up cron job to cleanup expired users daily at 00:10..."
cat > /etc/cron.d/darkuser-expire <<'EOF'
10 0 * * * root /usr/local/bin/darkuser-expire-cron.sh
EOF

cat > /usr/local/bin/darkuser-expire-cron.sh <<'EOF'
#!/usr/bin/env bash
# Remove or disable users whose expiry has passed in /etc/darkuser_bot/users.csv
set -euo pipefail
if [ ! -f /etc/darkuser_bot/users.csv ]; then
  exit 0
fi
tmp=$(mktemp)
while IFS=, read -r username password expires status created; do
  if [ -z "$username" ]; then
    continue
  fi
  if [ "$expires" = "never" ]; then
    echo "$username,$password,$expires,$status,$created" >> "$tmp"
    continue
  fi
  # if expired date < today -> lock user and mark expired
  if date -d "$expires" +%s 2>/dev/null <= date -d "$(date +%Y-%m-%d)" +%s 2>/dev/null; then
    passwd -l "$username" 2>/dev/null || true
    # mark as expired
    echo "$username,$password,$expires,expired,$created" >> "$tmp"
  else
    echo "$username,$password,$expires,$status,$created" >> "$tmp"
  fi
done < /etc/darkuser_bot/users.csv || true
mv "$tmp" /etc/darkuser_bot/users.csv
EOF

chmod +x /usr/local/bin/darkuser-expire-cron.sh
systemctl restart cron || true

echo "[*] Installer finished. Review generated configs and ensure DNS points to server for certbot to succeed."
