"""Accounts, sessions and saved runs. SQLite locally; Postgres when DATABASE_URL is set (Vercel)."""
import hashlib, hmac, json, os, re, secrets, sqlite3, time
from pathlib import Path

PG = os.getenv("DATABASE_URL") or os.getenv("POSTGRES_URL", "")
DB = os.getenv("FIRSTPR_DB") or ("/tmp/firstpr.db" if os.getenv("VERCEL") else str(Path(__file__).resolve().parent.parent / "firstpr.db"))
DAY = 86400


def q(sql, args=(), one=False, last=False):
    if PG:
        import psycopg
        from psycopg.rows import dict_row
        sql = sql.replace("?", "%s")
        ret = last and re.match(r"\s*insert into (users|runs)\b", sql, re.I)
        with psycopg.connect(PG, row_factory=dict_row) as c:  # commits and closes on exit
            cur = c.execute(sql + (" returning id" if ret else ""), args)
            if ret:
                return cur.fetchone()["id"]
            if last:
                return None
            rows = cur.fetchall() if cur.description else []
            return (rows[0] if rows else None) if one else rows
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    try:
        cur = c.execute(sql, args)
        c.commit()
        if last:
            return cur.lastrowid
        rows = [dict(r) for r in cur.fetchall()]
        return (rows[0] if rows else None) if one else rows
    finally:
        c.close()


SQLITE_DDL = [
    "create table if not exists users(id integer primary key, email text unique not null, name text not null, pw text not null, created real)",
    "create table if not exists sessions(token text primary key, user_id integer not null, expires real)",
    "create table if not exists runs(id integer primary key, user_id integer not null, repo text, title text, kind text, payload text, chat text default '[]', created real, pr_url text, pr_state text)"]
PG_DDL = [d.replace("integer primary key", "bigserial primary key").replace("integer not null", "bigint not null").replace(" real", " double precision")
          for d in SQLITE_DDL]


def init():
    for ddl in (PG_DDL if PG else SQLITE_DDL):
        q(ddl, last=True)
    for col in ("pr_url text", "pr_state text"):  # upgrade databases created by older versions
        try:
            q(f"alter table runs add column {col}", last=True)
        except Exception:
            pass
    q("delete from sessions where expires < ?", (time.time(),), last=True)


def _hash(pw, salt=None):
    salt = salt or secrets.token_bytes(16)
    return salt.hex() + ":" + hashlib.scrypt(pw.encode(), salt=salt, n=2**14, r=8, p=1).hex()


def _verify(pw, stored):
    salt, h = stored.split(":")
    return hmac.compare_digest(hashlib.scrypt(pw.encode(), salt=bytes.fromhex(salt), n=2**14, r=8, p=1).hex(), h)


def signup(name, email, pw):
    email, name = email.strip().lower(), name.strip()[:60]
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        raise ValueError("Enter a valid email address.")
    if len(pw) < 8:
        raise ValueError("Use a password with at least 8 characters.")
    if q("select 1 from users where email=?", (email,), one=True):
        raise ValueError("That email already has an account. Sign in instead.")
    name = name or email.split("@")[0]
    uid = q("insert into users(email,name,pw,created) values(?,?,?,?)", (email, name, _hash(pw), time.time()), last=True)
    return {"id": uid, "name": name, "email": email}


def login(email, pw):
    u = q("select * from users where email=?", (email.strip().lower(),), one=True)
    if not u or not _verify(pw, u["pw"]):
        raise ValueError("Wrong email or password.")
    return {"id": u["id"], "name": u["name"], "email": u["email"]}


def _th(token):
    return hashlib.sha256(token.encode()).hexdigest()


def new_session(uid):
    t = secrets.token_urlsafe(32)
    q("insert into sessions values(?,?,?)", (_th(t), uid, time.time() + 30 * DAY), last=True)
    return t


def user_for(token):
    if not token:
        return None
    return q("select u.id,u.name,u.email from sessions s join users u on u.id=s.user_id where s.token=? and s.expires>?",
             (_th(token), time.time()), one=True)


def end_session(token):
    if token:
        q("delete from sessions where token=?", (_th(token),), last=True)


def save_run(uid, repo, title, kind, payload):
    return q("insert into runs(user_id,repo,title,kind,payload,created) values(?,?,?,?,?,?)",
             (uid, repo, title, kind, json.dumps(payload), time.time()), last=True)


def list_runs(uid):
    return q("select id,repo,title,kind,created,pr_url,pr_state from runs where user_id=? order by id desc limit 60", (uid,))


def get_run(uid, rid):
    r = q("select * from runs where id=? and user_id=?", (rid, uid), one=True)
    if r:
        r["payload"], r["chat"] = json.loads(r["payload"]), json.loads(r["chat"] or "[]")
    return r


def delete_run(uid, rid):
    q("delete from runs where id=? and user_id=?", (rid, uid), last=True)


def set_chat(uid, rid, chat):
    q("update runs set chat=? where id=? and user_id=?", (json.dumps(chat), rid, uid), last=True)


def set_pr(uid, rid, url, state):
    q("update runs set pr_url=?, pr_state=? where id=? and user_id=?", (url, state, rid, uid), last=True)
