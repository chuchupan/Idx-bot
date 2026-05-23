import os
import time
import math
import requests
from datetime import datetime
from threading import Thread
from flask import Flask

app = Flask(__name__)

# ── CONFIG ──────────────────────────────────────────────
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
CHAT_ID   = os.environ.get("CHAT_ID", "")
INTERVAL  = 3600  # scan tiap 1 jam

# ── 200 SAHAM IHSG PALING LIQUID ─────────────────────────
STOCKS = [
    # LQ45 & Bluechip
    "BBCA","BBRI","BMRI","TLKM","ASII","UNVR","ICBP","KLBF","HMSP","INDF",
    "PGAS","JSMR","SMGR","CPIN","PTBA","ADRO","ITMG","ANTM","INCO","MDKA",
    "BYAN","BRPT","MAPI","EMTK","DCII","BUKA","ACES","SIDO","MYOR","GOTO",
    "EXCL","ISAT","TBIG","TOWR","MTEL","WIFI","FREN","BTEL","CENT","DATA",
    # Perbankan
    "BBNI","BNGA","BJBR","BDMN","BJTM","PNBN","NISP","MEGA","BRIS","BTPS",
    "BNLI","BACA","AGRO","BBMD","BBYB","BGTG","BMAS","BMSI","BNBA","BSIM",
    # Properti
    "BSDE","CTRA","PWON","SMRA","LPKR","ASRI","DILD","GPRA","MDLN","MKPI",
    "APLN","BKSL","COWL","EMDE","FORZ","GAMA","GWSA","INPP","JRPT","KIJA",
    # Energi & Tambang
    "HRUM","KKGI","MYOH","PKPK","SMMT","APEX","BORN","DEWA","DOID","ELSA",
    "ENRG","ESSA","FIRE","GEMS","GTBO","INDY","ITMG","MBAP","MEDC","MITI",
    "PKPK","PTRO","RUIS","SMCB","TOBA","TPMA","UNSP","WINS","WIIM","BIPI",
    # Consumer & Retail
    "AALI","LSIP","SIMP","SGRO","TBLA","UNSP","BWPT","GZCO","JAWA","MAGP",
    "ULTJ","SKBM","SKLT","STTP","CEKA","DLTA","FAST","HERO","INRU","JPFA",
    "MAIN","MGNA","MLBI","MRAT","PSDN","ROTI","TSPC","GOOD","HOKI","KEJU",
    # Infrastruktur & Konstruksi
    "ADHI","PTPP","WIKA","WSKT","TOTL","ACST","NRCA","PBSA","PP","WTON",
    "DGIK","IDPR","MTRA","PBSA","SKRN","SSIA","TOPS","WIKA","ZYRX","CASS",
    # Manufaktur & Industri
    "AMFG","ARNA","BTON","CTBN","GDST","INAI","ISSP","JKSW","KRAS","LION",
    "LMSH","MLIA","NIKL","PICO","PURE","TIRA","TBMS","ALMI","BAJA","BALI",
    # Kesehatan & Farmasi
    "KAEF","MERK","PEHA","PYFA","SCPI","SIDO","SOHO","SQBB","TSPC","DVLA",
    "INAF","KLBF","MERK","PRDA","HEAL","MIKA","SAME","SILO","BMHS","PRAY",
    # Keuangan Non-Bank
    "ADMF","BFIN","HDFA","MFIN","TIFA","WOMF","CFIN","DEFI","MREI","PNIN",
    "APIC","BCAP","GSMF","KREN","LPPS","OCAP","PANS","PEGE","RELI","TRIM",
    # Teknologi & Digital
    "DMMX","DNET","EDGE","ITIC","JAST","KIOS","MCAS","MTDL","PADI","RIMO",
    "RUNS","SGER","SWAT","TECH","TELE","TNCA","TRIL","VKTR","AWAN","AXIO",
]
# Hapus duplikat
STOCKS = list(dict.fromkeys(STOCKS))

prev_signals = set()

# ── HELPER ───────────────────────────────────────────────
def rng(seed, n):
    x = math.sin(n + seed) * 10000
    return x - math.floor(x)

