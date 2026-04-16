import json
from telegram import Bot
from telegram.constants import ParseMode
import html
import config

TELEGRAM_TOKEN = config.TELEGRAM_TOKEN
CHAT_ID = config.TELEGRAM_CHAT_ID

bot = Bot(token=TELEGRAM_TOKEN)


def format_trade_message(trade: dict) -> str:
    mint = trade['mint']
    result_emoji = "✅" if trade['roi'] > 0 else "❌"

    hold_time_str = f"{trade['hold_seconds']:,}s"          # e.g. 30s, 1,234s

    msg = f"<b>{result_emoji} Trade Result</b>\n"
    msg += f"<code>{mint}</code>\n\n"
    msg += f"📊 <b>Exit MC:</b> ${trade['exit_marketcap']:,.0f}\n" 
    msg += f"⏱️ <b>Held:</b> <code>{hold_time_str}</code>\n\n"   # ← fixed here

    msg += f"💵 <b>Entry Price:</b> ${trade['entry_price']:.8f}\n"
    msg += f"📈 <b>Exit Price:</b> ${trade['exit_price']:.8f}\n"
    msg += f"   <b>Reason:</b> {html.escape(trade['reason'])}\n"  # also escape reason

    msg += f"📊 <b>ROI:</b> {trade['roi']:.2f}%\n"

    if trade.get("stop_loss_triggered", False):
        msg += f"\n⚠️ Stop loss was triggered"

    msg += f"\n🔗 <a href='https://dexscreener.com/solana/{mint}'>View on Dex</a>"
    return msg

def format_token_message(row, age_min, rugcheck):
    name = html.escape(row['name'])
    symbol = html.escape(row['symbol'])
    source = html.escape(row['source'])
    mint = row['mint']

    msg = f"<b>🚨 New Token Alert!🚀 | {name} ({symbol})</b>\n"
    msg += f"<code>{mint}</code>\n\n"

    msg += f"📊 <b>Marketcap:</b> ${float(row['market_cap']):,.0f}\n"
    msg += f"⏱️ <b>Age:</b> {age_min:.0f}mins\n"
    msg += f"👨‍💻 <b>Dev:</b> <a href='https://solscan.io/account/{row.get('dev_wallet')}'>{row.get('dev_wallet', 'N/A')[:4]}...{row.get('dev_wallet', '')[-4:]}</a> 💰 {float(row['dev_wallet_pct']):.0f}%\n"
    
    if rugcheck['risks']:
        risk_summary = "\n".join(
            [f"⚠️ <b>{risk['name']}</b>: {risk['level']}" for risk in rugcheck['risks']])
    else:
        risk_summary = "No major risks detected ✅"

    msg += f"\n{risk_summary}"
    msg += f"\n🔗 <a href='{rugcheck['url']}'>View Full RugCheck</a>"

    msg += f"\n<b>DEX:</b> <a href='https://dexscreener.com/solana/{mint}'>View on Dex</a>\n"
    return msg


async def send_telegram_message(row, age_min, rugcheck, custom_message=None):
    if custom_message:
        message = custom_message
    else:
        message = format_token_message(row, age_min, rugcheck)

    try:
        await bot.send_message(
            chat_id=CHAT_ID,
            text=message,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True
        )
    except Exception as e:
        print(f"[Telegram Error] {e}")


async def send_trade_message(trade: dict):
    """Send a completed trade summary to Telegram"""
    message = format_trade_message(trade)
    try:
        await bot.send_message(
            chat_id=CHAT_ID,
            text=message,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True
        )
    except Exception as e:
        print(f"[Telegram Error - Trade] {e}")
