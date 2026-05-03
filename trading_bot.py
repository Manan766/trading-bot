"""
AI Trading Bot — Telegram Alerts
=================================
- Morning signals (8:30-9:30 AM IST): BUY recommendations
- Monitor (9:30 AM - 3:15 PM IST): SELL alerts based on news
- Closing alert (3:15-3:35 PM IST): Position review

Setup:
  export TELEGRAM_TOKEN="your_token"
  export TELEGRAM_CHAT_ID="your_chat_id"
  export PORTFOLIO="SBIN,IDEA,PNB"   # Optional: track only these
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

# Portfolio: agar specific stocks track karna hai (comma separated)
# Example: PORTFOLIO="SBIN,IDEA,PNB"
PORTFOLIO = [s.strip().upper() for s in os.environ.get("PORTFOLIO", "").split(",") if s.strip()]

BUDGET = int(os.environ.get("BUDGET", "2000"))
MAX_PRICE = int(os.environ.get("MAX_PRICE", "5000"))
MIN_PRICE = int(os.environ.get("MIN_PRICE", "10"))

# Test mode — relaxed thresholds
TEST_MODE = os.environ.get("TEST_MODE", "true").lower() == "true"
SCORE_THRESHOLD = 4 if TEST_MODE else 5  # Strict: even test mein 4 minimum
RR_THRESHOLD = 1.3 if TEST_MODE else 1.5

# IST timezone
IST = timezone(timedelta(hours=5, minutes=30))

# Telegram message limit
TG_MAX_LEN = 4000

# ============================================
# STOCK LIST (29 NSE stocks)
# ============================================
STOCKS = {
    "SBIN":       ("SBIN.NS",       "Banking"),
    "CANBK":      ("CANBK.NS",      "Banking"),
    "BANKBARODA": ("BANKBARODA.NS", "Banking"),
    "PNB":        ("PNB.NS",        "Banking"),
    "WIPRO":      ("WIPRO.NS",      "IT"),
    "TECHM":      ("TECHM.NS",      "IT"),
    "TATAMOTORS": ("TATAMOTORS.NS", "Auto"),
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
# UTILITY FUNCTIONS
# ============================================
def get_ist_time():
    return datetime.now(IST)

def is_market_open():
    """NSE market hours: 9:15 AM - 3:30 PM IST, Mon-Fri"""
    now = get_ist_time()
    if now.weekday() >= 5:  # Sat=5, Sun=6
        return False
    minutes = now.hour * 60 + now.minute
    return 9 * 60 + 15 <= minutes <= 15 * 60 + 30

def validate_config():
    """Ensure required env vars are set"""
    missing = []
    if not TELEGRAM_TOKEN:
        missing.append("TELEGRAM_TOKEN")
    if not TELEGRAM_CHAT_ID:
        missing.append("TELEGRAM_CHAT_ID")
    if missing:
        log.error(f"Missing environment variables: {', '.join(missing)}")
        log.error("Set them via: export TELEGRAM_TOKEN='your_token'")
        sys.exit(1)

def send_telegram(message):
    """Send message to Telegram, auto-split if too long"""
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"

    # Split into chunks if needed
    chunks = []
    while len(message) > TG_MAX_LEN:
        # Split at last newline before limit
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
    """
    Returns: (sentiment, titles, pos_count, neg_count)
    Better thresholds: >=2 difference for clear signal, mild signals also caught
    """
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

        # Improved thresholds
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
    """Proper ATR using True Range (High-Low, High-PrevClose, Low-PrevClose)"""
    high = df['High'].squeeze()
    low = df['Low'].squeeze()
    close = df['Close'].squeeze()
    prev_close = close.shift(1)

    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    return tr.rolling(window=period).mean()

def get_market_sentiment():
    try:
        nifty = yf.download("^NSEI", period="5d", interval="1d", progress=False)
        if nifty.empty or len(nifty) < 2:
            return "NEUTRAL", "Market data unavailable"
        close = nifty['Close'].squeeze()
        chg = round(((float(close.iloc[-1]) - float(close.iloc[-2])) / float(close.iloc[-2])) * 100, 2)
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
    """Single stock analysis — returns dict if BUY signal, else None"""
    try:
        df = yf.download(ticker, period="3mo", interval="1d", progress=False)
        if df.empty or len(df) < 30:
            return None

        close = df['Close'].squeeze()
        price = round(float(close.iloc[-1]), 2)
        if price > MAX_PRICE or price < MIN_PRICE or price > BUDGET:
            return None

        # Indicators
        rsi_series = calculate_rsi(close)
        macd_series, signal_series = calculate_macd(close)
        bb_upper, bb_mid, bb_lower = calculate_bollinger(close)
        sma20 = close.rolling(20).mean()
        sma50 = close.rolling(50).mean()
        atr_series = calculate_atr(df)

        rsi = float(rsi_series.iloc[-1])
        macd_val = float(macd_series.iloc[-1])
        macd_sig = float(signal_series.iloc[-1])
        bb_lo = float(bb_lower.iloc[-1])
        bb_up = float(bb_upper.iloc[-1])
        sma20_val = float(sma20.iloc[-1])
        sma50_val = float(sma50.iloc[-1])
        atr = float(atr_series.iloc[-1])

        # Scoring
        score = 0
        reasons = []
        if rsi < 35:
            score += 2
            reasons.append(f"RSI oversold({round(rsi,1)})")
        elif rsi > 65:
            score -= 2
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

        # News & volume
        news_sent, news_titles, _, _ = check_news(name)
        vol_avg = float(df['Volume'].rolling(20).mean().iloc[-1])
        vol_today = float(df['Volume'].iloc[-1])
        vol_high = vol_today > vol_avg * 1.2

        if market == "BULLISH": score += 1
        elif market == "BEARISH": score -= 2
        if news_sent == "POSITIVE": score += 1
        elif news_sent == "MILD_POSITIVE": score += 0.5
        elif news_sent == "NEGATIVE": score -= 3
        elif news_sent == "MILD_NEGATIVE": score -= 1
        if vol_high: score += 1

        # Target/SL using proper ATR
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

    # Sort by price ascending (sasta pehle)
    buy_list.sort(key=lambda x: x['price'])

    mode_tag = " 🧪 [TEST]" if TEST_MODE else ""
    msg = f"<b>🤖 Trading Signals — Subah</b>{mode_tag}\n📅 {now}\n💰 Budget: Rs.{BUDGET}\n\n"
    msg += f"<b>Market:</b> {market_msg}\n\n"

    if buy_list:
        msg += f"<b>🟢 BUY karo ({len(buy_list)} stocks — sasta se mehnga):</b>\n\n"
        for i, r in enumerate(buy_list, 1):
            cost = round(r['price'] * r['shares'], 2)
            profit_est = round((r['target'] - r['price']) * r['shares'], 2)
            msg += (
                f"<b>{i}. {r['name']}</b> [{r['industry']}] — Rs.{r['price']}\n"
                f"   💵 {r['shares']} share = Rs.{cost}\n"
                f"   🎯 Target: Rs.{r['target']} (+Rs.{profit_est})\n"
                f"   🛑 SL: Rs.{r['stop_loss']} | R:R={r['rr']}x\n"
                f"   📊 Score: {r['score']} | {', '.join(r['reasons'][:2])}\n"
                f"   📰 News: {r['news']}\n\n"
            )
        msg += "<b>⚠️ Zerodha mein manually order karo!</b>\n"
        msg += "<i>Har 30 min mein monitor hoga.</i>"
    else:
        msg += "<b>🔴 Aaj koi safe BUY signal nahi.</b>\n"
        if market == "BEARISH":
            msg += "Market neeche hai — cash safe rakho! ✅"
        else:
            msg += "Koi strong setup nahi mila — wait karo."

    send_telegram(msg)
    log.info(f"Morning signals done: {len(buy_list)} buy signals")

# ============================================
# MONITOR (30-min sell alerts)
# ============================================
def get_stock_snapshot(ticker):
    """Single API call to get current price + yesterday close"""
    try:
        df = yf.download(ticker, period="5d", interval="1d", progress=False)
        if df.empty or len(df) < 2:
            return None, None
        close = df['Close'].squeeze()
        current = round(float(close.iloc[-1]), 2)
        yesterday = float(close.iloc[-2])
        chg_pct = round(((current - yesterday) / yesterday) * 100, 2)
        return current, chg_pct
    except Exception as e:
        log.warning(f"Snapshot failed for {ticker}: {e}")
        return None, None

def monitor_trades():
    """Check news + price for all stocks, send consolidated alert"""
    log.info("🔍 Running monitor...")
    now_ist = get_ist_time()
    now = now_ist.strftime("%d %b %Y, %I:%M %p IST")

    # If portfolio is set, only monitor those stocks (faster)
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
                # Mild bad news + price drop = caution
                caution_list.append(stock_data)
            elif (chg_pct or 0) < -2.5:
                # Big drop without bad news
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

    # Show portfolio if set, else all stocks
    target_stocks = STOCKS
    if PORTFOLIO:
        target_stocks = {k: v for k, v in STOCKS.items() if k in PORTFOLIO}

    snapshots = []
    for name, (ticker, industry) in target_stocks.items():
        current, chg = get_stock_snapshot(ticker)
        if current is not None:
            snapshots.append((name, current, chg or 0.0))

    # Sort by % change descending (gainers first)
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

    # Weekend check
    if now_ist.weekday() >= 5:
        log.info("Weekend — market band hai. Exit.")
        return

    hour = now_ist.hour
    minute = now_ist.minute

    # Allow manual override via CLI arg for testing
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
    # Morning: 8:30-9:30 AM
    if (hour == 8 and minute >= 30) or (hour == 9 and minute <= 30):
        morning_signals()
    # Monitor: 9:30 AM - 3:15 PM
    elif (hour == 9 and minute > 30) or (10 <= hour <= 14) or (hour == 15 and minute <= 15):
        monitor_trades()
    # Closing: 3:15 PM - 3:35 PM
    elif hour == 15 and 15 < minute <= 35:
        closing_alert()
    else:
        log.info("Market hours ke bahar — koi action nahi.")

if __name__ == "__main__":
    main()
