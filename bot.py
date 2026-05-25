import os
import time
import requests
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

prev_signals   = set()
morning_sent   = None
last_scan_hour = -1
last_results   = []
fundamental_cache = {}

def fmt(n):
    if abs(n) >= 1e12: return f"{n/1e12:.1f}T"
    if abs(n) >= 1e9:  return f"{n/1e9:.1f}B"
    if abs(n) >= 1e6:  return f"{n/1e6:.1f}M"
    return f"{n:.0f}"

def send(msg, chat_id=None):
    cid = chat_id or CHAT_ID
    if not BOT_TOKEN or not cid: return
    try:
        requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json={"chat_id": cid, "text": msg, "parse_mode": "HTML"},
            timeout=10
        )
    except Exception as e:
        print(f"Telegram error: {e}")

def set_webhook():
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/setWebhook",
            json={"url": "https://idx-bot.onrender.com/webhook"}, timeout=10
        )
        print(f"Webhook: {r.json()}")
    except Exception as e:
        print(f"Webhook error: {e}")

# ── AMBIL DATA YAHOO FINANCE (harga + volume + foreign) ──
def get_yahoo_data(ticker):
    try:
        symbol = f"{ticker}.JK"
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?interval=1d&range=20d"
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=12)
        if r.status_code != 200: return None
        data   = r.json()
        result = data.get("chart", {}).get("result", [])
        if not result: return None

        meta   = result[0].get("meta", {})
        price  = meta.get("regularMarketPrice", 0)
        prev   = meta.get("chartPreviousClose", 0)
        volume = meta.get("regularMarketVolume", 0)
        change = ((price - prev) / prev * 100) if prev else 0
        if price == 0: return None

        indicators = result[0].get("indicators", {})
        quote  = indicators.get("quote", [{}])[0]
        closes = [x for x in (quote.get("close")  or []) if x]
        highs  = [x for x in (quote.get("high")   or []) if x]
        lows   = [x for x in (quote.get("low")    or []) if x]
        vols   = [x for x in (quote.get("volume") or []) if x]

        # Volume ratio vs rata-rata 14 hari
        avg_vol14 = sum(vols[-15:-1]) / 14 if len(vols) >= 15 else (sum(vols[:-1]) / max(len(vols)-1, 1))
        vol_ratio = volume / avg_vol14 if avg_vol14 else 1

        # Range harian rata-rata
        daily_ranges = [(h-l)/l*100 for h,l in zip(highs[-10:], lows[-10:]) if l > 0]
        avg_range = sum(daily_ranges)/len(daily_ranges) if daily_ranges else 0

        # Deteksi akumulasi dari harga:
        # Harga dalam range sempit tapi volume naik = akumulasi
        if len(closes) >= 5:
            last5_closes = closes[-5:]
            price_range  = (max(last5_closes) - min(last5_closes)) / min(last5_closes) * 100
            price_compression = price_range < 3.0  # harga bergerak < 3% dalam 5 hari
        else:
            price_compression = False

        # Tren volume naik (bandar mulai masuk)
        if len(vols) >= 5:
            vol_trend_up = vols[-1] > vols[-2] > vols[-3]
        else:
            vol_trend_up = False

        # Harga di atas MA5 (bullish)
        ma5 = sum(closes[-5:]) / 5 if len(closes) >= 5 else price
        above_ma5 = price > ma5

        # Foreign net (dari Yahoo — net foreign tidak langsung tersedia,
        # tapi kita estimasi dari price action + volume)
        # Proxy: volume besar + harga naik tipis = foreign/institusi beli
        foreign_proxy = vol_ratio >= 1.8 and change > -1 and change < 3

        return {
            "price":             price,
            "change":            round(change, 2),
            "volume":            volume,
            "vol_ratio":         round(vol_ratio, 2),
            "avg_range":         round(avg_range, 2),
            "price_compression": price_compression,
            "vol_trend_up":      vol_trend_up,
            "above_ma5":         above_ma5,
            "foreign_proxy":     foreign_proxy,
            "closes":            closes,
            "vols":              vols,
        }
    except Exception as e:
        print(f"Yahoo error {ticker}: {e}")
        return None

