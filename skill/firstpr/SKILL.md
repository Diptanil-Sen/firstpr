---
name: firstpr
description: Plans a first open-source contribution. Use when someone gives a GitHub repository and asks where to start, which issue to pick, how to contribute, or wants a branch name, commit message and pull request description that follow the project's own contribution rules. Useful during Hacktoberfest.
license: MIT
compatibility: Needs read access to a GitHub repository (gh CLI, git clone, or web fetch). Python 3 only for the optional checker script.
metadata:
  author: firstpr
  version: "1.0"
---

# FirstPR

Turn "I want to contribute to this repo" into a ready-to-open pull request that follows the project's own rules.

## Workflow

1. **Learn the project.** Read `README`, `CONTRIBUTING*`, `.github/pull_request_template.md` and `CODE_OF_CONDUCT*`. Note commit style, branch naming, DCO sign-off, required tests and PR sections.
2. **Collect the contributor's skills.** Ask once if they were not given.
3. **Find candidate issues.** List open, unassigned issues labelled `good first issue`, `help wanted` or `hacktoberfest` (`gh issue list --label "good first issue" --search "no:assignee"`). Skip issues that need a maintainer decision or already have an open PR.
4. **Rank them** by clear scope, match to the contributor's skills, and size (should fit a weekend). Present the top 5 with fit, difficulty, why, and a first step. Let the contributor choose.
5. **Understand the code.** Read the issue and its comments, list the repo files, and read the 1-4 files that matter. Explain them in plain words. Never invent paths.
6. **Draft the contribution:** understanding, relevant code, small concrete steps, how to verify, branch name, commit message, PR title and PR body. The body must fill every section of the PR template and include `Closes #N`.
7. **Check the draft** against the rules found in step 1:
   `echo '{"draft":{"branch":"...","commit_message":"...","pr_body":"..."},"rules":{"commit_style":"conventional","dco":false,"branch_pattern":null,"pr_sections":[],"must_run":[],"other":[]},"issue":123}' | python scripts/check_draft.py`
   Fix every `fail` and rerun. Show `todo` items as a reminder list.
8. **Respect maintainers.** Comment on the issue to claim it before starting, keep the change small, and never open a PR that is spam or auto-generated noise.

## Output format

Understanding, Code to read, Plan, Branch, Commit, PR title, PR description, Checks.
