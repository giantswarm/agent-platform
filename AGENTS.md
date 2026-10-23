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
