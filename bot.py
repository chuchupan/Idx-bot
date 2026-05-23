import os
import time
import requests
import json
from datetime import datetime, timezone, timedelta
from threading import Thread
from collections import defaultdict
from flask import Flask, request as flask_request

app = Flask(__name__)

WIB = timezone(timedelta(hours=7))
def now_wib():
    return datetime.now(WIB)

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

broker_history = defaultdict(list)
prev_signals   = set()
morning_sent   = None
last_scan_hour = -1
last_results   = []  # cache hasil scan terakhir

# ── HELPER ───────────────────────────────────────────────
def fmt(n):
    if abs(n) >= 1e12: return f"{n/1e12:.1f}T"
    if abs(n) >= 1e9:  return f"{n/1e9:.1f}B"
    if abs(n) >= 1e6:  return f"{n/1e6:.1f}M"
    return f"{n:.0f}"

def send(msg, chat_id=None):
    cid = chat_id or CHAT_ID
    if not BOT_TOKEN or not cid:
        print("⚠ TOKEN/CHAT_ID belum diset"); return
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json={"chat_id": cid, "text": msg, "parse_mode": "HTML"},
            timeout=10
        )
        print(f"Telegram: {r.json().get('ok')}")
    except Exception as e:
        print(f"Telegram error: {e}")

def set_webhook():
    try:
        url = f"https://idx-bot.onrender.com/webhook"
        r = requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/setWebhook",
            json={"url": url}, timeout=10
        )
        print(f"Webhook: {r.json()}")
    except Exception as e:
        print(f"Webhook error: {e}")

# ── DATA ─────────────────────────────────────────────────
def get_price_yahoo(ticker):
    try:
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}.JK?interval=1d&range=5d"
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
        if r.status_code != 200: return None
        data   = r.json()
        result = data.get("chart", {}).get("result", [])
        if not result: return None
        meta   = result[0].get("meta", {})
        price  = meta.get("regularMarketPrice", 0)
        prev   = meta.get("chartPreviousClose", 0)
        volume = meta.get("regularMarketVolume", 0)
        change = ((price - prev) / prev * 100) if prev else 0

        # Ambil data OHLCV 5 hari untuk analisa volatilitas
        indicators = result[0].get("indicators", {})
        quote = indicators.get("quote", [{}])[0]
        highs  = [x for x in (quote.get("high") or []) if x]
        lows   = [x for x in (quote.get("low")  or []) if x]
        closes = [x for x in (quote.get("close") or []) if x]
        vols   = [x for x in (quote.get("volume") or []) if x]

        avg_vol = sum(vols[:-1]) / len(vols[:-1]) if len(vols) > 1 else volume
        vol_ratio = volume / avg_vol if avg_vol else 1

        # Range harian rata-rata (untuk scalping)
        daily_ranges = [(h-l)/l*100 for h, l in zip(highs, lows) if l > 0]
        avg_range = sum(daily_ranges) / len(daily_ranges) if daily_ranges else 0

        if price == 0: return None
        return {
            "price": price, "change": round(change, 2),
            "volume": volume, "prev_close": prev,
            "vol_ratio": round(vol_ratio, 2),
            "avg_range": round(avg_range, 2),
            "closes": closes,
        }
    except Exception as e:
        print(f"Yahoo error {ticker}: {e}")
        return None

def get_broker_summary_idx(ticker):
    try:
        url = f"https://idx.co.id/api/broker-summary?code={ticker}&start=0&length=99"
        r = requests.get(url, headers={
            "User-Agent": "Mozilla/5.0",
            "Referer": "https://idx.co.id/",
            "Accept": "application/json",
        }, timeout=10)
        if r.status_code != 200: return None
        data = r.json()
        rows = data.get("data") or data.get("Data") or data.get("result") or []
        return rows if rows else None
    except Exception as e:
        print(f"IDX error {ticker}: {e}")
        return None

