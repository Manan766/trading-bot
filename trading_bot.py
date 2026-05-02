import yfinance as yf
import pandas as pd
import numpy as np
import requests
from datetime import datetime
import warnings
warnings.filterwarnings('ignore')

TELEGRAM_TOKEN = "8690412517:AAHSTJKxcVXMRLNFhTre-E41e2fptHW6DDU"
TELEGRAM_CHAT_ID = "1253843248"

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

def send_telegram(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    data = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"}
    try:
        requests.post(url, data=data)
        print("Telegram message sent!")
    except Exception as e:
        print(f"Telegram error: {e}")

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

def get_signal(row):
    score = 0
    reasons = []
    if row['RSI'] < 35:
        score += 2
        reasons.append("RSI oversold")
    elif row['RSI'] > 65:
        score -= 2
        reasons.append("RSI overbought")
    if row['MACD'] > row['MACD_Signal']:
        score += 1
        reasons.append("MACD bullish")
    else:
        score -= 1
        reasons.append("MACD bearish")
    if row['Close'] < row['BB_Lower']:
        score += 2
        reasons.append("Below Bollinger band")
    elif row['Close'] > row['BB_Upper']:
        score -= 2
        reasons.append("Above Bollinger band")
    if row['Close'] > row['SMA20'] > row['SMA50']:
        score += 1
        reasons.append("Strong uptrend")
    elif row['Close'] < row['SMA20'] < row['SMA50']:
        score -= 1
        reasons.append("Downtrend")
    if score >= 3:
        signal = "BUY"
    elif score <= -3:
        signal = "SELL"
    else:
        signal = "HOLD"
    return signal, score, reasons

def analyze_and_notify(budget=2000):
    now = datetime.now().strftime("%d %b %Y, %I:%M %p")
    print(f"\nRunning analysis at {now}...")

    buy_msgs = []
    sell_msgs = []
    hold_msgs = []

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
            rsi = round(float(latest['RSI']), 1)
            signal, score, reasons = get_signal(latest)
            atr = float(df['Close'].diff().abs().rolling(14).mean().iloc[-1])
            target = round(price + (atr * 2), 2)
            sl = round(price - (atr * 1.5), 2)
            shares = min(2, max(1, int(budget // price)))

            if signal == "BUY":
                cost = round(price * shares, 2)
                profit = round((target - price) * shares, 2)
                buy_msgs.append(
                    f"<b>{name}</b>\n"
                    f"   Kharido: {shares} share @ Rs.{price}\n"
                    f"   Lagat: Rs.{cost}\n"
                    f"   Target: Rs.{target} (+Rs.{profit})\n"
                    f"   Stop-loss: Rs.{sl}\n"
                    f"   RSI: {rsi} | {', '.join(reasons[:2])}"
                )
            elif signal == "SELL":
                sell_msgs.append(f"<b>{name}</b> @ Rs.{price} — {reasons[0]}")
            else:
                hold_msgs.append(f"{name} @ Rs.{price} (RSI: {rsi})")
        except Exception as e:
            print(f"Error {name}: {e}")
            continue

    msg = f"<b>AI Trading Signals</b>\n{now}\nBudget: Rs.{budget}\n\n"

    if buy_msgs:
        msg += "BUY karo aaj:\n\n"
        msg += "\n\n".join(buy_msgs)
    else:
        msg += "Aaj koi strong BUY signal nahi.\n"

    if sell_msgs:
        msg += "\n\nAvoid / SELL:\n" + "\n".join(sell_msgs)

    if hold_msgs:
        msg += "\n\nWait karo:\n" + ", ".join(hold_msgs)

    if not buy_msgs:
        msg += "\n\nTip: Aaj cash hold karo. Kal phir check hoga."
    else:
        msg += "\n\n⚠️ Sirf educational analysis hai. Risk apna hai."

    print(msg)
    send_telegram(msg)

if __name__ == "__main__":
    MY_BUDGET = 2000
    analyze_and_notify(budget=MY_BUDGET)
