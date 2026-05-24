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

broker_history   = defaultdict(list)
fundamental_cache = {}  # cache fundamental, refresh tiap hari
prev_signals     = set()
morning_sent     = None
last_scan_hour   = -1
last_results     = []

# ── HELPER ───────────────────────────────────────────────
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

# ── AMBIL HARGA + TEKNIKAL DARI YAHOO ────────────────────
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

        indicators = result[0].get("indicators", {})
        quote  = indicators.get("quote", [{}])[0]
        highs  = [x for x in (quote.get("high")   or []) if x]
        lows   = [x for x in (quote.get("low")    or []) if x]
        vols   = [x for x in (quote.get("volume") or []) if x]

        avg_vol   = sum(vols[:-1]) / len(vols[:-1]) if len(vols) > 1 else volume
        vol_ratio = volume / avg_vol if avg_vol else 1
        daily_ranges = [(h-l)/l*100 for h,l in zip(highs,lows) if l > 0]
        avg_range = sum(daily_ranges)/len(daily_ranges) if daily_ranges else 0

        if price == 0: return None
        return {
            "price": price, "change": round(change,2),
            "volume": volume, "vol_ratio": round(vol_ratio,2),
            "avg_range": round(avg_range,2),
        }
    except Exception as e:
        print(f"Yahoo price error {ticker}: {e}")
        return None

# ── AMBIL FUNDAMENTAL DARI YAHOO ─────────────────────────
def get_fundamental(ticker):
    # Cek cache — refresh tiap hari
    today = now_wib().strftime("%Y-%m-%d")
    cached = fundamental_cache.get(ticker)
    if cached and cached.get("date") == today:
        return cached

    try:
        url = f"https://query1.finance.yahoo.com/v10/finance/quoteSummary/{ticker}.JK?modules=financialData,defaultKeyStatistics,summaryDetail,incomeStatementHistory"
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=12)
        if r.status_code != 200: return None
        data = r.json().get("quoteSummary", {}).get("result", [])
        if not data: return None
        d = data[0]

        fin  = d.get("financialData", {})
        keys = d.get("defaultKeyStatistics", {})
        summ = d.get("summaryDetail", {})
        inc  = d.get("incomeStatementHistory", {}).get("incomeStatementHistory", [])

        # Rasio utama
        per       = summ.get("trailingPE",   {}).get("raw", None)
        roe       = fin.get("returnOnEquity",{}).get("raw", None)
        der       = keys.get("debtToEquity", {}).get("raw", None)
        div_yield = summ.get("dividendYield",{}).get("raw", None)
        profit_margin = fin.get("profitMargins",{}).get("raw", None)
        revenue_growth = fin.get("revenueGrowth",{}).get("raw", None)
        earnings_growth = fin.get("earningsGrowth",{}).get("raw", None)

        # Cek pertumbuhan laba dari histori
        laba_list = []
        for stmt in inc[:3]:
            net = stmt.get("netIncome", {}).get("raw", None)
            if net: laba_list.append(net)
        laba_tumbuh = len(laba_list) >= 2 and all(laba_list[i] > laba_list[i+1] for i in range(len(laba_list)-1))

        # Konversi
        if roe is not None: roe = roe * 100          # jadi persen
        if der is not None: der = der / 100           # Yahoo kasih dalam %, dibagi 100
        if div_yield is not None: div_yield = div_yield * 100

        # Skor fundamental (0-100)
        skor = 0
        catatan = []

        # PER
        if per is not None:
            if per < 10:   skor += 25; catatan.append(f"PER {per:.1f} (murah)")
            elif per < 20: skor += 15; catatan.append(f"PER {per:.1f} (wajar)")
            elif per < 30: skor += 5;  catatan.append(f"PER {per:.1f} (mahal)")
            else:          catatan.append(f"PER {per:.1f} (terlalu mahal)")
        else:
            catatan.append("PER N/A")

        # ROE
        if roe is not None:
            if roe > 20:   skor += 25; catatan.append(f"ROE {roe:.1f}% (sangat bagus)")
            elif roe > 15: skor += 15; catatan.append(f"ROE {roe:.1f}% (bagus)")
            elif roe > 10: skor += 8;  catatan.append(f"ROE {roe:.1f}% (cukup)")
            else:          catatan.append(f"ROE {roe:.1f}% (lemah)")
        else:
            catatan.append("ROE N/A")

        # DER
        if der is not None:
            if der < 0.5:  skor += 20; catatan.append(f"DER {der:.2f} (aman)")
            elif der < 1:  skor += 12; catatan.append(f"DER {der:.2f} (normal)")
            elif der < 1.5:skor += 5;  catatan.append(f"DER {der:.2f} (agak tinggi)")
            else:          catatan.append(f"DER {der:.2f} (hutang banyak)")
        else:
            catatan.append("DER N/A")

        # Pertumbuhan laba
        if laba_tumbuh:
            skor += 20; catatan.append("Laba tumbuh ✅")
        elif earnings_growth and earnings_growth > 0:
            skor += 10; catatan.append(f"EPS growth {earnings_growth*100:.1f}%")

        # Dividen
        if div_yield and div_yield > 3:
            skor += 10; catatan.append(f"Dividen {div_yield:.1f}%")
        elif div_yield and div_yield > 0:
            skor += 5; catatan.append(f"Dividen {div_yield:.1f}%")

        # Lolos filter fundamental?
        lolos = (
            (per is not None and per < 25) and
            (roe is not None and roe > 12) and
            (der is None or der < 1.5)
        )

        result = {
            "date":      today,
            "per":       per,
            "roe":       roe,
            "der":       der,
            "div_yield": div_yield,
            "laba_tumbuh": laba_tumbuh,
            "skor":      min(skor, 100),
            "catatan":   catatan,
            "lolos":     lolos,
        }
        fundamental_cache[ticker] = result
        return result

    except Exception as e:
        print(f"Fundamental error {ticker}: {e}")
        return None