def parse_broker_net(rows):
    bandar_net, ritel_net = 0.0, 0.0
    bandar_detail, ritel_detail = {}, {}
    for row in rows:
        broker = str(row.get("broker_id") or row.get("BrokerID") or row.get("broker") or "").upper().strip()
        buy  = float(row.get("buy_value")  or row.get("BuyValue")  or 0)
        sell = float(row.get("sell_value") or row.get("SellValue") or 0)
        net  = float(row.get("net_value")  or row.get("NetValue")  or (buy - sell))
        if broker in BANDAR_BROKERS:
            bandar_net += net
            bandar_detail[broker] = net
        if broker in RITEL_BROKERS:
            ritel_net += net
            ritel_detail[broker] = net
    return bandar_net, ritel_net, bandar_detail, ritel_detail

def analyze(ticker):
    price_data  = get_price_yahoo(ticker)
    broker_rows = get_broker_summary_idx(ticker)
    if not price_data: return None

    if broker_rows:
        bandar_net, ritel_net, bandar_detail, ritel_detail = parse_broker_net(broker_rows)
    else:
        return None

    today   = now_wib().strftime("%Y-%m-%d")
    history = broker_history[ticker]
    if not history or history[-1]["date"] != today:
        history.append({"date": today, "bandar_net": bandar_net, "ritel_net": ritel_net})
        broker_history[ticker] = history[-10:]

    days  = broker_history[ticker]
    bandar_accum_days = 0
    for d in reversed(days):
        if d["bandar_net"] > 0: bandar_accum_days += 1
        else: break

    recent        = days[-min(5, len(days)):]
    bandar_net_5d = sum(d["bandar_net"] for d in recent)
    ritel_net_5d  = sum(d["ritel_net"]  for d in recent)

    bandar_akum = bandar_net_5d > 0 and bandar_accum_days >= 2
    ritel_jual  = ritel_net_5d < 0
    sinyal_kuat = bandar_akum and ritel_jual and bandar_accum_days >= 3

    price     = price_data["price"]
    change    = price_data["change"]
    vol_ratio = price_data["vol_ratio"]
    avg_range = price_data["avg_range"]  # % range harian rata-rata

    # ── SKOR SWING (akumulasi bandar) ──
    swing_score = round(min(
        min(bandar_accum_days/5,1)*40 +
        (min(bandar_net_5d/5e11,1)*30 if bandar_net_5d>0 else 0) +
        (20 if ritel_jual else 0) +
        (10 if abs(change)<2 else 5),
        100
    ))

    # ── SKOR SCALPING (beli pagi jual sore) ──
    # Butuh: volume tinggi + range harian besar + harga > 200 + likuid
    scalp_score = round(min(
        min(vol_ratio/4,1)*35 +
        min(avg_range/3,1)*30 +
        (20 if price >= 500 else 10 if price >= 200 else 3) +
        (15 if vol_ratio >= 2 else 5),
        100
    ))

    top_bandar = sorted(
        [(k,v) for k,v in bandar_detail.items() if v>0],
        key=lambda x: x[1], reverse=True
    )[:3]

    return {
        "ticker":            ticker,
        "price":             price,
        "change":            change,
        "volume":            price_data["volume"],
        "vol_ratio":         vol_ratio,
        "avg_range":         avg_range,
        "bandar_net_5d":     bandar_net_5d,
        "ritel_net_5d":      ritel_net_5d,
        "bandar_accum_days": bandar_accum_days,
        "bandar_akum":       bandar_akum,
        "ritel_jual":        ritel_jual,
        "sinyal_kuat":       sinyal_kuat,
        "top_bandar":        top_bandar,
        "swing_score":       swing_score,
        "scalp_score":       scalp_score,
    }

