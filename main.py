"""main.py — Bot Telegram chauffage + ballon eau chaude. v15.6"""
import asyncio, threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

from config import TOKEN, DB_URL, VERSION, log
from bec import (manage_bec, bec_get_index, is_heure_creuse,
                 get_hc_label, minutes_until_next_transition, save_transition,
                 reset_transitions, get_absence_days, pct_to_temp,
                 write_capability, bec_authenticate, CAPS_QTITE, ATLANTIC_API)
from heating import (get_current_data, apply_heating_mode, perform_record,
                     init_db, get_rad_stats, get_salon_stats)
import httpx, json


def get_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🏠 MAISON",         callback_data="HOME"),
         InlineKeyboardButton("❄️ ABSENCE",        callback_data="ABSENCE")],
        [InlineKeyboardButton("🔍 ÉTAT RADS",      callback_data="LIST"),
         InlineKeyboardButton("📊 STATS SALON",    callback_data="SALON_STATS")],
        [InlineKeyboardButton("💧 BALLON ÉTAT",    callback_data="BEC_GET"),
         InlineKeyboardButton("📈 CONSO HC/HP",    callback_data="BEC_STATS")],
        [InlineKeyboardButton("🏡 BALLON MAISON",  callback_data="BEC_HOME"),
         InlineKeyboardButton("✈️ BALLON ABSENCE", callback_data="BEC_ABSENCE")],
        [InlineKeyboardButton("🗑️ RESET RELEVÉS",  callback_data="BEC_RESET")],
    ])


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"🚀 Cozybot v{VERSION}", reply_markup=get_keyboard())


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    chat_id = query.message.chat_id
    try:
        await query.answer()
    except Exception:
        pass

    action = query.data

    # ── Radiateurs : rapide ────────────────────────────────────────────────
    if action in ("HOME", "ABSENCE"):
        try:
            await query.edit_message_text(f"⏳ Radiateurs {action}...")
        except Exception:
            pass
        try:
            report = await apply_heating_mode(action)
            await context.bot.send_message(
                chat_id, f"<b>RÉSULTAT {action}</b>\n\n{report}",
                parse_mode="HTML", reply_markup=get_keyboard()
            )
        except Exception as e:
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
                if n == "Salon" and shelly_t:
                    lines.append(f"   └ 🌡️ <i>Shelly cuisine : {shelly_t}°C</i>")
            lines.append(f"\n{get_hc_label()}")
            await context.bot.send_message(
                chat_id, "🌡️ <b>ÉTAT ACTUEL</b>\n\n" + "\n".join(lines),
                parse_mode="HTML", reply_markup=get_keyboard()
            )
        except Exception as e:
            await context.bot.send_message(chat_id, f"⚠️ {e}", reply_markup=get_keyboard())
        return

    if action == "SALON_STATS":
        try:
            await query.edit_message_text("📊 Analyse salon...")
        except Exception:
            pass
        result = get_salon_stats()
        await context.bot.send_message(
            chat_id, result, parse_mode="HTML", reply_markup=get_keyboard()
        )
        return

    # ── BEC ────────────────────────────────────────────────────────────────
    if action.startswith("BEC_"):
        bec_action = action[4:]

        if bec_action == "RESET":
            ok = reset_transitions()
            await context.bot.send_message(
                chat_id,
                "🗑️ Table relevés vidée ✅" if ok else "❌ Erreur reset",
                reply_markup=get_keyboard()
            )
            return

        labels = {
            "GET":     "💧 Lecture ballon...",
            "STATS":   "📈 Chargement relevés...",
            "HOME":    "🏡 Retour maison ballon...\n<i>(~1-2 min)</i>",
            "ABSENCE": "✈️ Mode absence ballon...\n<i>(~1-2 min)</i>",
        }
        try:
            await context.bot.send_message(
                chat_id, labels.get(bec_action, f"⏳ {bec_action}..."),
                parse_mode="HTML"
            )
        except Exception:
            pass

        async def run_bec():
            try:
                res = await manage_bec(bec_action)
                chunks = [res[i:i+4000] for i in range(0, min(len(res), 8000), 4000)]
                for i, chunk in enumerate(chunks):
                    kb = get_keyboard() if i == len(chunks)-1 else None
                    await context.bot.send_message(
                        chat_id, f"<b>BALLON</b>\n\n{chunk}",
                        parse_mode="HTML", reply_markup=kb
                    )
            except Exception as e:
                log(f"BEC {bec_action} ERR: {e}")
                await context.bot.send_message(
                    chat_id, f"⚠️ {e}", reply_markup=get_keyboard()
                )

        asyncio.create_task(run_bec())


# ---------------------------------------------------------------------------
# SURVEILLANCE BALLON : auto 70% après 4 jours à 60%
# ---------------------------------------------------------------------------
async def background_bec_surveillance(app):
    """Vérifie chaque jour si le ballon est à 60% depuis plus de 4 jours.
    Si oui : passe automatiquement à 70% et notifie."""
    await asyncio.sleep(3600)  # attendre 1h au démarrage
    while True:
        try:
            jours = get_absence_days()
            log(f"Surveillance BEC : {jours} jour(s) à 60%")
            if jours is not None and jours >= 4:
                # Passer à 70% automatiquement
                token = await bec_authenticate()
                if token:
                    h = {"Authorization": f"Bearer {token}",
                         "Content-Type": "application/json"}
                    async with httpx.AsyncClient(timeout=30) as c:
                        r = await c.get(
                            f"{ATLANTIC_API}/magellan/cozytouch/setupviewv2", headers=h)
                        from bec import find_water_heater
                        dev = find_water_heater(r.json()[0].get("devices", []))
                        if dev:
                            dev_id = dev.get("deviceId")
                            T = pct_to_temp(70)  # 53.3°C
                            slot = json.dumps([[0, T], [0, 0], [0, 0], [0, 0]])
                            ok_list = await asyncio.gather(
                                *[write_capability(c, h, dev_id, cap_id, slot)
                                  for cap_id in CAPS_QTITE]
                            )
                            nb_ok = sum(ok_list)
                            log(f"Auto 70% : {nb_ok}/7 caps écrites")
                            # Notifier l'utilisateur
                            # Chat ID du proprio : à stocker en config ou en dur
                            from config import ADMIN_CHAT_ID
                            if ADMIN_CHAT_ID:
                                await app.bot.send_message(
                                    ADMIN_CHAT_ID,
                                    f"⚠️ <b>Ballon</b> : 60% depuis {jours} jours\n"
                                    f"✅ Passé automatiquement à <b>70%</b> (53°C)\n"
                                    f"<i>Sécurité anti-légionelle</i>",
                                    parse_mode="HTML"
                                )
        except Exception as e:
            log(f"Surveillance BEC ERR: {e}")
        await asyncio.sleep(24 * 3600)  # vérifier une fois par jour


# ---------------------------------------------------------------------------
# BACKGROUND TASKS
# ---------------------------------------------------------------------------
async def background_transition_logger():
    """Relevé BEC à chaque transition HC/HP (4×/jour)."""
    while True:
        wait = minutes_until_next_transition()
        log(f"Prochain relevé BEC dans {wait//60}min {wait%60}s")
        await asyncio.sleep(wait + 30)
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
            hc = is_heure_creuse()
            await perform_record(heure_creuse=hc)


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
        loop.create_task(background_bec_surveillance(application))

    app.post_init = post_init
    log(f"DÉMARRAGE v{VERSION}")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
