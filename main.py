"""main.py — Bot Telegram chauffage + ballon eau chaude. v15.7"""
import asyncio, threading, re, json, httpx, sys, os
import psycopg2
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (Application, CommandHandler, CallbackQueryHandler,
                           MessageHandler, filters, ContextTypes)
from telegram.error import Conflict, NetworkError

from config import TOKEN, DB_URL, VERSION, log, ADMIN_CHAT_ID, ATLANTIC_API
from bec import (manage_bec, bec_get_index, is_heure_creuse,
                 get_hc_label, minutes_until_next_transition, save_transition,
                 pct_to_temp, write_capability, bec_authenticate,
                 find_water_heater, CAPS_QTITE)
from heating import (get_current_data, apply_heating_mode, perform_record,
                     init_db, get_salon_stats)


# ---------------------------------------------------------------------------
# SCHEDULER (inline)
# ---------------------------------------------------------------------------
def init_scheduler_db():
    if not DB_URL:
        return
    try:
        conn = psycopg2.connect(DB_URL)
        cur  = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS scheduled_actions (
                id SERIAL PRIMARY KEY,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                target_dt  TIMESTAMP NOT NULL,
                action     TEXT NOT NULL,
                label      TEXT,
                chat_id    BIGINT NOT NULL,
                done       BOOLEAN DEFAULT FALSE,
                done_at    TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS bec_mode_log (
                id SERIAL PRIMARY KEY,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                mode TEXT NOT NULL
            );
        """)
        conn.commit(); cur.close(); conn.close()
    except Exception as e:
        log(f"Scheduler DB init ERR: {e}")


def save_scheduled(target_dt, action, label, chat_id):
    if not DB_URL:
        return None
    try:
        conn = psycopg2.connect(DB_URL)
        cur  = conn.cursor()
        cur.execute(
            "INSERT INTO scheduled_actions (target_dt,action,label,chat_id)"
            " VALUES (%s,%s,%s,%s) RETURNING id",
            (target_dt, action, label, chat_id))
        row_id = cur.fetchone()[0]
        conn.commit(); cur.close(); conn.close()
        return row_id
    except Exception as e:
        log(f"save_scheduled ERR: {e}"); return None


def mark_done(sched_id):
    if not DB_URL:
        return
    try:
        conn = psycopg2.connect(DB_URL)
        cur  = conn.cursor()
        cur.execute("UPDATE scheduled_actions SET done=TRUE,done_at=NOW()"
                    " WHERE id=%s", (sched_id,))
        conn.commit(); cur.close(); conn.close()
    except Exception as e:
        log(f"mark_done ERR: {e}")


def cancel_scheduled(sched_id, chat_id):
    if not DB_URL:
        return False
    try:
        conn = psycopg2.connect(DB_URL)
        cur  = conn.cursor()
        cur.execute("DELETE FROM scheduled_actions"
                    " WHERE id=%s AND chat_id=%s AND done=FALSE",
                    (sched_id, chat_id))
        deleted = cur.rowcount > 0
        conn.commit(); cur.close(); conn.close()
        return deleted
    except Exception as e:
        log(f"cancel_scheduled ERR: {e}"); return False


def get_pending(chat_id=None):
    if not DB_URL:
        return []
    try:
        conn = psycopg2.connect(DB_URL)
        cur  = conn.cursor()
        q = ("SELECT id,target_dt,action,label,chat_id"
             " FROM scheduled_actions"
             " WHERE done=FALSE AND target_dt > NOW()")
        params = []
        if chat_id:
            q += " AND chat_id=%s"
            params.append(chat_id)
        q += " ORDER BY target_dt ASC"
        cur.execute(q, params)
        rows = cur.fetchall(); cur.close(); conn.close()
        return [{"id": r[0], "target_dt": r[1], "action": r[2],
                 "label": r[3], "chat_id": r[4]} for r in rows]
    except Exception as e:
        log(f"get_pending ERR: {e}"); return []


def get_pending_summary(chat_id=None):
    items = get_pending(chat_id)
    if not items:
        return ""
    icons = {"BEC_HOME": "🏡💧", "BEC_ABSENCE": "✈️💧",
             "RADS_HOME": "🏡🌡️", "RADS_ABSENCE": "❄️🌡️"}
    lines = []
    for it in items[:3]:
        dt  = it["target_dt"].strftime("%d/%m %Hh%M")
        ico = icons.get(it["action"], "⏰")
        lbl = f" — {it['label']}" if it["label"] else ""
        lines.append(f"  {ico} {dt}{lbl} [/annuler{it['id']}]")
    return "\n".join(lines)


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
def parse_datetime_arg(args):
    if not args:
        return None, "", "no_args"
    text = " ".join(args).strip()
    if text.lower() == "now":
        return datetime.now(), "maintenant", ""

    jours_map = {
        "lun": 0, "mar": 1, "mer": 2, "jeu": 3, "ven": 4, "sam": 5, "dim": 6,
        "lundi": 0, "mardi": 1, "mercredi": 2, "jeudi": 3, "vendredi": 4,
        "samedi": 5, "dimanche": 6,
    }
    date_part = None
    remaining = text

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
    else:
        m_jour = re.match(
            r"(lundi|mardi|mercredi|jeudi|vendredi|samedi|dimanche"
            r"|lun|mar|mer|jeu|ven|sam|dim)\s*", text, re.I)
        if m_jour:
            target_dow = jours_map[m_jour.group(1).lower()]
            now  = datetime.now()
            diff = (target_dow - now.weekday()) % 7 or 7
            date_part = (now + timedelta(days=diff)).replace(
                hour=0, minute=0, second=0, microsecond=0)
            remaining = text[m_jour.end():]

    m_heure = re.match(r"(\d{1,2})h(\d{0,2})\s*", remaining.strip(), re.I)
    if not m_heure:
        return None, "", "heure manquante (ex: 14h ou 14h30)"
    h, mn = int(m_heure.group(1)), int(m_heure.group(2) or 0)
    if not (0 <= h <= 23 and 0 <= mn <= 59):
        return None, "", "heure invalide"

    label = remaining[m_heure.end():].strip()
    if date_part:
        target = date_part.replace(hour=h, minute=mn, second=0, microsecond=0)
    else:
        now    = datetime.now()
        target = now.replace(hour=h, minute=mn, second=0, microsecond=0)
        if target <= now:
            target += timedelta(days=1)
    return target, label, ""


# ---------------------------------------------------------------------------
# COMMANDES
# ---------------------------------------------------------------------------
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"🚀 Cozybot v{VERSION}",
                                    reply_markup=get_keyboard())


async def _schedule_action(update, context, action):
    chat_id = update.effective_chat.id
    args    = context.args or []
    icons   = {"BEC_HOME": "💧🏡", "BEC_ABSENCE": "💧✈️",
               "RADS_HOME": "🌡️🏡", "RADS_ABSENCE": "❄️🌡️"}
    icon    = icons.get(action, "⏰")

    if not args:
        help_txt = (
            "💧 <b>/bec [maison|absence] [now|Xh|jour Xh] [label]</b>\n"
            if "BEC" in action else
            "🌡️ <b>/rads [maison|absence] [now|Xh|jour Xh] [label]</b>\n"
        )
        await update.message.reply_text(help_txt, parse_mode="HTML")
        return

    target_dt, label, err = parse_datetime_arg(args)
    if err:
        await update.message.reply_text(f"❌ {err}")
        return

    delay = (target_dt - datetime.now()).total_seconds()
    if delay <= 0:
        await update.message.reply_text(f"{icon} En cours...")
        asyncio.create_task(
            _execute_action(action, chat_id, context, label or "maintenant"))
        return

    sched_id = save_scheduled(target_dt, action, label, chat_id)
    h_disp   = target_dt.strftime("%d/%m à %Hh%M")
    lbl_disp = f" — <i>{label}</i>" if label else ""
    hrs, mins = int(delay // 3600), int((delay % 3600) // 60)
    msg = (f"{icon} Programmé le <b>{h_disp}</b>{lbl_disp}\n"
           f"<i>dans {hrs}h{mins:02d}min</i>")
    if sched_id:
        msg += f"\n/annuler{sched_id}"
    await update.message.reply_text(msg, parse_mode="HTML",
                                    reply_markup=get_keyboard())

    async def delayed():
        await asyncio.sleep(delay)
        if sched_id:
            mark_done(sched_id)
        await _execute_action(action, chat_id, context, label or h_disp)

    asyncio.create_task(delayed())


async def _execute_action(action, chat_id, context, label):
    titles = {
        "BEC_HOME":     "🏡💧 BALLON MAISON",
        "BEC_ABSENCE":  "✈️💧 BALLON ABSENCE",
        "RADS_HOME":    "🏡🌡️ RADIATEURS MAISON",
        "RADS_ABSENCE": "❄️🌡️ RADIATEURS ABSENCE",
    }
    try:
        if   action == "BEC_HOME":     res = await manage_bec("HOME")
        elif action == "BEC_ABSENCE":  res = await manage_bec("ABSENCE")
        elif action == "RADS_HOME":    res = await apply_heating_mode("HOME")
        elif action == "RADS_ABSENCE": res = await apply_heating_mode("ABSENCE")
        else:                          res = f"Action inconnue : {action}"
        await context.bot.send_message(
            chat_id,
            f"<b>{titles.get(action, action)}</b> ({label})\n\n{res}",
            parse_mode="HTML", reply_markup=get_keyboard())
    except Exception as e:
        log(f"execute_action {action} ERR: {e}")
        await context.bot.send_message(chat_id, f"⚠️ {e}",
                                       reply_markup=get_keyboard())


async def cmd_bec(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args or []
    if args and args[0].lower() in ("absence", "absent"):
        context.args = args[1:]; await _schedule_action(update, context, "BEC_ABSENCE")
    else:
        if args and args[0].lower() == "maison": context.args = args[1:]
        await _schedule_action(update, context, "BEC_HOME")


async def cmd_rads(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args or []
    if args and args[0].lower() in ("absence", "absent"):
        context.args = args[1:]; await _schedule_action(update, context, "RADS_ABSENCE")
    else:
        if args and args[0].lower() == "maison": context.args = args[1:]
        await _schedule_action(update, context, "RADS_HOME")


async def cmd_prog(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    items   = get_pending(chat_id)
    if not items:
        await update.message.reply_text("✅ Aucune programmation en attente.")
        return
    icons = {"BEC_HOME": "💧🏡", "BEC_ABSENCE": "💧✈️",
             "RADS_HOME": "🌡️🏡", "RADS_ABSENCE": "🌡️❄️"}
    lines = ["⏰ <b>PROGRAMMATIONS EN ATTENTE</b>\n"]
    for it in items:
        dt    = it["target_dt"].strftime("%d/%m à %Hh%M")
        lbl   = f" — <i>{it['label']}</i>" if it["label"] else ""
        delay = (it["target_dt"] - datetime.now()).total_seconds()
        hrs, mins = int(delay // 3600), int((delay % 3600) // 60)
        lines.append(
            f"{icons.get(it['action'], '⏰')} <b>{dt}</b>{lbl}\n"
            f"   <i>dans {hrs}h{mins:02d}min</i>  /annuler{it['id']}")
    await update.message.reply_text(
        "\n\n".join(lines), parse_mode="HTML", reply_markup=get_keyboard())


async def cmd_annuler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    m = re.search(r"/annuler(\d+)", update.message.text or "")
    if not m:
        await update.message.reply_text("Usage : /annulerN (ex: /annuler42)")
        return
    sched_id = int(m.group(1))
    if cancel_scheduled(sched_id, chat_id):
        await update.message.reply_text(f"✅ Programmation #{sched_id} annulée.")
    else:
        await update.message.reply_text(
            f"❌ #{sched_id} introuvable ou déjà exécutée.")


# ---------------------------------------------------------------------------
# BOUTONS
# ---------------------------------------------------------------------------
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query   = update.callback_query
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
                chat_id, f"<b>RADIATEURS {action}</b>\n\n{report}",
                parse_mode="HTML", reply_markup=get_keyboard())
        except Exception as e:
            await context.bot.send_message(chat_id, f"⚠️ {e}",
                                           reply_markup=get_keyboard())
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
                lines.append(f"📍 <b>{n}</b>: {v['temp']}°C"
                             f" (Cible: {v['target']}°C)")
                if n == "Salon" and shelly_t:
                    lines.append(
                        f"   └ 🌡️ <i>Shelly cuisine : {shelly_t}°C</i>")
            lines.append(f"\n{get_hc_label()}")
            prog = get_pending_summary(chat_id)
            if prog:
                lines.append(f"\n⏰ <b>Programmations</b>\n{prog}")
            await context.bot.send_message(
                chat_id, "🌡️ <b>ÉTAT ACTUEL</b>\n\n" + "\n".join(lines),
                parse_mode="HTML", reply_markup=get_keyboard())
        except Exception as e:
            await context.bot.send_message(chat_id, f"⚠️ {e}",
                                           reply_markup=get_keyboard())
        return

    if action == "SALON_STATS":
        try:
            await query.edit_message_text("📊 Analyse salon...")
        except Exception:
            pass
        await context.bot.send_message(
            chat_id, get_salon_stats(), parse_mode="HTML",
            reply_markup=get_keyboard())
        return

    if action.startswith("BEC_"):
        bec_action = action[4:]

        if bec_action == "RESET":
            ok = reset_transitions()
            await context.bot.send_message(
                chat_id,
                "🗑️ Table relevés vidée ✅" if ok else "❌ Erreur reset",
                reply_markup=get_keyboard())
            return

        labels = {
            "GET":     "💧 Lecture ballon...",
            "STATS":   "📈 Chargement relevés...",
            "HOME":    "🏡 Retour maison ballon... <i>(~1-2 min)</i>",
            "ABSENCE": "✈️ Mode absence ballon... <i>(~1-2 min)</i>",
        }
        try:
            await context.bot.send_message(
                chat_id, labels.get(bec_action, f"⏳ {bec_action}..."),
                parse_mode="HTML")
        except Exception:
            pass

        async def run_bec():
            try:
                res = await manage_bec(bec_action)
                if bec_action == "GET":
                    prog = get_pending_summary(chat_id)
                    if prog:
                        res += f"\n\n⏰ <b>Programmations BEC</b>\n{prog}"
                chunks = [res[i:i+4000]
                          for i in range(0, min(len(res), 8000), 4000)]
                for i, chunk in enumerate(chunks):
                    kb = get_keyboard() if i == len(chunks) - 1 else None
                    await context.bot.send_message(
                        chat_id, f"<b>BALLON</b>\n\n{chunk}",
                        parse_mode="HTML", reply_markup=kb)
            except Exception as e:
                log(f"BEC {bec_action} ERR: {e}")
                await context.bot.send_message(
                    chat_id, f"⚠️ {e}", reply_markup=get_keyboard())

        asyncio.create_task(run_bec())


# ---------------------------------------------------------------------------
# SURVEILLANCE BALLON
# ---------------------------------------------------------------------------
async def background_bec_surveillance(app):
    # Surveillance désactivée dans cette version
    return


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
# ERROR HANDLER
# ---------------------------------------------------------------------------
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    err = context.error
    if isinstance(err, Conflict):
        return  # normal ~15s au déploiement
    if isinstance(err, NetworkError):
        log(f"NetworkError (transitoire) : {err}")
        return
    log(f"Erreur : {type(err).__name__}: {err}")


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
    init_scheduler_db()
    threading.Thread(
        target=lambda: HTTPServer(("0.0.0.0", 8000), Health).serve_forever(),
        daemon=True
    ).start()
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start",  cmd_start))
    app.add_handler(CommandHandler("bec",    cmd_bec))
    app.add_handler(CommandHandler("rads",   cmd_rads))
    app.add_handler(CommandHandler("prog",   cmd_prog))
    app.add_handler(MessageHandler(
        filters.Regex(r"^/annuler\d+"), cmd_annuler))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_error_handler(error_handler)

    async def post_init(application):
        loop = asyncio.get_event_loop()
        loop.create_task(background_transition_logger())
        loop.create_task(background_rad_logger())
        loop.create_task(background_bec_surveillance(application))

    app.post_init = post_init
    log(f"DÉMARRAGE v{VERSION}")
    app.run_polling(drop_pending_updates=True,
                    allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
