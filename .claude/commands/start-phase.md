Begin implementation of development phase $ARGUMENTS.

Reference the Development Phases section in CLAUDE.md for phase details.

## Pre-Flight: Check Slack for Context

Before anything else, read recent Slack messages to pick up any direction changes, decisions, or feedback from the PM:
```
mcp__slack__conversations_history(channel_id="#sysls-announcements", limit=5)
mcp__slack__conversations_history(channel_id="#sysls-blocked", limit=10)
mcp__slack__conversations_history(channel_id="#sysls-architecture", limit=10)
```

Check for any unresolved threads you're involved in:
```
mcp__slack__conversations_search_messages(search_query="phase $ARGUMENTS")
```

## Planning

Before writing any code:
1. Review CLAUDE.md thoroughly — architecture, conventions, event types, anti-patterns.
2. List the specific deliverables for this phase.
3. Identify dependencies on prior phases and verify they are complete.
4. Plan the implementation order (which files first, which depend on which).
5. Post the plan to `#sysls-dev` for visibility:
   ```
   mcp__slack__conversations_add_message(
     channel_id="#sysls-dev",
     payload="*<Agent Name>* — Starting Phase $ARGUMENTS\n\n*Deliverables:*\n<list>\n\n*Implementation order:*\n<list>\n\n*Dependencies:* <status of prerequisites>"
   )
   ```

## During Implementation

- Write tests alongside or before the implementation (TDD where practical).
- Follow all coding conventions in CLAUDE.md.
- Use the event type hierarchy as defined — don't create ad-hoc event types.
- Run the existing test suite after each major addition to ensure nothing breaks.
- Commit logically (one concern per commit) — use `/commit` workflow.
- Post progress updates to `#sysls-dev` at natural breakpoints.

## After Implementation

- Run the full test suite and fix any failures.
- Run linting (ruff) and type checking (mypy) and fix issues.
- Update CLAUDE.md if any architectural decisions were refined during implementation.
- Post a milestone announcement to `#sysls-announcements`:
  ```
  mcp__slack__conversations_add_message(
    channel_id="#sysls-announcements",
    payload=":white_check_mark: *Phase $ARGUMENTS Complete*\n<summary of what was built>\n\n*Stats:* <lines, files, test coverage>\n*Next phase:* <what Phase N+1 should expect>"
  )
  ```

Valid phases: 0 (Foundation), 1 (Data Layer), 2 (Execution: Single Venue), 3 (Strategy Framework), 4 (Backtesting), 5 (Multi-Venue), 6 (Analytics & CLI), 7 (Production Hardening).
