"""FirstPR brain: Gemma 4 via the Gemini API (long context, thinking, function calling, grounding)."""
import base64, contextvars, json, os, re, time
import httpx
from google import genai
from google.genai import types
from . import github as gh
from .checks import check_draft, git_kit

MODEL = os.getenv("FIRSTPR_MODEL", "gemma-4-26b-a4b-it")
_clients, _CTX = {}, {}
KEY = contextvars.ContextVar("key", default=None)  # per-request Gemini key (bring your own key)
LOCAL = contextvars.ContextVar("local", default=False)  # True: run Gemma on this machine through Ollama
OLLAMA = os.getenv("OLLAMA_HOST", "http://localhost:11434").rstrip("/")
LOCAL_MODEL = os.getenv("FIRSTPR_LOCAL_MODEL", "gemma4:e4b")
PROGRESS = contextvars.ContextVar("progress", default=None)  # callback that streams steps to the browser


def _emit(msg):
    cb = PROGRESS.get()
    if cb:
        cb(msg)


class Trace(list):
    """A trace that also reports every step live."""
    def append(self, msg):
        super().append(msg)
        _emit(msg)


def _local_chat(messages, tools=None, think=True, system=None):
    """One call to a local Gemma through Ollama's /api/chat (supports tools, thinking and images)."""
    body = {"model": LOCAL_MODEL, "stream": False, "think": think,
            "messages": ([{"role": "system", "content": system}] if system else []) + messages,
            "options": {"num_ctx": int(os.getenv("FIRSTPR_NUM_CTX", "16384")), "temperature": 0.3}}
    if tools:
        body["tools"] = [{"type": "function", "function": t} for t in tools]
    try:
        r = httpx.post(OLLAMA + "/api/chat", json=body, timeout=900)
        if r.status_code == 400 and "think" in r.text.lower():  # model without thinking support
            body.pop("think")
            r = httpx.post(OLLAMA + "/api/chat", json=body, timeout=900)
    except httpx.ConnectError:
        raise RuntimeError(f"Could not reach Ollama at {OLLAMA}. Install it from ollama.com, run `ollama pull {LOCAL_MODEL}` and keep it running.")
    if r.status_code == 404:
        raise RuntimeError(f"Model {LOCAL_MODEL} is not installed. Run: ollama pull {LOCAL_MODEL}")
    r.raise_for_status()
    return r.json()["message"]


def _lang(lang):
    return ("Write every explanatory text field (summary, why, first_step, understanding, plan steps, tests, risks, relevant_code why) "
            "in simple Bengali (বাংলা). Keep code, file paths, branch names, commit messages, PR titles, PR descriptions, "
            "claim comments and JSON keys in English.") if lang == "bn" else ""


def _generate(contents, think="high", **cfg):
    k = KEY.get()
    if k not in _clients:
        if len(_clients) > 40:
            _clients.clear()
        _clients[k] = genai.Client(api_key=k) if k else genai.Client()  # env GEMINI_API_KEY otherwise
    config = types.GenerateContentConfig(thinking_config=types.ThinkingConfig(thinking_level=think), **cfg)
    for attempt in range(6):
        try:
            return _clients[k].models.generate_content(model=MODEL, contents=contents, config=config)
        except Exception as e:
            s = str(e)
            if attempt == 5 or not any(c in s for c in ("429", "500", "503")):
                raise
            m = re.search(r"retry in ([\d.]+)s", s)  # the API says how long to wait
            wait = min(float(m.group(1)) + 2, 65) if m else 2 ** (attempt + 1)
            _emit(f"Google's free-tier limit reached, waiting {int(wait)}s")
            time.sleep(wait)


def _json(text):
    for m in re.finditer(r"\{", text):
        try:
            return json.JSONDecoder().raw_decode(text[m.start():])[0]
        except ValueError:
            continue
    raise ValueError("Gemma did not return JSON")


def _ask(prompt, **cfg):
    for _ in range(2):
        txt = _local_chat([{"role": "user", "content": prompt}])["content"] if LOCAL.get() else (_generate(prompt, **cfg).text or "")
        try:
            return _json(txt)
        except ValueError:
            prompt += "\n\nReturn ONLY a valid JSON object."
    raise RuntimeError("Gemma returned unparseable output twice. Try again.")


