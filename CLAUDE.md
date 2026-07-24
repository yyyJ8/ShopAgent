# CLAUDE.md

Behavioral guidelines to reduce common LLM coding mistakes, derived from Andrej Karpathy's observations on LLM coding pitfalls.

**Tradeoff:** These guidelines bias toward caution over speed. For trivial tasks, use judgment.

## 1. Think Before Coding

**Don't assume. Don't hide confusion. Surface tradeoffs.**

Before implementing:
- State your assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them - don't pick silently.
- If a simpler approach exists, say so. Push back when warranted.
- If something is unclear, stop. Name what's confusing. Ask.

## 2. Simplicity First

**Minimum code that solves the problem. Nothing speculative.**

- No features beyond what was asked.
- No abstractions for single-use code.
- No "flexibility" or "configurability" that wasn't requested.
- No error handling for impossible scenarios.
- If you write 200 lines and it could be 50, rewrite it.

Ask yourself: "Would a senior engineer say this is overcomplicated?" If yes, simplify.

## 3. Surgical Changes

**Touch only what you must. Clean up only your own mess.**

When editing existing code:
- Don't "improve" adjacent code, comments, or formatting.
- Don't refactor things that aren't broken.
- Match existing style, even if you'd do it differently.
- If you notice unrelated dead code, mention it - don't delete it.

When your changes create orphans:
- Remove imports/variables/functions that YOUR changes made unused.
- Don't remove pre-existing dead code unless asked.

The test: Every changed line should trace directly to the user's request.

## 4. Goal-Driven Execution

**Define success criteria. Loop until verified.**

Transform tasks into verifiable goals:
- "Add validation" → "Write tests for invalid inputs, then make them pass"
- "Fix the bug" → "Write a test that reproduces it, then make it pass"
- "Refactor X" → "Ensure tests pass before and after"

For multi-step tasks, state a brief plan:
```
1. [Step] → verify: [check]
2. [Step] → verify: [check]
3. [Step] → verify: [check]
```

Strong success criteria let you loop independently. Weak criteria ("make it work") require constant clarification.

## 5. Explicit Authorization Required for Code Changes

**Do not modify code unless explicitly told to. Default to read-only.**

- Default behavior: analyze, suggest, ask — never directly edit files.
- Only edit code when the user says "你直接修改", "直接改", "帮我改", "你来操作", or equivalent phrases that clearly authorize direct action.
- In auto/autonomous mode, this rule is especially strict: when in doubt, ask first rather than acting on your own.
- This rule covers project code and configuration files (`src/`, `config/`, etc.). It does not block editing of markdown docs, plan files, or other non-code content.

---

## Project-Specific Guidelines

- This is an OZON e-commerce data analysis Agent project.
- Agent does NOT touch SQL — MCP tools encapsulate all data access with fixed SQL.
- Rules for detection, LLM for attribution — know which is which.
- Config-driven: business metrics and thresholds live in YAML, not in code or prompts.
- Project planning docs are in `plan/` — read them before major decisions.
