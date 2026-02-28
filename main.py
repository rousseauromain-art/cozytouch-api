"""main.py — Bot Telegram chauffage + ballon eau chaude."""
import asyncio, threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

from config import TOKEN, DB_URL, VERSION, log
from bec import (manage_bec, bec_get_index, is_heure_creuse,
                 get_hc_label, minutes_until_next_transition, save_transition)
from heating import (get_current_data, apply_heating_mode, perform_record,
                     init_db, get_rad_stats)


# ---------------------------------------------------------------------------
# CLAVIER
# ---------------------------------------------------------------------------
def get_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🏠 MAISON",          callback_data="HOME"),
         InlineKeyboardButton("❄️ ABSENCE",         callback_data="ABSENCE")],
        [InlineKeyboardButton("🔍 ÉTAT RADS",       callback_data="LIST"),
         InlineKeyboardButton("📊 STATS 7J",        callback_data="REPORT")],
        [InlineKeyboardButton("💧 BALLON ÉTAT",     callback_data="BEC_GET"),
         InlineKeyboardButton("📈 CONSO HC/HP",     callback_data="BEC_STATS")],
        [InlineKeyboardButton("🏡 BALLON MAISON",   callback_data="BEC_HOME"),
         InlineKeyboardButton("✈️ BALLON ABSENCE",  callback_data="BEC_ABSENCE")],
    ])


# ---------------------------------------------------------------------------
# HANDLERS
# ---------------------------------------------------------------------------
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"🚀 v{VERSION}", reply_markup=get_keyboard())

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    try:
        # --- Radiateurs ---
        if query.data in ("HOME", "ABSENCE"):
            await query.edit_message_text(f"⏳ Radiateurs {query.data}...")
            report = await apply_heating_mode(query.data)
            await query.edit_message_text(
                f"<b>RÉSULTAT {query.data}</b>\n\n{report}",
                parse_mode="HTML", reply_markup=get_keyboard()
            )

        elif query.data == "LIST":
            await query.edit_message_text("🔍 Lecture...")
            data, shelly_t = await get_current_data()
            lines = []
            for n, v in data.items():
                lines.append(f"📍 <b>{n}</b>: {v['temp']}°C (Cible: {v['target']}°C)")
                if n == "Bureau" and shelly_t:
                    lines.append(f"   └ 🌡️ <i>Shelly : {shelly_t}°C</i>")
            lines.append(f"\n{get_hc_label()}")
            await query.edit_message_text(
                "🌡️ <b>ÉTAT ACTUEL</b>\n\n" + "\n".join(lines),
                parse_mode="HTML", reply_markup=get_keyboard()
            )

        elif query.data == "REPORT":
            # Températures actuelles Bureau
            data, shelly_t = await get_current_data()
            bureau = data.get("Bureau", {})
            rad_t  = bureau.get("temp")
            # Delta moyen 7 jours
            s = get_rad_stats()
            lines = ["📊 <b>BILAN BUREAU</b>"]
            if rad_t is not None:
                lines.append(f"🌡️ Radiateur actuel : <b>{rad_t}°C</b>")
            if shelly_t is not None:
                lines.append(f"🌡️ Shelly actuel    : <b>{shelly_t}°C</b>")
            if s and s[1] > 0:
                lines.append(f"📈 Δ moyen 7j : <b>{s[0]:+.1f}°C</b>  <i>({s[1]} mesures)</i>")
            else:
                lines.append("⚠️ Pas encore de données 7j.")
            await query.message.reply_text("\n".join(lines), parse_mode="HTML")

        # --- Ballon ---
        elif query.data.startswith("BEC_"):
            action = query.data[4:]
            await query.edit_message_text(f"⏳ Ballon {action}...")
            res = await manage_bec(action)
            await query.edit_message_text(
                f"<b>BALLON</b>\n\n{res[:4000]}",
                parse_mode="HTML", reply_markup=get_keyboard()
            )

    except Exception as e:
        log(f"Handler ERR: {e}")
        await query.edit_message_text(f"⚠️ {e}", reply_markup=get_keyboard())


# ---------------------------------------------------------------------------
# BACKGROUND
# ---------------------------------------------------------------------------
async def background_transition_logger():
    """Relevé BEC aux 4 transitions HC/HP journalières."""
    while True:
        wait = minutes_until_next_transition()
        log(f"Prochain relevé BEC dans {wait//60}min {wait%60}s")
        await asyncio.sleep(wait + 30)
        idx, temp_eau = await bec_get_index()
        if idx is not None:
            hc = is_heure_creuse()
            save_transition(idx, hc, temp_eau)
        else:
            log("BEC transition : impossible de lire l'index")

async def background_rad_logger():
    """Enregistrement horaire des radiateurs."""
    while True:
        await asyncio.sleep(3600)
        if DB_URL:
            await perform_record()


# ---------------------------------------------------------------------------
# HEALTH CHECK + MAIN
# ---------------------------------------------------------------------------
class Health(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.end_headers(); self.wfile.write(b"OK")
    def log_message(self, *a):
        pass

def main():
    init_db()
    threading.Thread(
        target=lambda: HTTPServer(("0.0.0.0", 8000), Health).serve_forever(),
        daemon=True
    ).start()
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CallbackQueryHandler(button_handler))
    loop = asyncio.get_event_loop()
    loop.create_task(background_transition_logger())
    loop.create_task(background_rad_logger())
    log(f"DÉMARRAGE v{VERSION}")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
