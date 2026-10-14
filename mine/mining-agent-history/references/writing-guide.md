# Writing guide

Minimal rules for turning a report candidate into a skill or an instruction rule.

## Skill

- One kebab-case directory per skill, containing `SKILL.md`.
- Frontmatter has two fields:

  ```yaml
  ---
  name: deploying-to-staging
  description: Deploy the current branch to staging. Use when asked to deploy, ship to staging, or push a build for QA.
  ---
  ```

- `description` states what the skill does and when to use it, in the words a user would type. Agents choose skills from this line alone.
- Body: imperative steps in order, with exact commands, paths, and checks. Include only what the agent cannot work out itself.
- Put deterministic or repeated mechanics in `scripts/` and call them from the steps.
- Keep `SKILL.md` short. Move long reference material into `references/` and link it.
- Location: project skills in `<repo>/.claude/skills/` or `<repo>/.agents/skills/`; personal skills in `~/.claude/skills/`, `~/.codex/skills/`, or `~/.agents/skills/`.

## Instruction rule (AGENTS.md or CLAUDE.md)

- One rule per bullet, imperative: "Run `make test` before committing."
- Add the reason when it is not obvious: "Edit generated code through `make generate`: deploys regenerate `vendor/`."
- Place the rule under an existing heading when one fits. Add a heading only for a new topic.
- To strengthen an ignored rule, make it specific and checkable, and move it near the top.
- To prune, remove rules that no longer apply. A short file gets read in full.
- In projects, prefer `AGENTS.md`. Codex reads it; Claude Code reads `CLAUDE.md`, which can import it with a line `@AGENTS.md` or be a symlink to it.