# ── AMBIL FUNDAMENTAL YAHOO ───────────────────────────────
def get_fundamental(ticker):
    today = now_wib().strftime("%Y-%m-%d")
    cached = fundamental_cache.get(ticker)
    if cached and cached.get("date") == today:
        return cached
    try:
        url = (f"https://query1.finance.yahoo.com/v10/finance/quoteSummary/{ticker}.JK"
               f"?modules=financialData,defaultKeyStatistics,summaryDetail,incomeStatementHistory")
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=12)
        if r.status_code != 200: return None
        data = r.json().get("quoteSummary", {}).get("result", [])
        if not data: return None
        d    = data[0]
        fin  = d.get("financialData", {})
        keys = d.get("defaultKeyStatistics", {})
        summ = d.get("summaryDetail", {})
        inc  = d.get("incomeStatementHistory", {}).get("incomeStatementHistory", [])

        per       = summ.get("trailingPE",    {}).get("raw")
        roe       = fin.get("returnOnEquity", {}).get("raw")
        der       = keys.get("debtToEquity",  {}).get("raw")
        div_yield = summ.get("dividendYield", {}).get("raw")

        if roe is not None: roe = roe * 100
        if der is not None: der = der / 100
        if div_yield is not None: div_yield = div_yield * 100

        laba_list = [s.get("netIncome", {}).get("raw") for s in inc[:3] if s.get("netIncome", {}).get("raw")]
        laba_tumbuh = len(laba_list) >= 2 and all(laba_list[i] > laba_list[i+1] for i in range(len(laba_list)-1))

        skor = 0
        if per:
            if per < 10:   skor += 25
            elif per < 20: skor += 15
            elif per < 30: skor += 5
        if roe:
            if roe > 20:   skor += 25
            elif roe > 15: skor += 15
            elif roe > 10: skor += 8
        if der is not None:
            if der < 0.5:  skor += 20
            elif der < 1:  skor += 12
            elif der < 1.5:skor += 5
        if laba_tumbuh: skor += 20
        if div_yield and div_yield > 3: skor += 10
        elif div_yield and div_yield > 0: skor += 5

        lolos = (
            (per is None or per < 25) and
            (roe is None or roe > 12) and
            (der is None or der < 1.5)
        ) and (per is not None or roe is not None)

        result = {
            "date": today, "per": per, "roe": roe, "der": der,
            "div_yield": div_yield, "laba_tumbuh": laba_tumbuh,
            "skor": min(skor, 100), "lolos": lolos,
        }
        fundamental_cache[ticker] = result
        return result
    except Exception as e:
        print(f"FA error {ticker}: {e}")
        return None

# ── ANALISA PER SAHAM ─────────────────────────────────────
def analyze(ticker):
    d = get_yahoo_data(ticker)
    if not d: return None

    price     = d["price"]
    change    = d["change"]
    vol_ratio = d["vol_ratio"]
    avg_range = d["avg_range"]

    # ── SKOR AKUMULASI (deteksi bandar masuk) ──
    # Pola: volume naik + harga compression + di atas MA = akumulasi
    akum_score = 0
    patterns   = []

    if vol_ratio >= 3:
        akum_score += 30; patterns.append("Volume Spike 3x+")
    elif vol_ratio >= 2:
        akum_score += 20; patterns.append("Volume Spike 2x+")
    elif vol_ratio >= 1.5:
        akum_score += 10; patterns.append("Volume Naik")

    if d["price_compression"]:
        akum_score += 25; patterns.append("Price Compression")

    if d["vol_trend_up"]:
        akum_score += 15; patterns.append("Volume Trend Naik")

    if d["above_ma5"]:
        akum_score += 10; patterns.append("Di atas MA5")

    if d["foreign_proxy"]:
        akum_score += 20; patterns.append("Potensi Foreign Buy")

    if change > 0 and change < 2 and vol_ratio >= 2:
        akum_score += 10; patterns.append("Stealth Accum")

    akum_score = min(akum_score, 100)
    is_akum    = akum_score >= 55

    # ── SKOR SCALPING ──
    scalp_score = round(min(
        min(vol_ratio/4,1)*35 +
        min(avg_range/3,1)*30 +
        (20 if price >= 500 else 10 if price >= 200 else 3) +
        (15 if vol_ratio >= 2 else 5), 100
    ))

    fundamental = fundamental_cache.get(ticker)

    return {
        "ticker":           ticker,
        "price":            price,
        "change":           change,
        "volume":           d["volume"],
        "vol_ratio":        vol_ratio,
        "avg_range":        avg_range,
        "akum_score":       akum_score,
        "scalp_score":      scalp_score,
        "is_akum":          is_akum,
        "patterns":         patterns,
        "price_compression":d["price_compression"],
        "vol_trend_up":     d["vol_trend_up"],
        "fundamental":      fundamental,
        "f_lolos":          fundamental.get("lolos", False) if fundamental else False,
        "f_skor":           fundamental.get("skor", 0) if fundamental else 0,
    }

