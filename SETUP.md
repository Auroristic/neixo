# Neixo — Ubuntu Server Setup Guide

## Project Structure
```
neixo/
├── neixo.py
├── neixoconfig.py
├── neixoset.toml
├── requirements.txt
├── .env
├── cogs/
│   ├── music.py
│   ├── help.py
│   └── events/
│       └── ontag.py
├── data/
└── assets/
```

---

## Step 1 — System dependencies

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y python3 python3-pip python3-venv git curl wget openjdk-17-jre-headless
```

check java:
```bash
java -version
# should say openjdk 17
```

---

## Step 2 — Upload your bot files

from your local machine (run this on YOUR pc, not the server):
```bash
scp -r ./neixo ubuntu@YOUR_SERVER_IP:~/neixo
```
or just clone/upload via whatever method you use (VSCode Remote SSH works fine too).

---

## Step 3 — Python venv + dependencies

```bash
cd ~/neixo
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

---

## Step 4 — Set up Lavalink

Lavalink is the audio server wavelink talks to. It needs Java 17.

```bash
mkdir ~/lavalink && cd ~/lavalink
wget https://github.com/lavalink-devs/Lavalink/releases/latest/download/Lavalink.jar
```

create the config file:
```bash
nano application.yml
```

paste this:
```yaml
server:
  port: 2333
  address: 0.0.0.0

lavalink:
  server:
    password: "youshallnotpass"
    sources:
      youtube: true
      bandcamp: true
      soundcloud: true
      twitch: true
      vimeo: true
      http: true
      local: false
    filters:
      volume: true
      equalizer: true
      karaoke: true
      timescale: true
      tremolo: true
      vibrato: true
      distortion: true
      rotation: true
      channelMix: true
      lowPass: true
    bufferDurationMs: 400
    frameBufferDurationMs: 5000
    youtubePlaylistLoadLimit: 6
    opusEncodingQuality: 10
    resamplingQuality: LOW
    trackStuckThresholdMs: 10000
    useSeekGhosting: true
    youtubeSearchEnabled: true
    soundcloudSearchEnabled: true

metrics:
  prometheus:
    enabled: false

sentry:
  dsn: ""

logging:
  level:
    root: INFO
    lavalink: INFO
```

---

## Step 5 — Run Lavalink with PM2

install pm2:
```bash
sudo npm install -g pm2
# if npm not installed:
sudo apt install -y nodejs npm
sudo npm install -g pm2
```

start lavalink:
```bash
cd ~/lavalink
pm2 start "java -jar Lavalink.jar" --name lavalink
pm2 save
pm2 startup  # follow the command it gives you
```

check it's running:
```bash
pm2 logs lavalink
# wait ~10s, should say "Lavalink is ready to accept connections"
```

---

## Step 6 — Configure `.env`

The bot requires **`DISCORD_TOKEN`** in `~/neixo/.env`.

```bash
cd ~/neixo
nano .env
```

```env
DISCORD_TOKEN=your_actual_bot_token
# Optional (defaults match code):
LAVALINK_URI=http://localhost:2333
LAVALINK_PASS=youshallnotpass
# Optional:
# CREATOR_ID=887382911924441139
```

---

## Step 7 — Run Neixo with PM2

```bash
cd ~/neixo
source venv/bin/activate

pm2 start "venv/bin/python neixo.py" --name neixo
pm2 save
```

check logs:
```bash
pm2 logs neixo
```

---

## Step 8 — Font files for music cards

The music card generator uses `arialbd.ttf` and `arial.ttf`.
On Ubuntu these aren't installed by default:

```bash
sudo apt install -y ttf-mscorefonts-installer
sudo fc-cache -fv
```

if that doesn't work, copy the fonts manually from your Windows machine:
- `C:\Windows\Fonts\arial.ttf`
- `C:\Windows\Fonts\arialbd.ttf`

upload to the bot folder:
```bash
scp arial.ttf arialbd.ttf ubuntu@YOUR_SERVER_IP:~/neixo/
```

---

## PM2 quick reference

```bash
pm2 list              # see all processes
pm2 logs neixo        # bot logs
pm2 logs lavalink     # lavalink logs
pm2 restart neixo     # restart bot
pm2 restart lavalink  # restart lavalink
pm2 stop neixo        # stop bot
```

---

## Firewall

Lavalink runs on port 2333 locally — no need to open it publicly since the bot and lavalink are on the same server. Only open it if you move lavalink to a separate machine.

```bash
# make sure 2333 is NOT publicly exposed
sudo ufw status
# if ufw is active, 2333 should not be in the allow list
```

---

## Updating the bot

```bash
cd ~/neixo
# edit your files via VSCode Remote SSH or nano
pm2 restart neixo
```

---

## Troubleshooting

| problem | fix |
|---|---|
| `Wavelink connection failed` | lavalink not running, check `pm2 logs lavalink` |
| `No module named wavelink` | forgot to activate venv or pip install |
| music card crashes | arial fonts missing, see Step 8 |
| bot not responding | check `pm2 logs neixo` for errors |
| `DISCORD_TOKEN not set` | .env file missing or wrong path |
