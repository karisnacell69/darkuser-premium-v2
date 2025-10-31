# DarkUser Premium Suite v2

## What it includes
- `install-darkuser-premium.sh` - full installer (SSH, Dropbear, Stunnel, Nginx, Certbot, BadVPN)
- `telegram-ssh-panel.py` - Telegram admin bot to manage SSH/UDP users
- `darkuser-bot.service` - systemd unit for the bot
- `update-darkuser.sh` - optional auto-update script (pull from your GitHub)
- `payloads.txt` - example payloads for WS/WSS/UDP

## Quick install
1. Upload the package to your VPS and extract.
2. Edit `install-darkuser-premium.sh`, set DOMAIN and EMAIL, then run:
   ```
   sudo bash install-darkuser-premium.sh
   ```
3. Put `telegram-ssh-panel.py` to `/usr/local/bin/telegram-ssh-panel.py` and make executable:
   ```
   sudo cp telegram-ssh-panel.py /usr/local/bin/telegram-ssh-panel.py
   sudo chmod +x /usr/local/bin/telegram-ssh-panel.py
   ```
4. Edit `darkuser-bot.service` to set your `DARKUSER_BOT_TOKEN` and `DARKUSER_ADMIN_ID`, then install:
   ```
   sudo cp darkuser-bot.service /etc/systemd/system/darkuser-bot.service
   sudo systemctl daemon-reload
   sudo systemctl enable --now darkuser-bot.service
   ```
5. Configure firewall and ensure domain DNS points to server if using certbot.

## Security notes
- The bot must run as root to manage system users. Keep the bot token & admin id secret.
- Test on a non-production VPS first.
- Back up `/etc/darkuser_bot/users.csv` regularly.

## License & attribution
For your personal/professional use. Attribution to 'DarkUser' and GitHub: https://github.com/karisnacell69