# ── FORMAT PESAN ──────────────────────────────────────────
def build_akum_msg(results):
    picks = [r for r in results if r["is_akum"]]
    picks = sorted(picks, key=lambda x: x["akum_score"], reverse=True)[:5]
    medal = ["🥇","🥈","🥉","4️⃣","5️⃣"]
    lines = ["📈 <b>SAHAM TERDETEKSI AKUMULASI</b>",
             f"📅 {now_wib().strftime('%d/%m/%Y %H:%M')} WIB\n",
             "Pola: Volume spike + Price compression + Trend naik\n"]
    if not picks:
        lines.append("Belum ada sinyal akumulasi kuat saat ini.")
        return "\n".join(lines)
    for i, s in enumerate(picks):
        arrow  = "📈" if s["change"] >= 0 else "📉"
        f_badge = " 💎" if s["f_lolos"] else ""
        lines.append(
            f"{medal[i]} <b>{s['ticker']}</b>{f_badge} — Score: <b>{s['akum_score']}/100</b>\n"
            f"   💰 Rp{s['price']:,.0f}  {arrow} {s['change']:+.2f}%\n"
            f"   📊 Volume: {s['vol_ratio']:.1f}x rata-rata\n"
            f"   🔎 {', '.join(s['patterns'][:3])}\n"
        )
    lines.append("⚠ Bukan rekomendasi beli/jual. DYOR!")
    return "\n".join(lines)

def build_pagi_msg(results):
    picks = sorted(results, key=lambda x: x["scalp_score"], reverse=True)
    picks = [p for p in picks if p["scalp_score"] >= 45][:5]
    medal = ["🥇","🥈","🥉","4️⃣","5️⃣"]
    lines = ["⚡ <b>SAHAM BELI PAGI JUAL SORE</b>",
             f"📅 {now_wib().strftime('%d/%m/%Y %H:%M')} WIB\n"]
    if not picks:
        lines.append("Belum ada kandidat scalping hari ini.")
        return "\n".join(lines)
    for i, s in enumerate(picks):
        arrow    = "📈" if s["change"] >= 0 else "📉"
        target   = round(s["price"] * (1 + s["avg_range"]*0.4/100))
        stoploss = round(s["price"] * (1 - s["avg_range"]*0.3/100))
        f_badge  = " 💎 Fundamental OK" if s["f_lolos"] else ""
        lines.append(
            f"{medal[i]} <b>{s['ticker']}</b>{f_badge} — Scalp: <b>{s['scalp_score']}/100</b>\n"
            f"   💰 Rp{s['price']:,.0f}  {arrow} {s['change']:+.2f}%\n"
            f"   📊 Vol: {s['vol_ratio']:.1f}x | Range: {s['avg_range']:.1f}%\n"
            f"   🎯 Target: Rp{target:,.0f} | 🛑 SL: Rp{stoploss:,.0f}\n"
        )
    lines.append("⚠ Target estimasi dari range harian. Bukan rekomendasi. DYOR!")
    return "\n".join(lines)

