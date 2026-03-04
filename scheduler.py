"""scheduler.py — Gestion des programmations BEC et radiateurs avec persistance DB."""
import psycopg2
from datetime import datetime
from config import DB_URL, log


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
                action     TEXT NOT NULL,   -- 'BEC_HOME', 'BEC_ABSENCE', 'RADS_HOME', 'RADS_ABSENCE'
                label      TEXT,            -- description libre ex: 'Retour jeudi soir'
                chat_id    BIGINT NOT NULL,
                done       BOOLEAN DEFAULT FALSE,
                done_at    TIMESTAMP
            );
        """)
        conn.commit(); cur.close(); conn.close()
    except Exception as e:
        log(f"Scheduler DB init ERR: {e}")


def save_scheduled(target_dt: datetime, action: str, label: str, chat_id: int) -> int | None:
    """Sauvegarde une programmation et retourne son ID."""
    if not DB_URL:
        return None
    try:
        conn = psycopg2.connect(DB_URL)
        cur  = conn.cursor()
        cur.execute(
            "INSERT INTO scheduled_actions (target_dt, action, label, chat_id)"
            " VALUES (%s,%s,%s,%s) RETURNING id",
            (target_dt, action, label, chat_id)
        )
        row_id = cur.fetchone()[0]
        conn.commit(); cur.close(); conn.close()
        return row_id
    except Exception as e:
        log(f"save_scheduled ERR: {e}"); return None


def mark_done(sched_id: int):
    if not DB_URL:
        return
    try:
        conn = psycopg2.connect(DB_URL)
        cur  = conn.cursor()
        cur.execute(
            "UPDATE scheduled_actions SET done=TRUE, done_at=NOW() WHERE id=%s",
            (sched_id,)
        )
        conn.commit(); cur.close(); conn.close()
    except Exception as e:
        log(f"mark_done ERR: {e}")


def cancel_scheduled(sched_id: int, chat_id: int) -> bool:
    """Annule une programmation si elle appartient au bon chat."""
    if not DB_URL:
        return False
    try:
        conn = psycopg2.connect(DB_URL)
        cur  = conn.cursor()
        cur.execute(
            "DELETE FROM scheduled_actions WHERE id=%s AND chat_id=%s AND done=FALSE",
            (sched_id, chat_id)
        )
        deleted = cur.rowcount > 0
        conn.commit(); cur.close(); conn.close()
        return deleted
    except Exception as e:
        log(f"cancel_scheduled ERR: {e}"); return False


def get_pending(chat_id: int | None = None) -> list[dict]:
    """Retourne les programmations en attente (non exécutées, futures)."""
    if not DB_URL:
        return []
    try:
        conn = psycopg2.connect(DB_URL)
        cur  = conn.cursor()
        q = """SELECT id, target_dt, action, label, chat_id
               FROM scheduled_actions
               WHERE done=FALSE AND target_dt > NOW()"""
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


def get_pending_summary(chat_id: int | None = None) -> str:
    """Résumé court des programmations actives pour affichage dans ÉTAT."""
    items = get_pending(chat_id)
    if not items:
        return ""
    icons = {"BEC_HOME": "🏡💧", "BEC_ABSENCE": "✈️💧",
             "RADS_HOME": "🏡🌡️", "RADS_ABSENCE": "❄️🌡️"}
    lines = []
    for it in items[:3]:  # max 3 dans le résumé
        dt  = it["target_dt"].strftime("%d/%m %Hh%M")
        ico = icons.get(it["action"], "⏰")
        lbl = f" — {it['label']}" if it["label"] else ""
        lines.append(f"  {ico} {dt}{lbl} [/annuler{it['id']}]")
    return "\n".join(lines)
