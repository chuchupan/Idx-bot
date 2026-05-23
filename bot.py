import os
import time
import math
import requests
import json
from datetime import datetime, timezone, timedelta
from threading import Thread
from flask import Flask
from collections import defaultdict

app = Flask(__name__)

# ── TIMEZONE WIB ─────────────────────────────────────────
WIB = timezone(timedelta(hours=7))
def now_wib():
    return datetime.now(WIB)

# ── CONFIG ───────────────────────────────────────────────
BOT_TOKEN      = os.environ.get("BOT_TOKEN", "")
CHAT_ID        = os.environ.get("CHAT_ID", "")
STOCKBIT_EMAIL = os.environ.get("STOCKBIT_EMAIL", "")
STOCKBIT_PASS  = os.environ.get("STOCKBIT_PASS", "")

BANDAR_BROKERS = ["AK", "MG", "CC", "DX", "FS"]
RITEL_BROKERS  = ["XL", "XC", "YP", "PD", "OD"]

STOCKS = [
    "BBCA","BBRI","BMRI","TLKM","ASII","UNVR","ICBP","KLBF","HMSP","INDF",
    "PGAS","JSMR","SMGR","CPIN","PTBA","ADRO","ITMG","ANTM","INCO","MDKA",
    "BYAN","BRPT","MAPI","EMTK","DCII","BUKA","ACES","SIDO","MYOR","GOTO",
    "EXCL","ISAT","TBIG","TOWR","MTEL","BBNI","BNGA","BJBR","BDMN","BJTM",
    "PNBN","NISP","MEGA","BRIS","BTPS","BSDE","CTRA","PWON","SMRA","LPKR",
    "ASRI","DILD","GPRA","MDLN","MKPI","APLN","HRUM","KKGI","MYOH","APEX",
    "DEWA","DOID","ELSA","ENRG","ESSA","GEMS","INDY","MBAP","MEDC","PTRO",
    "TOBA","BIPI","AALI","LSIP","SIMP","SGRO","TBLA","BWPT","ULTJ","SKBM",
    "STTP","CEKA","DLTA","FAST","JPFA","MAIN","MLBI","ROTI","TSPC","GOOD",
    "HOKI","ADHI","PTPP","WIKA","WSKT","TOTL","WTON","AMFG","ARNA","GDST",
    "INAI","ISSP","KRAS","LION","LMSH","NIKL","ALMI","KAEF","MERK","PEHA",
    "DVLA","INAF","HEAL","MIKA","SAME","SILO","BMHS","ADMF","BFIN","MFIN",
    "WOMF","DMMX","DNET","MTDL","TECH","TELE","VKTR","WIIM","WINS","SMCB",
]
STOCKS = list(dict.fromkeys(STOCKS))

# ── STATE ────────────────────────────────────────────────
stockbit_token = None
broker_history = defaultdict(list)
prev_signals   = set()
morning_sent   = None
last_scan_hour = -1

# ── HELPER ───────────────────────────────────────────────
def fmt(n):
    if abs(n) >= 1e12: return f"{n/1e12:.1f}T"
    if abs(n) >= 1e9:  return f"{n/1e9:.1f}B"
    if abs(n) >= 1e6:  return f"{n/1e6:.1f}M"
    return f"{n:.0f}"

def send(msg):
    if not BOT_TOKEN or not CHAT_ID:
        print("⚠ BOT_TOKEN/CHAT_ID belum diset"); return
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json={"chat_id": CHAT_ID, "text": msg, "parse_mode": "HTML"},
            timeout=10
        )
        print(f"Telegram: {r.json().get('ok')}")
    except Exception as e:
        print(f"Telegram error: {e}")

