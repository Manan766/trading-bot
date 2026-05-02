import yfinance as yf
import pandas as pd
import numpy as np
import requests
import feedparser
import os
from datetime import datetime
import warnings
warnings.filterwarnings('ignore')

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "8690412517:AAHSTJKxcVXMRLNFhTre-E41e2fptHW6DDU")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "1253843248")

STOCKS = {
    "RELIANCE":   "RELIANCE.NS",
    "TCS":        "TCS.NS",
    "INFY":       "INFY.NS",
    "HDFCBANK":   "HDFCBANK.NS",
    "ICICIBANK":  "ICICIBANK.NS",
    "WIPRO":      "WIPRO.NS",
    "SBIN":       "SBIN.NS",
    "TATAMOTORS": "TATAMOTORS.NS",
    "BAJFINANCE": "BAJFINANCE.NS",
    "ADANIENT":   "ADANIENT.NS",
}

# ============================================
# STEP 1: MARKET SENTIMENT CHECK
# ============================================
def check_market_sentiment():
    try:
        nifty = yf.download("^NSEI", period="5d", interval="1d", progress=False)
        if nifty.empty:
            return "NEUTRAL", "Market data unavailable"
        
        closes = nifty['Close'].squeeze()
        today = float(closes.iloc[-1])
        yesterday = float(closes.iloc[-2])
        change_pct = ((today - yesterday) / yesterday) * 100

        if change_pct > 0.5:
            return "BULLISH", f"Nifty +{round(change_pct,2)}% upar hai"
        elif change_pct < -0.5:
            return "BEARISH", f"Nifty {round(change_pct,2)}% neeche hai"
        else:
            return "NEUTRAL", f"Nifty flat hai ({round(change_pct,2)}%)"
    except:
        return "NEUTRAL", "Market data fetch nahi hua"

# ============================================
# STEP 2: NEWS SENTIMENT CHECK
# ============================================
def check_news_sentiment(stock_name):
    positive_words = [
        'profit', 'gain', 'surge', 'rise', 'growth', 'up', 'high', 'record',
        'strong', 'beat', 'upgrade', 'buy', 'bullish', 'positive', 'jump',
        'rally', 'boost', 'expand', 'win', 'success', 'revenue', 'dividend'
    ]
    negative_words = [
        'loss', 'fall', 'drop', 'decline', 'down', 'low', 'weak', 'miss',
        'downgrade', 'sell', 'bearish', 'negative', 'crash', 'plunge', 'cut',
        'debt', 'fraud', 'lawsuit', 'penalty', 'risk', 'concern', 'warning'
    ]
    
    try:
        # Google News RSS feed
        url = f"https://news.google.com/rss/search?q={stock_name}+NSE+stock&hl=en-IN&gl=IN&ceid=IN:en"
        feed = feedparser.parse(url)
        
        pos_count = 0
        neg_count = 0
        news_titles = []
        
        for entry in feed.entries[:5]:  # Check top 5 news
            title = entry.title.lower()
            news_titles.append(entry.title[:60])
            for word in positive_words:
                if word in title:
                    pos_count += 1
            for word in negative_words:
                if word in title:
                    neg_count += 1
        
        if pos_count > neg_count + 1:
            sentiment = "POSITIVE"
        elif neg_count > pos_count + 1:
            sentiment = "NEGATIVE"
        else:
            sentiment = "NEUTRAL"
            
        return sentiment, news_titles[:3]
    except:
        return "NEUTRAL", []

# ============================================
# STEP 3: TECHNICAL ANALYSIS
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

def check_volume(df):
    avg_volume = df['Volume'].rolling(20).mean().iloc[-1]
    today_volume = df['Volume'].iloc[-1]
    if float(today_volume) > float(avg_volume) * 1.2:
        return True, "Volume high hai (strong signal)"
    return False, "Volume normal hai"

def get_technical_signal(row):
    score = 0
    reasons = []

    # RSI
    if row['RSI'] < 35:
        score += 2
        reasons.append(f"RSI oversold ({round(float(row['RSI']),1)})")
    elif row['RSI'] > 65:
        score -= 2
        reasons.append(f"RSI overbought ({round(float(row['RSI']),1)})")

    # MACD
    if row['MACD'] > row['MACD_Signal']:
        score += 1
        reasons.append("MACD bullish")
    else:
        score -= 1
        reasons.append("MACD bearish")

    # Bollinger
    if row['Close'] < row['BB_Lower']:
        score += 2
        reasons.append("Price below Bollinger")
    elif row['Close'] > row['BB_Upper']:
        score -= 2
        reasons.append("Price above Bollinger")

    # Trend
    if row['Close'] > row['SMA20'] > row['SMA50']:
        score += 1
        reasons.append("Strong uptrend")
    elif row['Close'] < row['SMA20'] < row['SMA50']:
        score -= 1
        reasons.append("Downtrend")

    return score, reasons