def build_combo_msg(results):
    picks = [r for r in results if r["f_lolos"] and r["is_akum"]]
    picks = sorted(picks, key=lambda x: x["f_skor"] + x["akum_score"], reverse=True)[:5]
    medal = ["🥇","🥈","🥉","4️⃣","5️⃣"]
    lines = ["💎 <b>FUNDAMENTAL KUAT + AKUMULASI</b>",
             f"📅 {now_wib().strftime('%d/%m/%Y %H:%M')} WIB\n",
             "Saham bisnis bagus yang sedang dikumpulkan — entry ideal!\n"]
    if not picks:
        lines.append(
            "Belum ada yang lolos keduanya saat ini.\n\n"
            "Coba: /akum untuk akumulasi saja\n/fa untuk fundamental saja"
        )
        return "\n".join(lines)
    for i, s in enumerate(picks):
        arrow = "📈" if s["change"] >= 0 else "📉"
        f = s["fundamental"]
        rasio = " | ".join(filter(None, [
            f"PER {f['per']:.1f}" if f and f.get("per") else "",
            f"ROE {f['roe']:.1f}%" if f and f.get("roe") else "",
            f"DER {f['der']:.2f}" if f and f.get("der") is not None else "",
        ]))
        lines.append(
            f"{medal[i]} <b>{s['ticker']}</b>\n"
            f"   💰 Rp{s['price']:,.0f}  {arrow} {s['change']:+.2f}%\n"
            f"   📊 {rasio}\n"
            f"   📈 Skor F: {s['f_skor']}/100 | Akum: {s['akum_score']}/100\n"
            f"   🔎 {', '.join(s['patterns'][:2])}\n"
        )
    lines.append("⚠ Bukan rekomendasi. DYOR!")
    return "\n".join(lines)

def build_fa_msg(results):
    picks = [r for r in results if r["f_lolos"]]
    picks = sorted(picks, key=lambda x: x["f_skor"], reverse=True)[:8]
    lines = ["📊 <b>SAHAM FUNDAMENTAL KUAT</b>",
             f"📅 {now_wib().strftime('%d/%m/%Y %H:%M')} WIB\n",
             "Filter: PER<25, ROE>12%, DER<1.5\n"]
    if not picks:
        lines.append("Data fundamental belum tersedia. Ketik /scan dulu.")
        return "\n".join(lines)
    for s in picks:
        f     = s["fundamental"]
        arrow = "📈" if s["change"] >= 0 else "📉"
        rasio = " | ".join(filter(None, [
            f"PER {f['per']:.1f}" if f and f.get("per") else "",
            f"ROE {f['roe']:.1f}%" if f and f.get("roe") else "",
            f"DER {f['der']:.2f}" if f and f.get("der") is not None else "",
            f"Div {f['div_yield']:.1f}%" if f and f.get("div_yield") else "",
        ]))
        akum = " | 📈 Akumulasi" if s["is_akum"] else ""
        lines.append(
            f"• <b>{s['ticker']}</b> Rp{s['price']:,.0f} {arrow} {s['change']:+.2f}%\n"
            f"  {rasio}{akum}\n"
            f"  Skor F: {s['f_skor']}/100\n"
        )
    lines.append("⚠ Bukan rekomendasi. DYOR!")
    return "\n".join(lines)

def build_signal_msg(signals, total):
    medal = ["🥇","🥈","🥉","4️⃣","5️⃣"]
    lines = ["🚨 <b>SINYAL AKUMULASI TERDETEKSI!</b>",
             f"📅 {now_wib().strftime('%d/%m/%Y %H:%M')} WIB",
             f"🔍 Dari {total} saham IHSG\n"]
    for i, s in enumerate(signals[:5]):
        arrow  = "📈" if s["change"] >= 0 else "📉"
        f_badge = " 💎 Fundamental OK" if s["f_lolos"] else ""
        lines.append(
            f"{medal[i]} <b>{s['ticker']}</b>{f_badge} — Score: <b>{s['akum_score']}/100</b>\n"
            f"   💰 Rp{s['price']:,.0f}  {arrow} {s['change']:+.2f}%\n"
            f"   📊 Volume: {s['vol_ratio']:.1f}x\n"
            f"   🔎 {', '.join(s['patterns'][:3])}\n"
        )
    lines.append("⚠ Bukan rekomendasi. DYOR!")
    return "\n".join(lines)

