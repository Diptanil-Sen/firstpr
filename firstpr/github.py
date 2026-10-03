"""GitHub REST helpers. The three TOOLS are what Gemma can call."""
import base64, os, re
from functools import lru_cache
import httpx


def _get(path, **params):
    h = {"Accept": "application/vnd.github+json", "User-Agent": "firstpr"}
    if os.getenv("GITHUB_TOKEN"):
        h["Authorization"] = "Bearer " + os.environ["GITHUB_TOKEN"]
    r = httpx.get("https://api.github.com" + path, params=params, headers=h, timeout=20)
    if r.status_code == 401 and "Authorization" in h:  # bad token: retry without it
        h.pop("Authorization")
        r = httpx.get("https://api.github.com" + path, params=params, headers=h, timeout=20)
    if r.status_code == 404:
        raise FileNotFoundError(path)
    if r.status_code in (403, 429):
        raise RuntimeError("GitHub rate limit reached. Set GITHUB_TOKEN to raise it.")
    r.raise_for_status()
    return r.json()


def parse_repo(text):
    m = re.search(r"github\.com[/:]([\w.-]+)/([\w.-]+?)(?:\.git)?(?:[/#?]|$)", text) or \
        re.fullmatch(r"\s*([\w.-]+)/([\w.-]+)\s*", text)
    if not m:
        raise ValueError("Enter a GitHub URL such as https://github.com/owner/repo")
    return f"{m.group(1)}/{m.group(2)}"


def repo_info(repo):
    d = _get(f"/repos/{repo}")
    return {"name": d["full_name"], "description": d["description"], "language": d["language"],
            "topics": d.get("topics", []), "stars": d["stargazers_count"], "branch": d["default_branch"],
            "license": (d.get("license") or {}).get("spdx_id"), "archived": d["archived"]}


@lru_cache(maxsize=64)
def _branch(repo):
    return _get(f"/repos/{repo}")["default_branch"]


def read_file(repo, path, max_chars=6000):
    d = _get(f"/repos/{repo}/contents/{path.strip('/')}")
    if isinstance(d, list):
        return {"directory": [x["path"] for x in d]}
    return {"path": d["path"], "text": base64.b64decode(d.get("content", "")).decode("utf-8", "replace")[:max_chars]}


def list_files(repo, prefix="", limit=200):
    t = _get(f"/repos/{repo}/git/trees/{_branch(repo)}", recursive=1)
    skip = re.compile(r"(^|/)(node_modules|vendor|dist|build|\.git)/|\.(png|jpe?g|gif|svg|ico|lock|woff2?|pdf|zip)$")
    paths = [x["path"] for x in t["tree"]
             if x["type"] == "blob" and x["path"].startswith(prefix) and not skip.search(x["path"])]
    return {"files": paths[:limit], "total": len(paths)}


def _slim(i, n=400):
    return {"number": i["number"], "title": i["title"], "labels": [l["name"] for l in i["labels"]],
            "comments": i["comments"], "updated": i["updated_at"][:10],
            "body": (i.get("body") or "")[:n], "url": i["html_url"]}


def list_issues(repo, labels="", limit=30):
    items = _get(f"/repos/{repo}/issues", state="open", labels=labels, per_page=100, sort="updated")
    return [_slim(i) for i in items if "pull_request" not in i and not i.get("assignee")][:limit]


def get_issue(repo, number):
    n = int(number)
    cs = _get(f"/repos/{repo}/issues/{n}/comments", per_page=8)
    return {**_slim(_get(f"/repos/{repo}/issues/{n}"), 3500),
            "discussion": [{"by": c["user"]["login"], "text": c["body"][:500]} for c in cs]}


def first_file(repo, paths):
    for p in paths:
        try:
            return read_file(repo, p, 9000)["text"]
        except (FileNotFoundError, KeyError):
            continue
    return ""