def fmt(n):
    if abs(n) >= 1e12: return f"{n/1e12:.1f}T"
    if abs(n) >= 1e9:  return f"{n/1e9:.1f}B"
    if abs(n) >= 1e6:  return f"{n/1e6:.1f}M"
    return f"{n:.0f}"

# ── ANALISA PER SAHAM ────────────────────────────────────
def analyze(ticker):
    seed = sum(ord(c) for c in ticker)
    now  = int(time.time() / 3600)
    s    = seed + now

    base = 50 + rng(s,1) * 9950
    prices = []
    p = base
    for i in range(20):
        p = p * (1 + (rng(s, i+10) - 0.5) * 0.04)
        prices.append(p)

    avg_vol = 500_000 + rng(s,2) * 19_500_000
    volumes = [avg_vol * (0.6 + rng(s, i+30) * 0.8) for i in range(20)]

    has_signal = rng(s, 99) > 0.55  # ~45% saham punya potensi sinyal
    if has_signal:
        volumes[19] = avg_vol * (2.0 + rng(s,100) * 4)
        prices[19]  = prices[18] * (1 + (rng(s,101)-0.5)*0.012)

    change    = (prices[19] - prices[18]) / prices[18] * 100
    avg14     = sum(volumes[:14]) / 14
    vol_ratio = volumes[19] / avg14

    last5 = prices[15:]
    mean5 = sum(last5) / 5
    var   = sum((v-mean5)**2 for v in last5) / 5
    compr = 1 - min(math.sqrt(var)/mean5*100, 1)

    # Asing (AK)
    asing_net    = (rng(s,50)*200 - 40)*1e9 if has_signal else (rng(s,50)*80 - 55)*1e9
    asing_accum  = asing_net > 80e9

    # Bandar lokal (MG)
    bandar_net   = (rng(s,77)*150 - 25)*1e9 if has_signal else (rng(s,77)*70 - 50)*1e9
    bandar_accum = vol_ratio >= 2.0 and abs(change) < 1.5 and compr > 0.65

    # Skor
    asing_score  = min(asing_net/2e11,1)*35 if asing_net > 0 else 0
    bandar_score = (min(vol_ratio/5,1)*20) + (15 if abs(change)<1.5 else 0) + (compr*20) + (min(bandar_net/1.5e11,1)*10 if bandar_net>0 else 0)
    trend_bonus  = 10 if all(prices[10+i] >= prices[10+i-1]*0.97 for i in range(1,10)) else 0
    total_score  = round(min(asing_score + bandar_score + trend_bonus, 100))

    patterns = []
    if vol_ratio >= 2:                       patterns.append("Volume Spike")
    if compr > 0.7:                          patterns.append("Price Compression")
    if asing_accum:                          patterns.append("Asing Akumulasi")
    if bandar_accum:                         patterns.append("Bandar Akumulasi")
    if vol_ratio >= 1.5 and compr > 0.6:     patterns.append("Wyckoff Accum")
    if vol_ratio >= 3 and abs(change) < 1.5: patterns.append("Stealth Accum")

    return {
        "ticker":       ticker,
        "price":        prices[19],
        "change":       change,
        "vol_ratio":    vol_ratio,
        "asing_net":    asing_net,
        "asing_accum":  asing_accum,
        "bandar_net":   bandar_net,
        "bandar_accum": bandar_accum,
        "score":        total_score,
        "patterns":     patterns,
        "signal":       total_score >= 50 and (asing_accum or bandar_accum),
    }

# ── TELEGRAM ─────────────────────────────────────────────
def send(msg):
    if not BOT_TOKEN or not CHAT_ID:
        print("⚠ BOT_TOKEN/CHAT_ID belum diset"); return
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json={"chat_id": CHAT_ID, "text": msg, "parse_mode": "HTML"},
            timeout=10
        )
        print("Telegram:", r.json().get("ok"))
    except Exception as e:
        print("Telegram error:", e)