def build_morning_msg(results):
    combo = [r for r in results if r["f_lolos"] and r["is_akum"]][:2]
    akum  = [r for r in results if r["is_akum"] and not r["f_lolos"]][:3]
    scalp = sorted(results, key=lambda x: x["scalp_score"], reverse=True)[:3]
    lines = ["🌅 <b>REKOMENDASI PAGI</b>",
             f"📅 {now_wib().strftime('%d/%m/%Y')} WIB | Market buka 09:00\n"]
    if combo:
        lines.append("💎 <b>FUNDAMENTAL KUAT + AKUMULASI:</b>")
        for s in combo:
            lines.append(f"   ⭐ <b>{s['ticker']}</b> Rp{s['price']:,.0f} | F:{s['f_skor']} | Akum:{s['akum_score']}\n")
    if akum:
        lines.append("📈 <b>TERDETEKSI AKUMULASI:</b>")
        for s in akum:
            lines.append(f"   • <b>{s['ticker']}</b> Rp{s['price']:,.0f} | Score: {s['akum_score']}/100\n")
    if scalp:
        lines.append("⚡ <b>KANDIDAT SCALPING:</b>")
        for s in scalp:
            lines.append(f"   • <b>{s['ticker']}</b> Rp{s['price']:,.0f} | Range {s['avg_range']:.1f}% | Vol {s['vol_ratio']:.1f}x\n")
    if not combo and not akum:
        lines.append("Belum ada sinyal kuat. Ketik /scan untuk refresh.")
    lines.append("\nKetik /combo /akum /pagi /fa untuk detail")
    lines.append("⚠ Bukan rekomendasi. DYOR!")
    return "\n".join(lines)

def build_help_msg():
    return (
        "🤖 <b>IDX Bot — Perintah:</b>\n\n"
        "💎 /combo  — Fundamental kuat + Akumulasi\n"
        "📊 /fa     — Saham fundamental kuat\n"
        "📈 /akum   — Saham terdeteksi akumulasi\n"
        "⚡ /pagi   — Beli pagi jual sore (scalping)\n"
        "🔄 /scan   — Scan sekarang\n"
        "📋 /status — Status bot\n"
        "❓ /help   — Perintah ini\n\n"
        "💡 Gunakan /combo untuk hasil terbaik!"
    )

# ── SCAN ─────────────────────────────────────────────────
def run_scan(fetch_fa=False):
    global prev_signals, last_results
    total = len(STOCKS)
    results = []
    ok = 0
    print(f"[{now_wib().strftime('%H:%M')} WIB] Scanning {total} saham...")
    for i, ticker in enumerate(STOCKS):
        try:
            if fetch_fa or ticker not in fundamental_cache:
                get_fundamental(ticker)
                time.sleep(0.2)
            r = analyze(ticker)
            if r:
                results.append(r)
                ok += 1
            time.sleep(0.4)
        except Exception as e:
            print(f"  Error {ticker}: {e}")
        if (i+1) % 20 == 0:
            print(f"  {i+1}/{total} ({ok} ok)")

    results.sort(key=lambda x: x["akum_score"], reverse=True)
    last_results = results
    signals  = [r for r in results if r["is_akum"]]
    new_sigs = [s for s in signals if s["ticker"] not in prev_signals]
    combos   = [r for r in results if r["f_lolos"] and r["is_akum"]]

    print(f"  ✅ {ok}/{total} | Sinyal: {len(signals)} | Combo: {len(combos)} | Baru: {len(new_sigs)}")

    if new_sigs:
        send(build_signal_msg(signals, ok))
        if combos:
            send(f"💎 {len(combos)} saham di atas juga LOLOS fundamental! Ketik /combo")
    elif not signals:
        print("  Tidak ada sinyal.")

    prev_signals = {s["ticker"] for s in signals}
    return results

