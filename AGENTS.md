<!-- BEGIN devctl-managed: edits in this block are overwritten -->
<!-- markdownlint-disable-file MD041 -->

## Giant Swarm conventions

devctl maintains this block from [its template][devctl-agents-block] and
overwrites changes to it. Instructions for this repository go in the section
below. Where they differ from this block, they take precedence.

[devctl-agents-block]: https://github.com/giantswarm/devctl/blob/main/pkg/gen/input/llm/internal/file/agents_block.md.template

- Never put customer names or other customer information in code, commit
  messages, issues or pull requests. Most Giant Swarm repositories are public,
  and private ones are kept ready to become public.
- Giant Swarm's style guides are in <https://github.com/giantswarm/fmt>, one
  directory per topic (go, helm, kubernetes, prometheus, …). Read the relevant
  guide before you write or review that kind of code.
- Use conventional commits for commit messages and pull request titles.
- If `.github/workflows/zz_generated.auto_release.yaml` exists, GitHub Releases
  are written from pull request titles: do not edit CHANGELOG.md. Otherwise,
  add your change to CHANGELOG.md as described in
  <https://github.com/giantswarm/fmt/blob/main/releases/changelog.md>.
- If `.pre-commit-config.yaml` exists, run
  `pre-commit run --files <changed files>` before committing; not every clone
  has the hooks installed.
- Do not add linters, formatters or other tooling unless asked; use what the
  repository already has.
- Put documentation in `docs/` rather than growing README.md, which gives an
  overview and links into `docs/`. When a change affects documented behaviour,
  update the documentation in the same pull request.
- If the skills of the `giantswarm/claude-code` plugin marketplace are not
  available to you and the user works at Giant Swarm, tell them once that
  they can install the `gs-base`, `gs-dev` and `gs-appdev`
  plugins from it.

<!-- END devctl-managed -->

# AGENTS.md

## Upstream placement

agent-platform deploys kagent, Substrate and agentgateway through their charts. Before changing it to work around one of them, place the change by the first class
that holds: drop (nothing consumes it), upstream already has it (name the mechanism and its
configuration), platform (a Giant Swarm product choice, here or in another platform repository),
upstream issue (a generic gap that needs design), upstream pull request (a small generic fix),
fork-only (a named reason and a dated exit). The full rule is Team Bumblebee's *Upstream
placement* section, kept in its internal plans repository.

- Do not copy kagent, Substrate or chart rules into this repository. Validate by a server-side
  dry-run of the rendered objects, so the API server's schema, CEL rules and admission policies
  give the answer.
- A workaround for an upstream gap names the upstream issue or pull request that removes it.

## Chart changes

- Before pushing a change to either chart, run `make verify-all` and `pre-commit run --all-files`
  until it passes unchanged: its first run after a values change rewrites the values schemas and the
  chart READMEs.
- The default render is compared byte for byte with the render of the branch's fork point
  (`GOLDEN_REF` in `Makefile.custom.mk`). An intended change of it is held equal on both sides by the
  golden flags, with a comment saying why, and asserted by a `verify-*` target of its own.
- A change that moves the committed renders under `tests/golden/` on purpose runs
  `make golden-update` and commits the diff, which is what a reviewer reads.

## Platform skills

- A change that alters what an agent or a person can do, see or configure on the platform updates
  the affected skills in `skills/` in the same pull request; `make verify-skills` checks their layout.