# ── LOGIN STOCKBIT ────────────────────────────────────────
def login_stockbit():
    global stockbit_token
    if not STOCKBIT_EMAIL or not STOCKBIT_PASS:
        print("⚠ STOCKBIT_EMAIL/PASS belum diset")
        return False
    try:
        print(f"Login Stockbit ({STOCKBIT_EMAIL})...")
        r = requests.post(
            "https://api.stockbit.com/v2.4/login",
            json={"username": STOCKBIT_EMAIL, "password": STOCKBIT_PASS},
            headers={"Content-Type": "application/json"},
            timeout=15
        )
        data = r.json()
        token = data.get("data", {}).get("access_token")
        if token:
            stockbit_token = token
            print("✅ Login Stockbit berhasil")
            return True
        # Coba field alternatif
        token = data.get("access_token") or data.get("token")
        if token:
            stockbit_token = token
            print("✅ Login Stockbit berhasil (alt field)")
            return True
        print(f"❌ Login gagal: {json.dumps(data)[:200]}")
        send(f"⚠️ <b>Login Stockbit gagal</b>\nPesan: {data.get('message','unknown')}\nCek email/password di Render Environment.")
        return False
    except Exception as e:
        print(f"Login error: {e}")
        return False

# ── AMBIL BROKER SUMMARY ──────────────────────────────────
def get_broker_summary(ticker):
    global stockbit_token
    if not stockbit_token:
        if not login_stockbit(): return None
    try:
        headers = {"Authorization": f"Bearer {stockbit_token}"}
        # Coba endpoint broker summary
        r = requests.get(
            f"https://api.stockbit.com/v2.4/financials/broker_summary/{ticker}",
            headers=headers, timeout=10
        )
        if r.status_code == 401:
            if login_stockbit(): return get_broker_summary(ticker)
            return None
        if r.status_code != 200:
            return None
        data = r.json()
        # Coba beberapa struktur response
        result = (
            data.get("data") or
            data.get("result") or
            data.get("broker_summary") or
            []
        )
        return result if isinstance(result, list) else None
    except Exception as e:
        print(f"Broker summary error {ticker}: {e}")
        return None

# ── AMBIL HARGA REAL ──────────────────────────────────────
def get_stock_price(ticker):
    global stockbit_token
    if not stockbit_token:
        if not login_stockbit(): return None
    try:
        headers = {"Authorization": f"Bearer {stockbit_token}"}
        r = requests.get(
            f"https://api.stockbit.com/v2.4/stream/{ticker}",
            headers=headers, timeout=10
        )
        if r.status_code == 401:
            if login_stockbit(): return get_stock_price(ticker)
            return None
        if r.status_code != 200:
            return None
        data = r.json()
        d = data.get("data", {})
        if not d:
            d = data  # fallback struktur flat
        price  = d.get("last_price") or d.get("close") or d.get("price") or 0
        change = d.get("change_percent") or d.get("percent_change") or 0
        volume = d.get("volume") or d.get("vol") or 0
        if price == 0:
            return None
        return {"price": price, "change": change, "volume": volume}
    except Exception as e:
        print(f"Price error {ticker}: {e}")
        return None

# ── ANALISA BROKER DATA ───────────────────────────────────
def analyze_broker_data(ticker, summary_data, price_data):
    if not summary_data or not isinstance(summary_data, list) or len(summary_data) == 0:
        return None

    bandar_net = 0
    ritel_net  = 0

    for row in summary_data:
        broker = str(row.get("broker_id") or row.get("broker") or "").upper().strip()
        # Coba berbagai field net value
        net = float(
            row.get("net_value") or
            row.get("net") or
            row.get("buy_value", 0) - row.get("sell_value", 0)
        )
        if broker in BANDAR_BROKERS:
            bandar_net += net
        if broker in RITEL_BROKERS:
            ritel_net += net

    # Simpan histori harian
    today   = now_wib().strftime("%Y-%m-%d")
    history = broker_history[ticker]
    if not history or history[-1]["date"] != today:
        history.append({"date": today, "bandar_net": bandar_net, "ritel_net": ritel_net})
        broker_history[ticker] = history[-10:]

    days  = broker_history[ticker]
    n     = len(days)

    # Hitung berapa hari berturut-turut bandar akum
    bandar_accum_days = 0
    for d in reversed(days):
        if d["bandar_net"] > 0:
            bandar_accum_days += 1
        else:
            break

    recent        = days[-min(5, n):]
    bandar_net_5d = sum(d["bandar_net"] for d in recent)
    ritel_net_5d  = sum(d["ritel_net"]  for d in recent)

    bandar_akum  = bandar_net_5d > 0 and bandar_accum_days >= 2
    ritel_jual   = ritel_net_5d < 0
    sinyal_kuat  = bandar_akum and ritel_jual and bandar_accum_days >= 3

    # Skor
    skor = round(min(
        min(bandar_accum_days/5, 1) * 40 +
        (min(bandar_net_5d/5e11, 1) * 30 if bandar_net_5d > 0 else 0) +
        (20 if ritel_jual else 0) +
        (10 if price_data and abs(float(price_data.get("change", 0))) < 2 else 5),
        100
    ))

    return {
        "ticker":            ticker,
        "price":             float(price_data.get("price", 0)) if price_data else 0,
        "change":            float(price_data.get("change", 0)) if price_data else 0,
        "volume":            float(price_data.get("volume", 0)) if price_data else 0,
        "bandar_net_5d":     bandar_net_5d,
        "ritel_net_5d":      ritel_net_5d,
        "bandar_accum_days": bandar_accum_days,
        "bandar_akum":       bandar_akum,
        "ritel_jual":        ritel_jual,
        "sinyal_kuat":       sinyal_kuat,
        "skor":              skor,
    }