# ── FORMAT PESAN ──────────────────────────────────────────
def build_pagi_msg(results):
    """Saham enak beli pagi jual sore — scalping harian"""
    picks = sorted(results, key=lambda x: x["scalp_score"], reverse=True)
    picks = [p for p in picks if p["scalp_score"] >= 50][:5]
    medal = ["🥇","🥈","🥉","4️⃣","5️⃣"]
    lines = [
        "⚡ <b>SAHAM BELI PAGI JUAL SORE</b>",
        f"📅 {now_wib().strftime('%d/%m/%Y %H:%M')} WIB\n",
        "Kriteria: Volume tinggi + Range harian besar + Likuid\n",
    ]
    if not picks:
        lines.append("Belum ada saham yang memenuhi kriteria scalping hari ini.")
        return "\n".join(lines)
    for i, s in enumerate(picks):
        arrow = "📈" if s["change"] >= 0 else "📉"
        target = round(s["price"] * (1 + s["avg_range"]*0.4/100), 0)
        stoploss = round(s["price"] * (1 - s["avg_range"]*0.3/100), 0)
        lines.append(
            f"{medal[i]} <b>{s['ticker']}</b> — Scalp Score: <b>{s['scalp_score']}/100</b>\n"
            f"   💰 Harga: Rp{s['price']:,.0f}  {arrow} {s['change']:+.2f}%\n"
            f"   📊 Volume: {s['vol_ratio']:.1f}x | Range harian: {s['avg_range']:.1f}%\n"
            f"   🎯 Target jual: Rp{target:,.0f}\n"
            f"   🛑 Stop loss: Rp{stoploss:,.0f}\n"
        )
    lines.append("⚠ Estimasi target dari rata-rata range harian. Bukan rekomendasi. DYOR!")
    return "\n".join(lines)

def build_akum_msg(results):
    """Saham akumulasi bandar — swing pendek"""
    picks = [r for r in results if r["bandar_akum"]]
    picks = sorted(picks, key=lambda x: x["swing_score"], reverse=True)[:5]
    medal = ["🥇","🥈","🥉","4️⃣","5️⃣"]
    lines = [
        "📈 <b>SAHAM AKUMULASI BANDAR</b>",
        f"📅 {now_wib().strftime('%d/%m/%Y %H:%M')} WIB\n",
        "Kriteria: Bandar (AK/MG) net buy berhari-hari + Ritel mulai jual\n",
    ]
    if not picks:
        lines.append("Belum ada sinyal akumulasi bandar yang kuat saat ini.")
        return "\n".join(lines)
    for i, s in enumerate(picks):
        arrow = "📈" if s["change"] >= 0 else "📉"
        kuat  = " 🔥" if s["sinyal_kuat"] else ""
        top_b = ", ".join([f"{b}({fmt(v)})" for b, v in s["top_bandar"]]) or "-"
        lines.append(
            f"{medal[i]} <b>{s['ticker']}</b> — Swing Score: <b>{s['swing_score']}/100</b>{kuat}\n"
            f"   💰 Rp{s['price']:,.0f}  {arrow} {s['change']:+.2f}%\n"
            f"   🏦 Bandar akum <b>{s['bandar_accum_days']} hari</b>\n"
            f"   🏦 Broker beli: {top_b}\n"
            f"   👥 Ritel: {'⚠️ Jual' if s['ritel_jual'] else '➖ Netral'} ({fmt(s['ritel_net_5d'])})\n"
        )
    lines.append("⚠ Data broker delay 1 hari. Bukan rekomendasi. DYOR!")
    return "\n".join(lines)

def build_signal_msg(signals, total):
    medal = ["🥇","🥈","🥉","4️⃣","5️⃣"]
    lines = [
        "🚨 <b>SINYAL AKUMULASI BIG MONEY!</b>",
        f"📅 {now_wib().strftime('%d/%m/%Y %H:%M')} WIB",
        f"🔍 Dari {total} saham IHSG\n",
    ]
    for i, s in enumerate(signals[:5]):
        arrow = "📈" if s["change"] >= 0 else "📉"
        kuat  = " 🔥 <b>KUAT!</b>" if s["sinyal_kuat"] else ""
        top_b = ", ".join([f"{b}({fmt(v)})" for b, v in s["top_bandar"]]) or "-"
        lines.append(
            f"{medal[i]} <b>{s['ticker']}</b> — Score: <b>{s['swing_score']}/100</b>{kuat}\n"
            f"   💰 Rp{s['price']:,.0f}  {arrow} {s['change']:+.2f}%\n"
            f"   🏦 Bandar akum {s['bandar_accum_days']} hari | Net: {fmt(s['bandar_net_5d'])}\n"
            f"   🏦 Broker: {top_b}\n"
            f"   👥 Ritel: {'⚠️ Jual' if s['ritel_jual'] else '➖'} {fmt(s['ritel_net_5d'])}\n"
        )
    lines.append("⚠ Data delay 1 hari. Bukan rekomendasi. DYOR!")
    return "\n".join(lines)

