import base64, json, os, queue, re, threading

import httpx
from pathlib import Path
from fastapi import Cookie, Depends, FastAPI, Header, HTTPException, Response
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel
from . import core, db
from . import github as gh

app = FastAPI(title="FirstPR")
db.init()
COOKIE, ALLOWED = "fp_session", {"image/png", "image/jpeg", "image/webp"}


class Auth(BaseModel):
    email: str
    password: str
    name: str = ""


class Analyze(BaseModel):
    repo: str
    skills: str = ""
    ground: bool = False
    lang: str = "en"


class Plan(BaseModel):
    repo: str
    issue: int
    skills: str = ""
    image_b64: str | None = None
    image_mime: str | None = None
    lang: str = "en"


class Pr(BaseModel):
    url: str = ""


PR_RE = re.compile(r"https://github\.com/([\w.-]+)/([\w.-]+)/pull/(\d+)/?")


class Ask(BaseModel):
    question: str
    lang: str = "en"


def me(fp_session: str | None = Cookie(None)):
    u = db.user_for(fp_session)
    if not u:
        raise HTTPException(401, "Please sign in.")
    return u


def _run(fn):
    try:
        return fn()
    except FileNotFoundError:
        raise HTTPException(404, "Repository or file not found. Check the URL.")
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(500, f"{type(e).__name__}: {e}")


def _start(resp, u):
    resp.set_cookie(COOKIE, db.new_session(u["id"]), httponly=True, samesite="lax", secure=os.getenv("FIRSTPR_SECURE") == "1", max_age=30 * 86400)
    return u


def _use_key(k, backend=None):
    k = (k or "").strip()[:200]
    core.LOCAL.set(backend == "ollama")
    if backend != "ollama" and os.getenv("FIRSTPR_REQUIRE_KEY") == "1" and not k:
        raise ValueError("Add your free Gemini API key with the key button in the sidebar to use this demo.")
    core.KEY.set(k or None)


def _err(e):
    if isinstance(e, FileNotFoundError):
        return "Repository or file not found. Check the URL."
    return str(e) if isinstance(e, ValueError) else f"{type(e).__name__}: {e}"


