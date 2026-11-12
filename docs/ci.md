# CI

| Pipeline | When | What it runs |
| --- | --- | --- |
| CircleCI `test-ingress-modes` | Every pull request | `make verify-all`: every render assertion, including `verify-components-charts` and `verify-examples`. |
| CircleCI `verify-release-floors` | Every release tag, before the charts are pushed | `make verify-release-floors`: the strict run of `verify-components-charts`, which refuses a range or a BOM pin that resolves to no published chart. |
| GitHub Actions `component-render-schedule` | Every six hours, and by hand | The component render below, against `main` and published releases. |

## Scheduled component render

Each `components.*.versionRange` floats within its major, so a component release changes what an already published agent-platform release installs, without any agent-platform commit. The pull request and tag pipelines run before that component release and cannot see it.

[`component-render-schedule`](../.github/workflows/component-render-schedule.yaml) closes that gap. Every six hours it checks out `main` and the latest patch of the three highest minor releases, resolves every component range the way Flux does today, pulls the charts, and renders each one with the values the meta chart forwards to it. Each ref runs its own targets, with its own values:

- `main`: `make verify-components-charts verify-examples`
- a release tag: `make verify-release-floors verify-examples`

This catches a component that refuses the forwarded values: a closed values schema that no longer accepts a key, a `fail` guard in a template, a required value the meta chart does not set. It does not catch components that render but do not work together, or anything that only shows on a running cluster.

To render other refs, run the workflow by hand with the `refs` input, for example `gh workflow run component-render-schedule.yaml -f refs="v4.175.1"`.

### When an issue opens

A red ref opens the issue *agent-platform release `<version>` chart doesn't render* (or *agent-platform main branch chart doesn't render*), labelled `team/bumblebee`. Later red runs comment on it, and the next green run closes it. The issue names the component, the version its range resolved to, and the refusal.

Installations that reconcile that release into the new component version fail. Respond in one of these ways:

- Fix the meta chart on `main`: forward values the new component accepts, or cap the range below the breaking version. Then release a patch, and when an older minor that installations still run is affected, a patch of that minor too.
- Have the component restore what it removed in a patch release, when the refusal is a breaking change inside a major.
