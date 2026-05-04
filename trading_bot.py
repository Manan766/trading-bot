"""
AI Trading Bot — Telegram Alerts (FIXED)
=========================================
Fixes applied:
1. yfinance MultiIndex columns issue (auto_adjust=True, multi_level_index=False)
2. TATAMOTORS.NS replaced with TMPV.NS (post Oct-2025 demerger)
3. Safer float() conversions everywhere using helper
4. Volume calculation fix
"""

import yfinance as yf
import pandas as pd
import numpy as np
import requests
import feedparser
import os
import sys
import logging
from datetime import datetime, timezone, timedelta
import warnings
warnings.filterwarnings('ignore')

# ============================================
# LOGGING SETUP
# ============================================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)s | %(message)s',
    datefmt='%H:%M:%S'
)
log = logging.getLogger(__name__)

# ============================================
# CONFIG
# ============================================
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()

PORTFOLIO = [s.strip().upper() for s in os.environ.get("PORTFOLIO", "").split(",") if s.strip()]

BUDGET = int(os.environ.get("BUDGET", "2000"))
MAX_PRICE = int(os.environ.get("MAX_PRICE", "5000"))
MIN_PRICE = int(os.environ.get("MIN_PRICE", "10"))

TEST_MODE = os.environ.get("TEST_MODE", "true").lower() == "true"
SCORE_THRESHOLD = 4 if TEST_MODE else 5
RR_THRESHOLD = 1.3 if TEST_MODE else 1.5

IST = timezone(timedelta(hours=5, minutes=30))
TG_MAX_LEN = 4000

# ============================================
# STOCK LIST — TATAMOTORS replaced with TMPV (post Oct 2025 demerger)
# ============================================
STOCKS = {
    "SBIN":       ("SBIN.NS",       "Banking"),
    "CANBK":      ("CANBK.NS",      "Banking"),
    "BANKBARODA": ("BANKBARODA.NS", "Banking"),
    "PNB":        ("PNB.NS",        "Banking"),
    "WIPRO":      ("WIPRO.NS",      "IT"),
    "TECHM":      ("TECHM.NS",      "IT"),
    "TMPV":       ("TMPV.NS",       "Auto"),    # was TATAMOTORS — renamed Oct 2025
    "ASHOKLEY":   ("ASHOKLEY.NS",   "Auto"),
    "SUNPHARMA":  ("SUNPHARMA.NS",  "Pharma"),
    "CIPLA":      ("CIPLA.NS",      "Pharma"),
    "ONGC":       ("ONGC.NS",       "Energy"),
    "IOC":        ("IOC.NS",        "Energy"),
    "BPCL":       ("BPCL.NS",       "Energy"),
    "COALINDIA":  ("COALINDIA.NS",  "Energy"),
    "NTPC":       ("NTPC.NS",       "Energy"),
    "POWERGRID":  ("POWERGRID.NS",  "Energy"),
    "ITC":        ("ITC.NS",        "FMCG"),
    "DABUR":      ("DABUR.NS",      "FMCG"),
    "MARICO":     ("MARICO.NS",     "FMCG"),
    "TATASTEEL":  ("TATASTEEL.NS",  "Metals"),
    "SAIL":       ("SAIL.NS",       "Metals"),
    "HINDALCO":   ("HINDALCO.NS",   "Metals"),
    "NMDC":       ("NMDC.NS",       "Metals"),
    "NBCC":       ("NBCC.NS",       "Infra"),
    "IRB":        ("IRB.NS",        "Infra"),
    "IDEA":       ("IDEA.NS",       "Telecom"),
    "IRFC":       ("IRFC.NS",       "Finance"),
    "RECLTD":     ("RECLTD.NS",     "Finance"),
    "PFC":        ("PFC.NS",        "Finance"),
}

# ============================================
# HELPERS
# ============================================
def get_ist_time():
    return datetime.now(IST)

