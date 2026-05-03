import yfinance as yf
import pandas as pd
import numpy as np
import requests
import feedparser
import os
from datetime import datetime, timezone, timedelta
import warnings
warnings.filterwarnings('ignore')

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "8690412517:AAHSTJKxcVXMRLNFhTre-E41e2fptHW6DDU")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "1253843248")
BUDGET = 2000
MAX_PRICE = 1500

# IST = UTC + 5:30
IST = timezone(timedelta(hours=5, minutes=30))

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

def get_ist_time():
    return datetime.now(IST)

def is_weekday():
    return get_ist_time().weekday() <= 4

def send_telegram(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    # Telegram has a 4096 char limit per message
    if len(message) > 4000:
        message = message[:3950] + "\n\n... (truncated)"
    data = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"}
    try:
        r = requests.post(url, data=data, timeout=10)
        print("✅ Sent!" if r.status_code == 200 else f"❌ {r.text}")
    except Exception as e:
        print(f"Telegram error: {e}")

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
        if pos > neg + 1: return "POSITIVE", titles[:2], pos, neg
        elif neg > pos + 1: return "NEGATIVE", titles[:2], pos, neg
        return "NEUTRAL", titles[:2], pos, neg
    except:
        return "NEUTRAL", [], 0, 0

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

def get_market_sentiment():
    try:
        nifty = yf.download("^NSEI", period="5d", interval="1d", progress=False)
        close = nifty['Close'].squeeze()
        chg = round(((float(close.iloc[-1]) - float(close.iloc[-2])) / float(close.iloc[-2])) * 100, 2)
        if chg > 0.5: return "BULLISH", f"Nifty +{chg}% 📈"
        elif chg < -0.5: return "BEARISH", f"Nifty {chg}% 📉"
        return "NEUTRAL", f"Nifty {chg}% ➡️"
    except:
        return "NEUTRAL", "Market data unavailable"

# ============================================
# MORNING SIGNALS (9:15 AM)
# ============================================
def morning_signals():
    now_ist = get_ist_time()
    now = now_ist.strftime("%d %b %Y, %I:%M %p IST")
    market, market_msg = get_market_sentiment()
    buy_list = []

    for name, (ticker, industry) in STOCKS.items():
        try:
            df = yf.download(ticker, period="3mo", interval="1d", progress=False)
            if df.empty or len(df) < 30: continue
            close = df['Close'].squeeze()
            price = round(float(close.iloc[-1]), 2)
            if price > MAX_PRICE or price < 30: continue

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

            news_sent, news_titles, _, _ = check_news(name)
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
                })
        except Exception as e:
            print(f"Error {name}: {e}")

    buy_list.sort(key=lambda x: x['score'], reverse=True)

    msg = f"<b>🤖 AI Trading Signals — Subah</b>\n📅 {now}\n💰 Budget: Rs.{BUDGET}\n\n"
    msg += f"<b>Market:</b> {market_msg}\n\n"

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
        msg += "<b>⚠️ Zerodha mein manually order karo!</b>\n"
        msg += "<i>Har 30 min mein monitor hoga — alert aayega!</i>"
    else:
        msg += "<b>🔴 Aaj koi safe BUY signal nahi.</b>\n"
        if market == "BEARISH":
            msg += "Market neeche hai — cash safe rakho! ✅"
        else:
            msg += "Koi strong setup nahi mila — wait karo."

    send_telegram(msg)

