Enter Architect mode for Phase $ARGUMENTS.

Read CLAUDE.md (especially Multi-Agent Operation), check all Slack channels, then:
1. Plan Phase $ARGUMENTS tasks
2. Post task assignments to #sysls-dev
3. Spawn junior subagents for each task (they auto-isolate in worktrees)
4. Monitor #sysls-review for PRs, review with `gh pr diff/review`, merge with `gh pr merge --squash`
5. Do NOT write implementation code yourself — delegate everything to junior agents
