"""Deterministic checks of a drafted contribution against rules extracted from CONTRIBUTING.
Zero dependencies, so the Agent Skill can ship this file as scripts/check_draft.py.
Usage: echo '{"draft":{...},"rules":{...},"issue":12}' | python checks.py"""
import json, re, shlex, sys

CONVENTIONAL = r"^(feat|fix|docs|style|refactor|perf|test|build|ci|chore|revert)(\([\w./-]+\))?!?: .+"


def check_draft(d, rules, number):
    out = []

    def add(rule, ok, note="", status=None):
        out.append({"rule": rule, "status": status or ("ok" if ok else "fail"), "note": note})

    msg, branch = d.get("commit_message", ""), d.get("branch", "")
    subject = (msg.splitlines() or [""])[0]
    body = d.get("pr_body", "")
    add("Commit subject is 72 characters or fewer", len(subject) <= 72, f"{len(subject)} characters")
    add("Branch name has no spaces or odd characters", bool(re.fullmatch(r"[\w./-]+", branch)), branch)
    add(f"PR body links issue #{number}",
        bool(re.search(rf"\b(close[sd]?|fix(e[sd])?|resolve[sd]?|refs?|related to)\b[: ]+(\S+)?#{number}\b", body, re.I)),
        f"Add 'Closes #{number}'")
    if rules.get("commit_style") == "conventional":
        add("Commit follows Conventional Commits", bool(re.match(CONVENTIONAL, subject)), subject)
    if rules.get("dco"):
        add("Commit has a DCO sign-off", "signed-off-by:" in msg.lower(), "Commit with git commit -s")
    if rules.get("branch_pattern"):
        try:
            add("Branch matches the project's naming rule", bool(re.fullmatch(rules["branch_pattern"], branch)),
                f"expected {rules['branch_pattern']}")
        except re.error:
            pass
    for s in rules.get("pr_sections") or []:
        add(f"PR body has the '{s}' section", s.lower().strip("# ") in body.lower())
    for cmd in rules.get("must_run") or []:
        add(f"Run before opening the PR: {cmd}", True, "Reminder", status="todo")
    for note in rules.get("other") or []:
        add(note, True, "Check by hand", status="todo")
    return out


def git_kit(repo, base, d, rules):
    """Copy-paste commands from fork to pull request."""
    name, br = repo.split("/")[1], shlex.quote(d.get("branch") or "my-change")
    sign = " -s" if (rules or {}).get("dco") else ""
    return f"""# 1. Fork https://github.com/{repo} on GitHub, then:
git clone https://github.com/YOUR-USERNAME/{name}.git && cd {name}
git remote add upstream https://github.com/{repo}.git
git checkout -b {br}

# 2. Make your changes, run the project's checks, then:
git add -A
git commit{sign} -F - <<'MSG'
{d.get("commit_message", "")}
MSG
git push -u origin {br}

# 3. Open the pull request:
https://github.com/{repo}/compare/{base}...YOUR-USERNAME:{d.get("branch", "my-change")}?expand=1"""


if __name__ == "__main__":
    p = json.load(sys.stdin)
    print(json.dumps(check_draft(p["draft"], p.get("rules", {}), p["issue"]), indent=2))
