Perform a code review on the current branch vs main.

Run this self-review workflow for $ARGUMENTS (or the current branch if no argument given):

## Step 1: Identify what to review
```bash
git diff main..HEAD --stat
```

## Step 2: Spawn a Task subagent for independent review
Use the `Task` tool with this prompt:

> "You are a code reviewer for the sysls trading framework. Read CLAUDE.md for all coding conventions.
> Review the diff between main and the current branch (run `git diff main..HEAD`).
> Check against: coding conventions, layer separation, event contract compliance, test coverage, error handling, and performance on hot paths.
> Output a structured review in this format:
> REVIEW: [APPROVED | CHANGES REQUESTED]
> Module: <files reviewed>
> Findings:
> - [MUST FIX] (blocks merge)
> - [SHOULD FIX] (fix before phase completion)
> - [NIT] (optional)
> - [GOOD] (positive callout)"

## Step 3: Act on findings
- Fix all MUST FIX items. Re-commit.
- Fix SHOULD FIX items if quick. Otherwise note them for later.
- NITs are optional.

## Step 4: Open PR and post to Slack
```bash
gh pr create --base main --title "<layer>: <description>" --body "<review summary + what was fixed>"
```
Post the PR link and review summary to `#sysls-review`.

## Step 5: Merge (if self-review passed with no MUST FIX remaining)
```bash
gh pr merge --squash --delete-branch
```
React with ✅ on the Slack message.
