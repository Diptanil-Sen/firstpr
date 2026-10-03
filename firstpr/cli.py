import argparse, json
from . import core

ICON = {"ok": "[ok]  ", "fail": "[FAIL]", "warn": "[warn]", "todo": "[todo]"}


def main():
    ap = argparse.ArgumentParser(prog="firstpr", description="Draft your first PR for any GitHub repo with Gemma 4.")
    ap.add_argument("repo", help="GitHub URL or owner/name (or 'serve' to start the web UI)")
    ap.add_argument("--skills", default="", help="e.g. 'python, docs, react'")
    ap.add_argument("--pick", type=int, help="issue number to draft (skips the prompt)")
    ap.add_argument("--ground", action="store_true", help="add Google Search grounding")
    ap.add_argument("--local", action="store_true", help="run Gemma locally through Ollama")
    ap.add_argument("--json", action="store_true", help="print raw JSON")
    a = ap.parse_args()
    core.LOCAL.set(a.local)
    res = core.analyze(a.repo, a.skills, a.ground)
    if not a.json:
        print(f"\n{res['repo']}: {res['summary']}\n")
        for i in res["issues"]:
            print(f"  #{i['number']:<6} fit {i['fit']:>3}  {i['difficulty']:<6} {i['title']}\n          {i['why']}")
    num = a.pick or int(input("\nIssue number to draft: ").lstrip("#"))
    out = core.plan(a.repo, num, a.skills)
    if a.json:
        return print(json.dumps({"analysis": res, "plan": out}, indent=2))
    d = out["draft"]
    print(f"\n== Understanding ==\n{d.get('understanding')}\n\n== Code to read ==")
    for c in d.get("relevant_code", []):
        print(f"  {c['path']}: {c['why']}")
    print("\n== Plan ==")
    for n, s in enumerate(d.get("plan", []), 1):
        print(f"  {n}. {s}")
    print(f"\n== Git ==\nbranch: {d['branch']}\ncommit:\n{d['commit_message']}\n\n== PR ==\n{d['pr_title']}\n\n{d['pr_body']}\n\n== Checks ==")
    for c in out["checks"]:
        print(f"  {ICON.get(c['status'], '')} {c['rule']}  {c['note']}")
    print("\nTrace:\n  " + "\n  ".join(res["trace"] + out["trace"]))
