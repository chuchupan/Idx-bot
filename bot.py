import os
import time
import requests
import json
from datetime import datetime, timezone, timedelta
from threading import Thread
from collections import defaultdict
from flask import Flask

app = Flask(__name__)

# ── TIMEZONE WIB ─────────────────────────────────────────
WIB = timezone(timedelta(hours=7))
def now_wib():
    return datetime.now(WIB)

# ── CONFIG ───────────────────────────────────────────────
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
CHAT_ID   = os.environ.get("CHAT_ID", "")

BANDAR_BROKERS = {"AK", "MG", "CC", "DX", "FS", "BK", "ZP"}
RITEL_BROKERS  = {"XL", "XC", "YP", "PD", "OD", "KZ", "FZ"}

STOCKS = [
    "BBCA","BBRI","BMRI","TLKM","ASII","UNVR","ICBP","KLBF","HMSP","INDF",
    "PGAS","JSMR","SMGR","CPIN","PTBA","ADRO","ITMG","ANTM","INCO","MDKA",
    "BYAN","BRPT","MAPI","EMTK","DCII","BUKA","ACES","SIDO","MYOR","GOTO",
    "EXCL","ISAT","TBIG","TOWR","MTEL","BBNI","BNGA","BJBR","BDMN","BJTM",
    "PNBN","NISP","MEGA","BRIS","BTPS","BSDE","CTRA","PWON","SMRA","LPKR",
    "ASRI","DILD","GPRA","MDLN","MKPI","HRUM","KKGI","MYOH","DEWA","DOID",
    "ELSA","ESSA","GEMS","INDY","MBAP","MEDC","PTRO","TOBA","BIPI","AALI",
    "LSIP","SIMP","SGRO","TBLA","ULTJ","SKBM","STTP","DLTA","FAST","JPFA",
    "MLBI","ROTI","TSPC","GOOD","HOKI","ADHI","PTPP","WIKA","WSKT","TOTL",
    "WTON","AMFG","ARNA","KRAS","LION","NIKL","KAEF","MERK","DVLA","HEAL",
    "MIKA","SILO","BMHS","ADMF","BFIN","MFIN","DMMX","DNET","MTDL","WIIM",
]
STOCKS = list(dict.fromkeys(STOCKS))

# ── STATE ────────────────────────────────────────────────
broker_history = defaultdict(list)  # ticker -> [{date, bandar_net, ritel_net}]
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

# ── AMBIL HARGA REAL DARI YAHOO FINANCE ──────────────────
def get_price_yahoo(ticker):
    try:
        symbol = f"{ticker}.JK"
        url    = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?interval=1d&range=2d"
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
        if r.status_code != 200:
            return None
        data   = r.json()
        result = data.get("chart", {}).get("result", [])
        if not result:
            return None
        meta   = result[0].get("meta", {})
        price  = meta.get("regularMarketPrice", 0)
        prev   = meta.get("chartPreviousClose", 0)
        volume = meta.get("regularMarketVolume", 0)
        change = ((price - prev) / prev * 100) if prev else 0
        if price == 0:
            return None
        return {"price": price, "change": round(change, 2), "volume": volume, "prev_close": prev}
    except Exception as e:
        print(f"Yahoo error {ticker}: {e}")
        return None

# ── AMBIL BROKER SUMMARY DARI IDX ────────────────────────
def get_broker_summary_idx(ticker):
    """
    Ambil data broker summary dari IDX resmi.
    Data delay 1 hari tapi gratis dan tanpa login.
    """
    try:
        # IDX API endpoint broker summary
        url = f"https://idx.co.id/api/broker-summary?code={ticker}&start=0&length=99"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer":    "https://idx.co.id/",
            "Accept":     "application/json",
        }
        r = requests.get(url, headers=headers, timeout=10)
        if r.status_code != 200:
            return None
        data = r.json()
        rows = data.get("data") or data.get("Data") or data.get("result") or []
        if not rows:
            return None
        return rows
    except Exception as e:
        print(f"IDX broker error {ticker}: {e}")
        return None

# ── HITUNG BROKER NET ─────────────────────────────────────
def parse_broker_net(rows):
    bandar_net = 0.0
    ritel_net  = 0.0
    bandar_detail = {}
    ritel_detail  = {}

    for row in rows:
        # Coba berbagai key nama broker
        broker = str(
            row.get("broker_id") or row.get("BrokerID") or
            row.get("broker") or row.get("Broker") or ""
        ).upper().strip()

        # Net value: buy - sell dalam rupiah
        buy  = float(row.get("buy_value")  or row.get("BuyValue")  or row.get("buy")  or 0)
        sell = float(row.get("sell_value") or row.get("SellValue") or row.get("sell") or 0)
        net  = float(row.get("net_value")  or row.get("NetValue")  or row.get("net")  or (buy - sell))

        if broker in BANDAR_BROKERS:
            bandar_net += net
            bandar_detail[broker] = net
        if broker in RITEL_BROKERS:
            ritel_net += net
            ritel_detail[broker] = net

    return bandar_net, ritel_net, bandar_detail, ritel_detail

