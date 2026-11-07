# Changelog

The changes of each release are in its notes on GitHub Releases:
https://github.com/giantswarm/agent-platform/releases

Pending breaking changes are listed below.

## [Unreleased]

### Changed

- **Breaking:** Organization credential UI configuration uses `agent-manager.credentials.uiURL` with an `{operation}` placeholder. `agent-manager.devPortal` is rejected.

### Fixed

- Organization GitOps configuration in `agent-manager.tenants.commit` reaches compatible manager releases.
- Organization namespace erasure admits bulk RoleBinding deletion while preserving admin bindings in active namespaces.
