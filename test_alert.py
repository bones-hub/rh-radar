from telegram_alert import send_telegram_alert, format_candidate_message

msg = format_candidate_message(
    lane_label="undervalued_early",
    symbol="TEST",
    liquidity_usd=7000,
    volume_h24=500,
    market_cap=18000,
    extra_line="Age: 1.0h",
    dex_url="https://dexscreener.com",
    status_label="NEW"
)

result = send_telegram_alert(msg)
print("Sent:", result)