# ── ANALISA PER SAHAM ────────────────────────────────────
def analyze(ticker):
    price_data  = get_price_yahoo(ticker)
    broker_rows = get_broker_summary_idx(ticker)

    if not price_data:
        return None

    # Parse broker data
    if broker_rows:
        bandar_net, ritel_net, bandar_detail, ritel_detail = parse_broker_net(broker_rows)
    else:
        # Tidak ada data broker — skip saham ini
        return None

    # Simpan histori
    today   = now_wib().strftime("%Y-%m-%d")
    history = broker_history[ticker]
    if not history or history[-1]["date"] != today:
        history.append({
            "date":       today,
            "bandar_net": bandar_net,
            "ritel_net":  ritel_net,
        })
        broker_history[ticker] = history[-10:]

    days  = broker_history[ticker]
    n     = len(days)

    # Berapa hari berturut-turut bandar net buy
    bandar_accum_days = 0
    for d in reversed(days):
        if d["bandar_net"] > 0:
            bandar_accum_days += 1
        else:
            break

    recent        = days[-min(5, n):]
    bandar_net_5d = sum(d["bandar_net"] for d in recent)
    ritel_net_5d  = sum(d["ritel_net"]  for d in recent)

    # Volume relatif (butuh rata-rata — estimasi dari data hari ini vs prev)
    volume     = price_data.get("volume", 0)
    prev_close = price_data.get("prev_close", 0)
    change     = price_data.get("change", 0)
    price      = price_data.get("price", 0)

    # Kondisi sinyal
    bandar_akum = bandar_net_5d > 0 and bandar_accum_days >= 2
    ritel_jual  = ritel_net_5d < 0
    sinyal_kuat = bandar_akum and ritel_jual and bandar_accum_days >= 3

    # Skor 0-100
    skor_akum   = min(bandar_accum_days / 5, 1) * 40
    skor_net    = min(abs(bandar_net_5d) / 5e11, 1) * 30 if bandar_net_5d > 0 else 0
    skor_ritel  = 20 if ritel_jual else 0
    skor_harga  = 10 if abs(change) < 2 else 5
    skor        = round(min(skor_akum + skor_net + skor_ritel + skor_harga, 100))

    # Top broker bandar yang beli
    top_bandar = sorted(
        [(k, v) for k, v in bandar_detail.items() if v > 0],
        key=lambda x: x[1], reverse=True
    )[:3]

    return {
        "ticker":            ticker,
        "price":             price,
        "change":            change,
        "volume":            volume,
        "bandar_net_5d":     bandar_net_5d,
        "ritel_net_5d":      ritel_net_5d,
        "bandar_accum_days": bandar_accum_days,
        "bandar_akum":       bandar_akum,
        "ritel_jual":        ritel_jual,
        "sinyal_kuat":       sinyal_kuat,
        "top_bandar":        top_bandar,
        "skor":              skor,
    }

# ── FORMAT PESAN ──────────────────────────────────────────
def build_signal_msg(signals, total_scanned):
    medal = ["🥇","🥈","🥉","4️⃣","5️⃣"]
    lines = [
        "🚨 <b>SINYAL AKUMULASI BIG MONEY!</b>",
        f"📅 {now_wib().strftime('%d/%m/%Y %H:%M')} WIB",
        f"🔍 Dari {total_scanned} saham IHSG\n",
    ]
    for i, s in enumerate(signals[:5]):
        arrow = "📈" if s["change"] >= 0 else "📉"
        kuat  = " 🔥 <b>SINYAL KUAT!</b>" if s["sinyal_kuat"] else ""
        top_b = ", ".join([f"{b}({fmt(v)})" for b, v in s["top_bandar"]]) or "-"
        lines.append(
            f"{medal[i]} <b>{s['ticker']}</b> — Score: <b>{s['skor']}/100</b>{kuat}\n"
            f"   💰 Rp{s['price']:,.0f}  {arrow} {s['change']:+.2f}%\n"
            f"   🏦 Bandar akum <b>{s['bandar_accum_days']} hari</b> | Net 5d: {fmt(s['bandar_net_5d'])}\n"
            f"   🏦 Broker beli: {top_b}\n"
            f"   👥 Ritel net: {'⚠️ JUAL ' if s['ritel_jual'] else '➖ '}{fmt(s['ritel_net_5d'])}\n"
            f"   📊 Volume: {fmt(s['volume'])}\n"
        )
    lines.append("⚠ Data delay 1 hari (IDX resmi). Bukan rekomendasi beli/jual. DYOR!")
    return "\n".join(lines)

