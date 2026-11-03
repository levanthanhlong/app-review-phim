# CLAUDE.md

Behavioral guidelines to reduce common LLM coding mistakes. Merge with project-specific instructions as needed.

**Tradeoff:** These guidelines bias toward caution over speed. For trivial tasks, use judgment.

## 1. Think Before Coding

**Don't assume. Don't hide confusion. Surface tradeoffs.**

Before implementing:

* State your assumptions explicitly. If uncertain, ask.
* If multiple interpretations exist, present them - don't pick silently.
* If a simpler approach exists, say so. Push back when warranted.
* If something is unclear, stop. Name what's confusing. Ask.

## 2. Simplicity First

**Minimum code that solves the problem. Nothing speculative.**

* No features beyond what was asked.
* No abstractions for single-use code.
* No "flexibility" or "configurability" that wasn't requested.
* No error handling for impossible scenarios.
* If you write 200 lines and it could be 50, rewrite it.

Ask yourself: "Would a senior engineer say this is overcomplicated?" If yes, simplify.

## 3. Surgical Changes

**Touch only what you must. Clean up only your own mess.**

When editing existing code:

* Don't "improve" adjacent code, comments, or formatting.
* Don't refactor things that aren't broken.
* Match existing style, even if you'd do it differently.
* If you notice unrelated dead code, mention it - don't delete it.

When your changes create orphans:

* Remove imports/variables/functions that YOUR changes made unused.
* Don't remove pre-existing dead code unless asked.

The test: Every changed line should trace directly to the user's request.

## 4. Goal-Driven Execution

**Define success criteria. Loop until verified.**

Transform tasks into verifiable goals:

* "Add validation" → "Write tests for invalid inputs, then make them pass"
* "Fix the bug" → "Write a test that reproduces it, then make it pass"
* "Refactor X" → "Ensure tests pass before and after"

For multi-step tasks, state a brief plan:

```text
1. [Step] → verify: [check]

2. [Step] → verify: [check]

3. [Step] → verify: [check]
```

Strong success criteria let you loop independently. Weak criteria ("make it work") require constant clarification.

## 5. Use CodeGraph to Save Time

**Use CodeGraph whenever it can help understand or navigate the codebase faster.**

* Prefer CodeGraph for exploring code relationships, dependencies, references, call paths, and affected areas instead of manually searching through many files.
* Use CodeGraph to identify relevant code before making changes, especially when the task involves multiple files or an unfamiliar part of the codebase.
* After modifying code, always run `codegraph sync` to update the CodeGraph index with the latest code.
* Use CodeGraph to verify the impact of changes when appropriate.
* Don't spend unnecessary time manually tracing relationships that CodeGraph can resolve directly.
* Still verify the actual source code before making important changes.

## 6. Don't Optimize for Token or Tool Cost

**Prioritize correctness and completeness over token, context, or tool usage.**

* The project is being worked on with Claude Max, so token cost is not a concern.
* Use as much context, reasoning, file inspection, CodeGraph exploration, and tool usage as necessary to complete the task correctly.
* Don't avoid useful investigation just to reduce token or tool usage.
* If additional investigation can reduce uncertainty, do it rather than guessing.
* Prefer a correct and verified solution over a shorter or cheaper one.
* If the available token budget is exhausted, stop rather than compromising correctness.

---

**These guidelines are working if:** fewer unnecessary changes in diffs, fewer rewrites due to overcomplication, more efficient codebase exploration through CodeGraph, and clarifying questions come before implementation rather than after mistakes.