def build_morning_msg(results):
    kuat  = [s for s in results if s["sinyal_kuat"]][:3]
    biasa = [s for s in results if s["bandar_akum"] and not s["sinyal_kuat"]][:3]
    scalp = sorted(results, key=lambda x: x["scalp_score"], reverse=True)[:3]
    lines = [
        "🌅 <b>REKOMENDASI PAGI</b>",
        f"📅 {now_wib().strftime('%d/%m/%Y')} WIB | Market buka 09:00\n",
    ]
    if kuat:
        lines.append("🔥 <b>SINYAL KUAT — Bandar akum + Ritel jual:</b>")
        for s in kuat:
            lines.append(f"   ⭐ <b>{s['ticker']}</b> Rp{s['price']:,.0f} | Akum {s['bandar_accum_days']} hari | Score: {s['swing_score']}/100\n")
    if scalp:
        lines.append("⚡ <b>KANDIDAT SCALPING HARI INI:</b>")
        for s in scalp:
            lines.append(f"   • <b>{s['ticker']}</b> Rp{s['price']:,.0f} | Range: {s['avg_range']:.1f}% | Vol: {s['vol_ratio']:.1f}x\n")
    lines.append("Ketik /pagi untuk detail scalping | /akum untuk detail swing")
    lines.append("\n⚠ Bukan rekomendasi. DYOR!")
    return "\n".join(lines)

def build_help_msg():
    return (
        "🤖 <b>IDX Bot — Daftar Perintah:</b>\n\n"
        "⚡ /pagi — Saham enak beli pagi jual sore (scalping harian)\n"
        "📈 /akum — Saham akumulasi bandar AK/MG (swing pendek)\n"
        "🔄 /scan — Paksa scan sekarang\n"
        "📊 /status — Cek status bot\n"
        "❓ /help — Tampilkan perintah ini\n\n"
        "Bot scan otomatis tiap jam saat market buka (09:00–16:00 WIB).\n"
        "Rekomendasi pagi dikirim jam 08:45 WIB setiap Senin–Jumat."
    )

# ── SCAN ─────────────────────────────────────────────────
def run_scan():
    global prev_signals, last_results
    total   = len(STOCKS)
    results = []
    ok = 0
    print(f"[{now_wib().strftime('%H:%M')} WIB] Scanning {total} saham...")
    for i, ticker in enumerate(STOCKS):
        try:
            r = analyze(ticker)
            if r:
                results.append(r)
                ok += 1
            time.sleep(0.5)
        except Exception as e:
            print(f"  Error {ticker}: {e}")
        if (i+1) % 20 == 0:
            print(f"  {i+1}/{total} ({ok} ok)")

    results.sort(key=lambda x: x["swing_score"], reverse=True)
    last_results = results

    signals  = [r for r in results if r["bandar_akum"]]
    new_sigs = [s for s in signals if s["ticker"] not in prev_signals]

    print(f"  ✅ {ok}/{total} | Sinyal: {len(signals)} | Baru: {len(new_sigs)}")
    if new_sigs:
        send(build_signal_msg(signals, ok))
    elif not signals:
        send(f"🔍 <b>Scan selesai</b> — {now_wib().strftime('%H:%M')} WIB\nBelum ada sinyal bandar. Pantau terus! 👀")

    prev_signals = {s["ticker"] for s in signals}
    return results

