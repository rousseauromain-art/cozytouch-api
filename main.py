"""main.py — Bot Telegram chauffage + ballon eau chaude. v15.5"""
import asyncio, threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

from config import TOKEN, DB_URL, VERSION, log
from bec import (manage_bec, bec_get_index, is_heure_creuse,
                 get_hc_label, minutes_until_next_transition, save_transition)
from heating import (get_current_data, apply_heating_mode, perform_record,
                     init_db, get_rad_stats)


def get_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🏠 MAISON",         callback_data="HOME"),
         InlineKeyboardButton("❄️ ABSENCE",        callback_data="ABSENCE")],
        [InlineKeyboardButton("🔍 ÉTAT RADS",      callback_data="LIST"),
         InlineKeyboardButton("📊 STATS 7J",       callback_data="REPORT")],
        [InlineKeyboardButton("💧 BALLON ÉTAT",    callback_data="BEC_GET"),
         InlineKeyboardButton("📈 CONSO HC/HP",    callback_data="BEC_STATS")],
        [InlineKeyboardButton("🏡 BALLON MAISON",  callback_data="BEC_HOME"),
         InlineKeyboardButton("✈️ BALLON ABSENCE", callback_data="BEC_ABSENCE")],
    ])


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"🚀 Cozybot v{VERSION}", reply_markup=get_keyboard())


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    chat_id = query.message.chat_id

    # Répondre IMMÉDIATEMENT à Telegram (évite "Query is too old" après 60s)
    try:
        await query.answer()
    except Exception:
        pass  # Déjà expiré si double-clic, on continue quand même

    action = query.data

    # Actions rapides (< 5s) : éditer le message en place
    if action in ("HOME", "ABSENCE"):
        try:
            await query.edit_message_text(f"⏳ Radiateurs {action}...")
        except Exception:
            pass
        try:
            report = await apply_heating_mode(action)
            await context.bot.send_message(
                chat_id,
                f"<b>RÉSULTAT {action}</b>\n\n{report}",
                parse_mode="HTML", reply_markup=get_keyboard()
            )
        except Exception as e:
            log(f"Handler {action} ERR: {e}")
            await context.bot.send_message(chat_id, f"⚠️ {e}", reply_markup=get_keyboard())
        return

    if action == "LIST":
        try:
            await query.edit_message_text("🔍 Lecture radiateurs...")
        except Exception:
            pass
        try:
            data, shelly_t = await get_current_data()
            lines = []
            for n, v in data.items():
                lines.append(f"📍 <b>{n}</b>: {v['temp']}°C (Cible: {v['target']}°C)")
                if n == "Bureau" and shelly_t:
                    lines.append(f"   └ 🌡️ <i>Shelly : {shelly_t}°C</i>")
            lines.append(f"\n{get_hc_label()}")
            await context.bot.send_message(
                chat_id, "🌡️ <b>ÉTAT ACTUEL</b>\n\n" + "\n".join(lines),
                parse_mode="HTML", reply_markup=get_keyboard()
            )
        except Exception as e:
            log(f"Handler LIST ERR: {e}")
            await context.bot.send_message(chat_id, f"⚠️ {e}", reply_markup=get_keyboard())
        return

    if action == "REPORT":
        try:
            data, shelly_t = await get_current_data()
            bureau = data.get("Bureau", {})
            s = get_rad_stats()
            lines = ["📊 <b>BILAN BUREAU</b>"]
            if bureau.get("temp"):
                lines.append(f"🌡️ Radiateur actuel : <b>{bureau['temp']}°C</b>")
            if shelly_t:
                lines.append(f"🌡️ Shelly actuel    : <b>{shelly_t}°C</b>")
            if s and s[1] > 0:
                lines.append(f"📈 Δ moyen 7j : <b>{s[0]:+.1f}°C</b>  <i>({s[1]} mesures)</i>")
            else:
                lines.append("⚠️ Pas encore de données 7j.")
            await context.bot.send_message(
                chat_id, "\n".join(lines),
                parse_mode="HTML", reply_markup=get_keyboard()
            )
        except Exception as e:
            log(f"Handler REPORT ERR: {e}")
            await context.bot.send_message(chat_id, f"⚠️ {e}", reply_markup=get_keyboard())
        return

    # Actions BEC longues (jusqu'à 2-3min) : répondre immédiatement, traiter en background
    if action.startswith("BEC_"):
        bec_action = action[4:]
        labels = {
            "GET": "💧 Lecture ballon...",
            "STATS": "📈 Calcul conso...",
            "HOME": "🏡 Retour maison ballon...\n<i>(peut prendre 1-2 min pour les 7 jours)</i>",
            "ABSENCE": "✈️ Mode absence ballon...\n<i>(peut prendre 1-2 min pour les 7 jours)</i>",
        }
        try:
            await context.bot.send_message(
                chat_id,
                labels.get(bec_action, f"⏳ Ballon {bec_action}..."),
                parse_mode="HTML"
            )
        except Exception:
            pass

        # Lancer en tâche asyncio (non bloquant pour Telegram)
        async def run_bec():
            try:
                res = await manage_bec(bec_action)
                # Découper si trop long (limite Telegram 4096 chars)
                if len(res) > 4000:
                    await context.bot.send_message(
                        chat_id, f"<b>BALLON</b>\n\n{res[:4000]}",
                        parse_mode="HTML"
                    )
                    await context.bot.send_message(
                        chat_id, f"<i>...suite</i>\n{res[4000:8000]}",
                        parse_mode="HTML", reply_markup=get_keyboard()
                    )
                else:
                    await context.bot.send_message(
                        chat_id, f"<b>BALLON</b>\n\n{res}",
                        parse_mode="HTML", reply_markup=get_keyboard()
                    )
            except Exception as e:
                log(f"BEC {bec_action} ERR: {e}")
                await context.bot.send_message(
                    chat_id, f"⚠️ {e}", reply_markup=get_keyboard()
                )

        asyncio.create_task(run_bec())


# ---------------------------------------------------------------------------
# BACKGROUND TASKS
# ---------------------------------------------------------------------------
async def background_transition_logger():
    """Relevé BEC à chaque transition HC/HP (4× par jour).
    Enregistre index kWh + température eau haut ballon."""
    while True:
        wait = minutes_until_next_transition()
        log(f"Prochain relevé BEC dans {wait//60}min {wait%60}s")
        await asyncio.sleep(wait + 30)  # +30s pour être dans le bon slot
        idx, temp_eau = await bec_get_index()
        if idx is not None:
            hc = is_heure_creuse()
            save_transition(idx, hc, temp_eau)
        else:
            log("BEC transition : échec lecture index")

async def background_rad_logger():
    """Enregistrement horaire températures radiateurs."""
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
    def log_message(self, *a): pass

def main():
    init_db()
    threading.Thread(
        target=lambda: HTTPServer(("0.0.0.0", 8000), Health).serve_forever(),
        daemon=True
    ).start()
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CallbackQueryHandler(button_handler))

    async def post_init(application):
        loop = asyncio.get_event_loop()
        loop.create_task(background_transition_logger())
        loop.create_task(background_rad_logger())

    app.post_init = post_init
    log(f"DÉMARRAGE v{VERSION}")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