def _agent(repo, prompt, system, trace, max_steps=12, image=None):
    """Function-calling loop: Gemma decides which GitHub tools to call, we execute them."""
    if LOCAL.get():
        return _agent_local(repo, prompt, system, trace, max_steps, image)
    parts = [types.Part(text=prompt)]
    if image:
        parts.append(types.Part.from_bytes(data=image[0], mime_type=image[1]))
    contents = [types.Content(role="user", parts=parts)]
    cfg = dict(system_instruction=system, tools=[types.Tool(function_declarations=gh.TOOLS)],
               automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True))
    for step in range(max_steps):
        if step == 3 and "tools" in cfg:  # enough exploring: force the final answer
            cfg.pop("tools"); cfg.pop("automatic_function_calling")
            contents[-1].parts.append(types.Part(text="Stop exploring. Reply now with ONLY the JSON object."))
        r = _generate(contents, **cfg)
        if not r.candidates or not r.candidates[0].content:
            raise RuntimeError("Gemma returned no content (possibly filtered). Try again.")
        msg = r.candidates[0].content
        calls = [p.function_call for p in (msg.parts or []) if p.function_call]
        if calls:
            replies = []
            for c in calls:
                args = dict(c.args or {})
                trace.append(f"Gemma called {c.name}({', '.join(f'{k}={v!r}' for k, v in args.items())})")
                res = json.dumps(gh.call_tool(repo, c.name, args), default=str)[:6000]
                replies.append(types.Part.from_function_response(name=c.name, response={"result": res}))
            contents += [msg, types.Content(role="user", parts=replies)]
            continue
        try:
            return _json(r.text or "")
        except ValueError:
            contents += [msg, types.Content(role="user", parts=[types.Part(text="Reply with ONLY the JSON object.")])]
    raise RuntimeError("Gemma hit the step limit before finishing. Try again.")


def _agent_local(repo, prompt, system, trace, max_steps, image):
    """Same tool loop as _agent, but against a local Gemma."""
    user = {"role": "user", "content": prompt}
    if image:
        user["images"] = [base64.b64encode(image[0]).decode()]
    msgs, tools = [user], gh.TOOLS
    for step in range(max_steps):
        if step == 3 and tools:
            tools = None
            msgs.append({"role": "user", "content": "Stop exploring. Reply now with ONLY the JSON object."})
        m = _local_chat(msgs, tools, system=system)
        calls = m.get("tool_calls") or []
        if calls:
            msgs.append(m)
            for c in calls:
                f = c["function"]
                args = f.get("arguments") or {}
                args = json.loads(args) if isinstance(args, str) else args
                trace.append(f"Gemma called {f['name']}({', '.join(f'{k}={v!r}' for k, v in args.items())})")
                msgs.append({"role": "tool", "tool_name": f["name"],
                             "content": json.dumps(gh.call_tool(repo, f["name"], args), default=str)[:6000]})
            continue
        try:
            return _json(m.get("content") or "")
        except ValueError:
            msgs += [m, {"role": "user", "content": "Reply with ONLY the JSON object."}]
    raise RuntimeError("The local model hit the step limit. Try a larger model such as gemma4:12b.")


def _ctx(repo):
    if repo not in _CTX:
        _CTX[repo] = gh.gather(repo)
    return _CTX[repo]


def _ground(info):
    r = _generate(f"In under 120 words: what should a first-time contributor know about contributing to "
                  f"{info['name']} right now (recent activity, maintainer expectations)?", tools=[{"google_search": {}}])
    try:
        srcs = [c.web.uri for c in r.candidates[0].grounding_metadata.grounding_chunks][:3]
    except Exception:
        srcs = []
    return (r.text or "") + ("\nSources: " + ", ".join(srcs) if srcs else "")


def analyze(url, skills, ground=False, lang="en"):
    repo, trace = gh.parse_repo(url), Trace()
    ctx = _ctx(repo)
    if ctx["info"]["archived"]:
        raise ValueError("This repository is archived and cannot accept pull requests.")
    if not ctx["issues"]:
        raise ValueError("No open, unassigned issues found. Try another repository.")
    trace.append(f"Fetched README, CONTRIBUTING and {len(ctx['issues'])} open unassigned issues")
    if ground and not LOCAL.get():  # Google Search grounding needs the Gemini API
        ctx["grounding"] = _ground(ctx["info"])
        trace.append("Grounded with Google Search")
    trace.append("Gemma is ranking the issues for your skills")
    data = _ask(f"""You help newcomers land a first open-source pull request during Hacktoberfest.
CONTRIBUTOR SKILLS: {skills or 'not given'}
REPO: {json.dumps(ctx['info'])}
LIVE CONTEXT: {ctx.get('grounding', 'none')}
README:\n{ctx['readme'][:4000]}
CONTRIBUTING:\n{ctx['contributing'][:4000] or 'none found'}
OPEN UNASSIGNED ISSUES:\n{json.dumps(ctx['issues'])}

Pick the 5 best issues for this contributor: clear scope, matches their skills, finishable in a weekend,
not blocked on a maintainer decision. Use only issue numbers listed above.
Return ONLY JSON: {{"summary":"2 sentences: what the project is and how welcoming it looks",
"issues":[{{"number":0,"fit":"integer 0 to 100","difficulty":"easy|medium|hard","why":"","first_step":""}}]}}
{_lang(lang)}""")
    by_num = {i["number"]: i for i in ctx["issues"]}
    ranked = [{**i, **by_num[i["number"]]} for i in data.get("issues", [])
              if isinstance(i.get("number"), int) and i["number"] in by_num]
    for i in ranked:
        try:
            f = float(i.get("fit") or 0)
        except (TypeError, ValueError):
            f = 0
        i["fit"] = round(f * 100) if f <= 1 else round(f)
    trace.append(f"Gemma ranked {len(ranked)} issues by fit")
    return {"repo": repo, "summary": data.get("summary", ""), "issues": ranked, "trace": trace, "info": ctx["info"]}


