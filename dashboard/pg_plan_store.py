"""PostgreSQL storage belonging exclusively to FK Dashboard, not the bots."""
import os
from datetime import datetime
import psycopg
from psycopg.types.json import Jsonb

def _connect():
    url = os.getenv("PLAN_DATABASE_URL", "").strip()
    if not url:
        raise RuntimeError("PLAN_DATABASE_URL must point to a SEPARATE PostgreSQL database")
    return psycopg.connect(url, connect_timeout=15)

def init_storage():
    with _connect() as db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS fk_dashboard_plan_state (
                id smallint PRIMARY KEY CHECK (id=1),
                payload jsonb NOT NULL DEFAULT '{}'::jsonb,
                updated_at timestamptz NOT NULL DEFAULT now()
            )
        """)
        db.execute("""
            INSERT INTO fk_dashboard_plan_state(id,payload)
            VALUES (1,'{}'::jsonb) ON CONFLICT (id) DO NOTHING
        """)

def health():
    with _connect() as db:
        if db.execute("SELECT id FROM fk_dashboard_plan_state WHERE id=1").fetchone() is None:
            raise RuntimeError("Dashboard plan storage is not initialized")

def read():
    with _connect() as db:
        row=db.execute("SELECT payload FROM fk_dashboard_plan_state WHERE id=1").fetchone()
        if row is None:
            raise RuntimeError("Dashboard plan storage is not initialized")
        return row[0] or {}

def update(modify):
    with _connect() as db:
        row=db.execute("SELECT payload FROM fk_dashboard_plan_state WHERE id=1 FOR UPDATE").fetchone()
        if row is None:
            raise RuntimeError("Dashboard plan storage is not initialized")
        payload=row[0] or {}
        result=modify(payload)
        db.execute("UPDATE fk_dashboard_plan_state SET payload=%s,updated_at=now() WHERE id=1",(Jsonb(payload),))
        return result

def load():
    return read().get("plans",{})

def history():
    return list(reversed(read().get("history",[])))

def log_upload(entry, limit):
    def modify(payload):
        entries=payload.get("history",[])
        entries.append({"at":datetime.now().isoformat(timespec="seconds"),**entry})
        payload["history"]=entries[-limit:]
        payload.setdefault("plans",{})
    update(modify)

def save(new_plans,source=""):
    def modify(payload):
        current=payload.get("plans",{})
        replaced={day:sum(current[day].values()) for day in new_plans if day in current}
        current.update(new_plans)
        payload.update({"updated_at":datetime.now().isoformat(timespec="seconds"),"source":source,"plans":current})
        return dict(current),replaced
    return update(modify)

def delete_dates(dates,limit):
    def modify(payload):
        plans=payload.get("plans",{})
        removed=[day for day in dates if plans.pop(day,None) is not None]
        payload.update({"updated_at":datetime.now().isoformat(timespec="seconds"),"plans":plans})
        history=payload.get("history",[])
        history.append({"at":datetime.now().isoformat(timespec="seconds"),"action":"delete","ok":True,"file":"","dates":removed,"positions":0})
        payload["history"]=history[-limit:]
        return dict(plans)
    return update(modify)