# ── WEBHOOK — TERIMA PERINTAH TELEGRAM ───────────────────
@app.route("/webhook", methods=["POST"])
def webhook():
    global last_results
    try:
        data = flask_request.get_json()
        msg  = data.get("message", {})
        text = msg.get("text", "").strip().lower()
        cid  = str(msg.get("chat", {}).get("id", ""))

        if not text or not cid:
            return "ok"

        print(f"Perintah: {text} dari {cid}")

        if text in ["/pagi", "/pagi@mybut_bot"]:
            if last_results:
                send(build_pagi_msg(last_results), cid)
            else:
                send("⏳ Bot sedang scan pertama kali, tunggu sebentar lalu coba lagi.", cid)

        elif text in ["/akum", "/akum@mybut_bot"]:
            if last_results:
                send(build_akum_msg(last_results), cid)
            else:
                send("⏳ Bot sedang scan pertama kali, tunggu sebentar lalu coba lagi.", cid)

        elif text in ["/scan", "/scan@mybut_bot"]:
            send("🔄 Scanning sekarang... tunggu 2-3 menit ya.", cid)
            Thread(target=lambda: (run_scan(), send("✅ Scan selesai! Ketik /pagi atau /akum untuk lihat hasilnya.", cid))).start()

        elif text in ["/status", "/status@mybut_bot"]:
            send(
                f"✅ <b>Bot aktif</b>\n"
                f"⏰ {now_wib().strftime('%d/%m/%Y %H:%M')} WIB\n"
                f"📊 Saham dipantau: {len(STOCKS)}\n"
                f"📋 Data tersedia: {len(last_results)} saham\n"
                f"🔔 Sinyal aktif: {len([r for r in last_results if r['bandar_akum']])}", cid
            )

        elif text in ["/help", "/start", "/help@mybut_bot", "/start@mybut_bot"]:
            send(build_help_msg(), cid)

        else:
            send("❓ Perintah tidak dikenal. Ketik /help untuk daftar perintah.", cid)

    except Exception as e:
        print(f"Webhook error: {e}")
    return "ok"

# ── WEB SERVER ───────────────────────────────────────────
@app.route("/")
def home():
    return (
        f"<html><body style='font-family:monospace;background:#020817;color:#22d3ee;padding:40px'>"
        f"<h2>🤖 IDX Broker Flow Scanner</h2>"
        f"<p style='color:#94a3b8'>Status: Aktif ✅</p>"
        f"<p style='color:#94a3b8'>Saham: {len(STOCKS)}</p>"
        f"<p style='color:#94a3b8'>Waktu: {now_wib().strftime('%d/%m/%Y %H:%M:%S')} WIB</p>"
        f"<p style='color:#94a3b8'>Perintah: /pagi /akum /scan /status /help</p>"
        f"</body></html>"
    )

# ── MAIN LOOP ─────────────────────────────────────────────
def main_loop():
    global morning_sent, last_scan_hour
    print(f"🤖 IDX Bot — {len(STOCKS)} saham")

    time.sleep(5)
    set_webhook()

    send(
        f"🤖 <b>IDX Broker Flow Scanner aktif!</b>\n\n"
        f"📊 <b>{len(STOCKS)} saham IHSG</b>\n"
        f"📡 Yahoo Finance + IDX resmi\n\n"
        f"<b>Perintah tersedia:</b>\n"
        f"⚡ /pagi — beli pagi jual sore\n"
        f"📈 /akum — akumulasi bandar\n"
        f"🔄 /scan — scan sekarang\n"
        f"📊 /status — status bot\n\n"
        f"⏰ {now_wib().strftime('%d/%m/%Y %H:%M')} WIB ✅"
    )

    while True:
        n        = now_wib()
        is_wdays = n.weekday() < 5
        today    = n.strftime("%Y-%m-%d")

        if is_wdays and n.hour == 8 and 44 <= n.minute <= 46 and morning_sent != today:
            results = run_scan()
            send(build_morning_msg(results))
            morning_sent = today

        elif is_wdays and 9 <= n.hour <= 16 and n.hour != last_scan_hour:
            run_scan()
            last_scan_hour = n.hour

        elif is_wdays and n.hour == 16 and n.minute >= 30 and last_scan_hour != 99:
            run_scan()
            last_scan_hour = 99

        time.sleep(60)

if __name__ == "__main__":
    Thread(target=main_loop, daemon=True).start()
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