# ── BROKER SUMMARY IDX ───────────────────────────────────
def get_broker_summary_idx(ticker):
    try:
        url = f"https://idx.co.id/api/broker-summary?code={ticker}&start=0&length=99"
        r = requests.get(url, headers={
            "User-Agent": "Mozilla/5.0",
            "Referer": "https://idx.co.id/",
        }, timeout=10)
        if r.status_code != 200: return None
        data = r.json()
        rows = data.get("data") or data.get("Data") or []
        return rows if rows else None
    except:
        return None

def parse_broker_net(rows):
    bandar_net, ritel_net = 0.0, 0.0
    bandar_detail = {}
    for row in rows:
        broker = str(row.get("broker_id") or row.get("BrokerID") or "").upper().strip()
        buy  = float(row.get("buy_value")  or row.get("BuyValue")  or 0)
        sell = float(row.get("sell_value") or row.get("SellValue") or 0)
        net  = float(row.get("net_value")  or row.get("NetValue")  or (buy-sell))
        if broker in BANDAR_BROKERS:
            bandar_net += net
            bandar_detail[broker] = net
        if broker in RITEL_BROKERS:
            ritel_net += net
    return bandar_net, ritel_net, bandar_detail

# ── ANALISA LENGKAP ───────────────────────────────────────
def analyze(ticker):
    price_data = get_price_yahoo(ticker)
    if not price_data: return None

    broker_rows = get_broker_summary_idx(ticker)
    if not broker_rows: return None
    bandar_net, ritel_net, bandar_detail = parse_broker_net(broker_rows)

    # Histori broker
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

    recent        = days[-min(5,len(days)):]
    bandar_net_5d = sum(d["bandar_net"] for d in recent)
    ritel_net_5d  = sum(d["ritel_net"]  for d in recent)

    bandar_akum = bandar_net_5d > 0 and bandar_accum_days >= 2
    ritel_jual  = ritel_net_5d < 0
    sinyal_kuat = bandar_akum and ritel_jual and bandar_accum_days >= 3

    price     = price_data["price"]
    change    = price_data["change"]
    vol_ratio = price_data["vol_ratio"]
    avg_range = price_data["avg_range"]

    # Skor teknikal/broker
    swing_score = round(min(
        min(bandar_accum_days/5,1)*40 +
        (min(bandar_net_5d/5e11,1)*30 if bandar_net_5d>0 else 0) +
        (20 if ritel_jual else 0) +
        (10 if abs(change)<2 else 5), 100
    ))
    scalp_score = round(min(
        min(vol_ratio/4,1)*35 +
        min(avg_range/3,1)*30 +
        (20 if price>=500 else 10 if price>=200 else 3) +
        (15 if vol_ratio>=2 else 5), 100
    ))

    # Fundamental (ambil dari cache, tidak block scan)
    fundamental = fundamental_cache.get(ticker)

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
        "fundamental":       fundamental,
        "fundamental_lolos": fundamental.get("lolos", False) if fundamental else False,
        "fundamental_skor":  fundamental.get("skor", 0) if fundamental else 0,
    }

