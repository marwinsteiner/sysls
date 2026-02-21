Commit the current work with proper discipline.

Before committing:
1. Run `uv run pytest` for at minimum the affected test modules. ALL tests must pass.
2. Run `uv run ruff check src/ tests/` and fix any linting issues.
3. Run `uv run ruff format src/ tests/` to auto-format.
4. Review the diff with `git diff --stat` — if >500 lines changed, split into multiple commits.

Commit message format:
```
<layer>: <concise description>

<optional body explaining why, not what>
```

Valid layer prefixes: core, data, execution, strategy, backtest, analytics, cli, infra, docs, test

After committing:
- If this commit completes a reviewable chunk (new public API, module completion, >200 lines):
  1. Push the branch: `git push -u origin <branch>`
  2. Run `/code-review` to self-review via Task subagent
  3. Open a PR: `gh pr create --base main --title "<layer>: <description>" --body "<summary>"`
  4. Post to `#sysls-review` with the PR link
  5. Merge after review passes: `gh pr merge --squash --delete-branch`
- If this commit batch completes a milestone, post to `#sysls-announcements`
- For routine commit batches, post a brief summary to `#sysls-dev`
- Do NOT wait for human acknowledgment. Keep working.

Remember: commit early, commit often. One logical change per commit. Never accumulate uncommitted work.