def gather(repo):
    """Everything Gemma's long context gets to see up front."""
    issues, seen = [], set()
    for label in ("good first issue", "help wanted", "hacktoberfest", ""):
        for i in list_issues(repo, label, 30):
            if i["number"] not in seen:
                seen.add(i["number"])
                issues.append(i)
        if len(issues) >= 20:
            break
    return {"info": repo_info(repo),
            "readme": first_file(repo, ["README.md", "README.rst", "README"]),
            "contributing": first_file(repo, ["CONTRIBUTING.md", ".github/CONTRIBUTING.md", "docs/CONTRIBUTING.md", "CONTRIBUTING.rst"]),
            "template": first_file(repo, [".github/pull_request_template.md", ".github/PULL_REQUEST_TEMPLATE.md",
                                          "pull_request_template.md", "PULL_REQUEST_TEMPLATE.md"]),
            "issues": issues[:20]}


def _decl(name, desc, props, req=()):
    return {"name": name, "description": desc,
            "parameters": {"type": "object", "properties": props, "required": list(req)}}


TOOLS = [
    _decl("get_issue", "Read one issue with its discussion.", {"number": {"type": "integer"}}, ["number"]),
    _decl("list_files", "List repository files, optionally under a path prefix.", {"prefix": {"type": "string"}}),
    _decl("read_file", "Read one file (or list a directory).", {"path": {"type": "string"}}, ["path"]),
]


def call_tool(repo, name, args):
    """The repo is pinned here so the model can never wander off to another one."""
    try:
        if name == "get_issue":
            return get_issue(repo, args["number"])
        if name == "list_files":
            return list_files(repo, args.get("prefix", ""))
        if name == "read_file":
            return read_file(repo, args["path"])
        return {"error": f"unknown tool {name}"}
    except Exception as e:
        return {"error": str(e)}


CLAIM = re.compile(r"\b(i('| wi)ll (take|work)|i am working|i'm working|working on (this|it)|can i (work|take)|assign (this )?to me|i'd like to (work|take))", re.I)
AIWORD = re.compile(r"\b(ai|llm|copilot|chatgpt|generative|ai-generated|machine-generated)\b", re.I)
BAN = re.compile(r"\b(not (accept\w*|allow\w*|permit\w*)|no|ban|banned|prohibit\w*|forbid\w*|disallow\w*|reject\w*)\b", re.I)


def guard(repo, number, ctx, issue):
    """Hacktoberfest spam guard: cheap checks that save a maintainer's time."""
    out = []

    def add(rule, status, note=""):
        out.append({"rule": rule, "status": status, "note": note})
    try:
        prs = _get("/search/issues", q=f"repo:{repo} is:pr is:open {number}")["items"]
        prs = [p for p in prs if re.search(rf"#{number}\b", (p.get("body") or "") + p["title"])]
        add("No open pull request already targets this issue", "fail" if prs else "ok", f"See #{prs[0]['number']}" if prs else "")
    except Exception:
        add("Check for competing pull requests", "todo", "Search failed; look through the issue by hand")
    claims = [d for d in issue.get("discussion", []) if CLAIM.search(d["text"])]
    add("Nobody has claimed the issue in the comments", "warn" if claims else "ok", f"{claims[0]['by']} may already be on it" if claims else "")
    tagged = "hacktoberfest" in ctx["info"].get("topics", []) or "hacktoberfest" in [l.lower() for l in issue.get("labels", [])]
    add("Repository takes part in Hacktoberfest", "ok" if tagged else "warn", "" if tagged else "No hacktoberfest topic or label, so your PR may not count")
    text = (ctx.get("contributing", "") + "\n" + ctx.get("readme", "")).splitlines()
    pol = next((l.strip()[:200] for l in text if AIWORD.search(l) and (BAN.search(l) or "disclos" in l.lower())), None)
    add("Project's AI-contribution policy", ("fail" if BAN.search(pol) else "warn") if pol else "ok", pol or "No AI rule found in README or CONTRIBUTING")
    return out
