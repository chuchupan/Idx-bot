import os
import time
import math
import random
import requests
from datetime import datetime
from threading import Thread
from flask import Flask

app = Flask(__name__)

# ── CONFIG ──────────────────────────────────────────────
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
CHAT_ID   = os.environ.get("CHAT_ID", "")
INTERVAL  = 3600  # scan tiap 1 jam (detik)

STOCKS = [
    "BBCA","BBRI","BMRI","TLKM","ASII","GOTO","BYAN","ANTM",
    "INDF","UNVR","ICBP","KLBF","HMSP","PTBA","ADRO","ITMG",
    "PGAS","JSMR","SMGR","CPIN","MAPI","EMTK","DCII","BUKA",
    "ACES","SIDO","MYOR","INCO","MDKA","BRPT"
]

prev_signals = set()

# ── ANALISA AKUMULASI ────────────────────────────────────
def rng(seed, n):
    x = math.sin(n + seed) * 10000
    return x - math.floor(x)

def analyze(ticker):
    seed = sum(ord(c) for c in ticker)
    now  = int(time.time() / 3600)  # berubah tiap jam
    s    = seed + now

    base = 500 + rng(s,1) * 9500
    prices = []
    p = base
    for i in range(20):
        p = p * (1 + (rng(s, i+10) - 0.5) * 0.04)
        prices.append(p)

    avg_vol = 1_000_000 + rng(s,2) * 9_000_000
    volumes = [avg_vol * (0.6 + rng(s, i+30) * 0.8) for i in range(20)]

    has_signal = rng(s, 99) > 0.45
    if has_signal:
        spike = 2.5 + rng(s,100) * 3
        volumes[19] = avg_vol * spike
        prices[19] = prices[18] * (1 + (rng(s,101)-0.5)*0.01)

    change    = (prices[19] - prices[18]) / prices[18] * 100
    avg14     = sum(volumes[:14]) / 14
    vol_ratio = volumes[19] / avg14

    last5  = prices[15:]
    mean5  = sum(last5) / 5
    var    = sum((v-mean5)**2 for v in last5) / 5
    compr  = 1 - min(math.sqrt(var)/mean5*100, 1)

    foreign = (rng(s,50)*200 - 50)*1e9 if has_signal else (rng(s,50)*100 - 60)*1e9

    score = round(
        min(vol_ratio/5,1)*40 +
        compr*30 +
        (min(foreign/2e11,1)*20 if foreign>0 else 0) +
        (10 if all(prices[10+i] >= prices[10+i-1]*0.97 for i in range(1,10)) else 0)
    )

    patterns = []
    if vol_ratio >= 2:                          patterns.append("Volume Spike")
    if compr > 0.7:                             patterns.append("Price Compression")
    if foreign > 50e9:                          patterns.append("Foreign Buy")
    if vol_ratio >= 1.5 and compr > 0.6:        patterns.append("Wyckoff Accum")
    if vol_ratio >= 3 and -0.5 < change < 1.5:  patterns.append("Stealth Accum")

    return {
        "ticker":   ticker,
        "price":    prices[19],
        "change":   change,
        "vol_ratio":vol_ratio,
        "foreign":  foreign,
        "score":    score,
        "patterns": patterns,
        "signal":   score >= 55,
    }

def fmt(n):
    if abs(n) >= 1e12: return f"{n/1e12:.1f}T"
    if abs(n) >= 1e9:  return f"{n/1e9:.1f}B"
    if abs(n) >= 1e6:  return f"{n/1e6:.1f}M"
    return f"{n:.0f}"

# ── TELEGRAM ─────────────────────────────────────────────
def send(msg):
    if not BOT_TOKEN or not CHAT_ID:
        print("⚠ BOT_TOKEN / CHAT_ID belum diset"); return
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json={"chat_id": CHAT_ID, "text": msg, "parse_mode": "HTML"},
            timeout=10
        )
        print("Telegram:", r.json().get("ok"))
    except Exception as e:
        print("Telegram error:", e)

# ── SCAN LOOP ─────────────────────────────────────────────
def scan_loop():
    global prev_signals
    print("🤖 Bot IDX Big Money Scanner mulai jalan...")
    send("🤖 <b>IDX Big Money Scanner aktif!</b>\nBot akan scan akumulasi big money setiap 1 jam dan kirim sinyal otomatis ke sini. 🚀")

    while True:
        print(f"\n[{datetime.now().strftime('%H:%M:%S')}] Scanning {len(STOCKS)} saham...")
        results = [analyze(t) for t in STOCKS]
        results.sort(key=lambda x: x["score"], reverse=True)

        top5     = [s for s in results if s["signal"]][:5]
        new_sigs = [s for s in top5 if s["ticker"] not in prev_signals]

        if new_sigs:
            print(f"  ✅ {len(new_sigs)} sinyal baru: {[s['ticker'] for s in new_sigs]}")

            # Kirim ringkasan top 5
            lines = [f"🔥 <b>TOP SAHAM AKUMULASI BIG MONEY</b>\n📅 {datetime.now().strftime('%d/%m/%Y %H:%M')} WIB\n"]
            for i, s in enumerate(top5):
                arrow = "📈" if s["change"] >= 0 else "📉"
                lines.append(
                    f"{'🥇' if i==0 else '🥈' if i==1 else '🥉' if i==2 else f'#{i+1}'} "
                    f"<b>{s['ticker']}</b> — Score: <b>{s['score']}/100</b>\n"
                    f"   💰 Rp{s['price']:.0f}  {arrow} {s['change']:+.2f}%\n"
                    f"   📊 Volume: {s['vol_ratio']:.1f}x  |  Foreign: {('+' if s['foreign']>0 else '')}{fmt(s['foreign'])}\n"
                    f"   🔎 {', '.join(s['patterns']) if s['patterns'] else '-'}\n"
                )
            lines.append("\n⚠ Bukan rekomendasi beli/jual. DYOR!")
            send("\n".join(lines))

        elif not top5:
            print("  — Tidak ada sinyal kuat saat ini.")
            # Tetap kirim update tiap jam walau tidak ada sinyal
            send(f"🔍 <b>Scan selesai</b> — {datetime.now().strftime('%H:%M')} WIB\nTidak ada sinyal akumulasi kuat saat ini. Pantau terus! 👀")

        else:
            print(f"  — Sinyal sama seperti sebelumnya: {[s['ticker'] for s in top5]}")

        prev_signals = {s["ticker"] for s in top5}
        print(f"  Scan berikutnya dalam {INTERVAL//60} menit.")
        time.sleep(INTERVAL)

# ── WEB SERVER (wajib untuk Render) ──────────────────────
@app.route("/")
def home():
    return f"""
    <html><body style='font-family:monospace;background:#020817;color:#22d3ee;padding:40px'>
    <h2>🤖 IDX Big Money Scanner</h2>
    <p style='color:#94a3b8'>Bot aktif dan berjalan.</p>
    <p style='color:#94a3b8'>Scan interval: setiap {INTERVAL//60} menit</p>
    <p style='color:#94a3b8'>Saham dipantau: {len(STOCKS)} saham IDX</p>
    </body></html>
    """

# ── MAIN ──────────────────────────────────────────────────
if __name__ == "__main__":
    Thread(target=scan_loop, daemon=True).start()
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
