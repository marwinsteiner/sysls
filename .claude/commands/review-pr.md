Review PR #$ARGUMENTS as the Architect.

## Steps:
1. Read the PR:
```bash
gh pr view $ARGUMENTS
gh pr diff $ARGUMENTS
gh pr checks $ARGUMENTS
```

2. Review against CLAUDE.md criteria:
   - Architecture compliance (layer boundaries, event bus usage)
   - Coding conventions (type hints, async, Decimal for prices, structlog, docstrings)
   - Test coverage (corresponding test module, happy path + error cases)
   - Error handling (no bare except, meaningful exceptions from SyslsError)
   - Performance (hot paths, no blocking on event loop)

3. Spawn a Task subagent for detailed review:
> "Review this diff against CLAUDE.md conventions. Output MUST FIX / SHOULD FIX / NIT / GOOD findings."

4. Post decision:
```bash
# If good:
gh pr review $ARGUMENTS --approve --body "LGTM. <brief positive note>"
gh pr merge $ARGUMENTS --squash --delete-branch

# If needs work:
gh pr review $ARGUMENTS --request-changes --body "<structured feedback>"
```

5. Post review summary to the `#sysls-review` Slack thread for this PR.

6. After merge, verify main is green: `uv run pytest`