def _stream(work):
    """Run work() in a thread and stream its progress as newline-delimited JSON."""
    qu = queue.Queue()

    def run():
        core.PROGRESS.set(lambda m: qu.put({"t": "progress", "msg": m}))
        try:
            qu.put({"t": "done", "data": work()})
        except Exception as e:
            qu.put({"t": "error", "detail": _err(e)})
        qu.put(None)
    threading.Thread(target=run, daemon=True).start()

    def gen():
        while True:
            try:
                x = qu.get(timeout=15)
            except queue.Empty:
                yield "\n"  # heartbeat so proxies keep the connection open
                continue
            if x is None:
                return
            yield json.dumps(x) + "\n"
    return StreamingResponse(gen(), media_type="application/x-ndjson", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


def _image(r):
    if not r.image_b64:
        return None
    if r.image_mime not in ALLOWED:
        raise ValueError("Use a PNG, JPEG or WebP screenshot.")
    data = base64.b64decode(r.image_b64, validate=True)
    if len(data) > 4_000_000:
        raise ValueError("The screenshot is over 4 MB.")
    return data, r.image_mime


@app.post("/api/signup")
def signup(a: Auth, resp: Response):
    return _run(lambda: _start(resp, db.signup(a.name, a.email, a.password)))


@app.post("/api/login")
def login(a: Auth, resp: Response):
    return _run(lambda: _start(resp, db.login(a.email, a.password)))


@app.post("/api/logout")
def logout(resp: Response, fp_session: str | None = Cookie(None)):
    db.end_session(fp_session)
    resp.delete_cookie(COOKIE)
    return {"ok": True}


@app.get("/api/me")
def whoami(u=Depends(me)):
    return u


@app.post("/api/analyze")
def analyze(r: Analyze, u=Depends(me), x_gemini_key: str | None = Header(None), x_backend: str | None = Header(None), stream: int = 0):
    def go():
        _use_key(x_gemini_key, x_backend)
        out = core.analyze(r.repo, r.skills, r.ground, r.lang)
        out["skills"] = r.skills
        out["run_id"] = db.save_run(u["id"], out["repo"], f"Issues for {out['repo']}", "analysis", out)
        return out
    return _stream(go) if stream else _run(go)


@app.post("/api/plan")
def plan(r: Plan, u=Depends(me), x_gemini_key: str | None = Header(None), x_backend: str | None = Header(None), stream: int = 0):
    def go():
        _use_key(x_gemini_key, x_backend)
        out = core.plan(r.repo, r.issue, r.skills, _image(r), r.lang)
        title = f"#{r.issue} {out['draft'].get('pr_title', '')}"[:120]
        out["run_id"] = db.save_run(u["id"], out["repo"], title, "plan", out)
        return out
    return _stream(go) if stream else _run(go)


@app.get("/api/runs")
def runs(u=Depends(me)):
    return db.list_runs(u["id"])


@app.get("/api/runs/{rid}")
def run(rid: int, u=Depends(me)):
    r = db.get_run(u["id"], rid)
    if not r:
        raise HTTPException(404, "Not found.")
    return r


@app.delete("/api/runs/{rid}")
def delete(rid: int, u=Depends(me)):
    db.delete_run(u["id"], rid)
    return {"ok": True}


@app.post("/api/runs/{rid}/ask")
def ask(rid: int, a: Ask, u=Depends(me), x_gemini_key: str | None = Header(None), x_backend: str | None = Header(None)):
    r = db.get_run(u["id"], rid)
    if not r:
        raise HTTPException(404, "Not found.")

    def go():
        _use_key(x_gemini_key, x_backend)
        q = a.question.strip()[:1000]
        ans = core.ask(r["payload"], r["chat"], q, a.lang)
        db.set_chat(u["id"], rid, r["chat"] + [{"role": "user", "text": q}, {"role": "ai", "text": ans}])
        return {"answer": ans}
    return _run(go)


@app.post("/api/runs/{rid}/pr")
def set_pr(rid: int, a: Pr, u=Depends(me)):
    if not db.get_run(u["id"], rid):
        raise HTTPException(404, "Not found.")
    url = a.url.strip()
    if url and not PR_RE.fullmatch(url):
        raise HTTPException(400, "Paste the full pull request link, like https://github.com/owner/repo/pull/123")
    state = "open" if url else None
    db.set_pr(u["id"], rid, url or None, state)
    return {"pr_url": url or None, "pr_state": state}


@app.post("/api/runs/{rid}/pr/refresh")
def refresh_pr(rid: int, u=Depends(me)):
    r = db.get_run(u["id"], rid)
    m = PR_RE.fullmatch((r or {}).get("pr_url") or "")
    if not m:
        raise HTTPException(400, "Save your pull request link first.")

    def go():
        d = gh._get(f"/repos/{m[1]}/{m[2]}/pulls/{m[3]}")
        state = "merged" if d.get("merged") else d["state"]
        db.set_pr(u["id"], rid, r["pr_url"], state)
        return {"pr_url": r["pr_url"], "pr_state": state}
    return _run(go)


@app.get("/api/local")
def local():
    m = core.LOCAL_MODEL
    try:
        names = [x.get("name", "") for x in httpx.get(core.OLLAMA + "/api/tags", timeout=2).json().get("models", [])]
        return {"up": True, "hosted": bool(os.getenv("VERCEL")), "model": m, "has_model": any(n == m or (":" not in m and n.startswith(m + ":")) for n in names)}
    except Exception:
        return {"up": False, "hosted": bool(os.getenv("VERCEL") or os.getenv("FIRSTPR_REQUIRE_KEY")), "model": m, "has_model": False}


@app.get("/api/scoreboard")
def scoreboard():
    f = Path(__file__).resolve().parent.parent / "eval" / "results.json"
    return json.loads(f.read_text()) if f.exists() else {}


@app.get("/")
def index():
    return FileResponse(Path(__file__).parent / "web" / "index.html")
