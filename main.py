"""main.py — Bot Telegram chauffage + ballon eau chaude. v15.7"""
import asyncio, threading, re, json, httpx
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (Application, CommandHandler, CallbackQueryHandler,
                           MessageHandler, filters, ContextTypes)

from config import TOKEN, DB_URL, VERSION, log
from bec import (manage_bec, bec_get_index, is_heure_creuse,
                 get_hc_label, minutes_until_next_transition, save_transition,
                 reset_transitions, get_absence_days, pct_to_temp,
                 write_capability, bec_authenticate, CAPS_QTITE, ATLANTIC_API,
                 find_water_heater)
from heating import (get_current_data, apply_heating_mode, perform_record,
                     init_db, get_rad_stats, get_salon_stats)
from scheduler import (init_scheduler_db, save_scheduled, mark_done,
                       cancel_scheduled, get_pending, get_pending_summary)


# ---------------------------------------------------------------------------
# CLAVIER
# ---------------------------------------------------------------------------
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


# ---------------------------------------------------------------------------
# PARSER DATE/HEURE
# ---------------------------------------------------------------------------
def parse_datetime_arg(args: list[str]) -> tuple[datetime | None, str, str]:
    """Parse les arguments d'une commande programmée.
    Formats acceptés :
      /bec now                         → immédiat
      /bec 14h                         → aujourd'hui 14h00
      /bec 14h30                       → aujourd'hui 14h30
      /bec jeu 14h                     → prochain jeudi 14h
      /bec 06/03 14h                   → le 06 mars à 14h
      /bec 06/03 14h Retour weekend    → avec label
    Retourne : (datetime_cible, label, erreur)
    """
    if not args:
        return None, "", "no_args"

    text = " ".join(args).strip()

    if text.lower() == "now":
        return datetime.now(), "maintenant", ""

    jours_map = {
        "lun": 0, "mar": 1, "mer": 2, "jeu": 3, "ven": 4, "sam": 5, "dim": 6,
        "lundi": 0, "mardi": 1, "mercredi": 2, "jeudi": 3, "vendredi": 4,
        "samedi": 5, "dimanche": 6
    }

    # Extraire date optionnelle
    date_part = None
    remaining = text

    # Format JJ/MM
    m_date = re.match(r"(\d{1,2})/(\d{1,2})\s*", text, re.I)
    if m_date:
        day, month = int(m_date.group(1)), int(m_date.group(2))
        year = datetime.now().year
        try:
            date_part = datetime(year, month, day)
            if date_part < datetime.now():
                date_part = datetime(year + 1, month, day)
        except ValueError:
            return None, "", "date invalide"
        remaining = text[m_date.end():]

    # Format jour de semaine
    elif any(text.lower().startswith(j) for j in jours_map):
        m_jour = re.match(r"(lundi|mardi|mercredi|jeudi|vendredi|samedi|dimanche|"
                          r"lun|mar|mer|jeu|ven|sam|dim)\s*", text, re.I)
        if m_jour:
            target_dow = jours_map[m_jour.group(1).lower()]
            now = datetime.now()
            diff = (target_dow - now.weekday()) % 7
            if diff == 0:
                diff = 7  # prochain si même jour
            date_part = (now + timedelta(days=diff)).replace(
                hour=0, minute=0, second=0, microsecond=0)
            remaining = text[m_jour.end():]

    # Extraire heure (obligatoire)
    m_heure = re.match(r"(\d{1,2})h(\d{0,2})\s*", remaining.strip(), re.I)
    if not m_heure:
        return None, "", "heure manquante (ex: 14h ou 14h30)"

    h, mn = int(m_heure.group(1)), int(m_heure.group(2) or 0)
    if not (0 <= h <= 23 and 0 <= mn <= 59):
        return None, "", "heure invalide"

    label = remaining[m_heure.end():].strip() or ""

    if date_part:
        target = date_part.replace(hour=h, minute=mn, second=0, microsecond=0)
    else:
        now = datetime.now()
        target = now.replace(hour=h, minute=mn, second=0, microsecond=0)
        if target <= now:
            target += timedelta(days=1)

    return target, label, ""


# ---------------------------------------------------------------------------
# COMMANDES /start /bec /rads /annuler /prog
# ---------------------------------------------------------------------------
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"🚀 Cozybot v{VERSION}", reply_markup=get_keyboard())


HELP_BEC = (
    "💧 <b>/bec</b> — Programme le ballon eau chaude\n\n"
    "<b>Modes disponibles :</b>\n"
    "  maison  → 80% semaine, 100% weekend (défaut si omis)\n"
    "  absence → 60% tous les jours\n\n"
    "<b>Formats date/heure :</b>\n"
    "  /bec maison now           → immédiatement\n"
    "  /bec maison 14h           → aujourd'hui à 14h\n"
    "  /bec maison jeu 18h       → jeudi prochain 18h\n"
    "  /bec maison 06/03 20h     → le 06/03 à 20h\n"
    "  /bec absence dim 10h Départ → avec étiquette\n\n"
    "/prog — voir toutes les programmations\n"
    "/annuler42 — annuler la #42"
)