# ── WEBHOOK ───────────────────────────────────────────────
@app.route("/webhook", methods=["POST"])
def webhook():
    try:
        data = flask_request.get_json()
        msg  = data.get("message", {})
        text = msg.get("text", "").strip().lower().split("@")[0]
        cid  = str(msg.get("chat", {}).get("id", ""))
        if not text or not cid: return "ok"

        if text == "/pagi":
            send(build_pagi_msg(last_results) if last_results else "⏳ Ketik /scan dulu.", cid)
        elif text == "/akum":
            send(build_akum_msg(last_results) if last_results else "⏳ Ketik /scan dulu.", cid)
        elif text == "/combo":
            send(build_combo_msg(last_results) if last_results else "⏳ Ketik /scan dulu.", cid)
        elif text == "/fa":
            send(build_fa_msg(last_results) if last_results else "⏳ Ketik /scan dulu.", cid)
        elif text == "/scan":
            send("🔄 Scanning... tunggu 3-5 menit.", cid)
            Thread(target=lambda: (run_scan(True), send("✅ Selesai! Ketik /combo /akum /pagi /fa", cid))).start()
        elif text == "/status":
            send(
                f"✅ <b>Bot aktif</b>\n"
                f"⏰ {now_wib().strftime('%d/%m/%Y %H:%M')} WIB\n"
                f"📊 Dipantau: {len(STOCKS)} saham\n"
                f"📋 Data: {len(last_results)} saham\n"
                f"📈 Sinyal akumulasi: {len([r for r in last_results if r['is_akum']])}\n"
                f"💎 Combo (F+Akum): {len([r for r in last_results if r['f_lolos'] and r['is_akum']])}", cid
            )
        elif text in ["/help", "/start"]:
            send(build_help_msg(), cid)
        else:
            send("❓ Tidak dikenal. Ketik /help", cid)
    except Exception as e:
        print(f"Webhook error: {e}")
    return "ok"

@app.route("/")
def home():
    return (
        f"<html><body style='font-family:monospace;background:#020817;color:#22d3ee;padding:40px'>"
        f"<h2>🤖 IDX Bot</h2>"
        f"<p style='color:#94a3b8'>Status: Aktif ✅</p>"
        f"<p style='color:#94a3b8'>Sumber: Yahoo Finance (real-time)</p>"
        f"<p style='color:#94a3b8'>Saham: {len(STOCKS)}</p>"
        f"<p style='color:#94a3b8'>Waktu: {now_wib().strftime('%d/%m/%Y %H:%M:%S')} WIB</p>"
        f"<p style='color:#94a3b8'>CMD: /combo /akum /pagi /fa /scan /status /help</p>"
        f"</body></html>"
    )

# ── MAIN LOOP ─────────────────────────────────────────────
def main_loop():
    global morning_sent, last_scan_hour
    print(f"🤖 IDX Bot — {len(STOCKS)} saham via Yahoo Finance")
    time.sleep(5)
    set_webhook()
    send(
        f"🤖 <b>IDX Bot aktif!</b>\n\n"
        f"📊 <b>{len(STOCKS)} saham IHSG</b>\n"
        f"📡 Sumber: Yahoo Finance (real-time)\n"
        f"🔎 Deteksi: Volume Spike, Price Compression,\n"
        f"   Volume Trend, Foreign Proxy, MA5\n\n"
        f"<b>Perintah:</b>\n"
        f"💎 /combo  📈 /akum  ⚡ /pagi\n"
        f"📊 /fa  🔄 /scan  📋 /status\n\n"
        f"⏰ {now_wib().strftime('%d/%m/%Y %H:%M')} WIB ✅"
    )

    while True:
        n        = now_wib()
        is_wdays = n.weekday() < 5
        today    = n.strftime("%Y-%m-%d")

        if is_wdays and n.hour == 8 and 44 <= n.minute <= 46 and morning_sent != today:
            results = run_scan(fetch_fa=True)
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
