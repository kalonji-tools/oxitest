# Issue tracker: GitHub

Issues and PRDs for this repo live as GitHub issues. Use the `gh` CLI for all operations.

## Conventions

- **Create an issue**: `gh issue create --title "..." --body-file <path>`. Write the body to a file first; never pass it as an inline shell string. Bash command-substitutes backticked words before `gh` sees them, and one issue was created with two terms **silently missing** — visible only as `command not found` on stderr, after the URL had already printed. A heredoc is only safe with a **quoted** delimiter (`<<'EOF'`); an unquoted one interpolates exactly the same way.
- **Read an issue**: `gh issue view <number> --comments`, filtering comments by `jq` and also fetching labels.
- **List issues**: `gh issue list --state open --limit 500 --json number,title,body,labels,comments --jq '[.[] | {number, title, body, labels: [.labels[].name], comments: [.comments[].body]}]'` with appropriate `--label` and `--state` filters. **`--limit` is not optional** — it defaults to 30, and this recipe without it returned 30 of 46 open issues, so a triage sweep silently skips the rest.
- **Comment on an issue**: `gh issue comment <number> --body "..."`
- **Apply / remove labels**: `gh issue edit <number> --add-label "..."` / `--remove-label "..."`
- **Close**: `gh issue close <number> --comment "..."`

Infer the repo from `git remote -v` — `gh` does this automatically when run inside a clone.

## When a skill says "publish to the issue tracker"

Create a GitHub issue.

## When a skill says "fetch the relevant ticket"

Run `gh issue view <number> --comments`.
