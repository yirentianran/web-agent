---
description: "Knowledge extraction rules — injected into web-agent system prompt for proactive skill creation"
---

# Knowledge Extraction & Skill Creation

## When to Create a Skill
Proactively create a skill when:
- A non-trivial error was resolved and the fix is reusable
- A debugging technique or tool combination proved effective
- A workaround for a library quirk, API limitation, or version issue was discovered
- A project convention, architecture decision, or integration pattern was established
- The user explicitly asks to save something as a skill

## When NOT to Create
Skip extraction for:
- Trivial fixes (typos, simple syntax errors)
- One-time external issues (API outages, network failures)
- Generic advice already widely known

## Extraction Workflow
When a skill-worthy insight emerges, follow these steps:

1. **Review** — Identify the core insight: what problem was solved, how, and why it works.
2. **Classify** — Tag the knowledge type:
   - `error-resolution` — root cause + fix
   - `debugging-technique` — non-obvious diagnosis method
   - `workaround` — library/API quirk mitigation
   - `project-convention` — codebase-specific rule or pattern
3. **Check duplicates** — Search `.claude/skills/` for an existing skill on the same topic. If one exists and the new insight extends it, propose appending instead of creating a new skill.
4. **Quality gate** — Before drafting, verify:
   - The insight is specific and actionable (contains concrete steps, code, or commands)
   - The insight is not already covered by an existing skill or the user's memory
   - The insight is genuinely reusable (realistic future scenarios exist)
   - If the insight fails these checks, skip extraction silently.
5. **Confirm** — Draft the skill content and ask the user: "I noticed a reusable pattern from our conversation: [brief description]. Should I save this as a skill?" Wait for the user to explicitly agree before writing anything.
6. **Write** — Use skill-creator to create the skill at `.claude/skills/<kebab-case-name>/SKILL.md`. After creation, write a `skill-meta.json` file in the same directory with `{"source": "skill-creator", "created_at": "<ISO 8601 timestamp>"}`.

## Skill File Format

---
name: kebab-case-name
description: "Under 130 characters, describing when to use this skill"
user-invocable: false
origin: auto-extracted
---

# [Descriptive Title]

**Extracted:** YYYY-MM-DD
**Context:** [When this skill applies — project, tool, or scenario]

## Problem
[The specific problem this solves — be specific]

## Solution
[The pattern, technique, or convention — include code examples when applicable]

## When to Use
[Trigger conditions that should activate this skill]

## Anti-Overwrite Rule
- Before creating a skill, check if `.claude/skills/<name>/` already exists.
- If it exists, DO NOT overwrite. Notify the user and suggest either renaming or appending to the existing skill.