HELP_RADS = (
    "🌡️ <b>/rads</b> — Programme les radiateurs\n\n"
    "<b>Modes disponibles :</b>\n"
    "  maison  → températures confort (défaut si omis)\n"
    "  absence → températures éco\n\n"
    "<b>Formats date/heure :</b>\n"
    "  /rads maison now          → immédiatement\n"
    "  /rads maison 7h           → aujourd'hui à 7h\n"
    "  /rads maison jeu 17h      → jeudi prochain 17h\n"
    "  /rads absence dim 10h Départ\n\n"
    "/prog — voir toutes les programmations\n"
    "/annuler42 — annuler la #42"
)


async def _schedule_action(update: Update, context: ContextTypes.DEFAULT_TYPE,
                           action: str):
    """Handler commun pour /bec et /rads."""
    chat_id  = update.effective_chat.id
    args     = context.args or []
    icon_map = {"BEC_HOME": "💧🏡", "BEC_ABSENCE": "💧✈️", "RADS_HOME": "🌡️🏡", "RADS_ABSENCE": "❄️🌡️"}
    icon     = icon_map.get(action, "⏰")

    if not args:
        help_txt = HELP_BEC if action == "BEC_HOME" else HELP_RADS
        await update.message.reply_text(help_txt, parse_mode="HTML")
        return

    target_dt, label, err = parse_datetime_arg(args)
    if err:
        await update.message.reply_text(
            f"❌ {err}\n\n" + (HELP_BEC if action == "BEC_HOME" else HELP_RADS),
            parse_mode="HTML"
        )
        return

    delay = (target_dt - datetime.now()).total_seconds()

    if delay <= 0:
        # Exécution immédiate
        await update.message.reply_text(f"{icon} En cours...")
        asyncio.create_task(_execute_action(action, chat_id, context, label or "maintenant"))
        return

    # Sauvegarder en DB
    sched_id = save_scheduled(target_dt, action, label, chat_id)
    h_disp = target_dt.strftime("%d/%m à %Hh%M")
    lbl_disp = f" — <i>{label}</i>" if label else ""
    hrs = int(delay // 3600); mins = int((delay % 3600) // 60)
    msg = (f"{icon} Programmé le <b>{h_disp}</b>{lbl_disp}\n"
           f"<i>dans {hrs}h{mins:02d}min</i>")
    if sched_id:
        msg += f"\n/annuler{sched_id}"
    await update.message.reply_text(msg, parse_mode="HTML", reply_markup=get_keyboard())

    async def delayed():
        await asyncio.sleep(delay)
        if sched_id:
            mark_done(sched_id)
        await _execute_action(action, chat_id, context, label or h_disp)

    asyncio.create_task(delayed())


async def _execute_action(action: str, chat_id: int, context, label: str):
    """Exécute une action programmée."""
    titles = {
        "BEC_HOME":     "🏡💧 BALLON MAISON",
        "BEC_ABSENCE":  "✈️💧 BALLON ABSENCE",
        "RADS_HOME":    "🏡🌡️ RADIATEURS MAISON",
        "RADS_ABSENCE": "❄️🌡️ RADIATEURS ABSENCE",
    }
    try:
        if action == "BEC_HOME":
            res = await manage_bec("HOME")
        elif action == "BEC_ABSENCE":
            res = await manage_bec("ABSENCE")
        elif action == "RADS_HOME":
            res = await apply_heating_mode("HOME")
        elif action == "RADS_ABSENCE":
            res = await apply_heating_mode("ABSENCE")
        else:
            res = f"Action inconnue : {action}"
        title = titles.get(action, action)
        await context.bot.send_message(
            chat_id, f"<b>{title}</b> ({label})\n\n{res}",
            parse_mode="HTML", reply_markup=get_keyboard()
        )
    except Exception as e:
        log(f"execute_action {action} ERR: {e}")
        await context.bot.send_message(
            chat_id, f"⚠️ {action} ({label}) : {e}", reply_markup=get_keyboard()
        )


async def cmd_bec(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Usage: /bec [maison|absence] [date] [heure] [label]
    Sans 2e argument : maison par défaut."""
    args = context.args or []
    if args and args[0].lower() in ("absence", "absent"):
        context.args = args[1:]
        await _schedule_action(update, context, "BEC_ABSENCE")
    else:
        await _schedule_action(update, context, "BEC_HOME")


async def cmd_rads(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Usage: /rads [maison|absence] [date] [heure] [label]
    Sans 2e argument : maison par défaut."""
    args = context.args or []
    if args and args[0].lower() in ("absence", "absent"):
        context.args = args[1:]
        await _schedule_action(update, context, "RADS_ABSENCE")
    else:
        await _schedule_action(update, context, "RADS_HOME")


async def cmd_prog(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Affiche toutes les programmations actives."""
    chat_id = update.effective_chat.id
    items = get_pending(chat_id)
    if not items:
        await update.message.reply_text("✅ Aucune programmation en attente.")
        return
    icons = {"BEC_HOME": "💧🏡", "BEC_ABSENCE": "💧✈️",
             "RADS_HOME": "🌡️🏡", "RADS_ABSENCE": "🌡️❄️"}
    lines = ["⏰ <b>PROGRAMMATIONS EN ATTENTE</b>\n"]
    for it in items:
        dt   = it["target_dt"].strftime("%d/%m à %Hh%M")
        ico  = icons.get(it["action"], "⏰")
        lbl  = f" — <i>{it['label']}</i>" if it["label"] else ""
        delay = (it["target_dt"] - datetime.now()).total_seconds()
        hrs = int(delay // 3600); mins = int((delay % 3600) // 60)
        lines.append(f"{ico} <b>{dt}</b>{lbl}\n"
                     f"   <i>dans {hrs}h{mins:02d}min</i>  /annuler{it['id']}")
    await update.message.reply_text(
        "\n\n".join(lines), parse_mode="HTML", reply_markup=get_keyboard()
    )


async def cmd_annuler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler générique pour /annulerN (ex: /annuler42)."""
    chat_id = update.effective_chat.id
    text = update.message.text or ""
    m = re.search(r"/annuler(\d+)", text)
    if not m:
        await update.message.reply_text("Usage : /annulerN (ex: /annuler42)")
        return
    sched_id = int(m.group(1))
    if cancel_scheduled(sched_id, chat_id):
        await update.message.reply_text(f"✅ Programmation #{sched_id} annulée.")
    else:
        await update.message.reply_text(f"❌ #{sched_id} introuvable ou déjà exécutée.")


# ---------------------------------------------------------------------------
# BOUTONS
# ---------------------------------------------------------------------------
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    chat_id = query.message.chat_id
    try:
        await query.answer()
    except Exception:
        pass

    action = query.data

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
            # Programmations rads en cours
            prog = get_pending_summary(chat_id)
            if prog:
                lines.append(f"\n⏰ <b>Programmations</b>\n{prog}")
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
                # Ajouter les programmations BEC dans l'état
                if bec_action == "GET":
                    prog = get_pending_summary(chat_id)
                    if prog:
                        res += f"\n\n⏰ <b>Programmations BEC</b>\n{prog}"
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
    await asyncio.sleep(3600)
    while True:
        try:
            jours = get_absence_days()
            log(f"Surveillance BEC : {jours} jour(s) à 60%")
            if jours is not None and jours >= 4:
                token = await bec_authenticate()
                if token:
                    h = {"Authorization": f"Bearer {token}",
                         "Content-Type": "application/json"}
                    async with httpx.AsyncClient(timeout=30) as c:
                        r = await c.get(
                            f"{ATLANTIC_API}/magellan/cozytouch/setupviewv2", headers=h)
                        dev = find_water_heater(r.json()[0].get("devices", []))
                        if dev:
                            dev_id = dev.get("deviceId")
                            T = pct_to_temp(70)
                            slot = json.dumps([[0, T], [0, 0], [0, 0], [0, 0]])
                            await asyncio.gather(
                                *[write_capability(c, h, dev_id, cap_id, slot)
                                  for cap_id in CAPS_QTITE]
                            )
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
        await asyncio.sleep(24 * 3600)


# ---------------------------------------------------------------------------
# BACKGROUND TASKS
# ---------------------------------------------------------------------------
async def background_transition_logger():
    while True:
        wait = minutes_until_next_transition()
        log(f"Prochain relevé BEC dans {wait//60}min {wait%60}s")
        await asyncio.sleep(wait + 30)
        idx, temp_eau = await bec_get_index()
        if idx is not None:
            save_transition(idx, is_heure_creuse(), temp_eau)
        else:
            log("BEC transition : échec lecture index")


async def background_rad_logger():
    while True:
        await asyncio.sleep(3600)
        if DB_URL:
            await perform_record(heure_creuse=is_heure_creuse())


# ---------------------------------------------------------------------------
# HEALTH + MAIN
# ---------------------------------------------------------------------------
class Health(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.end_headers(); self.wfile.write(b"OK")
    def log_message(self, *a): pass


def main():
    init_db()
    init_scheduler_db()
    threading.Thread(
        target=lambda: HTTPServer(("0.0.0.0", 8000), Health).serve_forever(),
        daemon=True
    ).start()
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("bec",   cmd_bec))
    app.add_handler(CommandHandler("rads",  cmd_rads))
    app.add_handler(CommandHandler("prog",  cmd_prog))
    # /annulerN : regex pour capturer le N
    app.add_handler(MessageHandler(
        filters.Regex(r"^/annuler\d+"), cmd_annuler
    ))

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