# ── PESAN ────────────────────────────────────────────────
def build_signal_msg(signals, total):
    medal = ["🥇","🥈","🥉","4️⃣","5️⃣"]
    lines = [
        "🚨 <b>SINYAL AKUMULASI BIG MONEY!</b>",
        f"📅 {now_wib().strftime('%d/%m/%Y %H:%M')} WIB",
        f"🔍 Dari {total} saham IHSG\n",
    ]
    for i, s in enumerate(signals[:5]):
        arrow  = "📈" if s["change"] >= 0 else "📉"
        kuat   = " 🔥 <b>KUAT!</b>" if s["sinyal_kuat"] else ""
        lines.append(
            f"{medal[i]} <b>{s['ticker']}</b> — Score: <b>{s['skor']}/100</b>{kuat}\n"
            f"   💰 Rp{s['price']:,.0f}  {arrow} {s['change']:+.2f}%\n"
            f"   🏦 Bandar (AK/MG): {'✅ AKUM' if s['bandar_akum'] else '➖'} "
            f"{s['bandar_accum_days']} hari | Net: {fmt(s['bandar_net_5d'])}\n"
            f"   👥 Ritel (XL/YP/PD): {'⚠️ JUAL' if s['ritel_jual'] else '➖ Netral'} "
            f"| Net: {fmt(s['ritel_net_5d'])}\n"
            f"   📊 Volume: {fmt(s['volume'])}\n"
        )
    lines.append("⚠ Bukan rekomendasi beli/jual. DYOR!")
    return "\n".join(lines)

def build_morning_msg(signals):
    kuat  = [s for s in signals if s["sinyal_kuat"]][:3]
    biasa = [s for s in signals if s["bandar_akum"] and not s["sinyal_kuat"]][:3]
    lines = [
        "🌅 <b>REKOMENDASI PAGI — BROKER FLOW</b>",
        f"📅 {now_wib().strftime('%d/%m/%Y')} | Market buka 09:00 WIB\n",
    ]
    if kuat:
        lines.append("🔥 <b>SINYAL KUAT (Bandar akum + Ritel jual):</b>")
        for s in kuat:
            lines.append(
                f"   ⭐ <b>{s['ticker']}</b> Rp{s['price']:,.0f} ({s['change']:+.2f}%)\n"
                f"      🏦 Akum {s['bandar_accum_days']} hari: {fmt(s['bandar_net_5d'])}\n"
                f"      👥 Ritel net jual: {fmt(s['ritel_net_5d'])}\n"
                f"      Score: {s['skor']}/100\n"
            )
    if biasa:
        lines.append("📈 <b>BANDAR AKUMULASI (pantau):</b>")
        for s in biasa:
            lines.append(
                f"   • <b>{s['ticker']}</b> Rp{s['price']:,.0f} "
                f"— akum {s['bandar_accum_days']} hari | Score: {s['skor']}/100\n"
            )
    if not kuat and not biasa:
        lines.append("Belum ada sinyal kuat hari ini. Pantau terus! 👀")
    lines.append("\n⚠ Bukan rekomendasi beli/jual. DYOR!")
    return "\n".join(lines)