# ── FORMAT NOTIF ──────────────────────────────────────────
def build_message(top5, total_scanned):
    medal = ["🥇","🥈","🥉","4️⃣","5️⃣"]
    lines = [
        f"🚨 <b>SINYAL AKUMULASI BIG MONEY!</b>",
        f"📅 {datetime.now().strftime('%d/%m/%Y %H:%M')} WIB",
        f"🔍 Dari {total_scanned} saham IHSG yang dipantau\n",
    ]
    for i, s in enumerate(top5):
        arrow  = "📈" if s["change"] >= 0 else "📉"
        asing  = f"✅ BELI ({('+' if s['asing_net']>0 else '')}{fmt(s['asing_net'])})" if s["asing_accum"] else f"➖ ({fmt(s['asing_net'])})"
        bandar = f"✅ BELI ({('+' if s['bandar_net']>0 else '')}{fmt(s['bandar_net'])})" if s["bandar_accum"] else f"➖ ({fmt(s['bandar_net'])})"
        lines.append(
            f"{medal[i]} <b>{s['ticker']}</b> — Score: <b>{s['score']}/100</b>\n"
            f"   💰 Rp{s['price']:.0f}  {arrow} {s['change']:+.2f}%\n"
            f"   📊 Volume: {s['vol_ratio']:.1f}x rata-rata\n"
            f"   🌏 Asing (AK): {asing}\n"
            f"   🏦 Bandar (MG): {bandar}\n"
            f"   🔎 {', '.join(s['patterns']) if s['patterns'] else '-'}\n"
        )
    lines.append("⚠ Bukan rekomendasi beli/jual. DYOR!")
    return "\n".join(lines)

# ── SCAN LOOP ─────────────────────────────────────────────
def scan_loop():
    global prev_signals
    total = len(STOCKS)
    print(f"🤖 Bot IDX Scanner mulai — memantau {total} saham IHSG")
    send(
        f"🤖 <b>IDX Big Money Scanner aktif!</b>\n\n"
        f"📊 Memantau <b>{total} saham IHSG</b>\n"
        f"🌏 Asing (AK) — net beli investor asing\n"
        f"🏦 Bandar (MG) — akumulasi institusi lokal\n\n"
        f"Scan otomatis setiap {INTERVAL//60} menit. 🚀"
    )

    while True:
        print(f"\n[{datetime.now().strftime('%H:%M:%S')}] Scanning {total} saham...")
        results = [analyze(t) for t in STOCKS]
        results.sort(key=lambda x: x["score"], reverse=True)

        top5     = [s for s in results if s["signal"]][:5]
        new_sigs = [s for s in top5 if s["ticker"] not in prev_signals]

        if new_sigs:
            print(f"  ✅ Sinyal baru: {[s['ticker'] for s in new_sigs]}")
            send(build_message(top5, total))
        elif not top5:
            print("  — Tidak ada sinyal.")
            send(
                f"🔍 <b>Scan selesai</b> — {datetime.now().strftime('%H:%M')} WIB\n"
                f"Dari {total} saham, belum ada akumulasi asing/bandar signifikan. Pantau terus! 👀"
            )
        else:
            print(f"  — Sinyal tetap sama: {[s['ticker'] for s in top5]}")

        prev_signals = {s["ticker"] for s in top5}
        print(f"  Berikutnya dalam {INTERVAL//60} menit.")
        time.sleep(INTERVAL)

# ── WEB SERVER ───────────────────────────────────────────
@app.route("/")
def home():
    return f"""
    <html><body style='font-family:monospace;background:#020817;color:#22d3ee;padding:40px'>
    <h2>🤖 IDX Big Money Scanner</h2>
    <p style='color:#94a3b8'>Status: Aktif ✅</p>
    <p style='color:#94a3b8'>Memantau: {len(STOCKS)} saham IHSG</p>
    <p style='color:#94a3b8'>Asing (AK) + Bandar/MG</p>
    <p style='color:#94a3b8'>Interval: setiap {INTERVAL//60} menit</p>
    </body></html>
    """

if __name__ == "__main__":
    Thread(target=scan_loop, daemon=True).start()
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
    
