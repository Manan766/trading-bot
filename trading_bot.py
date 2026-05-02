import yfinance as yf
import pandas as pd
import numpy as np
import requests
import feedparser
import os
import json
import time
import schedule
from datetime import datetime
import warnings
warnings.filterwarnings('ignore')

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "8690412517:AAHSTJKxcVXMRLNFhTre-E41e2fptHW6DDU")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "1253843248")
TRADES_FILE = "active_trades.json"
BUDGET = 2000
MAX_PRICE = 1500

STOCKS = {
    "SBIN":       ("SBIN.NS",       "Banking"),
    "CANBK":      ("CANBK.NS",      "Banking"),
    "BANKBARODA": ("BANKBARODA.NS", "Banking"),
    "PNB":        ("PNB.NS",        "Banking"),
    "UNIONBANK":  ("UNIONBANK.NS",  "Banking"),
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
# HELPERS
# ============================================
def send_telegram(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    data = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"}
    try:
        r = requests.post(url, data=data, timeout=10)
        print("✅ Sent!" if r.status_code == 200 else f"❌ {r.text}")
    except Exception as e:
        print(f"Telegram error: {e}")

def is_market_open():
    now = datetime.now()
    # Monday=0, Friday=4
    if now.weekday() > 4:
        return False
    market_open = now.replace(hour=9, minute=15, second=0)
    market_close = now.replace(hour=15, minute=30, second=0)
    return market_open <= now <= market_close

def save_trades(trades):
    with open(TRADES_FILE, 'w') as f:
        json.dump(trades, f)

def load_trades():
    if os.path.exists(TRADES_FILE):
        with open(TRADES_FILE, 'r') as f:
            return json.load(f)
    return []

def save_alerted(key):
    alerted = load_alerted()
    alerted[key] = datetime.now().strftime("%Y-%m-%d")
    with open("alerted.json", 'w') as f:
        json.dump(alerted, f)

def load_alerted():
    if os.path.exists("alerted.json"):
        with open("alerted.json", 'r') as f:
            data = json.load(f)
        today = datetime.now().strftime("%Y-%m-%d")
        return {k: v for k, v in data.items() if v == today}
    return {}

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

# ============================================
# NEWS CHECK
# ============================================
def check_news(stock_name):
    positive = ['profit','gain','surge','rise','growth','strong','beat',
                'upgrade','bullish','positive','jump','rally','boost',
                'order','contract','launch','dividend','revenue']
    negative = ['loss','fall','drop','decline','weak','miss','downgrade',
                'bearish','crash','plunge','debt','fraud','lawsuit',
                'penalty','probe','investigation','resign','default']
    try:
        url = f"https://news.google.com/rss/search?q={stock_name}+NSE+India&hl=en-IN&gl=IN&ceid=IN:en"
        feed = feedparser.parse(url)
        pos, neg = 0, 0
        titles = []
        for entry in feed.entries[:5]:
            title = entry.title.lower()
            titles.append(entry.title[:70])
            for w in positive:
                if w in title: pos += 1
            for w in negative:
                if w in title: neg += 1
        if pos > neg + 1: return "POSITIVE", titles[:2]
        elif neg > pos + 1: return "NEGATIVE", titles[:2]
        return "NEUTRAL", titles[:2]
    except:
        return "NEUTRAL", []

# ============================================
# 🔴 INSTANT ALERT: HAR 15 MIN MONITOR
# ============================================
def monitor_active_trades():
    if not is_market_open():
        print(f"Market closed at {datetime.now().strftime('%H:%M')}, skipping monitor.")
        return

    trades = load_trades()
    if not trades:
        print("No active trades to monitor.")
        return

    alerted = load_alerted()
    now = datetime.now().strftime("%I:%M %p")
    print(f"🔍 Monitoring {len(trades)} trades at {now}...")

    for trade in trades:
        name = trade['name']
        ticker = trade['ticker']
        buy_price = trade['buy_price']
        target = trade['target']
        sl = trade['stop_loss']

        try:
            # Current price
            df = yf.download(ticker, period="1d", interval="5m", progress=False)
            if df.empty:
                continue
            current = round(float(df['Close'].iloc[-1]), 2)
            change_pct = round(((current - buy_price) / buy_price) * 100, 2)

            # News check
            news_sentiment, news_titles = check_news(name)

            # ---- STOP LOSS HIT ----
            sl_key = f"{name}_sl"
            if current <= sl and sl_key not in alerted:
                msg = (
                    f"🚨 <b>TURANT SELL KARO — {name}</b> 🚨\n"
                    f"⏰ {now}\n\n"
                    f"🛑 Stop-Loss Hit!\n"
                    f"   Kharida tha: Rs.{buy_price}\n"
                    f"   Current: Rs.{current}\n"
                    f"   Loss: {change_pct}%\n\n"
                    f"<b>Zerodha mein ABHI SELL karo!</b>\n"
                    f"Aur loss mat badhao!"
                )
                send_telegram(msg)
                save_alerted(sl_key)
                print(f"🛑 SL alert sent for {name}")

            # ---- TARGET HIT ----
            target_key = f"{name}_target"
            if current >= target and target_key not in alerted:
                profit = round((current - buy_price) * trade.get('shares', 1), 2)
                msg = (
                    f"🎯 <b>TARGET HIT — {name} SELL KARO!</b>\n"
                    f"⏰ {now}\n\n"
                    f"✅ Profit aa gaya!\n"
                    f"   Kharida tha: Rs.{buy_price}\n"
                    f"   Current: Rs.{current}\n"
                    f"   Profit: +{change_pct}% 🟢\n"
                    f"   Estimated: +Rs.{profit}\n\n"
                    f"<b>Zerodha mein ABHI SELL karo!</b>"
                )
                send_telegram(msg)
                save_alerted(target_key)
                print(f"🎯 Target alert sent for {name}")

            # ---- BAD NEWS ALERT ----
            news_key = f"{name}_news"
            if news_sentiment == "NEGATIVE" and news_key not in alerted:
                msg = (
                    f"📰 <b>BREAKING NEWS ALERT — {name}</b>\n"
                    f"⏰ {now}\n\n"
                    f"⚠️ Buri khabar aayi hai!\n"
                    f"   Current Price: Rs.{current} ({change_pct}%)\n"
                    f"   News: {news_titles[0] if news_titles else 'Negative news detected'}\n\n"
                    f"<b>Suggestion: SELL karo aur protect karo apna paisa!</b>\n"
                    f"Stop-loss: Rs.{sl}"
                )
                send_telegram(msg)
                save_alerted(news_key)
                print(f"📰 News alert sent for {name}")

            # ---- TRAILING STOP LOSS (profit protect) ----
            trail_key = f"{name}_trail"
            if change_pct >= 2.5 and trail_key not in alerted:
                new_sl = round(buy_price * 1.01, 2)  # 1% above buy price
                msg = (
                    f"💰 <b>Profit Protect Karo — {name}</b>\n"
                    f"⏰ {now}\n\n"
                    f"Stock +{change_pct}% upar hai!\n"
                    f"   Current: Rs.{current}\n"
                    f"   Buy: Rs.{buy_price}\n\n"
                    f"<b>Tip: Stop-loss Rs.{new_sl} pe laga do</b>\n"
                    f"Matlab: Ab chahe kuch bhi ho, profit safe hai! ✅"
                )
                send_telegram(msg)
                save_alerted(trail_key)
                print(f"💰 Trail SL alert sent for {name}")

            print(f"  {name}: Rs.{current} ({change_pct}%) | News: {news_sentiment}")

        except Exception as e:
            print(f"Monitor error {name}: {e}")

# ============================================
# 🌅 MORNING BUY SIGNALS (9 AM)
# ============================================
def morning_signals():
    if not is_market_open():
        return

    now = datetime.now().strftime("%d %b %Y, %I:%M %p")
    print(f"🌅 Morning signals at {now}")

    # Market sentiment
    try:
        nifty = yf.download("^NSEI", period="5d", interval="1d", progress=False)
        nifty_close = nifty['Close'].squeeze()
        nifty_chg = round(((float(nifty_close.iloc[-1]) - float(nifty_close.iloc[-2])) / float(nifty_close.iloc[-2])) * 100, 2)
        market = "BULLISH" if nifty_chg > 0.5 else "BEARISH" if nifty_chg < -0.5 else "NEUTRAL"
        market_msg = f"Nifty {'+' if nifty_chg > 0 else ''}{nifty_chg}%"
    except:
        market, market_msg = "NEUTRAL", "Data unavailable"

    buy_list = []

    for name, (ticker, industry) in STOCKS.items():
        try:
            df = yf.download(ticker, period="3mo", interval="1d", progress=False)
            if df.empty or len(df) < 30:
                continue

            close = df['Close'].squeeze()
            price = round(float(close.iloc[-1]), 2)

            if price > MAX_PRICE or price < 30:
                continue

            df['RSI'] = calculate_rsi(close)
            df['MACD'], df['MACD_Signal'] = calculate_macd(close)
            df['BB_Upper'], df['BB_Mid'], df['BB_Lower'] = calculate_bollinger(close)
            df['SMA20'] = close.rolling(20).mean()
            df['SMA50'] = close.rolling(50).mean()

            latest = df.iloc[-1]
            rsi = float(latest['RSI'])

            score = 0
            reasons = []

            if rsi < 35: score += 2; reasons.append(f"RSI oversold({round(rsi,1)})")
            elif rsi > 65: score -= 2
            if float(latest['MACD']) > float(latest['MACD_Signal']): score += 1; reasons.append("MACD bullish")
            else: score -= 1
            if price < float(latest['BB_Lower']): score += 2; reasons.append("Bollinger low")
            elif price > float(latest['BB_Upper']): score -= 2
            if price > float(latest['SMA20']) > float(latest['SMA50']): score += 1; reasons.append("Uptrend")
            elif price < float(latest['SMA20']) < float(latest['SMA50']): score -= 1

            news_sent, news_titles = check_news(name)
            vol_avg = float(df['Volume'].rolling(20).mean().iloc[-1])
            vol_today = float(df['Volume'].iloc[-1])
            vol_high = vol_today > vol_avg * 1.2

            if market == "BULLISH": score += 1
            elif market == "BEARISH": score -= 2
            if news_sent == "POSITIVE": score += 1
            elif news_sent == "NEGATIVE": score -= 3
            if vol_high: score += 1

            atr = float(df['Close'].diff().abs().rolling(14).mean().iloc[-1])
            target = round(price + (atr * 2), 2)
            sl = round(price - (atr * 1.5), 2)
            rr = round((target - price) / (price - sl), 2) if (price - sl) > 0 else 0

            if score >= 4 and rr >= 1.5:
                shares = min(2, max(1, int(BUDGET // price)))
                buy_list.append({
                    "name": name, "ticker": ticker, "industry": industry,
                    "price": price, "target": target, "stop_loss": sl,
                    "shares": shares, "score": score, "rr": rr,
                    "reasons": reasons, "news": news_sent,
                    "news_title": news_titles[0][:60] if news_titles else "",
                    "buy_price": price
                })
        except Exception as e:
            print(f"Error {name}: {e}")

    buy_list.sort(key=lambda x: x['score'], reverse=True)
    save_trades(buy_list[:5])  # Save top 5 for monitoring

    market_emoji = "📈" if market == "BULLISH" else "📉" if market == "BEARISH" else "➡️"
    msg = (
        f"<b>🤖 AI Trading Signals</b>\n"
        f"📅 {now}\n"
        f"💰 Budget: Rs.{BUDGET}\n\n"
        f"{market_emoji} <b>Market:</b> {market_msg}\n\n"
    )

    if buy_list:
        msg += f"<b>🟢 BUY karo ({min(len(buy_list),5)} stocks):</b>\n\n"
        for r in buy_list[:5]:
            cost = round(r['price'] * r['shares'], 2)
            profit_est = round((r['target'] - r['price']) * r['shares'], 2)
            msg += (
                f"<b>{r['name']}</b> [{r['industry']}]\n"
                f"   💵 Rs.{r['price']} | {r['shares']} share = Rs.{cost}\n"
                f"   🎯 Target: Rs.{r['target']} (+Rs.{profit_est})\n"
                f"   🛑 Stop-loss: Rs.{r['stop_loss']}\n"
                f"   📊 R:R={r['rr']}x | {', '.join(r['reasons'][:2])}\n"
                f"   📰 News: {r['news']}\n\n"
            )
        msg += "<b>⚡ Har 15 min mein monitor hoga!</b>\n"
        msg += "<b>Koi bhi alert aate hi TURANT check karo!</b>"
    else:
        msg += "<b>🔴 Aaj koi safe BUY signal nahi.</b>\n"
        msg += "Cash safe rakho aaj. ✅"

    send_telegram(msg)

# ============================================
# 🔔 CLOSING ALERT (3 PM)
# ============================================
def closing_alert():
    now = datetime.now().strftime("%d %b %Y, %I:%M %p")
    trades = load_trades()

    if not trades:
        return

    msg = f"<b>🔔 3 PM — Market Band Hone Wala Hai!</b>\n{now}\n\n"
    msg += "<b>Abhi decision lo — 3:20 PM tak!</b>\n\n"

    for trade in trades:
        try:
            df = yf.download(trade['ticker'], period="1d", interval="5m", progress=False)
            if df.empty: continue
            current = round(float(df['Close'].iloc[-1]), 2)
            change = round(((current - trade['buy_price']) / trade['buy_price']) * 100, 2)

            if change >= 1.5:
                msg += f"✅ <b>{trade['name']}</b>: Rs.{current} (+{change}%) — <b>SELL karo! Profit lo!</b>\n\n"
            elif change <= -1:
                msg += f"❌ <b>{trade['name']}</b>: Rs.{current} ({change}%) — <b>SELL karo! Loss cut karo!</b>\n\n"
            else:
                msg += f"⏳ <b>{trade['name']}</b>: Rs.{current} ({change}%) — Kal tak hold kar sakte ho\n\n"
        except:
            pass

    send_telegram(msg)
    save_trades([])  # Clear after market close

# ============================================
# SCHEDULER
# ============================================
def run_scheduler():
    print("🤖 Trading Bot Started — 24/7 Monitoring!")
    send_telegram(
        "🤖 <b>Trading Bot Start Ho Gaya!</b>\n"
        "✅ Har 15 min mein monitor karega\n"
        "✅ 9 AM — BUY signals\n"
        "✅ 3 PM — SELL alerts\n"
        "✅ Buri news pe TURANT alert\n"
        "✅ Stop-loss hit pe TURANT alert"
    )

    # Morning signals at 9:15 AM
    schedule.every().day.at("09:15").do(morning_signals)

    # Monitor every 15 minutes during market hours
    schedule.every(15).minutes.do(monitor_active_trades)

    # Closing alert at 3:00 PM
    schedule.every().day.at("15:00").do(closing_alert)

    while True:
        schedule.run_pending()
        time.sleep(60)  # Check every minute

if __name__ == "__main__":
    run_scheduler()