def _rules(ctx):
    if "rules" not in ctx:
        ctx["rules"] = _ask(f"""Extract the contribution rules from these files.
CONTRIBUTING:\n{ctx['contributing'][:4000] or 'none'}\nPR TEMPLATE:\n{ctx['template'][:2500] or 'none'}
Return ONLY JSON: {{"commit_style":"conventional|imperative|free","dco":false,"branch_pattern":null or a Python regex,
"pr_sections":["headings the PR template requires"],"must_run":["tests/lint commands to run before a PR"],
"other":["any other explicit rule, max 4"]}}. Use null or [] when the files say nothing.""")
    return ctx["rules"]


def plan(url, number, skills, image=None, lang="en"):
    repo, trace = gh.parse_repo(url), Trace()
    ctx = _ctx(repo)
    rules = _rules(ctx)
    trace.append("Extracted the repo's contribution rules")
    issue = gh.get_issue(repo, number)
    allf = gh.list_files(repo, limit=100000)["files"]
    files = allf[:150]
    guard = gh.guard(repo, number, ctx, issue)
    trace.append(f"Spam guard: {sum(g['status'] != 'ok' for g in guard)} warning(s)")
    trace.append(f"Fetched issue #{number} and the repo file list")
    if image:
        trace.append("Gemma looked at your screenshot")
    trace.append("Gemma is reading the code and drafting your PR")
    draft = _agent(repo, f"""Contributor skills: {skills or 'not given'}. Target issue: #{number} in {repo}.{' A screenshot from the contributor is attached; use it to understand the problem.' if image else ''}
CONTRIBUTING:\n{ctx['contributing'][:3500] or 'none'}\nPR TEMPLATE:\n{ctx['template'][:2000] or 'none'}
RULES: {json.dumps(rules)}

ISSUE: {json.dumps(issue)[:3500]}\nFILES: {json.dumps(files)}\n\nCall read_file on the 1-3 most relevant files (several in ONE step), then return ONLY JSON:
{{"understanding":"what the issue needs, in plain words","relevant_code":[{{"path":"","why":""}}],
"plan":["small concrete steps"],"tests":"how to verify","branch":"","commit_message":"subject + blank line + body",
"pr_title":"","pr_body":"fills every section of the PR template and includes 'Closes #{number}'","risks":"","claim_comment":"short polite comment to post on the issue asking to be assigned, naming your planned approach"}}""",
        "You are a careful senior maintainer coaching a first-time contributor. Ground every claim in files you read. "
        "Follow the repo's rules exactly. Never invent file paths. " + _lang(lang), trace, image=image)
    checks = check_draft(draft, rules, number)
    fails = [c for c in checks if c["status"] == "fail"]
    before = len(fails)
    if fails:
        trace.append(f"{len(fails)} check(s) failed, asking Gemma to fix the draft")
        fixed = _ask("Fix this contribution draft so it passes these failed checks. Change nothing else. "
                     f"Return ONLY the full JSON object.\nFAILED: {json.dumps(fails)}\nDRAFT: {json.dumps(draft)}")
        draft = {**draft, **{k: v for k, v in fixed.items() if k in draft}}
        checks = check_draft(draft, rules, number)
    trace.append("Checked the draft against the repo's rules")
    paths = [c.get("path") for c in draft.get("relevant_code", []) if isinstance(c, dict)]
    stats = {"before": before, "after": sum(c["status"] == "fail" for c in checks), "paths": len(paths),
             "paths_ok": sum(p in set(allf) for p in paths), "tool_calls": sum(t.startswith("Gemma called") for t in trace)}
    return {"repo": repo, "issue": number, "title": issue["title"], "draft": draft, "checks": checks, "rules": rules,
            "guard": guard, "kit": git_kit(repo, ctx["info"]["branch"], draft, rules), "stats": stats, "trace": trace}


def ask(payload, history, question, lang="en"):
    """Mentor chat about a saved analysis or draft."""
    prompt = f"""You are a patient open-source mentor helping a first-time contributor. Answer briefly and concretely
in plain text. If you are unsure, say so instead of guessing.
CONTEXT: {json.dumps(payload)[:9000]}
CHAT SO FAR: {json.dumps(history[-8:])}
QUESTION: {question}
{'Answer in simple Bengali (বাংলা), keeping code and commands in English.' if lang == 'bn' else ''}"""
    txt = _local_chat([{"role": "user", "content": prompt}], think=False)["content"] if LOCAL.get() else (_generate(prompt, think="minimal").text or "")
    return txt.strip() or "I could not answer that. Try rephrasing."