# ============================================
# MONITOR (Har 30 min) — CONSOLIDATED SELL LIST
# ============================================
def monitor_trades():
    now_ist = get_ist_time()
    now = now_ist.strftime("%d %b %Y, %I:%M %p IST")

    sell_list = []      # NEGATIVE news → SELL
    caution_list = []   # Price drop > 2% even without bad news
    safe_list = []      # POSITIVE / NEUTRAL — hold

    for name, (ticker, industry) in STOCKS.items():
        try:
            news_sent, news_titles, pos, neg = check_news(name)

            # Get current intraday price
            df = yf.download(ticker, period="2d", interval="5m", progress=False)
            if df.empty:
                continue
            close = df['Close'].squeeze()
            current = round(float(close.iloc[-1]), 2)

            # Get yesterday close for % change
            df_daily = yf.download(ticker, period="2d", interval="1d", progress=False)
            if not df_daily.empty and len(df_daily) >= 2:
                yest_close = float(df_daily['Close'].squeeze().iloc[-2])
                chg_pct = round(((current - yest_close) / yest_close) * 100, 2)
            else:
                chg_pct = 0.0

            stock_data = {
                "name": name,
                "industry": industry,
                "price": current,
                "chg": chg_pct,
                "news": news_sent,
                "news_title": news_titles[0] if news_titles else "",
                "neg_count": neg,
                "pos_count": pos,
            }

            if news_sent == "NEGATIVE":
                sell_list.append(stock_data)
            elif chg_pct < -2.0:
                # Big drop without explicit bad news — caution
                caution_list.append(stock_data)
            else:
                safe_list.append(stock_data)

        except Exception as e:
            print(f"Monitor error {name}: {e}")

    # Sort sell list by most negative (highest neg count, then biggest drop)
    sell_list.sort(key=lambda x: (-x['neg_count'], x['chg']))
    caution_list.sort(key=lambda x: x['chg'])

    # Build consolidated message
    msg = f"<b>🔔 30-Min News Alert</b>\n⏰ {now}\n\n"

    if sell_list:
        msg += f"<b>🔴 SELL KARO ({len(sell_list)} stocks — Negative News):</b>\n\n"
        for s in sell_list:
            emoji_chg = "🔻" if s['chg'] < 0 else "▲"
            msg += (
                f"<b>{s['name']}</b> [{s['industry']}]\n"
                f"   💵 Rs.{s['price']} {emoji_chg} {s['chg']:+.2f}%\n"
                f"   📰 {s['news_title'][:65]}\n"
                f"   ⚠️ Negative news count: {s['neg_count']}\n\n"
            )
    else:
        msg += "<b>✅ Koi NEGATIVE news wala stock nahi.</b>\n\n"

    if caution_list:
        msg += f"<b>🟡 CAUTION ({len(caution_list)} stocks — 2%+ drop):</b>\n"
        for c in caution_list[:5]:
            msg += f"   • {c['name']}: Rs.{c['price']} ({c['chg']:+.2f}%)\n"
        msg += "\n"

    msg += f"<b>📊 Summary:</b>\n"
    msg += f"🔴 Sell signals: {len(sell_list)}\n"
    msg += f"🟡 Caution: {len(caution_list)}\n"
    msg += f"🟢 Safe/Hold: {len(safe_list)}\n\n"

    if sell_list:
        msg += "<b>⚠️ Agar in stocks ko hold kiya hai → Zerodha mein SELL karo!</b>"
    else:
        msg += "<i>Sab kuch theek hai — apni positions hold kar sakte ho.</i>"

    send_telegram(msg)
    print(f"[{now}] Sent monitor alert. Sell={len(sell_list)}, Caution={len(caution_list)}, Safe={len(safe_list)}")

# ============================================
# CLOSING ALERT (3:15 - 3:30 PM)
# ============================================
def closing_alert():
    now_ist = get_ist_time()
    now = now_ist.strftime("%d %b %Y, %I:%M %p IST")

    price_lines = []
    for name, (ticker, industry) in list(STOCKS.items())[:10]:
        try:
            df = yf.download(ticker, period="2d", interval="1d", progress=False)
            if df.empty: continue
            close = df['Close'].squeeze()
            today = round(float(close.iloc[-1]), 2)
            yesterday = round(float(close.iloc[-2]), 2)
            chg = round(((today - yesterday) / yesterday) * 100, 2)
            emoji = "🟢" if chg > 0 else "🔴"
            price_lines.append(f"{emoji} {name}: Rs.{today} ({chg:+.1f}%)")
        except:
            pass

    msg = (
        f"<b>🔔 Market Band Hone Wala Hai!</b>\n"
        f"⏰ {now}\n\n"
        f"<b>Aaj ka closing snapshot:</b>\n"
        + "\n".join(price_lines) +
        f"\n\n<b>Apni positions check karo:</b>\n"
        f"✅ Profit mein → SELL karo, profit lock karo!\n"
        f"❌ Loss mein → SELL karo, aur loss mat badhao!\n"
        f"⏳ Breakeven → Kal tak hold kar sakte ho\n\n"
        f"<i>⚠️ 3:30 PM ke baad market band!</i>"
    )
    send_telegram(msg)

# ============================================
# MAIN
# ============================================
if __name__ == "__main__":
    now_ist = get_ist_time()
    hour = now_ist.hour
    minute = now_ist.minute
    weekday = now_ist.weekday()

    print(f"IST Time: {now_ist.strftime('%A %d %b %Y, %H:%M')}")

    if weekday >= 5:
        print("Weekend — market band hai. Koi action nahi.")
        exit(0)

    if hour == 8 or (hour == 9 and minute <= 30):
        print("Running morning signals...")
        morning_signals()
    elif (hour == 9 and minute > 30) or (10 <= hour <= 14) or (hour == 15 and minute <= 15):
        print("Running monitor...")
        monitor_trades()
    elif hour == 15 and 15 < minute <= 35:
        print("Running closing alert...")
        closing_alert()
    else:
        print("Market hours ke bahar — koi action nahi.")
