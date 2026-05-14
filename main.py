import os
import logging
import requests
import time
import threading
import datetime
import json
import asyncio
import websockets
from flask import Flask
from binance.client import Client

# --- 1. SETUP & KEEP-ALIVE SERVER ---
app = Flask('')

@app.route('/')
def home():
    return "Bot is awake and monitoring!"

def run_flask():
    log = logging.getLogger('werkzeug')
    log.setLevel(logging.ERROR)
    app.run(host='0.0.0.0', port=8080, use_reloader=False)

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
CHAT_ID = os.environ.get("CHAT_ID")
START_TIME = time.time()

def send_telegram_alert(message):
    if not TELEGRAM_TOKEN or not CHAT_ID:
        print("Telegram credentials missing!")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        requests.post(url, data={"chat_id": CHAT_ID, "text": message, "parse_mode": "Markdown"})
    except Exception as e:
        print(f"Telegram error: {e}")

# --- 2. STATUS LISTENER WITH AUTO-RESTART ---
def telegram_status_listener():
    last_update_id = 0
    while True:
        try:
            url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates?offset={last_update_id + 1}&timeout=5"
            response = requests.get(url, timeout=10).json()
            if "result" in response:
                for update in response["result"]:
                    last_update_id = update["update_id"]
                    if "message" in update and "text" in update["message"]:
                        if update["message"]["text"] == "/status":
                            uptime_hours = round((time.time() - START_TIME) / 3600, 2)
                            msg = (
                                f"✅ *Bot Status: ACTIVE*\n"
                                f"⏳ Uptime: {uptime_hours}h\n"
                                f"📡 Pairs: {len(symbols)}\n"
                                f"🕒 {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
                            )
                            send_telegram_alert(msg)
        except Exception as e:
            print(f"Status Listener crashed: {e}. Restarting in 10s...")
            time.sleep(10)

# --- 3. HELPERS ---

def get_stars(multiplier):
    if multiplier >= 100:
        return "⭐⭐⭐⭐⭐", "Super Strong"
    elif multiplier >= 50:
        return "⭐⭐⭐⭐", "Very Strong"
    elif multiplier >= 20:
        return "⭐⭐⭐", "Strong"
    elif multiplier >= 10:
        return "⭐⭐", "Moderate"
    else:
        return "⭐", "Weak"

def get_signal_score(multiplier, price_change_pct):
    score = 0
    if multiplier >= 100: score += 7
    elif multiplier >= 50: score += 6
    elif multiplier >= 20: score += 5
    elif multiplier >= 10: score += 4
    else: score += 2
    if price_change_pct >= 5: score += 3
    elif price_change_pct >= 2: score += 2
    elif price_change_pct >= 0.5: score += 1
    return min(score, 10)

def format_volume(vol_usd):
    if vol_usd >= 1_000_000:
        return f"${vol_usd / 1_000_000:.1f}m"
    elif vol_usd >= 1_000:
        return f"${vol_usd / 1_000:.1f}k"
    else:
        return f"${vol_usd:.0f}"

def get_oi_change(symbol):
    try:
        url = f"https://fapi.binance.com/futures/data/openInterestHist?symbol={symbol.upper()}&period=3m&limit=2"
        data = requests.get(url, timeout=5).json()
        if isinstance(data, list) and len(data) >= 2:
            prev_oi = float(data[0]['sumOpenInterest'])
            curr_oi = float(data[1]['sumOpenInterest'])
            if prev_oi > 0:
                change = round(((curr_oi - prev_oi) / prev_oi) * 100, 2)
                trend  = "✅ Rising" if change > 0 else "❌ Falling"
                sign   = "+" if change > 0 else ""
                return f"{sign}{change}%", trend
    except Exception:
        pass
    return "N/A", "N/A"

def is_compressed(high, low, close, avg_volume, current_volume):
    try:
        price_range_pct = ((high - low) / close) * 100
        return price_range_pct < 0.5 and current_volume < avg_volume
    except Exception:
        return False