def is_market_open():
    now = get_ist_time()
    if now.weekday() >= 5:
        return False
    minutes = now.hour * 60 + now.minute
    return 9 * 60 + 15 <= minutes <= 15 * 60 + 30

def safe_float(x):
    """Convert anything (Series, scalar, numpy) to float safely."""
    try:
        if hasattr(x, 'iloc'):
            x = x.iloc[-1] if len(x) > 0 else float('nan')
        if hasattr(x, 'item'):
            return float(x.item())
        return float(x)
    except Exception:
        return float('nan')

def download_history(ticker, period="3mo", interval="1d"):
    """Wrapper around yf.download that ALWAYS returns flat (non-MultiIndex) columns."""
    df = yf.download(
        ticker,
        period=period,
        interval=interval,
        progress=False,
        auto_adjust=True,
        multi_level_index=False,  # KEY FIX: prevents MultiIndex columns
    )
    # Defensive: flatten MultiIndex if it still exists (older yfinance)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    return df

def validate_config():
    missing = []
    if not TELEGRAM_TOKEN:
        missing.append("TELEGRAM_TOKEN")
    if not TELEGRAM_CHAT_ID:
        missing.append("TELEGRAM_CHAT_ID")
    if missing:
        log.error(f"Missing environment variables: {', '.join(missing)}")
        sys.exit(1)

def send_telegram(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    chunks = []
    while len(message) > TG_MAX_LEN:
        split_at = message.rfind('\n', 0, TG_MAX_LEN)
        if split_at == -1:
            split_at = TG_MAX_LEN
        chunks.append(message[:split_at])
        message = message[split_at:].lstrip()
    chunks.append(message)

    for i, chunk in enumerate(chunks):
        data = {"chat_id": TELEGRAM_CHAT_ID, "text": chunk, "parse_mode": "HTML"}
        try:
            r = requests.post(url, data=data, timeout=15)
            if r.status_code == 200:
                log.info(f"✅ Telegram sent (chunk {i+1}/{len(chunks)})")
            else:
                log.error(f"❌ Telegram failed: {r.status_code} {r.text}")
        except requests.RequestException as e:
            log.error(f"Telegram network error: {e}")

# ============================================
# NEWS ANALYSIS
# ============================================
POSITIVE_WORDS = {
    'profit', 'gain', 'surge', 'rise', 'growth', 'strong', 'beat',
    'upgrade', 'bullish', 'positive', 'jump', 'rally', 'boost',
    'order', 'contract', 'launch', 'dividend', 'revenue', 'high',
    'record', 'expansion', 'acquisition', 'partnership'
}
NEGATIVE_WORDS = {
    'loss', 'fall', 'drop', 'decline', 'weak', 'miss', 'downgrade',
    'bearish', 'crash', 'plunge', 'debt', 'fraud', 'lawsuit',
    'penalty', 'probe', 'investigation', 'resign', 'default',
    'cut', 'slump', 'bankruptcy', 'scam', 'raid'
}

def check_news(stock_name):
    try:
        url = f"https://news.google.com/rss/search?q={stock_name}+NSE+India&hl=en-IN&gl=IN&ceid=IN:en"
        feed = feedparser.parse(url)
        pos, neg = 0, 0
        titles = []
        for entry in feed.entries[:5]:
            title = entry.title.lower()
            titles.append(entry.title[:70])
            words = set(title.split())
            pos += len(words & POSITIVE_WORDS)
            neg += len(words & NEGATIVE_WORDS)

        if neg >= 2 and neg > pos:
            return "NEGATIVE", titles[:2], pos, neg
        elif pos >= 2 and pos > neg:
            return "POSITIVE", titles[:2], pos, neg
        elif pos > neg:
            return "MILD_POSITIVE", titles[:2], pos, neg
        elif neg > pos:
            return "MILD_NEGATIVE", titles[:2], pos, neg
        return "NEUTRAL", titles[:2], pos, neg
    except Exception as e:
        log.warning(f"News fetch failed for {stock_name}: {e}")
        return "NEUTRAL", [], 0, 0

# ============================================
# TECHNICAL INDICATORS
# ============================================
def calculate_rsi(prices, period=14):
    delta = prices.diff()
    gain = delta.where(delta > 0, 0).rolling(window=period).mean()
    loss = -delta.where(delta < 0, 0).rolling(window=period).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))