# ── SCAN ─────────────────────────────────────────────────
def run_scan():
    global prev_signals
    total   = len(STOCKS)
    results = []
    print(f"[{now_wib().strftime('%H:%M')} WIB] Scanning {total} saham...")

    for i, ticker in enumerate(STOCKS):
        try:
            summary = get_broker_summary(ticker)
            price   = get_stock_price(ticker)
            result  = analyze_broker_data(ticker, summary, price)
            if result:
                results.append(result)
            time.sleep(0.3)
        except Exception as e:
            print(f"  Error {ticker}: {e}")
        if (i+1) % 25 == 0:
            print(f"  {i+1}/{total} selesai...")

    results.sort(key=lambda x: x["skor"], reverse=True)
    signals  = [r for r in results if r["bandar_akum"]]
    new_sigs = [s for s in signals if s["ticker"] not in prev_signals]

    print(f"  ✅ Sinyal: {len(signals)} | Baru: {len(new_sigs)}")
    if new_sigs:
        send(build_signal_msg(signals, total))
    prev_signals = {s["ticker"] for s in signals}
    return results

# ── MAIN LOOP ────────────────────────────────────────────
def main_loop():
    global morning_sent, last_scan_hour

    print(f"🤖 IDX Broker Flow Scanner — {len(STOCKS)} saham IHSG")
    send(
        f"🤖 <b>IDX Broker Flow Scanner aktif!</b>\n\n"
        f"📊 <b>{len(STOCKS)} saham IHSG</b>\n"
        f"🏦 Bandar: AK, MG, CC, DX, FS\n"
        f"👥 Ritel: XL, XC, YP, PD, OD\n\n"
        f"🔔 Sinyal: Bandar akum berhari-hari + Ritel mulai jual\n"
        f"🌅 Rekomendasi pagi: 08:45 WIB (Senin–Jumat)\n"
        f"🕘 Scan: tiap jam saat market buka\n"
        f"⏰ Waktu server: {now_wib().strftime('%d/%m/%Y %H:%M')} WIB ✅"
    )

    login_stockbit()

    while True:
        n        = now_wib()
        is_wdays = n.weekday() < 5
        today    = n.strftime("%Y-%m-%d")

        # Rekomendasi pagi 08:45
        if is_wdays and n.hour == 8 and 44 <= n.minute <= 46 and morning_sent != today:
            results = run_scan()
            send(build_morning_msg(results))
            morning_sent = today

        # Scan tiap jam market buka 09:00–16:00
        elif is_wdays and 9 <= n.hour <= 16 and n.hour != last_scan_hour:
            run_scan()
            last_scan_hour = n.hour

        # Scan penutupan 16:30
        elif is_wdays and n.hour == 16 and n.minute >= 30 and last_scan_hour != 99:
            run_scan()
            last_scan_hour = 99

        time.sleep(60)

# ── WEB SERVER ───────────────────────────────────────────
@app.route("/")
def home():
    return (
        f"<html><body style='font-family:monospace;background:#020817;color:#22d3ee;padding:40px'>"
        f"<h2>🤖 IDX Broker Flow Scanner</h2>"
        f"<p style='color:#94a3b8'>Status: Aktif ✅</p>"
        f"<p style='color:#94a3b8'>Saham: {len(STOCKS)} saham IHSG</p>"
        f"<p style='color:#94a3b8'>Bandar: AK, MG, CC, DX, FS</p>"
        f"<p style='color:#94a3b8'>Ritel: XL, XC, YP, PD, OD</p>"
        f"<p style='color:#94a3b8'>Waktu WIB: {now_wib().strftime('%d/%m/%Y %H:%M:%S')} WIB</p>"
        f"</body></html>"
    )

if __name__ == "__main__":
    Thread(target=main_loop, daemon=True).start()
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
        