# ============================================
# STEP 4: COMBINED SMART SIGNAL
# ============================================
def get_smart_signal(tech_score, market_sentiment, news_sentiment, volume_high):
    final_score = tech_score

    # Market boost/penalty
    if market_sentiment == "BULLISH":
        final_score += 1
    elif market_sentiment == "BEARISH":
        final_score -= 2  # Strong penalty in bad market

    # News boost/penalty
    if news_sentiment == "POSITIVE":
        final_score += 1
    elif news_sentiment == "NEGATIVE":
        final_score -= 2  # Strong penalty for bad news

    # Volume confirmation
    if volume_high:
        final_score += 1

    # Only give BUY if everything aligns
    if final_score >= 4:
        return "BUY", final_score
    elif final_score <= -4:
        return "SELL", final_score
    else:
        return "HOLD", final_score

# ============================================
# MAIN: ANALYZE + NOTIFY
# ============================================
def analyze_and_notify(budget=2000):
    now = datetime.now().strftime("%d %b %Y, %I:%M %p")
    print(f"Starting smart analysis at {now}...")

    # Step 1: Market check
    market_sentiment, market_msg = check_market_sentiment()
    print(f"Market: {market_sentiment} — {market_msg}")

    buy_msgs = []
    skip_msgs = []

    for name, ticker in STOCKS.items():
        try:
            df = yf.download(ticker, period="3mo", interval="1d", progress=False)
            if df.empty or len(df) < 30:
                continue

            close = df['Close'].squeeze()
            df['RSI'] = calculate_rsi(close)
            df['MACD'], df['MACD_Signal'] = calculate_macd(close)
            df['BB_Upper'], df['BB_Mid'], df['BB_Lower'] = calculate_bollinger(close)
            df['SMA20'] = close.rolling(20).mean()
            df['SMA50'] = close.rolling(50).mean()

            latest = df.iloc[-1]
            price = round(float(latest['Close']), 2)
            tech_score, tech_reasons = get_technical_signal(latest)

            # Step 2: News check
            news_sentiment, news_titles = check_news_sentiment(name)

            # Step 3: Volume check
            volume_high, volume_msg = check_volume(df)

            # Step 4: Smart combined signal
            final_signal, final_score = get_smart_signal(
                tech_score, market_sentiment, news_sentiment, volume_high
            )

            atr = float(df['Close'].diff().abs().rolling(14).mean().iloc[-1])
            target = round(price + (atr * 2), 2)
            sl = round(price - (atr * 1.5), 2)
            shares = min(2, max(1, int(budget // price)))
            risk_reward = round((target - price) / (price - sl), 2) if (price - sl) > 0 else 0

            if final_signal == "BUY" and risk_reward >= 1.5:
                cost = round(price * shares, 2)
                profit = round((target - price) * shares, 2)
                news_line = f"\n   News: {news_titles[0][:50]}..." if news_titles else ""
                buy_msgs.append({
                    "score": final_score,
                    "text": (
                        f"<b>{name}</b>\n"
                        f"   Kharido: {shares} share @ Rs.{price}\n"
                        f"   Lagat: Rs.{cost}\n"
                        f"   Target: Rs.{target} (+Rs.{profit})\n"
                        f"   Stop-loss: Rs.{sl}\n"
                        f"   R:R Ratio: {risk_reward}x\n"
                        f"   Technical: {', '.join(tech_reasons[:2])}\n"
                        f"   News: {news_sentiment}{news_line}\n"
                        f"   Volume: {volume_msg}"
                    )
                })
            else:
                reason = []
                if news_sentiment == "NEGATIVE":
                    reason.append("bad news")
                if market_sentiment == "BEARISH":
                    reason.append("market down")
                if risk_reward < 1.5:
                    reason.append("risk zyada")
                if reason:
                    skip_msgs.append(f"{name} — Skip ({', '.join(reason)})")

        except Exception as e:
            print(f"Error {name}: {e}")
            continue

    # Sort by score
    buy_msgs.sort(key=lambda x: x['score'], reverse=True)

    # Build message
    market_emoji = "📈" if market_sentiment == "BULLISH" else "📉" if market_sentiment == "BEARISH" else "➡️"
    msg = (
        f"<b>AI Smart Trading Signals</b>\n"
        f"{now}\n"
        f"Budget: Rs.{budget}\n\n"
        f"{market_emoji} <b>Market:</b> {market_msg}\n\n"
    )

    if buy_msgs:
        msg += f"<b>BUY karo aaj ({len(buy_msgs)} stock):</b>\n\n"
        msg += "\n\n".join([m['text'] for m in buy_msgs])
    else:
        msg += "<b>Aaj koi safe BUY signal nahi.</b>\n"
        msg += "Reasons: Market ya news theek nahi, ya risk zyada hai.\n"

    if skip_msgs:
        msg += f"\n\n<b>Skip kiye ({len(skip_msgs)}):</b>\n"
        msg += "\n".join(skip_msgs)

    msg += "\n\n<i>Analysis: Technical + News + Market + Volume</i>"
    msg += "\n<i>⚠️ Risk apna hai. Stop-loss zaroor follow karo.</i>"

    print(msg)
    send_telegram(msg)

def send_telegram(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    data = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"}
    try:
        r = requests.post(url, data=data)
        print("Telegram message sent!" if r.status_code == 200 else f"Failed: {r.text}")
    except Exception as e:
        print(f"Telegram error: {e}")

if __name__ == "__main__":
    MY_BUDGET = 2000
    analyze_and_notify(budget=MY_BUDGET)
