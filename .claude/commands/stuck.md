You are stuck on: $ARGUMENTS

Before escalating to the human PM, you MUST complete this checklist:

## Troubleshooting Protocol (ALL steps required)

- [ ] **Read the full error.** Parse the complete traceback. What is the root cause, not just the symptom?
- [ ] **Search the web.** Look up the exact error message. Check GitHub issues for the relevant library. Check Stack Overflow. Check the library's official docs.
- [ ] **Search Slack for prior discussion.** Use `mcp__slack__conversations_search_messages` to check if this was already discussed or solved:
  ```
  mcp__slack__conversations_search_messages(search_query="<relevant keywords>")
  ```
- [ ] **Check project context.** Re-read the relevant sections of CLAUDE.md. Has another agent changed something that affects you? Check recent commits with `git log --oneline -20`.
- [ ] **Attempt 1:** Describe what you tried and why it didn't work.
- [ ] **Attempt 2:** Describe what you tried and why it didn't work.
- [ ] **Attempt 3:** Describe what you tried and why it didn't work.
- [ ] **Isolate the problem.** Write a minimal reproduction if possible. Is this:
  - A bug in our code?
  - A bug in a third-party library?
  - An environment/configuration issue?
  - A misunderstanding of the API/contract?
- [ ] **Check if another agent owns this.** If the issue is in another agent's domain, raise it in `#sysls-dev` first.

## Escalation Decision

If ALL checklist items are complete and you're still stuck:

**Is this in another agent's domain?**
→ Post to `#sysls-dev` tagging the responsible agent. Wait for their response in the thread.

**Is this an architecture question?**
→ Post to `#sysls-architecture`. Architect agent responds. Only if Architect is also stuck, escalate to PM.

**Is this a genuine blocker that only the human PM can resolve?** (e.g., API keys, account access, third-party service down, fundamental design decision, budget/subscription issues)
→ Post to `#sysls-blocked`:
```
mcp__slack__conversations_add_message(
  channel_id="#sysls-blocked",
  payload=":sos: *<Agent Name> — BLOCKED*\n<concise description of what's blocked>\n\n*Tried:*\n1. <attempt 1>\n2. <attempt 2>\n3. <attempt 3>\n\n*Root cause:* <your best diagnosis>\n*Impact:* <what can't proceed, what can proceed in parallel>"
)
```

Then **periodically check for the PM's reply** in the thread:
```
mcp__slack__conversations_history(channel_id="#sysls-blocked", limit=10)
mcp__slack__conversations_replies(channel_id="#sysls-blocked", thread_ts="<your message ts>")
```

You may continue working on other unrelated tasks while waiting. When the PM replies, read the reply, act on it, and confirm in the thread.
