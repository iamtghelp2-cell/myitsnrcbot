# 🚀 Itsnrcbot

Private Telegram bot to save / forward content from public & private channels (including restricted ones) with custom caption, thumbnail, word replace/delete, prefix/suffix, ad-cleaner and forum-topic routing.

📢 Channel: https://t.me/iamtghelp  |  👤 Owner: @IamTgBhai

## Access (strict)
Only these can use the bot:
- **SUDO_USERS** (`config.py`, or the `SUDO_USERS` env variable) and **Admins** (owner + `ADMINS` env) – never expire.
- Users granted premium by an admin. Everyone else gets an **Access Denied** message (with @IamTgBhai and the channel link) for `/start`, any command, any link and any button press.

Admin commands (owner / `ADMINS` only):
- `/add_premium <user_id> <days>` – premium for N days (days are *added* to an active plan); `/add_premium <user_id>` – lifetime
- `/remove_premium <user_id>` – remove (`/adduser`, `/removeuser` still work as aliases)
- `/premium_users` (or `/allowed`) – list premium users + expiry
- `/ban <id>` / `/unban <id>`, `/broadcast` (reply to a message), `/users`, `/set_dump <user_id> <chat_id>`

Premium expiry is stored as a UTC timestamp in MongoDB and checked on every request.

## User commands
`/start /help /settings /login /logout /cancel /myplan /premium`
`/setchat /clearchat /set_topics /settopic /map_topic /show_topics /reset_topics`
`/set_thumb /view_thumb /del_thumb /set_caption /see_caption /del_caption`
`/clean_ads /replace /clear_replace /prefix /suffix /set_del_word /rem_del_word /set_repl_word /rem_repl_word`

Caption placeholders: `{filename}`, `{size}`, `{caption}` (cleaned original caption).
All settings are saved in MongoDB, so they survive restarts.

## How transfers work (nothing is written to disk)
1. **Copy allowed** → `copy_message`, fully server-side: no download, no upload.
2. *(optional, `USER_SESSION_COPY=True`)* the user's session copies server-side into the target chat when the bot itself can't reach the source.
3. **Restricted chat / bot has no access** → the file is read through the user's session:
   - files up to `RAM_FILE_LIMIT_MB` (default 20 MB) are held in RAM and re-sent;
   - bigger files are **streamed**: `STREAM_WORKERS` parallel 8 MB download segments feed 512 KB upload parts (`UPLOAD_PARALLEL` in flight) – memory per transfer is only about `(STREAM_WORKERS+1) × STREAM_SEGMENT_MB` MB.
   
   Limits: 2 GB per file (4 GB if the bot account is Premium).

## Same code on every server
Nothing in the code changes between hosts - only the settings (`BOT_TOKEN`, `API_ID`, `API_HASH`, `DB_URI`).
- **VPS:** put them in `.env`. **Koyeb / Render / Heroku / Railway:** put them in the host's Environment / Config Vars.
- Web-service hosts (Koyeb, Render) need an open port: the bot opens it automatically when the host sets `PORT`
  (or set `KEEP_ALIVE=True`; the port is `PORT`, default 8080).
- Before the first start run `python check_setup.py` - it catches wrong folder names, missing packages and bad settings.

## Setup on a VPS (Ubuntu)
```bash
sudo apt update && sudo apt install -y python3 python3-venv python3-pip
cd Itsnrcbot
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env && nano .env      # fill BOT_TOKEN, API_ID, API_HASH, DB_URI
python check_setup.py                  # checks folders, packages, .env, MongoDB
python bot.py                          # start
```
Run 24/7 with systemd: edit paths in `itsnrcbot.service`, copy it to `/etc/systemd/system/`, then
`sudo systemctl enable --now itsnrcbot` (logs: `journalctl -u itsnrcbot -f`).

Docker: `docker build -t itsnrcbot . && docker run -d --restart always --env-file .env itsnrcbot`

## Speed tips
- Choose a VPS with a fast network (1 Gbps), 2 vCPU, 2–4 GB RAM; Singapore or Europe is close to Telegram data centers.
- Messages are fetched 100 at a time. Raise `STREAM_WORKERS` (e.g. 6–8) on fast links; lower it on small-RAM servers.

Licensed under MIT (see LICENSE).