def build_morning_msg(signals):
    kuat  = [s for s in signals if s["sinyal_kuat"]][:3]
    biasa = [s for s in signals if s["bandar_akum"] and not s["sinyal_kuat"]][:3]
    lines = [
        "🌅 <b>REKOMENDASI PAGI — BROKER FLOW</b>",
        f"📅 {now_wib().strftime('%d/%m/%Y')} WIB | Market buka 09:00\n",
    ]
    if kuat:
        lines.append("🔥 <b>SINYAL KUAT — Bandar akum + Ritel jual:</b>")
        for s in kuat:
            top_b = ", ".join([f"{b}({fmt(v)})" for b, v in s["top_bandar"]]) or "-"
            lines.append(
                f"   ⭐ <b>{s['ticker']}</b> Rp{s['price']:,.0f} ({s['change']:+.2f}%)\n"
                f"      🏦 Akum {s['bandar_accum_days']} hari | Net: {fmt(s['bandar_net_5d'])}\n"
                f"      🏦 Broker: {top_b}\n"
                f"      👥 Ritel jual: {fmt(s['ritel_net_5d'])}\n"
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
    lines.append("\n⚠ Data delay 1 hari. Bukan rekomendasi beli/jual. DYOR!")
    return "\n".join(lines)

# ── SCAN ─────────────────────────────────────────────────
def run_scan():
    global prev_signals
    total   = len(STOCKS)
    results = []
    ok_count = 0
    print(f"[{now_wib().strftime('%H:%M')} WIB] Scanning {total} saham...")

    for i, ticker in enumerate(STOCKS):
        try:
            result = analyze(ticker)
            if result:
                results.append(result)
                ok_count += 1
            time.sleep(0.5)  # hindari rate limit IDX
        except Exception as e:
            print(f"  Error {ticker}: {e}")
        if (i+1) % 20 == 0:
            print(f"  {i+1}/{total} ({ok_count} berhasil)...")

    results.sort(key=lambda x: x["skor"], reverse=True)
    signals  = [r for r in results if r["bandar_akum"]]
    new_sigs = [s for s in signals if s["ticker"] not in prev_signals]

    print(f"  ✅ Data: {ok_count}/{total} | Sinyal: {len(signals)} | Baru: {len(new_sigs)}")

    if new_sigs:
        send(build_signal_msg(signals, ok_count))
    elif not signals:
        send(
            f"🔍 <b>Scan selesai</b> — {now_wib().strftime('%H:%M')} WIB\n"
            f"Dari {ok_count} saham, belum ada akumulasi bandar yang signifikan. 👀"
        )

    prev_signals = {s["ticker"] for s in signals}
    return results

# ── MAIN LOOP ─────────────────────────────────────────────
def main_loop():
    global morning_sent, last_scan_hour

    print(f"🤖 IDX Broker Flow Scanner — {len(STOCKS)} saham")
    send(
        f"🤖 <b>IDX Broker Flow Scanner aktif!</b>\n\n"
        f"📊 <b>{len(STOCKS)} saham IHSG</b>\n"
        f"📡 Sumber: Yahoo Finance (harga) + IDX resmi (broker flow)\n"
        f"🏦 Bandar: AK, MG, CC, DX, FS, BK, ZP\n"
        f"👥 Ritel: XL, XC, YP, PD, OD, KZ, FZ\n"
        f"⚠️ Data broker delay 1 hari\n\n"
        f"🌅 Rekomendasi pagi: 08:45 WIB (Senin–Jumat)\n"
        f"🕘 Scan: tiap jam saat market buka\n"
        f"⏰ {now_wib().strftime('%d/%m/%Y %H:%M')} WIB ✅"
    )

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
        f"<p style='color:#94a3b8'>Sumber: Yahoo Finance + IDX resmi</p>"
        f"<p style='color:#94a3b8'>Bandar: AK, MG, CC, DX, FS, BK, ZP</p>"
        f"<p style='color:#94a3b8'>Ritel: XL, XC, YP, PD, OD, KZ, FZ</p>"
        f"<p style='color:#94a3b8'>Waktu: {now_wib().strftime('%d/%m/%Y %H:%M:%S')} WIB</p>"
        f"</body></html>"
    )

if __name__ == "__main__":
    Thread(target=main_loop, daemon=True).start()
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
        