def calculate_macd(prices):
    ema12 = prices.ewm(span=12, adjust=False).mean()
    ema26 = prices.ewm(span=26, adjust=False).mean()
    macd = ema12 - ema26
    signal = macd.ewm(span=9, adjust=False).mean()
    return macd, signal

def calculate_bollinger(prices, period=20):
    sma = prices.rolling(window=period).mean()
    std = prices.rolling(window=period).std()
    return sma + (2 * std), sma, sma - (2 * std)

def calculate_atr(df, period=14):
    high = df['High']
    low = df['Low']
    close = df['Close']
    prev_close = close.shift(1)
    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    return tr.rolling(window=period).mean()

def get_market_sentiment():
    try:
        nifty = download_history("^NSEI", period="5d", interval="1d")
        if nifty.empty or len(nifty) < 2:
            return "NEUTRAL", "Market data unavailable"
        close = nifty['Close']
        last = safe_float(close.iloc[-1])
        prev = safe_float(close.iloc[-2])
        if prev == 0 or np.isnan(prev) or np.isnan(last):
            return "NEUTRAL", "Market data unavailable"
        chg = round(((last - prev) / prev) * 100, 2)
        if chg > 0.5:
            return "BULLISH", f"Nifty +{chg}% 📈"
        elif chg < -0.5:
            return "BEARISH", f"Nifty {chg}% 📉"
        return "NEUTRAL", f"Nifty {chg}% ➡️"
    except Exception as e:
        log.warning(f"Market sentiment fetch failed: {e}")
        return "NEUTRAL", "Market data unavailable"