# ── FORMAT PESAN ──────────────────────────────────────────
def fmt_fundamental(f):
    if not f: return "   📊 Fundamental: data belum tersedia\n"
    lines = []
    if f.get("per"):       lines.append(f"PER {f['per']:.1f}")
    if f.get("roe"):       lines.append(f"ROE {f['roe']:.1f}%")
    if f.get("der") is not None: lines.append(f"DER {f['der']:.2f}")
    if f.get("div_yield"): lines.append(f"Div {f['div_yield']:.1f}%")
    badge = "✅ Fundamental KUAT" if f.get("lolos") else "⚠️ Cek fundamental"
    return f"   📊 {badge} | {' | '.join(lines)}\n   📈 Skor fundamental: {f.get('skor',0)}/100\n"

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
        f_badge  = "✅ Fundamental OK" if s["fundamental_lolos"] else "⚠️ Cek fundamental"
        lines.append(
            f"{medal[i]} <b>{s['ticker']}</b> — Scalp: <b>{s['scalp_score']}/100</b>\n"
            f"   💰 Rp{s['price']:,.0f}  {arrow} {s['change']:+.2f}%\n"
            f"   📊 Vol: {s['vol_ratio']:.1f}x | Range: {s['avg_range']:.1f}%\n"
            f"   🎯 Target: Rp{target:,.0f} | 🛑 SL: Rp{stoploss:,.0f}\n"
            f"   {f_badge} (Skor: {s['fundamental_skor']}/100)\n"
        )
    lines.append("⚠ Target estimasi dari range harian. Bukan rekomendasi. DYOR!")
    return "\n".join(lines)

def build_akum_msg(results):
    picks = [r for r in results if r["bandar_akum"]]
    picks = sorted(picks, key=lambda x: x["swing_score"], reverse=True)[:5]
    medal = ["🥇","🥈","🥉","4️⃣","5️⃣"]
    lines = ["📈 <b>AKUMULASI BANDAR (AK/MG)</b>",
             f"📅 {now_wib().strftime('%d/%m/%Y %H:%M')} WIB\n"]
    if not picks:
        lines.append("Belum ada sinyal akumulasi bandar.")
        return "\n".join(lines)
    for i, s in enumerate(picks):
        arrow = "📈" if s["change"] >= 0 else "📉"
        kuat  = " 🔥" if s["sinyal_kuat"] else ""
        top_b = ", ".join([f"{b}({fmt(v)})" for b,v in s["top_bandar"]]) or "-"
        lines.append(
            f"{medal[i]} <b>{s['ticker']}</b> — Swing: <b>{s['swing_score']}/100</b>{kuat}\n"
            f"   💰 Rp{s['price']:,.0f}  {arrow} {s['change']:+.2f}%\n"
            f"   🏦 Bandar akum <b>{s['bandar_accum_days']} hari</b> | Net: {fmt(s['bandar_net_5d'])}\n"
            f"   🏦 Broker: {top_b}\n"
            f"   👥 Ritel: {'⚠️ Jual' if s['ritel_jual'] else '➖'} {fmt(s['ritel_net_5d'])}\n"
        )
    lines.append("⚠ Data broker delay 1 hari. Bukan rekomendasi. DYOR!")
    return "\n".join(lines)

def build_fundamental_msg(results):
    """Saham fundamental kuat + sedang diakumulasi = entry ideal"""
    picks = [r for r in results if r["fundamental_lolos"] and r["bandar_akum"]]
    picks = sorted(picks, key=lambda x: x["fundamental_skor"] + x["swing_score"], reverse=True)[:5]
    medal = ["🥇","🥈","🥉","4️⃣","5️⃣"]
    lines = [
        "💎 <b>FUNDAMENTAL KUAT + BANDAR AKUMULASI</b>",
        f"📅 {now_wib().strftime('%d/%m/%Y %H:%M')} WIB\n",
        "Saham dengan bisnis bagus yang sedang dikumpulkan bandar — entry ideal!\n",
    ]
    if not picks:
        lines.append(
            "Belum ada saham yang lolos filter fundamental SEKALIGUS diakumulasi bandar.\n\n"
            "Coba ketik:\n/akum — lihat akumulasi bandar saja\n/fa — lihat fundamental saja"
        )
        return "\n".join(lines)
    for i, s in enumerate(picks):
        arrow = "📈" if s["change"] >= 0 else "📉"
        kuat  = " 🔥" if s["sinyal_kuat"] else ""
        f     = s["fundamental"]
        per_str = f"PER {f['per']:.1f}" if f and f.get("per") else ""
        roe_str = f"ROE {f['roe']:.1f}%" if f and f.get("roe") else ""
        der_str = f"DER {f['der']:.2f}" if f and f.get("der") is not None else ""
        rasio   = " | ".join(filter(None, [per_str, roe_str, der_str]))
        lines.append(
            f"{medal[i]} <b>{s['ticker']}</b>{kuat}\n"
            f"   💰 Rp{s['price']:,.0f}  {arrow} {s['change']:+.2f}%\n"
            f"   📊 Fundamental: {rasio}\n"
            f"   📈 Skor F: {s['fundamental_skor']}/100 | Skor T: {s['swing_score']}/100\n"
            f"   🏦 Bandar akum {s['bandar_accum_days']} hari | Net: {fmt(s['bandar_net_5d'])}\n"
            f"   👥 Ritel: {'⚠️ Jual' if s['ritel_jual'] else '➖'} {fmt(s['ritel_net_5d'])}\n"
        )
    lines.append("⚠ Data broker delay 1 hari. Bukan rekomendasi. DYOR!")
    return "\n".join(lines)