# --- 4. THE ANALYTICS ENGINE ---
def process_kline(symbol, kline):
    if not kline['x']:  # Only process closed candles
        return

    open_price     = float(kline['o'])
    close_price    = float(kline['c'])
    high_price     = float(kline['h'])
    low_price      = float(kline['l'])
    current_volume = float(kline['v'])

    if symbol not in volume_history:
        return

    history = volume_history[symbol]
    history.append(current_volume)
    if len(history) > 20:
        history.pop(0)

    if len(history) == 20:
        avg_volume = sum(history[:-1]) / 19
        if avg_volume > 0:
            volume_multiplier = round(current_volume / avg_volume, 1)

            if volume_multiplier >= 5:
                consecutive_spikes[symbol] += 1
                if consecutive_spikes[symbol] >= 3:
                    timestamp        = datetime.datetime.now().strftime("%H:%M:%S")
                    price_change_pct = round(((close_price - open_price) / open_price) * 100, 2)
                    avg_vol_usd      = avg_volume * close_price
                    current_vol_usd  = current_volume * close_price
                    stars, strength  = get_stars(volume_multiplier)
                    signal_score     = get_signal_score(volume_multiplier, price_change_pct)
                    price_arrow      = "📈" if price_change_pct >= 0 else "📉"
                    change_sign      = "+" if price_change_pct >= 0 else ""
                    compression      = "Yes" if is_compressed(high_price, low_price, close_price, avg_volume, current_volume) else "No"

                    if symbol.endswith('usdt'):
                        oi_change, oi_trend = get_oi_change(symbol)
                    else:
                        oi_change, oi_trend = "N/A", "N/A"

                    alert_msg = (
                        f"🚨 *CONFIRMED PUMP* · *{symbol.upper()}*\n"
                        f"[3m]\n\n"
                        f"🚨 *High Chance of Pump — Rapid Spike Detected!*\n\n"
                        f"*Stars:* {stars}\n"
                        f"({strength})\n\n"
                        f"*Entry:* ${close_price}\n"
                        f"*Volume Spike:* {volume_multiplier}x avg\n"
                        f"*Avg Vol (20×3m):* {format_volume(avg_vol_usd)} | Current: {format_volume(current_vol_usd)}\n"
                        f"*Price Change:* {price_arrow} {change_sign}{price_change_pct}%\n"
                        f"*OI Change:* {oi_change} | OI: {oi_trend}\n"
                        f"*Compression:* {compression}\n"
                        f"*Signal Score:* {signal_score}/10 | Timeframe: 3m\n"
                        f"🕒 {timestamp}"
                    )

                    send_telegram_alert(alert_msg)
                    consecutive_spikes[symbol] = 0
            else:
                consecutive_spikes[symbol] = 0

# --- 5. WEBSOCKET STREAMS ---
async def stream_chunk(streams):
    """Connect to a combined stream and process messages."""
    stream_path = "/".join(streams)
    url = f"wss://stream.binance.com:9443/stream?streams={stream_path}"

    while True:
        try:
            async with websockets.connect(url, ping_interval=20, ping_timeout=10) as ws:
                print(f"Connected to {len(streams)} streams")
                async for message in ws:
                    try:
                        data = json.loads(message)
                        if 'data' in data and data['data']['e'] == 'kline':
                            symbol = data['data']['s'].lower()
                            kline  = data['data']['k']
                            process_kline(symbol, kline)
                    except Exception as e:
                        print(f"Message error: {e}")
        except Exception as e:
            print(f"Stream error: {e}. Reconnecting in 5s...")
            await asyncio.sleep(5)

async def run_all_streams():
    """Split symbols into chunks and run all streams concurrently."""
    chunk_size = 200  # Binance allows max 1024 streams per connection
    stream_list = [f"{s}@kline_3m" for s in symbols]
    chunks = [stream_list[i:i+chunk_size] for i in range(0, len(stream_list), chunk_size)]

    print(f"Starting {len(chunks)} stream connections for {len(symbols)} pairs...")
    await asyncio.gather(*[stream_chunk(chunk) for chunk in chunks])

def run_streams_thread():
    """Run the async event loop in a thread."""
    while True:
        try:
            asyncio.run(run_all_streams())
        except Exception as e:
            print(f"Stream thread crashed: {e}. Restarting in 10s...")
            time.sleep(10)

# --- 6. LAUNCHER ---
print("Connecting to Binance Public API...")
client = Client()
exchange_info = client.get_exchange_info()
symbols = [s['symbol'].lower() for s in exchange_info['symbols']
           if s['status'] == 'TRADING' and s['quoteAsset'] in ['USDT', 'USDC']]

volume_history = {symbol: [] for symbol in symbols}
consecutive_spikes = {symbol: 0 for symbol in symbols}

if __name__ == "__main__":
    send_telegram_alert(f"🚀 *Bot Online!*\nMonitoring {len(symbols)} pairs.")

    threading.Thread(target=run_flask, daemon=True).start()
    threading.Thread(target=telegram_status_listener, daemon=True).start()
    threading.Thread(target=run_streams_thread, daemon=True).start()

    # Keep main thread alive
    while True:
        time.sleep(60)
