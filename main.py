import os
import logging
import requests
import time
import threading
import datetime
from flask import Flask
from binance.client import Client
from binance import ThreadedWebsocketManager

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
    """Fetch Open Interest change from Binance Futures (USDT pairs only)."""
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

def is_compressed(data, avg_volume, current_volume):
    """Compression = tight price range AND volume below average."""
    try:
        high  = float(data['k']['h'])
        low   = float(data['k']['l'])
        close = float(data['k']['c'])
        price_range_pct = ((high - low) / close) * 100
        return price_range_pct < 0.5 and current_volume < avg_volume
    except Exception:
        return False

# --- 4. THE ANALYTICS ENGINE ---
def handle_socket_message(msg):
    if 'data' not in msg:
        return
    data = msg['data']
    if data['e'] != 'kline' or not data['k']['x']:
        return

    symbol = data['s'].lower()
    if symbol not in volume_history:
        return

    open_price     = float(data['k']['o'])
    close_price    = float(data['k']['c'])
    current_volume = float(data['k']['v'])

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
                    compression      = "Yes" if is_compressed(data, avg_volume, current_volume) else "No"

                    # OI only available for USDT futures pairs
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

# --- 5. LAUNCHER ---
print("Connecting to Binance Public API...")
client = Client()
exchange_info = client.get_exchange_info()
symbols = [s['symbol'].lower() for s in exchange_info['symbols']
           if s['status'] == 'TRADING' and s['quoteAsset'] in ['USDT', 'USDC']]

volume_history = {symbol: [] for symbol in symbols}
consecutive_spikes = {symbol: 0 for symbol in symbols}

def run_binance_streams():
    while True:
        twm = None
        try:
            print("Starting Binance WebSocket streams...")
            twm = ThreadedWebsocketManager()
            twm.start()

            chunk_size = 500
            for i in range(0, len(symbols), chunk_size):
                twm.start_multiplex_socket(
                    callback=handle_socket_message,
                    streams=[f"{s}@kline_3m" for s in symbols[i:i+chunk_size]]
                )

            send_telegram_alert(f"🚀 *Bot Online!*\nMonitoring {len(symbols)} pairs.")
            twm.join()

        except Exception as e:
            print(f"Binance stream crashed: {e}")
            send_telegram_alert(f"⚠️ *WebSocket crashed:* {e}\nRestarting in 15s...")
        finally:
            if twm:
                try:
                    twm.stop()
                except Exception:
                    pass

        print("Restarting Binance streams in 15 seconds...")
        time.sleep(15)

if __name__ == "__main__":
    threading.Thread(target=run_flask, daemon=True).start()
    threading.Thread(target=telegram_status_listener, daemon=True).start()
    run_binance_streams()