def build_fa_msg(results):
    """Daftar saham dengan fundamental kuat saja"""
    picks = [r for r in results if r["fundamental_lolos"]]
    picks = sorted(picks, key=lambda x: x["fundamental_skor"], reverse=True)[:8]
    lines = [
        "📊 <b>SAHAM FUNDAMENTAL KUAT</b>",
        f"📅 {now_wib().strftime('%d/%m/%Y %H:%M')} WIB\n",
        "Filter: PER<25, ROE>12%, DER<1.5\n",
    ]
    if not picks:
        lines.append("Data fundamental belum tersedia. Ketik /scan dulu.")
        return "\n".join(lines)
    for s in picks:
        f     = s["fundamental"]
        arrow = "📈" if s["change"] >= 0 else "📉"
        per_str = f"PER {f['per']:.1f}" if f and f.get("per") else "PER N/A"
        roe_str = f"ROE {f['roe']:.1f}%" if f and f.get("roe") else "ROE N/A"
        der_str = f"DER {f['der']:.2f}" if f and f.get("der") is not None else "DER N/A"
        div_str = f"Div {f['div_yield']:.1f}%" if f and f.get("div_yield") else ""
        akum_str = f" | 🏦 Bandar akum {s['bandar_accum_days']}h" if s["bandar_akum"] else ""
        lines.append(
            f"• <b>{s['ticker']}</b> Rp{s['price']:,.0f} {arrow} {s['change']:+.2f}%\n"
            f"  {per_str} | {roe_str} | {der_str}{' | '+div_str if div_str else ''}\n"
            f"  Skor F: {s['fundamental_skor']}/100{akum_str}\n"
        )
    lines.append("⚠ Bukan rekomendasi. DYOR!")
    return "\n".join(lines)

def build_signal_msg(signals, total):
    medal = ["🥇","🥈","🥉","4️⃣","5️⃣"]
    lines = ["🚨 <b>SINYAL AKUMULASI BIG MONEY!</b>",
             f"📅 {now_wib().strftime('%d/%m/%Y %H:%M')} WIB",
             f"🔍 Dari {total} saham IHSG\n"]
    for i, s in enumerate(signals[:5]):
        arrow   = "📈" if s["change"] >= 0 else "📉"
        kuat    = " 🔥 <b>KUAT!</b>" if s["sinyal_kuat"] else ""
        f_badge = " 💎 Fundamental OK" if s["fundamental_lolos"] else ""
        top_b   = ", ".join([f"{b}({fmt(v)})" for b,v in s["top_bandar"]]) or "-"
        lines.append(
            f"{medal[i]} <b>{s['ticker']}</b>{kuat}{f_badge}\n"
            f"   💰 Rp{s['price']:,.0f}  {arrow} {s['change']:+.2f}%\n"
            f"   🏦 Bandar akum {s['bandar_accum_days']} hari | Net: {fmt(s['bandar_net_5d'])}\n"
            f"   🏦 Broker: {top_b}\n"
            f"   👥 Ritel: {'⚠️ Jual' if s['ritel_jual'] else '➖'} {fmt(s['ritel_net_5d'])}\n"
        )
    lines.append("⚠ Data broker delay 1 hari. Bukan rekomendasi. DYOR!")
    return "\n".join(lines)

def build_morning_msg(results):
    kuat  = [s for s in results if s["sinyal