# ============================================
# MORNING SIGNALS
# ============================================
def analyze_stock(name, ticker, industry, market):
    try:
        df = download_history(ticker, period="3mo", interval="1d")
        if df.empty or len(df) < 30:
            log.info(f"  ⏭️  {name} skipped — insufficient data")
            return None

        close = df['Close']
        price = round(safe_float(close.iloc[-1]), 2)
        if np.isnan(price) or price > MAX_PRICE or price < MIN_PRICE or price > BUDGET:
            return None

        # Indicators
        rsi_series = calculate_rsi(close)
        macd_series, signal_series = calculate_macd(close)
        bb_upper, bb_mid, bb_lower = calculate_bollinger(close)
        sma20 = close.rolling(20).mean()
        sma50 = close.rolling(50).mean()
        atr_series = calculate_atr(df)

        rsi = safe_float(rsi_series.iloc[-1])
        macd_val = safe_float(macd_series.iloc[-1])
        macd_sig = safe_float(signal_series.iloc[-1])
        bb_lo = safe_float(bb_lower.iloc[-1])
        bb_up = safe_float(bb_upper.iloc[-1])
        sma20_val = safe_float(sma20.iloc[-1])
        sma50_val = safe_float(sma50.iloc[-1])
        atr = safe_float(atr_series.iloc[-1])

        if any(np.isnan(v) for v in [rsi, macd_val, macd_sig, bb_lo, bb_up, sma20_val, sma50_val, atr]):
            log.info(f"  ⏭️  {name} skipped — NaN in indicators")
            return None

        # NEWS-FIRST FILTER
        news_sent, news_titles, pos_count, neg_count = check_news(name)
        if news_sent not in ("POSITIVE", "MILD_POSITIVE"):
            log.info(f"  ⏭️  {name} skipped — news: {news_sent}")
            return None

        # Volume
        volume = df['Volume']
        vol_avg = safe_float(volume.rolling(20).mean().iloc[-1])
        vol_today = safe_float(volume.iloc[-1])
        vol_high = (not np.isnan(vol_avg)) and (not np.isnan(vol_today)) and vol_today > vol_avg * 1.2

        # SCORING
        score = 0
        reasons = []

        if news_sent == "POSITIVE":
            score += 3
            reasons.append(f"📰 Strong positive news (pos:{pos_count})")
        elif news_sent == "MILD_POSITIVE":
            score += 1.5
            reasons.append(f"📰 Mild positive news")

        if rsi < 35:
            score += 2
            reasons.append(f"RSI oversold({round(rsi,1)})")
        elif rsi > 70:
            log.info(f"  ⏭️  {name} skipped — RSI overbought ({round(rsi,1)})")
            return None
        elif rsi > 65:
            score -= 1

        if macd_val > macd_sig:
            score += 1
            reasons.append("MACD bullish")
        else:
            score -= 1

        if price < bb_lo:
            score += 2
            reasons.append("Bollinger low")
        elif price > bb_up:
            score -= 2

        if price > sma20_val > sma50_val:
            score += 1
            reasons.append("Uptrend")
        elif price < sma20_val < sma50_val:
            score -= 1

        if market == "BULLISH":
            score += 1
        elif market == "BEARISH":
            score -= 2

        if vol_high:
            score += 1
            reasons.append("High volume")

        target = round(price + (atr * 2), 2)
        sl = round(price - (atr * 1.5), 2)
        rr = round((target - price) / (price - sl), 2) if (price - sl) > 0 else 0

        if score >= SCORE_THRESHOLD and rr >= RR_THRESHOLD:
            shares = int(BUDGET // price)
            if shares < 1:
                return None
            return {
                "name": name, "ticker": ticker, "industry": industry,
                "price": price, "target": target, "stop_loss": sl,
                "shares": shares, "score": score, "rr": rr, "atr": round(atr, 2),
                "reasons": reasons, "news": news_sent,
                "news_title": news_titles[0][:60] if news_titles else "",
            }
        return None
    except Exception as e:
        log.error(f"Analysis failed for {name}: {e}")
        return None

def morning_signals():
    log.info("🌅 Running morning signals...")
    now_ist = get_ist_time()
    now = now_ist.strftime("%d %b %Y, %I:%M %p IST")
    market, market_msg = get_market_sentiment()
    log.info(f"Market: {market} | {market_msg}")

    buy_list = []
    for name, (ticker, industry) in STOCKS.items():
        result = analyze_stock(name, ticker, industry, market)
        if result:
            buy_list.append(result)

    buy_list.sort(key=lambda x: x['price'])

    mode_tag = " 🧪 [TEST]" if TEST_MODE else ""
    msg = f"<b>🤖 Trading Signals — Subah</b>{mode_tag}\n📅 {now}\n💰 Budget: Rs.{BUDGET}\n\n"
    msg += f"<b>Market:</b> {market_msg}\n\n"

    if buy_list:
        msg += f"<b>🟢 BUY karo ({len(buy_list)} stocks — sasta se mehnga):</b>\n\n"
        for i, r in enumerate(buy_list, 1):
            cost = round(r['price'] * r['shares'], 2)
            profit_est = round((r['target'] - r['price']) * r['shares'], 2)
            news_emoji = "🟢" if r['news'] == "POSITIVE" else "🟡"
            msg += (
                f"<b>{i}. {r['name']}</b> [{r['industry']}] — Rs.{r['price']}\n"
                f"   {news_emoji} <b>News:</b> {r['news']}\n"
                f"   📰 <i>{r['news_title']}</i>\n"
                f"   💵 {r['shares']} share = Rs.{cost}\n"
                f"   🎯 Target: Rs.{r['target']} (+Rs.{profit_est})\n"
                f"   🛑 SL: Rs.{r['stop_loss']} | R:R={r['rr']}x\n"
                f"   📊 Score: {r['score']} | {', '.join(r['reasons'][:2])}\n\n"
            )
        msg += "<b>⚠️ Zerodha mein manually order karo!</b>\n"
        msg += "<i>Har 30 min mein news monitor hoga.</i>"
    else:
        msg += "<b>🔴 Aaj koi safe BUY signal nahi.</b>\n\n"
        msg += "<i>Reason: Kisi bhi stock mein POSITIVE news + accha technical setup nahi mila.</i>\n\n"
        if market == "BEARISH":
            msg += "Market bhi neeche hai — cash safe rakho! ✅"
        else:
            msg += "Wait karo, kal phir check karenge."

    send_telegram(msg)
    log.info(f"Morning signals done: {len(buy_list)} buy signals")

# ============================================
# MONITOR
# ============================================
def get_stock_snapshot(ticker):
    try:
        df = download_history(ticker, period="5d", interval="1d")
        if df.empty or len(df) < 2:
            return None, None
        close = df['Close']
        current = round(safe_float(close.iloc[-1]), 2)
        yesterday = safe_float(close.iloc[-2])
        if np.isnan(current) or np.isnan(yesterday) or yesterday == 0:
            return None, None
        chg_pct = round(((current - yesterday) / yesterday) * 100, 2)
        return current, chg_pct
    except Exception as e:
        log.warning(f"Snapshot failed for {ticker}: {e}")
        return None, None

def monitor_trades():
    log.info("🔍 Running monitor...")
    now_ist = get_ist_time()
    now = now_ist.strftime("%d %b %Y, %I:%M %p IST")

    target_stocks = STOCKS
    if PORTFOLIO:
        target_stocks = {k: v for k, v in STOCKS.items() if k in PORTFOLIO}
        log.info(f"Portfolio mode: {list(target_stocks.keys())}")

    sell_list = []
    caution_list = []
    safe_count = 0

    for name, (ticker, industry) in target_stocks.items():
        try:
            news_sent, news_titles, pos, neg = check_news(name)
            current, chg_pct = get_stock_snapshot(ticker)
            if current is None:
                continue

            stock_data = {
                "name": name, "industry": industry,
                "price": current, "chg": chg_pct or 0.0,
                "news": news_sent,
                "news_title": news_titles[0] if news_titles else "",
                "neg_count": neg, "pos_count": pos,
            }

            if news_sent == "NEGATIVE":
                sell_list.append(stock_data)
            elif news_sent == "MILD_NEGATIVE" and (chg_pct or 0) < -1.5:
                caution_list.append(stock_data)
            elif (chg_pct or 0) < -2.5:
                caution_list.append(stock_data)
            else:
                safe_count += 1
        except Exception as e:
            log.error(f"Monitor error {name}: {e}")

    sell_list.sort(key=lambda x: (-x['neg_count'], x['chg']))
    caution_list.sort(key=lambda x: x['chg'])

    portfolio_tag = f" — {','.join(PORTFOLIO)}" if PORTFOLIO else ""
    msg = f"<b>🔔 30-Min News Alert{portfolio_tag}</b>\n⏰ {now}\n\n"

    if sell_list:
        msg += f"<b>🔴 SELL ({len(sell_list)} — Negative News):</b>\n\n"
        for s in sell_list:
            arrow = "🔻" if s['chg'] < 0 else "▲"
            msg += (
                f"<b>{s['name']}</b> [{s['industry']}]\n"
                f"   💵 Rs.{s['price']} {arrow} {s['chg']:+.2f}%\n"
                f"   📰 {s['news_title'][:65]}\n"
                f"   ⚠️ Neg news count: {s['neg_count']}\n\n"
            )
    else:
        msg += "<b>✅ Koi negative news wala stock nahi.</b>\n\n"

    if caution_list:
        msg += f"<b>🟡 CAUTION ({len(caution_list)}):</b>\n"
        for c in caution_list[:5]:
            msg += f"   • {c['name']}: Rs.{c['price']} ({c['chg']:+.2f}%) — {c['news']}\n"
        msg += "\n"

    msg += f"<b>📊 Summary:</b>\n"
    msg += f"🔴 Sell: {len(sell_list)} | 🟡 Caution: {len(caution_list)} | 🟢 Safe: {safe_count}\n\n"

    if sell_list:
        msg += "<b>⚠️ In stocks ko hold kiya hai → SELL karo!</b>"
    else:
        msg += "<i>Sab theek hai — positions hold kar sakte ho.</i>"

    send_telegram(msg)
    log.info(f"Monitor done: Sell={len(sell_list)} Caution={len(caution_list)} Safe={safe_count}")

# ============================================
# CLOSING ALERT
# ============================================
def closing_alert():
    log.info("🔔 Running closing alert...")
    now_ist = get_ist_time()
    now = now_ist.strftime("%d %b %Y, %I:%M %p IST")

    target_stocks = STOCKS
    if PORTFOLIO:
        target_stocks = {k: v for k, v in STOCKS.items() if k in PORTFOLIO}

    snapshots = []
    for name, (ticker, industry) in target_stocks.items():
        current, chg = get_stock_snapshot(ticker)
        if current is not None:
            snapshots.append((name, current, chg or 0.0))

    snapshots.sort(key=lambda x: -x[2])

    price_lines = []
    for name, price, chg in snapshots:
        emoji = "🟢" if chg > 0 else ("🔴" if chg < 0 else "⚪")
        price_lines.append(f"{emoji} {name}: Rs.{price} ({chg:+.1f}%)")

    portfolio_tag = " (Portfolio)" if PORTFOLIO else ""
    msg = (
        f"<b>🔔 Market Band Hone Wala Hai!{portfolio_tag}</b>\n"
        f"⏰ {now}\n\n"
        f"<b>Closing snapshot:</b>\n"
        + "\n".join(price_lines) +
        f"\n\n<b>Apni positions check karo:</b>\n"
        f"✅ Profit mein → SELL karo, lock karo!\n"
        f"❌ Loss mein → SELL karo, loss mat badhao!\n"
        f"⏳ Breakeven → Kal tak hold OK\n\n"
        f"<i>⚠️ 3:30 PM ke baad market band!</i>"
    )
    send_telegram(msg)
    log.info("Closing alert sent")

# ============================================
# MAIN
# ============================================
def main():
    validate_config()
    now_ist = get_ist_time()
    log.info(f"Started: {now_ist.strftime('%A %d %b %Y, %H:%M IST')}")
    log.info(f"Mode: {'TEST' if TEST_MODE else 'PRODUCTION'} | Budget: Rs.{BUDGET}")
    if PORTFOLIO:
        log.info(f"Portfolio filter: {PORTFOLIO}")

    if now_ist.weekday() >= 5:
        log.info("Weekend — market band hai. Exit.")
        return

    hour = now_ist.hour
    minute = now_ist.minute

    if len(sys.argv) > 1:
        cmd = sys.argv[1].lower()
        if cmd == "morning":
            morning_signals()
        elif cmd == "monitor":
            monitor_trades()
        elif cmd == "closing":
            closing_alert()
        elif cmd == "test":
            log.info("Sending test message...")
            send_telegram(f"<b>🧪 Test message</b>\n⏰ {now_ist.strftime('%I:%M %p IST')}\nBot is working ✅")
        else:
            log.error(f"Unknown command: {cmd}. Use: morning|monitor|closing|test")
        return

    # Auto-schedule based on time
    if (hour == 8 and minute >= 30) or (hour == 9 and minute <= 30):
        morning_signals()
    elif (hour == 9 and minute > 30) or (10 <= hour <= 14) or (hour == 15 and minute <= 15):
        monitor_trades()
    elif hour == 15 and 15 < minute <= 35:
        closing_alert()
    else:
        log.info("Market hours ke bahar — koi action nahi.")

if __name__ == "__main__":
    main()
