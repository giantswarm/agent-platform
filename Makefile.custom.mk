# Custom targets, auto-included by the root Makefile's `include Makefile.*.mk`.
# Lives outside the devctl-generated Makefile.gen.app.mk so it survives
# regeneration. DO NOT move these targets into the generated file.

##@ Custom

CHART_DIR ?= helm/agent-platform
CONNECTIVITY_DIR ?= helm/agent-platform-connectivity

# The API groups a Giant Swarm management cluster serves and the cluster-shape
# knobs detect (kyvernoPolicies.enabled, networkPolicy.flavor,
# global.observability.metrics.serviceMonitor.enabled, dicebear.route.enabled,
# agentSandbox.podSecurity.enabled default to `auto`): Kyverno, Cilium,
# prometheus-operator, Gateway API, Envoy Gateway. `helm template` alone serves
# Helm's built-in set, i.e. renders the vanilla shape; the assertions below that
# expect the fleet shape pass these. verify-auto covers the resolution itself.
FLEET_APIS := --api-versions kyverno.io/v1 --api-versions cilium.io/v2 --api-versions monitoring.coreos.com/v1 --api-versions gateway.networking.k8s.io/v1 --api-versions gateway.envoyproxy.io/v1alpha1 --api-versions autoscaling.k8s.io/v1
# parentRefs[0].name satisfies the all-modes ingress guard so a single guard is
# isolated under test, and the fleet's API groups are served so the fleet shape
# renders. Neither chart has subcharts anymore, so no `helm dependency build`
# and no subchart-fail quieting is needed.
# kagent.harness.snapshotLocation is required with kagent on (the meta chart's
# agent-platform.validateSubstrate); set on every render so a target can turn
# kagent on without repeating it. The connectivity chart (and GOLDEN_REF's)
# accepts the key in its open kagent block.
VM := --set ingress.parentRefs[0].name=x --set kagent.harness.snapshotLocation=s3://ci-agent-snapshots/agents $(FLEET_APIS)
# Agent Substrate on, as the meta chart forwards it (the two Substrate entries
# follow components.kagent there; the connectivity chart reads the roster).
SUBSTRATE_ON := --set components.substrate.enabled=true --set components.substrate-crds.enabled=true

# The components that own a kyverno.io object: agentSandbox's pod-security
# ClusterPolicy is the one left with kagent on and Substrate off — kagent owns
# no ClusterPolicy since kagent API v2 (agents run as Substrate actors, so no
# Agent CR, per-agent Deployment or config Secret is left to mutate) and no
# PolicyException either (the v1alpha2 agent Deployments' seccomp exception went
# with them; its successor is Substrate's substrate-workers). With Agent
# Substrate on, its four PolicyExceptions join (verify-kyverno holds their rule
# lists to the workloads).
KYVERNO_ALL := $(VM) --set components.kagent.enabled=true --set components.agent-sandbox.enabled=true
# The golden render deliberately uses the kubernetes networkPolicy flavor: the
# cilium flavor's CNPG section is now gated on postgres.enabled, the one intended
# render change (verify-global asserts that gate both ways). It also leaves
# agentSandbox off (see the 1.1.x note about its dropped resource-policy).
# GOLDEN_REF is the ref the rest of the default render must still match byte for
# byte; GOLDEN_REF= (empty) opts out for a clone that has no such ref. The
# kagent-flux tenant identity is the other intended change: both sides render
# with it off (a chart that predates the key ignores it, the kagent block is
# additionalProperties: true), and verify-identity asserts it both ways. The
# third intended change is the discovery opt-out label on the muster
# RemoteMCPServer, derived from muster's OAuth toggle: both sides render with
# that toggle off (a chart that predates the derivation reads nothing from it),
# and verify-kagent-discovery asserts the label both ways. The fourth is the
# kagent controller metrics Service's selector (#305: kagent's own instance
# label, not this release's): both sides render with the kagent ServiceMonitor
# off — the metrics Service is gated on it — and verify-global asserts the
# selector.
# kagent.namespaceOverride=default (the release namespace of `helm template t`) drops the kagent Namespace object from both renders: this branch
# keeps it (helm.sh/resource-policy: keep), an intended difference to GOLDEN_REF; every other kagent object renders alike on both sides.
# The fifth intended change (connectivity 4.0, kagent API v2): the shared muster RemoteMCPServer and the two kagent declarative-agent
# ClusterPolicies are gone — the Generic agent chart 1.x renders one RemoteMCPServer per agent, and there is no Agent CR, per-agent
# Deployment or config Secret left to mutate. There is no toggle to render both sides without them, so the golden side is compared with
# those three objects removed (GOLDEN_RETIRED) and everything else byte for byte; verify-kagent-discovery and verify-modes assert the absence.
GOLDEN_RETIRED := python3 -c 'import sys; d=open(sys.argv[1]).read().split("\n---\n"); keep=[x for x in d if not (("kind: RemoteMCPServer\n" in x and "\n  name: muster\n" in x) or ("kind: ClusterPolicy\n" in x and ("kagent-declarative-pod-security\n" in x or "kagent-srt-settings\n" in x)))]; out="\n---\n".join(keep); open(sys.argv[1],"w").write(out if out.endswith("\n") else out+"\n")'
# The sixth intended change is the kagent controller's ingress admission (#345: the
# data-plane pods and the UI only, in both flavors): both sides render with the
# network policies off, and verify-kagent-route asserts the admission in both
# flavors (verify-kagent-netpol, verify-managers and verify-llm-routing cover the
# other policies).
# The seventh intended change (giantswarm/agent-platform#439): the muster-valkey
# PodDisruptionBudget this chart renders by default (templates/valkey/pdb.yaml).
# Both sides render with valkey.podDisruptionBudget.enabled=false (a chart that
# predates the key ignores it, the valkey block is additionalProperties: true),
# and verify-disruption asserts the budget on, off and inert.
# The eighth (giantswarm/agent-platform#472): the Substrate worker pool's
# PodDisruptionBudget this chart renders by default in the kagent namespace
# (templates/kagent/workerpool-pdb.yaml). Both sides render with
# kagent.substrateWorkerPool.podDisruptionBudget.enabled=false (the kagent
# block is additionalProperties: true), and verify-workerpool asserts the
# budget on, off and its selector.
# The ninth (giantswarm/agent-platform#329): components.model-manager is on by
# default with no backend, so the default render carries its wiring. Both sides
# render with the component off (a chart that predates the default accepts the
# key), and verify-managers asserts the default shape and the static forms.
# The tenth (giantswarm/agent-platform#455 follow-up): the kagent controller
# VPA's memory cap moves from 480Mi to 1280Mi. Both sides render with the new
# cap (the key exists on both sides), and verify-kagent-vpa asserts the default.
KYVERNO_GOLDEN := $(VM) --set components.kagent.enabled=true --set networkPolicy.enabled=false --set networkPolicy.flavor=kubernetes --set kagent.fluxServiceAccountName= --set muster.muster.oauth.server.enabled=false --set kagent.serviceMonitor.enabled=false --set kagent.namespaceOverride=default --set valkey.podDisruptionBudget.enabled=false --set kagent.substrateWorkerPool.podDisruptionBudget.enabled=false --set components.model-manager.enabled=false --set kagent.controller.vpa.maxAllowed.memory=1280Mi
# GOLDEN_REF's chart reads the same component toggle, so both sides render alike.
KYVERNO_GOLDEN_REF := $(KYVERNO_GOLDEN)
GOLDEN_REF ?= origin/main
# The postgres golden of verify-wiring (both flavours against GOLDEN_REF): model-manager
# is on by default with no backend (giantswarm/agent-platform#329), an intended difference
# held equal on both sides — a chart that predates the default accepts the key. Empty this
# once GOLDEN_REF carries the line.
# The platform's own board ConfigMaps are dropped from a golden render by name,
# never by a value: GOLDEN_REF's schema has no `dashboards` key, so --set on it
# fails the render outright and every document then reads as added
# (giantswarm/giantswarm#36711). One name per board; the list goes with the line.
DASHBOARDS_GOLDEN_DROP := agent-platform-connectivity-dashboard-overview agent-platform-connectivity-dashboard-usage-by-person agent-platform-connectivity-dashboard-klaus-gateway agent-platform-connectivity-dashboard-valkey agent-platform-connectivity-dashboard-llm-usage
# Drop those documents from a rendered manifest in place, by metadata.name, and
# keep the leading document separator whatever was dropped — a stripped first
# document would otherwise read as a one-line diff of its own.
define drop_dashboards
	@python3 -c 'import re,sys; ex=set(sys.argv[2].split()); docs=open(sys.argv[1]).read().split("\n---\n"); keep=[d for d in docs if not (re.search(r"^  name: (\S+)", d, re.M) and re.search(r"^  name: (\S+)", d, re.M).group(1) in ex)]; out="\n---\n".join(keep).lstrip("-\n"); open(sys.argv[1],"w").write("---\n"+out.rstrip("\n")+"\n")' $(1) "$(DASHBOARDS_GOLDEN_DROP)"
endef
WIRING_PG_GOLDEN_HOLD := --set components.model-manager.enabled=false
# Objects the 4.0 line changes on purpose, dropped from BOTH renders before the
# golden diff (by metadata.name): the v1alpha2 agent Deployments' seccomp
# PolicyException is gone with them, the kagent controller's ingress policy
# no longer admits the app: kagent agent pods (the actors arrive through Agent
# Substrate's egress gateway — templates/substrate/netpol.yaml), and `kagent`
# is the platform Harness the 4.0 connectivity chart renders whenever kagent is
# on (templates/kagent/harness.yaml — a new object with no 3.x counterpart; its
# shape is asserted by verify-kagent-harness / verify-kagent-crds). Empty this
# list once GOLDEN_REF carries the line.
GOLDEN_EXCLUDE := kagent-declarative-seccomp agent-platform-connectivity-kagent-controller-ingress kagent $(DASHBOARDS_GOLDEN_DROP)
# Any reference is enough: the assertions read the rendered exception, not the image.
PGVECTOR_IMG := gsoci.azurecr.io/giantswarm/pgvector:0.8.2-18-bookworm


.PHONY: verify-modes
verify-modes: ## Assert ingress.mode fail-guards fire (connectivity chart owns the wiring + guards).
	@echo "====> $@ ($(CONNECTIVITY_DIR))"
	@echo "--> muster-direct with no Gateway named anywhere must fail"
	@if helm template t $(CONNECTIVITY_DIR) --set ingress.mode=muster-direct >/tmp/vm-parents.out 2>&1; then \
		echo "FAIL: empty-parentRefs guard did not fire (render succeeded)"; cat /tmp/vm-parents.out; exit 1; \
	elif ! grep -q "no public Gateway for ingress.parentRefs" /tmp/vm-parents.out; then \
		echo "FAIL: empty-parentRefs check failed for the wrong reason"; cat /tmp/vm-parents.out; exit 1; \
	else echo "ok: empty-parentRefs guard"; fi
	@echo "--> agentgateway-direct must be blocked with the DCR message"
	@if helm template t $(CONNECTIVITY_DIR) $(VM) --set ingress.mode=agentgateway-direct >/tmp/vm-direct.out 2>&1; then \
		echo "FAIL: direct-mode guard did not fire (render succeeded)"; cat /tmp/vm-direct.out; exit 1; \
	elif ! grep -q "requires a DCR-capable IdP" /tmp/vm-direct.out; then \
		echo "FAIL: direct-mode failed for the wrong reason"; cat /tmp/vm-direct.out; exit 1; \
	else echo "ok: direct blocked"; fi
	@echo "--> agentgateway-muster + viaMuster:false must fail"
	@if helm template t $(CONNECTIVITY_DIR) $(VM) --set ingress.mode=agentgateway-muster --set components.agentgateway.enabled=true --set components.agent-platform-mcps.enabled=true --set agent-platform-mcps.agentgateway.viaMuster=false >/tmp/vm-via.out 2>&1; then \
		echo "FAIL: viaMuster guard did not fire"; exit 1; \
	elif ! grep -q "viaMuster=true" /tmp/vm-via.out; then \
		echo "FAIL: viaMuster check failed for the wrong reason"; cat /tmp/vm-via.out; exit 1; \
	else echo "ok: viaMuster guard"; fi
	@echo "--> bogus mode must fail"
	@if helm template t $(CONNECTIVITY_DIR) $(VM) --set ingress.mode=bogus >/tmp/vm-enum.out 2>&1; then \
		echo "FAIL: enum guard did not fire"; exit 1; \
	elif ! grep -q "must be one of" /tmp/vm-enum.out; then \
		echo "FAIL: enum check failed for the wrong reason"; cat /tmp/vm-enum.out; exit 1; \
	else echo "ok: enum guard"; fi
	@echo "--> agentgateway-muster + components.agentgateway.enabled:false must fail"
	@if helm template t $(CONNECTIVITY_DIR) $(VM) --set ingress.mode=agentgateway-muster --set components.agentgateway.enabled=false >/tmp/vm-dep.out 2>&1; then \
		echo "FAIL: dep-condition guard did not fire"; exit 1; \
	elif ! grep -q "components.agentgateway.enabled must be true" /tmp/vm-dep.out; then \
		echo "FAIL: dep-condition check failed for the wrong reason"; cat /tmp/vm-dep.out; exit 1; \
	else echo "ok: dep-condition guard"; fi
	@echo "--> positive: a valid agentgateway-muster config must render"
	@if helm template t $(CONNECTIVITY_DIR) $(VM) --set ingress.mode=agentgateway-muster --set components.agentgateway.enabled=true --set components.agent-platform-mcps.enabled=true --set agent-platform-mcps.agentgateway.viaMuster=true >/dev/null 2>&1; then \
		echo "ok: valid config renders"; \
	else echo "FAIL: a valid agentgateway-muster config was rejected"; exit 1; fi
	@echo "--> agentSandbox.podSecurity.enabled with no kyverno policies must fail"
	@if helm template t $(CONNECTIVITY_DIR) $(KYVERNO_ALL) --set kyvernoPolicies.enabled=false --set agentSandbox.podSecurity.enabled=true >/tmp/vm-pe-guard.out 2>&1; then \
		echo "FAIL: the sandbox lost its only securityContext source and the render succeeded"; exit 1; \
	elif ! grep -q "agentSandbox.podSecurity.enabled requires kyvernoPolicies.enabled" /tmp/vm-pe-guard.out; then \
		echo "FAIL: the sandbox pod-security guard failed for the wrong reason"; cat /tmp/vm-pe-guard.out; exit 1; \
	else echo "ok: sandbox pod-security guard"; fi
	@echo "--> kyvernoPolicies.enabled=false renders no kyverno.io object"
	@helm template t $(CONNECTIVITY_DIR) $(KYVERNO_ALL) --set kyvernoPolicies.enabled=false --set agentSandbox.podSecurity.enabled=false >/tmp/vm-pe-none.out 2>&1 || { cat /tmp/vm-pe-none.out; exit 1; }
	@if grep -q "kyverno.io" /tmp/vm-pe-none.out; then \
		echo "FAIL: kyverno.io objects still render under kyvernoPolicies.enabled=false"; grep -n "kyverno.io" /tmp/vm-pe-none.out; exit 1; \
	else echo "ok: no kyverno.io kinds"; fi
	@echo "--> the default (kyverno) render carries one kyverno.io object — the agent-sandbox ClusterPolicy — no kagent Agent mutation and no app: kagent exception; Substrate on adds its four PolicyExceptions"
	@helm template t $(CONNECTIVITY_DIR) $(KYVERNO_ALL) >/tmp/vm-pe-kyverno.out 2>&1 || { cat /tmp/vm-pe-kyverno.out; exit 1; }
	@if [ "$$(grep -c '^apiVersion: kyverno.io/' /tmp/vm-pe-kyverno.out)" != "1" ]; then \
		echo "FAIL: expected 1 kyverno.io object, got $$(grep -c '^apiVersion: kyverno.io/' /tmp/vm-pe-kyverno.out)"; grep -n -A3 '^apiVersion: kyverno.io/' /tmp/vm-pe-kyverno.out; exit 1; \
	elif grep -qE 'kagent-declarative-pod-security|kagent-srt-settings|kagent\.dev/v1alpha2' /tmp/vm-pe-kyverno.out; then \
		echo "FAIL: a kagent v1alpha2 Agent mutation is back (no Agent CR, per-agent Deployment or config Secret exists on kagent API v2)"; exit 1; \
	elif grep -q 'kagent-declarative-seccomp' /tmp/vm-pe-kyverno.out; then \
		echo "FAIL: the v1alpha2 agent Deployments' seccomp exception is back; nothing carries app: kagent on kagent API v2 (substrate-workers is its successor)"; exit 1; \
	elif [ "$$(grep -c '^kind: ClusterPolicy$$' /tmp/vm-pe-kyverno.out)" != "1" ] || ! grep -q 'agent-sandbox-pod-security' /tmp/vm-pe-kyverno.out; then \
		echo "FAIL: the one ClusterPolicy must be the agent-sandbox pod-security policy"; exit 1; \
	else echo "ok: 1 kyverno.io object, no Agent mutation, no app: kagent exception"; fi
	@helm template t $(CONNECTIVITY_DIR) $(KYVERNO_ALL) $(SUBSTRATE_ON) >/tmp/vm-pe-substrate.out 2>&1 || { cat /tmp/vm-pe-substrate.out; exit 1; }
	@if [ "$$(grep -c '^kind: PolicyException' /tmp/vm-pe-substrate.out)" != "4" ]; then \
		echo "FAIL: expected the four Substrate PolicyExceptions, got $$(grep -c '^kind: PolicyException' /tmp/vm-pe-substrate.out)"; exit 1; \
	else echo "ok: Substrate on renders substrate-atelet, substrate-workers, substrate-control-plane, substrate-podcertificate-controller"; fi
	@echo "--> the CNPG ImageVolume exception renders only with an extension image"
	@helm template t $(CONNECTIVITY_DIR) $(KYVERNO_ALL) --set postgres.enabled=true --set postgres.vector.enabled=true >/tmp/vm-pe-noimg.out 2>&1 || { cat /tmp/vm-pe-noimg.out; exit 1; }
	@if grep -q "image-volume" /tmp/vm-pe-noimg.out; then \
		echo "FAIL: the volume-types exception renders with no image volume to except"; exit 1; \
	else echo "ok: no exception without an extension image"; fi
	@helm template t $(CONNECTIVITY_DIR) $(KYVERNO_ALL) --set postgres.enabled=true --set postgres.vector.enabled=true --set postgres.vector.extensionImage.reference=$(PGVECTOR_IMG) >/tmp/vm-pe-img.out 2>&1 || { cat /tmp/vm-pe-img.out; exit 1; }
	@if ! grep -q "name: kagent-pg-image-volume" /tmp/vm-pe-img.out; then \
		echo "FAIL: no volume-types exception for the ImageVolume pgvector path; CNPG instance pods would be denied admission"; exit 1; \
	elif ! grep -q "cnpg.io/cluster: kagent-pg" /tmp/vm-pe-img.out; then \
		echo "FAIL: the exception is not scoped to the Cluster's own pods"; exit 1; \
	else echo "ok: ImageVolume exception scoped to cnpg.io/cluster"; fi
	@echo "--> a rule with no ClusterPolicy in kyvernoPolicies.rules must fail (an exception naming no policy matches nothing)"
	@if helm template t $(CONNECTIVITY_DIR) $(KYVERNO_ALL) --set postgres.enabled=true --set postgres.vector.enabled=true --set postgres.vector.extensionImage.reference=$(PGVECTOR_IMG) --set kyvernoPolicies.rules.restricted-volumes=null >/tmp/vm-pe-rule.out 2>&1; then \
		echo "FAIL: the unknown-rule guard did not fire"; exit 1; \
	elif ! grep -q 'names no ClusterPolicy for the rule "restricted-volumes"' /tmp/vm-pe-rule.out; then \
		echo "FAIL: the unknown-rule guard failed for the wrong reason"; cat /tmp/vm-pe-rule.out; exit 1; \
	else echo "ok: unknown-rule guard"; fi
	@grep -A3 'name: kagent-pg-image-volume' /tmp/vm-pe-img.out >/dev/null && grep -q 'autogen-restricted-volumes' /tmp/vm-pe-img.out || { echo "FAIL: the ImageVolume exception does not cite the rule's autogen copy"; exit 1; }
	@echo "ok: exceptions cite <rule> and autogen-<rule> from kyvernoPolicies.rules"
	@echo "--> the agent-sandbox policy carries no helm.sh/resource-policy (Helm must prune it; the kagent Namespace is the one kept object)"
	@if awk 'BEGIN{RS="\n---\n"} /kind: ClusterPolicy/ && /helm.sh\/resource-policy/ {found=1} END{exit !found}' /tmp/vm-pe-kyverno.out; then \
		echo "FAIL: helm.sh/resource-policy is back on a ClusterPolicy; the policy would be orphaned on removal"; exit 1; \
	else echo "ok: prunable"; fi
	@echo "--> a component toggle left in its old per-chart block must fail loudly"
	@if helm template t $(CONNECTIVITY_DIR) $(VM) --set kagent.enabled=true >/tmp/vm-legacy.out 2>&1; then \
		echo "FAIL: a removed toggle rendered silently; the component would be off with no warning"; exit 1; \
	elif ! grep -q "components.kagent.enabled" /tmp/vm-legacy.out; then \
		echo "FAIL: the legacy-toggle guard failed for the wrong reason"; cat /tmp/vm-legacy.out; exit 1; \
	else echo "ok: legacy-toggle guard"; fi
	@echo "--> a legacy false under a component that is on must fail loudly"
	@if helm template t $(CONNECTIVITY_DIR) $(VM) --set components.klaus-gateway.enabled=true --set klausGateway.enabled=false >/tmp/vm-legacy-on.out 2>&1; then \
		echo "FAIL: klausGateway.enabled=false rendered silently while components.klaus-gateway.enabled=true"; exit 1; \
	elif ! grep -q "components.klaus-gateway.enabled" /tmp/vm-legacy-on.out; then \
		echo "FAIL: the on+false legacy-toggle guard failed for the wrong reason"; cat /tmp/vm-legacy-on.out; exit 1; \
	else echo "ok: on+false legacy-toggle guard"; fi
	@echo "--> a legacy true under a component that is on fails too: neither chart has a Helm dependency, so no chart default is ever coalesced into these blocks and the key can only be the operator's"
	@if helm template t $(CONNECTIVITY_DIR) $(VM) --set components.klaus-gateway.enabled=true --set klausGateway.enabled=true >/tmp/vm-legacy-true.out 2>&1; then \
		echo "FAIL: klausGateway.enabled=true rendered silently while components.klaus-gateway.enabled=true"; exit 1; \
	elif ! grep -q "components.klaus-gateway.enabled" /tmp/vm-legacy-true.out; then \
		echo "FAIL: the on+true legacy-toggle guard failed for the wrong reason"; cat /tmp/vm-legacy-true.out; exit 1; \
	else echo "ok: on+true legacy-toggle guard"; fi
	@echo "--> the meta chart's copy of the probe reports the same key"
	@if helm template t $(CHART_DIR) -f $(CHART_DIR)/ci/ci-values.yaml --set klausGateway.enabled=true >/tmp/vm-legacy-meta.out 2>&1; then \
		echo "FAIL: the meta chart's legacy-toggle guard did not fire on on+true"; exit 1; \
	elif ! grep -q "components.klaus-gateway.enabled" /tmp/vm-legacy-meta.out; then \
		echo "FAIL: the meta chart's legacy-toggle guard failed for the wrong reason"; cat /tmp/vm-legacy-meta.out; exit 1; \
	else echo "ok: meta legacy-toggle guard"; fi
	@echo "--> golden: the default render is byte-identical to $(GOLDEN_REF)"
	@if [ -z "$(GOLDEN_REF)" ]; then \
		echo "skip: GOLDEN_REF is empty (explicit opt-out)"; \
	elif ! git rev-parse --verify -q $(GOLDEN_REF) >/dev/null; then \
		echo "FAIL: GOLDEN_REF=$(GOLDEN_REF) does not resolve; fetch it, point GOLDEN_REF at another ref, or run with GOLDEN_REF= to opt out"; exit 1; \
	else \
		out=$$(mktemp -d); tree=$$(mktemp -d); \
		git worktree add -q --detach $$tree $(GOLDEN_REF) || { echo "FAIL: cannot check out $(GOLDEN_REF)"; exit 1; }; \
		helm template t $$tree/$(CONNECTIVITY_DIR) $(KYVERNO_GOLDEN_REF) >$$out/golden 2>&1 \
			|| { echo "FAIL: the $(GOLDEN_REF) render failed"; cat $$out/golden; git worktree remove --force $$tree; exit 1; }; \
		git worktree remove --force $$tree; \
		$(GOLDEN_RETIRED) $$out/golden; \
		helm template t $(CONNECTIVITY_DIR) $(KYVERNO_GOLDEN) >$$out/head 2>&1 \
			|| { echo "FAIL: the working-tree render failed"; cat $$out/head; exit 1; }; \
		for f in golden head; do python3 -c 'import re,sys; ex=set(sys.argv[2].split()); docs=open(sys.argv[1]).read().split("\n---\n"); keep=[d for d in docs if not (re.search(r"^  name: (\S+)", d, re.M) and re.search(r"^  name: (\S+)", d, re.M).group(1) in ex)]; out="\n---\n".join(keep).lstrip("-\n"); open(sys.argv[1],"w").write("---\n"+out.rstrip("\n")+"\n")' $$out/$$f "$(GOLDEN_EXCLUDE)"; done; \
		if diff -u $$out/golden $$out/head; then echo "ok: default render unchanged (excluding $(GOLDEN_EXCLUDE))"; \
		else echo "FAIL: the default render drifted from $(GOLDEN_REF)"; exit 1; fi; \
	fi
	@echo "All mode guards verified."

# The global.* contract inputs a standalone install sets; the fleet sets none of
# them, which the golden check above pins to a byte-identical render.
GLOBAL_VM := --set global.domain=ci.example.com --set 'global.gatewayApi.parentRefs[0].name=giantswarm-default' --set 'global.gatewayApi.parentRefs[0].namespace=envoy-gateway-system' $(FLEET_APIS)
# A valid edge-mode config: the chart-owned Gateway is the public edge.
EDGE_VM := --set global.domain=ci.example.com --set ingress.mode=agentgateway-muster --set components.agentgateway.enabled=true --set gatewayApi.gateway.create=true --set gatewayApi.gateway.tls.secretName=wildcard-tls $(FLEET_APIS)

.PHONY: verify-global
verify-global: ## Assert the global.* contract behaviors (derived hostnames, gateway fallback, observability gates, edge mode) and their guards.
	@echo "====> $@ ($(CONNECTIVITY_DIR))"
	@echo "--> hostnames derive from global.domain, routes attach to global.gatewayApi.parentRefs"
	@helm template t $(CONNECTIVITY_DIR) $(GLOBAL_VM) >/tmp/vg-derive.out 2>&1 || { cat /tmp/vg-derive.out; exit 1; }
	@grep -q 'muster.ci.example.com' /tmp/vg-derive.out || { echo "FAIL: muster hostname not derived from global.domain"; exit 1; }
	@grep -q 'name: giantswarm-default' /tmp/vg-derive.out || { echo "FAIL: routes do not attach to global.gatewayApi.parentRefs"; exit 1; }
	@echo "ok: derived hostname + Gateway fallback"
	@echo "--> explicit ingress.hostnames / parentRefs still win over global.*"
	@helm template t $(CONNECTIVITY_DIR) $(GLOBAL_VM) $(VM) --set 'ingress.hostnames[0]=own.example.org' >/tmp/vg-override.out 2>&1 || { cat /tmp/vg-override.out; exit 1; }
	@grep -q 'own.example.org' /tmp/vg-override.out || { echo "FAIL: ingress.hostnames override lost"; exit 1; }
	@if grep -q 'muster.ci.example.com' /tmp/vg-override.out; then echo "FAIL: derived hostname rendered next to the override"; exit 1; fi
	@grep -q 'name: x' /tmp/vg-override.out || { echo "FAIL: ingress.parentRefs override lost"; exit 1; }
	@echo "ok: per-route overrides win"
	@echo "--> ingress.httpRoute.timeouts lands on the muster route"
	@helm template t $(CONNECTIVITY_DIR) $(VM) --set 'ingress.httpRoute.timeouts.request=0s' 2>/dev/null | grep -A1 'timeouts:' | grep -q 'request: 0s' || { echo "FAIL: HTTPRoute timeouts missing"; exit 1; }
	@echo "ok: route timeouts"
	@echo "--> global.observability.metrics.serviceMonitor.enabled=false removes every monitor object"
	@helm template t $(CONNECTIVITY_DIR) $(VM) --set components.kagent.enabled=true --set postgres.enabled=true --set global.observability.metrics.serviceMonitor.enabled=false >/tmp/vg-mon.out 2>&1 || { cat /tmp/vg-mon.out; exit 1; }
	@for pattern in 'kind: ServiceMonitor' 'enablePodMonitor' 'inheritedMetadata'; do \
		if grep -q "$$pattern" /tmp/vg-mon.out; then echo "FAIL: monitor-gated render still contains $$pattern"; exit 1; fi; \
	done
	@echo "ok: monitor gate"
	@echo "--> the default render keeps the CNPG PodMonitor (fleet behavior) and renders NO kagent ServiceMonitor or metrics Service: the kagent line serves no /metrics (kagent.serviceMonitor.enabled: false)"
	@helm template t $(CONNECTIVITY_DIR) $(VM) --set components.kagent.enabled=true --set postgres.enabled=true >/tmp/vg-mon-default.out 2>&1 || { cat /tmp/vg-mon-default.out; exit 1; }
	@if grep -q 'kind: ServiceMonitor' /tmp/vg-mon-default.out; then echo "FAIL: the default render carries a kagent ServiceMonitor; the line's controller serves no /metrics and the monitor would sit at up=0"; exit 1; fi
	@if grep -q 'kagent-controller-metrics' /tmp/vg-mon-default.out; then echo "FAIL: the default render carries the kagent controller metrics Service; nothing listens behind it on the line"; exit 1; fi
	@grep -q 'enablePodMonitor: true' /tmp/vg-mon-default.out || { echo "FAIL: default render lost the CNPG PodMonitor"; exit 1; }
	@grep -q 'helm.sh/resource-policy: keep' /tmp/vg-mon-default.out || { echo "FAIL: the CNPG Cluster lost helm.sh/resource-policy: keep"; exit 1; }
	@echo "ok: default: no kagent monitor, CNPG PodMonitor + keep"
	@echo "--> kagent.serviceMonitor.enabled=true (for when upstream serves metrics) renders the Service and the ServiceMonitor under the global gate"
	@helm template t $(CONNECTIVITY_DIR) $(VM) --set components.kagent.enabled=true --set postgres.enabled=true --set kagent.serviceMonitor.enabled=true >/tmp/vg-mon-on.out 2>&1 || { cat /tmp/vg-mon-on.out; exit 1; }
	@grep -q 'kind: ServiceMonitor' /tmp/vg-mon-on.out || { echo "FAIL: kagent.serviceMonitor.enabled=true renders no ServiceMonitor"; exit 1; }
	@grep -q 'observability.giantswarm.io/tenant: giantswarm' /tmp/vg-mon-on.out || { echo "FAIL: the kagent ServiceMonitor lost the tenant label"; exit 1; }
	@helm template t $(CONNECTIVITY_DIR) $(VM) --set components.kagent.enabled=true --set kagent.serviceMonitor.enabled=true --set global.observability.metrics.serviceMonitor.enabled=false 2>/dev/null | grep -q 'kind: ServiceMonitor' && { echo "FAIL: the global monitor gate no longer holds the kagent ServiceMonitor back"; exit 1; } || true
	@echo "ok: the toggle under the global gate"
	@echo "--> the kagent controller metrics Service selects kagent's own release instance (the pods' label), the ServiceMonitor this chart's Service"
	@python3 -c 'import re,sys; docs=open("/tmp/vg-mon-on.out").read().split("\n---\n"); svc=[d for d in docs if "\nkind: Service\n" in d and re.search(r"^  name: t-kagent-controller-metrics$$", d, re.M)]; sys.exit("FAIL: the kagent controller metrics Service did not render") if len(svc)!=1 else None; sel=svc[0][svc[0].index("  selector:"):]; sys.exit("FAIL: the metrics Service does not select app.kubernetes.io/instance: kagent (the kagent release name the meta chart fixes):\n"+sel) if not re.search(r"^    app.kubernetes.io/instance: kagent$$", sel, re.M) else None; sys.exit("FAIL: the metrics Service selects this release (t) — under the meta chart that matches no pod (#305)") if re.search(r"^    app.kubernetes.io/instance: \"?t\"?$$", sel, re.M) else None; sm=[d for d in docs if "kind: ServiceMonitor" in d and "-kagent-controller\n" in d]; sys.exit("FAIL: the kagent ServiceMonitor did not render") if len(sm)!=1 else None; sys.exit("FAIL: the ServiceMonitor must select this chart\x27s Service (instance t)") if "      app.kubernetes.io/instance: \"t\"" not in sm[0] else None; print("ok: metrics Service selects instance kagent; the ServiceMonitor selects this release\x27s Service")'
	@echo "--> no kagent-targeting selector, Service name or hostname in this chart derives from .Release.Name (the standalone umbrella's one-release assumption)"
	@if grep -nE 'fullnameOverride \| default \.Release\.Name|fullnameOverride" \| default \(printf "%s-oauth2-proxy" \.Release\.Name' $(CONNECTIVITY_DIR)/templates/kagent/*.yaml; then echo "FAIL: a kagent template falls back to .Release.Name for a kagent-chart object; use agent-platform.kagent.fullname / agent-platform.kagent.releaseName"; exit 1; else echo "ok: kagent templates derive kagent names from the kagent helpers"; fi
	@echo "--> the CNPG CiliumNetworkPolicy renders only when postgres.enabled"
	@grep -q 'cnpg.io/cluster' /tmp/vg-mon-on.out || { echo "FAIL: no CNPG network policy with postgres.enabled=true"; exit 1; }
	@helm template t $(CONNECTIVITY_DIR) $(VM) --set components.kagent.enabled=true >/tmp/vg-nopg.out 2>&1 || { cat /tmp/vg-nopg.out; exit 1; }
	@if grep -q 'cnpg.io/cluster' /tmp/vg-nopg.out; then echo "FAIL: CNPG network policy rendered for a postgres cluster that does not exist"; exit 1; fi
	@echo "ok: CNPG netpol gate"
	@echo "--> global.observability.traces.otlp.endpoint replaces the default OTEL env (no duplicate names)"
	@helm template t $(CONNECTIVITY_DIR) $(VM) --set ingress.mode=agentgateway-muster --set components.agentgateway.enabled=true --set global.observability.traces.otlp.endpoint=http://collector:4317 >/tmp/vg-otlp.out 2>&1 || { cat /tmp/vg-otlp.out; exit 1; }
	@grep -q 'value: http://collector:4317' /tmp/vg-otlp.out || { echo "FAIL: OTLP endpoint not rendered"; exit 1; }
	@if grep -q 'otlp-gateway.kube-system' /tmp/vg-otlp.out; then echo "FAIL: default OTEL env rendered next to the global one (duplicate env names)"; exit 1; fi
	@helm template t $(CONNECTIVITY_DIR) $(VM) --set ingress.mode=agentgateway-muster --set components.agentgateway.enabled=true 2>/dev/null | grep -q 'otlp-gateway.kube-system' || { echo "FAIL: default OTEL env lost with global.* unset"; exit 1; }
	@echo "ok: OTLP env"
	@echo "--> the data-plane Service overlay nests at spec.service.spec.type (a bare spec.service.type is not in the CRD schema)"
	@grep -A2 '^  service:' /tmp/vg-otlp.out | grep -q '^      type: ClusterIP' || { echo "FAIL: gateway.parameters.serviceType is not rendered at spec.service.spec.type"; exit 1; }
	@echo "ok: Service overlay nesting"
	@echo "--> the kagent JWT policy defaults its issuer from global.identity.issuerUrl"
	@helm template t $(CONNECTIVITY_DIR) $(VM) --set ingress.mode=agentgateway-muster --set components.agentgateway.enabled=true --set components.kagent.enabled=true --set kagent.controllerRoute.enabled=true --set kagent.controllerRoute.hostname=agw.example.com --set kagent.controllerRoute.jwtAuthentication.enabled=true --set gateway.jwksEgress.enabled=true --set global.identity.issuerUrl=https://dex.ci.example.com 2>/dev/null | grep -q 'issuer: "https://dex.ci.example.com"' || { echo "FAIL: JWT issuer not defaulted from global.identity"; exit 1; }
	@echo "ok: JWT issuer default"
	@echo "--> a muster issuer that differs from global.identity fails"
	@if helm template t $(CONNECTIVITY_DIR) $(VM) --set global.identity.issuerUrl=https://dex.ci.example.com --set muster.muster.oauth.server.enabled=true --set muster.muster.oauth.server.dex.issuerUrl=https://other.example.com >/tmp/vg-idp.out 2>&1; then \
		echo "FAIL: muster issuer differing from global.identity accepted"; exit 1; \
	elif ! grep -q "differs from global.identity.issuerUrl" /tmp/vg-idp.out; then \
		echo "FAIL: identity consistency check failed for the wrong reason"; cat /tmp/vg-idp.out; exit 1; \
	else echo "ok: identity consistency guard"; fi
	@echo "--> edge mode renders the HTTPS listener, pins public routes to it, and suppresses the layer-1 routes"
	@helm template t $(CONNECTIVITY_DIR) $(EDGE_VM) --set components.kagent.enabled=true --set kagent.controllerRoute.enabled=true --set gateway.jwksEgress.enabled=true --set global.identity.issuerUrl=https://dex.ci.example.com >/tmp/vg-edge.out 2>&1 || { cat /tmp/vg-edge.out; exit 1; }
	@grep -q 'hostname: "\*.ci.example.com"' /tmp/vg-edge.out || { echo "FAIL: edge HTTPS listener missing"; exit 1; }
	@grep -q 'sectionName: https' /tmp/vg-edge.out || { echo "FAIL: public routes not pinned to the HTTPS listener (plaintext 8080 would ride the LB)"; exit 1; }
	@grep -A2 '^  service:' /tmp/vg-edge.out | grep -q '^      type: LoadBalancer' || { echo "FAIL: edge data-plane Service type is not nested at spec.service.spec.type (the CRD prunes a bare spec.service.type)"; exit 1; }
	@if grep -q 'name: kagent-controller-public' /tmp/vg-edge.out; then echo "FAIL: layer-1 kagent route rendered with the edge as data plane"; exit 1; fi
	@grep -A3 '^kind: GRPCRoute$$' /tmp/vg-edge.out | grep -q '^  name: kagent-controller$$' || { echo "FAIL: the kagent controller GRPCRoute is missing in edge mode"; exit 1; }
	@if grep -qE '^      value: /mcp' /tmp/vg-edge.out; then echo "FAIL: layer-1 /mcp route rendered with the edge as data plane"; exit 1; fi
	@grep -B4 -A4 '"world", "cluster"' /tmp/vg-edge.out | grep -q '"443"' || { echo "FAIL: edge network policy does not admit world traffic on 443"; exit 1; }
	@echo "ok: edge mode"
	@echo "--> edge guards: the certificate Secret and the agentgateway mode are required"
	@if helm template t $(CONNECTIVITY_DIR) $(EDGE_VM) --set gatewayApi.gateway.tls.secretName= >/tmp/vg-tls.out 2>&1; then \
		echo "FAIL: gateway.create without tls.secretName accepted"; exit 1; \
	elif ! grep -q "gatewayApi.gateway.tls.secretName is empty" /tmp/vg-tls.out; then \
		echo "FAIL: tls guard failed for the wrong reason"; cat /tmp/vg-tls.out; exit 1; \
	else echo "ok: tls guard"; fi
	@if helm template t $(CONNECTIVITY_DIR) --set global.domain=ci.example.com --set gatewayApi.gateway.create=true --set gatewayApi.gateway.tls.secretName=wildcard-tls >/tmp/vg-mode.out 2>&1; then \
		echo "FAIL: gateway.create in muster-direct mode accepted"; exit 1; \
	elif ! grep -q "ingress.mode is muster-direct" /tmp/vg-mode.out; then \
		echo "FAIL: edge mode guard failed for the wrong reason"; cat /tmp/vg-mode.out; exit 1; \
	else echo "ok: edge mode guard"; fi
	@echo "--> kagent uiRoute derives its hostname from global.domain (and still fails with neither set)"
	@helm template t $(CONNECTIVITY_DIR) $(GLOBAL_VM) --set components.kagent.enabled=true --set kagent.uiRoute.enabled=true 2>/dev/null | grep -q '"kagent.ci.example.com"' || { echo "FAIL: kagent UI hostname not derived"; exit 1; }
	@if helm template t $(CONNECTIVITY_DIR) $(VM) --set components.kagent.enabled=true --set kagent.uiRoute.enabled=true >/tmp/vg-uihost.out 2>&1; then \
		echo "FAIL: uiRoute with no hostname and no global.domain accepted (route would capture all traffic)"; exit 1; \
	elif ! grep -q "global.domain is empty and kagent.uiRoute.hostname is not set" /tmp/vg-uihost.out; then \
		echo "FAIL: uiRoute hostname guard failed for the wrong reason"; cat /tmp/vg-uihost.out; exit 1; \
	else echo "ok: uiRoute hostname derivation + guard"; fi
	@echo "All global.* contract behaviors verified."

# Every credential key the guard knows, set to one canary value. The value must
# never appear in the failure message; the paths must all appear.
INLINE_SECRET_PATHS := kagent.providers.anthropic.apiKey kagent.oauth2-proxy.config.clientSecret kagent.oauth2-proxy.config.cookieSecret muster.muster.oauth.server.dex.clientSecret muster.muster.oauth.server.registrationToken muster.muster.oauth.server.encryptionKeyValue muster.muster.oauth.server.storage.valkey.password valkey.valkey.auth.aclUsers.default.password klausGateway.slack.botToken klausGateway.slack.signingSecret klausGateway.obo.stateKey klausGateway.obo.storeKey model-manager.oauth.dex.clientSecret agent-manager.oauth.dex.clientSecret cluster-manager.oauth.dex.clientSecret
INLINE_SECRET_SETS := --set kagent.providers.anthropic.apiKey=LEAK-CANARY-VALUE --set kagent.oauth2-proxy.config.clientSecret=LEAK-CANARY-VALUE --set kagent.oauth2-proxy.config.cookieSecret=LEAK-CANARY-VALUE --set muster.muster.oauth.server.dex.clientSecret=LEAK-CANARY-VALUE --set muster.muster.oauth.server.registrationToken=LEAK-CANARY-VALUE --set muster.muster.oauth.server.encryptionKeyValue=LEAK-CANARY-VALUE --set muster.muster.oauth.server.storage.valkey.password=LEAK-CANARY-VALUE --set valkey.valkey.auth.aclUsers.default.password=LEAK-CANARY-VALUE --set klausGateway.slack.botToken=LEAK-CANARY-VALUE --set klausGateway.slack.signingSecret=LEAK-CANARY-VALUE --set klausGateway.obo.stateKey=LEAK-CANARY-VALUE --set klausGateway.obo.storeKey=LEAK-CANARY-VALUE --set model-manager.oauth.dex.clientSecret=LEAK-CANARY-VALUE --set agent-manager.oauth.dex.clientSecret=LEAK-CANARY-VALUE --set cluster-manager.oauth.dex.clientSecret=LEAK-CANARY-VALUE
# The same installation on referenced Secrets: the knobs an operator sets instead.
REFERENCED_SECRET_SETS := --set kagent.providers.anthropic.apiKeySecretRef=kagent-anthropic-key --set kagent.oauth2-proxy.config.existingSecret=kagent-oauth2-proxy-credentials --set muster.muster.oauth.server.existingSecret=muster-oauth-credentials --set muster.muster.oauth.server.storage.valkey.existingSecret=muster-valkey-credentials --set valkey.valkey.auth.usersExistingSecret=muster-valkey-credentials --set valkey.valkey.auth.aclUsers.default.passwordKey=valkey-password --set klausGateway.slack.secretName=klaus-gateway-slack-credentials --set klausGateway.obo.existingSecret=klaus-gateway-obo-keys

.PHONY: verify-secrets
verify-secrets: ## Assert gitops.forbidInlineSecrets: off by default, fails the render naming (only) the inline credential paths, passes on referenced Secrets.
	@echo "====> $@ ($(CHART_DIR))"
	@echo "--> default (forbidInlineSecrets: false): an inline credential still renders and is forwarded (the pre-existing behavior)"
	@helm template t $(CHART_DIR) -f $(CHART_DIR)/ci/ci-values.yaml $(INLINE_SECRET_SETS) >/tmp/vs-default.out 2>&1 || { cat /tmp/vs-default.out; exit 1; }
	@grep -q 'LEAK-CANARY-VALUE' /tmp/vs-default.out || { echo "FAIL: the inline credential did not reach a child HelmRelease (test setup)"; exit 1; }
	@echo "ok: default render unchanged"
	@echo "--> forbidInlineSecrets: true fails on every known inline credential path"
	@if helm template t $(CHART_DIR) -f $(CHART_DIR)/ci/ci-values.yaml --set gitops.forbidInlineSecrets=true $(INLINE_SECRET_SETS) >/tmp/vs-forbid.out 2>&1; then \
		echo "FAIL: the inline-secret guard did not fire"; exit 1; fi
	@grep -q "gitops.forbidInlineSecrets is true" /tmp/vs-forbid.out || { echo "FAIL: the render failed for the wrong reason"; cat /tmp/vs-forbid.out; exit 1; }
	@for p in $(INLINE_SECRET_PATHS); do \
		grep -q "$$p" /tmp/vs-forbid.out || { echo "FAIL: the guard did not name $$p"; cat /tmp/vs-forbid.out; exit 1; }; \
	done
	@echo "ok: every inline path named"
	@echo "--> the failure message carries the key paths, never the values"
	@if grep -q 'LEAK-CANARY-VALUE' /tmp/vs-forbid.out; then echo "FAIL: the guard's message leaked a credential value"; exit 1; else echo "ok: no value in the message"; fi
	@echo "--> a single inline key is enough to fail, and is the only one named"
	@if helm template t $(CHART_DIR) -f $(CHART_DIR)/ci/ci-values.yaml --set gitops.forbidInlineSecrets=true --set kagent.providers.anthropic.apiKey=LEAK-CANARY-VALUE >/tmp/vs-one.out 2>&1; then \
		echo "FAIL: one inline key passed the guard"; exit 1; fi
	@grep -q 'kagent.providers.anthropic.apiKey' /tmp/vs-one.out || { echo "FAIL: the single key was not named"; cat /tmp/vs-one.out; exit 1; }
	@if grep -q 'klausGateway.slack.botToken' /tmp/vs-one.out; then echo "FAIL: an unset key was named"; exit 1; fi
	@echo "ok: single key"
	@echo "--> forbidInlineSecrets: true with referenced Secrets renders, and no child HelmRelease carries a credential"
	@helm template t $(CHART_DIR) -f $(CHART_DIR)/ci/ci-values.yaml --set gitops.forbidInlineSecrets=true $(REFERENCED_SECRET_SETS) >/tmp/vs-ref.out 2>&1 || { cat /tmp/vs-ref.out; exit 1; }
	@if grep -E '^\s*(apiKey|clientSecret|cookieSecret|botToken|signingSecret|appToken|stateKey|storeKey|registrationToken|encryptionKeyValue|password): ' /tmp/vs-ref.out | grep -vqE ': ""$$'; then \
		echo "FAIL: a child HelmRelease still carries a non-empty credential key:"; grep -nE '^\s*(apiKey|clientSecret|cookieSecret|botToken|signingSecret|appToken|stateKey|storeKey|registrationToken|encryptionKeyValue|password): ' /tmp/vs-ref.out | grep -vE ': ""$$'; exit 1; fi
	@grep -q 'existingSecret: klaus-gateway-obo-keys' /tmp/vs-ref.out || { echo "FAIL: klausGateway.obo.existingSecret was not forwarded to the klaus-gateway release"; exit 1; }
	@grep -q 'apiKeySecretRef: kagent-anthropic-key' /tmp/vs-ref.out || { echo "FAIL: kagent.providers.anthropic.apiKeySecretRef was not forwarded"; exit 1; }
	@echo "ok: referenced Secrets render clean"
	@echo "--> the flag itself is meta-package plumbing and is not forwarded to any child release"
	@if grep -q 'forbidInlineSecrets' /tmp/vs-ref.out; then echo "FAIL: gitops.forbidInlineSecrets leaked into a child HelmRelease's values"; exit 1; else echo "ok: flag not forwarded"; fi

.PHONY: verify-login-connector
verify-login-connector: ## Assert gitops.forbidPinnedLoginConnector: off by default a pinned connectorId renders and reaches the muster release, on it fails the render naming the key, on with an empty or absent connectorId renders, and the knob reaches no child release.
	@echo "====> $@ ($(CHART_DIR))"
	@echo "--> default (forbidPinnedLoginConnector: false): a pinned connectorId renders and reaches the muster release (the pre-existing behavior)"
	@helm template t $(CHART_DIR) -f $(CHART_DIR)/ci/ci-values.yaml --set muster.muster.oauth.server.dex.connectorId=pinned-connector >/tmp/vlc-default.out 2>&1 || { cat /tmp/vlc-default.out; exit 1; }
	@grep -q 'connectorId: pinned-connector' /tmp/vlc-default.out || { echo "FAIL: the pinned connectorId did not reach the muster HelmRelease (test setup)"; exit 1; }
	@echo "ok: default render unchanged"
	@echo "--> forbidPinnedLoginConnector: true with a pinned connectorId fails, naming the key"
	@if helm template t $(CHART_DIR) -f $(CHART_DIR)/ci/ci-values.yaml --set gitops.forbidPinnedLoginConnector=true --set muster.muster.oauth.server.dex.connectorId=pinned-connector >/tmp/vlc-forbid.out 2>&1; then \
		echo "FAIL: the pinned-connector guard did not fire"; exit 1; fi
	@grep -q "gitops.forbidPinnedLoginConnector is true" /tmp/vlc-forbid.out || { echo "FAIL: the render failed for the wrong reason"; cat /tmp/vlc-forbid.out; exit 1; }
	@grep -q "muster.muster.oauth.server.dex.connectorId" /tmp/vlc-forbid.out || { echo "FAIL: the guard did not name the key"; cat /tmp/vlc-forbid.out; exit 1; }
	@if grep -q 'pinned-connector' /tmp/vlc-forbid.out; then echo "FAIL: the guard's message repeated the pinned value"; exit 1; fi
	@echo "ok: guard fires naming the key"
	@echo "--> forbidPinnedLoginConnector: true with no pin renders (connectorId absent, and set to the empty string)"
	@helm template t $(CHART_DIR) -f $(CHART_DIR)/ci/ci-values.yaml --set gitops.forbidPinnedLoginConnector=true >/tmp/vlc-none.out 2>&1 || { cat /tmp/vlc-none.out; exit 1; }
	@helm template t $(CHART_DIR) -f $(CHART_DIR)/ci/ci-values.yaml --set gitops.forbidPinnedLoginConnector=true --set muster.muster.oauth.server.dex.connectorId="" >/tmp/vlc-empty.out 2>&1 || { cat /tmp/vlc-empty.out; exit 1; }
	@echo "ok: no pin renders"
	@echo "--> the knob itself is meta-package plumbing and is not forwarded to any child release"
	@if grep -q 'forbidPinnedLoginConnector' /tmp/vlc-none.out; then echo "FAIL: gitops.forbidPinnedLoginConnector leaked into a child HelmRelease's values"; exit 1; else echo "ok: knob not forwarded"; fi

.PHONY: verify-meta
# The meta chart's render assertions run with the bundled Flux engine OFF (the
# fleet's value): the pure-renderer rule holds for the platform objects, and
# the engine's own objects (operator, FluxInstance, identities, hooks, CRDs)
# are asserted by verify-engine in both shapes.
ENGINE_OFF := --set components.flux.enabled=false
.PHONY: verify-meta
verify-meta: ## Assert the app-of-apps meta-package render (pure renderer with the engine off, ranges as values, Flux the only engine, pinned BOM).
	@echo "====> $@ ($(CHART_DIR))"
	@echo "--> Chart.yaml's only dependency is the flux-engine subchart, conditional on components.flux.enabled; no component pin"
	@if ! grep -q '^dependencies:' $(CHART_DIR)/Chart.yaml; then \
		echo "FAIL: Chart.yaml declares no dependencies; the flux-engine subchart must be one"; exit 1; \
	elif [ "$$(sed -n '/^dependencies:/,$$p' $(CHART_DIR)/Chart.yaml | grep -c '^  - name: ')" != "1" ] || ! sed -n '/^dependencies:/,$$p' $(CHART_DIR)/Chart.yaml | grep -q '^  - name: flux-engine$$'; then \
		echo "FAIL: Chart.yaml dependencies must be exactly flux-engine — components are values (versionRange), never package-time pins"; sed -n '/^dependencies:/,$$p' $(CHART_DIR)/Chart.yaml; exit 1; \
	elif ! sed -n '/^dependencies:/,$$p' $(CHART_DIR)/Chart.yaml | grep -q 'condition: components.flux.enabled'; then \
		echo "FAIL: the flux-engine dependency is not conditional on components.flux.enabled"; exit 1; \
	else echo "ok: one dependency, flux-engine, conditional, not a component"; fi
	@echo "--> flux engine renders OCIRepository + HelmRelease with version RANGES + app-owned CRDs"
	@helm template t $(CHART_DIR) -f $(CHART_DIR)/ci/ci-values.yaml $(ENGINE_OFF) >/tmp/ap-flux.out 2>&1 || { cat /tmp/ap-flux.out; exit 1; }
	@grep -q 'kind: OCIRepository' /tmp/ap-flux.out || { echo "FAIL: no OCIRepository"; exit 1; }
	@grep -q 'kind: HelmRelease'   /tmp/ap-flux.out || { echo "FAIL: no HelmRelease"; exit 1; }
	@grep -q 'semver: "0.x"'       /tmp/ap-flux.out || { echo "FAIL: muster range not rendered as a value"; exit 1; }
	@grep -q 'name: agent-platform-connectivity' /tmp/ap-flux.out || { echo "FAIL: connectivity release missing"; exit 1; }
	@grep -qE '^  name: dicebear$$' /tmp/ap-flux.out || { echo "FAIL: dicebear avatar component not rendered"; exit 1; }
	@if grep -q 'platform-crds' /tmp/ap-flux.out; then echo "FAIL: retired platform-crds bundle still referenced"; exit 1; else echo "ok: no platform-crds bundle (app-owned CRDs)"; fi
	@grep -q 'crds: CreateReplace' /tmp/ap-flux.out || { echo "FAIL: app-owned CRDs (crds: CreateReplace) not rendered"; exit 1; }
	@grep -qE '^    - name: agentgateway$$' /tmp/ap-flux.out || { echo "FAIL: a CR consumer no longer dependsOn its CRD-owning component (agentgateway)"; exit 1; }
	@echo "--> kagent's first install does not wait for the controller (it mounts the CNPG Secret connectivity renders, and connectivity dependsOn kagent)"
	@helm template t $(CHART_DIR) -f $(CHART_DIR)/ci/ci-values.yaml $(ENGINE_OFF) --set components.kagent.enabled=true >/tmp/ap-kag.out 2>&1 || { cat /tmp/ap-kag.out; exit 1; }
	@python3 -c 'import re,sys; docs=open("/tmp/ap-kag.out").read().split("\n---\n"); hr=[d for d in docs if "kind: HelmRelease" in d and re.search(r"^  name: kagent$$", d, re.M)]; sys.exit("FAIL: kagent HelmRelease not rendered") if not hr else None; waived=[re.search(r"^  name: (.*)$$", d, re.M).group(1) for d in docs if "kind: HelmRelease" in d and "disableWait: true" in d]; sys.exit("FAIL: a component install no longer waits for its workload: "+", ".join(waived)+" (kagent and substrate depend on the connectivity release, whose hooks mint what their pods start against, so every install waits)") if waived else print("ok: every component install waits for its workload (no install.disableWait)")'
	@echo "ok: flux render"
	@echo "--> agentgateway 2.x wiring: forwarded values are FLAT and carry no umbrella-only key"
	@./tests/verify-agentgateway-wiring.py /tmp/ap-flux.out
	@grep -q 'semver: ">=2.4.0 <3.0.0"' /tmp/ap-flux.out || { echo "FAIL: agentgateway range is not >=2.4.0 <3.0.0 (the flattened chart line, floored at the packaging release whose monitoring values this chart sets — giantswarm/agentgateway#60)"; exit 1; }
	@echo "ok: agentgateway 2.x wiring"
	@echo "--> the kagent line's wiring: kagent + kagent-crds on the line's release range, one build (tag + Harness digest), flat forwarded values with no umbrella-only or retired key"
	@./tests/verify-kagent-wiring.py /tmp/ap-flux.out
	@if helm template t $(CHART_DIR) -f $(CHART_DIR)/ci/ci-values.yaml $(ENGINE_OFF) --set components.kagent-crds.enabled=false >/tmp/ap-kag-crds.out 2>&1; then \
		echo "FAIL: kagent on with kagent-crds off rendered; the controller would run without its CRDs"; exit 1; \
	elif ! grep -q "components.kagent-crds.enabled is not" /tmp/ap-kag-crds.out; then \
		echo "FAIL: the kagent-crds guard failed for the wrong reason"; cat /tmp/ap-kag-crds.out; exit 1; \
	else echo "ok: kagent on without kagent-crds is refused"; fi
	@echo "ok: the kagent line's wiring"
	@echo "--> PURE app-of-apps (engine off): root emits ONLY OCIRepository + HelmRelease as release objects (no raw CRs; the storage-version hooks of #396 are Helm hooks, verify-kagent-storage-version's)"
	@python3 -c 'import re,sys; docs=open("/tmp/ap-flux.out").read().split("\n---\n"); bad=[re.search(r"^kind: (\S+)$$", d, re.M).group(1) for d in docs if re.search(r"^kind: ", d, re.M) and "helm.sh/hook:" not in d and not re.search(r"^kind: (OCIRepository|HelmRelease)$$", d, re.M)]; sys.exit("FAIL: root rendered a non-app-of-apps kind: " + ", ".join(bad)) if bad else print("ok: pure renderer (only OCIRepository/HelmRelease besides the hooks)")'
	@echo "--> Flux is the only engine: the render carries no argoproj.io object"
	@if grep -q 'argoproj.io' /tmp/ap-flux.out; then \
		echo "FAIL: an argoproj.io object rendered; the Argo render engine was removed"; grep -n 'argoproj.io' /tmp/ap-flux.out; exit 1; \
	else echo "ok: no argoproj.io object"; fi
	@echo "--> gitops.engine=argo is refused by the schema (enum: flux)"
	@if helm template t $(CHART_DIR) -f $(CHART_DIR)/ci/ci-values.yaml $(ENGINE_OFF) --set gitops.engine=argo >/tmp/ap-argo.out 2>&1; then \
		echo "FAIL: gitops.engine=argo rendered; the Argo render engine was removed"; exit 1; \
	elif ! grep -q "gitops" /tmp/ap-argo.out || ! grep -q "flux" /tmp/ap-argo.out; then \
		echo "FAIL: gitops.engine=argo failed for the wrong reason (expected the schema enum naming flux)"; cat /tmp/ap-argo.out; exit 1; \
	else echo "ok: argo refused by the schema"; fi
	@echo "--> gitops.engine=argo is refused by the template guard too, naming flux as the only engine"
	@if helm template t $(CHART_DIR) -f $(CHART_DIR)/ci/ci-values.yaml $(ENGINE_OFF) --set gitops.engine=argo --skip-schema-validation >/tmp/ap-argo-guard.out 2>&1; then \
		echo "FAIL: gitops.engine=argo rendered past the schema; the template guard is gone"; exit 1; \
	elif ! grep -q "gitops.engine=argo is not supported; flux is the only engine" /tmp/ap-argo-guard.out; then \
		echo "FAIL: gitops.engine=argo failed for the wrong reason (expected the guard message)"; cat /tmp/ap-argo-guard.out; exit 1; \
	else echo "ok: argo refused by the guard"; fi
	@echo "--> gitops.argo.* is gone: the schema rejects the key"
	@if helm template t $(CHART_DIR) -f $(CHART_DIR)/ci/ci-values.yaml $(ENGINE_OFF) --set gitops.argo.project=x >/tmp/ap-argo-vals.out 2>&1; then \
		echo "FAIL: gitops.argo.project passed the schema"; exit 1; \
	elif ! grep -q "argo" /tmp/ap-argo-vals.out; then \
		echo "FAIL: gitops.argo.project failed for the wrong reason"; cat /tmp/ap-argo-vals.out; exit 1; \
	else echo "ok: gitops.argo.* refused by the schema"; fi
	@echo "--> gitops.engine: flux set explicitly renders exactly the default"
	@helm template t $(CHART_DIR) -f $(CHART_DIR)/ci/ci-values.yaml $(ENGINE_OFF) --set gitops.engine=flux >/tmp/ap-flux-explicit.out 2>&1 || { cat /tmp/ap-flux-explicit.out; exit 1; }
	@cmp -s /tmp/ap-flux.out /tmp/ap-flux-explicit.out || { echo "FAIL: an explicit gitops.engine=flux renders differently from the default"; exit 1; }
	@echo "ok: explicit flux"
	@echo "--> bogus engine must fail (schema, then the guard behind it)"
	@if helm template t $(CHART_DIR) $(ENGINE_OFF) --set gitops.engine=bogus >/tmp/ap-eng.out 2>&1; then \
		echo "FAIL: engine guard did not fire"; exit 1; \
	elif ! grep -q "flux" /tmp/ap-eng.out; then \
		echo "FAIL: engine schema check failed for the wrong reason"; cat /tmp/ap-eng.out; exit 1; \
	else echo "ok: engine schema"; fi
	@if helm template t $(CHART_DIR) $(ENGINE_OFF) --set gitops.engine=bogus --skip-schema-validation >/tmp/ap-eng-guard.out 2>&1; then \
		echo "FAIL: engine guard did not fire past the schema"; exit 1; \
	elif ! grep -q "flux is the only engine" /tmp/ap-eng-guard.out; then \
		echo "FAIL: engine guard failed for the wrong reason"; cat /tmp/ap-eng-guard.out; exit 1; \
	else echo "ok: engine guard"; fi
	@echo "--> customer BOM pins every range to an exact version"
	@helm template t $(CHART_DIR) -f $(CHART_DIR)/ci/ci-values.yaml -f $(CHART_DIR)/examples/customer-bom.yaml $(ENGINE_OFF) >/tmp/ap-bom.out 2>&1 || { cat /tmp/ap-bom.out; exit 1; }
	@grep -q 'semver: "$(PRESETS_MUSTER_VERSION)"' /tmp/ap-bom.out || { echo "FAIL: BOM did not pin muster to $(PRESETS_MUSTER_VERSION)"; exit 1; }
	@if grep -qE 'semver: "[0-9]+\.x"' /tmp/ap-bom.out; then echo "FAIL: BOM still contains an unpinned x-range"; exit 1; fi
	@echo "ok: customer BOM pinned"
	@echo "--> gitops.namespace routes the Flux CRs to an exempt ns, targetNamespace routes workloads"
	@helm template t $(CHART_DIR) -f $(CHART_DIR)/ci/ci-values.yaml $(ENGINE_OFF) --set gitops.namespace=flux-giantswarm --set gitops.targetNamespace=agent-platform >/tmp/ap-ns.out 2>&1 || { cat /tmp/ap-ns.out; exit 1; }
	@python3 -c 'import re,sys; docs=open("/tmp/ap-ns.out").read().split("\n---\n"); bad=[(re.search(r"^kind: (\S+)$$", d, re.M).group(1), re.search(r"^  namespace: (\S+)$$", d, re.M).group(1)) for d in docs if re.search(r"^kind: (OCIRepository|HelmRelease)$$", d, re.M) and re.search(r"^  namespace: (\S+)$$", d, re.M) and re.search(r"^  namespace: (\S+)$$", d, re.M).group(1) != "flux-giantswarm"]; sys.exit("FAIL: a rendered CR is not in the gitops.namespace: " + str(bad)) if bad else print("ok: all CRs in flux-giantswarm (the hook Jobs stay in the release namespace, where Helm runs them)")'
	@grep -q 'targetNamespace: agent-platform' /tmp/ap-ns.out || { echo "FAIL: HelmRelease targetNamespace not routed"; exit 1; }
	@echo "ok: gitops namespace routing"
	@echo "--> components.<name>.enabled=false skips that component's release"
	@helm template t $(CHART_DIR) -f $(CHART_DIR)/ci/ci-values.yaml $(ENGINE_OFF) --set components.kagent.enabled=false >/tmp/ap-noc.out 2>&1 || { cat /tmp/ap-noc.out; exit 1; }
	@if grep -qE '^  name: kagent$$' /tmp/ap-noc.out; then echo "FAIL: kagent still rendered when disabled"; exit 1; else echo "ok: kagent component skipped"; fi
	@grep -q 'name: muster' /tmp/ap-noc.out || { echo "FAIL: disabling kagent dropped other components"; exit 1; }
	@echo "--> a dependsOn ref to a disabled component is dropped (no dangling dependency)"
	@if grep -qE '^    - name: kagent$$' /tmp/ap-noc.out; then echo "FAIL: connectivity still dependsOn disabled kagent (would block forever)"; exit 1; else echo "ok: dangling dependsOn dropped"; fi
	@echo "--> the meta chart forwards the RESOLVED enablement to the connectivity chart"
	@python3 tests/verify-component-enablement.py $(CHART_DIR) $(CONNECTIVITY_DIR)
	@echo "ok: a disabled component renders neither a release nor its wiring"
	@echo "--> schema symmetry: every key the connectivity chart declares is settable through the meta chart and every key the meta chart forwards is declared by connectivity — nested keys included, not only the top level"
	@python3 tests/verify-schema-symmetry.py $(CHART_DIR) $(CONNECTIVITY_DIR)
	@echo "--> the symmetry check has teeth both ways: a nested key one schema lacks fails it, naming the path"
	@python3 -c 'import json; s=json.load(open("$(CHART_DIR)/values.schema.json")); del s["properties"]["gateway"]["properties"]["parameters"]["properties"]["dataPlaneResources"]; json.dump(s, open("/tmp/ap-sym-meta.json", "w"))'
	@if python3 tests/verify-schema-symmetry.py $(CHART_DIR) $(CONNECTIVITY_DIR) --meta-schema /tmp/ap-sym-meta.json >/tmp/ap-sym-neg-meta.out 2>&1; then \
		echo "FAIL: the symmetry check passed a meta schema without gateway.parameters.dataPlaneResources (the #303 shape)"; exit 1; \
	elif ! grep -q 'gateway.parameters.dataPlaneResources' /tmp/ap-sym-neg-meta.out; then \
		echo "FAIL: the symmetry check failed for the wrong reason"; cat /tmp/ap-sym-neg-meta.out; exit 1; \
	else echo "ok: a nested connectivity key the meta schema lacks fails, naming gateway.parameters.dataPlaneResources"; fi
	@python3 -c 'import json; s=json.load(open("$(CONNECTIVITY_DIR)/values.schema.json")); del s["properties"]["gateway"]["properties"]["parameters"]["properties"]["dataPlaneEnv"]; json.dump(s, open("/tmp/ap-sym-conn.json", "w"))'
	@if python3 tests/verify-schema-symmetry.py $(CHART_DIR) $(CONNECTIVITY_DIR) --connectivity-schema /tmp/ap-sym-conn.json >/tmp/ap-sym-neg-conn.out 2>&1; then \
		echo "FAIL: the symmetry check passed a connectivity schema without gateway.parameters.dataPlaneEnv"; exit 1; \
	elif ! grep -q 'gateway.parameters.dataPlaneEnv' /tmp/ap-sym-neg-conn.out; then \
		echo "FAIL: the symmetry check failed for the wrong reason"; cat /tmp/ap-sym-neg-conn.out; exit 1; \
	else echo "ok: a nested meta key the connectivity schema lacks fails, naming gateway.parameters.dataPlaneEnv"; fi
	@echo "--> mirrored defaults: the meta chart's copy of every networkPolicy fqdns / cidrs list and port, of every leaf of the modelServing namespace, serving, cache, policies, prepull, modelImages and imageVerification blocks of the clusterManager prewarmPriorityClass block and of the agentgateway data plane's resource budget, equals the connectivity chart's default — the forwarded copy shadows the child's (#522, #525, #537, #539, #545, #551, #552, #565)"
	@python3 tests/verify-mirrored-values.py $(CHART_DIR) $(CONNECTIVITY_DIR)
	@echo "--> the mirrored-defaults check has teeth: a meta prewarm PriorityClass under another name fails it, naming the path"
	@python3 -c 'import yaml; v=yaml.safe_load(open("$(CHART_DIR)/values.yaml")); v["clusterManager"]["prewarmPriorityClass"]["name"]="other-placeholder"; yaml.safe_dump(v, open("/tmp/ap-mirror-meta-pc.yaml", "w"))'
	@if python3 tests/verify-mirrored-values.py $(CHART_DIR) $(CONNECTIVITY_DIR) --meta-values /tmp/ap-mirror-meta-pc.yaml >/tmp/ap-mirror-neg-pc.out 2>&1; then \
		echo "FAIL: the mirrored-defaults check passed a meta chart whose clusterManager.prewarmPriorityClass.name differs (every pool's placeholder would name a class the installation lacks)"; exit 1; \
	elif ! grep -q 'clusterManager.prewarmPriorityClass.name' /tmp/ap-mirror-neg-pc.out; then \
		echo "FAIL: the mirrored-defaults check failed for the wrong reason"; cat /tmp/ap-mirror-neg-pc.out; exit 1; \
	else echo "ok: a mirrored class name that differs fails, naming clusterManager.prewarmPriorityClass.name"; fi
	@echo "--> the mirrored-defaults check has teeth: a meta env list short of its entry fails it, naming the path"
	@python3 -c 'import yaml; v=yaml.safe_load(open("$(CHART_DIR)/values.yaml")); v["modelServing"]["policies"]["env"].pop(); yaml.safe_dump(v, open("/tmp/ap-mirror-meta-env.yaml", "w"))'
	@if python3 tests/verify-mirrored-values.py $(CHART_DIR) $(CONNECTIVITY_DIR) --meta-values /tmp/ap-mirror-meta-env.yaml >/tmp/ap-mirror-neg-env.out 2>&1; then \
		echo "FAIL: the mirrored-defaults check passed a meta chart whose modelServing.policies.env lacks the HF_HUB_DISABLE_XET entry (the connectivity release would render without it)"; exit 1; \
	elif ! grep -q 'modelServing.policies.env' /tmp/ap-mirror-neg-env.out; then \
		echo "FAIL: the mirrored-defaults check failed for the wrong reason"; cat /tmp/ap-mirror-neg-env.out; exit 1; \
	else echo "ok: a mirrored env list short of one entry fails, naming modelServing.policies.env"; fi
	@echo "--> the mirrored-defaults check has teeth: a meta list short of one entry fails it, naming the path"
	@python3 -c 'import yaml; v=yaml.safe_load(open("$(CHART_DIR)/values.yaml")); v["modelServing"]["networkPolicy"]["huggingFace"]["fqdns"].pop(); yaml.safe_dump(v, open("/tmp/ap-mirror-meta.yaml", "w"))'
	@if python3 tests/verify-mirrored-values.py $(CHART_DIR) $(CONNECTIVITY_DIR) --meta-values /tmp/ap-mirror-meta.yaml >/tmp/ap-mirror-neg.out 2>&1; then \
		echo "FAIL: the mirrored-defaults check passed a meta chart whose modelServing.networkPolicy.huggingFace.fqdns lacks the CDN pattern (the 4.28.17 shape)"; exit 1; \
	elif ! grep -q 'modelServing.networkPolicy.huggingFace.fqdns' /tmp/ap-mirror-neg.out; then \
		echo "FAIL: the mirrored-defaults check failed for the wrong reason"; cat /tmp/ap-mirror-neg.out; exit 1; \
	else echo "ok: a mirrored list short of one entry fails, naming modelServing.networkPolicy.huggingFace.fqdns"; fi
	@echo "--> gateway.parameters.dataPlaneResources is settable through the meta chart and the override reaches the connectivity release (#303)"
	@helm template t $(CHART_DIR) -f $(CHART_DIR)/ci/ci-values.yaml $(ENGINE_OFF) --set gateway.parameters.dataPlaneResources.limits.ephemeral-storage=1Gi >/tmp/ap-dpr.out 2>&1 || { cat /tmp/ap-dpr.out; exit 1; }
	@python3 -c 'import re,sys; docs=open("/tmp/ap-dpr.out").read().split("\n---\n"); hr=[d for d in docs if "kind: HelmRelease" in d and re.search(r"^  name: agent-platform-connectivity$$", d, re.M)]; sys.exit("FAIL: connectivity HelmRelease not rendered") if not hr else None; v=hr[0][hr[0].index("\n  values:\n"):]; sys.exit("FAIL: dataPlaneResources did not reach the connectivity release values") if "dataPlaneResources:" not in v else None; sys.exit("FAIL: the 1Gi override did not reach the connectivity release (still the 512Mi default)") if "ephemeral-storage: 1Gi" not in v or "ephemeral-storage: 512Mi" in v else print("ok: the override reaches the connectivity release (limits.ephemeral-storage: 1Gi, the 512Mi default replaced)")'
	@echo "--> connectivity chart owns the wiring (renders an HTTPRoute)"
	@helm template t $(CONNECTIVITY_DIR) -f $(CONNECTIVITY_DIR)/ci/ci-values.yaml >/tmp/ap-conn.out 2>&1 || { cat /tmp/ap-conn.out; exit 1; }
	@grep -q 'kind: HTTPRoute' /tmp/ap-conn.out || { echo "FAIL: connectivity did not render the muster HTTPRoute"; exit 1; }
	@echo "ok: connectivity wiring"
	@echo "meta-package render verified."

# LLM routing on, with the agentgateway data plane the listener rides on.
LLM_VM := $(VM) --set ingress.mode=agentgateway-muster --set components.agentgateway.enabled=true --set llmRouting.enabled=true

.PHONY: verify-llm-routing
verify-llm-routing: ## Assert the llmRouting toggle: off renders nothing of the LLM path (the Gateway's metrics policy is not its — verify-metric-labels), on renders the listener + routing, and the guards fire.
	@echo "====> $@ ($(CONNECTIVITY_DIR))"
	@echo "--> off (default): no LLM listener, route, backend, LLM policy or price ConfigMap"
	@helm template t $(CONNECTIVITY_DIR) $(VM) --set ingress.mode=agentgateway-muster --set components.agentgateway.enabled=true --set components.kagent.enabled=true >/tmp/vl-off.out 2>&1 || { cat /tmp/vl-off.out; exit 1; }
	@for pattern in 'AgentgatewayBackend' 'name: agent-platform-connectivity-llm$$' '^  backend:$$' 'sectionName: llm' 'model-catalog' 'modelCatalog'; do \
		if grep -qE -- "$$pattern" /tmp/vl-off.out; then echo "FAIL: llmRouting is off but the render still contains $$pattern"; exit 1; fi; \
	done
	@if grep -qE '^      port: 8081$$' /tmp/vl-off.out; then echo "FAIL: the LLM listener renders with llmRouting off"; exit 1; fi
	@if grep -q 'name: agent-platform-connectivity-dashboard-llm-usage$$' /tmp/vl-off.out; then echo "FAIL: the LLM usage board renders with llmRouting off; every panel of it reads a series the LLM listener alone produces"; exit 1; fi
	@grep -q 'name: agent-platform-connectivity-dashboard-usage-by-person$$' /tmp/vl-off.out || { echo "FAIL: the Usage by person board is gated on llmRouting; it reads the data plane's request series, which exist without the LLM listener"; exit 1; }
	@echo "ok: nothing of the LLM path renders"
	@echo "--> off, agentgateway on: the Gateway's metrics policy renders all the same (the labels are not the LLM path's)"
	@grep -q 'name: agent-platform-connectivity-metrics$$' /tmp/vl-off.out || { echo "FAIL: the -metrics policy is gated on llmRouting; the controller route's metrics would carry no agent label without LLM routing"; exit 1; }
	@echo "ok: metrics policy independent of llmRouting"
	@echo "--> off, agentgateway on: this chart renders no PodMonitor of its own (the packaging chart's is the one scrape path) and the data-plane policy still admits the scrape port"
	@if grep -q 'kind: PodMonitor' /tmp/vl-off.out; then echo "FAIL: this chart renders a data-plane PodMonitor again; with the packaging chart's own monitor on, two monitors of the same pods double every data-plane series"; exit 1; fi
	@grep -A32 'name: agent-platform-connectivity-dataplane$$' /tmp/vl-off.out | grep -q '"15020"' || { echo "FAIL: the data-plane policy does not admit the scrape port; the PodMonitor target reports up=0 and every metric is lost"; exit 1; }
	@echo "ok: no monitor of our own + scrape port"
	@echo "--> off: the meta chart still turns the packaging chart's monitoring on (the MCP path's HTTP and tool-call series are not the LLM path's)"
	@helm template t $(CHART_DIR) -f $(CHART_DIR)/ci/ci-values.yaml --api-versions monitoring.coreos.com/v1 >/tmp/vl-mon.out 2>&1 || { cat /tmp/vl-mon.out; exit 1; }
	@grep -A4 '^      monitoring:$$' /tmp/vl-mon.out | grep -q 'enabled: true' || { echo "FAIL: agentgateway.monitoring.enabled did not resolve on with monitoring served; the gateway would have no scrape and no board"; exit 1; }
	@grep -q 'observability.giantswarm.io/folder: Agent Platform' /tmp/vl-mon.out || { echo "FAIL: the dashboard ConfigMap lost its folder annotation; the board lands in the organization's General folder"; exit 1; }
	@grep -q 'observability.giantswarm.io/organization: Shared Org' /tmp/vl-mon.out || { echo "FAIL: the dashboard ConfigMap lost its organization annotation; customers would not see the board"; exit 1; }
	@echo "ok: monitoring on, board in Shared Org / Agent Platform"
	@echo "--> on: the llm listener, the pinned route, the AI backend, the Gateway policy and the price catalog"
	@helm template t $(CONNECTIVITY_DIR) $(LLM_VM) --set components.kagent.enabled=true $(SUBSTRATE_ON) >/tmp/vl-on.out 2>&1 || { cat /tmp/vl-on.out; exit 1; }
	@grep -q 'name: llm' /tmp/vl-on.out || { echo "FAIL: no llm listener on the Gateway"; exit 1; }
	@grep -q 'sectionName: llm' /tmp/vl-on.out || { echo "FAIL: the LLM route is not pinned to its listener; in edge mode it would attach to the public HTTPS listener"; exit 1; }
	@grep -A4 'kind: AgentgatewayBackend' /tmp/vl-on.out >/dev/null || { echo "FAIL: no AgentgatewayBackend"; exit 1; }
	@grep -A3 '^  ai:' /tmp/vl-on.out | grep -q 'anthropic: {}' || { echo "FAIL: the AI backend is not the Anthropic provider with its defaults"; exit 1; }
	@if grep -q 'policies:' /tmp/vl-on.out; then echo "FAIL: the AI backend carries backend policies; the gateway must hold no credential"; exit 1; fi
	@echo "ok: listener + pinned route + credential-free AI backend"
	@grep -q 'name: agent-platform-connectivity-dashboard-llm-usage$$' /tmp/vl-on.out || { echo "FAIL: llmRouting is on and the LLM usage board does not render"; exit 1; }
	@echo "ok: LLM usage board with the listener"
	@echo "--> the LLM route matches the provider path prefixes, never a bare / (the MCP catch-all wins that tie)"
	@grep -A6 'sectionName: llm' /tmp/vl-on.out | grep -q 'value: "/v1"' || { echo "FAIL: the LLM route does not match the provider path prefix"; exit 1; }
	@if grep -A6 'sectionName: llm' /tmp/vl-on.out | grep -qE 'value: "?/"?$$'; then \
		echo "FAIL: the LLM route matches a bare /; agent-platform-mcps renders a catch-all route on the same Gateway that wins an equal match, so every inference call would reach the MCP backend"; exit 1; \
	fi
	@helm template t $(CONNECTIVITY_DIR) $(LLM_VM) --set 'llmRouting.pathPrefixes[0]=/openai/v1' >/tmp/vl-prefix.out 2>&1 || { cat /tmp/vl-prefix.out; exit 1; }
	@grep -A6 'sectionName: llm' /tmp/vl-prefix.out | grep -q 'value: "/openai/v1"' || { echo "FAIL: llmRouting.pathPrefixes does not reach the route matches"; exit 1; }
	@echo "ok: path prefixes"
	@echo "--> guard: a bare / prefix, and an empty prefix list, must fail"
	@if helm template t $(CONNECTIVITY_DIR) $(LLM_VM) --set 'llmRouting.pathPrefixes[0]=/' >/tmp/vl-slash.out 2>&1; then \
		echo "FAIL: the bare-/ guard did not fire; the MCP catch-all would swallow every inference call"; exit 1; \
	elif ! grep -q "must be more specific" /tmp/vl-slash.out; then \
		echo "FAIL: the bare-/ guard failed for the wrong reason"; cat /tmp/vl-slash.out; exit 1; \
	else echo "ok: bare-/ guard"; fi
	@if helm template t $(CONNECTIVITY_DIR) $(LLM_VM) --set llmRouting.pathPrefixes=null >/tmp/vl-empty.out 2>&1; then \
		echo "FAIL: the empty-prefix-list guard did not fire; the route would match nothing"; exit 1; \
	elif ! grep -q "must list at least one prefix" /tmp/vl-empty.out; then \
		echo "FAIL: the empty-prefix-list guard failed for the wrong reason"; cat /tmp/vl-empty.out; exit 1; \
	else echo "ok: empty-prefix-list guard"; fi
	@echo "--> the LLM policy carries the route-type map INCLUDING the wildcard, and no metrics section (the Gateway's -metrics policy is the one)"
	@grep -q '"/v1/messages": Messages' /tmp/vl-on.out || { echo "FAIL: no Messages route type; the gateway would parse Anthropic bodies as OpenAI Completions"; exit 1; }
	@grep -q '"/v1/messages/count_tokens": AnthropicTokenCount' /tmp/vl-on.out || { echo "FAIL: no AnthropicTokenCount route type"; exit 1; }
	@grep -q '"\*": Passthrough' /tmp/vl-on.out || { echo "FAIL: no wildcard route type; any other path would fall back to Completions parsing"; exit 1; }
	@$(METRICS_POLICIES) /tmp/vl-on.out >/tmp/vl-on-metrics.out
	@[ "$$(cat /tmp/vl-on-metrics.out)" = "agent-platform-connectivity-metrics" ] || { echo "FAIL: the policies with a frontend.metrics section are [$$(tr '\n' ' ' </tmp/vl-on-metrics.out)], not agent-platform-connectivity-metrics alone; the data plane keeps one per Gateway and drops the rest in silence"; exit 1; }
	@awk '/^  name: agent-platform-connectivity-llm$$/{f=1} f&&/^---/{exit} f' /tmp/vl-on.out | grep -q 'frontend:' && { echo "FAIL: the LLM policy carries a frontend section; the labels moved to the -metrics policy"; exit 1; } || true
	@$(METRICS_EXPRS) /tmp/vl-on.out | grep -q '^agent=.* : source.unverifiedWorkload.serviceAccount$$' || { echo "FAIL: no agent attribution label (the source ServiceAccount behind the Substrate egress predicate; verify-metric-labels holds the expression)"; exit 1; }
	@$(METRICS_EXPRS) /tmp/vl-on.out | grep -q '^agent_namespace=.* : source.unverifiedWorkload.namespace$$' || { echo "FAIL: no agent_namespace attribution label"; exit 1; }
	@echo "ok: route-type map on the LLM policy, the labels on the metrics policy"
	@echo "--> the price ConfigMap renders and the AgentgatewayParameters references it"
	@grep -q 'name: t-model-catalog' /tmp/vl-on.out || { echo "FAIL: no model-price ConfigMap; every cost lookup would report NoCatalog"; exit 1; }
	@grep -A4 '^  modelCatalog:' /tmp/vl-on.out | grep -q 'key: catalog.json' || { echo "FAIL: AgentgatewayParameters does not reference the price ConfigMap"; exit 1; }
	@grep -q '"claude-sonnet-4-6"' /tmp/vl-on.out || { echo "FAIL: the platform's default model is unpriced"; exit 1; }
	@echo "ok: price catalog wired"
	@echo "--> the network policies admit the LLM port in both flavors"
	@grep -A24 'name: agent-platform-connectivity-dataplane$$' /tmp/vl-on.out | grep -q '"8081"' || { echo "FAIL: the cilium data-plane policy does not admit the LLM port"; exit 1; }
	@grep -A32 'name: agent-platform-connectivity-dataplane$$' /tmp/vl-on.out | grep -q '"15020"' || { echo "FAIL: the cilium data-plane policy does not admit the scrape port"; exit 1; }
	@awk "/^  name: substrate-atenet-egress$$/,/^---/" /tmp/vl-on.out | grep -q 'gateway.networking.k8s.io/gateway-name: agentgateway' || { echo "FAIL: the actors' egress gateway (substrate-atenet-egress) has no egress to the data plane's LLM port"; exit 1; }
	@helm template t $(CONNECTIVITY_DIR) $(LLM_VM) --set components.kagent.enabled=true --set networkPolicy.flavor=kubernetes >/tmp/vl-k8s.out 2>&1 || { cat /tmp/vl-k8s.out; exit 1; }
	@grep -A24 'name: agent-platform-connectivity-dataplane$$' /tmp/vl-k8s.out | grep -q 'port: 8081' || { echo "FAIL: the kubernetes data-plane policy does not admit the LLM port"; exit 1; }
	@grep -A32 'name: agent-platform-connectivity-dataplane$$' /tmp/vl-k8s.out | grep -q 'port: 15020' || { echo "FAIL: the kubernetes data-plane policy does not admit the scrape port"; exit 1; }
	@echo "ok: network policies"
	@echo "--> guard: llmRouting on with no agentgateway data plane must fail"
	@if helm template t $(CONNECTIVITY_DIR) $(VM) --set llmRouting.enabled=true >/tmp/vl-guard.out 2>&1; then \
		echo "FAIL: llmRouting rendered with no data plane; the cutover would take every agent offline"; exit 1; \
	elif ! grep -q "llmRouting.enabled requires the agentgateway data plane" /tmp/vl-guard.out; then \
		echo "FAIL: the llmRouting guard failed for the wrong reason"; cat /tmp/vl-guard.out; exit 1; \
	else echo "ok: data-plane guard"; fi
	@echo "--> guard: an LLM port that collides with an existing listener must fail"
	@if helm template t $(CONNECTIVITY_DIR) $(LLM_VM) --set llmRouting.listener.port=8080 >/tmp/vl-port.out 2>&1; then \
		echo "FAIL: the port-collision guard did not fire"; exit 1; \
	elif ! grep -q "is already taken by the http listener" /tmp/vl-port.out; then \
		echo "FAIL: the port-collision guard failed for the wrong reason"; cat /tmp/vl-port.out; exit 1; \
	else echo "ok: port-collision guard"; fi
	@echo "--> the CI scenario renders, and a chart-owned ModelConfig can ride the listener"
	@helm template t $(CONNECTIVITY_DIR) -f $(CONNECTIVITY_DIR)/ci/test-llm-routing-values.yaml >/tmp/vl-ci.out 2>&1 || { cat /tmp/vl-ci.out; exit 1; }
	@grep -A3 'anthropic:' /tmp/vl-ci.out | grep -q 'baseUrl: "http://agentgateway.default.svc:8081"' || { echo "FAIL: a chart-owned ModelConfig does not reach the listener; that agent would bypass it"; exit 1; }
	@echo "ok: CI scenario"
	@echo "--> a ModelConfig that names no baseUrl rides the listener"
	@awk '/name: "anthropic-sonnet"/{f=1} f&&/^---/{exit} f' /tmp/vl-ci.out | grep -q 'baseUrl: "http://agentgateway.default.svc:8081"' || { echo "FAIL: an entry with no baseUrl stayed direct; a new model would be unmetered by default"; exit 1; }
	@echo "ok: routed by default"
	@echo "--> an explicit baseUrl wins (the escape hatch), and an entry for another provider stays direct"
	@awk '/name: "anthropic-opus-direct"/{f=1} f&&/^---/{exit} f' /tmp/vl-ci.out | grep -q 'baseUrl: "https://api.anthropic.com"' || { echo "FAIL: an explicit baseUrl was overwritten by the listener default; a model could never leave the gateway"; exit 1; }
	@helm template t $(CONNECTIVITY_DIR) -f $(CONNECTIVITY_DIR)/ci/test-llm-routing-values.yaml --set 'kagent.modelConfigs[0].provider=OpenAI' >/tmp/vl-other.out 2>&1 || { cat /tmp/vl-other.out; exit 1; }
	@if awk '/name: "anthropic-sonnet"/{f=1} f&&/^---/{exit} f' /tmp/vl-other.out | grep -q 'baseUrl:'; then \
		echo "FAIL: a non-Anthropic model was pointed at the Anthropic listener; it would reach the wrong upstream"; exit 1; \
	else echo "ok: explicit URL and foreign provider"; fi
	@echo "--> an entry whose provider has no baseUrl in ModelConfigSpec fails the render, naming the entry"
	@if helm template t $(CONNECTIVITY_DIR) -f $(CONNECTIVITY_DIR)/ci/test-llm-routing-values.yaml --set 'kagent.modelConfigs[1].provider=Ollama' >/tmp/vl-nourl.out 2>&1; then \
		echo "FAIL: a baseUrl rendered under a provider block the CRD prunes; the model would stay direct in silence"; exit 1; \
	else grep -q 'anthropic-opus-direct' /tmp/vl-nourl.out || { cat /tmp/vl-nourl.out; echo "FAIL: the failure does not name the entry"; exit 1; }; fi
	@echo "ok: provider without a baseUrl"
	@echo "--> a non-Anthropic entry's baseUrl goes under the CRD's block key (openAI), never the lower-cased provider"
	@awk '/name: "openai-gpt-direct"/{f=1} f&&/^---/{exit} f' /tmp/vl-ci.out | grep -A1 '^  openAI:$$' | grep -q 'baseUrl: "https://api.openai.com/v1"' || { echo "FAIL: the OpenAI entry's baseUrl is not under spec.openAI; the API server would prune it and the model would stay direct in silence"; exit 1; }
	@if grep -qE '^  (openai|sapaicore):$$' /tmp/vl-ci.out; then echo "FAIL: a lower-cased provider block rendered; the CRD knows openAI and sapAICore only"; exit 1; fi
	@helm template t $(CONNECTIVITY_DIR) -f $(CONNECTIVITY_DIR)/ci/test-llm-routing-values.yaml --set llmRouting.backend.provider=openai --set 'kagent.modelConfigs[0].provider=OpenAI' >/tmp/vl-openai-routed.out 2>&1 || { cat /tmp/vl-openai-routed.out; exit 1; }
	@awk '/name: "anthropic-sonnet"/{f=1} f&&/^---/{exit} f' /tmp/vl-openai-routed.out | grep -A1 '^  openAI:$$' | grep -q 'baseUrl: "http://agentgateway.default.svc:8081"' || { echo "FAIL: the routed default for an OpenAI listener is not under spec.openAI; every routed OpenAI model would be pruned to the direct path"; exit 1; }
	@echo "ok: openAI block key, explicit and routed"
	@echo "--> guard: a provider outside the CRD's enum fails the render, naming the entry and the enum"
	@if helm template t $(CONNECTIVITY_DIR) -f $(CONNECTIVITY_DIR)/ci/test-llm-routing-values.yaml --set 'kagent.modelConfigs[0].provider=openai' >/tmp/vl-enum.out 2>&1; then \
		echo "FAIL: provider 'openai' rendered; the API server refuses it at admission (the enum is case-sensitive) after the chart said nothing"; exit 1; \
	elif ! grep -q 'anthropic-sonnet' /tmp/vl-enum.out || ! grep -q 'Anthropic, OpenAI, AzureOpenAI, Ollama, Gemini, GeminiVertexAI, AnthropicVertexAI, Bedrock, SAPAICore, Foundry' /tmp/vl-enum.out; then \
		cat /tmp/vl-enum.out; echo "FAIL: the enum guard does not name the entry and the CRD's enum"; exit 1; \
	else echo "ok: enum guard"; fi
	@echo "--> the MutatingAdmissionPolicy writes the provider's own block name"
	@helm template t $(CONNECTIVITY_DIR) -f $(CONNECTIVITY_DIR)/ci/test-llm-routing-values.yaml -a admissionregistration.k8s.io/v1/MutatingAdmissionPolicy --set llmRouting.backend.provider=openai >/tmp/vl-openai.out 2>&1 || { cat /tmp/vl-openai.out; exit 1; }
	@grep -q 'object.spec.openAI.baseUrl' /tmp/vl-openai.out || { echo "FAIL: the policy reads the lower-cased provider name, not the ModelConfigSpec block; every CEL evaluation would error"; exit 1; }
	@if helm template t $(CONNECTIVITY_DIR) -f $(CONNECTIVITY_DIR)/ci/test-llm-routing-values.yaml -a admissionregistration.k8s.io/v1/MutatingAdmissionPolicy --set llmRouting.backend.provider=ollama 2>/dev/null | grep -q 'kind: MutatingAdmissionPolicy'; then \
		echo "FAIL: the policy renders for a provider whose block carries no baseUrl; the mutation would be pruned"; exit 1; \
	else echo "ok: provider block name"; fi
	@echo "--> the MutatingAdmissionPolicy renders only where the API server serves the GA group"
	@if grep -q 'kind: MutatingAdmissionPolicy' /tmp/vl-ci.out; then \
		echo "FAIL: the policy rendered without the GA API version; on 1.34/1.35 it would exist and mutate nothing"; exit 1; \
	fi
	@helm template t $(CONNECTIVITY_DIR) -f $(CONNECTIVITY_DIR)/ci/test-llm-routing-values.yaml -a admissionregistration.k8s.io/v1/MutatingAdmissionPolicy >/tmp/vl-map.out 2>&1 || { cat /tmp/vl-map.out; exit 1; }
	@grep -q 'kind: MutatingAdmissionPolicy$$' /tmp/vl-map.out || { echo "FAIL: no MutatingAdmissionPolicy on a 1.36 API server; a ModelConfig created outside the chart would be unmetered"; exit 1; }
	@grep -q 'kind: MutatingAdmissionPolicyBinding' /tmp/vl-map.out || { echo "FAIL: the policy has no binding; it matches nothing"; exit 1; }
	@grep -q 'failurePolicy: Ignore' /tmp/vl-map.out || { echo "FAIL: the policy fails closed; a CEL error would block every ModelConfig write"; exit 1; }
	@grep -q 'kubernetes.io/metadata.name: kagent' /tmp/vl-map.out || { echo "FAIL: the policy is not scoped to the kagent namespace"; exit 1; }
	@grep -q 'base-url-absent' /tmp/vl-map.out || { echo "FAIL: the policy has no baseUrl guard; it would overwrite a model pointed at another upstream"; exit 1; }
	@grep -q 'baseUrl: "http://agentgateway.default.svc:8081"' /tmp/vl-map.out || { echo "FAIL: the policy writes the wrong listener URL"; exit 1; }
	@if helm template t $(CONNECTIVITY_DIR) -f $(CONNECTIVITY_DIR)/ci/test-llm-routing-values.yaml -a admissionregistration.k8s.io/v1/MutatingAdmissionPolicy --set llmRouting.modelConfigPolicy.enabled=false 2>/dev/null | grep -q 'kind: MutatingAdmissionPolicy'; then \
		echo "FAIL: llmRouting.modelConfigPolicy.enabled=false still rendered the policy"; exit 1; \
	else echo "ok: admission policy gated on the GA group and its own toggle"; fi
	@echo "--> the meta chart forwards the cutover value to the kagent release"
	@helm template t $(CHART_DIR) -f $(CHART_DIR)/ci/ci-values.yaml --set kagent.providers.anthropic.config.baseUrl=http://agentgateway.default.svc:8081 >/tmp/vl-meta.out 2>&1 || { cat /tmp/vl-meta.out; exit 1; }
	@awk '/^kind: HelmRelease$$/{h=1} h&&/^  name: kagent$$/{f=1} f&&/^---/{exit} f' /tmp/vl-meta.out >/tmp/vl-meta-kagent.out
	@grep -A8 '^    providers:$$' /tmp/vl-meta-kagent.out | grep -q 'baseUrl: http://agentgateway.default.svc:8081' || { echo "FAIL: the cutover value never reaches the kagent HelmRelease (flat, kagent 0.2.0+: providers.anthropic.config.baseUrl at the values root); the default ModelConfig would stay direct"; exit 1; }
	@echo "ok: cutover forwarded"
	@echo "All llmRouting behaviors verified."

# The names of the AgentgatewayPolicy documents of a render that carry a
# frontend.metrics section, one per line. The data plane honours ONE metrics
# policy per Gateway (custom labels replace rather than merge; of two policies
# one is kept and the other dropped in silence, and which one has changed
# between agentgateway releases), so the platform renders exactly one,
# <release>-metrics, and no other policy of the chart may carry the section.
METRICS_POLICIES := python3 -c 'import re,sys; docs=open(sys.argv[1]).read().split("\n---\n"); [print(re.search(r"^  name: (\S+)", d, re.M).group(1)) for d in docs if "kind: AgentgatewayPolicy\n" in d and re.search(r"^  frontend:\n(?:.*\n)*?    metrics:", d, re.M)]'
# The document of the -metrics policy, from a render.
METRICS_DOC := awk '/^  name: agent-platform-connectivity-metrics$$/{f=1} f&&/^---/{exit} f'
# name=expression of every label the -metrics policy adds, parsed: toYaml folds an expression longer than a line across lines, so a grep of the rendered text cannot hold one.
METRICS_EXPRS := python3 -c 'import sys,yaml; [print(a["name"]+"="+a["expression"]) for d in yaml.safe_load_all(open(sys.argv[1])) if d and d.get("kind")=="AgentgatewayPolicy" and d["metadata"]["name"].endswith("-metrics") for a in d["spec"]["frontend"]["metrics"]["attributes"]["add"]]'
# gateway.metricLabels as the meta chart forwards it to the connectivity HelmRelease, name=enabled=expression, the expression as written (the meta chart must not render it).
META_METRIC_LABELS := python3 -c 'import sys,yaml; hr=[d for d in yaml.safe_load_all(open(sys.argv[1])) if d and d.get("kind")=="HelmRelease" and d["metadata"]["name"]=="agent-platform-connectivity"][0]; [print(n+"="+str(e.get("enabled",True))+"="+e["expression"]) for n,e in sorted(hr["spec"]["values"]["gateway"]["metricLabels"].items())]'
# The Substrate egress predicate the default expressions read the kagent runtime's identity headers behind (agent-platform.substrate.egressCall, giantswarm/agent-platform#586): the fixed Substrate namespace and the egress workload's ServiceAccount.
EGRESS_CALL := (source.unverifiedWorkload.namespace == "ate-system" && source.unverifiedWorkload.serviceAccount == "atenet-egress")
# The data plane on, no route verifying a bearer: jwt. entries are held here;
# KAGENT_ROUTE and MANAGERS_ON + MANAGERS_ROUTES render them.
AGW_VM := $(VM) --set ingress.mode=agentgateway-muster --set components.agentgateway.enabled=true
# $(call vml_must_fail,<description>,<NAME of a variable holding the flags — JSON has commas, which call would split on>,<message fragment>)
define vml_must_fail
	@if helm template t $(CONNECTIVITY_DIR) $($(2)) >/tmp/vml-fail.out 2>&1; then \
		echo "FAIL: $(1): the render succeeded"; exit 1; \
	elif ! grep -qF -- "$(3)" /tmp/vml-fail.out; then \
		echo "FAIL: $(1): failed for the wrong reason"; cat /tmp/vml-fail.out; exit 1; \
	else echo "ok: $(1)"; fi
endef
# $(call vml_must_fail_schema,<description>,<NAME of a variable holding the flags>,<case-insensitive regex, no commas>): a refusal by the
# schema. Its wording differs between Helm 3 (gojsonschema: "Additional property x is not allowed", "Invalid type. Expected: object, given:
# string") and Helm 4 ("additional properties 'x' not allowed", "got string, want object"); CI runs one, a laptop may run the other.
define vml_must_fail_schema
	@if helm template t $(CONNECTIVITY_DIR) $($(2)) >/tmp/vml-fail.out 2>&1; then \
		echo "FAIL: $(1): the render succeeded"; exit 1; \
	elif ! grep -q "values don't meet the specifications of the schema" /tmp/vml-fail.out || ! grep -qiE -- "$(3)" /tmp/vml-fail.out; then \
		echo "FAIL: $(1): failed for the wrong reason"; cat /tmp/vml-fail.out; exit 1; \
	else echo "ok: $(1)"; fi
endef
# Recursive (=): KAGENT_ROUTE and MANAGERS_ON are defined further down. A --set-json on one key merges with the three defaults.
VML_NO_EXPR = $(AGW_VM) --set-json 'gateway.metricLabels.channel={}'
# The schema holds every entry's shape, a custom one (admitted by any name) like a default: a misspelt key, a scalar entry, a string for enabled.
VML_BAD_KEY_DEFAULT = $(AGW_VM) --set-json 'gateway.metricLabels.user={"enable":false}'
VML_BAD_KEY = $(AGW_VM) --set-json 'gateway.metricLabels.team={"expression":"jwt.groups","enable":false}'
VML_SCALAR = $(AGW_VM) --set gateway.metricLabels.team=foo
VML_ENABLED_STRING = $(AGW_VM) --set-json 'gateway.metricLabels.team={"expression":"jwt.groups","enabled":"false"}'
VML_EMPTY_TPL = $(AGW_VM) --set-json 'gateway.metricLabels.channel={"expression":"{{ \"\" }}"}'
VML_MULTILINE = $(AGW_VM) --set-json 'gateway.metricLabels.channel={"expression":"has(jwt.aud)\n? jwt.aud : 1"}'
# jwt.* entries the gate holds: the name guards run on them all the same.
VML_BAD_NAME = $(AGW_VM) --set-json 'gateway.metricLabels.agent-name={"expression":"jwt.aud"}'
VML_EMPTY_NAME = $(AGW_VM) --set-json 'gateway.metricLabels={"":{"expression":"jwt.aud"}}'
VML_RESERVED = $(AGW_VM) --set-json 'gateway.metricLabels.route={"expression":"jwt.aud"}'
VML_SCRAPE = $(AGW_VM) --set-json 'gateway.metricLabels.pod={"expression":"jwt.aud"}'
VML_DUNDER = $(AGW_VM) --set-json 'gateway.metricLabels.__meta_team={"expression":"jwt.aud"}'
# The claim is checked where it is read as CEL: the user entry on the managers' routes, controller route off — and kagent off altogether.
VML_BAD_CLAIM = $(MANAGERS_ON) $(MANAGERS_ROUTES) --set kagent.controller.auth.userIdClaim=https://example.com/email
VML_BAD_CLAIM_NO_KAGENT = $(MANAGERS_ON) $(MANAGERS_ROUTES) --set components.kagent.enabled=false --set components.agent-manager.enabled=false --set kagent.controller.auth.userIdClaim=https://example.com/email
# Fourteen plus the three defaults: seventeen.
VML_SEVENTEEN = $(KAGENT_ROUTE) --set-json 'gateway.metricLabels=$(shell python3 -c 'import json; print(json.dumps({"l%d" % i: {"expression": "jwt.aud"} for i in range(14)}))')'
# A boolean-looking name, on a non-jwt expression so it renders with the data plane alone.
VML_ON = $(AGW_VM) --set-json 'gateway.metricLabels.on={"expression":"source.unverifiedWorkload.name"}'

.PHONY: verify-metric-labels
verify-metric-labels: ## Assert the data plane's metric labels: one Gateway-scoped -metrics policy from gateway.metricLabels (a map — one entry off or one more without restating the rest; tpl; an entry that reads jwt. held until a route verifies a bearer), the three defaults reading the kagent runtime's identity headers behind the Substrate egress predicate and nowhere else (#586), an installation's own expression replacing a default, no policy with nothing enabled or in muster-direct, one template with a frontend section, the meta chart's forwarding (the expressions as written), the retired llmRouting.metricLabels refused, the schema holding every entry's shape, the claim guard where the claim is read and silent elsewhere, and the guards.
	@echo "====> $@ ($(CONNECTIVITY_DIR))"
	@echo "--> the data plane on, no route verifying a bearer: one -metrics policy on the Gateway, the two agent labels, nothing that reads jwt. — the default user entry and a custom claim are held (they would read unknown on every series)"
	@helm template t $(CONNECTIVITY_DIR) $(AGW_VM) --set-json 'gateway.metricLabels.team={"expression":"jwt.groups"}' --set-json 'gateway.metricLabels.org={"expression":"jwt[\"https://example.com/org\"]"}' >/tmp/vml-on.out 2>&1 || { cat /tmp/vml-on.out; exit 1; }
	@$(METRICS_DOC) /tmp/vml-on.out >/tmp/vml-pol.out
	@[ -s /tmp/vml-pol.out ] || { echo "FAIL: no agent-platform-connectivity-metrics policy with the data plane on"; exit 1; }
	@grep -q 'kind: Gateway' /tmp/vml-pol.out && grep -q 'name: agentgateway' /tmp/vml-pol.out || { echo "FAIL: the metrics policy does not target the data-plane Gateway (frontend.metrics may target nothing else)"; exit 1; }
	@$(METRICS_EXPRS) /tmp/vml-on.out >/tmp/vml-exprs.out
	@grep -qxF 'agent=$(EGRESS_CALL) ? request.headers["x-kagent-agent"] : source.unverifiedWorkload.serviceAccount' /tmp/vml-exprs.out || { echo "FAIL: the agent label is not the runtime's x-kagent-agent header behind the Substrate egress predicate, else the source ServiceAccount: $$(grep '^agent=' /tmp/vml-exprs.out)"; exit 1; }
	@grep -qxF 'agent_namespace=$(EGRESS_CALL) ? request.headers["x-kagent-agent-namespace"] : source.unverifiedWorkload.namespace' /tmp/vml-exprs.out || { echo "FAIL: the agent_namespace label is not the runtime's x-kagent-agent-namespace header behind the Substrate egress predicate, else the source namespace: $$(grep '^agent_namespace=' /tmp/vml-exprs.out)"; exit 1; }
	@if grep -qE 'jwt[.[]' /tmp/vml-pol.out; then echo "FAIL: a jwt label rendered with no route verifying a bearer; it would read unknown on every series"; exit 1; fi
	@[ "$$(wc -l </tmp/vml-exprs.out | tr -d ' ')" = "2" ] || { echo "FAIL: the policy does not carry exactly the two agent labels: $$(cut -d= -f1 /tmp/vml-exprs.out | tr '\n' ' ')"; exit 1; }
	@echo "--> the header is read behind the predicate and nowhere else: the predicate's namespace and ServiceAccount are the chart's Substrate names, and the rendered policy parses back to one line per expression"
	@grep -q 'define "agent-platform.substrate.namespace" -}}ate-system{{' $(CONNECTIVITY_DIR)/templates/_helpers.tpl && grep -q 'define "agent-platform.substrate.egressServiceAccount" -}}atenet-egress{{' $(CONNECTIVITY_DIR)/templates/_helpers.tpl || { echo "FAIL: the Substrate names the predicate is built from moved; update EGRESS_CALL and the README"; exit 1; }
	@[ "$$(grep -c 'request.headers\[' /tmp/vml-exprs.out)" = "2" ] && ! grep -v '^[a-z_]*=$(EGRESS_CALL) ?' /tmp/vml-exprs.out | grep -q 'request.headers' || { echo "FAIL: a default expression reads a request header outside the Substrate egress predicate"; cat /tmp/vml-exprs.out; exit 1; }
	@[ "$$($(METRICS_POLICIES) /tmp/vml-on.out)" = "agent-platform-connectivity-metrics" ] || { echo "FAIL: the policies with a frontend.metrics section are not agent-platform-connectivity-metrics alone"; exit 1; }
	@echo "ok: data plane alone"
	@echo "--> the controller route with its JWT policy: user = jwt.email and both custom claims with it — five labels, in name order"
	@helm template t $(CONNECTIVITY_DIR) $(KAGENT_ROUTE) --set-json 'gateway.metricLabels.team={"expression":"jwt.groups"}' --set-json 'gateway.metricLabels.org={"expression":"jwt[\"https://example.com/org\"]"}' >/tmp/vml-route.out 2>&1 || { cat /tmp/vml-route.out; exit 1; }
	@$(METRICS_DOC) /tmp/vml-route.out >/tmp/vml-route-pol.out
	@$(METRICS_EXPRS) /tmp/vml-route.out >/tmp/vml-route-exprs.out
	@grep -qxF 'user=$(EGRESS_CALL) ? request.headers["x-kagent-user"] : jwt.email' /tmp/vml-route-exprs.out || { echo "FAIL: the person label is not the runtime's x-kagent-user header behind the Substrate egress predicate, else jwt.email, with the controller route on: $$(grep '^user=' /tmp/vml-route-exprs.out)"; exit 1; }
	@grep -qxF 'team=jwt.groups' /tmp/vml-route-exprs.out || { echo "FAIL: a custom jwt entry does not render with the controller route on"; exit 1; }
	@grep -qxF 'org=jwt["https://example.com/org"]' /tmp/vml-route-exprs.out || { echo "FAIL: an indexed claim entry (jwt[...]) does not render with the controller route on"; exit 1; }
	@[ "$$(cut -d= -f1 /tmp/vml-route-exprs.out | tr '\n' ' ')" = "agent agent_namespace org team user " ] || { echo "FAIL: the policy does not carry exactly agent, agent_namespace, org, team, user in name order: $$(cut -d= -f1 /tmp/vml-route-exprs.out | tr '\n' ' ')"; exit 1; }
	@[ "$$($(METRICS_POLICIES) /tmp/vml-route.out)" = "agent-platform-connectivity-metrics" ] || { echo "FAIL: with the controller route on, the policies with a frontend.metrics section are [$$($(METRICS_POLICIES) /tmp/vml-route.out | tr '\n' ' ')], not the metrics policy alone (the route's JWT policy is a traffic policy)"; exit 1; }
	@echo "ok: jwt entries with the controller route"
	@echo "--> the managers' routes with their JWT policies gate it too; the expression follows the claim knob through tpl; enabled false drops an entry and keeps the rest; a renamed person label is user off plus one more entry"
	@helm template t $(CONNECTIVITY_DIR) $(MANAGERS_ON) $(MANAGERS_ROUTES) >/tmp/vml-managers.out 2>&1 || { cat /tmp/vml-managers.out; exit 1; }
	@$(METRICS_EXPRS) /tmp/vml-managers.out | grep -qxF 'user=$(EGRESS_CALL) ? request.headers["x-kagent-user"] : jwt.email' || { echo "FAIL: the managers' JWT routes do not render the person label; their series carry the claim too"; exit 1; }
	@helm template t $(CONNECTIVITY_DIR) $(KAGENT_ROUTE) --set kagent.controller.auth.userIdClaim=sub >/tmp/vml-claim.out 2>&1 || { cat /tmp/vml-claim.out; exit 1; }
	@$(METRICS_EXPRS) /tmp/vml-claim.out | grep -qxF 'user=$(EGRESS_CALL) ? request.headers["x-kagent-user"] : jwt.sub' || { echo "FAIL: the user entry does not follow kagent.controller.auth.userIdClaim behind the predicate (its expression is a tpl of the knob)"; exit 1; }
	@helm template t $(CONNECTIVITY_DIR) $(MANAGERS_ON) $(MANAGERS_ROUTES) --set kagent.controller=null >/tmp/vml-nocontroller.out 2>&1 || { cat /tmp/vml-nocontroller.out; exit 1; }
	@$(METRICS_EXPRS) /tmp/vml-nocontroller.out | grep -qxF 'user=$(EGRESS_CALL) ? request.headers["x-kagent-user"] : jwt.email' || { echo "FAIL: with the kagent.controller block deleted the user entry does not fall back to jwt.email (the expression includes the claim helper, which carries the default)"; exit 1; }
	@helm template t $(CONNECTIVITY_DIR) $(KAGENT_ROUTE) --set gateway.metricLabels.user.enabled=false >/tmp/vml-user-off.out 2>&1 || { cat /tmp/vml-user-off.out; exit 1; }
	@if $(METRICS_DOC) /tmp/vml-user-off.out | grep -q 'jwt\.'; then echo "FAIL: gateway.metricLabels.user.enabled=false still rendered a jwt label"; exit 1; fi
	@$(METRICS_EXPRS) /tmp/vml-user-off.out | grep -q '^agent=.* : source.unverifiedWorkload.serviceAccount$$' || { echo "FAIL: turning the person label off lost the agent labels (a map merges; the other defaults must stay)"; exit 1; }
	@helm template t $(CONNECTIVITY_DIR) $(KAGENT_ROUTE) --set gateway.metricLabels.agent.expression=source.unverifiedWorkload.serviceAccount >/tmp/vml-own.out 2>&1 || { cat /tmp/vml-own.out; exit 1; }
	@$(METRICS_EXPRS) /tmp/vml-own.out | grep -qxF 'agent=source.unverifiedWorkload.serviceAccount' || { echo "FAIL: an installation's own agent expression does not replace the default, predicate and all"; exit 1; }
	@$(METRICS_EXPRS) /tmp/vml-own.out | grep -qxF 'agent_namespace=$(EGRESS_CALL) ? request.headers["x-kagent-agent-namespace"] : source.unverifiedWorkload.namespace' || { echo "FAIL: replacing one default expression changed another (a map merges)"; exit 1; }
	@helm template t $(CONNECTIVITY_DIR) $(KAGENT_ROUTE) --set gateway.metricLabels.user.enabled=false --set-json 'gateway.metricLabels.person={"expression":"jwt.{{ .Values.kagent.controller.auth.userIdClaim }}"}' >/tmp/vml-person.out 2>&1 || { cat /tmp/vml-person.out; exit 1; }
	@$(METRICS_EXPRS) /tmp/vml-person.out | grep -qxF 'person=jwt.email' || { echo "FAIL: a renamed person label (user off, person with the same tpl expression) does not render"; exit 1; }
	@if $(METRICS_EXPRS) /tmp/vml-person.out | grep -q '^user='; then echo "FAIL: user still renders beside person"; exit 1; fi
	@echo "ok: gate, tpl, toggle, own expression, rename"
	@echo "--> nothing enabled, no policy — the map deleted, or the agent entries off with the user entry held; the person label alone is a policy; nothing in muster-direct"
	@helm template t $(CONNECTIVITY_DIR) $(AGW_VM) --set gateway.metricLabels=null >/tmp/vml-none.out 2>&1 || { cat /tmp/vml-none.out; exit 1; }
	@if grep -q 'name: agent-platform-connectivity-metrics$$' /tmp/vml-none.out; then echo "FAIL: a metrics policy with nothing to add rendered; the API server refuses an empty add list"; exit 1; fi
	@helm template t $(CONNECTIVITY_DIR) $(AGW_VM) --set gateway.metricLabels.agent.enabled=false --set gateway.metricLabels.agent_namespace.enabled=false >/tmp/vml-gated.out 2>&1 || { cat /tmp/vml-gated.out; exit 1; }
	@if grep -q 'name: agent-platform-connectivity-metrics$$' /tmp/vml-gated.out; then echo "FAIL: a metrics policy rendered with only the held user entry left and no route verifying a bearer"; exit 1; fi
	@helm template t $(CONNECTIVITY_DIR) $(KAGENT_ROUTE) --set gateway.metricLabels.agent.enabled=false --set gateway.metricLabels.agent_namespace.enabled=false >/tmp/vml-useronly.out 2>&1 || { cat /tmp/vml-useronly.out; exit 1; }
	@$(METRICS_EXPRS) /tmp/vml-useronly.out | grep -qxF 'user=$(EGRESS_CALL) ? request.headers["x-kagent-user"] : jwt.email' || { echo "FAIL: the person label alone renders no policy"; exit 1; }
	@[ "$$($(METRICS_EXPRS) /tmp/vml-useronly.out | wc -l | tr -d ' ')" = "1" ] || { echo "FAIL: the agent entries rendered although disabled"; exit 1; }
	@helm template t $(CONNECTIVITY_DIR) $(VM) >/tmp/vml-direct.out 2>&1 || { cat /tmp/vml-direct.out; exit 1; }
	@if grep -q 'frontend:' /tmp/vml-direct.out; then echo "FAIL: a metrics policy rendered in muster-direct, where there is no data plane"; exit 1; fi
	@echo "ok: empty, held-only, person-only, muster-direct"
	@echo "--> with llmRouting on the labels stay on the -metrics policy and the LLM policy carries none; one template of the chart carries a frontend.metrics section"
	@helm template t $(CONNECTIVITY_DIR) $(LLM_VM) >/tmp/vml-llm.out 2>&1 || { cat /tmp/vml-llm.out; exit 1; }
	@[ "$$($(METRICS_POLICIES) /tmp/vml-llm.out)" = "agent-platform-connectivity-metrics" ] || { echo "FAIL: with llmRouting on, the policies with a frontend.metrics section are [$$($(METRICS_POLICIES) /tmp/vml-llm.out | tr '\n' ' ')], not the metrics policy alone"; exit 1; }
	@[ "$$(grep -lPz '(?m)^  frontend:\n    metrics:' $(CONNECTIVITY_DIR)/templates/*/*.yaml | wc -l | tr -d ' ')" = "1" ] || { echo "FAIL: more than one template of the chart carries a frontend.metrics section: $$(grep -lPz '(?m)^  frontend:\n    metrics:' $(CONNECTIVITY_DIR)/templates/*/*.yaml | tr '\n' ' '); a second metrics policy is dropped in silence by the data plane"; exit 1; }
	@echo "ok: one metrics policy, one template"
	@echo "--> a name YAML 1.1 reads as a boolean is rendered quoted"
	@helm template t $(CONNECTIVITY_DIR) $(VML_ON) >/tmp/vml-quoted.out 2>&1 || { cat /tmp/vml-quoted.out; exit 1; }
	@grep -q '^          - name: "on"$$' /tmp/vml-quoted.out || { echo "FAIL: the label name on is not quoted; a YAML 1.1 reader makes it a boolean and the API server refuses the policy"; exit 1; }
	@helm template t $(CONNECTIVITY_DIR) $(AGW_VM) --set-json 'gateway.metricLabels.m={"expression":"{\"a\": 1}[\"a\"]"}' >/tmp/vml-brace.out 2>&1 || { cat /tmp/vml-brace.out; exit 1; }
	@$(METRICS_DOC) /tmp/vml-brace.out | grep -A1 '^          - name: m$$' | grep -qE "expression: ['\"]\{" || { echo "FAIL: an expression starting with { is not quoted; a YAML reader takes it for a flow mapping"; $(METRICS_DOC) /tmp/vml-brace.out | grep -A1 'name: m$$'; exit 1; }
	@echo "ok: boolean-looking name and brace-leading expression quoted"
	@echo "--> guards"
	$(call vml_must_fail,an entry without an expression,VML_NO_EXPR,gateway.metricLabels.channel has no expression)
	$(call vml_must_fail_schema,a misspelt enabled on a default entry (the schema),VML_BAD_KEY_DEFAULT,additional propert(y|ies) .?enable.? (is )?not allowed)
	$(call vml_must_fail_schema,a misspelt enabled on a custom entry (the schema too),VML_BAD_KEY,additional propert(y|ies) .?enable.? (is )?not allowed)
	$(call vml_must_fail_schema,a scalar in place of an entry,VML_SCALAR,got string. want object|Expected: object. given: string)
	$(call vml_must_fail_schema,a string for enabled,VML_ENABLED_STRING,got string. want boolean|Expected: boolean. given: string)
	$(call vml_must_fail,an expression that renders empty,VML_EMPTY_TPL,gateway.metricLabels.channel: the expression)
	$(call vml_must_fail,a multi-line expression,VML_MULTILINE,gateway.metricLabels.channel spans more than one line)
	$(call vml_must_fail,a name that is not a Prometheus label name — on an entry the gate holds,VML_BAD_NAME,is not a Prometheus label name)
	$(call vml_must_fail,an empty name,VML_EMPTY_NAME,metric label \"\" (gateway.metricLabels) is not a Prometheus label name)
	$(call vml_must_fail,a name the data plane already emits,VML_RESERVED,already puts on its series)
	$(call vml_must_fail,a name the scrape adds,VML_SCRAPE,store the policy's as exported_pod)
	$(call vml_must_fail,a __ name,VML_DUNDER,starts with __)
	$(call vml_must_fail,an identity claim that is no CEL identifier with the controller route off,VML_BAD_CLAIM,is not a plain claim name)
	$(call vml_must_fail,the same with kagent off (model-manager's route alone reads it),VML_BAD_CLAIM_NO_KAGENT,is not a plain claim name)
	@helm template t $(CONNECTIVITY_DIR) $(VM) --set kagent.controller.auth.userIdClaim=https://example.com/email >/tmp/vml-claim-inert.out 2>&1 || { echo "FAIL: a non-identifier claim fails the render in muster-direct, where nothing reads it as CEL"; cat /tmp/vml-claim-inert.out; exit 1; }
	@echo "ok: the claim guard is silent where nothing reads the claim"
	$(call vml_must_fail,more than 16 enabled entries,VML_SEVENTEEN,the AgentgatewayPolicy CRD takes at most 16)
	@echo "--> the meta chart forwards the map — one entry off, the tpl expression as written for the connectivity release to render — and its schema refuses the retired llmRouting.metricLabels"
	@helm template t $(CHART_DIR) -f $(CHART_DIR)/ci/ci-values.yaml --set gateway.metricLabels.user.enabled=false >/tmp/vml-meta.out 2>&1 || { cat /tmp/vml-meta.out; exit 1; }
	@$(META_METRIC_LABELS) /tmp/vml-meta.out >/tmp/vml-meta-labels.out
	@grep -qxF 'user=False={{ include "agent-platform.substrate.egressCall" . }} ? request.headers["x-kagent-user"] : jwt.{{ include "agent-platform.kagent.userIdClaim" . }}' /tmp/vml-meta-labels.out || { echo "FAIL: gateway.metricLabels.user (enabled false, the tpl expression as written — the meta chart must not render it) does not reach the connectivity HelmRelease: $$(grep '^user=' /tmp/vml-meta-labels.out)"; exit 1; }
	@grep -qxF 'agent=True={{ include "agent-platform.substrate.egressCall" . }} ? request.headers["x-kagent-agent"] : source.unverifiedWorkload.serviceAccount' /tmp/vml-meta-labels.out || { echo "FAIL: gateway.metricLabels.agent does not reach the connectivity HelmRelease as written: $$(grep '^agent=' /tmp/vml-meta-labels.out)"; exit 1; }
	@if helm template t $(CHART_DIR) -f $(CHART_DIR)/ci/ci-values.yaml --set 'llmRouting.metricLabels[0].name=agent' --set 'llmRouting.metricLabels[0].expression=x' >/tmp/vml-retired.out 2>&1; then \
		echo "FAIL: the meta chart accepted llmRouting.metricLabels; the value would reach the connectivity release and be refused there on every installation"; exit 1; \
	elif ! grep -q "values don't meet the specifications of the schema" /tmp/vml-retired.out || ! grep -q "metricLabels" /tmp/vml-retired.out; then \
		echo "FAIL: llmRouting.metricLabels was refused, but not by the schema naming the key"; cat /tmp/vml-retired.out; exit 1; \
	else echo "ok: forwarded map, retired key refused"; fi
	@echo "All metric-label behaviors verified."

# The agentgateway data plane is the platform's critical path: every MCP call
# and, with llmRouting on, every model call crosses the Deployment the
# controller reconciles from the Gateway. The AgentgatewayParameters shapes it
# for a node reboot or drain: two replicas, a PodDisruptionBudget and a
# hostname spread, each behind a knob, with guards on the budget shapes that
# Kubernetes rejects or that would allow no eviction and hang every drain.
AGP_DOC := awk '/^kind: AgentgatewayParameters$$/{f=1} f{print} f&&/^---/{exit}'
# $(call vha_must_fail,<description>,<helm flags>,<message fragment>)
define vha_must_fail
	@if helm template t $(CONNECTIVITY_DIR) $(LLM_VM) $(2) >/tmp/vha-fail.out 2>&1; then \
		echo "FAIL: $(1): the render succeeded"; exit 1; \
	elif ! grep -q "$(3)" /tmp/vha-fail.out; then \
		echo "FAIL: $(1): failed for the wrong reason"; cat /tmp/vha-fail.out; exit 1; \
	else echo "ok: $(1)"; fi
endef
# Same, for a value the SCHEMA must reject rather than a template guard. The
# two are asserted apart: helm's own wording for a failed constraint moves
# between releases (v3 "Must be greater than or equal to 1" at a dotted path,
# v4 "minimum: got 0" at a JSON pointer), so pinning the constraint's text
# pins the helm binary. The preamble does not move, and it is what tells a
# schema rejection from a template `fail` -- which is the distinction the
# wrong-reason arm exists to make. $(3) is the values key.
# $(call vha_must_fail_schema,<description>,<helm flags>,<values key>)
define vha_must_fail_schema
	@if helm template t $(CONNECTIVITY_DIR) $(LLM_VM) $(2) >/tmp/vha-fail.out 2>&1; then \
		echo "FAIL: $(1): the render succeeded"; exit 1; \
	elif ! grep -q "values don't meet the specifications of the schema" /tmp/vha-fail.out; then \
		echo "FAIL: $(1): the render failed, but NOT on the schema -- a template guard or another error fired instead, so this case no longer asserts the schema"; cat /tmp/vha-fail.out; exit 1; \
	elif ! grep -q "$(3)" /tmp/vha-fail.out; then \
		echo "FAIL: $(1): the schema rejected the values, but the rejection does not name $(3)"; cat /tmp/vha-fail.out; exit 1; \
	else echo "ok: $(1)"; fi
endef
.PHONY: verify-dataplane-ha
verify-dataplane-ha: ## Assert the agentgateway data plane's availability shape: two replicas, a PodDisruptionBudget (maxUnavailable 1 from the template), a hostname spread and a whole resource budget (cpu + memory on BOTH sides, not only ephemeral-storage: CPU_LIMIT is a resourceFieldRef on this container's own limits.cpu, and an unset limit resolves against the node's allocatable capacity) by default on the AgentgatewayParameters, the pod selector following gateway.name, one constraint per topologyKeys entry, the knobs off, minAvailable and a percentage passed through — also through the meta chart, where a null never reaches the connectivity defaults — the guards (both budget fields, every zero-eviction budget, a fractional or non-percentage value, spread without a key, the schema minimums and the whenUnsatisfiable enum), none in muster-direct, the meta chart forwarding the keys at the same defaults and two controller replicas.
	@echo "====> $@ ($(CONNECTIVITY_DIR))"
	@echo "--> default: replicas 2, PDB maxUnavailable 1, one hostname spread constraint selecting the data-plane pods"
	@helm template t $(CONNECTIVITY_DIR) $(LLM_VM) >/tmp/vha-on.out 2>&1 || { cat /tmp/vha-on.out; exit 1; }
	@$(AGP_DOC) /tmp/vha-on.out >/tmp/vha-params.out
	@grep -qE '^      replicas: 2$$' /tmp/vha-params.out || { echo "FAIL: the data-plane Deployment is not asked for two replicas; one pod is one node reboot away from taking every agent offline"; exit 1; }
	@grep -A2 '^  podDisruptionBudget:$$' /tmp/vha-params.out | grep -q 'maxUnavailable: 1' || { echo "FAIL: no PodDisruptionBudget with maxUnavailable 1; a drain could evict both data-plane pods at once"; exit 1; }
	@if grep -q 'minAvailable' /tmp/vha-params.out; then echo "FAIL: the default budget uses minAvailable; a lone replica could never be evicted"; exit 1; fi
	@grep -A5 '^          topologySpreadConstraints:$$' /tmp/vha-params.out | grep -q 'topologyKey: kubernetes.io/hostname' || { echo "FAIL: no hostname topologySpreadConstraint; both replicas may land on the node that reboots"; exit 1; }
	@grep -A5 '^          topologySpreadConstraints:$$' /tmp/vha-params.out | grep -q 'whenUnsatisfiable: ScheduleAnyway' || { echo "FAIL: the spread is not ScheduleAnyway; a single-node lab would leave the second pod Pending"; exit 1; }
	@grep -A8 '^          topologySpreadConstraints:$$' /tmp/vha-params.out | grep -q 'gateway.networking.k8s.io/gateway-name: agentgateway' || { echo "FAIL: the spread constraint does not select the data-plane pods by the Gateway's name label"; exit 1; }
	@grep -A10 '^          topologySpreadConstraints:$$' /tmp/vha-params.out | grep -A1 'matchLabelKeys:' | grep -q 'pod-template-hash' || { echo "FAIL: the spread constraint does not carry matchLabelKeys: [pod-template-hash]; a rollout balances the surge pod against the OLD revision's pods, so once those drain both survivors can be left on one node and ScheduleAnyway never moves them back"; exit 1; }
	@if [ "$$(grep -c 'topologyKey:' /tmp/vha-params.out)" != "1" ]; then echo "FAIL: expected exactly one topologySpreadConstraint by default"; exit 1; fi
	@echo "ok: default shape"
	@echo "--> the data-plane container carries a whole budget: cpu and memory on BOTH sides, not only ephemeral-storage"
	@$(AGP_DOC) /tmp/vha-on.out | awk '/^              resources:$$/{f=1;next} f&&/^              [a-zA-Z]/{exit} f' >/tmp/vha-res.out
	@awk '/^                limits:$$/{f=1;next} f&&/^                [a-zA-Z]/{exit} f' /tmp/vha-res.out >/tmp/vha-res-limits.out
	@awk '/^                requests:$$/{f=1;next} f&&/^                [a-zA-Z]/{exit} f' /tmp/vha-res.out >/tmp/vha-res-requests.out
	@grep -q 'cpu:' /tmp/vha-res-limits.out || { echo "FAIL: the data-plane container has no cpu limit; the generated pod carries CPU_LIMIT as a resourceFieldRef on limits.cpu, so an unset limit resolves against the NODE's allocatable capacity and the proxy sizes its worker threads for whatever node it lands on"; exit 1; }
	@grep -q 'memory:' /tmp/vha-res-limits.out || { echo "FAIL: the data-plane container has no memory limit; nothing caps the proxy every MCP call crosses before it threatens the node it runs on"; exit 1; }
	@grep -q 'ephemeral-storage:' /tmp/vha-res-limits.out || { echo "FAIL: the data-plane container lost its ephemeral-storage limit; the controller injects a writable /tmp emptyDir without a sizeLimit"; exit 1; }
	@grep -q 'cpu:' /tmp/vha-res-requests.out || { echo "FAIL: the data-plane container has no cpu request; the scheduler reserves nothing for the proxy every MCP call crosses, so it is first to starve under node CPU pressure"; exit 1; }
	@grep -q 'memory:' /tmp/vha-res-requests.out || { echo "FAIL: the data-plane container has no memory request"; exit 1; }
	@grep -q 'ephemeral-storage:' /tmp/vha-res-requests.out || { echo "FAIL: the data-plane container lost its ephemeral-storage request; the require-emptydir-requests-and-limits Kyverno policy denies the pod without it"; exit 1; }
	@echo "ok: whole resource budget"
	@echo "--> the budget is a knob: an installation's own limits reach the container, and a millicore value is accepted (the cpu limit IS what CPU_LIMIT resolves to, rounded up to whole cores)"
	@helm template t $(CONNECTIVITY_DIR) $(LLM_VM) --set gateway.parameters.dataPlaneResources.limits.cpu=500m --set gateway.parameters.dataPlaneResources.limits.memory=1Gi >/tmp/vha-res-set.out 2>&1 || { cat /tmp/vha-res-set.out; exit 1; }
	@$(AGP_DOC) /tmp/vha-res-set.out | awk '/^              resources:$$/{f=1;next} f&&/^              [a-zA-Z]/{exit} f' >/tmp/vha-res-set.res
	@awk '/^                limits:$$/{f=1;next} f&&/^                [a-zA-Z]/{exit} f' /tmp/vha-res-set.res >/tmp/vha-res-set-limits.out
	@grep -q 'cpu: 500m' /tmp/vha-res-set-limits.out || { echo "FAIL: an installation's cpu limit does not reach the data-plane container"; exit 1; }
	@grep -q 'memory: 1Gi' /tmp/vha-res-set-limits.out || { echo "FAIL: an installation's memory limit does not reach the data-plane container"; exit 1; }
	@echo "ok: budget knob"
	@echo "--> the pod selector follows gateway.name, and each topologyKeys entry is one constraint"
	@helm template t $(CONNECTIVITY_DIR) $(LLM_VM) --set gateway.name=edge --set-json 'gateway.parameters.spread.topologyKeys=["kubernetes.io/hostname","topology.kubernetes.io/zone"]' >/tmp/vha-two.out 2>&1 || { cat /tmp/vha-two.out; exit 1; }
	@$(AGP_DOC) /tmp/vha-two.out >/tmp/vha-two-params.out
	@if [ "$$(grep -c 'topologyKey:' /tmp/vha-two-params.out)" != "2" ] || ! grep -q 'topologyKey: topology.kubernetes.io/zone' /tmp/vha-two-params.out; then echo "FAIL: two topologyKeys did not render two constraints"; exit 1; fi
	@if [ "$$(grep -c 'gateway.networking.k8s.io/gateway-name: edge' /tmp/vha-two-params.out)" != "2" ] || grep -q 'gateway-name: agentgateway' /tmp/vha-two-params.out; then echo "FAIL: the spread selector does not follow gateway.name; the constraint would select nothing"; exit 1; fi
	@echo "ok: selector + keys"
	@echo "--> knobs off: one replica, no budget, no spread"
	@helm template t $(CONNECTIVITY_DIR) $(LLM_VM) --set gateway.parameters.replicas=1 --set gateway.parameters.podDisruptionBudget.enabled=false --set gateway.parameters.spread.enabled=false >/tmp/vha-off.out 2>&1 || { cat /tmp/vha-off.out; exit 1; }
	@$(AGP_DOC) /tmp/vha-off.out >/tmp/vha-off-params.out
	@grep -qE '^      replicas: 1$$' /tmp/vha-off-params.out || { echo "FAIL: gateway.parameters.replicas does not reach the Deployment"; exit 1; }
	@if grep -qE 'podDisruptionBudget|topologySpreadConstraints' /tmp/vha-off-params.out; then echo "FAIL: the budget or the spread renders with its knob off"; exit 1; fi
	@echo "ok: knobs off"
	@echo "--> a set budget field is passed through as written, with no maxUnavailable next to it: minAvailable 1, minAvailable 50%, maxUnavailable 50%, unhealthyPodEvictionPolicy"
	@helm template t $(CONNECTIVITY_DIR) $(LLM_VM) --set gateway.parameters.podDisruptionBudget.minAvailable=1 >/tmp/vha-min.out 2>&1 || { cat /tmp/vha-min.out; exit 1; }
	@$(AGP_DOC) /tmp/vha-min.out >/tmp/vha-min-params.out
	@grep -A2 '^  podDisruptionBudget:$$' /tmp/vha-min-params.out | grep -q 'minAvailable: 1' || { echo "FAIL: minAvailable 1 below replicas 2 was not rendered"; exit 1; }
	@if grep -q 'maxUnavailable' /tmp/vha-min-params.out; then echo "FAIL: the template's maxUnavailable default renders next to an operator's minAvailable; Kubernetes rejects the budget"; exit 1; fi
	@helm template t $(CONNECTIVITY_DIR) $(LLM_VM) --set gateway.parameters.podDisruptionBudget.minAvailable=50% >/tmp/vha-pct.out 2>&1 || { cat /tmp/vha-pct.out; exit 1; }
	@$(AGP_DOC) /tmp/vha-pct.out >/tmp/vha-pct-params.out
	@grep -A2 '^  podDisruptionBudget:$$' /tmp/vha-pct-params.out | grep -q 'minAvailable: 50%' || { echo "FAIL: a percentage minAvailable was not rendered"; exit 1; }
	@echo "--> unhealthyPodEvictionPolicy set ALONE survives the template's own default: the default fills in the missing budget field, it does not replace the spec"
	@helm template t $(CONNECTIVITY_DIR) $(LLM_VM) --set gateway.parameters.podDisruptionBudget.unhealthyPodEvictionPolicy=AlwaysAllow >/tmp/vha-pol.out 2>&1 || { cat /tmp/vha-pol.out; exit 1; }
	@$(AGP_DOC) /tmp/vha-pol.out >/tmp/vha-pol-params.out
	@grep -A3 '^  podDisruptionBudget:$$' /tmp/vha-pol-params.out | grep -q 'unhealthyPodEvictionPolicy: AlwaysAllow' || { echo "FAIL: unhealthyPodEvictionPolicy set on its own is dropped by the template's default budget; the operator asked for AlwaysAllow to unblock drains past an unhealthy data-plane pod and silently got IfHealthyBudget"; exit 1; }
	@grep -A3 '^  podDisruptionBudget:$$' /tmp/vha-pol-params.out | grep -q 'maxUnavailable: 1' || { echo "FAIL: the template's maxUnavailable default is gone when only unhealthyPodEvictionPolicy is set"; exit 1; }
	@helm template t $(CONNECTIVITY_DIR) $(LLM_VM) --set gateway.parameters.podDisruptionBudget.maxUnavailable=50% --set gateway.parameters.podDisruptionBudget.unhealthyPodEvictionPolicy=AlwaysAllow >/tmp/vha-maxpct.out 2>&1 || { cat /tmp/vha-maxpct.out; exit 1; }
	@$(AGP_DOC) /tmp/vha-maxpct.out >/tmp/vha-maxpct-params.out
	@grep -A3 '^  podDisruptionBudget:$$' /tmp/vha-maxpct-params.out | grep -q 'maxUnavailable: 50%' || { echo "FAIL: a percentage maxUnavailable was not rendered"; exit 1; }
	@grep -A3 '^  podDisruptionBudget:$$' /tmp/vha-maxpct-params.out | grep -q 'unhealthyPodEvictionPolicy: AlwaysAllow' || { echo "FAIL: unhealthyPodEvictionPolicy is not passed through"; exit 1; }
	@echo "ok: budget pass-through"
	@echo "--> through the meta chart: minAvailable set there reaches the connectivity render alone (a null never reaches this chart's defaults; the default is the template's)"
	@helm template t $(CHART_DIR) -f $(CHART_DIR)/ci/ci-values.yaml $(ENGINE_OFF) --set gateway.parameters.podDisruptionBudget.minAvailable=1 >/tmp/vha-meta-min.out 2>&1 || { cat /tmp/vha-meta-min.out; exit 1; }
	@awk '/^kind: HelmRelease$$/{h=1} h&&/^  name: agent-platform-connectivity$$/{f=1} f&&/^---/{exit} f' /tmp/vha-meta-min.out | awk '/^    gateway:$$/{f=1;print;next} f&&/^    [a-zA-Z]/{exit} f' | sed 's/^    //' >/tmp/vha-meta-min-gw.yaml
	@grep -q '^gateway:' /tmp/vha-meta-min-gw.yaml || { echo "FAIL: could not extract the forwarded gateway block"; exit 1; }
	@helm template t $(CONNECTIVITY_DIR) $(LLM_VM) -f /tmp/vha-meta-min-gw.yaml >/tmp/vha-meta-min-conn.out 2>&1 || { echo "FAIL: the connectivity chart refuses the gateway block the meta chart forwards with minAvailable set (#373 review: a null cannot unset a connectivity default across the forward)"; cat /tmp/vha-meta-min-conn.out | tail -3; exit 1; }
	@$(AGP_DOC) /tmp/vha-meta-min-conn.out >/tmp/vha-meta-min-params.out
	@grep -A2 '^  podDisruptionBudget:$$' /tmp/vha-meta-min-params.out | grep -q 'minAvailable: 1' || { echo "FAIL: minAvailable set through the meta chart did not reach the AgentgatewayParameters"; exit 1; }
	@if grep -q 'maxUnavailable' /tmp/vha-meta-min-params.out; then echo "FAIL: maxUnavailable renders next to the minAvailable set through the meta chart"; exit 1; fi
	@echo "ok: minAvailable through the meta chart"
	@echo "--> guards: both budget fields; every zero-eviction budget; a fractional or non-percentage value; spread with no key"
	$(call vha_must_fail,both-fields guard,--set gateway.parameters.podDisruptionBudget.minAvailable=1 --set gateway.parameters.podDisruptionBudget.maxUnavailable=1,sets both minAvailable and maxUnavailable)
	$(call vha_must_fail,minAvailable equal to replicas,--set gateway.parameters.podDisruptionBudget.minAvailable=2,is not below gateway.parameters.replicas)
	$(call vha_must_fail,minAvailable 100%,--set gateway.parameters.podDisruptionBudget.minAvailable=100%,rounds up to every replica)
	$(call vha_must_fail,minAvailable 51% of 2 (rounds up to 2),--set gateway.parameters.podDisruptionBudget.minAvailable=51%,rounds up to every replica)
	$(call vha_must_fail,maxUnavailable 0,--set gateway.parameters.podDisruptionBudget.maxUnavailable=0,allows no eviction)
	$(call vha_must_fail,maxUnavailable 0%,--set gateway.parameters.podDisruptionBudget.maxUnavailable=0%,allows no eviction)
	$(call vha_must_fail,fractional minAvailable (a float from JSON or a values file),--set-json gateway.parameters.podDisruptionBudget.minAvailable=1.5,is not a whole number)
	$(call vha_must_fail,fractional minAvailable (--set hands Helm the string),--set gateway.parameters.podDisruptionBudget.minAvailable=1.5,is neither an integer nor a percentage)
	$(call vha_must_fail,a numeric string budget (no %),--set-string gateway.parameters.podDisruptionBudget.maxUnavailable=1,is neither an integer nor a percentage)
	@helm template t $(CONNECTIVITY_DIR) $(LLM_VM) --set-json gateway.parameters.podDisruptionBudget.minAvailable=1 >/tmp/vha-float.out 2>&1 || { echo "FAIL: a whole number from JSON (float64 1) was refused as fractional"; tail -2 /tmp/vha-float.out; exit 1; }
	@$(AGP_DOC) /tmp/vha-float.out >/tmp/vha-float-params.out
	@grep -A2 '^  podDisruptionBudget:$$' /tmp/vha-float-params.out | grep -q 'minAvailable: 1' || { echo "FAIL: a whole number from JSON did not render as minAvailable: 1"; exit 1; }
	@echo "ok: a whole float passes"
	$(call vha_must_fail,empty-keys guard,--set gateway.parameters.spread.topologyKeys=null,needs at least one gateway.parameters.spread.topologyKeys)
	@echo "--> guards: a negative or over-100% budget; a key the budget block does not pass through; an unhealthyPodEvictionPolicy outside the API's enum"
	$(call vha_must_fail,negative maxUnavailable,--set-json gateway.parameters.podDisruptionBudget.maxUnavailable=-1,maxUnavailable=-1 is negative)
	$(call vha_must_fail,negative minAvailable,--set-json gateway.parameters.podDisruptionBudget.minAvailable=-1,minAvailable=-1 is negative)
	$(call vha_must_fail,maxUnavailable over 100%,--set gateway.parameters.podDisruptionBudget.maxUnavailable=200%,is neither an integer nor a percentage from 0% to 100%)
	$(call vha_must_fail,a misspelt budget field (the schema leaves the block open),--set gateway.parameters.podDisruptionBudget.minAvailabe=1,minAvailabe is not one of the PodDisruptionBudget spec fields)
	$(call vha_must_fail,a budget key that is not a PDB spec field,--set gateway.parameters.podDisruptionBudget.bogusKey=x,bogusKey is not one of the PodDisruptionBudget spec fields)
	$(call vha_must_fail,a misspelt budget field with the budget OFF (the guard is outside the enabled check, so the typo is caught now and not on the day the budget is turned on),--set gateway.parameters.podDisruptionBudget.enabled=false --set gateway.parameters.podDisruptionBudget.minAvailabe=1,minAvailabe is not one of the PodDisruptionBudget spec fields)
	$(call vha_must_fail,unhealthyPodEvictionPolicy enum,--set gateway.parameters.podDisruptionBudget.unhealthyPodEvictionPolicy=Always,is not a PodDisruptionBudget eviction policy)
	@echo "--> guards: a key Helm DELETED rather than set — a null through the meta chart, an emptied entry in a values file — which no schema keyword can see"
	$(call vha_must_fail,replicas unset (nil would render 0 and scale the data plane to zero),--set gateway.parameters.replicas=null,gateway.parameters.replicas is unset)
	$(call vha_must_fail,maxSkew unset (nil would render maxSkew: 0),--set gateway.parameters.spread.maxSkew=null,gateway.parameters.spread.maxSkew is unset)
	$(call vha_must_fail,whenUnsatisfiable unset (nil would render an empty value),--set gateway.parameters.spread.whenUnsatisfiable=null,gateway.parameters.spread.whenUnsatisfiable is unset)
	$(call vha_must_fail,an empty topologyKeys entry,--set 'gateway.parameters.spread.topologyKeys[0]=',topologyKeys has an empty entry)
	@echo "--> schema: replicas and maxSkew at least 1, whenUnsatisfiable an enum (asserted as a SCHEMA rejection naming the key, so a render that fails for another reason cannot pass for it)"
	$(call vha_must_fail_schema,replicas 0 refused by the schema,--set gateway.parameters.replicas=0,replicas)
	$(call vha_must_fail_schema,maxSkew 0 refused by the schema,--set gateway.parameters.spread.maxSkew=0,maxSkew)
	$(call vha_must_fail_schema,whenUnsatisfiable enum,--set gateway.parameters.spread.whenUnsatisfiable=Maybe,whenUnsatisfiable)
	@echo "--> muster-direct (no data plane): no AgentgatewayParameters at all"
	@helm template t $(CONNECTIVITY_DIR) $(VM) >/tmp/vha-direct.out 2>&1 || { cat /tmp/vha-direct.out; exit 1; }
	@if grep -q 'kind: AgentgatewayParameters' /tmp/vha-direct.out; then echo "FAIL: AgentgatewayParameters renders without the agentgateway data plane"; exit 1; else echo "ok: none in muster-direct"; fi
	@echo "--> the meta chart forwards the keys at the same defaults, and two controller replicas"
	@helm template t $(CHART_DIR) -f $(CHART_DIR)/ci/ci-values.yaml $(ENGINE_OFF) >/tmp/vha-meta.out 2>&1 || { cat /tmp/vha-meta.out; exit 1; }
	@awk '/^kind: HelmRelease$$/{h=1} h&&/^  name: agent-platform-connectivity$$/{f=1} f&&/^---/{exit} f' /tmp/vha-meta.out >/tmp/vha-meta-conn.out
	@awk '/^    gateway:$$/{f=1;print;next} f&&/^    [a-zA-Z]/{exit} f' /tmp/vha-meta-conn.out >/tmp/vha-meta-gw.out
	@grep -qE '^        replicas: 2$$' /tmp/vha-meta-gw.out || { echo "FAIL: the meta chart does not forward gateway.parameters.replicas: 2 to the connectivity release"; exit 1; }
	@grep -A1 '^        podDisruptionBudget:$$' /tmp/vha-meta-gw.out | grep -q 'enabled: true' || { echo "FAIL: the meta chart does not forward the budget switch"; exit 1; }
	@if grep -A2 '^        podDisruptionBudget:$$' /tmp/vha-meta-gw.out | grep -q 'maxUnavailable'; then echo "FAIL: the meta chart carries a maxUnavailable default; that default belongs to the connectivity template so a null set through the meta chart is not needed"; exit 1; fi
	@grep -A6 '^        spread:$$' /tmp/vha-meta-gw.out | grep -q 'kubernetes.io/hostname' || { echo "FAIL: the meta chart does not forward the default spread"; exit 1; }
	@awk '/^        dataPlaneResources:$$/{f=1;next} f&&/^        [a-zA-Z]/{exit} f' /tmp/vha-meta-gw.out | awk '/^          limits:$$/{g=1;next} g&&/^          [a-zA-Z]/{exit} g' >/tmp/vha-meta-res-limits.out
	@grep -q 'cpu:' /tmp/vha-meta-res-limits.out || { echo "FAIL: the meta chart does not forward a cpu limit for the data plane; its forwarded copy shadows the connectivity default, so an installation's container renders without one"; exit 1; }
	@grep -q 'memory:' /tmp/vha-meta-res-limits.out || { echo "FAIL: the meta chart does not forward a memory limit for the data plane; its forwarded copy shadows the connectivity default"; exit 1; }
	@./tests/verify-agentgateway-wiring.py /tmp/vha-meta.out
	@echo "ok: forwarded + controller replicas"
	@echo "All data-plane availability behaviors verified."

DPT_VM := $(VM) --set components.agentgateway.enabled=true --set ingress.mode=agentgateway-muster
DPT_POLICY := agent-platform-connectivity-tracing
DPT_EGRESS := agent-platform-connectivity-dataplane-otlp-egress

.PHONY: verify-dataplane-tracing
verify-dataplane-tracing: ## Assert the agentgateway data plane's trace export (giantswarm/giantswarm#36711): the connectivity chart renders the Gateway-scoped -tracing AgentgatewayPolicy (frontend.tracing, url and protocol from gateway.parameters.dataPlaneEnv, global.observability.traces.otlp winning when its endpoint is set) and -dataplane-otlp-egress (DNS + the endpoint's namespace on its port in the cilium flavour, the cluster entity for a host that is not an in-cluster Service, a namespaceSelector in the kubernetes one) exactly while an endpoint is set; none in muster-direct or with no endpoint, no egress policy with networkPolicy off; gateway.parameters.podLabels reaches the pod template; gateway.tracing.randomSampling reaches the policy as a quoted CEL literal (the chart's 0.1 by default, a number or a boolean an installation sets as written; empty, null and 0 render none; the schema refuses what is not a literal between 0 and 1); the meta chart forwards the tenant label and the same sampling default.
	@echo "====> $@ ($(CHART_DIR) + $(CONNECTIVITY_DIR))"
	@helm template t $(CONNECTIVITY_DIR) $(DPT_VM) --set 'gateway.parameters.podLabels.observability\.giantswarm\.io/tenant=giantswarm' >/tmp/vdt.out 2>&1 || { cat /tmp/vdt.out; exit 1; }
	@$(PICK) /tmp/vdt.out AgentgatewayPolicy $(DPT_POLICY) >/tmp/vdt-pol.out || { echo "FAIL: no AgentgatewayPolicy $(DPT_POLICY) with the default data-plane endpoint"; exit 1; }
	@grep -q '^      kind: Gateway$$' /tmp/vdt-pol.out && grep -q '^      name: agentgateway$$' /tmp/vdt-pol.out || { echo "FAIL: the tracing policy does not target the data-plane Gateway"; cat /tmp/vdt-pol.out; exit 1; }
	@grep -q '^      url: "http://otlp-gateway.kube-system.svc:4317"$$' /tmp/vdt-pol.out && grep -q '^      protocol: GRPC$$' /tmp/vdt-pol.out || { echo "FAIL: the tracing policy does not export to the dataPlaneEnv endpoint over gRPC"; cat /tmp/vdt-pol.out; exit 1; }
	@$(PICK) /tmp/vdt.out CiliumNetworkPolicy $(DPT_EGRESS) >/tmp/vdt-cnp.out || { echo "FAIL: no CiliumNetworkPolicy $(DPT_EGRESS)"; exit 1; }
	@grep -q 'gateway.networking.k8s.io/gateway-name: agentgateway' /tmp/vdt-cnp.out && grep -q 'io.kubernetes.pod.namespace: kube-system$$' /tmp/vdt-cnp.out && grep -q 'port: "4317"' /tmp/vdt-cnp.out && grep -q 'k8s-app: kube-dns' /tmp/vdt-cnp.out || { echo "FAIL: the egress policy does not admit DNS and kube-system:4317 for the data-plane pods"; cat /tmp/vdt-cnp.out; exit 1; }
	@if grep -q 'world\|kube-apiserver\|toCIDR' /tmp/vdt-cnp.out; then echo "FAIL: the egress policy admits more than DNS and the collector"; exit 1; fi
	@grep -A3 '^      template:$$' /tmp/vdt.out | grep -q '^            observability.giantswarm.io/tenant: giantswarm$$' || { echo "FAIL: gateway.parameters.podLabels does not reach the data-plane pod template"; exit 1; }
	@grep -q '^      randomSampling: "0.1"$$' /tmp/vdt-pol.out || { echo "FAIL: the tracing policy does not start traces for 0.1 of the requests with no traceparent (gateway.tracing.randomSampling)"; cat /tmp/vdt-pol.out; exit 1; }
	@echo "ok: defaults"
	@helm template t $(CONNECTIVITY_DIR) $(DPT_VM) --set gateway.tracing.randomSampling=1 >/tmp/vdt-rs1.out 2>&1 || { cat /tmp/vdt-rs1.out; exit 1; }
	@$(PICK) /tmp/vdt-rs1.out AgentgatewayPolicy $(DPT_POLICY) | grep -q '^      randomSampling: "1"$$' || { echo "FAIL: a number set by an installation does not reach randomSampling as its literal"; exit 1; }
	@helm template t $(CONNECTIVITY_DIR) $(DPT_VM) --set-string gateway.tracing.randomSampling=false >/tmp/vdt-rsb.out 2>&1 || { cat /tmp/vdt-rsb.out; exit 1; }
	@$(PICK) /tmp/vdt-rsb.out AgentgatewayPolicy $(DPT_POLICY) | grep -q '^      randomSampling: "false"$$' || { echo "FAIL: a boolean set by an installation does not reach randomSampling as written"; exit 1; }
	@for v in 'gateway.tracing.randomSampling=' 'gateway.tracing.randomSampling=null' 'gateway.tracing.randomSampling=0'; do \
	  helm template t $(CONNECTIVITY_DIR) $(DPT_VM) --set "$$v" >/tmp/vdt-rs0.out 2>&1 || { cat /tmp/vdt-rs0.out; exit 1; }; \
	  $(PICK) /tmp/vdt-rs0.out AgentgatewayPolicy $(DPT_POLICY) >/tmp/vdt-rs0-pol.out || { echo "FAIL: no tracing policy with $$v (sampling off must keep the export of traced requests)"; exit 1; }; \
	  if grep -q 'randomSampling' /tmp/vdt-rs0-pol.out; then echo "FAIL: $$v still renders randomSampling"; exit 1; fi; \
	done
	@for v in 10% 1.5 always; do \
	  if helm template t $(CONNECTIVITY_DIR) $(DPT_VM) --set-string "gateway.tracing.randomSampling=$$v" >/tmp/vdt-rsbad.out 2>&1; then echo "FAIL: randomSampling $$v passed the schema"; exit 1; \
	  elif ! grep -q 'randomSampling' /tmp/vdt-rsbad.out; then echo "FAIL: randomSampling $$v failed for the wrong reason"; cat /tmp/vdt-rsbad.out; exit 1; fi; \
	done
	@echo "ok: random sampling"
	@helm template t $(CONNECTIVITY_DIR) $(DPT_VM) --set global.observability.traces.otlp.endpoint=https://collector.example.com --set global.observability.traces.otlp.protocol=http/protobuf >/tmp/vdt-global.out 2>&1 || { cat /tmp/vdt-global.out; exit 1; }
	@$(PICK) /tmp/vdt-global.out AgentgatewayPolicy $(DPT_POLICY) | grep -q '^      url: "https://collector.example.com"$$' || { echo "FAIL: global.observability.traces.otlp.endpoint does not win"; exit 1; }
	@$(PICK) /tmp/vdt-global.out AgentgatewayPolicy $(DPT_POLICY) | grep -q '^      protocol: HTTP$$' || { echo "FAIL: http/protobuf does not map to HTTP"; exit 1; }
	@$(PICK) /tmp/vdt-global.out CiliumNetworkPolicy $(DPT_EGRESS) | grep -q 'port: "443"' || { echo "FAIL: an external https collector is not on 443"; exit 1; }
	@echo "ok: the global endpoint wins"
	@helm template t $(CONNECTIVITY_DIR) $(DPT_VM) --set networkPolicy.flavor=kubernetes --set networkPolicy.kubernetes.apiServerCIDR=10.9.0.1/32 >/tmp/vdt-k8s.out 2>&1 || { cat /tmp/vdt-k8s.out; exit 1; }
	@$(PICK) /tmp/vdt-k8s.out NetworkPolicy $(DPT_EGRESS) >/tmp/vdt-k8s-pol.out || { echo "FAIL: no NetworkPolicy $(DPT_EGRESS) in the kubernetes flavour"; exit 1; }
	@grep -q 'kubernetes.io/metadata.name: kube-system' /tmp/vdt-k8s-pol.out && grep -q 'port: 4317' /tmp/vdt-k8s-pol.out || { echo "FAIL: the NetworkPolicy does not admit kube-system:4317"; cat /tmp/vdt-k8s-pol.out; exit 1; }
	@echo "ok: kubernetes flavour"
	@helm template t $(CONNECTIVITY_DIR) $(DPT_VM) --set 'gateway.parameters.dataPlaneEnv=null' >/tmp/vdt-none.out 2>&1 || { cat /tmp/vdt-none.out; exit 1; }
	@if grep -q 'name: $(DPT_POLICY)$$\|name: $(DPT_EGRESS)$$' /tmp/vdt-none.out; then echo "FAIL: tracing objects render with no endpoint"; exit 1; fi
	@helm template t $(CONNECTIVITY_DIR) $(VM) >/tmp/vdt-direct.out 2>&1 || { cat /tmp/vdt-direct.out; exit 1; }
	@if grep -q 'name: $(DPT_POLICY)$$\|name: $(DPT_EGRESS)$$' /tmp/vdt-direct.out; then echo "FAIL: tracing objects render in muster-direct"; exit 1; fi
	@helm template t $(CONNECTIVITY_DIR) $(DPT_VM) --set networkPolicy.enabled=false >/tmp/vdt-nonp.out 2>&1 || { cat /tmp/vdt-nonp.out; exit 1; }
	@$(PICK) /tmp/vdt-nonp.out AgentgatewayPolicy $(DPT_POLICY) >/dev/null || { echo "FAIL: the tracing policy needs networkPolicy.enabled"; exit 1; }
	@if grep -q 'name: $(DPT_EGRESS)$$' /tmp/vdt-nonp.out; then echo "FAIL: the egress policy renders with networkPolicy.enabled=false"; exit 1; fi
	@echo "ok: guards"
	@helm template t $(CHART_DIR) $(VM) >/tmp/vdt-meta.out 2>&1 || { cat /tmp/vdt-meta.out; exit 1; }
	@$(PICK) /tmp/vdt-meta.out HelmRelease agent-platform-connectivity | grep -A1 '^        podLabels:$$' | grep -q '^          observability.giantswarm.io/tenant: giantswarm$$' || { echo "FAIL: the meta chart does not forward the data plane's tenant label"; exit 1; }
	@$(PICK) /tmp/vdt-meta.out HelmRelease agent-platform-connectivity | grep -A1 '^      tracing:$$' | grep -q '^        randomSampling: "0.1"$$' || { echo "FAIL: the meta chart does not forward gateway.tracing.randomSampling 0.1 to the connectivity release"; exit 1; }
	@echo "ok: $@"

# The data plane's buffer (giantswarm/agent-platform#630): the connectivity chart
# renders ONE Gateway-scoped -http AgentgatewayPolicy carrying
# frontend.http.maxBufferSize from gateway.http.maxBufferSize — the cap on what a
# tool may answer through the platform, since the MCP path reads every answer whole.
DPB_POLICY := agent-platform-connectivity-http
# The AgentgatewayPolicy names whose spec carries a frontend.http section.
HTTP_POLICIES := python3 -c 'import re,sys; docs=open(sys.argv[1]).read().split("\n---\n"); [print(re.search(r"^  name: (\S+)", d, re.M).group(1)) for d in docs if "kind: AgentgatewayPolicy\n" in d and re.search(r"^  frontend:\n(?:.*\n)*?    http:", d, re.M)]'
.PHONY: verify-dataplane-buffer
verify-dataplane-buffer: ## Assert the data plane's buffer (giantswarm/agent-platform#630): with a data plane the connectivity chart renders the Gateway-scoped -http AgentgatewayPolicy with frontend.http.maxBufferSize 8Mi (the chart's own number, not agentgateway's 2 MiB default), targeting the data-plane Gateway; a quantity or a byte count set by an installation reaches it as written; no other policy of the chart carries a frontend.http section (frontend policies merge field by field); empty renders none, muster-direct renders none, a malformed quantity is refused by the schema; the meta chart forwards the same default to the connectivity release.
	@echo "====> $@ ($(CHART_DIR) + $(CONNECTIVITY_DIR))"
	@helm template t $(CONNECTIVITY_DIR) $(DPT_VM) >/tmp/vdb-on.out 2>&1 || { cat /tmp/vdb-on.out; exit 1; }
	@$(PICK) /tmp/vdb-on.out AgentgatewayPolicy $(DPB_POLICY) >/tmp/vdb-pol.out || { echo "FAIL: no AgentgatewayPolicy $(DPB_POLICY) with the data plane on"; exit 1; }
	@grep -q '^      kind: Gateway$$' /tmp/vdb-pol.out && grep -q '^      name: agentgateway$$' /tmp/vdb-pol.out || { echo "FAIL: the -http policy does not target the data-plane Gateway (a frontend policy may target nothing else)"; cat /tmp/vdb-pol.out; exit 1; }
	@grep -q '^      maxBufferSize: 8Mi$$' /tmp/vdb-pol.out || { echo "FAIL: the -http policy does not carry the chart's default maxBufferSize 8Mi"; cat /tmp/vdb-pol.out; exit 1; }
	@[ "$$($(HTTP_POLICIES) /tmp/vdb-on.out)" = "$(DPB_POLICY)" ] || { echo "FAIL: the policies with a frontend.http section are not $(DPB_POLICY) alone: $$($(HTTP_POLICIES) /tmp/vdb-on.out | tr '\n' ' ')"; exit 1; }
	@echo "ok: defaults"
	@helm template t $(CONNECTIVITY_DIR) $(DPT_VM) --set gateway.http.maxBufferSize=16Mi >/tmp/vdb-q.out 2>&1 || { cat /tmp/vdb-q.out; exit 1; }
	@$(PICK) /tmp/vdb-q.out AgentgatewayPolicy $(DPB_POLICY) | grep -q '^      maxBufferSize: 16Mi$$' || { echo "FAIL: a quantity set by an installation does not reach the policy as written"; exit 1; }
	@helm template t $(CONNECTIVITY_DIR) $(DPT_VM) --set gateway.http.maxBufferSize=16777216 >/tmp/vdb-i.out 2>&1 || { cat /tmp/vdb-i.out; exit 1; }
	@$(PICK) /tmp/vdb-i.out AgentgatewayPolicy $(DPB_POLICY) | grep -q '^      maxBufferSize: 16777216$$' || { echo "FAIL: a byte count set by an installation does not reach the policy as the integer"; exit 1; }
	@echo "ok: an installation's own size"
	@helm template t $(CONNECTIVITY_DIR) $(DPT_VM) --set gateway.http.maxBufferSize= >/tmp/vdb-none.out 2>&1 || { cat /tmp/vdb-none.out; exit 1; }
	@if grep -q 'name: $(DPB_POLICY)$$' /tmp/vdb-none.out; then echo "FAIL: the -http policy renders with the size empty (agentgateway's default is meant to apply)"; exit 1; fi
	@helm template t $(CONNECTIVITY_DIR) $(VM) >/tmp/vdb-direct.out 2>&1 || { cat /tmp/vdb-direct.out; exit 1; }
	@if grep -q 'name: $(DPB_POLICY)$$' /tmp/vdb-direct.out; then echo "FAIL: the -http policy renders in muster-direct, with no data plane to target"; exit 1; fi
	@if helm template t $(CONNECTIVITY_DIR) $(DPT_VM) --set gateway.http.maxBufferSize=8MB >/tmp/vdb-bad.out 2>&1; then echo "FAIL: 8MB (not a Kubernetes quantity) passed the schema"; exit 1; \
	elif ! grep -q "maxBufferSize" /tmp/vdb-bad.out; then echo "FAIL: 8MB failed for the wrong reason"; cat /tmp/vdb-bad.out; exit 1; fi
	@echo "ok: guards"
	@helm template t $(CHART_DIR) $(VM) >/tmp/vdb-meta.out 2>&1 || { cat /tmp/vdb-meta.out; exit 1; }
	@$(PICK) /tmp/vdb-meta.out HelmRelease agent-platform-connectivity | grep -A1 '^      http:$$' | grep -q '^        maxBufferSize: 8Mi$$' || { echo "FAIL: the meta chart does not forward gateway.http.maxBufferSize 8Mi to the connectivity release"; exit 1; }
	@echo "ok: $@"

# Anthropic prompt caching (giantswarm/giantswarm#37788; the kagent line's carried patch kagent-dev/kagent#2788).
.PHONY: verify-prompt-caching
verify-prompt-caching: ## Assert Anthropic prompt caching: the meta chart forwards kagent.providers.anthropic.config.promptCaching: true + cacheTTL to the kagent release (the default ModelConfig) and to the connectivity release; the connectivity chart's Anthropic catalog entries inherit both in the one provider block next to the listener baseUrl, an entry's own keys win (false included), an OpenAI entry gets nothing, a Bedrock entry takes its own keys under spec.bedrock, the chart alone renders nothing; a cacheTTL outside the CRD's enum and the keys on a provider without them fail the render naming the entry.
	@echo "====> $@"
	@echo "--> the meta chart forwards promptCaching + cacheTTL to the kagent release and to the connectivity release"
	@helm template t $(CHART_DIR) -f $(CHART_DIR)/ci/ci-values.yaml >/tmp/vpc-meta.out 2>&1 || { cat /tmp/vpc-meta.out; exit 1; }
	@awk '/^kind: HelmRelease$$/{h=1} h&&/^  name: kagent$$/{f=1} f&&/^---/{exit} f' /tmp/vpc-meta.out >/tmp/vpc-meta-kagent.out
	@grep -A14 '^    providers:$$' /tmp/vpc-meta-kagent.out | grep -q 'promptCaching: true' || { echo "FAIL: the kagent HelmRelease values carry no providers.anthropic.config.promptCaching: true; the default ModelConfig would stay uncached"; exit 1; }
	@grep -A14 '^    providers:$$' /tmp/vpc-meta-kagent.out | grep -q 'cacheTTL: 5m' || { echo "FAIL: the kagent HelmRelease values carry no providers.anthropic.config.cacheTTL"; exit 1; }
	@awk '/^kind: HelmRelease$$/{h=1} h&&/^  name: agent-platform-connectivity$$/{f=1} f&&/^---/{exit} f' /tmp/vpc-meta.out >/tmp/vpc-meta-conn.out
	@grep -q 'promptCaching: true' /tmp/vpc-meta-conn.out || { echo "FAIL: the connectivity HelmRelease values carry no providers.anthropic.config.promptCaching; the catalog's Anthropic entries would inherit nothing"; exit 1; }
	@echo "ok: forwarded to both releases"
	@echo "--> connectivity: an Anthropic entry inherits the platform default in one block next to the listener baseUrl; an OpenAI entry gets nothing"
	@helm template t $(CONNECTIVITY_DIR) -f $(CONNECTIVITY_DIR)/ci/test-llm-routing-values.yaml >/tmp/vpc-ci.out 2>&1 || { cat /tmp/vpc-ci.out; exit 1; }
	@awk '/name: "anthropic-sonnet"/{f=1} f&&/^---/{exit} f' /tmp/vpc-ci.out >/tmp/vpc-sonnet.out
	@grep -q 'baseUrl: "http://agentgateway.default.svc:8081"' /tmp/vpc-sonnet.out && grep -q '^    promptCaching: true$$' /tmp/vpc-sonnet.out && grep -q '^    cacheTTL: 5m$$' /tmp/vpc-sonnet.out || { cat /tmp/vpc-sonnet.out; echo "FAIL: an Anthropic entry without its own keys did not inherit promptCaching: true / cacheTTL: 5m under spec.anthropic next to the listener baseUrl"; exit 1; }
	@if [ "$$(grep -c '^  anthropic:$$' /tmp/vpc-sonnet.out)" != "1" ]; then cat /tmp/vpc-sonnet.out; echo "FAIL: the anthropic block renders more than once (baseUrl and the cache keys share one block; a second would overwrite the first)"; exit 1; fi
	@awk '/name: "openai-gpt-direct"/{f=1} f&&/^---/{exit} f' /tmp/vpc-ci.out >/tmp/vpc-openai.out
	@if grep -qE 'promptCaching|cacheTTL' /tmp/vpc-openai.out; then cat /tmp/vpc-openai.out; echo "FAIL: an OpenAI entry carries the Anthropic cache keys; the API server would prune them"; exit 1; fi
	@echo "ok: inherited by Anthropic entries only"
	@echo "--> an entry's own keys win, false included"
	@helm template t $(CONNECTIVITY_DIR) -f $(CONNECTIVITY_DIR)/ci/test-llm-routing-values.yaml --set 'kagent.modelConfigs[1].promptCaching=false' --set 'kagent.modelConfigs[1].cacheTTL=1h' >/tmp/vpc-own.out 2>&1 || { cat /tmp/vpc-own.out; exit 1; }
	@awk '/name: "anthropic-opus-direct"/{f=1} f&&/^---/{exit} f' /tmp/vpc-own.out >/tmp/vpc-opus.out
	@grep -q '^    promptCaching: false$$' /tmp/vpc-opus.out && grep -q '^    cacheTTL: 1h$$' /tmp/vpc-opus.out || { cat /tmp/vpc-opus.out; echo "FAIL: an entry's own promptCaching: false / cacheTTL: 1h did not win over the platform default"; exit 1; }
	@echo "ok: own keys win"
	@echo "--> the connectivity chart alone (no platform default): nothing renders"
	@helm template t $(CONNECTIVITY_DIR) $(VM) --set components.kagent.enabled=true --set kagent.namespaceOverride=kagent --set-json 'kagent.modelConfigs=[{"name":"plain","provider":"Anthropic","model":"m","apiKeySecret":"s","apiKeySecretKey":"k"}]' >/tmp/vpc-plain.out 2>&1 || { cat /tmp/vpc-plain.out; exit 1; }
	@if grep -qE 'promptCaching|cacheTTL' /tmp/vpc-plain.out; then cat /tmp/vpc-plain.out; echo "FAIL: the connectivity chart invents a caching default of its own; the meta chart owns it"; exit 1; fi
	@echo "ok: nothing by default"
	@echo "--> a Bedrock entry takes its own keys under spec.bedrock (no baseUrl there)"
	@helm template t $(CONNECTIVITY_DIR) $(VM) --set components.kagent.enabled=true --set kagent.namespaceOverride=kagent --set-json 'kagent.modelConfigs=[{"name":"bedrock","provider":"Bedrock","model":"m","apiKeySecret":"s","promptCaching":true,"cacheTTL":"1h"}]' >/tmp/vpc-bedrock.out 2>&1 || { cat /tmp/vpc-bedrock.out; exit 1; }
	@grep -A2 '^  bedrock:$$' /tmp/vpc-bedrock.out | grep -q 'promptCaching: true' || { cat /tmp/vpc-bedrock.out; echo "FAIL: a Bedrock entry's promptCaching is not under spec.bedrock"; exit 1; }
	@echo "ok: bedrock block"
	@echo "--> guards: a cacheTTL outside the CRD's enum, and the keys on a provider without them, fail naming the entry"
	@if helm template t $(CONNECTIVITY_DIR) -f $(CONNECTIVITY_DIR)/ci/test-llm-routing-values.yaml --set 'kagent.modelConfigs[0].cacheTTL=10m' >/tmp/vpc-ttl.out 2>&1; then \
		echo "FAIL: cacheTTL 10m rendered; the CRD's enum is 5m, 1h and the API server refuses it after the render said nothing"; exit 1; \
	elif ! grep -q 'anthropic-sonnet' /tmp/vpc-ttl.out || ! grep -q '5m, 1h' /tmp/vpc-ttl.out; then cat /tmp/vpc-ttl.out; echo "FAIL: the cacheTTL guard does not name the entry and the enum"; exit 1; \
	else echo "ok: cacheTTL guard"; fi
	@if helm template t $(CONNECTIVITY_DIR) -f $(CONNECTIVITY_DIR)/ci/test-llm-routing-values.yaml --set 'kagent.modelConfigs[2].promptCaching=true' >/tmp/vpc-foreign.out 2>&1; then \
		echo "FAIL: promptCaching on an OpenAI entry rendered; the API server would prune it and the model would stay uncached in silence"; exit 1; \
	elif ! grep -q 'openai-gpt-direct' /tmp/vpc-foreign.out; then cat /tmp/vpc-foreign.out; echo "FAIL: the provider guard does not name the entry"; exit 1; \
	else echo "ok: provider guard"; fi
	@echo "All prompt-caching behaviors verified."

.PHONY: verify-engine
verify-engine: ## Assert the bundled Flux engine's two shapes: engine off (pure renderer, no CRD/hook/operator/identity) and engine on (the eleven CRDs, operator, FluxInstance, agent-platform-flux on every HelmRelease, the teardown hooks). HELM selects the binary.
	@echo "====> $@ ($(CHART_DIR))"
	@python3 tests/verify-engine.py $(CHART_DIR)
	@echo "flux engine shapes verified."

.PHONY: verify-target
verify-target: ## Assert one release of this chart per target cluster (giantswarm/agent-platform#328): gitops.target.kubeConfig.secretRef stamps spec.kubeConfig.secretRef (name, key when set) onto every component HelmRelease and changes nothing else — unset, the meta and connectivity renders are byte-identical to GOLDEN_REF; components.muster / components.dicebear gain enabled (off = no release, the roster says so, the connectivity chart drops the /mcp route, muster's egress policy, every rule selecting its pods and the avatars host in the portal's CSP); the knob with the bundled engine fails; no hook Job renders with the knob; the serving- and runtime-shaped toggle sets (ci/test-slice-*-values.yaml) render alone, combined (the union, the first slice's documents unchanged — an in-place upgrade) and with the knob (ci/test-target-values.yaml), agentgateway off beside the platform's release and on for a workload cluster; the schema. The lookup guards (a foreign helm-controller, a second owner of a component's CRDs — components.<name>.ownedCrds) need a live cluster: README "One release per target cluster". HELM selects the binary.
	@echo "====> $@ ($(CHART_DIR), $(CONNECTIVITY_DIR))"
	@GOLDEN_REF="$(GOLDEN_REF)" python3 tests/verify-target.py $(CHART_DIR) $(CONNECTIVITY_DIR)
	@echo "target cluster shapes verified."

.PHONY: verify-serving-slice
verify-serving-slice: ## Assert the serving slice (giantswarm/agent-platform#326): examples/serving-slice.yaml renders exactly the three llm-d releases and connectivity (no muster, dicebear, valkey, kagent, Backstage, agent-manager, model-manager, agentgateway; the engine off) — kserve-runtime-configs after kserve-llmisvc-crd into the release namespace with the well-known configs on, the runtimes off, the llm-d-fast/ prefix as imageRegistry — the prefix the pre-pull's llm-d-cuda reference carries (#568) — and its block held back from connectivity; KServe's ingress-gateway value derived onto kserve-llmisvc-resources (a differing copy fails, an equal one is a no-op, none with the Gateway off); runtimeClassName nvidia forwarded; the target knob adds agentgateway and stamps every kubeConfig. The connectivity chart with the forwarded values: the models Gateway on models.<global.domain> with the wildcard Secret and the external-dns hostname, ONE Strict AgentgatewayPolicy on the Gateway (audience dex-k8s-authenticator, inheritance Override, the issuer), the JWKS backend at the issuer's host on 443 with TLS, the discovery ConfigMap's gateway entry; a Certificate only with tls.issuerRef.name; nothing with modelsGateway.enabled false (the default: the Gateway is the slice's, the profile turns it on); the guards name their key. The four 24 GB presets (the September 2026 line-up, giantswarm/agent-platform#591): schema keys, a signed model image and no serving image, tools and reasoning on with their parsers, one GPU, <= 24 GiB, requests within what a g6.xlarge leaves a predictor after the kubelet's reservations and the daemonsets (#502), the description naming the instance. Every shipped preset's resources.gpus equals the tensor-parallel size its arguments set (1 without the flag): the well-known template adds no --tensor-parallel-size unless spec.parallelism.tensor is set, which model-manager never does, so a four-GPU preset's flag is the one on the command line. Every shipped preset's arguments survive the llm-d template's entrypoint: its exact eval "… $@" (#532), run over the preset's args with argv dumped in place of vLLM, yields one word per argument and every JSON value parses; a values preset whose argument carries whitespace, a quote or a shell metacharacter outside single quotes fails the render naming the guard; no shipped preset carries --disable-fastapi-docs and a values preset with it fails the render naming the flag (it removes the route list model-manager reads the interfaces from, giantswarm/agent-platform#602); a preset carrying the removed classic fields spec.runtime or spec.predictor fails the render naming them. The cache claim (#483): no PersistentVolumeClaim object — a post-install,post-upgrade hook Job server-side applies hf-cache (keep, RWO, 100Gi on the chart's gp3 class at 500 MiB/s / 3000 IOPS, named with a digest of provisioner and parameters — a parameter change renders a new class, #570; the class incl. "-", size, volumeName and access-mode knobs) as <release>-hooks with get/create/patch on claims — its script, run against a stub kubectl, keeps an existing claim's class and size (grows, never shrinks; a Pending claim on a class the cluster lacks fails naming the way out); cache.enabled false or an existing claim render neither hook nor identity; the serving namespace carries helm.sh/resource-policy: keep with the cache on and off (#565: a namespace kept only with the cache on took a claim an earlier slice left there down with it), not with namespace.keep false. The live half (a served LLMInferenceService answers 200 with a person's id_token and 401 without; no bearer in the model server's log) runs on a GPU cluster: README "The serving slice and the models Gateway". HELM selects the binary.
	@echo "====> $@ ($(CHART_DIR), $(CONNECTIVITY_DIR))"
	@python3 tests/verify-serving-slice.py $(CHART_DIR) $(CONNECTIVITY_DIR)
	@echo "serving slice verified."

.PHONY: verify-preset-weights
verify-preset-weights: ## Assert every shipped serving preset's requirements.weightsGiB matches the Hub (giantswarm/agent-platform#535): each preset's spec.model.id is sized the way model-manager sizes a fit — model.safetensors.index.json's metadata.total_size when it agrees with the shards it maps (a stale index is overruled by their sum), else the sum of the *.safetensors files — and passes at >= the Hub's size and <= 15 % above it; a preset the Hub cannot size fails. The two fixtures (tests/fixtures/serving-preset-weights-*.yaml) are the negative controls and must verify as understated and overstated. Network: the Hub API.
	@echo "====> $@ ($(CONNECTIVITY_DIR)/files/model-serving/presets)"
	@python3 tests/verify-preset-weights.py $(CONNECTIVITY_DIR)/files/model-serving/presets
	@python3 tests/verify-preset-weights.py --expect understated tests/fixtures/serving-preset-weights-understated.yaml
	@python3 tests/verify-preset-weights.py --expect overstated tests/fixtures/serving-preset-weights-overstated.yaml
	@echo "preset weights verified against the Hub."

# The live half of verify-serving-slice that needs no GPU (giantswarm/agent-platform#505). Not a
# verify-* target: it reads a cluster, so verify-all must not collect it.
GATEWAY ?= models
CONTROLLER_NAMESPACE ?= agent-platform
.PHONY: live-serving-slice
live-serving-slice: ## Against the current kubeconfig (KUBE_CONTEXT= selects a context), a cluster with the serving slice installed: the models JWT policy's Accepted condition is Valid on every ancestor and the agentgateway controller's jwks-store holds keys for the backend's URL — the controller fetches every JWKS and pushes the keys to the data plane, so a failed fetch is `401 token uses the unknown key` for every caller; on a failure the JWKS URL and the controller's `error fetching jwks` lines are printed. NAMESPACE=<the slice's release namespace> (required), GATEWAY=models, CONTROLLER_NAMESPACE=agent-platform.
	@test -n "$(NAMESPACE)" || { echo "usage: make live-serving-slice NAMESPACE=<the slice's release namespace> [GATEWAY=models] [CONTROLLER_NAMESPACE=agent-platform] [KUBE_CONTEXT=<context>]"; exit 2; }
	@echo "====> $@ (namespace $(NAMESPACE), gateway $(GATEWAY), controller in $(CONTROLLER_NAMESPACE))"
	@python3 tests/verify-serving-slice-live.py --namespace "$(NAMESPACE)" --gateway "$(GATEWAY)" --controller-namespace "$(CONTROLLER_NAMESPACE)" $(if $(KUBE_CONTEXT),--context "$(KUBE_CONTEXT)")

.PHONY: verify-serving-slice-store
verify-serving-slice-store: ## Assert tests/verify-serving-slice-live.py reads the agentgateway controller's jwks-store ConfigMaps in the shape the controller writes them (giantswarm/agent-platform#515): over tests/fixtures/jwks-store-configmaps.json (a copied store — the models issuer with two keys, an issuer with an empty key set, a labelled ConfigMap without the entry) the entries carry the writer's fields ({requestKey, url, fetchedAt, jwks}, no count field), the key count comes from the `jwks` JSON string, the live check passes for the issuer's URL naming the ConfigMap and the kids, fails naming the URL for the empty set and for a URL the store does not hold, and fails naming the ConfigMap when `jwks` is not a JWKS document. Offline, stdlib-only.
	@echo "====> $@"
	@python3 tests/verify-serving-slice-store.py
	@echo "serving slice live check's store reader verified."

.PHONY: verify-gpu-operator
verify-gpu-operator: ## Assert the GPU operator component (giantswarm/agent-platform#327): components.gpu-operator off by default (no release, the roster says so, the gpu-operator values block held back from connectivity); on, ONE OCIRepository (the catalog's gpu-operator wrapper chart, 1.x) + ONE HelmRelease into kube-system (release history there too, crds CreateReplace on install and upgrade, no dependsOn, no global) with the values nested under the wrapper's subchart key — the Flatcar row, driver and toolkit off, plus the DCGM exporter ServiceMonitor's tenant label — and nothing else of the render moved but the roster entry; the pre-installed-driver row (gpu-operator.toolkit.enabled=true) reaches the release with the driver off; the target knob stamps kubeConfig.secretRef on it; the one-owner guard is silent offline with the nvidia.com, Flux and App APIs served; the schema refuses a non-boolean toggle; the BOM pins the exact version and the pin reaches the OCIRepository. The lookup guard itself needs a cluster: tests/fixtures/gpu-operator-foreign-owner.yaml (README "The GPU operator"). HELM selects the binary.
	@echo "====> $@ ($(CHART_DIR))"
	@python3 tests/verify-gpu-operator.py $(CHART_DIR)
	@echo "GPU operator component verified."

.PHONY: verify-cluster-manager
verify-cluster-manager: ## Assert the cluster-manager component (giantswarm/agent-platform#316): components.cluster-manager off by default (no release, the roster says so, its two blocks held back from connectivity, nothing named cluster-manager in the connectivity render); on, ONE OCIRepository (the catalog's cluster-manager chart, >=0.4.0 <1.0.0) + ONE HelmRelease dependsOn muster with the block forwarded — the pinned Service name, the OAuth resource server acting as the caller, the muster registration with forwardToken and the kube-apiserver's audience, global injected, modelManager.namespace derived from the platform's namespace (an own value must agree) — and nothing else of the render moved but the roster entry and the two blocks; the connectivity chart renders the ingress (muster + probes), egress (DNS, the kube-apiserver, the identity provider, the workload clusters' API servers on their ports, the extra names and blocks) and muster-to policies in both flavors, none while the component is off; the prewarm placeholder's PriorityClass agent-platform-prewarm-placeholder (-1000, Never, not the global default) with the component on — the gpu-node-pool chart's default pool.prewarm.priorityClassName, one owner per installation (giantswarm/agent-platform#539) — none off or with clusterManager.prewarmPriorityClass.enabled false, name and value knobs; the guards (muster off, a missing identity input, a bad CIDR, no ports, a placeholder priority at or above 0 or fractional, a reserved or malformed class name); the target knob; the schema refuses a non-boolean toggle; the BOM pins the exact version and the pin reaches the OCIRepository. HELM selects the binary.
	@echo "====> $@ ($(CHART_DIR), $(CONNECTIVITY_DIR))"
	@python3 tests/verify-cluster-manager.py $(CHART_DIR) $(CONNECTIVITY_DIR)
	@echo "cluster-manager component verified."

.PHONY: verify-self
verify-self: ## Assert self-management's shapes: engine off renders nothing of it; engine on renders the self OCIRepository + suspended HelmRelease, the -6/-5/0 hooks, the identity and the admission policy (CLI day-0 only); engine on with self off (lab, hand-back) renders the -6/-5 hooks at pre-upgrade too and nothing else; the guards and knobs. HELM selects the binary.
	@echo "====> $@ ($(CHART_DIR))"
	@python3 tests/verify-self.py $(CHART_DIR)
	@echo "self-management shapes verified."

.PHONY: verify-insecure
verify-insecure: ## Assert components.<name>.insecure renders OCIRepository.spec.insecure for that component only (a lab's plain-HTTP registry), and nothing by default.
	@echo "====> $@ ($(CHART_DIR))"
	@helm template t $(CHART_DIR) -f $(CHART_DIR)/ci/ci-values.yaml --set components.flux.enabled=false >/tmp/ap-insecure-off.out 2>&1 || { cat /tmp/ap-insecure-off.out; exit 1; }
	@if grep -q '^  insecure: true' /tmp/ap-insecure-off.out; then echo "FAIL: an OCIRepository renders insecure by default"; exit 1; fi
	@helm template t $(CHART_DIR) -f $(CHART_DIR)/ci/ci-values.yaml --set components.flux.enabled=false --set components.muster.insecure=true --set components.muster.repository=oci://registry.registry.svc.cluster.local:5000/charts >/tmp/ap-insecure-on.out 2>&1 || { cat /tmp/ap-insecure-on.out; exit 1; }
	@if [ "$$(grep -c '^  insecure: true' /tmp/ap-insecure-on.out)" != "1" ]; then echo "FAIL: components.muster.insecure must render exactly one insecure OCIRepository"; grep -n 'insecure' /tmp/ap-insecure-on.out; exit 1; fi
	@if ! grep -q 'url: oci://registry.registry.svc.cluster.local:5000/charts/muster' /tmp/ap-insecure-on.out; then echo "FAIL: components.muster.repository did not steer the OCIRepository url"; exit 1; fi
	@echo "components.<name>.insecure verified."

.PHONY: verify-model-serving-policies
verify-model-serving-policies: ## Assert the model-serving policies over the LLMInferenceService workload pod the llm-d controller creates (giantswarm/agent-platform#506): `kyverno apply` of the rendered ClusterPolicies over its fixture pod (tests/fixtures/model-serving-llmisvc-workload-pod.yaml) mounts the hf-cache claim at /mnt/models with the model's name as subPath on the storage-initializer and the runtime container (main), mounts the claim a second time on the runtime container alone at /mnt/vllm-cache from the claim-wide subPath .vllm-cache with VLLM_CACHE_ROOT naming it and TRITON_CACHE_DIR its triton/ directory in the same rule (both under the mount, neither on a pod without the cache — Triton's kernel cache for a preset that serves eager, giantswarm/agent-platform#572) — vLLM's cache a directory of the claim's own, never under /mnt/models, where the initializer's Hugging Face client owns <model>/.cache as uid 1000 mode 755 and a cache root crash-looped every cold start (giantswarm/agent-platform#541); no mount or env value of the pod names a path under /mnt/models — gives the pod the claim's fsGroup (also over one it declared), raises the initializer's limit, merges modelServing.policies.env (HF_HUB_DISABLE_XET=1: the Hugging Face client off the Xet path a toFQDNs allow-list cannot follow, giantswarm/agent-platform#520) by name onto the storage-initializer's and the runtime container's own env (added where a container has none; an empty list renders no env rule), adds no container, keeps containers and volumes, and is a no-op over its own output (the reinvoked webhook, giantswarm/agent-platform#514); a pod without a storage-initializer and a download-Job pod are untouched, a workload pod without a model name gets the env but no cache mount and no fsGroup; the mutated pod passes the fleet's restricted Pod Security Standard (tests/fixtures/restricted-pss-clusterpolicies.yaml) with the chart's PolicyException — every rule passes or is skipped by the exception, the bare pod fails exactly the four excepted rules, a pod with the former root hf-cache-init fails exactly the two that denied it (giantswarm/agent-platform#518); the Deployment gets the progress deadline; the network policies (both flavours), the kagent agents' egress and the PolicyException select the fixture by exactly its own policy and never the download Job's pod; model-manager's egress (both flavours) reaches the fixture on exactly that port and the fixture's ingress admits the release namespace there, no model-manager policy with the component off and no rule into the serving namespace with the slice off (the route list model-manager reads the interfaces from, giantswarm/agent-platform#602); the policies — the cilium ingress from the callers and from the kubelet, the kubernetes-flavour ingress, the agents' egress rule — admit exactly the port the fixture's routing sidecar listens on, 8000 (the port its Service targets; giantswarm/agent-platform#525), from the connectivity defaults and from the meta chart's forwarded values, and a render on another port fails naming the policy; the model pods' and the download Job's cilium egress admits every name of the Hugging Face download path — the Hub, the LFS fronts, the Xet fronts, the download CDN us.aws.cdn.hf.co at three labels under hf.co — under Cilium's pattern rule (a * matches one label, never a dot; giantswarm/agent-platform#522), keeps out a deeper name and a look-alike domain, and stays a toFQDNs allow-list (no toCIDR, no world), as rendered from the connectivity chart's defaults AND from the values the meta chart forwards to its connectivity release (forwardAllValues; the render an installation gets — a forwarded copy of a default shadows the child's, the 4.28.17 drift); the kubernetes flavour admits 443 to every public block. The pre-pull DaemonSet (modelServing.prepull, giantswarm/agent-platform#545) renders in the serving namespace by default — one /bin/true init container per image of modelServing.prepull.images, the llm-d runtime image first, a pause main container, the pool's taint tolerated first and every taint after it, Karpenter's GPU label selected with the pool's label merged under it, no GPU, no runtimeClass, no token —, its pod passes every rule of the fleet's restricted PSS with NO exception and is touched by no mutation, no shape's policy, the PolicyException or the agents' egress selects it, its own deny-all policy (both flavours) selects it alone, enabled false renders neither object, an empty image list fails the render naming the key, and the meta chart's forwarded values render the same pod; it is a post-install,post-upgrade,post-rollback hook object replaced before creation (giantswarm/agent-platform#563: a release resource's pods gate the release's wait, and a pod whose image cannot be pulled is never Ready), its deny-all a release resource, a pre-delete hook Job deletes it by name as the hook identity, whose ClusterRole carries delete on exactly that DaemonSet and is created for that event, and enabled false renders neither the Job nor the rule; modelServing.prepull.nodeSelector set renders alone (giantswarm/agent-platform#562), empty renders Karpenter's label, the pool's label is merged under either, a non-string label value fails the render naming the key, and a selector set on the meta chart reaches the DaemonSet alone. Image verification (modelServing.imageVerification, giantswarm/agent-platform#552, #575) is off by default and renders nothing without kyverno.io/v1; enabled alone, the chart's defaults reach the rule — every image under the platform's registry namespace, the Giant Swarm CircleCI identity (issuer https://oidc.circleci.com, subject a pipeline definition) as the one keyless attestor, the Sigstore bundle format (the only format Kyverno finds an architect-orb signature in); on with an installation's own block, one verifyImages ClusterPolicy with one rule selects exactly the workload Pods in the serving namespace at CREATE and UPDATE, carrying the image references, the type and the attestor entries verbatim (a keyless identity by exact subject and a public key, one attestor set of count 1) with mutateDigest, required and failureAction as set; `kyverno apply` accepts the policy and skips the fixture pod (no image matches; a misspelt verifyImages field drops the policy, so the acceptance has teeth); enabled with an empty images list, no attestor, a non-Kyverno entry, a type outside SigstoreBundle | Cosign, a failureAction outside Enforce | Audit or an unknown key fails the render naming the key; the meta chart's forwarded values render the same policy. Its egress (modelServing.imageVerification.kyvernoEgress, giantswarm/agent-platform#599): the default cilium render carries one CiliumNetworkPolicy in Kyverno's namespace selecting the admission controller with DNS through Cilium's DNS proxy and 443 to the registry, the Azure Storage accounts its blob reads redirect to, the Sigstore TUF repository and Rekor as a toFQDNs allow-list (the controller fetches and verifies the bundles itself, and the fleet's Kyverno may reach the API server only); nothing in the kubernetes flavour, with either switch off or without kyverno.io/v1; an installation's own namespace, labels and hosts reach it verbatim; an empty host list, a host that is no toFQDNs entry, an empty namespace, a selector that is no mapping, a nulled block or an unknown key fails the render naming the key; the meta chart's forwarded values render the same policy. Needs PyYAML and the kyverno CLI. HELM and KYVERNO select the binaries.
	@echo "====> $@ ($(CONNECTIVITY_DIR), $(CHART_DIR))"
	@python3 tests/verify-model-serving-policies.py $(CONNECTIVITY_DIR) $(CHART_DIR)

.PHONY: verify-gpu-pool
verify-gpu-pool: ## Assert the GPU node pool input of the model serving layer (giantswarm/agent-platform#315): modelServing.gpuPool.taint tolerated by every published preset (the pool's entry first, a preset's equal entry once; Exists without a value, Equal with one), modelServing.gpuPool.nodeSelector merged under the presets' own (their keys win), both published as spec.gpuPool in the discovery ConfigMap model-manager >= 0.23.0 reads; an empty taint key renders no toleration and no taint and leaves the serving render byte-identical to GOLDEN_REF but for the discovery block; the guards (effect, key, string label values); the meta chart forwards the block. Fixture: ci/test-model-serving-gpu-pool-values.yaml. HELM selects the binary.
	@echo "====> $@ ($(CHART_DIR), $(CONNECTIVITY_DIR))"
	@GOLDEN_REF=$(GOLDEN_REF) python3 tests/verify-gpu-pool.py $(CHART_DIR) $(CONNECTIVITY_DIR)
	@echo "GPU node pool input verified."

.PHONY: verify-model-images
verify-model-images: ## Assert models as OCI images (giantswarm/agent-platform#551): with modelServing.modelImages.registry set, every oci:// preset's published storageUri — a shipped preset's and a values preset's alike — carries that host and nothing else of the document changes, an hf:// preset is untouched, an empty registry leaves every reference as written; the discovery ConfigMap publishes spec.modelImages.registry; modelServing.prepull.modelPresets renders one init container per named preset after the runtime images — pull-model-<preset>, the published storageUri minus oci://, /bin/true, the runtime init containers' security context and resources — a digest reference keeps its digest; the guards (an unknown name, a preset that is not oci://, a name twice, a registry with a scheme or a path, an oci:// reference without a host) fail the render naming it; the meta chart forwards both blocks and its forwarded values render the same preset ConfigMap and pre-pull pod. Fixture: ci/test-model-serving-oci-values.yaml. Needs PyYAML. HELM selects the binary.
	@echo "====> $@ ($(CHART_DIR), $(CONNECTIVITY_DIR))"
	@python3 tests/verify-model-images.py $(CHART_DIR) $(CONNECTIVITY_DIR)
	@echo "Models as OCI images verified."

.PHONY: verify-labels
verify-labels: ## Assert every label value stays valid at the versions the charts are installed under: helm-controller's +digest and a branch build's long prerelease, with the 63-character cut landing on each separator. HELM selects the binary.
	@echo "====> $@ ($(CHART_DIR), $(CONNECTIVITY_DIR))"
	@python3 tests/verify-labels.py $(CHART_DIR) $(CONNECTIVITY_DIR)
	@echo "label values verified."

.PHONY: verify-components
verify-components: ## Assert the roster: the standalone chart's extras (backstage, mcp-kubernetes, cloudnative-pg, the kserve charts) off by default, sources and ranges, CRD-before-CR dependsOn, BOM pins, the forwarded tree validates against the connectivity schema; the kagent line and the managers on their ranges, the managers pinned to the line's kagent API version, kagent-crds following kagent; the connectivity range holds its own major.
	@echo "====> $@ ($(CHART_DIR), $(CONNECTIVITY_DIR))"
	@python3 tests/verify-components.py $(CHART_DIR) $(CONNECTIVITY_DIR)
	@echo "component roster verified."

# The tag pipeline's run of the same check (giantswarm/agent-platform#624): a
# release whose range or BOM pin names a chart nobody can pull is a release nobody
# can install, so the fallbacks (UNRELEASED, RENDER_AGAINST) do not apply there and
# the check fails naming the component and the version it waits for. Not part of
# verify-all: a branch renders against the fallbacks on purpose, so the PR can land
# before the component release; .circleci/custom.yml runs this on tags, before the
# connectivity push (the meta chart's push-chart-release gates on it through the
# generated pipeline's chart release gate).
.PHONY: verify-release-floors
verify-release-floors: ## Assert, on a release tag, that every component range and every BOM pin resolves to a chart that is pullable from its registry — no UNRELEASED/RENDER_AGAINST fallback; a floor or pin nobody can pull fails naming the component and the version the release waits for (rerun the tag's workflow from failed once it is out). Network: gsoci.azurecr.io (ghcr.io for the CloudNativePG chart).
	@echo "====> $@ ($(CHART_DIR))"
	@python3 tests/verify-components-charts.py $(CHART_DIR) --strict
	@echo "every component floor and BOM pin is published; the release is installable."

.PHONY: verify-components-charts
verify-components-charts: ## Render every component chart with the values the meta chart forwards to it — the roster is values.yaml's, the BOM must pin all of it (both ways) — at the range's resolution and at the BOM pin, resolved the way Flux does (the tag and the layer the OCIRepository selects); a chart released with the meta chart (releasedWithChart) from the working tree. A forwarded key a closed schema does not declare fails the release on every installation, which a meta-only render cannot see. Network: gsoci.azurecr.io (ghcr.io for the CloudNativePG chart).
	@echo "====> $@ ($(CHART_DIR))"
	@python3 tests/verify-components-charts.py $(CHART_DIR)
	@echo "component charts accept the forwarded values."

# The two platform services the connectivity chart wires — model-manager and
# agent-manager (route + JWT policy + network policies + render-time guards). A
# valid configuration of both on the agentgateway topology, with the identity
# contract set so the OAuth guards are satisfied.
MANAGERS_ON := $(VM) --namespace agent-platform --set ingress.mode=agentgateway-muster --set components.agentgateway.enabled=true --set components.kagent.enabled=true --set components.model-manager.enabled=true --set components.agent-manager.enabled=true --set model-manager.backend=ollama --set model-manager.ollama.endpoint=http://10.0.0.1:11434 --set global.domain=ci.example.com --set global.identity.issuerUrl=https://dex.ci.example.com --set global.identity.clientId=platform --set global.identity.existingSecret=platform-oauth --set gateway.jwksEgress.enabled=true
# modelManager.networkPolicy.registeredBackends (giantswarm/agent-platform#478): a block and a name; the block alone for the kubernetes flavor.
REGISTERED_BACKENDS := --set 'modelManager.networkPolicy.registeredBackends[0].cidr=192.0.2.0/24' --set 'modelManager.networkPolicy.registeredBackends[0].port=11434' --set 'modelManager.networkPolicy.registeredBackends[1].fqdn=ollama.models.svc.cluster.local' --set 'modelManager.networkPolicy.registeredBackends[1].port=1234'
REGISTERED_BACKENDS_CIDR := --set 'modelManager.networkPolicy.registeredBackends[0].cidr=192.0.2.0/24' --set 'modelManager.networkPolicy.registeredBackends[0].port=11434'
MANAGERS_ROUTES := --set modelManager.route.enabled=true --set modelManager.route.jwtAuthentication.enabled=true --set agentManager.route.enabled=true --set agentManager.route.jwtAuthentication.enabled=true
# A minimal on-state that trips no other guard, for probing one guard at a time.
MANAGERS_MIN := $(VM) --set components.kagent.enabled=true --set global.identity.issuerUrl=https://dex.ci.example.com --set global.identity.clientId=platform --set global.identity.existingSecret=platform-oauth --set global.domain=ci.example.com

# $(call managers_must_fail,<description>,<helm flags>,<message fragment>)
define managers_must_fail
	@if helm template t $(CONNECTIVITY_DIR) $(2) >/tmp/vmg-fail.out 2>&1; then \
		echo "FAIL: $(1): the render succeeded"; exit 1; \
	elif ! grep -q "$(3)" /tmp/vmg-fail.out; then \
		echo "FAIL: $(1): failed for the wrong reason"; cat /tmp/vmg-fail.out; exit 1; \
	else echo "ok: $(1)"; fi
endef
# $(call managers_must_pass,<description>,<helm flags>)
define managers_must_pass
	@helm template t $(CONNECTIVITY_DIR) $(2) >/tmp/vmg-pass.out 2>&1 || { echo "FAIL: $(1): a valid configuration was rejected"; cat /tmp/vmg-pass.out; exit 1; }
	@echo "ok: $(1)"
endef

# kagent's built-in tool server (kagent.kagent-tools.enabled) is an MCP endpoint
# the controller discovers and the agents call directly; both run under the
# default-deny egress lists above, so the chart has to open the path or the
# kagent-tool-server RemoteMCPServer never becomes Accepted (SYN dropped, "Policy
# denied") and the agents that reference it run without tools. Off by default
# and off in the golden render, so the default render is unchanged. The rules
# name the namespace the kagent chart renders the server into — the subchart's
# namespaceOverride, else the release namespace, never the kagent namespace by
# assumption (#421: the rule said kagent, the server sat in the release
# namespace, discovery timed out) — and tests/verify-kagent-tools-namespace.py
# ties them to the rendered Deployment and RemoteMCPServer URL of the kagent
# chart the range resolves to, through the values the meta chart forwards
# (network: gsoci.azurecr.io, like verify-components-charts).
KAGENT_NETPOL := $(VM) --set components.kagent.enabled=true $(SUBSTRATE_ON) --set muster.enabled=true --set networkPolicy.flavor=cilium --set kagent.namespaceOverride=kagent
# An actor whose ModelConfig points at a host model server dials it through the
# egress gateway, on a port the gateway's allow-list does not otherwise open:
# the same on-state plus a model-manager in front of one.
KAGENT_MM := $(KAGENT_NETPOL) --set components.model-manager.enabled=true --set model-manager.backend=ollama --set model-manager.ollama.endpoint=http://10.0.0.1:11434 --set global.domain=ci.example.com --set global.identity.issuerUrl=https://dex.ci.example.com --set global.identity.clientId=platform --set global.identity.existingSecret=platform-oauth
# The two files of the v1alpha2 agent templates giantswarm/agent-platform#299
# deletes; until then they are the only place `app: kagent` may still appear.
KAGENT_V1ALPHA2_TEMPLATES := $(CONNECTIVITY_DIR)/templates/kagent/declarative-agent-pod-security.yaml $(CONNECTIVITY_DIR)/templates/kagent/declarative-agent-srt-settings.yaml
.PHONY: verify-kagent-netpol
verify-valkey: ## Assert muster-valkey's memory bound (giantswarm/agent-platform#446): the valkey release carries a valkeyConfig fragment with maxmemory at or under two thirds of resources.limits.memory and maxmemory-policy volatile-lru, no AOF; an installation's own fragment reaches the release verbatim.
	@echo "====> $@ ($(CHART_DIR))"
	@echo "--> default render: the fragment, the bound, the policy"
	@helm template t $(CHART_DIR) $(VM) >/tmp/vv-meta.out 2>&1 || { cat /tmp/vv-meta.out; exit 1; }
	@python3 tests/verify-valkey.py /tmp/vv-meta.out
	@echo "--> an installation's own valkeyConfig replaces the fragment whole"
	@helm template t $(CHART_DIR) $(VM) -f $(CHART_DIR)/ci/test-valkey-override-values.yaml >/tmp/vv-meta-override.out 2>&1 || { cat /tmp/vv-meta-override.out; exit 1; }
	@python3 tests/verify-valkey.py --override /tmp/vv-meta-override.out
	@echo "$@: all passed"

verify-klausgateway-valkey: ## Assert the gateway's Valkey routing-store defaults (giantswarm/klaus-gateway#252): with klausGateway.routing.store: valkey the klaus-gateway release's routing.valkey url, existingSecret and passwordKey are filled from the valkey release (Service name, auth Secret, default user's key); an operator's own value wins; any other store forwards nothing about Valkey.
	@echo "====> $@ ($(CHART_DIR))"
	@echo "--> routing.store memory (the default): no routing.valkey forwarded"
	@helm template t $(CHART_DIR) $(VM) --set components.klaus-gateway.enabled=true >/tmp/vkv-memory.out 2>&1 || { cat /tmp/vkv-memory.out; exit 1; }
	@python3 tests/verify-klausgateway-valkey.py --memory /tmp/vkv-memory.out
	@echo "--> routing.store valkey: url, existingSecret, passwordKey from the valkey release"
	@helm template t $(CHART_DIR) $(VM) -f $(CHART_DIR)/ci/test-klausgateway-valkey-values.yaml >/tmp/vkv-defaults.out 2>&1 || { cat /tmp/vkv-defaults.out; exit 1; }
	@python3 tests/verify-klausgateway-valkey.py --defaults /tmp/vkv-defaults.out
	@echo "--> an operator's own url and Secret win; the key is still filled"
	@helm template t $(CHART_DIR) $(VM) -f $(CHART_DIR)/ci/test-klausgateway-valkey-values.yaml --set klausGateway.routing.valkey.url=cache.example.internal:6380 --set klausGateway.routing.valkey.existingSecret=my-valkey >/tmp/vkv-own.out 2>&1 || { cat /tmp/vkv-own.out; exit 1; }
	@python3 tests/verify-klausgateway-valkey.py --own /tmp/vkv-own.out
	@echo "$@: all passed"

verify-disruption: ## Assert the voluntary-disruption guards (giantswarm/agent-platform#431): karpenter.sh/do-not-disrupt on muster, kagent-controller, klaus-gateway and muster-valkey (their charts' podAnnotations) and NOT on the agentgateway data plane, which is HA instead (two replicas + budget + spread, verify-dataplane-ha) though its annotation knob still travels when set; PodDisruptionBudgets from the muster, kagent and klaus-gateway charts' knobs and the connectivity chart's own for agent-manager and muster-valkey (#439); every knob off = nothing; the budgets' guards. And the placement of the stateful singletons (#439): scheduling.singletons.nodeSelector / tolerations reach muster, muster-valkey, the kagent controller and klaus-gateway as their charts' knobs (a component's own keys win), never the connectivity release; empty = nothing forwarded.
	@echo "====> $@ ($(CONNECTIVITY_DIR) + $(CHART_DIR))"
	@echo "--> connectivity, agent-manager on: the HA data plane carries NO do-not-disrupt, and the agent-manager budget renders"
	@helm template t $(CONNECTIVITY_DIR) $(MANAGERS_ON) >/tmp/vd-on.out 2>&1 || { cat /tmp/vd-on.out; exit 1; }
	@awk '/^kind: AgentgatewayParameters/,/^---/' /tmp/vd-on.out >/tmp/vd-params.out
	@if grep -q 'do-not-disrupt' /tmp/vd-params.out; then echo "FAIL: the data-plane pod template carries karpenter.sh/do-not-disrupt. That was #431's answer to a SINGLE data-plane pod; next to gateway.parameters.replicas 2, the budget and the spread it pins BOTH pods' nodes against Karpenter's consolidation, drift and expiry indefinitely, and the budget -- which is what holds a drain to one pod at a time -- is never reached"; cat /tmp/vd-params.out; exit 1; fi
	@echo "ok: no do-not-disrupt on the HA data plane (its shape is verify-dataplane-ha's)"
	@echo "--> connectivity: the data-plane annotation knob still travels when an installation sets it"
	@helm template t $(CONNECTIVITY_DIR) $(MANAGERS_ON) --set-string 'gateway.parameters.podAnnotations.karpenter\.sh/do-not-disrupt=true' >/tmp/vd-ann.out 2>&1 || { cat /tmp/vd-ann.out; exit 1; }
	@awk '/^kind: AgentgatewayParameters/,/^---/' /tmp/vd-ann.out >/tmp/vd-ann-params.out
	@grep -q '^      template:$$' /tmp/vd-ann-params.out || { echo "FAIL: AgentgatewayParameters deployment overlay lost its pod template"; exit 1; }
	@grep -A2 '^        metadata:$$' /tmp/vd-ann-params.out | grep -q 'karpenter.sh/do-not-disrupt: "true"' || { echo "FAIL: gateway.parameters.podAnnotations no longer reaches deployment.spec.template.metadata, so an installation cannot trade node churn for its open streams"; cat /tmp/vd-ann-params.out; exit 1; }
	@echo "ok: the knob reaches deployment.spec.template.metadata"
	@awk '/^kind: PodDisruptionBudget/,/^---/' /tmp/vd-on.out | awk '/^  name: agent-manager$$/{f=1} /^---/{f=0} f' >/tmp/vd-pdb.out
	@grep -q '^  name: agent-manager$$' /tmp/vd-pdb.out || { echo "FAIL: no PodDisruptionBudget agent-manager in the render"; exit 1; }
	@grep -q '^  minAvailable: 1$$' /tmp/vd-pdb.out || { echo "FAIL: the agent-manager budget is not minAvailable: 1"; cat /tmp/vd-pdb.out; exit 1; }
	@if grep -q 'maxUnavailable' /tmp/vd-pdb.out; then echo "FAIL: the agent-manager budget carries maxUnavailable next to minAvailable"; exit 1; fi
	@grep -q '^  unhealthyPodEvictionPolicy: AlwaysAllow$$' /tmp/vd-pdb.out || { echo "FAIL: the agent-manager budget does not keep unhealthy pods evictable (AlwaysAllow)"; exit 1; }
	@grep -A2 '^    matchLabels:$$' /tmp/vd-pdb.out | grep -q 'app.kubernetes.io/name: agent-manager' || { echo "FAIL: the agent-manager budget does not select the agent-manager pods by name"; exit 1; }
	@[ "$$(grep -c '^kind: PodDisruptionBudget' /tmp/vd-on.out)" = "3" ] || { echo "FAIL: expected exactly three PodDisruptionBudgets from the connectivity chart (agent-manager, muster-valkey, the Substrate worker pool — #472, verify-workerpool asserts that one), got $$(grep -c '^kind: PodDisruptionBudget' /tmp/vd-on.out)"; exit 1; }
	@echo "ok: agent-manager budget minAvailable: 1, AlwaysAllow, selects the pods by name"
	@echo "--> connectivity: the muster-valkey budget (#439) renders from valkey.podDisruptionBudget, named after the Deployment, selecting the pod as the valkey subchart labels it"
	@awk '/^kind: PodDisruptionBudget/,/^---/' /tmp/vd-on.out | awk '/^  name: muster-valkey$$/{f=1} /^---/{f=0} f' >/tmp/vd-valkey-pdb.out
	@grep -q '^  name: muster-valkey$$' /tmp/vd-valkey-pdb.out || { echo "FAIL: no PodDisruptionBudget muster-valkey in the render"; exit 1; }
	@grep -q '^  minAvailable: 1$$' /tmp/vd-valkey-pdb.out || { echo "FAIL: the muster-valkey budget is not minAvailable: 1"; cat /tmp/vd-valkey-pdb.out; exit 1; }
	@if grep -q 'maxUnavailable' /tmp/vd-valkey-pdb.out; then echo "FAIL: the muster-valkey budget carries maxUnavailable next to minAvailable"; exit 1; fi
	@grep -q '^  unhealthyPodEvictionPolicy: AlwaysAllow$$' /tmp/vd-valkey-pdb.out || { echo "FAIL: the muster-valkey budget does not keep unhealthy pods evictable (AlwaysAllow)"; exit 1; }
	@grep -A3 '^    matchLabels:$$' /tmp/vd-valkey-pdb.out | grep -q 'app.kubernetes.io/name: valkey' || { echo "FAIL: the muster-valkey budget does not select the valkey pods by name"; cat /tmp/vd-valkey-pdb.out; exit 1; }
	@grep -A3 '^    matchLabels:$$' /tmp/vd-valkey-pdb.out | grep -q 'app.kubernetes.io/instance: valkey' || { echo "FAIL: the muster-valkey budget does not select the valkey release's pods (app.kubernetes.io/instance: valkey)"; cat /tmp/vd-valkey-pdb.out; exit 1; }
	@echo "ok: muster-valkey budget minAvailable: 1, AlwaysAllow, selects the valkey release's pods by name and instance"
	@echo "--> knobs off: no annotation, no budget"
	@helm template t $(CONNECTIVITY_DIR) $(MANAGERS_ON) --set gateway.parameters.podAnnotations=null --set agentManager.podDisruptionBudget.enabled=false --set valkey.podDisruptionBudget.enabled=false --set kagent.substrateWorkerPool.podDisruptionBudget.enabled=false >/tmp/vd-off.out 2>&1 || { cat /tmp/vd-off.out; exit 1; }
	@if grep -q 'do-not-disrupt' /tmp/vd-off.out; then echo "FAIL: karpenter.sh/do-not-disrupt renders with gateway.parameters.podAnnotations cleared"; exit 1; fi
	@if grep -q '^kind: PodDisruptionBudget' /tmp/vd-off.out; then echo "FAIL: a PodDisruptionBudget renders with agentManager.podDisruptionBudget.enabled=false, valkey.podDisruptionBudget.enabled=false and kagent.substrateWorkerPool.podDisruptionBudget.enabled=false"; exit 1; fi
	@echo "ok: knobs off render nothing"
	@echo "--> agent-manager off: its budget is inert (the muster-valkey budget stays: valkey is on)"
	@helm template t $(CONNECTIVITY_DIR) $(VM) --set components.kagent.enabled=true >/tmp/vd-am-off.out 2>&1 || { cat /tmp/vd-am-off.out; exit 1; }
	@if awk '/^kind: PodDisruptionBudget/,/^---/' /tmp/vd-am-off.out | grep -q '^  name: agent-manager$$'; then echo "FAIL: the agent-manager PodDisruptionBudget renders while agent-manager is off"; exit 1; fi
	@awk '/^kind: PodDisruptionBudget/,/^---/' /tmp/vd-am-off.out | grep -q '^  name: muster-valkey$$' || { echo "FAIL: the muster-valkey budget vanished with agent-manager off"; exit 1; }
	@echo "ok: inert while the component is off"
	@echo "--> valkey off: its budget is inert (the worker budget stays: kagent is on)"
	@helm template t $(CONNECTIVITY_DIR) $(VM) --set components.kagent.enabled=true --set components.valkey.enabled=false >/tmp/vd-valkey-off.out 2>&1 || { cat /tmp/vd-valkey-off.out; exit 1; }
	@if awk '/^kind: PodDisruptionBudget/,/^---/' /tmp/vd-valkey-off.out | grep -qE '^  name: (muster-valkey|agent-manager)$$'; then echo "FAIL: the muster-valkey or agent-manager PodDisruptionBudget renders while valkey and agent-manager are off"; exit 1; fi
	@[ "$$(grep -c '^kind: PodDisruptionBudget' /tmp/vd-valkey-off.out)" = "1" ] || { echo "FAIL: expected the worker budget alone with valkey and agent-manager off, got $$(grep -c '^kind: PodDisruptionBudget' /tmp/vd-valkey-off.out)"; exit 1; }
	@echo "--> kagent off: the worker budget is inert"
	@helm template t $(CONNECTIVITY_DIR) $(VM) --set components.valkey.enabled=false >/tmp/vd-kagent-off.out 2>&1 || { cat /tmp/vd-kagent-off.out; exit 1; }
	@if grep -q '^kind: PodDisruptionBudget' /tmp/vd-kagent-off.out; then echo "FAIL: a PodDisruptionBudget renders while kagent, valkey and agent-manager are off"; exit 1; fi
	@echo "ok: inert while valkey is off"
	@echo "--> the muster-valkey budget's guards"
	@if helm template t $(CONNECTIVITY_DIR) $(MANAGERS_ON) --set valkey.podDisruptionBudget.maxUnavailable=1 >/tmp/vd-valkey-both.out 2>&1; then echo "FAIL: both minAvailable and maxUnavailable accepted on valkey.podDisruptionBudget"; exit 1; fi
	@grep -q 'valkey.podDisruptionBudget sets both minAvailable and maxUnavailable' /tmp/vd-valkey-both.out || { echo "FAIL: wrong error for both fields set on valkey.podDisruptionBudget"; tail -3 /tmp/vd-valkey-both.out; exit 1; }
	@if helm template t $(CONNECTIVITY_DIR) $(MANAGERS_ON) --set valkey.podDisruptionBudget.minAvailable=null >/tmp/vd-valkey-none.out 2>&1; then echo "FAIL: a valkey budget with neither field accepted"; exit 1; fi
	@grep -q 'neither minAvailable nor maxUnavailable' /tmp/vd-valkey-none.out || { echo "FAIL: wrong error for neither field set on valkey.podDisruptionBudget"; tail -3 /tmp/vd-valkey-none.out; exit 1; }
	@if helm template t $(CONNECTIVITY_DIR) $(MANAGERS_ON) --set valkey.podDisruptionBudget.unhealthyPodEvictionPolicy=Sometimes >/tmp/vd-valkey-enum.out 2>&1; then echo "FAIL: an unknown unhealthyPodEvictionPolicy accepted on valkey.podDisruptionBudget"; exit 1; fi
	@grep -q 'is not a PodDisruptionBudget eviction policy' /tmp/vd-valkey-enum.out || { echo "FAIL: wrong error for the valkey eviction policy enum"; tail -3 /tmp/vd-valkey-enum.out; exit 1; }
	@if helm template t $(CONNECTIVITY_DIR) $(MANAGERS_ON) --set valkey.valkey.fullnameOverride=null >/tmp/vd-valkey-name.out 2>&1; then echo "FAIL: a valkey budget without valkey.valkey.fullnameOverride accepted"; exit 1; fi
	@grep -q 'valkey.valkey.fullnameOverride must be set' /tmp/vd-valkey-name.out || { echo "FAIL: wrong error for the missing valkey fullnameOverride"; tail -3 /tmp/vd-valkey-name.out; exit 1; }
	@echo "ok: the muster-valkey budget's guards fire"
	@echo "--> the agent-manager budget's guards"
	@if helm template t $(CONNECTIVITY_DIR) $(MANAGERS_ON) --set agentManager.podDisruptionBudget.maxUnavailable=1 >/tmp/vd-both.out 2>&1; then echo "FAIL: both minAvailable and maxUnavailable accepted"; exit 1; fi
	@grep -q 'sets both minAvailable and maxUnavailable' /tmp/vd-both.out || { echo "FAIL: wrong error for both fields set"; tail -3 /tmp/vd-both.out; exit 1; }
	@if helm template t $(CONNECTIVITY_DIR) $(MANAGERS_ON) --set agentManager.podDisruptionBudget.minAvailable=null >/tmp/vd-none.out 2>&1; then echo "FAIL: a budget with neither field accepted"; exit 1; fi
	@grep -q 'neither minAvailable nor maxUnavailable' /tmp/vd-none.out || { echo "FAIL: wrong error for neither field set"; tail -3 /tmp/vd-none.out; exit 1; }
	@if helm template t $(CONNECTIVITY_DIR) $(MANAGERS_ON) --set agentManager.podDisruptionBudget.unhealthyPodEvictionPolicy=Sometimes >/tmp/vd-enum.out 2>&1; then echo "FAIL: an unknown unhealthyPodEvictionPolicy accepted"; exit 1; fi
	@grep -q 'is not a PodDisruptionBudget eviction policy' /tmp/vd-enum.out || { echo "FAIL: wrong error for the eviction policy enum"; tail -3 /tmp/vd-enum.out; exit 1; }
	@helm template t $(CONNECTIVITY_DIR) $(MANAGERS_ON) --set agentManager.podDisruptionBudget.minAvailable=null --set agentManager.podDisruptionBudget.maxUnavailable=50% >/tmp/vd-max.out 2>&1 || { cat /tmp/vd-max.out; exit 1; }
	@awk '/^kind: PodDisruptionBudget/,/^---/' /tmp/vd-max.out | grep -q '^  maxUnavailable: 50%$$' || { echo "FAIL: maxUnavailable alone does not pass through"; exit 1; }
	@echo "ok: guards fire, maxUnavailable alone passes through"
	@echo "--> meta chart: the component charts' own knobs travel on their HelmReleases"
	@helm template t $(CHART_DIR) $(VM) --set components.kagent.enabled=true --set components.klaus-gateway.enabled=true --set components.agent-manager.enabled=true --set components.agentgateway.enabled=true >/tmp/vd-meta.out 2>&1 || { cat /tmp/vd-meta.out; exit 1; }
	@python3 tests/verify-disruption.py /tmp/vd-meta.out
	@echo "--> meta chart: a component's knob off leaves its HelmRelease without it"
	@helm template t $(CHART_DIR) $(VM) --set components.kagent.enabled=true --set components.klaus-gateway.enabled=true --set components.agent-manager.enabled=true --set components.agentgateway.enabled=true --set muster.podDisruptionBudget.enabled=false --set kagent.controller.pdb.enabled=false --set klausGateway.podDisruptionBudget.enabled=false --set valkey.podDisruptionBudget.enabled=false --set 'muster.podAnnotations.karpenter\.sh/do-not-disrupt=null' --set 'valkey.valkey.podAnnotations.karpenter\.sh/do-not-disrupt=null' >/tmp/vd-meta-off.out 2>&1 || { cat /tmp/vd-meta-off.out; exit 1; }
	@python3 tests/verify-disruption.py --off /tmp/vd-meta-off.out
	@echo "--> meta chart: scheduling.singletons (#439) reaches the four singletons as their charts' nodeSelector / tolerations — merged under a component's own keys (muster keeps its spot + zone selector, the kagent controller its own toleration first) — and never the connectivity release"
	@helm template t $(CHART_DIR) $(VM) --set components.kagent.enabled=true --set components.klaus-gateway.enabled=true --set components.agent-manager.enabled=true --set components.agentgateway.enabled=true --set 'scheduling.singletons.nodeSelector.karpenter\.sh/capacity-type=on-demand' --set 'scheduling.singletons.tolerations[0].key=dedicated' --set 'scheduling.singletons.tolerations[0].operator=Equal' --set 'scheduling.singletons.tolerations[0].value=singletons' --set 'scheduling.singletons.tolerations[0].effect=NoSchedule' --set 'muster.nodeSelector.karpenter\.sh/capacity-type=spot' --set 'muster.nodeSelector.topology\.kubernetes\.io/zone=eu-central-1a' --set 'kagent.controller.tolerations[0].key=own' --set 'kagent.controller.tolerations[0].operator=Exists' >/tmp/vd-meta-place.out 2>&1 || { cat /tmp/vd-meta-place.out; exit 1; }
	@python3 tests/verify-disruption.py --placement /tmp/vd-meta-place.out
	@echo "--> meta chart: the schema takes the knob's shape and refuses a stray key next to it"
	@if helm template t $(CHART_DIR) $(VM) --set scheduling.singletons.nodeSelektor.x=y >/tmp/vd-meta-typo.out 2>&1; then echo "FAIL: scheduling.singletons.nodeSelektor (a typo) passed the schema"; exit 1; fi
	@grep -q 'nodeSelektor' /tmp/vd-meta-typo.out || { echo "FAIL: the typo was refused for the wrong reason"; tail -3 /tmp/vd-meta-typo.out; exit 1; }
	@echo "ok: a stray key under scheduling.singletons is refused by the schema"
	@echo "$@: all passed"

# The kagent controller's VerticalPodAutoscaler (templates/kagent/controller-vpa.yaml):
# kagent on under the fleet's API groups; VPA_VANILLA serves no group at all, so
# the `auto` knob resolves off.
VPA_ON := $(VM) --set components.kagent.enabled=true
VPA_VANILLA := --set ingress.parentRefs[0].name=x --set kagent.harness.snapshotLocation=s3://ci-agent-snapshots/agents --set components.kagent.enabled=true

.PHONY: verify-kagent-vpa
verify-kagent-vpa: ## Assert the kagent controller's VerticalPodAutoscaler: with autoscaling.k8s.io/v1 served the connectivity chart renders it on Deployment kagent-controller in the kagent namespace (InPlaceOrRecreate, RequestsOnly, minAllowed the chart's requests, maxAllowed a step under its limits, minReplicas 1); a vanilla render none; an explicit true / false wins both ways; inert with kagent off; a deleted kagent.controller.vpa or kagent.controller renders nothing in both charts rather than dying on a nil pointer; the enum guards, the spelling guard (VPA off too) and the unset wording fire; the meta chart forwards the knob resolved to the connectivity release and never to the kagent release, and a meta-layer override reaches the rendered object.
	@echo "====> $@ ($(CONNECTIVITY_DIR) + $(CHART_DIR))"
	@echo "--> connectivity, autoscaling.k8s.io/v1 served (the fleet): the VPA renders"
	@helm template t $(CONNECTIVITY_DIR) $(VPA_ON) >/tmp/vk-on.out 2>&1 || { cat /tmp/vk-on.out; exit 1; }
	@awk '/^kind: VerticalPodAutoscaler/,/^---/' /tmp/vk-on.out >/tmp/vk-vpa.out
	@[ "$$(grep -c '^kind: VerticalPodAutoscaler' /tmp/vk-on.out)" = "1" ] || { echo "FAIL: expected exactly one VerticalPodAutoscaler"; exit 1; }
	@grep -q '^  name: kagent-controller$$' /tmp/vk-vpa.out || { echo "FAIL: the VPA is not named kagent-controller"; cat /tmp/vk-vpa.out; exit 1; }
	@grep -q '^  namespace: kagent$$' /tmp/vk-vpa.out || { echo "FAIL: the VPA is not in the kagent namespace"; cat /tmp/vk-vpa.out; exit 1; }
	@grep -A3 '^  targetRef:$$' /tmp/vk-vpa.out | grep -q '^    kind: Deployment$$' || { echo "FAIL: the VPA does not target a Deployment"; cat /tmp/vk-vpa.out; exit 1; }
	@grep -A3 '^  targetRef:$$' /tmp/vk-vpa.out | grep -q '^    name: kagent-controller$$' || { echo "FAIL: the VPA does not target kagent-controller"; cat /tmp/vk-vpa.out; exit 1; }
	@grep -q '^    updateMode: InPlaceOrRecreate$$' /tmp/vk-vpa.out || { echo "FAIL: updateMode is not InPlaceOrRecreate"; cat /tmp/vk-vpa.out; exit 1; }
	@grep -q '^    minReplicas: 1$$' /tmp/vk-vpa.out || { echo "FAIL: minReplicas: 1 missing"; cat /tmp/vk-vpa.out; exit 1; }
	@grep -q '^      - containerName: controller$$' /tmp/vk-vpa.out || { echo "FAIL: the container policy does not name the controller container"; cat /tmp/vk-vpa.out; exit 1; }
	@grep -q '^        controlledValues: RequestsOnly$$' /tmp/vk-vpa.out || { echo "FAIL: controlledValues is not RequestsOnly"; cat /tmp/vk-vpa.out; exit 1; }
	@grep -A2 '^        minAllowed:$$' /tmp/vk-vpa.out | grep -q 'cpu: 100m' || { echo "FAIL: minAllowed.cpu is not the chart's request (100m)"; cat /tmp/vk-vpa.out; exit 1; }
	@grep -A2 '^        minAllowed:$$' /tmp/vk-vpa.out | grep -q 'memory: 128Mi' || { echo "FAIL: minAllowed.memory is not the chart's request (128Mi)"; cat /tmp/vk-vpa.out; exit 1; }
	@grep -A2 '^        maxAllowed:$$' /tmp/vk-vpa.out | grep -q 'cpu: 1900m' || { echo "FAIL: maxAllowed.cpu is not a step under the chart's limit (1900m)"; cat /tmp/vk-vpa.out; exit 1; }
	@grep -A2 '^        maxAllowed:$$' /tmp/vk-vpa.out | grep -q 'memory: 1280Mi' || { echo "FAIL: maxAllowed.memory is not a step under the controller's limit (1280Mi)"; cat /tmp/vk-vpa.out; exit 1; }
	@echo "ok: VerticalPodAutoscaler kagent-controller on Deployment kagent-controller — InPlaceOrRecreate, RequestsOnly, 100m/128Mi to 1900m/1280Mi"
	@echo "--> vanilla (no autoscaling.k8s.io/v1): auto resolves off"
	@helm template t $(CONNECTIVITY_DIR) $(VPA_VANILLA) >/tmp/vk-vanilla.out 2>&1 || { cat /tmp/vk-vanilla.out; exit 1; }
	@if grep -q '^kind: VerticalPodAutoscaler' /tmp/vk-vanilla.out; then echo "FAIL: a VerticalPodAutoscaler renders without autoscaling.k8s.io/v1 served"; exit 1; fi
	@echo "ok: nothing on a vanilla cluster"
	@echo "--> explicit values win over detection, both ways"
	@helm template t $(CONNECTIVITY_DIR) $(VPA_VANILLA) --set kagent.controller.vpa.enabled=true >/tmp/vk-force-on.out 2>&1 || { cat /tmp/vk-force-on.out; exit 1; }
	@grep -q '^kind: VerticalPodAutoscaler' /tmp/vk-force-on.out || { echo "FAIL: kagent.controller.vpa.enabled=true renders nothing without the API served"; exit 1; }
	@helm template t $(CONNECTIVITY_DIR) $(VPA_ON) --set kagent.controller.vpa.enabled=false >/tmp/vk-force-off.out 2>&1 || { cat /tmp/vk-force-off.out; exit 1; }
	@if grep -q '^kind: VerticalPodAutoscaler' /tmp/vk-force-off.out; then echo "FAIL: kagent.controller.vpa.enabled=false still renders the VPA"; exit 1; fi
	@echo "ok: explicit true / false win"
	@echo "--> kagent off: inert"
	@helm template t $(CONNECTIVITY_DIR) $(VM) --set kagent.controller.vpa.enabled=true >/tmp/vk-kagent-off.out 2>&1 || { cat /tmp/vk-kagent-off.out; exit 1; }
	@if grep -q '^kind: VerticalPodAutoscaler' /tmp/vk-kagent-off.out; then echo "FAIL: the VPA renders while kagent is off"; exit 1; fi
	@echo "ok: inert while kagent is off"
	@echo "--> the block is the switch: a deleted kagent.controller.vpa — or a deleted kagent.controller around it — renders nothing and never dereferences a key that is gone"
	@for deleted in kagent.controller.vpa kagent.controller; do \
		helm template t $(CONNECTIVITY_DIR) $(VPA_ON) --set $$deleted=null >/tmp/vk-deleted.out 2>&1 || { echo "FAIL: the render died with $$deleted deleted"; tail -3 /tmp/vk-deleted.out; exit 1; }; \
		if grep -q '^kind: VerticalPodAutoscaler' /tmp/vk-deleted.out; then echo "FAIL: the VPA renders with $$deleted deleted"; exit 1; fi; \
		helm template t $(CHART_DIR) $(VPA_ON) --set $$deleted=null >/tmp/vk-deleted-meta.out 2>&1 || { echo "FAIL: the meta render died with $$deleted deleted"; tail -3 /tmp/vk-deleted-meta.out; exit 1; }; \
	done
	@echo "ok: a deleted block renders nothing, in both charts"
	@echo "--> the guards"
	@if helm template t $(CONNECTIVITY_DIR) $(VPA_ON) --set kagent.controller.vpa.updateMode=Sometimes >/tmp/vk-mode.out 2>&1; then echo "FAIL: an unknown updateMode accepted"; exit 1; fi
	@grep -q 'is not a VerticalPodAutoscaler update mode' /tmp/vk-mode.out || { echo "FAIL: wrong error for the updateMode enum"; tail -3 /tmp/vk-mode.out; exit 1; }
	@if helm template t $(CONNECTIVITY_DIR) $(VPA_ON) --set kagent.controller.vpa.controlledValues=Nothing >/tmp/vk-cv.out 2>&1; then echo "FAIL: an unknown controlledValues accepted"; exit 1; fi
	@grep -q 'is not a VerticalPodAutoscaler controlledValues' /tmp/vk-cv.out || { echo "FAIL: wrong error for the controlledValues enum"; tail -3 /tmp/vk-cv.out; exit 1; }
	@for chart in $(CONNECTIVITY_DIR) $(CHART_DIR); do \
		if helm template t $$chart $(VPA_ON) --set kagent.controller.vpa.enabled=maybe >/tmp/vk-maybe.out 2>&1; then echo "FAIL: $$chart: kagent.controller.vpa.enabled=maybe accepted"; exit 1; fi; \
		grep -q 'kagent.controller.vpa.enabled' /tmp/vk-maybe.out || { echo "FAIL: $$chart: wrong error for kagent.controller.vpa.enabled=maybe"; tail -3 /tmp/vk-maybe.out; exit 1; }; \
	done
	@echo "ok: the enum guards fire in both charts"
	@echo "--> a misspelt key is refused, VPA on or off (the kagent block is open in the schema); an unset updateMode is named as unset"
	@if helm template t $(CONNECTIVITY_DIR) $(VPA_VANILLA) --set kagent.controller.vpa.maxAllowd.cpu=5 >/tmp/vk-typo.out 2>&1; then echo "FAIL: kagent.controller.vpa.maxAllowd (a typo) rendered with the VPA off"; exit 1; fi
	@grep -q 'kagent.controller.vpa.maxAllowd is not a key' /tmp/vk-typo.out || { echo "FAIL: wrong error for the misspelt key"; tail -3 /tmp/vk-typo.out; exit 1; }
	@if helm template t $(CONNECTIVITY_DIR) $(VPA_ON) --set kagent.controller.vpa.updateMode=null >/tmp/vk-unset.out 2>&1; then echo "FAIL: an unset updateMode rendered"; exit 1; fi
	@grep -q 'kagent.controller.vpa.updateMode is unset' /tmp/vk-unset.out || { echo "FAIL: an unset updateMode is not named as unset"; tail -3 /tmp/vk-unset.out; exit 1; }
	@echo "ok: the spelling guard and the unset wording"
	@echo "--> meta chart: the resolved knob reaches the connectivity release, never the kagent release"
	@helm template t $(CHART_DIR) -f $(CHART_DIR)/ci/ci-values.yaml $(VPA_ON) >/tmp/vk-meta.out 2>&1 || { cat /tmp/vk-meta.out; exit 1; }
	@$(PICK) /tmp/vk-meta.out HelmRelease kagent >/tmp/vk-meta-kagent.out || { echo "FAIL: no kagent HelmRelease in the meta render"; exit 1; }
	@if grep -q 'vpa:' /tmp/vk-meta-kagent.out; then echo "FAIL: kagent.controller.vpa travels on the kagent HelmRelease (components.kagent.omitKeys)"; exit 1; fi
	@grep -q '^      pdb:$$' /tmp/vk-meta-kagent.out || { echo "FAIL: the rest of kagent.controller vanished from the kagent HelmRelease with the vpa hold-back"; exit 1; }
	@python3 -c 'import sys,yaml; d=[x for x in yaml.safe_load_all(open(sys.argv[1])) if x][0]; r=d["spec"]["values"]["controller"]["resources"]; assert r=={"requests":{"cpu":"100m","memory":"128Mi"},"limits":{"cpu":2,"memory":"1536Mi"}}, r; print("ok: the kagent HelmRelease carries the controller limits 2 / 1536Mi the VPA cap sits under")' /tmp/vk-meta-kagent.out || { echo "FAIL: kagent.controller.resources did not reach the kagent HelmRelease as 100m/128Mi requests, 2/1536Mi limits"; exit 1; }
	@$(PICK) /tmp/vk-meta.out HelmRelease agent-platform-connectivity >/tmp/vk-meta-conn.out || { echo "FAIL: no agent-platform-connectivity HelmRelease in the meta render"; exit 1; }
	@grep -A9 '^        vpa:$$' /tmp/vk-meta-conn.out | grep -q '^          enabled: true$$' || { echo "FAIL: the connectivity HelmRelease does not carry kagent.controller.vpa.enabled resolved to true with the API served"; exit 1; }
	@helm template t $(CHART_DIR) -f $(CHART_DIR)/ci/ci-values.yaml $(VPA_VANILLA) >/tmp/vk-meta-vanilla.out 2>&1 || { cat /tmp/vk-meta-vanilla.out; exit 1; }
	@$(PICK) /tmp/vk-meta-vanilla.out HelmRelease agent-platform-connectivity >/tmp/vk-meta-conn-vanilla.out || { echo "FAIL: no agent-platform-connectivity HelmRelease in the vanilla meta render"; exit 1; }
	@grep -A9 '^        vpa:$$' /tmp/vk-meta-conn-vanilla.out | grep -q '^          enabled: false$$' || { echo "FAIL: the connectivity HelmRelease does not carry kagent.controller.vpa.enabled resolved to false without the API"; exit 1; }
	@if grep -q 'enabled: auto' /tmp/vk-meta.out /tmp/vk-meta-vanilla.out; then echo "FAIL: an unresolved auto reached a HelmRelease"; exit 1; fi
	@echo "ok: resolved once, forwarded to connectivity only"
	@echo "--> meta chart: an override set at the meta layer (updateMode, maxAllowed.memory) reaches the rendered VPA through the connectivity HelmRelease's values"
	@helm template t $(CHART_DIR) $(VPA_ON) --set kagent.controller.vpa.updateMode=Initial --set kagent.controller.vpa.maxAllowed.memory=400Mi >/tmp/vk-meta-over.out 2>&1 || { cat /tmp/vk-meta-over.out; exit 1; }
	@$(PICK) /tmp/vk-meta-over.out HelmRelease agent-platform-connectivity | sed -n '/^  values:$$/,$$p' | sed '1d; s/^    //' | sed '/^---$$/,$$d' >/tmp/vk-meta-over-values.yaml
	@helm template t $(CONNECTIVITY_DIR) -n default -f /tmp/vk-meta-over-values.yaml $(FLEET_APIS) >/tmp/vk-conn-over.out 2>&1 || { echo "FAIL: the connectivity chart rejects the values the meta chart forwards"; tail -3 /tmp/vk-conn-over.out; exit 1; }
	@awk '/^kind: VerticalPodAutoscaler/,/^---/' /tmp/vk-conn-over.out >/tmp/vk-conn-over-vpa.out
	@grep -q '^    updateMode: Initial$$' /tmp/vk-conn-over-vpa.out || { echo "FAIL: kagent.controller.vpa.updateMode set at the meta layer did not reach the rendered VPA"; cat /tmp/vk-conn-over-vpa.out; exit 1; }
	@grep -A2 '^        maxAllowed:$$' /tmp/vk-conn-over-vpa.out | grep -q 'memory: 400Mi' || { echo "FAIL: kagent.controller.vpa.maxAllowed.memory set at the meta layer did not reach the rendered VPA"; cat /tmp/vk-conn-over-vpa.out; exit 1; }
	@grep -A2 '^        maxAllowed:$$' /tmp/vk-conn-over-vpa.out | grep -q 'cpu: 1900m' || { echo "FAIL: the untouched maxAllowed.cpu default did not survive a sibling override at the meta layer"; cat /tmp/vk-conn-over-vpa.out; exit 1; }
	@echo "ok: meta-layer overrides reach the object, siblings keep their defaults"
	@echo "$@: all passed"

verify-kagent-netpol: ## Assert the kagent controller's and the actors' egress (Substrate's egress gateway) to the built-in tool server renders iff kagent.kagent-tools.enabled, in the namespace and port the kagent chart renders the server into (kagent.kagent-tools.namespaceOverride, else the release namespace — tied to the rendered Deployment and RemoteMCPServer URL of the kagent chart the range resolves to by tests/verify-kagent-tools-namespace.py; network: gsoci.azurecr.io); Agent Substrate's hops in both flavours (the worker pods reach only the egress gateway, the dns and the cluster DNS; the egress gateway carries the actors' allow-list; the controller reaches ate-api and the router; no `app: kagent` selector remains outside the two v1alpha2 templates #299 deletes); that the egress gateway opens every host model server model-manager fronts, at its agentHost, with the DNS proxy on where one is named by hostname; and the oauth2-proxy ingress admits kagent.oauth2ProxyIngress.additionalPeers on the proxy port only.
	@echo "====> $@ ($(CONNECTIVITY_DIR))"
	@echo "--> Agent Substrate on, cilium: the worker pods' egress is the egress gateway, the dns and the cluster DNS — nothing else"
	@helm template t $(CONNECTIVITY_DIR) $(KAGENT_NETPOL) >/tmp/vkn-sub.out 2>&1 || { cat /tmp/vkn-sub.out; exit 1; }
	@awk "/^  name: substrate-workers$$/,/^---/" /tmp/vkn-sub.out >/tmp/vkn-sub-workers.out
	@grep -q 'ate.dev/worker-pool' /tmp/vkn-sub-workers.out || { echo "FAIL: the worker-pod policy does not select ate.dev/worker-pool (the label ate-controller puts on every worker pod)"; exit 1; }
	@grep -q 'app: atenet-egress' /tmp/vkn-sub-workers.out || { echo "FAIL: the worker pods cannot reach the egress gateway"; exit 1; }
	@if grep -qE 'app.kubernetes.io/name: muster|toEntities:' /tmp/vkn-sub-workers.out; then echo "FAIL: the worker-pod policy opens the actors' destinations directly; those belong on the egress gateway"; cat /tmp/vkn-sub-workers.out; exit 1; fi
	@awk "/^  name: substrate-atenet-egress$$/,/^---/" /tmp/vkn-sub.out >/tmp/vkn-sub-egress.out
	@grep -q 'app.kubernetes.io/name: muster' /tmp/vkn-sub-egress.out || { echo "FAIL: the egress gateway has no egress to muster (the actors' tool calls)"; exit 1; }
	@grep -A6 'app.kubernetes.io/component: controller' /tmp/vkn-sub-egress.out | grep -q 'port: "8083"' || { echo "FAIL: the egress gateway has no egress to the kagent controller API"; exit 1; }
	@grep -B1 -A4 -- '- world' /tmp/vkn-sub-egress.out | grep -q 'port: "443"' || { echo "FAIL: the egress gateway has no world:443 (the LLM provider, git)"; exit 1; }
	@grep -q 'port: "10443"' /tmp/vkn-sub-egress.out || { echo "FAIL: the egress gateway lost the cluster 443/10443 rule (muster's OAuth endpoints behind an internal LB)"; exit 1; }
	@awk "/^  name: substrate-actors-to-kagent-controller$$/,/^---/" /tmp/vkn-sub.out | grep -q 'app: atenet-egress' || { echo "FAIL: the kagent controller does not admit the actors' egress gateway"; exit 1; }
	@awk "/^  name: agent-platform-connectivity-kagent-controller-egress$$/,/^---/" /tmp/vkn-sub.out >/tmp/vkn-sub-ctl.out
	@grep -A4 'app: ate-api-server' /tmp/vkn-sub-ctl.out | grep -q 'port: "443"' || { echo "FAIL: the kagent controller has no egress to ate-api"; exit 1; }
	@grep -A4 'app: atenet-router' /tmp/vkn-sub-ctl.out | grep -q 'port: "8080"' || { echo "FAIL: the kagent controller has no egress to the atenet router"; exit 1; }
	@awk "/^  name: substrate-atenet-router$$/,/^---/" /tmp/vkn-sub.out | awk "/ate.dev\/worker-pool/,/^    - |^---/" >/tmp/vkn-sub-router-workers.out
	@grep -q 'port: "443"' /tmp/vkn-sub-router-workers.out || { echo "FAIL: the atenet router has no egress to the worker pods' tunnel (443)"; exit 1; }
	@grep -q 'port: "8443"' /tmp/vkn-sub-router-workers.out || { echo "FAIL: the atenet router has no egress to the worker pods' mTLS CONNECT listener (8443) — every turn fails with 'Connect: deadline has elapsed' (#383)"; exit 1; }
	@for n in substrate-ate-api-server substrate-ate-controller substrate-atelet substrate-atenet-router substrate-dns substrate-podcertificate-controller; do grep -q "^  name: $$n$$" /tmp/vkn-sub.out || { echo "FAIL: no policy $$n"; exit 1; }; done
	@awk "/^  name: substrate-ate-api-server$$/,/^---/" /tmp/vkn-sub.out | grep -q 'port: "8085"' || { echo "FAIL: ate-api-server has no egress to atelet's hostPort 8085 (the bootstrap API → node agent rule upstream lacks)"; exit 1; }
	@if grep -q 'kagent-agent-muster-egress' /tmp/vkn-sub.out; then echo "FAIL: the v1alpha2 agent pods' egress policy is back; the actors' egress is the egress gateway's"; exit 1; fi
	@echo "ok: Substrate hops (cilium)"
	@echo "--> Agent Substrate on, kubernetes flavour: the ingress policies of the hops, no egress policy, no cilium.io object"
	@helm template t $(CONNECTIVITY_DIR) $(KAGENT_NETPOL) --set networkPolicy.flavor=kubernetes >/tmp/vkn-sub-k8s.out 2>&1 || { cat /tmp/vkn-sub-k8s.out; exit 1; }
	@for n in substrate-ate-api-server-ingress substrate-atenet-router-ingress substrate-atenet-egress-ingress substrate-dns-ingress substrate-workers-ingress substrate-actors-to-kagent-controller; do grep -q "^  name: $$n$$" /tmp/vkn-sub-k8s.out || { echo "FAIL: kubernetes flavour: no policy $$n"; exit 1; }; done
	@if grep -q 'cilium.io' /tmp/vkn-sub-k8s.out; then echo "FAIL: cilium.io objects render in the kubernetes flavour"; exit 1; fi
	@if awk '/^---/{p=0} /^  name: substrate-/{p=1} p' /tmp/vkn-sub-k8s.out | grep -q 'policyTypes: \[Egress\]'; then echo "FAIL: the kubernetes flavour renders an egress policy for Substrate; it renders ingress only, as for kagent (model-manager, on by default, has its own egress policy in this flavour)"; exit 1; fi
	@echo "ok: Substrate hops (kubernetes)"
	@echo "--> Substrate off: none of its policies renders"
	@helm template t $(CONNECTIVITY_DIR) $(VM) --set components.kagent.enabled=true --set muster.enabled=true --set networkPolicy.flavor=cilium >/tmp/vkn-nosub.out 2>&1 || { cat /tmp/vkn-nosub.out; exit 1; }
	@if grep -q 'name: substrate-' /tmp/vkn-nosub.out; then echo "FAIL: Substrate policies render while the component is off"; exit 1; else echo "ok: inert while Substrate is off"; fi
	@echo "--> no app: kagent selector remains (the v1alpha2 agent Deployments' label) outside the two templates #299 deletes"
	@if grep -rln 'app: kagent$$' $(CONNECTIVITY_DIR)/templates $(CHART_DIR)/templates | grep -vxF -e "$$(printf '%s\n' $(KAGENT_V1ALPHA2_TEMPLATES))"; then echo "FAIL: an app: kagent selector remains in the chart (above); on kagent API v2 no pod carries it"; exit 1; else echo "ok: no app: kagent selector left"; fi
	@echo "--> kagent-tools off (the default): no tool-server egress"
	@helm template t $(CONNECTIVITY_DIR) $(KAGENT_NETPOL) >/tmp/vkn-off.out 2>&1 || { cat /tmp/vkn-off.out; exit 1; }
	@if grep -q 'kagent-tools' /tmp/vkn-off.out; then echo "FAIL: tool-server egress renders while kagent-tools is off"; grep -n 'kagent-tools' /tmp/vkn-off.out | head; exit 1; else echo "ok: inert while off"; fi
	@echo "--> kagent-tools on with the meta chart's default kagent.kagent-tools.namespaceOverride=kagent: the controller's and the actors' egress (Substrate's egress gateway) to the kagent-tools pods on 8084 in the kagent namespace"
	@helm template t $(CONNECTIVITY_DIR) --namespace agent-platform $(KAGENT_NETPOL) --set kagent.kagent-tools.enabled=true --set kagent.kagent-tools.namespaceOverride=kagent >/tmp/vkn-on.out 2>&1 || { cat /tmp/vkn-on.out; exit 1; }
	@for n in agent-platform-connectivity-kagent-controller-egress substrate-atenet-egress; do \
		awk "/^  name: $$n$$/,/^---/" /tmp/vkn-on.out >/tmp/vkn-on-$$n.out; \
		grep -q 'app.kubernetes.io/name: kagent-tools' /tmp/vkn-on-$$n.out || { echo "FAIL: $$n has no egress to the kagent-tools pods"; exit 1; }; \
		grep -A1 'app.kubernetes.io/name: kagent-tools' /tmp/vkn-on-$$n.out | grep -q 'io.kubernetes.pod.namespace: kagent$$' || { echo "FAIL: $$n tool-server egress does not follow kagent.kagent-tools.namespaceOverride (kagent)"; exit 1; }; \
		grep -A4 'app.kubernetes.io/name: kagent-tools' /tmp/vkn-on-$$n.out | grep -q 'port: "8084"' || { echo "FAIL: $$n tool-server egress does not open port 8084"; exit 1; }; \
	done
	@echo "ok: both policies open the tool server"
	@echo "--> kagent-tools on without kagent.kagent-tools.namespaceOverride (this chart carries no default of its own): the rules name the RELEASE namespace, where the subchart renders without the override — not the kagent namespace kagent.namespaceOverride moves the controller to (#421)"
	@helm template t $(CONNECTIVITY_DIR) --namespace agent-platform $(KAGENT_NETPOL) --set kagent.kagent-tools.enabled=true >/tmp/vkn-release-ns.out 2>&1 || { cat /tmp/vkn-release-ns.out; exit 1; }
	@[ "$$(grep -A1 'app.kubernetes.io/name: kagent-tools' /tmp/vkn-release-ns.out | grep -c 'io.kubernetes.pod.namespace: agent-platform$$')" = "2" ] || { echo "FAIL: without kagent.kagent-tools.namespaceOverride the tool-server egress does not name the release namespace (where the kagent chart renders the server)"; grep -A1 'app.kubernetes.io/name: kagent-tools' /tmp/vkn-release-ns.out; exit 1; }
	@echo "ok: the rules follow the subchart's fallback, the release namespace"
	@echo "--> the rules' namespace and port are the kagent chart's: its rendered kagent-tools Deployment and RemoteMCPServer URL, with the values the meta chart forwards (network: gsoci.azurecr.io); the controller VPA's targetRef and containerName are its rendered controller Deployment's"
	@python3 tests/verify-kagent-tools-namespace.py $(CHART_DIR) $(CONNECTIVITY_DIR)
	@echo "--> an explicit kagent.kagent-tools.namespaceOverride / service.ports.tools.targetPort follows into the rules"
	@helm template t $(CONNECTIVITY_DIR) $(KAGENT_NETPOL) --set kagent.kagent-tools.enabled=true --set kagent.kagent-tools.namespaceOverride=tools-ns --set kagent.kagent-tools.service.ports.tools.targetPort=9084 >/tmp/vkn-override.out 2>&1 || { cat /tmp/vkn-override.out; exit 1; }
	@[ "$$(grep -A1 'app.kubernetes.io/name: kagent-tools' /tmp/vkn-override.out | grep -c 'io.kubernetes.pod.namespace: tools-ns$$')" = "2" ] || { echo "FAIL: the tool-server egress does not follow kagent.kagent-tools.namespaceOverride"; exit 1; }
	@[ "$$(grep -A4 'app.kubernetes.io/name: kagent-tools' /tmp/vkn-override.out | grep -c 'port: "9084"')" = "2" ] || { echo "FAIL: the tool-server egress does not follow the tools targetPort"; exit 1; }
	@echo "ok: namespace and port overrides"
	@echo "--> a host model server among model-manager's backends: the egress gateway opens the address the ModelConfigs carry"
	@helm template t $(CONNECTIVITY_DIR) $(KAGENT_NETPOL) >/tmp/vkn-mm-off.out 2>&1 || { cat /tmp/vkn-mm-off.out; exit 1; }
	@if grep -q 'host model server the actors dial' /tmp/vkn-mm-off.out; then echo "FAIL: host model server egress renders while model-manager is off"; exit 1; else echo "ok: inert while model-manager is off"; fi
	@helm template t $(CONNECTIVITY_DIR) $(KAGENT_MM) --set 'model-manager.backends[0]=ollama' --set 'model-manager.backends[1]=lmstudio' --set model-manager.lmstudio.endpoint=http://10.0.0.3:1234 >/tmp/vkn-mm-two.out 2>&1 || { cat /tmp/vkn-mm-two.out; exit 1; }
	@awk "/^  name: substrate-atenet-egress$$/,/^---/" /tmp/vkn-mm-two.out >/tmp/vkn-mm-two-pol.out
	@for pair in 10.0.0.1/32:11434 10.0.0.3/32:1234; do \
		addr=$${pair%%:*}; port=$${pair##*:}; \
		grep -A3 -e "- $$addr$$" /tmp/vkn-mm-two-pol.out | grep -q "port: \"$$port\"" || { echo "FAIL: the egress gateway does not open $$addr on $$port"; cat /tmp/vkn-mm-two-pol.out; exit 1; }; \
	done
	@echo "ok: every host backend opened for the actors"
	@echo "--> kserve alone among the backends: nothing to open on the host"
	@helm template t $(CONNECTIVITY_DIR) $(KAGENT_MM) --set 'model-manager.backends[0]=kserve' --set modelManager.kserve.requireApi=false >/tmp/vkn-mm-kserve.out 2>&1 || { cat /tmp/vkn-mm-kserve.out; exit 1; }
	@if grep -q 'host model server the actors dial' /tmp/vkn-mm-kserve.out; then echo "FAIL: a host-model rule renders for a kserve-only backend list"; exit 1; else echo "ok: kserve alone renders no host-model rule"; fi
	@echo "--> the inference path follows agentHost, not the endpoint model-manager itself dials"
	@helm template t $(CONNECTIVITY_DIR) $(KAGENT_MM) --set model-manager.ollama.agentHost=http://172.21.0.1:11434 >/tmp/vkn-mm-agenthost.out 2>&1 || { cat /tmp/vkn-mm-agenthost.out; exit 1; }
	@awk "/^  name: substrate-atenet-egress$$/,/^---/" /tmp/vkn-mm-agenthost.out >/tmp/vkn-mm-agenthost-pol.out
	@grep -q -- '- 172.21.0.1/32' /tmp/vkn-mm-agenthost-pol.out || { echo "FAIL: the egress gateway ignores model-manager.ollama.agentHost"; cat /tmp/vkn-mm-agenthost-pol.out; exit 1; }
	@if grep -q -- '- 10.0.0.1/32' /tmp/vkn-mm-agenthost-pol.out; then echo "FAIL: the egress gateway opens the management endpoint next to agentHost"; cat /tmp/vkn-mm-agenthost-pol.out; exit 1; fi
	@grep -q -- '- 10.0.0.1/32' /tmp/vkn-mm-agenthost.out || { echo "FAIL: model-manager's own policy lost the management endpoint"; exit 1; }
	@echo "ok: agentHost wins for the actors, the endpoint stays model-manager's"
	@echo "--> an agentHost without a scheme is refused by the chart, not parsed into an empty host"
	@if helm template t $(CONNECTIVITY_DIR) $(KAGENT_MM) --set model-manager.ollama.agentHost=ollama.lan >/tmp/vkn-mm-noscheme.out 2>&1; then echo "FAIL: a scheme-less model-manager.ollama.agentHost rendered"; grep -n 'matchName' /tmp/vkn-mm-noscheme.out; exit 1; fi
	@grep -q 'model-manager.ollama.agentHost' /tmp/vkn-mm-noscheme.out || { echo "FAIL: wrong error for a scheme-less agentHost"; tail -3 /tmp/vkn-mm-noscheme.out; exit 1; }
	@if helm template t $(CONNECTIVITY_DIR) $(KAGENT_MM) --set model-manager.ollama.agentHost=172.21.0.1:11434 >/tmp/vkn-mm-hostport.out 2>&1; then echo "FAIL: a host:port model-manager.ollama.agentHost rendered"; exit 1; fi
	@grep -q 'model-manager.ollama.agentHost' /tmp/vkn-mm-hostport.out || { echo "FAIL: wrong error for a host:port agentHost"; tail -3 /tmp/vkn-mm-hostport.out; exit 1; }
	@echo "ok: the agentHost guard matches the endpoint's"
	@echo "--> a host model server named by hostname takes the FQDN arm, and turns the DNS proxy on so the FQDN cache fills"
	@helm template t $(CONNECTIVITY_DIR) $(KAGENT_MM) --set model-manager.ollama.endpoint=http://ollama.lan:11434 >/tmp/vkn-mm-fqdn.out 2>&1 || { cat /tmp/vkn-mm-fqdn.out; exit 1; }
	@awk "/^  name: substrate-atenet-egress$$/,/^---/" /tmp/vkn-mm-fqdn.out >/tmp/vkn-mm-fqdn-pol.out
	@grep -q 'matchName: ollama.lan$$' /tmp/vkn-mm-fqdn-pol.out || { echo "FAIL: no FQDN rule for a hostname endpoint"; cat /tmp/vkn-mm-fqdn-pol.out; exit 1; }
	@grep -A4 'matchName: ollama.lan$$' /tmp/vkn-mm-fqdn-pol.out | grep -q 'port: "11434"' || { echo "FAIL: the FQDN rule does not open the server's port"; cat /tmp/vkn-mm-fqdn-pol.out; exit 1; }
	@grep -q 'matchPattern: "\*"' /tmp/vkn-mm-fqdn-pol.out || { echo "FAIL: the FQDN arm renders without the DNS proxy rule that fills Cilium's FQDN cache — the name stays blocked"; cat /tmp/vkn-mm-fqdn-pol.out; exit 1; }
	@echo "ok: hostname endpoints, with the DNS proxy on"
	@echo "--> an IP-only backend list leaves the DNS rule plain: no L7 proxy on every actor lookup"
	@awk "/^  name: substrate-atenet-egress$$/,/^---/" /tmp/vkn-mm-two.out >/tmp/vkn-mm-two-dns.out
	@if grep -q 'matchPattern' /tmp/vkn-mm-two-dns.out; then echo "FAIL: the DNS proxy renders for IP-only host model servers"; cat /tmp/vkn-mm-two-dns.out; exit 1; fi
	@echo "ok: the DNS proxy renders only where a name needs it"
	@echo "--> kubernetes flavor: no egress gateway policy to extend, so no host-model rule either"
	@helm template t $(CONNECTIVITY_DIR) $(KAGENT_MM) --set networkPolicy.flavor=kubernetes >/tmp/vkn-mm-k8s.out 2>&1 || { cat /tmp/vkn-mm-k8s.out; exit 1; }
	@if grep -q 'host model server the actors dial' /tmp/vkn-mm-k8s.out; then echo "FAIL: the kubernetes flavor renders a host-model rule it has no egress gateway policy for"; exit 1; else echo "ok: kubernetes flavor has no host-model rule"; fi
	@echo "--> kubernetes flavor: renders, and has no kagent egress policy to extend"
	@helm template t $(CONNECTIVITY_DIR) $(KAGENT_NETPOL) --set networkPolicy.flavor=kubernetes --set kagent.kagent-tools.enabled=true >/tmp/vkn-k8s.out 2>&1 || { cat /tmp/vkn-k8s.out; exit 1; }
	@if grep -q 'kagent-tools' /tmp/vkn-k8s.out; then echo "FAIL: kubernetes flavor renders a tool-server rule it has no egress policy for"; exit 1; else echo "ok: kubernetes flavor untouched"; fi
	@echo "--> oauth2-proxy ingress: only the Gateway's Envoy pods by default; kagent.oauth2ProxyIngress.additionalPeers adds callers on the proxy port"
	@helm template t $(CONNECTIVITY_DIR) $(KAGENT_NETPOL) --set 'kagent.oauth2-proxy.enabled=true' >/tmp/vkn-o2p-off.out 2>&1 || { cat /tmp/vkn-o2p-off.out; exit 1; }
	@awk "/^  name: agent-platform-connectivity-oauth2-proxy-ingress$$/,/^---/" /tmp/vkn-o2p-off.out >/tmp/vkn-o2p-off-pol.out
	@[ "$$(grep -c 'fromEndpoints:' /tmp/vkn-o2p-off-pol.out)" = "1" ] || { echo "FAIL: oauth2-proxy ingress admits more than the Envoy pods by default"; cat /tmp/vkn-o2p-off-pol.out; exit 1; }
	@helm template t $(CONNECTIVITY_DIR) $(KAGENT_NETPOL) --set 'kagent.oauth2-proxy.enabled=true' --set-json 'kagent.oauth2ProxyIngress.additionalPeers=[{"app":"teleport-kube-agent","io.kubernetes.pod.namespace":"kube-system"}]' >/tmp/vkn-o2p-on.out 2>&1 || { cat /tmp/vkn-o2p-on.out; exit 1; }
	@awk "/^  name: agent-platform-connectivity-oauth2-proxy-ingress$$/,/^---/" /tmp/vkn-o2p-on.out >/tmp/vkn-o2p-on-pol.out
	@[ "$$(grep -c 'fromEndpoints:' /tmp/vkn-o2p-on-pol.out)" = "2" ] || { echo "FAIL: additionalPeers did not add a peer to the oauth2-proxy ingress"; cat /tmp/vkn-o2p-on-pol.out; exit 1; }
	@grep -A1 'app: teleport-kube-agent' /tmp/vkn-o2p-on-pol.out | grep -q 'io.kubernetes.pod.namespace: kube-system' || { echo "FAIL: the extra peer's labels are not rendered verbatim"; cat /tmp/vkn-o2p-on-pol.out; exit 1; }
	@grep -A5 'app: teleport-kube-agent' /tmp/vkn-o2p-on-pol.out | grep -q 'port: "4180"' || { echo "FAIL: the extra peer is not limited to the proxy port"; cat /tmp/vkn-o2p-on-pol.out; exit 1; }
	@if grep -q 'teleport-kube-agent' /tmp/vkn-o2p-off.out; then echo "FAIL: a peer renders without being configured"; exit 1; fi
	@if helm template t $(CONNECTIVITY_DIR) $(KAGENT_NETPOL) --set 'kagent.oauth2-proxy.enabled=true' --set-json 'kagent.oauth2ProxyIngress.additionalPeers=["teleport-kube-agent"]' >/tmp/vkn-o2p-bad.out 2>&1; then \
		echo "FAIL: a non-map oauth2-proxy peer was accepted"; exit 1; \
	elif ! grep -q 'additionalPeers: every item is a non-empty pod label map' /tmp/vkn-o2p-bad.out; then \
		echo "FAIL: the oauth2-proxy peer guard failed for the wrong reason"; cat /tmp/vkn-o2p-bad.out; exit 1; \
	else echo "ok: oauth2-proxy ingress peers"; fi
	@echo "--> oauth2-proxy off: no oauth2-proxy policy, peers ignored"
	@if helm template t $(CONNECTIVITY_DIR) $(KAGENT_NETPOL) --set-json 'kagent.oauth2ProxyIngress.additionalPeers=[{"app":"teleport-kube-agent"}]' 2>&1 | grep -q 'teleport-kube-agent'; then echo "FAIL: oauth2-proxy peers render while oauth2-proxy is off"; exit 1; else echo "ok: inert while oauth2-proxy is off"; fi

# The kagent controller route in its fleet shape: agentgateway-muster with the
# agentgateway and kagent components on, the route on its default hostname, the
# issuer from global.identity, jwksEgress open (the JWT policy is on by default).
KAGENT_ROUTE := $(VM) --namespace agent-platform --set ingress.mode=agentgateway-muster --set components.agentgateway.enabled=true --set components.kagent.enabled=true --set kagent.namespaceOverride=kagent --set kagent.controllerRoute.enabled=true --set global.domain=ci.example.com --set global.identity.issuerUrl=https://dex.ci.example.com --set gateway.jwksEgress.enabled=true
# $(call kagent_route_doc,<kind>,<name>,<render file>,<out file>): one rendered object.
define kagent_route_doc
	@python3 -c 'import sys; docs=open("$(3)").read().split("\n---\n"); hit=[d for d in docs if "\nkind: $(1)\n" in d and "\n  name: $(2)\n" in d]; sys.exit("FAIL: $(1) $(2) missing from the render") if len(hit)!=1 else open("$(4)","w").write(hit[0])'
endef

.PHONY: verify-kagent-route
verify-kagent-route: ## Assert the kagent controller route (4.0): a GRPCRoute matched by the kagent API v2 + A2A v1 services (one service-only match per service by default, one exact service/method match per listed RPC as the fallback) on both hops, the bearer passthrough without a protocol pin, the JWT policy on by default in Strict mode with the identity transformation (x-user-id from the verified claim) and the claim requirement, the UI route's identity-header strip, the controller's network policy admission (data plane + UI only), the off switch, the Envoy timeout policy on the public hop, and the guards.
	@echo "====> $@ ($(CONNECTIVITY_DIR))"
	@echo "--> the default shape: GRPCRoutes on both hops, no REST route, no path prefix"
	@helm template t $(CONNECTIVITY_DIR) $(KAGENT_ROUTE) --set ingress.backendTrafficPolicy.enabled=true >/tmp/vkr.out 2>&1 || { cat /tmp/vkr.out; exit 1; }
	$(call kagent_route_doc,GRPCRoute,kagent-controller,/tmp/vkr.out,/tmp/vkr-inner.out)
	$(call kagent_route_doc,GRPCRoute,kagent-controller-public,/tmp/vkr.out,/tmp/vkr-public.out)
	@if grep -A3 '^kind: HTTPRoute$$' /tmp/vkr.out | grep -q 'name: kagent-controller'; then echo "FAIL: a kagent-controller HTTPRoute still renders (the REST /kagent route was retired)"; exit 1; fi
	@if grep -qE 'value: /kagent$$|pathPrefix|replacePrefixMatch: /$$' /tmp/vkr.out; then echo "FAIL: the render still carries a /kagent path prefix"; grep -nE 'value: /kagent$$|pathPrefix' /tmp/vkr.out | head; exit 1; fi
	@for svc in kagent.api.v1alpha1.AgentInstanceService kagent.api.v1alpha1.AgentTemplateService kagent.api.v1alpha1.ModelService kagent.api.v1alpha1.SystemService lf.a2a.v1.A2AService; do \
		for f in /tmp/vkr-inner.out /tmp/vkr-public.out; do \
			grep -B1 "^            service: $$svc$$" $$f | grep -q 'type: Exact' || { echo "FAIL: $$f has no exact match for $$svc"; exit 1; }; \
		done; \
	done
	@for f in /tmp/vkr-inner.out /tmp/vkr-public.out; do \
		[ "$$(grep -c '^    - matches:' $$f)" = "5" ] || { echo "FAIL: $$f does not carry one rule per service (5)"; grep -c '^    - matches:' $$f; exit 1; }; \
		[ "$$(grep -c '^            service: ' $$f)" = "5" ] || { echo "FAIL: $$f does not match exactly the line's five services once each"; grep -c '^            service: ' $$f; exit 1; }; \
		if grep -q '^            method: ' $$f; then echo "FAIL: $$f lists methods by default — the default is one service-only match per service (agentgateway chart >= 2.1.1 translates it to the path prefix /<service>/)"; exit 1; fi; \
		grep -q '^            service: lf.a2a.v1.A2AService$$' $$f || { echo "FAIL: $$f does not route lf.a2a.v1.A2AService"; exit 1; }; \
	done
	@if grep -q '^  hostnames:' /tmp/vkr-inner.out; then echo "FAIL: the inner GRPCRoute is hostname-scoped (the in-cluster authority agentgateway.<ns>.svc.cluster.local:8080 would not match)"; exit 1; fi
	@grep -q 'kind: AgentgatewayBackend' /tmp/vkr-inner.out || { echo "FAIL: the inner GRPCRoute does not target the kagent AgentgatewayBackend"; exit 1; }
	@grep -q '"agentgateway.ci.example.com"' /tmp/vkr-public.out || { echo "FAIL: the public GRPCRoute does not derive its hostname from global.domain"; exit 1; }
	@grep -A2 'backendRefs:' /tmp/vkr-public.out | grep -q 'name: agentgateway' || { echo "FAIL: the public GRPCRoute does not forward to the agentgateway Service"; exit 1; }
	@grep -A2 'backendRefs:' /tmp/vkr-public.out | grep -q 'port: 8080' || { echo "FAIL: the public GRPCRoute does not forward to port 8080"; exit 1; }
	@echo "ok: GRPCRoutes"
	@echo "--> the controller backend: passthrough of the bearer, no protocol pin (h2c inferred for gRPC, HTTP/1.1 kept for gRPC-Web)"
	$(call kagent_route_doc,AgentgatewayBackend,kagent,/tmp/vkr.out,/tmp/vkr-backend.out)
	@grep -q 'passthrough: {}' /tmp/vkr-backend.out || { echo "FAIL: the kagent backend no longer passes the bearer through"; exit 1; }
	@if grep -qE '^ +version: HTTP' /tmp/vkr-backend.out; then echo "FAIL: the kagent backend pins a protocol version — pinned HTTP/2 sends gRPC-Web to the controller's native gRPC server (415); agentgateway infers h2c for gRPC and keeps HTTP/1.1 for gRPC-Web"; exit 1; fi
	@grep -q 'host: kagent-controller.kagent.svc.cluster.local' /tmp/vkr-backend.out || { echo "FAIL: the kagent backend does not target the controller Service in the kagent namespace"; exit 1; }
	@echo "ok: backend"
	@echo "--> the JWT policy: on by default, Strict, on the GRPCRoute, the identity transformation and the claim requirement"
	$(call kagent_route_doc,AgentgatewayPolicy,kagent-controller-jwt,/tmp/vkr.out,/tmp/vkr-jwt.out)
	$(call kagent_route_doc,AgentgatewayBackend,kagent-controller-jwks,/tmp/vkr.out,/tmp/vkr-jwks.out)
	@grep -A2 'targetRefs:' /tmp/vkr-jwt.out | grep -q 'kind: GRPCRoute' || { echo "FAIL: the JWT policy does not target the GRPCRoute"; exit 1; }
	@grep -A3 'targetRefs:' /tmp/vkr-jwt.out | grep -q 'name: kagent-controller$$' || { echo "FAIL: the JWT policy targets the wrong route"; exit 1; }
	@grep -q 'mode: Strict' /tmp/vkr-jwt.out || { echo "FAIL: the JWT policy is not Strict by default"; exit 1; }
	@grep -q 'issuer: "https://dex.ci.example.com"' /tmp/vkr-jwt.out || { echo "FAIL: the JWT issuer is not defaulted from global.identity.issuerUrl"; exit 1; }
	@grep -A4 '^        set:' /tmp/vkr-jwt.out | grep -q 'name: x-user-id' || { echo "FAIL: the transformation does not set x-user-id"; exit 1; }
	@grep -A4 '^        set:' /tmp/vkr-jwt.out | grep -q 'value: "jwt.email"' || { echo "FAIL: x-user-id is not set from the email claim (kagent.controller.auth.userIdClaim)"; exit 1; }
	@if grep -q 'remove:' /tmp/vkr-jwt.out; then echo "FAIL: the policy removes a header — agentgateway applies remove after set, which would strip the identity header it just set"; exit 1; fi
	@grep -q 'action: Require' /tmp/vkr-jwt.out || { echo "FAIL: the policy does not require the identity claim"; exit 1; }
	@grep -q -- '- "has(jwt.email)"' /tmp/vkr-jwt.out || { echo "FAIL: the claim requirement does not name the email claim"; exit 1; }
	@grep -q 'host: dex.giantswarm.svc.cluster.local' /tmp/vkr-jwks.out || { echo "FAIL: the JWKS backend does not default to the dex Service"; exit 1; }
	@if grep -q 'tls:' /tmp/vkr-jwks.out; then echo "FAIL: the JWKS backend originates TLS without jwks.tls.enabled"; exit 1; fi
	@echo "ok: JWT policy + identity transformation"
	@echo "--> kagent.controller.auth.userIdClaim drives both the header and the requirement; jwks.tls verifies against the named CA"
	@helm template t $(CONNECTIVITY_DIR) $(KAGENT_ROUTE) --set kagent.controller.auth.userIdClaim=sub --set kagent.controllerRoute.jwtAuthentication.jwks.tls.enabled=true --set kagent.controllerRoute.jwtAuthentication.jwks.tls.caSecretName=dex-ca >/tmp/vkr-sub.out 2>&1 || { cat /tmp/vkr-sub.out; exit 1; }
	@grep -q 'value: "jwt.sub"' /tmp/vkr-sub.out || { echo "FAIL: the transformation does not follow kagent.controller.auth.userIdClaim"; exit 1; }
	@grep -q -- '- "has(jwt.sub)"' /tmp/vkr-sub.out || { echo "FAIL: the claim requirement does not follow kagent.controller.auth.userIdClaim"; exit 1; }
	@grep -B1 -A3 'caCertificateRefs:' /tmp/vkr-sub.out | grep -q 'name: dex-ca' || { echo "FAIL: jwks.tls.caSecretName does not reach the JWKS backend"; exit 1; }
	@echo "ok: claim + JWKS TLS knobs"
	@echo "--> the public hop's Envoy timeout policy targets the public GRPCRoute; nothing of it in edge mode"
	$(call kagent_route_doc,BackendTrafficPolicy,kagent-controller-public,/tmp/vkr.out,/tmp/vkr-btp.out)
	@grep -A3 'targetRefs:' /tmp/vkr-btp.out | grep -q 'kind: GRPCRoute' || { echo "FAIL: the kagent BackendTrafficPolicy does not target the GRPCRoute"; exit 1; }
	@grep -q 'requestTimeout: "0s"' /tmp/vkr-btp.out || { echo "FAIL: the kagent BackendTrafficPolicy does not carry ingress.backendTrafficPolicy.timeout"; exit 1; }
	@helm template t $(CONNECTIVITY_DIR) $(KAGENT_ROUTE) >/tmp/vkr-nobtp.out 2>&1 || { cat /tmp/vkr-nobtp.out; exit 1; }
	@if grep -A3 '^kind: BackendTrafficPolicy$$' /tmp/vkr-nobtp.out | grep -q 'kagent-controller-public'; then echo "FAIL: the kagent BackendTrafficPolicy renders with ingress.backendTrafficPolicy off"; exit 1; fi
	@helm template t $(CONNECTIVITY_DIR) $(EDGE_VM) --set components.kagent.enabled=true --set kagent.controllerRoute.enabled=true --set gateway.jwksEgress.enabled=true --set global.identity.issuerUrl=https://dex.ci.example.com --set ingress.backendTrafficPolicy.enabled=true >/tmp/vkr-edge.out 2>&1 || { cat /tmp/vkr-edge.out; exit 1; }
	@if grep -q 'kagent-controller-public' /tmp/vkr-edge.out; then echo "FAIL: the public GRPCRoute or its BackendTrafficPolicy renders with the edge as data plane"; exit 1; fi
	@grep -A3 '^kind: AgentgatewayPolicy$$' /tmp/vkr-edge.out | grep -q 'name: kagent-controller-jwt' || { echo "FAIL: the JWT policy is missing in edge mode"; exit 1; }
	@echo "ok: public-hop timeout policy + edge mode"
	@echo "--> the off switch: no policy, no JWKS backend, the route stays"
	@helm template t $(CONNECTIVITY_DIR) $(KAGENT_ROUTE) --set kagent.controllerRoute.jwtAuthentication.enabled=false --set gateway.jwksEgress.enabled=false >/tmp/vkr-off.out 2>&1 || { cat /tmp/vkr-off.out; exit 1; }
	@if grep -qE 'kagent-controller-jwt|kagent-controller-jwks|name: x-user-id' /tmp/vkr-off.out; then echo "FAIL: JWT objects render with jwtAuthentication.enabled=false"; exit 1; fi
	@grep -A3 '^kind: GRPCRoute$$' /tmp/vkr-off.out | grep -q '^  name: kagent-controller$$' || { echo "FAIL: the GRPCRoute is gone with the JWT policy off"; exit 1; }
	@echo "ok: off switch"
	@echo "--> the UI route strips the identity header, with and without oauth2-proxy"
	@for proxy in true false; do \
		helm template t $(CONNECTIVITY_DIR) $(KAGENT_ROUTE) --set kagent.uiRoute.enabled=true --set kagent.oauth2-proxy.enabled=$$proxy >/tmp/vkr-ui-$$proxy.out 2>&1 || { cat /tmp/vkr-ui-$$proxy.out; exit 1; }; \
		python3 -c 'import sys; docs=open("/tmp/vkr-ui-'$$proxy'.out").read().split("\n---\n"); ui=[d for d in docs if "\nkind: HTTPRoute\n" in d and "\n  name: agent-platform-connectivity-ui\n" in d]; sys.exit("FAIL: the kagent UI HTTPRoute did not render") if len(ui)!=1 else None; r=ui[0]; sys.exit("FAIL: the UI route has no RequestHeaderModifier filter") if "type: RequestHeaderModifier" not in r else None; sys.exit("FAIL: the UI route does not remove x-user-id") if "remove:\n              - x-user-id" not in r else None; print("ok: UI route strips x-user-id (oauth2-proxy '$$proxy')")' || exit 1; \
	done
	@echo "--> network policy: the controller admits the data plane and the UI only, in both flavors"
	@helm template t $(CONNECTIVITY_DIR) $(KAGENT_ROUTE) --set networkPolicy.flavor=cilium --set muster.enabled=true >/tmp/vkr-np-cilium.out 2>&1 || { cat /tmp/vkr-np-cilium.out; exit 1; }
	$(call kagent_route_doc,CiliumNetworkPolicy,agent-platform-connectivity-kagent-from-agentgateway,/tmp/vkr-np-cilium.out,/tmp/vkr-np-cilium-ingress.out)
	@[ "$$(grep -c 'fromEndpoints:' /tmp/vkr-np-cilium-ingress.out)" = "1" ] || { echo "FAIL: the cilium controller ingress has more than one rule"; cat /tmp/vkr-np-cilium-ingress.out; exit 1; }
	@[ "$$(grep -c 'matchLabels:' /tmp/vkr-np-cilium-ingress.out)" = "3" ] || { echo "FAIL: the cilium controller ingress does not admit exactly the data plane and the UI (plus its own selector)"; cat /tmp/vkr-np-cilium-ingress.out; exit 1; }
	@grep -q 'gateway.networking.k8s.io/gateway-name: agentgateway' /tmp/vkr-np-cilium-ingress.out || { echo "FAIL: the cilium controller ingress does not admit the data plane"; exit 1; }
	@grep -q 'app.kubernetes.io/component: ui' /tmp/vkr-np-cilium-ingress.out || { echo "FAIL: the cilium controller ingress does not admit the UI"; exit 1; }
	@if grep -q '^            app: kagent$$' /tmp/vkr-np-cilium-ingress.out; then echo "FAIL: the cilium controller ingress still admits app: kagent pods"; exit 1; fi
	@helm template t $(CONNECTIVITY_DIR) $(KAGENT_ROUTE) --set networkPolicy.flavor=kubernetes >/tmp/vkr-np-k8s.out 2>&1 || { cat /tmp/vkr-np-k8s.out; exit 1; }
	$(call kagent_route_doc,NetworkPolicy,agent-platform-connectivity-kagent-controller-ingress,/tmp/vkr-np-k8s.out,/tmp/vkr-np-k8s-ingress.out)
	@grep -q 'gateway.networking.k8s.io/gateway-name: agentgateway' /tmp/vkr-np-k8s-ingress.out || { echo "FAIL: the kubernetes controller ingress admits the whole release namespace instead of the data-plane pods"; exit 1; }
	@grep -q 'app.kubernetes.io/component: ui' /tmp/vkr-np-k8s-ingress.out || { echo "FAIL: the kubernetes controller ingress does not admit the UI"; exit 1; }
	@if grep -q '^              app: kagent$$' /tmp/vkr-np-k8s-ingress.out; then echo "FAIL: the kubernetes controller ingress still admits app: kagent pods"; exit 1; fi
	@echo "ok: controller ingress in both flavors"
	@echo "--> the guards"
	$(call managers_must_fail,a stale pathPrefix fails the render,$(KAGENT_ROUTE) --set kagent.controllerRoute.pathPrefix=/kagent,pathPrefix was retired with 4.0)
	$(call managers_must_fail,the JWT policy needs jwksEgress,$(KAGENT_ROUTE) --set gateway.jwksEgress.enabled=false,gateway.jwksEgress.enabled is false)
	$(call managers_must_fail,the JWT policy needs an issuer,$(KAGENT_ROUTE) --set global.identity.issuerUrl=,needs the login issuer)
	$(call managers_must_fail,the identity claim is a plain claim name,$(KAGENT_ROUTE) --set kagent.controller.auth.userIdClaim=x-claim,is not a plain claim name)
	$(call managers_must_fail,an empty service map fails,$(KAGENT_ROUTE) --set kagent.controllerRoute.grpc.services=null,grpc.services is empty)
	@echo "--> a service with RPCs listed renders one exact service/method match per RPC (the shape an agentgateway chart < 2.1.1 needs); the other services stay service-only"
	@helm template t $(CONNECTIVITY_DIR) $(KAGENT_ROUTE) --set-json 'kagent.controllerRoute.grpc.services={"kagent.api.v1alpha1.SystemService":["GetVersion","GetCurrentUser"]}' >/tmp/vkr-extra.out 2>&1 || { cat /tmp/vkr-extra.out; exit 1; }
	@[ "$$(grep -c 'method: GetCurrentUser' /tmp/vkr-extra.out)" = "2" ] || { echo "FAIL: a listed RPC does not reach both GRPCRoutes"; exit 1; }
	@[ "$$(grep -c '^            service: kagent.api.v1alpha1.SystemService$$' /tmp/vkr-extra.out)" = "4" ] || { echo "FAIL: the listed service does not render one match per RPC on both routes"; grep -c '^            service: kagent.api.v1alpha1.SystemService$$' /tmp/vkr-extra.out; exit 1; }
	@[ "$$(grep -c '^            service: ' /tmp/vkr-extra.out)" = "12" ] || { echo "FAIL: the other services' service-only matches did not survive a one-service override"; grep -c '^            service: ' /tmp/vkr-extra.out; exit 1; }
	@[ "$$(grep -c '^            method: ' /tmp/vkr-extra.out)" = "4" ] || { echo "FAIL: methods rendered for a service without a list"; exit 1; }
	@echo "ok: the RPC list is the per-service fallback (the map merges, a list replaces)"
	@echo "--> every connectivity CI values file renders"
	@for f in $(CONNECTIVITY_DIR)/ci/*.yaml; do \
		helm template t $(CONNECTIVITY_DIR) --namespace agent-platform -f $$f >/tmp/vkr-ci.out 2>&1 || { echo "FAIL: $$f does not render"; cat /tmp/vkr-ci.out | tail -5; exit 1; }; \
	done
	@echo "ok: CI values"
	@echo "All kagent controller route behaviors verified."

.PHONY: verify-kagent-discovery
verify-kagent-discovery: ## Assert the platform renders no RemoteMCPServer for muster (the Generic agent chart 1.x renders one per agent, with the toolset header and the discovery opt-out label), nothing it renders for muster carries a static header, the operator-defined kagent.remoteMcpServers are kagent.dev/v1alpha3 in the kagent namespace (tokenSecret = a Secret-sourced Authorization header, no opt-out label).
	@echo "====> $@ ($(CONNECTIVITY_DIR))"
	@echo "--> kagent + muster on (OAuth on, the default) with operator extras: no RemoteMCPServer for muster; every RemoteMCPServer kagent.dev/v1alpha3 in the kagent namespace"
	@helm template t $(CONNECTIVITY_DIR) $(KAGENT_NETPOL) --set-json 'kagent.remoteMcpServers=[{"name":"external","url":"https://external.example/mcp","tokenSecret":"external-token"},{"name":"open","url":"http://open.tools.svc:8080/mcp"}]' >/tmp/vkd-on.out 2>&1 || { cat /tmp/vkd-on.out; exit 1; }
	@awk 'BEGIN{RS="\n---\n"} /\nkind: RemoteMCPServer\n/' /tmp/vkd-on.out >/tmp/vkd-on-rms.out
	@[ "$$(grep -c '^kind: RemoteMCPServer$$' /tmp/vkd-on.out)" = "2" ] || { echo "FAIL: expected the two operator RemoteMCPServers and nothing else, got $$(grep -c '^kind: RemoteMCPServer$$' /tmp/vkd-on.out)"; grep -n -A3 '^kind: RemoteMCPServer$$' /tmp/vkd-on.out; exit 1; }
	@if grep -qE '^  name: "?muster"?$$' /tmp/vkd-on-rms.out; then echo "FAIL: a RemoteMCPServer named muster is back — the Generic agent chart 1.x renders one per agent (docs/authentication.md, tool discovery)"; exit 1; fi
	@if grep -q 'svc.cluster.local:8090/mcp' /tmp/vkd-on-rms.out; then echo "FAIL: a RemoteMCPServer of the platform's own targets muster — the per-agent carrier is the agent chart's; a static header here would override the propagated caller token in every agent"; exit 1; fi
	@if grep -q 'allowedNamespaces' /tmp/vkd-on-rms.out; then echo "FAIL: allowedNamespaces is back on a RemoteMCPServer (the v1alpha2 cross-namespace grant; an AgentTemplate binds a same-namespace server only)"; exit 1; fi
	@[ "$$(grep -c '^apiVersion: kagent.dev/v1alpha3$$' /tmp/vkd-on-rms.out)" = "2" ] || { echo "FAIL: a RemoteMCPServer is not kagent.dev/v1alpha3"; grep -n apiVersion /tmp/vkd-on-rms.out; exit 1; }
	@[ "$$(grep -c '^  namespace: kagent$$' /tmp/vkd-on-rms.out)" = "2" ] || { echo "FAIL: a RemoteMCPServer is not in the kagent namespace, where the AgentTemplates that bind it live"; exit 1; }
	@if grep -q 'kagent.dev/v1alpha2' /tmp/vkd-on.out; then echo "FAIL: a kagent.dev/v1alpha2 object renders"; grep -n 'v1alpha2' /tmp/vkd-on.out; exit 1; fi
	@if grep -q 'kagent.dev/discovery' /tmp/vkd-on-rms.out; then echo "FAIL: the discovery opt-out label leaked onto an operator-defined RemoteMCPServer (the Generic agent chart sets it on the per-agent carrier)"; exit 1; fi
	@echo "ok: no muster server, v1alpha3 in the kagent namespace, no allowedNamespaces, no v1alpha2, no opt-out label on the extras"
	@echo "--> tokenSecret renders a Secret-sourced Authorization header in the v1alpha3 shape; without it no headersFrom"
	@awk 'BEGIN{RS="\n---\n"} /\nkind: RemoteMCPServer\n/ && /\n  name: "external"\n/' /tmp/vkd-on.out >/tmp/vkd-on-external.out
	@grep -q '^  name: "external"$$' /tmp/vkd-on-external.out || { echo "FAIL: the operator-defined RemoteMCPServer external did not render"; exit 1; }
	@for pattern in '^  headersFrom:$$' '^  - name: Authorization$$' '^    valueFrom:$$' '^      type: Secret$$' '^      name: "external-token"$$' '^      key: token$$'; do \
		grep -q -- "$$pattern" /tmp/vkd-on-external.out || { echo "FAIL: tokenSecret no longer renders headersFrom {name: Authorization, valueFrom: {type: Secret, name, key: token}} — missing $$pattern"; cat /tmp/vkd-on-external.out; exit 1; }; \
	done
	@if grep -q '^    value:' /tmp/vkd-on-external.out; then echo "FAIL: the Authorization header carries an inline value next to valueFrom (the CRD takes exactly one)"; exit 1; fi
	@awk 'BEGIN{RS="\n---\n"} /\nkind: RemoteMCPServer\n/ && /\n  name: "open"\n/' /tmp/vkd-on.out >/tmp/vkd-on-open.out
	@grep -q '^  name: "open"$$' /tmp/vkd-on-open.out || { echo "FAIL: the operator-defined RemoteMCPServer open did not render"; exit 1; }
	@if grep -q 'headersFrom' /tmp/vkd-on-open.out; then echo "FAIL: an operator-defined RemoteMCPServer without tokenSecret carries headersFrom"; cat /tmp/vkd-on-open.out; exit 1; fi
	@grep -q '^  description: "open MCP server"$$' /tmp/vkd-on-open.out || { echo "FAIL: the default description (required by the v1alpha3 CRD) is gone"; exit 1; }
	@echo "ok: operator extras"
	@echo "--> muster OAuth off, no extras: still no RemoteMCPServer of the platform's own"
	@helm template t $(CONNECTIVITY_DIR) $(KAGENT_NETPOL) --set muster.muster.oauth.server.enabled=false >/tmp/vkd-off.out 2>&1 || { cat /tmp/vkd-off.out; exit 1; }
	@if grep -q '^kind: RemoteMCPServer$$' /tmp/vkd-off.out; then echo "FAIL: a RemoteMCPServer renders with no kagent.remoteMcpServers (the shared muster server is retired)"; exit 1; else echo "ok: nothing without extras"; fi
	@echo "--> kagent off: no RemoteMCPServer at all, extras included"
	@if helm template t $(CONNECTIVITY_DIR) $(VM) --set-json 'kagent.remoteMcpServers=[{"name":"external","url":"https://external.example/mcp"}]' 2>&1 | grep -q 'kind: RemoteMCPServer'; then echo "FAIL: a RemoteMCPServer renders while kagent is off"; exit 1; else echo "ok: inert while kagent is off"; fi
	@echo "kagent tool-discovery invariants verified."

.PHONY: verify-kagent-crds
verify-kagent-crds: ## Assert every kagent.dev object the connectivity chart renders (the ModelConfig / RemoteMCPServer catalog, the Harness) validates against the kagent line's CRDs at the pinned release — kagent.dev/v1alpha3, every field known to the CRD, the CEL rules the shapes can trip — that a ModelConfig of every provider in the CRD's enum renders (its baseUrl under the block the CRD gives one to, refused where it gives none, an unknown provider refused naming the enum), and no render of the chart carries kagent.dev/v1alpha2 (tests/verify-kagent-crds.py; needs PyYAML).
	@echo "====> $@ ($(CONNECTIVITY_DIR))"
	@python3 -c 'import yaml' 2>/dev/null || { echo "FAIL: PyYAML is not installed (apt: python3-yaml, pip: pyyaml)"; exit 1; }
	@python3 tests/verify-kagent-crds.py $(CONNECTIVITY_DIR)
	@echo "ok: $@"

.PHONY: verify-kagent-harness
verify-kagent-harness: ## Assert the platform Harness is the kagent chart's since 4.8.0: the connectivity chart renders none, the meta chart forwards the GS policy only (create, snapshot location, KAGENT_PROPAGATE_TOKEN, the admission label; no image by default, an override digest when set).
	@echo "====> $@ ($(CONNECTIVITY_DIR))"
	@python3 -c 'import yaml' 2>/dev/null || { echo "FAIL: PyYAML is not installed (apt: python3-yaml, pip: pyyaml)"; exit 1; }
	@python3 tests/verify-kagent-harness.py $(CONNECTIVITY_DIR) $(CHART_DIR)
	@echo "ok: $@"

.PHONY: verify-workerpool
verify-workerpool: ## Assert the Substrate WorkerPool reaches the cluster as written and is guarded (giantswarm/agent-platform#457, #472): the meta chart forwards kagent.substrateWorkerPool.template to the kagent release verbatim — the architecture alone by default, an installation's vendor + CPU generation pin as set, every nodeSelector value a string, the karpenter.sh/do-not-disrupt annotation and the karpenter.sh/capacity-type selector as set — and the kagent chart the range resolves to renders it unchanged into the one WorkerPool's spec.template; a nodeSelector value that is not a string, a topologySpreadConstraints / podAntiAffinity value while the pinned Substrate range's floor is below the release that carries the fields (agent-platform.substrate.workerPoolSpreadFloor: 1.0.0) and any other key WorkerPool.spec.template does not have fail the render naming the key; the worker PodDisruptionBudget (kagent.substrateWorkerPool.podDisruptionBudget) renders from the connectivity chart of the working tree in the kagent namespace with the pool's ate.dev/worker-pool selector and maxUnavailable: 1, is gone with enabled: false and never reaches the kagent release. Network: gsoci.azurecr.io; needs PyYAML.
	@echo "====> $@ ($(CHART_DIR))"
	@python3 -c 'import yaml' 2>/dev/null || { echo "FAIL: PyYAML is not installed (apt: python3-yaml, pip: pyyaml)"; exit 1; }
	@python3 tests/verify-workerpool.py $(CHART_DIR)
	@echo "ok: $@"

.PHONY: verify-worker-image
verify-worker-image: ## Assert the Substrate worker image follows the chart's own Substrate pin (giantswarm/agent-platform#466): the kagent release carries substrateWorkerPool.workerImage = <substrate.image.registry>/ateom-gvisor:<floor of components.substrate.versionRange> — derived over the forwarded block, never the kagent build's stamp — so the worker and the atelet are one Substrate release whatever kagent build the kagent range admits (the 4.15.2 shape — the Substrate range a minor behind — forwards that minor's worker); a mirror's substrate.image.registry moves it; an own workerImage stands while its tag is the pinned release and fails the render otherwise; an exact pin derives that version; a Substrate range that does not confine one runtime contract (no floor, a ceiling past the next minor or at the next patch, the former -gs.N shape, ~, ^, <=) fails the render naming the range; the kagent chart the range resolves to renders the one WorkerPool with the derived image and was published against the pinned release's X.Y.Z (its Chart.yaml substrate dependency). Network: gsoci.azurecr.io; needs PyYAML.
	@echo "====> $@ ($(CHART_DIR))"
	@python3 -c 'import yaml' 2>/dev/null || { echo "FAIL: PyYAML is not installed (apt: python3-yaml, pip: pyyaml)"; exit 1; }
	@python3 tests/verify-worker-image.py $(CHART_DIR)
	@echo "ok: $@"

.PHONY: verify-managers
verify-managers: ## Assert the model-manager / agent-manager wiring (routes, JWT policies, network policies in both flavors) and its guards.
	@echo "====> $@ ($(CONNECTIVITY_DIR))"
	@echo "--> both components off render nothing of theirs (agent-manager is off by default; model-manager is on since giantswarm/agent-platform#329)"
	@helm template t $(CONNECTIVITY_DIR) $(VM) --set components.kagent.enabled=true --set components.model-manager.enabled=false >/tmp/vmg-off.out 2>&1 || { cat /tmp/vmg-off.out; exit 1; }
	@if grep -vE '^\s+"' /tmp/vmg-off.out | grep -qE 'model-manager|agent-manager'; then echo "FAIL: model-manager / agent-manager objects render while the components are off (the boards' JSON payloads, which name the managers in prose, are not objects and are skipped)"; grep -vE '^\s+"' /tmp/vmg-off.out | grep -nE 'model-manager|agent-manager' | head; exit 1; else echo "ok: inert while off"; fi
	@echo "--> the default (giantswarm/agent-platform#329): model-manager on with no backend — its policies render without a model-server or Hub egress, no endpoint is required"
	@helm template t $(CONNECTIVITY_DIR) $(MANAGERS_MIN) >/tmp/vmg-default.out 2>&1 || { cat /tmp/vmg-default.out; exit 1; }
	@for n in model-manager-ingress model-manager-egress muster-to-model-manager; do \
		grep -A3 '^kind: CiliumNetworkPolicy$$' /tmp/vmg-default.out | grep -q "^  name: agent-platform-connectivity-$$n$$" || { echo "FAIL: CiliumNetworkPolicy agent-platform-connectivity-$$n missing from the default render"; exit 1; }; \
	done
	@awk '/^  name: agent-platform-connectivity-model-manager-egress$$/,/^---/' /tmp/vmg-default.out >/tmp/vmg-default-egress.out
	@if grep -qE 'huggingface.co|/32' /tmp/vmg-default-egress.out; then echo "FAIL: the default model-manager egress opens a model server or the Hub without a static backend"; cat /tmp/vmg-default-egress.out; exit 1; fi
	@grep -q 'matchName: dex.ci.example.com' /tmp/vmg-default-egress.out || { echo "FAIL: the default model-manager egress lost the identity provider"; exit 1; }
	@helm template t $(CONNECTIVITY_DIR) $(MANAGERS_MIN) --set networkPolicy.flavor=kubernetes >/tmp/vmg-default-k8s.out 2>&1 || { cat /tmp/vmg-default-k8s.out; exit 1; }
	@grep -A3 '^kind: NetworkPolicy$$' /tmp/vmg-default-k8s.out | grep -q '^  name: agent-platform-connectivity-model-manager-egress$$' || { echo "FAIL: kubernetes flavor: the default model-manager egress policy is missing"; exit 1; }
	@if grep -q 'cidr: 10.0.0.1/32' /tmp/vmg-default-k8s.out; then echo "FAIL: kubernetes flavor: a model-server address renders without a static backend"; exit 1; fi
	@echo "ok: default shape, zero backends"
	@echo "--> the one-backend form and the one-element backends list render alike (the old default, backend: ollama, is now a static choice)"
	@helm template t $(CONNECTIVITY_DIR) $(MANAGERS_ON) >/tmp/vmg-one-form.out 2>&1 || { cat /tmp/vmg-one-form.out; exit 1; }
	@helm template t $(CONNECTIVITY_DIR) $(MANAGERS_ON) --set model-manager.backend= --set 'model-manager.backends[0]=ollama' >/tmp/vmg-list-form.out 2>&1 || { cat /tmp/vmg-list-form.out; exit 1; }
	@grep -q '10.0.0.1/32' /tmp/vmg-one-form.out || { echo "FAIL: backend: ollama does not open the Ollama endpoint"; exit 1; }
	@grep -q '10.0.0.1/32' /tmp/vmg-list-form.out || { echo "FAIL: backends: [ollama] does not open the Ollama endpoint"; exit 1; }
	@echo "ok: static forms"
	@echo "--> cilium: routes, JWT policies and network policies of both components"
	@helm template t $(CONNECTIVITY_DIR) $(MANAGERS_ON) $(MANAGERS_ROUTES) >/tmp/vmg-cilium.out 2>&1 || { cat /tmp/vmg-cilium.out; exit 1; }
	@for name in model-manager agent-manager; do \
		for obj in "AgentgatewayBackend $$name" "AgentgatewayBackend $$name-jwks" "HTTPRoute $$name" "HTTPRoute $$name-public" "AgentgatewayPolicy $$name-jwt" \
			"CiliumNetworkPolicy agent-platform-connectivity-$$name-ingress" "CiliumNetworkPolicy agent-platform-connectivity-$$name-egress" \
			"CiliumNetworkPolicy agent-platform-connectivity-dataplane-to-$$name" "CiliumNetworkPolicy agent-platform-connectivity-muster-to-$$name"; do \
			kind=$${obj% *}; n=$${obj#* }; \
			grep -A3 "^kind: $$kind$$" /tmp/vmg-cilium.out | grep -q "^  name: $$n$$" || { echo "FAIL: $$kind $$n missing from the cilium render"; exit 1; }; \
		done; \
	done
	@echo "ok: all objects present"
	@grep -q 'replacePrefixMatch: /' /tmp/vmg-cilium.out || { echo "FAIL: the inner route does not strip the path prefix"; exit 1; }
	@grep -q 'value: /model-manager' /tmp/vmg-cilium.out || { echo "FAIL: model-manager path prefix missing"; exit 1; }
	@grep -q 'value: /agent-manager' /tmp/vmg-cilium.out || { echo "FAIL: agent-manager path prefix missing"; exit 1; }
	@grep -q 'host: model-manager.agent-platform.svc.cluster.local' /tmp/vmg-cilium.out || { echo "FAIL: the AgentgatewayBackend does not target the pinned model-manager Service"; exit 1; }
	@grep -q 'host: agent-manager.agent-platform.svc.cluster.local' /tmp/vmg-cilium.out || { echo "FAIL: the AgentgatewayBackend does not target the pinned agent-manager Service"; exit 1; }
	@[ "$$(grep -c 'issuer: "https://dex.ci.example.com"' /tmp/vmg-cilium.out)" = "2" ] || { echo "FAIL: the JWT policies do not default their issuer from global.identity.issuerUrl"; exit 1; }
	@[ "$$(grep -c '"agentgateway.ci.example.com"' /tmp/vmg-cilium.out)" = "2" ] || { echo "FAIL: the public routes do not derive their hostname from global.domain"; exit 1; }
	@echo "ok: routes + JWT policies"
	@grep -q 'matchName: dex.ci.example.com' /tmp/vmg-cilium.out || { echo "FAIL: no FQDN egress to the identity provider"; exit 1; }
	@grep -q 'matchName: gsoci.azurecr.io' /tmp/vmg-cilium.out || { echo "FAIL: agent-manager egress does not name the agent chart registry"; exit 1; }
	@grep -qE "matchPattern: ['\"]\*\.blob\.core\.windows\.net['\"]" /tmp/vmg-cilium.out || { echo "FAIL: agent-manager egress lost the registry blob front"; exit 1; }
	@grep -q '10.0.0.1/32' /tmp/vmg-cilium.out || { echo "FAIL: model-manager egress does not pin the Ollama endpoint address"; exit 1; }
	@grep -B2 -A2 'matchPattern: "\*"' /tmp/vmg-cilium.out | grep -q 'dns:' || { echo "FAIL: the FQDN policies carry no DNS proxy rule"; exit 1; }
	@grep -q '\- remote-node' /tmp/vmg-cilium.out || { echo "FAIL: the ingress policies do not admit the kubelet probes"; exit 1; }
	@if grep -q 'huggingface.co' /tmp/vmg-cilium.out; then echo "FAIL: Hugging Face egress rendered for the ollama backend"; exit 1; fi
	@if grep -q 'matchName: .*google' /tmp/vmg-cilium.out; then echo "FAIL: Google endpoints rendered for the dex provider"; exit 1; fi
	@echo "ok: cilium egress"
	@echo "--> cilium, google provider: the IdP egress names Google's discovery, JWKS/userinfo and token hosts, not a Dex issuer"
	@helm template t $(CONNECTIVITY_DIR) $(MANAGERS_ON) --set model-manager.oauth.provider=google --set agent-manager.oauth.provider=google >/tmp/vmg-google.out 2>&1 || { cat /tmp/vmg-google.out; exit 1; }
	@for n in model-manager agent-manager; do \
		awk "/^  name: agent-platform-connectivity-$$n-egress$$/,/^---/" /tmp/vmg-google.out >/tmp/vmg-google-$$n.out; \
		for h in accounts.google.com www.googleapis.com oauth2.googleapis.com; do \
			grep -q "matchName: $$h$$" /tmp/vmg-google-$$n.out || { echo "FAIL: $$n egress lacks $$h for the google provider"; exit 1; }; \
		done; \
		if grep -q 'matchName: dex.ci.example.com' /tmp/vmg-google-$$n.out; then echo "FAIL: $$n egress names the Dex issuer for the google provider"; exit 1; fi; \
		grep -q '\- cluster' /tmp/vmg-google-$$n.out || { echo "FAIL: $$n egress lost the cluster entity for the google provider"; exit 1; }; \
	done
	@echo "ok: google IdP egress"
	@echo "--> modelManager.networkPolicy.egress: names and blocks on 443 (cilium), blocks (kubernetes), whatever the backend; the chart-wide additional egress renders for the ollama backend too"
	@helm template t $(CONNECTIVITY_DIR) $(MANAGERS_ON) --set 'modelManager.networkPolicy.egress.fqdns[0].matchName=idp.example.internal' --set 'modelManager.networkPolicy.egress.fqdns[1].matchPattern=*.mirror.example.internal' --set 'modelManager.networkPolicy.egress.cidrs[0]=198.51.100.0/24' --set 'networkPolicy.additionalEgressFQDNs[0].matchName=extra.example.internal' --set 'networkPolicy.additionalEgressCIDRs[0]=203.0.113.0/24' >/tmp/vmg-mm-egress.out 2>&1 || { cat /tmp/vmg-mm-egress.out; exit 1; }
	@awk '/^  name: agent-platform-connectivity-model-manager-egress$$/,/^---/' /tmp/vmg-mm-egress.out >/tmp/vmg-mm-egress-policy.out
	@for pattern in 'matchName: idp.example.internal' "matchPattern: '\*.mirror.example.internal'" '\- 198.51.100.0/24' 'matchName: extra.example.internal' '\- 203.0.113.0/24' '\- 10.0.0.1/32'; do \
		grep -q -e "$$pattern" /tmp/vmg-mm-egress-policy.out || { echo "FAIL: cilium model-manager egress lacks $$pattern"; exit 1; }; \
	done
	@helm template t $(CONNECTIVITY_DIR) $(MANAGERS_ON) --set networkPolicy.flavor=kubernetes --set 'modelManager.networkPolicy.egress.cidrs[0]=198.51.100.0/24' --set 'networkPolicy.additionalEgressCIDRs[0]=203.0.113.0/24' >/tmp/vmg-mm-egress-k8s.out 2>&1 || { cat /tmp/vmg-mm-egress-k8s.out; exit 1; }
	@awk '/^  name: agent-platform-connectivity-model-manager-egress$$/,/^---/' /tmp/vmg-mm-egress-k8s.out >/tmp/vmg-mm-egress-k8s-policy.out
	@for pattern in 'cidr: "198.51.100.0/24"' 'cidr: "203.0.113.0/24"'; do \
		grep -q -e "$$pattern" /tmp/vmg-mm-egress-k8s-policy.out || { echo "FAIL: kubernetes model-manager egress lacks $$pattern"; exit 1; }; \
	done
	@echo "ok: model-manager egress knob"
	@echo "--> modelManager.networkPolicy.registeredBackends (giantswarm/agent-platform#478): a backend registered at runtime is opened by its block or name on its port, in both flavors, next to the static rules; empty, nothing renders"
	@if grep -q 'registered at runtime' /tmp/vmg-default-egress.out; then echo "FAIL: the default model-manager egress carries a registered-backend rule with the list empty"; exit 1; fi
	@helm template t $(CONNECTIVITY_DIR) $(MANAGERS_ON) $(REGISTERED_BACKENDS) >/tmp/vmg-registered.out 2>&1 || { cat /tmp/vmg-registered.out; exit 1; }
	@awk '/^  name: agent-platform-connectivity-model-manager-egress$$/,/^---/' /tmp/vmg-registered.out >/tmp/vmg-registered-policy.out
	@for pattern in '- 192.0.2.0/24' 'port: "11434"' 'matchName: ollama.models.svc.cluster.local' 'port: "1234"' '- 10.0.0.1/32'; do \
		grep -q -e "$$pattern" /tmp/vmg-registered-policy.out || { echo "FAIL: cilium model-manager egress lacks $$pattern"; cat /tmp/vmg-registered-policy.out; exit 1; }; \
	done
	@sed -n '/matchName: ollama.models.svc.cluster.local/,$$p' /tmp/vmg-registered-policy.out | grep -q '^        - cluster$$' || { echo "FAIL: the fqdn entry does not open the cluster entity on its port (an in-cluster Service)"; exit 1; }
	@[ "$$(grep -c 'registered at runtime' /tmp/vmg-registered-policy.out)" = "2" ] || { echo "FAIL: expected one registered-backend rule per entry (2)"; exit 1; }
	@[ "$$(awk '/^  name: agent-platform-connectivity-agent-manager-egress$$/,/^---/' /tmp/vmg-registered.out | grep -c 'registered at runtime')" = "0" ] || { echo "FAIL: the registered-backend rules leaked into agent-manager's egress"; exit 1; }
	@helm template t $(CONNECTIVITY_DIR) $(MANAGERS_ON) --set networkPolicy.flavor=kubernetes $(REGISTERED_BACKENDS_CIDR) >/tmp/vmg-registered-k8s.out 2>&1 || { cat /tmp/vmg-registered-k8s.out; exit 1; }
	@awk '/^  name: agent-platform-connectivity-model-manager-egress$$/,/^---/' /tmp/vmg-registered-k8s.out >/tmp/vmg-registered-k8s-policy.out
	@for pattern in 'cidr: "192.0.2.0/24"' 'port: 11434' 'cidr: 10.0.0.1/32'; do \
		grep -q -e "$$pattern" /tmp/vmg-registered-k8s-policy.out || { echo "FAIL: kubernetes model-manager egress lacks $$pattern"; cat /tmp/vmg-registered-k8s-policy.out; exit 1; }; \
	done
	@echo "ok: registered backends in both flavors"
	$(call managers_must_fail,registered backend: cidr must parse,$(MANAGERS_ON) --set 'modelManager.networkPolicy.registeredBackends[0].cidr=10.244.0.0' --set 'modelManager.networkPolicy.registeredBackends[0].port=11434',is not an IPv4 CIDR)
	$(call managers_must_fail,registered backend: port required,$(MANAGERS_ON) --set 'modelManager.networkPolicy.registeredBackends[0].cidr=10.244.0.0/16',has no port)
	$(call managers_must_fail,registered backend: port is a number in range,$(MANAGERS_ON) --set 'modelManager.networkPolicy.registeredBackends[0].cidr=10.244.0.0/16' --set 'modelManager.networkPolicy.registeredBackends[0].port=70000',is not a TCP port)
	$(call managers_must_fail,registered backend: fqdn refused under the kubernetes flavor,$(MANAGERS_ON) --set networkPolicy.flavor=kubernetes --set 'modelManager.networkPolicy.registeredBackends[0].fqdn=ollama.models.svc.cluster.local' --set 'modelManager.networkPolicy.registeredBackends[0].port=11434',needs the cilium network-policy flavor)
	$(call managers_must_fail,registered backend: one destination per entry,$(MANAGERS_ON) --set 'modelManager.networkPolicy.registeredBackends[0].cidr=10.244.0.0/16' --set 'modelManager.networkPolicy.registeredBackends[0].fqdn=ollama.lan' --set 'modelManager.networkPolicy.registeredBackends[0].port=11434',sets both cidr)
	$(call managers_must_fail,registered backend: a destination is required,$(MANAGERS_ON) --set 'modelManager.networkPolicy.registeredBackends[0].port=11434',names no destination)
	$(call managers_must_fail,registered backend: fqdn is a hostname,$(MANAGERS_ON) --set 'modelManager.networkPolicy.registeredBackends[0].fqdn=http://ollama:11434' --set 'modelManager.networkPolicy.registeredBackends[0].port=11434',is not a hostname)
	$(call managers_must_pass,registered backend: fqdn under the kubernetes flavor with policies off renders,$(MANAGERS_ON) --set networkPolicy.flavor=kubernetes --set networkPolicy.enabled=false --set 'modelManager.networkPolicy.registeredBackends[0].fqdn=ollama.models.svc.cluster.local' --set 'modelManager.networkPolicy.registeredBackends[0].port=11434')
	@echo "--> cilium, kserve backend: Hugging Face egress instead of the Ollama endpoint"
	@helm template t $(CONNECTIVITY_DIR) $(MANAGERS_ON) --set model-manager.backend=kserve --set modelManager.kserve.requireApi=false >/tmp/vmg-kserve.out 2>&1 || { cat /tmp/vmg-kserve.out; exit 1; }
	@grep -q 'matchName: huggingface.co' /tmp/vmg-kserve.out || { echo "FAIL: no Hugging Face egress for the kserve backend"; exit 1; }
	@if grep -q '10.0.0.1/32' /tmp/vmg-kserve.out; then echo "FAIL: Ollama egress rendered for the kserve backend"; exit 1; fi
	@echo "ok: kserve egress"
	@echo "--> lemonade backend: egress to the host Lemonade Server instead of Ollama, in both flavors"
	@helm template t $(CONNECTIVITY_DIR) $(MANAGERS_ON) --set model-manager.backend=lemonade --set model-manager.lemonade.endpoint=http://10.0.0.2:13305 >/tmp/vmg-lemonade.out 2>&1 || { cat /tmp/vmg-lemonade.out; exit 1; }
	@grep -q '10.0.0.2/32' /tmp/vmg-lemonade.out || { echo "FAIL: model-manager egress does not pin the Lemonade endpoint address"; exit 1; }
	@grep -q 'port: "13305"' /tmp/vmg-lemonade.out || { echo "FAIL: model-manager egress does not open the Lemonade port"; exit 1; }
	@if grep -q '10.0.0.1/32' /tmp/vmg-lemonade.out; then echo "FAIL: Ollama egress rendered for the lemonade backend"; exit 1; fi
	@if grep -q 'huggingface.co' /tmp/vmg-lemonade.out; then echo "FAIL: Hugging Face egress rendered for the lemonade backend"; exit 1; fi
	@helm template t $(CONNECTIVITY_DIR) $(MANAGERS_ON) --set networkPolicy.flavor=kubernetes --set model-manager.backend=lemonade --set model-manager.lemonade.endpoint=http://10.0.0.2:13305 >/tmp/vmg-lemonade-k8s.out 2>&1 || { cat /tmp/vmg-lemonade-k8s.out; exit 1; }
	@grep -q 'cidr: 10.0.0.2/32' /tmp/vmg-lemonade-k8s.out || { echo "FAIL: kubernetes model-manager egress does not pin the Lemonade endpoint address"; exit 1; }
	@grep -q 'port: 13305' /tmp/vmg-lemonade-k8s.out || { echo "FAIL: kubernetes model-manager egress does not open the Lemonade port"; exit 1; }
	@helm template t $(CONNECTIVITY_DIR) $(MANAGERS_ON) --set model-manager.backend=lemonade --set model-manager.lemonade.endpoint=http://lemonade.lan:13305 >/tmp/vmg-lemonade-fqdn.out 2>&1 || { cat /tmp/vmg-lemonade-fqdn.out; exit 1; }
	@grep -q 'matchName: lemonade.lan' /tmp/vmg-lemonade-fqdn.out || { echo "FAIL: a hostname Lemonade endpoint is not opened by name"; exit 1; }
	@echo "ok: lemonade egress"
	@echo "--> lmstudio backend: egress to the host LM Studio, its own flags, and the guard on a missing endpoint"
	@helm template t $(CONNECTIVITY_DIR) $(MANAGERS_ON) --set model-manager.backend=lmstudio --set model-manager.lmstudio.endpoint=http://10.0.0.3:1234 >/tmp/vmg-lmstudio.out 2>&1 || { cat /tmp/vmg-lmstudio.out; exit 1; }
	@grep -q '10.0.0.3/32' /tmp/vmg-lmstudio.out || { echo "FAIL: model-manager egress does not pin the LM Studio endpoint address"; exit 1; }
	@grep -q 'port: "1234"' /tmp/vmg-lmstudio.out || { echo "FAIL: model-manager egress does not open the LM Studio port"; exit 1; }
	@if grep -q '10.0.0.1/32' /tmp/vmg-lmstudio.out; then echo "FAIL: Ollama egress rendered for the lmstudio backend"; exit 1; fi
	@helm template t $(CONNECTIVITY_DIR) $(MANAGERS_ON) --set networkPolicy.flavor=kubernetes --set model-manager.backend=lmstudio --set model-manager.lmstudio.endpoint=http://10.0.0.3:1234 >/tmp/vmg-lmstudio-k8s.out 2>&1 || { cat /tmp/vmg-lmstudio-k8s.out; exit 1; }
	@grep -q 'cidr: 10.0.0.3/32' /tmp/vmg-lmstudio-k8s.out || { echo "FAIL: kubernetes model-manager egress does not pin the LM Studio endpoint address"; exit 1; }
	@grep -q 'port: 1234' /tmp/vmg-lmstudio-k8s.out || { echo "FAIL: kubernetes model-manager egress does not open the LM Studio port"; exit 1; }
	@if grep -q 'huggingface.co' /tmp/vmg-lmstudio.out; then echo "FAIL: Hugging Face egress rendered for the lmstudio backend"; exit 1; fi
	@helm template t $(CONNECTIVITY_DIR) $(MANAGERS_ON) --set model-manager.backend=lmstudio --set model-manager.lmstudio.endpoint=http://lmstudio.lan:1234 >/tmp/vmg-lmstudio-fqdn.out 2>&1 || { cat /tmp/vmg-lmstudio-fqdn.out; exit 1; }
	@grep -q 'matchName: lmstudio.lan' /tmp/vmg-lmstudio-fqdn.out || { echo "FAIL: a hostname LM Studio endpoint is not opened by name"; exit 1; }
	@echo "ok: lmstudio egress"
	$(call managers_must_fail,lmstudio endpoint required,$(MANAGERS_ON) --set model-manager.backend=lmstudio,model-manager.lmstudio.endpoint is empty)
	$(call managers_must_fail,lmstudio endpoint must be a URL,$(MANAGERS_ON) --set model-manager.backend=lmstudio --set model-manager.lmstudio.endpoint=lmstudio:1234,must be an http(s) URL)
	@echo "--> backends list: one model-manager in front of Ollama AND Lemonade opens both host endpoints, in both flavors"
	@helm template t $(CONNECTIVITY_DIR) $(MANAGERS_ON) --set 'model-manager.backends[0]=ollama' --set 'model-manager.backends[1]=lemonade' --set model-manager.lemonade.endpoint=http://10.0.0.2:13305 >/tmp/vmg-multi.out 2>&1 || { cat /tmp/vmg-multi.out; exit 1; }
	@grep -q '10.0.0.1/32' /tmp/vmg-multi.out || { echo "FAIL: backends list: the Ollama endpoint is not opened"; exit 1; }
	@grep -q '10.0.0.2/32' /tmp/vmg-multi.out || { echo "FAIL: backends list: the Lemonade endpoint is not opened"; exit 1; }
	@grep -q 'port: "11434"' /tmp/vmg-multi.out || { echo "FAIL: backends list: the Ollama port is not opened"; exit 1; }
	@grep -q 'port: "13305"' /tmp/vmg-multi.out || { echo "FAIL: backends list: the Lemonade port is not opened"; exit 1; }
	@if grep -q 'huggingface.co' /tmp/vmg-multi.out; then echo "FAIL: Hugging Face egress rendered without kserve in the backends list"; exit 1; fi
	@helm template t $(CONNECTIVITY_DIR) $(MANAGERS_ON) --set networkPolicy.flavor=kubernetes --set 'model-manager.backends[0]=ollama' --set 'model-manager.backends[1]=lemonade' --set model-manager.lemonade.endpoint=http://10.0.0.2:13305 >/tmp/vmg-multi-k8s.out 2>&1 || { cat /tmp/vmg-multi-k8s.out; exit 1; }
	@grep -q 'cidr: 10.0.0.1/32' /tmp/vmg-multi-k8s.out || { echo "FAIL: kubernetes backends list: the Ollama endpoint is not opened"; exit 1; }
	@grep -q 'cidr: 10.0.0.2/32' /tmp/vmg-multi-k8s.out || { echo "FAIL: kubernetes backends list: the Lemonade endpoint is not opened"; exit 1; }
	@echo "--> backends list, all three host backends: hostTargets opens each address and port in both flavors"
	@helm template t $(CONNECTIVITY_DIR) $(MANAGERS_ON) --set 'model-manager.backends[0]=ollama' --set 'model-manager.backends[1]=lemonade' --set 'model-manager.backends[2]=lmstudio' --set model-manager.lemonade.endpoint=http://10.0.0.2:13305 --set model-manager.lmstudio.endpoint=http://10.0.0.3:1234 >/tmp/vmg-multi3.out 2>&1 || { cat /tmp/vmg-multi3.out; exit 1; }
	@for pair in 10.0.0.1/32:11434 10.0.0.2/32:13305 10.0.0.3/32:1234; do \
		addr=$${pair%%:*}; port=$${pair##*:}; \
		grep -q "$$addr" /tmp/vmg-multi3.out || { echo "FAIL: three host backends: $$addr is not opened"; exit 1; }; \
		grep -q "port: \"$$port\"" /tmp/vmg-multi3.out || { echo "FAIL: three host backends: port $$port is not opened"; exit 1; }; \
	done
	@helm template t $(CONNECTIVITY_DIR) $(MANAGERS_ON) --set networkPolicy.flavor=kubernetes --set 'model-manager.backends[0]=ollama' --set 'model-manager.backends[1]=lemonade' --set 'model-manager.backends[2]=lmstudio' --set model-manager.lemonade.endpoint=http://10.0.0.2:13305 --set model-manager.lmstudio.endpoint=http://10.0.0.3:1234 >/tmp/vmg-multi3-k8s.out 2>&1 || { cat /tmp/vmg-multi3-k8s.out; exit 1; }
	@for addr in 10.0.0.1/32 10.0.0.2/32 10.0.0.3/32; do \
		grep -q "cidr: $$addr" /tmp/vmg-multi3-k8s.out || { echo "FAIL: kubernetes three host backends: $$addr is not opened"; exit 1; }; \
	done
	@echo "ok: three host backends"
	@echo "--> backends list with kserve: the Ollama endpoint AND the Hub"
	@helm template t $(CONNECTIVITY_DIR) $(MANAGERS_ON) --set 'model-manager.backends[0]=ollama' --set 'model-manager.backends[1]=kserve' --set modelManager.kserve.requireApi=false >/tmp/vmg-multi-kserve.out 2>&1 || { cat /tmp/vmg-multi-kserve.out; exit 1; }
	@grep -q 'matchName: huggingface.co' /tmp/vmg-multi-kserve.out || { echo "FAIL: backends list with kserve: no Hugging Face egress"; exit 1; }
	@grep -q '10.0.0.1/32' /tmp/vmg-multi-kserve.out || { echo "FAIL: backends list with kserve: the Ollama endpoint is not opened"; exit 1; }
	@echo "--> backends list: the one-element list renders as the single backend does"
	@helm template t $(CONNECTIVITY_DIR) $(MANAGERS_ON) $(MANAGERS_ROUTES) --set 'model-manager.backends[0]=ollama' >/tmp/vmg-multi-one.out 2>&1 || { cat /tmp/vmg-multi-one.out; exit 1; }
	@awk '/^kind: (CiliumNetworkPolicy|HTTPRoute|AgentgatewayBackend|AgentgatewayPolicy)$$/,/^---/' /tmp/vmg-cilium.out | grep -v 'model-manager:' >/tmp/vmg-multi-one-want.out; awk '/^kind: (CiliumNetworkPolicy|HTTPRoute|AgentgatewayBackend|AgentgatewayPolicy)$$/,/^---/' /tmp/vmg-multi-one.out | grep -v 'model-manager:' >/tmp/vmg-multi-one-got.out; cmp -s /tmp/vmg-multi-one-want.out /tmp/vmg-multi-one-got.out || { echo "FAIL: backends: [ollama] renders differently from backend: ollama"; diff /tmp/vmg-multi-one-want.out /tmp/vmg-multi-one-got.out | head; exit 1; }
	@echo "ok: backends list"
	@echo "--> kubernetes flavor: NetworkPolicy objects, no cilium.io kinds"
	@helm template t $(CONNECTIVITY_DIR) $(MANAGERS_ON) $(MANAGERS_ROUTES) --set networkPolicy.flavor=kubernetes >/tmp/vmg-k8s.out 2>&1 || { cat /tmp/vmg-k8s.out; exit 1; }
	@if grep -q 'cilium.io' /tmp/vmg-k8s.out; then echo "FAIL: cilium.io objects render in the kubernetes flavor"; exit 1; fi
	@for n in model-manager-ingress model-manager-egress dataplane-to-model-manager muster-to-model-manager agent-manager-ingress agent-manager-egress dataplane-to-agent-manager muster-to-agent-manager; do \
		grep -A3 '^kind: NetworkPolicy$$' /tmp/vmg-k8s.out | grep -q "^  name: agent-platform-connectivity-$$n$$" || { echo "FAIL: NetworkPolicy agent-platform-connectivity-$$n missing from the kubernetes render"; exit 1; }; \
	done
	@echo "ok: kubernetes flavor"
	@echo "--> networkPolicy.enabled=false renders no policy for either component"
	@helm template t $(CONNECTIVITY_DIR) $(MANAGERS_ON) --set networkPolicy.enabled=false >/tmp/vmg-nonp.out 2>&1 || { cat /tmp/vmg-nonp.out; exit 1; }
	@if grep -qE 'kind: (CiliumNetworkPolicy|NetworkPolicy)' /tmp/vmg-nonp.out; then echo "FAIL: network policies render with networkPolicy.enabled=false"; exit 1; else echo "ok: policy master switch"; fi
	@echo "--> routes off: no agentgateway.dev object of theirs, muster still reaches the MCP endpoints"
	@grep -q 'agent-platform-connectivity-muster-to-agent-manager' /tmp/vmg-kserve.out || { echo "FAIL: muster egress to agent-manager missing with the route off"; exit 1; }
	@if grep -q 'name: model-manager-jwt' /tmp/vmg-kserve.out; then echo "FAIL: JWT policy rendered with the route off"; exit 1; else echo "ok: routes off"; fi
	@echo "--> ingress.additionalPeers: extra same-namespace callers in both flavors, counted as a platform caller"
	@helm template t $(CONNECTIVITY_DIR) $(MANAGERS_ON) --set model-manager.muster.mcpServer.enabled=false --set agent-manager.muster.mcpServer.enabled=false --set-json 'modelManager.networkPolicy.ingress.additionalPeers=[{"app.kubernetes.io/name":"portal"}]' --set-json 'agentManager.networkPolicy.ingress.additionalPeers=[{"app.kubernetes.io/name":"portal","app.kubernetes.io/component":"backend"}]' >/tmp/vmg-peers.out 2>&1 || { cat /tmp/vmg-peers.out; exit 1; }
	@for n in model-manager agent-manager; do \
		awk "/^  name: agent-platform-connectivity-$$n-ingress$$/,/^---/" /tmp/vmg-peers.out >/tmp/vmg-peers-$$n.out; \
		grep -A1 'app.kubernetes.io/name: portal' /tmp/vmg-peers-$$n.out | grep -q 'io.kubernetes.pod.namespace: agent-platform' || { echo "FAIL: cilium $$n ingress lacks the extra peer pinned to the release namespace"; cat /tmp/vmg-peers-$$n.out; exit 1; }; \
		if grep -q 'app.kubernetes.io/component: none' /tmp/vmg-peers-$$n.out; then echo "FAIL: $$n ingress renders the no-caller placeholder next to an extra peer"; exit 1; fi; \
	done
	@grep -q 'app.kubernetes.io/component: backend' /tmp/vmg-peers-agent-manager.out || { echo "FAIL: a multi-label peer lost a label"; exit 1; }
	@helm template t $(CONNECTIVITY_DIR) $(MANAGERS_ON) --set model-manager.muster.mcpServer.enabled=false --set agent-manager.muster.mcpServer.enabled=false >/tmp/vmg-nopeers.out 2>&1 || { cat /tmp/vmg-nopeers.out; exit 1; }
	@[ "$$(grep -c 'app.kubernetes.io/component: none' /tmp/vmg-nopeers.out)" = "2" ] || { echo "FAIL: without a platform caller or an extra peer the ingress policies do not render the placeholder peer"; exit 1; }
	@helm template t $(CONNECTIVITY_DIR) $(MANAGERS_ON) --set networkPolicy.flavor=kubernetes --set-json 'modelManager.networkPolicy.ingress.additionalPeers=[{"app.kubernetes.io/name":"portal"}]' >/tmp/vmg-peers-k8s.out 2>&1 || { cat /tmp/vmg-peers-k8s.out; exit 1; }
	@awk '/^  name: agent-platform-connectivity-model-manager-ingress$$/,/^---/' /tmp/vmg-peers-k8s.out | grep -B2 'app.kubernetes.io/name: portal' | grep -q 'podSelector' || { echo "FAIL: kubernetes model-manager ingress lacks the extra peer as a podSelector"; exit 1; }
	@if grep -q 'io.kubernetes.pod.namespace' /tmp/vmg-peers-k8s.out; then echo "FAIL: a Cilium namespace label leaked into the kubernetes flavor"; exit 1; fi
	@echo "ok: ingress.additionalPeers"
	@echo "--> guards"
	$(call managers_must_fail,ollama endpoint required,$(MANAGERS_MIN) --set model-manager.backend=ollama,model-manager.ollama.endpoint is empty)
	$(call managers_must_fail,backends list ollama endpoint required (one element),$(MANAGERS_MIN) --set 'model-manager.backends[0]=ollama',model-manager.ollama.endpoint is empty)
	$(call managers_must_fail,backend enum,$(MANAGERS_MIN) --set components.model-manager.enabled=true --set model-manager.backend=bogus,must be one of: ollama)
	$(call managers_must_fail,lemonade endpoint required,$(MANAGERS_MIN) --set components.model-manager.enabled=true --set model-manager.backend=lemonade,model-manager.lemonade.endpoint is empty)
	$(call managers_must_fail,lemonade endpoint must be a URL,$(MANAGERS_MIN) --set components.model-manager.enabled=true --set model-manager.backend=lemonade --set model-manager.lemonade.endpoint=172.21.0.1:13305,must be an http(s) URL)
	$(call managers_must_fail,kserve API required,$(MANAGERS_MIN) --set components.model-manager.enabled=true --set model-manager.backend=kserve,serving.kserve.io/v1alpha2 API)
	$(call managers_must_fail,backends list name enum,$(MANAGERS_MIN) --set components.model-manager.enabled=true --set model-manager.ollama.endpoint=http://10.0.0.1:11434 --set 'model-manager.backends[0]=ollama' --set 'model-manager.backends[1]=bogus',must be one of: ollama)
	$(call managers_must_fail,backends list duplicate,$(MANAGERS_MIN) --set components.model-manager.enabled=true --set model-manager.ollama.endpoint=http://10.0.0.1:11434 --set 'model-manager.backends[0]=ollama' --set 'model-manager.backends[1]=ollama',lists a driver twice)
	$(call managers_must_fail,backends list lemonade endpoint required,$(MANAGERS_MIN) --set components.model-manager.enabled=true --set model-manager.ollama.endpoint=http://10.0.0.1:11434 --set 'model-manager.backends[0]=ollama' --set 'model-manager.backends[1]=lemonade',model-manager.lemonade.endpoint is empty)
	$(call managers_must_fail,backends list ollama endpoint required,$(MANAGERS_MIN) --set components.model-manager.enabled=true --set 'model-manager.backends[0]=lemonade' --set 'model-manager.backends[1]=ollama' --set model-manager.lemonade.endpoint=http://10.0.0.2:13305,model-manager.ollama.endpoint is empty)
	$(call managers_must_fail,backends list kserve API required,$(MANAGERS_MIN) --set components.model-manager.enabled=true --set model-manager.ollama.endpoint=http://10.0.0.1:11434 --set 'model-manager.backends[0]=ollama' --set 'model-manager.backends[1]=kserve',serving.kserve.io/v1alpha2 API)
	$(call managers_must_pass,backends list kserve API present,$(MANAGERS_MIN) --set components.model-manager.enabled=true --set model-manager.ollama.endpoint=http://10.0.0.1:11434 --set 'model-manager.backends[0]=ollama' --set 'model-manager.backends[1]=kserve' --api-versions serving.kserve.io/v1alpha2)
	$(call managers_must_pass,kserve API present,$(MANAGERS_MIN) --set components.model-manager.enabled=true --set model-manager.backend=kserve --api-versions serving.kserve.io/v1alpha2)
	$(call managers_must_pass,model-manager without kagent when wiring is off,$(VM) --set components.model-manager.enabled=true --set model-manager.ollama.endpoint=http://10.0.0.1:11434 --set model-manager.kagent.disableWiring=true --set global.identity.issuerUrl=https://dex.ci.example.com --set global.identity.clientId=platform --set global.identity.existingSecret=s --set global.domain=ci.example.com)
	$(call managers_must_fail,model-manager kagent namespace mismatch,$(MANAGERS_MIN) --set components.model-manager.enabled=true --set model-manager.ollama.endpoint=http://10.0.0.1:11434 --set model-manager.kagent.namespace=other,must equal the kagent component's namespace)
	$(call managers_must_fail,agent-manager needs kagent,$(VM) --set components.agent-manager.enabled=true,agent-manager manages kagent agents)
	$(call managers_must_fail,agent-manager kagent namespace mismatch,$(MANAGERS_MIN) --set components.agent-manager.enabled=true --set agent-manager.kagent.namespace=other,must equal the kagent component's namespace)
	$(call managers_must_fail,agent-manager Flux API required when asked,$(MANAGERS_MIN) --set components.agent-manager.enabled=true --set agentManager.flux.requireApi=true,helm.toolkit.fluxcd.io/v2 API)
	$(call managers_must_pass,agent-manager Flux API present,$(MANAGERS_MIN) --set components.agent-manager.enabled=true --set agentManager.flux.requireApi=true --api-versions helm.toolkit.fluxcd.io/v2 --api-versions source.toolkit.fluxcd.io/v1)
	$(call managers_must_fail,OAuth needs an issuer,$(VM) --set components.kagent.enabled=true --set components.agent-manager.enabled=true --set agent-manager.oauth.baseURL=https://x --set agent-manager.oauth.dex.clientID=c --set agent-manager.oauth.existingSecret=s,global.identity.issuerUrl is not set)
	$(call managers_must_fail,OAuth needs a base URL,$(VM) --set components.kagent.enabled=true --set components.agent-manager.enabled=true --set global.identity.issuerUrl=https://dex.ci.example.com --set global.identity.clientId=platform --set global.identity.existingSecret=s,global.domain is not set)
	$(call managers_must_fail,OAuth needs the client secret,$(VM) --set components.kagent.enabled=true --set components.agent-manager.enabled=true --set global.identity.issuerUrl=https://dex.ci.example.com --set global.identity.clientId=platform --set global.domain=ci.example.com,needs the platform client's secret)
	$(call managers_must_pass,OAuth off needs none of it,$(VM) --set components.kagent.enabled=true --set components.agent-manager.enabled=true --set agent-manager.oauth.enabled=false)
	$(call managers_must_fail,route needs an agentgateway mode,$(MANAGERS_MIN) --set components.agent-manager.enabled=true --set agentManager.route.enabled=true,requires an agentgateway-\* ingress.mode)
	$(call managers_must_fail,JWT policy needs jwksEgress,$(MANAGERS_ON) --set modelManager.route.enabled=true --set modelManager.route.jwtAuthentication.enabled=true --set gateway.jwksEgress.enabled=false,gateway.jwksEgress.enabled is false)
	$(call managers_must_fail,parentRef needs both halves,$(MANAGERS_ON) --set agentManager.route.enabled=true --set agentManager.route.parentRef.name=edge --set agentManager.route.parentRef.namespace=,parentRef.name is set but .namespace is empty)
	$(call managers_must_fail,path prefix must be absolute,$(MANAGERS_ON) --set agentManager.route.enabled=true --set agentManager.route.pathPrefix=agent-manager,must start with /)
	$(call managers_must_fail,MCPServer CR needs muster,$(MANAGERS_MIN) --set components.agent-manager.enabled=true --set components.muster.enabled=false,the MCPServer CRD ships with muster)
	$(call managers_must_fail,additionalPeers items are label maps,$(MANAGERS_ON) --set-json 'modelManager.networkPolicy.ingress.additionalPeers=["portal"]',non-empty pod label map)
	$(call managers_must_fail,additionalPeers items are non-empty,$(MANAGERS_ON) --set-json 'agentManager.networkPolicy.ingress.additionalPeers=[{}]',non-empty pod label map)
	@echo "--> meta: both components render as releases that wait for muster and kagent"
	@helm template t $(CHART_DIR) -f $(CHART_DIR)/ci/ci-values.yaml >/tmp/vmg-meta.out 2>&1 || { cat /tmp/vmg-meta.out; exit 1; }
	@for n in model-manager agent-manager; do \
		grep -A3 '^kind: HelmRelease$$' /tmp/vmg-meta.out | grep -q "^  name: $$n$$" || { echo "FAIL: no $$n HelmRelease in the meta render"; exit 1; }; \
		grep -A3 '^kind: OCIRepository$$' /tmp/vmg-meta.out | grep -q "^  name: $$n$$" || { echo "FAIL: no $$n OCIRepository in the meta render"; exit 1; }; \
	done
	@awk '/^  name: agent-manager$$/{f=1} f&&/^  dependsOn:/{d=1} d&&/- name: muster/{m=1} d&&/- name: kagent/{k=1} /^---/{if(f&&d&&m&&k){ok=1}; f=0;d=0;m=0;k=0} END{if(ok)exit 0; else exit 1}' /tmp/vmg-meta.out || { echo "FAIL: the agent-manager release does not dependsOn muster and kagent"; exit 1; }
	@grep -q 'helmReleaseServiceAccount: kagent-flux' /tmp/vmg-meta.out || { echo "FAIL: agent-manager values lost flux.helmReleaseServiceAccount"; exit 1; }
	@helm template t $(CHART_DIR) -f $(CHART_DIR)/ci/ci-values.yaml --set components.kagent.enabled=false --set components.agent-manager.enabled=false >/tmp/vmg-meta-off.out 2>&1 || { cat /tmp/vmg-meta-off.out; exit 1; }
	@if grep -qE '^  name: agent-manager$$' /tmp/vmg-meta-off.out; then echo "FAIL: agent-manager release rendered while disabled"; exit 1; fi
	@if grep -qE '^    - name: kagent$$' /tmp/vmg-meta-off.out; then echo "FAIL: a dependsOn on the disabled kagent survived"; exit 1; fi
	@echo "ok: meta render"
	@echo "All model-manager / agent-manager wiring verified."

# The kagent-flux tenant identity (PRD Q3 / D4) and the upstream fixes that
# retired the text patches the standalone umbrella's generator applied to its
# copy of the connectivity templates (muster-off route gate, kagent Service
# naming, the muster-direct guards, the kserve guard's deference to the bundled
# components, the legacy-toggle probe, doc fixes).
IDENTITY_ON := $(VM) --set components.kagent.enabled=true
MCPS_ONE := --set components.agent-platform-mcps.enabled=true --set agent-platform-mcps.mcpServers[0].cluster=ci --set agent-platform-mcps.mcpServers[0].group=kubernetes --set agent-platform-mcps.mcpServers[0].url=https://mcp.ci.example.com/mcp
.PHONY: verify-identity
verify-identity: ## Assert the kagent-flux tenant identity (ONE value: ServiceAccount, RoleBinding, agent-manager, the portal helper) and the fixes that retired the standalone's template patches.
	@echo "====> $@ ($(CONNECTIVITY_DIR), $(CHART_DIR))"
	@echo "--> kagent on: ServiceAccount + RoleBinding kagent-flux in the kagent namespace, bound to cluster-admin"
	@helm template t $(CONNECTIVITY_DIR) $(IDENTITY_ON) >/tmp/vid-on.out 2>&1 || { cat /tmp/vid-on.out; exit 1; }
	@awk '/^kind: ServiceAccount$$/,/^---/' /tmp/vid-on.out >/tmp/vid-sa.out; grep -q '^  name: kagent-flux$$' /tmp/vid-sa.out || { echo "FAIL: no ServiceAccount kagent-flux"; exit 1; }
	@grep -q '^  namespace: kagent$$' /tmp/vid-sa.out || { echo "FAIL: the ServiceAccount is not in the kagent namespace"; exit 1; }
	@grep -q 'application.giantswarm.io/team: "bumblebee"' /tmp/vid-sa.out || { echo "FAIL: the ServiceAccount lost the team label the fleet's hand-written copy carries"; exit 1; }
	@awk '/^kind: RoleBinding$$/,/^---/' /tmp/vid-on.out >/tmp/vid-rb.out; grep -q '^  name: kagent-flux$$' /tmp/vid-rb.out || { echo "FAIL: no RoleBinding kagent-flux"; exit 1; }
	@grep -q '^  namespace: kagent$$' /tmp/vid-rb.out || { echo "FAIL: the RoleBinding is not in the kagent namespace"; exit 1; }
	@grep -A3 '^roleRef:' /tmp/vid-rb.out | grep -q 'kind: ClusterRole' || { echo "FAIL: the RoleBinding roleRef is not a ClusterRole"; exit 1; }
	@grep -A3 '^roleRef:' /tmp/vid-rb.out | grep -q 'name: cluster-admin' || { echo "FAIL: the RoleBinding does not bind cluster-admin (namespace-scoped admin; the fleet object's immutable roleRef)"; exit 1; }
	@grep -A3 '^subjects:' /tmp/vid-rb.out | grep -q 'name: kagent-flux' || { echo "FAIL: the RoleBinding subject is not kagent-flux"; exit 1; }
	@grep -A3 '^subjects:' /tmp/vid-rb.out | grep -q 'namespace: kagent' || { echo "FAIL: the RoleBinding subject is not in the kagent namespace"; exit 1; }
	@if grep -q '^kind: ClusterRoleBinding$$' /tmp/vid-on.out; then echo "FAIL: the identity must be namespace-scoped, no ClusterRoleBinding"; exit 1; fi
	@echo "ok: identity rendered"
	@echo "--> kagent off: neither object, and no kagent Namespace either"
	@helm template t $(CONNECTIVITY_DIR) $(VM) --set components.kagent.enabled=false >/tmp/vid-off.out 2>&1 || { cat /tmp/vid-off.out; exit 1; }
	@if grep -qE '^kind: (ServiceAccount|RoleBinding)$$' /tmp/vid-off.out; then echo "FAIL: the identity renders with kagent off"; exit 1; else echo "ok: no identity without kagent"; fi
	@if grep -q '^kind: Namespace$$' /tmp/vid-off.out; then echo "FAIL: the kagent Namespace renders with kagent off (an empty Helm-owned namespace)"; exit 1; else echo "ok: no kagent Namespace without kagent"; fi
	@grep -q '^kind: Namespace$$' /tmp/vid-on.out || { echo "FAIL: the kagent Namespace is gone with kagent on"; exit 1; }
	@echo "ok: kagent Namespace follows the component"
	@echo "--> ONE value renames all three consumers: the ServiceAccount, the RoleBinding subject, agent-manager's flux.helmReleaseServiceAccount"
	@helm template t $(CONNECTIVITY_DIR) $(IDENTITY_ON) --set kagent.fluxServiceAccountName=tenant-x >/tmp/vid-x.out 2>&1 || { cat /tmp/vid-x.out; exit 1; }
	@[ "$$(grep -c '^  name: tenant-x$$' /tmp/vid-x.out)" = "2" ] || { echo "FAIL: renaming kagent.fluxServiceAccountName did not rename ServiceAccount and RoleBinding"; exit 1; }
	@grep -A3 '^subjects:' /tmp/vid-x.out | grep -q 'name: tenant-x' || { echo "FAIL: the RoleBinding subject did not follow the value"; exit 1; }
	@if grep -q 'kagent-flux' /tmp/vid-x.out; then echo "FAIL: the old name survives in the connectivity render"; grep -n kagent-flux /tmp/vid-x.out; exit 1; fi
	@helm template t $(CHART_DIR) -f $(CHART_DIR)/ci/ci-values.yaml --set kagent.fluxServiceAccountName=tenant-x >/tmp/vid-meta-x.out 2>&1 || { cat /tmp/vid-meta-x.out; exit 1; }
	@awk '/^kind: HelmRelease$$/{h=1} h&&/^  name: agent-manager$$/{f=1} f&&/^---/{exit} f' /tmp/vid-meta-x.out >/tmp/vid-meta-am.out
	@grep -q 'helmReleaseServiceAccount: tenant-x' /tmp/vid-meta-am.out || { echo "FAIL: agent-manager's flux.helmReleaseServiceAccount is not derived from kagent.fluxServiceAccountName"; head -40 /tmp/vid-meta-am.out; exit 1; }
	@grep -q 'fluxServiceAccountName: tenant-x' /tmp/vid-meta-x.out || { echo "FAIL: the value is not forwarded to the connectivity release"; exit 1; }
	@awk '/^kind: HelmRelease$$/{h=1} h&&/^  name: kagent$$/{f=1} f&&/^---/{exit} f' /tmp/vid-meta-x.out >/tmp/vid-meta-kagent.out
	@if grep -q 'fluxServiceAccountName' /tmp/vid-meta-kagent.out; then echo "FAIL: fluxServiceAccountName forwarded to the kagent chart, whose schema rejects it"; exit 1; fi
	@if grep -q 'kagent-flux' /tmp/vid-meta-x.out; then echo "FAIL: the old name survives in the meta render"; grep -n kagent-flux /tmp/vid-meta-x.out; exit 1; fi
	@echo "ok: one value, three consumers"
	@echo "--> the portal surface reads the same helper (it renders agentPlatform.fluxServiceAccountName from it)"
	@grep -q 'define "agent-platform.kagent.fluxServiceAccountName"' $(CONNECTIVITY_DIR)/templates/_helpers.tpl || { echo "FAIL: the connectivity chart lost the agent-platform.kagent.fluxServiceAccountName helper"; exit 1; }
	@grep -q 'define "agent-platform.kagent.fluxServiceAccountName"' $(CHART_DIR)/templates/_helpers.tpl || { echo "FAIL: the meta chart lost the agent-platform.kagent.fluxServiceAccountName helper"; exit 1; }
	@echo "--> the default: agent-manager receives kagent-flux from the derivation, not from values.yaml"
	@helm template t $(CHART_DIR) -f $(CHART_DIR)/ci/ci-values.yaml >/tmp/vid-meta.out 2>&1 || { cat /tmp/vid-meta.out; exit 1; }
	@grep -q 'helmReleaseServiceAccount: kagent-flux' /tmp/vid-meta.out || { echo "FAIL: agent-manager lost flux.helmReleaseServiceAccount"; exit 1; }
	@if grep -q 'helmReleaseServiceAccount:' $(CHART_DIR)/values.yaml $(CONNECTIVITY_DIR)/values.yaml; then echo "FAIL: agent-manager.flux.helmReleaseServiceAccount is set in a values.yaml again; it is derived from kagent.fluxServiceAccountName"; exit 1; fi
	@echo "--> agent-manager receives the platform's muster MCP URL (muster.url) from the same derivation — the helper agent-platform.musterMcpUrl in both charts — never from values.yaml; it follows muster.fullnameOverride; a disagreeing agent-manager.muster.url fails naming the source"
	@awk '/^kind: HelmRelease$$/{h=1} h&&/^  name: agent-manager$$/{f=1} f&&/^---/{exit} f' /tmp/vid-meta.out >/tmp/vid-meta-am-url.out
	@grep -q '^      url: http://muster.default.svc.cluster.local:8090/mcp$$' /tmp/vid-meta-am-url.out || { echo "FAIL: agent-manager's muster.url is not derived from the muster Service"; grep -n -A4 '^    muster:' /tmp/vid-meta-am-url.out; exit 1; }
	@helm template t $(CHART_DIR) -f $(CHART_DIR)/ci/ci-values.yaml --set muster.fullnameOverride=other-muster 2>/dev/null | awk '/^kind: HelmRelease$$/{h=1} h&&/^  name: agent-manager$$/{f=1} f&&/^---/{exit} f' | grep -q 'url: http://other-muster.default.svc.cluster.local:8090/mcp' || { echo "FAIL: agent-manager's muster.url does not follow muster.fullnameOverride"; exit 1; }
	@if awk '/^agent-manager:/{f=1} f&&/^[a-z]/&&!/^agent-manager/{f=0} f' $(CHART_DIR)/values.yaml | grep -qE '^    url:'; then echo "FAIL: agent-manager.muster.url is set in the meta chart's values.yaml; it is derived from the muster Service"; exit 1; fi
	@if helm template t $(CHART_DIR) -f $(CHART_DIR)/ci/ci-values.yaml --set agent-manager.muster.url=http://other/mcp >/tmp/vid-url-guard.out 2>&1; then \
		echo "FAIL: a disagreeing agent-manager.muster.url was accepted"; exit 1; \
	elif ! grep -q "leave agent-manager.muster.url unset" /tmp/vid-url-guard.out; then \
		echo "FAIL: the muster.url guard failed for the wrong reason"; cat /tmp/vid-url-guard.out; exit 1; \
	else echo "ok: muster.url guard"; fi
	@helm template t $(CHART_DIR) -f $(CHART_DIR)/ci/ci-values.yaml --set agent-manager.muster.url=http://muster.default.svc.cluster.local:8090/mcp >/dev/null 2>&1 || { echo "FAIL: an agreeing agent-manager.muster.url must pass"; exit 1; }
	@grep -q 'define "agent-platform.musterMcpUrl"' $(CONNECTIVITY_DIR)/templates/_helpers.tpl || { echo "FAIL: the connectivity chart lost the agent-platform.musterMcpUrl helper"; exit 1; }
	@grep -q 'define "agent-platform.musterMcpUrl"' $(CHART_DIR)/templates/_helpers.tpl || { echo "FAIL: the meta chart lost the agent-platform.musterMcpUrl helper"; exit 1; }
	@helm template t $(CHART_DIR) -f $(CHART_DIR)/ci/ci-values.yaml --set components.muster.enabled=false 2>/dev/null | awk '/^kind: HelmRelease$$/{h=1} h&&/^  name: agent-manager$$/{f=1} f&&/^---/{exit} f' >/tmp/vid-meta-am-nomuster.out; if grep -q 'url: http://' /tmp/vid-meta-am-nomuster.out; then echo "FAIL: agent-manager's muster.url is derived while the muster component is off"; exit 1; fi
	@echo "ok: muster MCP URL — one helper, one consumer (agent-manager muster.url; the portal sends none)"
	@echo "--> empty value: no identity, agent-manager omits the ServiceAccount"
	@helm template t $(CONNECTIVITY_DIR) $(IDENTITY_ON) --set kagent.fluxServiceAccountName= >/tmp/vid-empty.out 2>&1 || { cat /tmp/vid-empty.out; exit 1; }
	@if grep -qE '^kind: (ServiceAccount|RoleBinding)$$' /tmp/vid-empty.out; then echo "FAIL: an empty kagent.fluxServiceAccountName still renders the identity"; exit 1; fi
	@helm template t $(CHART_DIR) -f $(CHART_DIR)/ci/ci-values.yaml --set kagent.fluxServiceAccountName= >/tmp/vid-meta-empty.out 2>&1 || { cat /tmp/vid-meta-empty.out; exit 1; }
	@grep -q 'helmReleaseServiceAccount: ""' /tmp/vid-meta-empty.out || { echo "FAIL: an empty value does not reach agent-manager as an empty ServiceAccount"; exit 1; }
	@echo "ok: empty value"
	@echo "--> a disagreeing agent-manager.flux.helmReleaseServiceAccount fails, naming the one key; an agreeing one passes"
	@if helm template t $(CHART_DIR) -f $(CHART_DIR)/ci/ci-values.yaml --set agent-manager.flux.helmReleaseServiceAccount=other >/tmp/vid-guard.out 2>&1; then \
		echo "FAIL: a disagreeing agent-manager.flux.helmReleaseServiceAccount was accepted"; exit 1; \
	elif ! grep -q "set kagent.fluxServiceAccountName" /tmp/vid-guard.out; then \
		echo "FAIL: the identity guard failed for the wrong reason"; cat /tmp/vid-guard.out; exit 1; \
	else echo "ok: identity guard"; fi
	@helm template t $(CHART_DIR) -f $(CHART_DIR)/ci/ci-values.yaml --set agent-manager.flux.helmReleaseServiceAccount=kagent-flux >/dev/null 2>&1 || { echo "FAIL: an agreeing agent-manager.flux.helmReleaseServiceAccount must pass"; exit 1; }
	@echo "--> upstream fixes that retired the standalone's template patches"
	@helm template t $(CONNECTIVITY_DIR) $(VM) --set components.muster.enabled=false >/tmp/vid-nomuster.out 2>&1 || { cat /tmp/vid-nomuster.out; exit 1; }
	@if grep -A3 '^kind: HTTPRoute$$' /tmp/vid-nomuster.out | grep -q '^  name: muster$$'; then echo "FAIL: the muster / HTTPRoute renders with the muster component off (hostname-less, it would blackhole the shared Gateway)"; exit 1; else echo "ok: muster route gated on the component"; fi
	@helm template t $(CONNECTIVITY_DIR) $(VM) >/tmp/vid-muster.out 2>&1 || { cat /tmp/vid-muster.out; exit 1; }
	@grep -A3 '^kind: HTTPRoute$$' /tmp/vid-muster.out | grep -q '^  name: muster$$' || { echo "FAIL: the muster / HTTPRoute is gone with muster on"; exit 1; }
	@helm template t $(CONNECTIVITY_DIR) $(IDENTITY_ON) --set kagent.uiRoute.enabled=true --set kagent.uiRoute.hostname=kagent.ci.example.com --set kagent.oauth2-proxy.enabled=false >/tmp/vid-ui.out 2>&1 || { cat /tmp/vid-ui.out; exit 1; }
	@grep -q '^        - name: kagent-ui$$' /tmp/vid-ui.out || { echo "FAIL: the kagent UI route does not target the Service named from kagent.fullnameOverride"; grep -n -- '-ui$$' /tmp/vid-ui.out; exit 1; }
	@echo "ok: UI route backend follows fullnameOverride"
	@for case in "kagent.controllerRoute:--set components.kagent.enabled=true --set kagent.controllerRoute.enabled=true --set gateway.jwksEgress.enabled=true --set global.identity.issuerUrl=https://dex.ci.example.com" \
	             "klausGateway.agentgatewayRoute:--set components.klaus-gateway.enabled=true --set klausGateway.agentgatewayRoute.enabled=true" \
	             "agent-platform-mcps.agentgateway:$(MCPS_ONE)"; do \
		knob=$${case%%:*}; flags=$${case#*:}; \
		if helm template t $(CONNECTIVITY_DIR) $(VM) --set ingress.mode=muster-direct --set global.domain=ci.example.com $$flags >/tmp/vid-md.out 2>&1; then \
			echo "FAIL: $$knob renders agentgateway.dev objects in muster-direct mode without failing"; exit 1; \
		elif ! grep -q "agentgateway.dev" /tmp/vid-md.out; then \
			echo "FAIL: the muster-direct guard for $$knob failed for the wrong reason"; cat /tmp/vid-md.out; exit 1; \
		else echo "ok: muster-direct guard: $$knob"; fi; \
	done
	@helm template t $(CONNECTIVITY_DIR) $(VM) --set ingress.mode=muster-direct $(MCPS_ONE) --set agent-platform-mcps.agentgateway.enabled=false >/dev/null 2>&1 || { echo "FAIL: mcps through muster (agentgateway.enabled=false) must pass in muster-direct"; exit 1; }
	@echo "ok: mcps through muster passes in muster-direct"
	@helm template t $(CONNECTIVITY_DIR) $(MANAGERS_MIN) --set components.model-manager.enabled=true --set model-manager.backend=kserve --set components.kserve-llmisvc-resources.enabled=true >/dev/null 2>&1 || { echo "FAIL: the kserve API guard must defer to the bundled kserve-llmisvc-resources component"; exit 1; }
	@helm template t $(CONNECTIVITY_DIR) $(MANAGERS_MIN) --set components.model-manager.enabled=true --set model-manager.backend=kserve --set components.modelServing.enabled=true --set modelServing.kserve.requireApi=false >/dev/null 2>&1 || { echo "FAIL: the kserve API guard must defer to the modelServing component (its own prerequisite check skipped here to isolate the deferral)"; exit 1; }
	@if helm template t $(CONNECTIVITY_DIR) $(MANAGERS_MIN) --set components.model-manager.enabled=true --set model-manager.backend=kserve --set components.kserve-llmisvc-resources.enabled=false >/tmp/vid-kserve.out 2>&1; then echo "FAIL: a kserve-llmisvc-resources component that is OFF must not satisfy the kserve API guard"; exit 1; fi
	@grep -q 'serving.kserve.io/v1alpha2 API' /tmp/vid-kserve.out || { echo "FAIL: the kserve guard failed for the wrong reason"; cat /tmp/vid-kserve.out; exit 1; }
	@echo "ok: kserve guard defers to the bundled components only"
	@if grep -q 'serviceMonitor.enabled, default true' $(CONNECTIVITY_DIR)/templates/_helpers.tpl; then echo "FAIL: the serviceMonitor helper prose states a default again (an umbrella may flip it)"; exit 1; fi
	@echo "ok: upstream fixes"
	@echo "kagent-flux identity verified."

# postgres.backup: the Barman Cloud plugin wiring (ObjectStore, ScheduledBackup,
# the Cluster's plugin entry and ServiceAccount identity, the object-store
# egress), the Crossplane store on AWS and Azure, the volumeSnapshot method, the
# "no backup" signal and every guard.
PG_ON := $(VM) --set components.kagent.enabled=true --set postgres.enabled=true
PG_BACKUP := $(PG_ON) --set postgres.backup.enabled=true
PG_MINIO := $(PG_BACKUP) --set postgres.backup.objectStore.destinationPath=s3://kagent-pg-backups/ --set postgres.backup.objectStore.endpointURL=http://minio.minio.svc:9000 --set postgres.backup.objectStore.s3.accessKeyId.name=minio --set postgres.backup.objectStore.s3.secretAccessKey.name=minio
PG_XP_AWS := $(PG_BACKUP) --set postgres.backup.crossplane.enabled=true --set postgres.backup.crossplane.providerConfigRef=ci --set postgres.backup.crossplane.region=eu-central-1 --set postgres.backup.crossplane.aws.bucketName=giantswarm-ci-kagent-pg --set-string postgres.backup.crossplane.aws.accountId=123456789012 --set postgres.backup.crossplane.aws.oidcProvider=irsa.ci.example.com
PG_XP_AZURE := $(PG_BACKUP) --set postgres.backup.crossplane.enabled=true --set postgres.backup.crossplane.provider=azure --set postgres.backup.crossplane.providerConfigRef=ci --set postgres.backup.crossplane.region=westeurope --set postgres.backup.crossplane.azure.storageAccountName=giantswarmcikagentpg --set postgres.backup.crossplane.azure.containerName=giantswarm-ci-kagent-pg --set postgres.backup.crossplane.azure.resourceGroup=ci

.PHONY: verify-postgres
verify-postgres: ## Assert the postgres.backup wiring (plugin ObjectStore + ScheduledBackup, Crossplane store on AWS/Azure, volume snapshots, the no-backup signal) and its guards.
	@echo "====> $@ ($(CONNECTIVITY_DIR))"
	@echo "--> postgres without backup: the Cluster says so, nothing else renders"
	@helm template t $(CONNECTIVITY_DIR) $(PG_ON) >/tmp/vp-off.out 2>&1 || { cat /tmp/vp-off.out; exit 1; }
	@grep -q 'agent-platform.giantswarm.io/backup: none' /tmp/vp-off.out || { echo "FAIL: a Cluster without backup does not carry agent-platform.giantswarm.io/backup: none"; exit 1; }
	@if grep -qE 'kind: (ObjectStore|ScheduledBackup)|^  plugins:|serviceAccountTemplate' /tmp/vp-off.out; then echo "FAIL: backup objects render with postgres.backup.enabled=false"; exit 1; fi
	@if awk '/^  name: kagent-pg-cluster$$/,/^---/' /tmp/vp-off.out | grep -q '\- world'; then echo "FAIL: the CNPG policy opens world egress without a backup"; exit 1; fi
	@helm template t $(CONNECTIVITY_DIR) $(PG_ON) --set postgres.backup.enabled=true --set postgres.enabled=false >/tmp/vp-nopg.out 2>&1 || { cat /tmp/vp-nopg.out; exit 1; }
	@if grep -qE 'kind: (ObjectStore|ScheduledBackup|Cluster)$$' /tmp/vp-nopg.out; then echo "FAIL: backup objects render without a Cluster"; exit 1; fi
	@echo "ok: no-backup signal"
	@echo "--> plugin to an S3-compatible store with static keys (MinIO shape)"
	@helm template t $(CONNECTIVITY_DIR) $(PG_MINIO) >/tmp/vp-minio.out 2>&1 || { cat /tmp/vp-minio.out; exit 1; }
	@grep -q 'agent-platform.giantswarm.io/backup: plugin' /tmp/vp-minio.out || { echo "FAIL: the Cluster does not announce the plugin backup"; exit 1; }
	@grep -A3 '^kind: ObjectStore$$' /tmp/vp-minio.out | grep -q '^  name: kagent-pg-backup$$' || { echo "FAIL: ObjectStore kagent-pg-backup missing"; exit 1; }
	@grep -A3 '^kind: ScheduledBackup$$' /tmp/vp-minio.out | grep -q '^  name: kagent-pg-scheduled$$' || { echo "FAIL: ScheduledBackup kagent-pg-scheduled missing"; exit 1; }
	@grep -q 'barmanObjectName: kagent-pg-backup' /tmp/vp-minio.out || { echo "FAIL: the Cluster's plugin entry does not name the ObjectStore"; exit 1; }
	@grep -q 'isWALArchiver: true' /tmp/vp-minio.out || { echo "FAIL: the plugin is not the WAL archiver"; exit 1; }
	@grep -q 'endpointURL: "http://minio.minio.svc:9000"' /tmp/vp-minio.out || { echo "FAIL: endpointURL missing"; exit 1; }
	@grep -A1 'accessKeyId:' /tmp/vp-minio.out | grep -q 'name: "minio"' || { echo "FAIL: static S3 credentials missing"; exit 1; }
	@if grep -q 'inheritFromIAMRole' /tmp/vp-minio.out; then echo "FAIL: IRSA rendered next to static keys"; exit 1; fi
	@grep -q 'retentionPolicy: "30d"' /tmp/vp-minio.out || { echo "FAIL: default retention missing"; exit 1; }
	@grep -q 'schedule: "0 0 2 \* \* \*"' /tmp/vp-minio.out || { echo "FAIL: default schedule missing"; exit 1; }
	@grep -q 'immediate: true' /tmp/vp-minio.out || { echo "FAIL: the first backup is not immediate"; exit 1; }
	@grep -q 'method: plugin' /tmp/vp-minio.out || { echo "FAIL: ScheduledBackup method is not plugin"; exit 1; }
	@if grep -q '^        serverName:' /tmp/vp-minio.out; then echo "FAIL: serverName rendered while unset (must default to the Cluster name)"; exit 1; fi
	@awk '/^  name: kagent-pg-cluster$$/,/^---/' /tmp/vp-minio.out >/tmp/vp-minio-cnp.out
	@grep -q '\- world' /tmp/vp-minio-cnp.out || { echo "FAIL: the CNPG policy has no world egress for the store"; exit 1; }
	@grep -A6 'endpointSelector:' /tmp/vp-minio-cnp.out | grep -q '\- kagent-pg-restore$$' || { echo "FAIL: the CNPG policy does not select the <clusterName>-restore scratch Cluster"; exit 1; }
	@grep -q '\- cluster' /tmp/vp-minio-cnp.out || { echo "FAIL: an in-cluster endpointURL did not add the cluster entity"; exit 1; }
	@grep -q 'k8s-app: kube-dns' /tmp/vp-minio-cnp.out || { echo "FAIL: the CNPG policy has no DNS egress for the store"; exit 1; }
	@grep -q 'port: "443"' /tmp/vp-minio-cnp.out || { echo "FAIL: the store egress does not open 443"; exit 1; }
	@helm template t $(CONNECTIVITY_DIR) $(PG_MINIO) --set postgres.backup.serverName=kagent-pg-2 --set 'postgres.backup.networkPolicy.fqdns[0].matchName=minio.example.internal' --set 'postgres.backup.networkPolicy.cidrs[0]=198.51.100.0/24' --set-string 'postgres.backup.networkPolicy.ports[0]=9000' >/tmp/vp-minio2.out 2>&1 || { cat /tmp/vp-minio2.out; exit 1; }
	@grep -q 'serverName: "kagent-pg-2"' /tmp/vp-minio2.out || { echo "FAIL: serverName override missing from the plugin entry"; exit 1; }
	@grep -q 'matchName: minio.example.internal' /tmp/vp-minio2.out || { echo "FAIL: FQDN egress for the store missing"; exit 1; }
	@grep -B2 -A2 'matchPattern: "\*"' /tmp/vp-minio2.out | grep -q 'dns:' || { echo "FAIL: FQDN egress without the DNS proxy rule"; exit 1; }
	@grep -q '\- 198.51.100.0/24' /tmp/vp-minio2.out || { echo "FAIL: CIDR egress for the store missing"; exit 1; }
	@grep -q 'port: "9000"' /tmp/vp-minio2.out || { echo "FAIL: store port override missing"; exit 1; }
	@echo "ok: plugin + static keys"
	@echo "--> Crossplane AWS: bucket, lifecycle, public-access block, TLS policy, IRSA role; derived destinationPath and role annotation"
	@helm template t $(CONNECTIVITY_DIR) $(PG_XP_AWS) >/tmp/vp-aws.out 2>&1 || { cat /tmp/vp-aws.out; exit 1; }
	@for obj in "Bucket giantswarm-ci-kagent-pg" "BucketLifecycleConfiguration giantswarm-ci-kagent-pg" "BucketPublicAccessBlock giantswarm-ci-kagent-pg" "BucketPolicy giantswarm-ci-kagent-pg" "Role giantswarm-ci-kagent-pg" "ObjectStore kagent-pg-backup" "ScheduledBackup kagent-pg-scheduled"; do \
		kind=$${obj% *}; n=$${obj#* }; \
		grep -A3 "^kind: $$kind$$" /tmp/vp-aws.out | grep -q "^  name: $$n$$" || { echo "FAIL: $$kind $$n missing from the AWS render"; exit 1; }; \
	done
	@grep -q 'destinationPath: "s3://giantswarm-ci-kagent-pg/"' /tmp/vp-aws.out || { echo "FAIL: destinationPath not derived from the bucket"; exit 1; }
	@grep -q 'inheritFromIAMRole: true' /tmp/vp-aws.out || { echo "FAIL: IRSA not selected by the AWS store"; exit 1; }
	@grep -q 'eks.amazonaws.com/role-arn: arn:aws:iam::123456789012:role/giantswarm-ci-kagent-pg' /tmp/vp-aws.out || { echo "FAIL: the Cluster ServiceAccount does not carry the derived role"; exit 1; }
	@grep -q '"irsa.ci.example.com:sub": "system:serviceaccount:kagent:kagent-pg"' /tmp/vp-aws.out || { echo "FAIL: the role does not trust the Cluster ServiceAccount"; exit 1; }
	@grep -q '"irsa.ci.example.com:sub": "system:serviceaccount:kagent:kagent-pg-restore\*"' /tmp/vp-aws.out || { echo "FAIL: the role does not trust scratch restore clusters"; exit 1; }
	@grep -q 'arn:aws:iam::123456789012:oidc-provider/irsa.ci.example.com' /tmp/vp-aws.out || { echo "FAIL: the OIDC provider ARN is wrong"; exit 1; }
	@grep -q 'helm.sh/resource-policy: keep' /tmp/vp-aws.out || { echo "FAIL: the Bucket lost helm.sh/resource-policy: keep"; exit 1; }
	@awk '/^kind: Bucket$$/,/^---/' /tmp/vp-aws.out | grep -q 'LateInitialize' || { echo "FAIL: the Bucket management policy allows Delete"; exit 1; }
	@if awk '/^kind: Bucket$$/,/^---/' /tmp/vp-aws.out | grep -q '"\*"'; then echo "FAIL: the Bucket management policy allows Delete"; exit 1; fi
	@grep -q 'days: 45' /tmp/vp-aws.out || { echo "FAIL: lifecycle expiration missing"; exit 1; }
	@grep -q 'aws:SecureTransport' /tmp/vp-aws.out || { echo "FAIL: TLS-only bucket policy missing"; exit 1; }
	@grep -q 'managed-by: crossplane' /tmp/vp-aws.out || { echo "FAIL: default tags missing"; exit 1; }
	@helm template t $(CONNECTIVITY_DIR) $(PG_XP_AWS) --set postgres.backup.crossplane.region=cn-north-1 --set postgres.backup.crossplane.observeOnly=true >/tmp/vp-aws-cn.out 2>&1 || { cat /tmp/vp-aws-cn.out; exit 1; }
	@grep -q 'arn:aws-cn:iam::123456789012:role/giantswarm-ci-kagent-pg' /tmp/vp-aws-cn.out || { echo "FAIL: China partition ARN missing"; exit 1; }
	@grep -q '"sts.amazonaws.com.cn"' /tmp/vp-aws-cn.out || { echo "FAIL: China STS audience missing"; exit 1; }
	@if grep -A1 'managementPolicies:' /tmp/vp-aws-cn.out | grep -q '"\*"'; then echo "FAIL: observeOnly still renders a full management policy"; exit 1; fi
	@grep -A1 'managementPolicies:' /tmp/vp-aws-cn.out | grep -q '\- Observe' || { echo "FAIL: observeOnly renders no Observe policy"; exit 1; }
	@echo "ok: Crossplane AWS"
	@echo "--> Crossplane Azure: Account, Container, ManagementPolicy, PrivateEndpoint when private; derived destinationPath and connection Secret"
	@helm template t $(CONNECTIVITY_DIR) $(PG_XP_AZURE) >/tmp/vp-az.out 2>&1 || { cat /tmp/vp-az.out; exit 1; }
	@for obj in "Account giantswarmcikagentpg" "Container giantswarm-ci-kagent-pg" "ManagementPolicy giantswarmcikagentpg" "ObjectStore kagent-pg-backup"; do \
		kind=$${obj% *}; n=$${obj#* }; \
		grep -A3 "^kind: $$kind$$" /tmp/vp-az.out | grep -q "^  name: $$n$$" || { echo "FAIL: $$kind $$n missing from the Azure render"; exit 1; }; \
	done
	@if grep -q 'kind: PrivateEndpoint' /tmp/vp-az.out; then echo "FAIL: PrivateEndpoint rendered for a public installation"; exit 1; fi
	@grep -q 'destinationPath: "https://giantswarmcikagentpg.blob.core.windows.net/giantswarm-ci-kagent-pg/"' /tmp/vp-az.out || { echo "FAIL: destinationPath not derived from the container"; exit 1; }
	@grep -A2 'connectionString:' /tmp/vp-az.out | grep -q 'name: "kagent-pg-backup-store"' || { echo "FAIL: azure credentials do not read the Account's connection Secret"; exit 1; }
	@grep -q 'key: "attribute.primary_blob_connection_string"' /tmp/vp-az.out || { echo "FAIL: connection string key missing"; exit 1; }
	@grep -A1 'writeConnectionSecretToRef:' /tmp/vp-az.out | grep -q 'name: kagent-pg-backup-store' || { echo "FAIL: the Account does not write kagent-pg-backup-store"; exit 1; }
	@grep -q 'publicNetworkAccessEnabled: true' /tmp/vp-az.out || { echo "FAIL: a public installation lost public network access"; exit 1; }
	@grep -q 'managed_by: crossplane' /tmp/vp-az.out || { echo "FAIL: Azure tags keep hyphens"; exit 1; }
	@if grep -q 'serviceAccountTemplate' /tmp/vp-az.out; then echo "FAIL: a ServiceAccount identity rendered for connection-string credentials"; exit 1; fi
	@helm template t $(CONNECTIVITY_DIR) $(PG_XP_AZURE) --set postgres.backup.crossplane.azure.private=true --set postgres.backup.crossplane.azure.subscriptionId=00000000-0000-0000-0000-000000000000 >/tmp/vp-az-priv.out 2>&1 || { cat /tmp/vp-az-priv.out; exit 1; }
	@grep -A3 '^kind: PrivateEndpoint$$' /tmp/vp-az-priv.out | grep -q '^  name: giantswarm-ci-kagent-pg$$' || { echo "FAIL: PrivateEndpoint missing on a private installation"; exit 1; }
	@grep -q 'publicNetworkAccessEnabled: false' /tmp/vp-az-priv.out || { echo "FAIL: a private installation keeps public network access"; exit 1; }
	@grep -q 'subnetId: /subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/ci/providers/Microsoft.Network/virtualNetworks/ci-vnet/subnets/node-subnet' /tmp/vp-az-priv.out || { echo "FAIL: subnet id not derived"; exit 1; }
	@grep -q 'name: ci-privatelink.blob.core.windows.net' /tmp/vp-az-priv.out || { echo "FAIL: private DNS zone ref not derived"; exit 1; }
	@echo "ok: Crossplane Azure"
	@echo "--> volume snapshots and an existing ObjectStore"
	@helm template t $(CONNECTIVITY_DIR) $(PG_BACKUP) --set postgres.backup.method=volumeSnapshot --set postgres.backup.volumeSnapshot.className=ebs-vsc >/tmp/vp-vs.out 2>&1 || { cat /tmp/vp-vs.out; exit 1; }
	@grep -q 'className: "ebs-vsc"' /tmp/vp-vs.out || { echo "FAIL: volumeSnapshot class missing from the Cluster"; exit 1; }
	@grep -q 'method: volumeSnapshot' /tmp/vp-vs.out || { echo "FAIL: ScheduledBackup method is not volumeSnapshot"; exit 1; }
	@grep -q 'agent-platform.giantswarm.io/backup: volumeSnapshot' /tmp/vp-vs.out || { echo "FAIL: the Cluster does not announce the snapshot backup"; exit 1; }
	@if grep -qE 'kind: ObjectStore|pluginConfiguration|^  plugins:' /tmp/vp-vs.out; then echo "FAIL: plugin objects render for the volumeSnapshot method"; exit 1; fi
	@helm template t $(CONNECTIVITY_DIR) $(PG_BACKUP) --set postgres.backup.objectStore.existingName=shared-store >/tmp/vp-existing.out 2>&1 || { cat /tmp/vp-existing.out; exit 1; }
	@if grep -q 'kind: ObjectStore' /tmp/vp-existing.out; then echo "FAIL: an ObjectStore renders next to existingName"; exit 1; fi
	@grep -q 'barmanObjectName: shared-store' /tmp/vp-existing.out || { echo "FAIL: the plugin entry does not name the existing store"; exit 1; }
	@echo "ok: volumeSnapshot + existingName"
	@echo "--> guards"
	@if helm template t $(CONNECTIVITY_DIR) $(PG_BACKUP) >/tmp/vp-g1.out 2>&1; then echo "FAIL: an empty destinationPath rendered"; exit 1; \
	elif ! grep -q 'destinationPath is empty' /tmp/vp-g1.out; then echo "FAIL: empty destinationPath failed for the wrong reason"; cat /tmp/vp-g1.out; exit 1; else echo "ok: destinationPath guard"; fi
	@if helm template t $(CONNECTIVITY_DIR) $(PG_BACKUP) --set postgres.backup.objectStore.destinationPath=s3://b/ >/tmp/vp-g2.out 2>&1; then echo "FAIL: a store without credentials rendered"; exit 1; \
	elif ! grep -q 'exactly one credential source' /tmp/vp-g2.out; then echo "FAIL: missing credentials failed for the wrong reason"; cat /tmp/vp-g2.out; exit 1; else echo "ok: credentials guard (none)"; fi
	@if helm template t $(CONNECTIVITY_DIR) $(PG_MINIO) --set postgres.backup.objectStore.azure.inheritFromAzureAD=true >/tmp/vp-g3.out 2>&1; then echo "FAIL: two credential sources rendered"; exit 1; \
	elif ! grep -q 'got 2' /tmp/vp-g3.out; then echo "FAIL: two credential sources failed for the wrong reason"; cat /tmp/vp-g3.out; exit 1; else echo "ok: credentials guard (two)"; fi
	@if helm template t $(CONNECTIVITY_DIR) $(PG_BACKUP) --set postgres.backup.objectStore.destinationPath=s3://b/ --set postgres.backup.objectStore.s3.inheritFromIAMRole=true >/tmp/vp-g4.out 2>&1; then echo "FAIL: IRSA without a role annotation rendered"; exit 1; \
	elif ! grep -q 'carries no role' /tmp/vp-g4.out; then echo "FAIL: IRSA without a role failed for the wrong reason"; cat /tmp/vp-g4.out; exit 1; else echo "ok: IRSA role guard"; fi
	@if helm template t $(CONNECTIVITY_DIR) $(PG_BACKUP) --set postgres.backup.method=bogus >/tmp/vp-g5.out 2>&1; then echo "FAIL: a bogus method rendered"; exit 1; \
	elif ! grep -q 'must be one of: plugin, volumeSnapshot' /tmp/vp-g5.out; then echo "FAIL: bogus method failed for the wrong reason"; cat /tmp/vp-g5.out; exit 1; else echo "ok: method guard"; fi
	@if helm template t $(CONNECTIVITY_DIR) $(PG_BACKUP) --set postgres.backup.method=volumeSnapshot >/tmp/vp-g6.out 2>&1; then echo "FAIL: volumeSnapshot without a class rendered"; exit 1; \
	elif ! grep -q 'volumeSnapshot.className' /tmp/vp-g6.out; then echo "FAIL: volumeSnapshot without a class failed for the wrong reason"; cat /tmp/vp-g6.out; exit 1; else echo "ok: snapshot class guard"; fi
	@if helm template t $(CONNECTIVITY_DIR) $(PG_MINIO) --set postgres.backup.objectStore.retentionPolicy=30 >/tmp/vp-g7.out 2>&1; then echo "FAIL: a bad retention rendered"; exit 1; \
	elif ! grep -q 'retentionPolicy' /tmp/vp-g7.out; then echo "FAIL: bad retention failed for the wrong reason"; cat /tmp/vp-g7.out; exit 1; else echo "ok: retention guard"; fi
	@if helm template t $(CONNECTIVITY_DIR) $(PG_XP_AWS) --set-string postgres.backup.crossplane.aws.accountId= >/tmp/vp-g8.out 2>&1; then echo "FAIL: Crossplane AWS without an account rendered"; exit 1; \
	elif ! grep -q 'crossplane.aws.accountId is required' /tmp/vp-g8.out; then echo "FAIL: missing account failed for the wrong reason"; cat /tmp/vp-g8.out; exit 1; else echo "ok: Crossplane AWS inputs guard"; fi
	@if helm template t $(CONNECTIVITY_DIR) $(PG_XP_AWS) --set postgres.backup.objectStore.destinationPath=s3://other-bucket/ >/tmp/vp-g9.out 2>&1; then echo "FAIL: a destinationPath outside the Crossplane bucket rendered"; exit 1; \
	elif ! grep -q 'does not point into the Crossplane bucket' /tmp/vp-g9.out; then echo "FAIL: foreign destinationPath failed for the wrong reason"; cat /tmp/vp-g9.out; exit 1; else echo "ok: Crossplane AWS path guard"; fi
	@if helm template t $(CONNECTIVITY_DIR) $(PG_XP_AZURE) --set postgres.backup.crossplane.azure.storageAccountName=Bad-Name >/tmp/vp-g10.out 2>&1; then echo "FAIL: a bad storage account name rendered"; exit 1; \
	elif ! grep -q '3 to 24 lowercase' /tmp/vp-g10.out; then echo "FAIL: bad storage account name failed for the wrong reason"; cat /tmp/vp-g10.out; exit 1; else echo "ok: Crossplane Azure name guard"; fi
	@if helm template t $(CONNECTIVITY_DIR) $(PG_XP_AZURE) --set postgres.backup.crossplane.azure.private=true >/tmp/vp-g11.out 2>&1; then echo "FAIL: a private Azure store without a subscription rendered"; exit 1; \
	elif ! grep -q 'subscriptionId' /tmp/vp-g11.out; then echo "FAIL: private without subscription failed for the wrong reason"; cat /tmp/vp-g11.out; exit 1; else echo "ok: Crossplane Azure private guard"; fi
	@if helm template t $(CONNECTIVITY_DIR) $(PG_XP_AWS) --set postgres.backup.method=volumeSnapshot --set postgres.backup.volumeSnapshot.className=x >/tmp/vp-g12.out 2>&1; then echo "FAIL: Crossplane rendered for the volumeSnapshot method"; exit 1; \
	elif ! grep -q 'method=volumeSnapshot does not use' /tmp/vp-g12.out; then echo "FAIL: crossplane+volumeSnapshot failed for the wrong reason"; cat /tmp/vp-g12.out; exit 1; else echo "ok: Crossplane vs volumeSnapshot guard"; fi
	@echo "--> kubernetes flavor: no cilium.io object, the CNPG pods keep their unrestricted egress"
	@helm template t $(CONNECTIVITY_DIR) $(PG_MINIO) --set networkPolicy.flavor=kubernetes >/tmp/vp-k8s.out 2>&1 || { cat /tmp/vp-k8s.out; exit 1; }
	@if grep -q 'cilium.io' /tmp/vp-k8s.out; then echo "FAIL: cilium.io objects render in the kubernetes flavor"; exit 1; else echo "ok: kubernetes flavor"; fi
	@echo "--> postgres.databases: with Substrate on, the Database kagent-pg-substrate and the derived-Secret hook render; the Secret lands in the Cluster's namespace and in ate-system; the CNPG policy admits ate-api-server; the substrate release gets the Secret reference"
	@helm template t $(CONNECTIVITY_DIR) $(PG_ON) $(SUBSTRATE_ON) >/tmp/vp-db.out 2>&1 || { cat /tmp/vp-db.out; exit 1; }
	@awk "/^kind: Database$$/,/^---/" /tmp/vp-db.out | grep -q '^  name: kagent-pg-substrate$$' || { echo "FAIL: no Database kagent-pg-substrate with Substrate on"; exit 1; }
	@awk "/^  name: kagent-pg-substrate$$/,/^---/" /tmp/vp-db.out | grep -q 'name: substrate$$' || { echo "FAIL: the Substrate Database's spec.name is not substrate"; exit 1; }
	@awk "/^  name: kagent-pg-substrate$$/,/^---/" /tmp/vp-db.out | grep -q 'databaseReclaimPolicy: retain' || { echo "FAIL: the Substrate Database is not retained"; exit 1; }
	@awk "/^  name: t-postgres-databases$$/,/^---/" /tmp/vp-db.out >/tmp/vp-db-hook.out
	@grep -q 'helm.sh/hook: post-install,post-upgrade' /tmp/vp-db-hook.out || { echo "FAIL: the databases hook is not a post-install,post-upgrade hook"; exit 1; }
	@grep -q 'wait --for=create "secret/$$src"' /tmp/vp-db-hook.out || { echo "FAIL: the databases hook does not wait for the CNPG app Secret"; exit 1; }
	@grep -q 'derive "substrate" "substrate" "kagent"' /tmp/vp-db-hook.out || { echo "FAIL: the databases hook does not derive the Secret into the Cluster's namespace"; exit 1; }
	@grep -q 'derive "substrate" "substrate" "ate-system"' /tmp/vp-db-hook.out || { echo "FAIL: the databases hook does not derive the Secret into ate-system (where ate-api-server reads it)"; exit 1; }
	@awk "/^  name: kagent-pg-cluster$$/,/^---/" /tmp/vp-db.out | grep -A1 'app: ate-api-server' | grep -q 'io.kubernetes.pod.namespace: ate-system' || { echo "FAIL: the CNPG policy does not admit ate-api-server from ate-system"; exit 1; }
	@helm template t $(CHART_DIR) -f $(CHART_DIR)/ci/ci-values.yaml --set components.flux.enabled=false --set postgres.enabled=true >/tmp/vp-db-meta.out 2>&1 || { cat /tmp/vp-db-meta.out; exit 1; }
	@awk "/^  name: substrate$$/,/^---/" /tmp/vp-db-meta.out | grep -A2 'connectionStringSecretRef:' | grep -q 'name: kagent-pg-substrate-app' || { echo "FAIL: the meta chart does not derive substrate.postgres.connectionStringSecretRef from the Cluster"; exit 1; }
	@awk "/^  name: substrate$$/,/^---/" /tmp/vp-db-meta.out | grep -A6 '^    postgres:' | grep -q 'enabled: false' || { echo "FAIL: the bundled Substrate Postgres is not off with the platform Cluster on"; exit 1; }
	@helm template t $(CHART_DIR) -f $(CHART_DIR)/ci/ci-values.yaml --set components.flux.enabled=false >/tmp/vp-db-meta-off.out 2>&1 || { cat /tmp/vp-db-meta-off.out; exit 1; }
	@awk "/^  name: substrate$$/,/^---/" /tmp/vp-db-meta-off.out | grep -A6 '^    postgres:' | grep -q 'enabled: true' || { echo "FAIL: without the platform Cluster the substrate release does not run its bundled Postgres (auto)"; exit 1; }
	@if grep -q 'enabled: auto' /tmp/vp-db-meta-off.out; then echo "FAIL: an unresolved substrate.postgres.enabled: auto reached a child release"; exit 1; fi
	@echo "ok: the Substrate database on the Cluster"
	@echo "--> postgres.databases: an installation's own entry renders a Database and a derived Secret; a name that is not an identifier, or the initdb database's, fails"
	@helm template t $(CONNECTIVITY_DIR) $(PG_ON) --set postgres.databases.sessions-v2.name=sessions_v2 --set 'postgres.databases.sessions-v2.extensions[0]=vector' >/tmp/vp-db-own.out 2>&1 || { cat /tmp/vp-db-own.out; exit 1; }
	@awk "/^  name: kagent-pg-sessions-v2$$/,/^---/" /tmp/vp-db-own.out | grep -q -- '- name: vector' || { echo "FAIL: an own database entry did not render with its extension"; exit 1; }
	@grep -q 'derive "sessions-v2" "sessions_v2" "kagent"' /tmp/vp-db-own.out || { echo "FAIL: an own database entry gets no derived Secret"; exit 1; }
	@if grep -q 'kagent-pg-substrate' /tmp/vp-db-own.out; then echo "FAIL: the Substrate database renders while the component is off"; exit 1; fi
	@if helm template t $(CONNECTIVITY_DIR) $(PG_ON) --set postgres.databases.bad.name=Bad-Name >/tmp/vp-db-g1.out 2>&1; then echo "FAIL: a database name that is no identifier rendered"; exit 1; \
	elif ! grep -q 'is not a plain PostgreSQL identifier' /tmp/vp-db-g1.out; then echo "FAIL: bad database name failed for the wrong reason"; cat /tmp/vp-db-g1.out; exit 1; else echo "ok: database name guard"; fi
	@if helm template t $(CONNECTIVITY_DIR) $(PG_ON) --set postgres.databases.dup.name=kagent >/tmp/vp-db-g2.out 2>&1; then echo "FAIL: a database named like the initdb database rendered"; exit 1; \
	elif ! grep -q 'names the initdb database' /tmp/vp-db-g2.out; then echo "FAIL: initdb name clash failed for the wrong reason"; cat /tmp/vp-db-g2.out; exit 1; else echo "ok: initdb name guard"; fi
	@if helm template t $(CONNECTIVITY_DIR) $(PG_ON) $(SUBSTRATE_ON) --set 'postgres.databases.substrate.secretNamespaces[0]=elsewhere' >/tmp/vp-db-g3.out 2>&1; then echo "FAIL: the Substrate database's Secret rendered without ate-system among its namespaces"; exit 1; \
	elif ! grep -q 'secretNamespaces must include ate-system' /tmp/vp-db-g3.out; then echo "FAIL: secretNamespaces guard failed for the wrong reason"; cat /tmp/vp-db-g3.out; exit 1; else echo "ok: ate-system Secret guard"; fi
	@echo "--> postgres off: no Database, no hook; Substrate then runs its bundled Postgres (auto)"
	@helm template t $(CONNECTIVITY_DIR) $(VM) --set components.kagent.enabled=true $(SUBSTRATE_ON) >/tmp/vp-db-off.out 2>&1 || { cat /tmp/vp-db-off.out; exit 1; }
	@if grep -qE 'kind: Database|t-postgres-databases' /tmp/vp-db-off.out; then echo "FAIL: databases render without the platform Cluster"; exit 1; fi
	@awk "/^  name: substrate-ate-api-server$$/,/^---/" /tmp/vp-db-off.out | grep -q 'app: postgres' || { echo "FAIL: without the Cluster, ate-api-server's policy does not open the bundled Postgres"; exit 1; }
	@if helm template t $(CONNECTIVITY_DIR) $(VM) --set components.kagent.enabled=true $(SUBSTRATE_ON) --set substrate.postgres.enabled=false >/tmp/vp-db-g4.out 2>&1; then echo "FAIL: Substrate with no database at all rendered"; exit 1; \
	elif ! grep -q 'has no database' /tmp/vp-db-g4.out; then echo "FAIL: the no-database guard failed for the wrong reason"; cat /tmp/vp-db-g4.out; exit 1; else echo "ok: no-database guard"; fi
	@echo "ok: $@"

.PHONY: verify-kyverno
verify-kyverno: ## Assert the Kyverno PolicyExceptions of Agent Substrate: every Substrate workload — the substrate chart's Deployments and DaemonSet at the pinned build, rendered with the values the meta chart forwards, and the worker pod ate-controller renders for a WorkerPool — is matched by an exception that names exactly the restricted-PSS rules its pod spec violates (computed here), each with its autogen copy; nothing else is excepted; no exception selects app: kagent. Network: gsoci.azurecr.io (the substrate chart); needs PyYAML.
	@echo "====> $@ ($(CHART_DIR), $(CONNECTIVITY_DIR))"
	@python3 tests/verify-kyverno.py $(CHART_DIR) $(CONNECTIVITY_DIR)
	@echo "ok: $@"

# The floor of components.muster.versionRange, the muster chart version the
# platform toolset presets are rendered through: the presets need the `label:`
# rule (muster#1168, from 5.12.0; a muster before it refuses to start on them),
# the floor is 5.31.4 (giantswarm/muster#1323). The customer BOM pins it. The chart is pulled anonymously from gsoci to render its ConfigMap
# with the values the meta chart forwards, so the check reads the real schema
# and template of that version, not a copy.
PRESETS_MUSTER_VERSION := 5.31.4
# The muster chart's own render guards want the OAuth inputs an installation
# supplies; these are placeholders for the render, not part of the assertion.
PRESETS_MUSTER_SETS := --set muster.oauth.server.baseUrl=https://muster.ci.example.com --set muster.oauth.server.dex.issuerUrl=https://dex.ci.example.com --set muster.oauth.server.dex.clientId=platform --set muster.oauth.server.existingSecret=muster-oauth

.PHONY: verify-presets
verify-presets: ## Assert the infrastructure / agent-platform toolset presets reach muster: forwarded on its HelmRelease, accepted by the muster chart's own schema, rendered into its ConfigMap.
	@echo "====> $@ ($(CHART_DIR))"
	@echo "--> the muster HelmRelease values carry both presets, selecting by the tool-group label"
	@helm template t $(CHART_DIR) -f $(CHART_DIR)/ci/ci-values.yaml >/tmp/vp-flux.out 2>&1 || { cat /tmp/vp-flux.out; exit 1; }
	@python3 tests/verify-toolset-presets.py release-values /tmp/vp-flux.out >/tmp/vp-muster-values.yaml
	@echo "--> components.muster.versionRange floors at the muster that has the label rule"
	@grep -q 'semver: ">=$(PRESETS_MUSTER_VERSION) <6.0.0"' /tmp/vp-flux.out || { echo "FAIL: the muster range does not floor at $(PRESETS_MUSTER_VERSION)"; exit 1; }
	@echo "ok: forwarded and floored"
	@echo "--> muster $(PRESETS_MUSTER_VERSION) accepts the forwarded values and renders the presets into its ConfigMap"
	@rm -rf /tmp/vp-muster-chart && mkdir -p /tmp/vp-muster-chart
	@helm pull oci://gsoci.azurecr.io/charts/giantswarm/muster --version $(PRESETS_MUSTER_VERSION) --untar --untardir /tmp/vp-muster-chart >/tmp/vp-pull.out 2>&1 || { cat /tmp/vp-pull.out; exit 1; }
	@helm template muster /tmp/vp-muster-chart/muster --namespace agent-platform -f /tmp/vp-muster-values.yaml $(PRESETS_MUSTER_SETS) --show-only templates/configmap.yaml >/tmp/vp-cm.out 2>&1 || { cat /tmp/vp-cm.out; exit 1; }
	@python3 tests/verify-toolset-presets.py configmap /tmp/vp-cm.out
	@echo "--> a preset that redefines a built-in is refused by the meta chart before it reaches muster"
	@if helm template t $(CHART_DIR) -f $(CHART_DIR)/ci/ci-values.yaml --set-json 'muster.muster.toolsetPresets.full={"include":[{"pattern":"*"}]}' >/tmp/vp-builtin.out 2>&1; then \
		echo "FAIL: a toolsetPresets entry named full passed the render"; exit 1; \
	elif ! grep -q "built into muster" /tmp/vp-builtin.out; then \
		echo "FAIL: the built-in guard failed for the wrong reason"; cat /tmp/vp-builtin.out; exit 1; \
	else echo "ok: built-in names refused"; fi
	@echo "ok: presets reach muster's config"

.PHONY: verify-auto
verify-auto: ## Assert the cluster-shape knobs: `auto` resolves by served API group once, the fleet shape equals the explicit fleet values byte for byte, the vanilla shape has no Kyverno / Cilium / monitor / Envoy-only object, every component copy follows the one detection, explicit values win.
	@echo "====> $@ ($(CHART_DIR), $(CONNECTIVITY_DIR))"
	@python3 tests/verify-cluster-shape.py $(CHART_DIR) $(CONNECTIVITY_DIR)
	@echo "ok: $@"

# The standalone umbrella's wiring, ported into the connectivity chart behind the
# component toggles the meta chart forwards: the Backstage app-config surface
# (components.backstage), the mcp-kubernetes MCPServer registration
# (components.mcp-kubernetes), the KServe/vLLM model serving layer
# (components.modelServing, a feature switch with no chart, on the
# kserve-llmisvc-crd + kserve-llmisvc-resources components) and the llm-d
# controller's guards and network policy. The quick-start inputs Backstage takes by design: global.domain,
# global.identity and a public Gateway for its route.
WIRING_QUICKSTART := --set global.domain=ci.example.com --set global.identity.issuerUrl=https://dex.ci.example.com --set global.identity.clientId=agent-platform --set global.identity.existingSecret=agent-platform-idp --set 'global.gatewayApi.parentRefs[0].name=giantswarm-default' --set 'global.gatewayApi.parentRefs[0].namespace=envoy-gateway-system'
WIRING_BACKSTAGE := $(VM) --namespace agent-platform $(WIRING_QUICKSTART) --set components.backstage.enabled=true
WIRING_SERVING := $(VM) --namespace agent-platform --set components.modelServing.enabled=true --set components.kserve-llmisvc-crd.enabled=true --set components.kserve-llmisvc-resources.enabled=true
# The fleet-shape render with every toggle of this slice off: byte-identical to origin/main's.
WIRING_OFF := $(VM) --namespace agent-platform --set components.kagent.enabled=true
# The Backstage app pods' own policy. WIRING_BACKSTAGE already carries the
# portal's quick-start inputs, the front Gateway its route attaches to among
# them; the agentgateway-* mode gives muster's leg a data plane to name.
WIRING_BACKSTAGE_NETPOL := $(WIRING_BACKSTAGE) --set ingress.mode=agentgateway-muster --set components.agentgateway.enabled=true --set 'ingress.parentRefs[0].namespace=envoy-gateway-system'
# The same, with the portal on its own database: an egress leg the default
# sqlite engine does not render.
WIRING_BACKSTAGE_NETPOL_FULL := $(WIRING_BACKSTAGE_NETPOL) --set backstage.database.engine=postgresql
# The same, with the chart owning the edge: the portal's route then attaches to
# the agentgateway data plane, not to the front Gateway.
WIRING_BACKSTAGE_NETPOL_EDGE := $(WIRING_BACKSTAGE_NETPOL) --set gatewayApi.gateway.create=true --set gatewayApi.gateway.tls.secretName=wildcard-tls --set ingress.parentRefs=null
# The platform Postgres Cluster's placement and pull secrets.
WIRING_PG := $(VM) --namespace agent-platform --set postgres.enabled=true
WIRING_PG_SET := $(WIRING_PG) --set 'postgres.imagePullSecrets[0].name=mirror-pull-secret' --set postgres.affinity.enablePodAntiAffinity=true --set postgres.affinity.topologyKey=topology.kubernetes.io/zone
# Every wired component on: the render the app-config assertions read.
WIRING_BACKSTAGE_FULL := $(WIRING_BACKSTAGE) --set components.kagent.enabled=true --set kagent.controllerRoute.enabled=true --set ingress.mode=agentgateway-muster --set components.agentgateway.enabled=true --set components.model-manager.enabled=true --set model-manager.ollama.endpoint=http://10.0.0.1:11434 --set modelManager.route.enabled=true --set gateway.jwksEgress.enabled=true
# The controller's JWKS egress: an agentgateway-* mode with the kagent controller
# route and its JWT policy on. JWKS_INCLUSTER keeps values.yaml's in-cluster host
# (dex.giantswarm.svc.cluster.local), which gateway.jwksEgress covers alone;
# JWKS_EXTERNAL points the same route at a Google-shaped host, which no
# in-cluster rule can reach, on 443 and with no jwks.tls key: the port implies
# TLS.
JWKS_BASE := $(VM) --namespace agent-platform --set ingress.mode=agentgateway-muster --set components.agentgateway.enabled=true --set components.kagent.enabled=true --set global.domain=ci.example.com --set kagent.controllerRoute.enabled=true --set kagent.controllerRoute.jwtAuthentication.enabled=true
JWKS_INCLUSTER := $(JWKS_BASE) --set global.identity.issuerUrl=https://dex.ci.example.com --set gateway.jwksEgress.enabled=true
# Nothing external at all: the platform's issuer in-cluster too (giantswarm/agent-platform#505
# admits global.identity.issuerUrl's host from the controller policy, so a public issuer is an
# external target whatever the routes name). This is the shape held against GOLDEN_REF.
JWKS_ISSUER_INCLUSTER := $(JWKS_BASE) --set global.identity.issuerUrl=https://dex.giantswarm.svc.cluster.local:5556 --set gateway.jwksEgress.enabled=true
# The platform's release beside a serving slice: the controller on, no route with a JWT policy
# of its own (kagent off), a public issuer. The slice's models policy in another namespace
# takes that issuer on 443, and only this release's policy governs the controller.
JWKS_PLATFORM := $(VM) --namespace agent-platform --set ingress.mode=agentgateway-muster --set components.agentgateway.enabled=true --set global.domain=ci.example.com --set global.identity.issuerUrl=https://dex.ci.example.com
JWKS_EXTERNAL := $(JWKS_BASE) --set global.identity.issuerUrl=https://accounts.google.com --set kagent.controllerRoute.jwtAuthentication.jwks.host=www.googleapis.com --set kagent.controllerRoute.jwtAuthentication.jwks.port=443
# The controller policy of one render, isolated from the rest of the manifest.
CTRL_POLICY := awk '/^  name: agent-platform-connectivity-controller$$/{f=1} f&&/^---$$/{exit} f'

.PHONY: verify-wiring
verify-wiring: ## Assert the standalone's ported wiring: toggles off = no object; on = the Backstage app-config (one-value identity), route and config-reload hook, the mcp-kubernetes MCPServer (OAuth, forwarded token, kube audience), the model serving objects on the kserve components and the guard without them, the KServe controller policies; the meta chart forwards the blocks, omits the wiring keys and renders no release for the switch.
	@echo "====> $@ ($(CONNECTIVITY_DIR), $(CHART_DIR))"
	@echo "--> toggles off: none of the ported objects renders"
	@helm template t $(CONNECTIVITY_DIR) $(WIRING_OFF) >/tmp/vw-off.out 2>&1 || { cat /tmp/vw-off.out; exit 1; }
	@for pattern in 'agent-platform-backstage-app-config' 'kind: MCPServer' 'serving.kserve.io' 'agent-platform-model-serving' 'name: hf-cache' 'kserve-controller' 'backstage-config-reload' 'kind: Job'; do \
		if grep -q -- "$$pattern" /tmp/vw-off.out; then echo "FAIL: toggles off but the render contains $$pattern"; exit 1; fi; \
	done
	@echo "ok: inert while off"
	@echo "--> Backstage on: the app-config ConfigMap the backstage: block mounts, derived from the platform's values"
	@helm template t $(CONNECTIVITY_DIR) $(WIRING_BACKSTAGE_FULL) >/tmp/vw-bs.out 2>&1 || { cat /tmp/vw-bs.out; exit 1; }
	@awk '/^kind: ConfigMap$$/,/^---/' /tmp/vw-bs.out | awk '/name: agent-platform-backstage-app-config$$/,/^---/' >/tmp/vw-bs-cm.out
	@[ -s /tmp/vw-bs-cm.out ] || { echo "FAIL: no ConfigMap agent-platform-backstage-app-config (the backstage: block's extraAppConfig mounts exactly this name)"; exit 1; }
	@for pattern in 'baseUrl: https://backstage.ci.example.com' 'metadataUrl: https://dex.ci.example.com/.well-known/openid-configuration' 'clientId: agent-platform' 'url: https://muster.ci.example.com/mcp' 'baseDomain: ci.example.com' '^        agent-platform:$$' 'name: agent-platform$$' 'fluxServiceAccountName: kagent-flux' 'apiBaseUrl: https://agentgateway.ci.example.com$$' 'apiBaseUrl: https://agentgateway.ci.example.com/model-manager' 'https://avatars.ci.example.com' 'repositories:' 'templates/agent-deployment/template.yaml' 'rootRedirect: /agent-platform'; do \
		grep -q -- "$$pattern" /tmp/vw-bs-cm.out || { echo "FAIL: the Backstage app-config lacks $$pattern"; exit 1; }; \
	done
	@if grep -q 'client: pg' /tmp/vw-bs-cm.out; then echo "FAIL: the pg database block rendered with the chart's sqlite default"; exit 1; fi
	@if grep -q 'musterMcpUrl' /tmp/vw-bs-cm.out; then echo "FAIL: agentPlatform.musterMcpUrl is back in the portal's app-config — the Dev Portal reads no such key (create_agent takes no muster argument); where muster is reaches agent-manager as muster.url (verify-identity)"; exit 1; fi
	@grep -q 'configMapRef: agent-platform-backstage-app-config' $(CHART_DIR)/values.yaml || { echo "FAIL: the meta chart's backstage: block no longer mounts the ConfigMap this chart renders"; exit 1; }
	@echo "ok: app-config"
	@echo "--> the one-value identity: renaming kagent.fluxServiceAccountName renames the portal's agentPlatform.fluxServiceAccountName; kagent off drops it"
	@helm template t $(CONNECTIVITY_DIR) $(WIRING_BACKSTAGE) --set components.kagent.enabled=true --set kagent.fluxServiceAccountName=tenant-x 2>/dev/null | grep -q 'fluxServiceAccountName: tenant-x' || { echo "FAIL: the app-config does not follow kagent.fluxServiceAccountName"; exit 1; }
	@helm template t $(CONNECTIVITY_DIR) $(WIRING_BACKSTAGE) >/tmp/vw-bs-nokagent.out 2>&1 || { cat /tmp/vw-bs-nokagent.out; exit 1; }
	@if grep -q 'fluxServiceAccountName' /tmp/vw-bs-nokagent.out; then echo "FAIL: agentPlatform.fluxServiceAccountName rendered with kagent off"; exit 1; fi
	@echo "ok: one-value identity"
	@echo "--> Backstage on: the route, the pg block, the installation name, the config-reload hook and its network policy in both flavors"
	@awk '/^kind: HTTPRoute$$/,/^---/' /tmp/vw-bs.out | awk '/^  name: backstage$$/,/^---/' >/tmp/vw-bs-route.out
	@grep -q '"backstage.ci.example.com"' /tmp/vw-bs-route.out || { echo "FAIL: the Backstage HTTPRoute lacks the derived hostname"; exit 1; }
	@grep -q 'name: giantswarm-default' /tmp/vw-bs-route.out || { echo "FAIL: the Backstage HTTPRoute does not attach to global.gatewayApi.parentRefs"; exit 1; }
	@grep -A1 'backendRefs:' /tmp/vw-bs-route.out | grep -q 'name: backstage' || { echo "FAIL: the Backstage HTTPRoute does not target the backstage Service"; exit 1; }
	@helm template t $(CONNECTIVITY_DIR) $(WIRING_BACKSTAGE) --set backstage.database.engine=postgresql --set backstage.installationName=lab --set backstage.hostname=portal.example.org 2>/dev/null >/tmp/vw-bs-pg.out
	@grep -q 'client: pg' /tmp/vw-bs-pg.out || { echo "FAIL: backstage.database.engine=postgresql did not render the pg block"; exit 1; }
	@grep -q '^        lab:$$' /tmp/vw-bs-pg.out || { echo "FAIL: backstage.installationName does not key gs.installations"; exit 1; }
	@grep -q 'url: https://muster.ci.example.com/mcp' /tmp/vw-bs-pg.out && grep -q '"portal.example.org"' /tmp/vw-bs-pg.out || { echo "FAIL: backstage.hostname override lost"; exit 1; }
	@awk '/^kind: Job$$/,/^---/' /tmp/vw-bs.out >/tmp/vw-bs-job.out
	@grep -q 'helm.sh/hook: post-install,post-upgrade' /tmp/vw-bs-job.out || { echo "FAIL: the config-reload Job is not a post-install/post-upgrade hook"; exit 1; }
	@grep -q -- '--selector=app=backstage' /tmp/vw-bs-job.out || { echo "FAIL: the config-reload Job does not select the Backstage Deployment by label (a missing Deployment must be a no-op)"; exit 1; }
	@grep -qE 'AGENT_PLATFORM_APP_CONFIG_CHECKSUM=[0-9a-f]{64}' /tmp/vw-bs-job.out || { echo "FAIL: the config-reload Job carries no app-config checksum"; exit 1; }
	@echo "--> the checksum covers the app-config data only: a chart-version bump keeps it, an app-config change moves it (#424)"
	@rm -rf /tmp/vw-bs-vbump && cp -r $(CONNECTIVITY_DIR) /tmp/vw-bs-vbump && sed -i 's/^version: .*/version: 0.0.0-verify/' /tmp/vw-bs-vbump/Chart.yaml
	@helm template t /tmp/vw-bs-vbump $(WIRING_BACKSTAGE_FULL) >/tmp/vw-bs-vbump.out 2>&1 || { cat /tmp/vw-bs-vbump.out; exit 1; }
	@grep -q 'helm.sh/chart: "agent-platform-connectivity-0.0.0-verify"' /tmp/vw-bs-vbump.out || { echo "FAIL: the version bump did not reach the rendered labels (the assertion below would pass vacuously)"; exit 1; }
	@base=$$(grep -o 'AGENT_PLATFORM_APP_CONFIG_CHECKSUM=[0-9a-f]*' /tmp/vw-bs-job.out); \
	bump=$$(grep -o 'AGENT_PLATFORM_APP_CONFIG_CHECKSUM=[0-9a-f]*' /tmp/vw-bs-vbump.out); \
	[ "$$base" = "$$bump" ] || { echo "FAIL: the app-config checksum moved on a chart-version bump alone ($$base vs $$bump): every release would roll the portal"; exit 1; }; \
	for change in 'backstage.installationName=other' 'kagent.fluxServiceAccountName=tenant-x' 'global.identity.issuerUrl=https://idp.example.org'; do \
		moved=$$(helm template t $(CONNECTIVITY_DIR) $(WIRING_BACKSTAGE_FULL) --set "$$change" 2>/dev/null | grep -o 'AGENT_PLATFORM_APP_CONFIG_CHECKSUM=[0-9a-f]*'); \
		[ -n "$$moved" ] && [ "$$moved" != "$$base" ] || { echo "FAIL: the app-config checksum did not move on --set $$change"; exit 1; }; \
	done
	@echo "ok: checksum follows the app-config data only"
	@grep -q 'kind: CiliumNetworkPolicy' /tmp/vw-bs.out && grep -q 'agent-platform-connectivity-backstage-config-reload' /tmp/vw-bs.out || { echo "FAIL: no cilium policy for the config-reload Job"; exit 1; }
	@helm template t $(CONNECTIVITY_DIR) $(WIRING_BACKSTAGE) --set networkPolicy.flavor=kubernetes 2>/dev/null | awk '/^kind: NetworkPolicy$$/,/^---/' | grep -q 'agent-platform-connectivity-backstage-config-reload' || { echo "FAIL: no kubernetes policy for the config-reload Job"; exit 1; }
	@helm template t $(CONNECTIVITY_DIR) $(WIRING_BACKSTAGE) --set backstage.configReload.enabled=false >/tmp/vw-bs-noreload.out 2>&1 || { cat /tmp/vw-bs-noreload.out; exit 1; }
	@if grep -q 'backstage-config-reload' /tmp/vw-bs-noreload.out; then echo "FAIL: configReload.enabled=false still renders the hook"; exit 1; fi
	@echo "ok: route, pg block, installation name, config-reload hook"
	@echo "--> Backstage on without global.domain fails, naming it"
	@if helm template t $(CONNECTIVITY_DIR) $(VM) --set components.backstage.enabled=true --set global.identity.issuerUrl=https://dex.ci.example.com --set 'global.gatewayApi.parentRefs[0].name=gw' --set 'global.gatewayApi.parentRefs[0].namespace=gw-system' >/tmp/vw-bs-nodomain.out 2>&1; then \
		echo "FAIL: Backstage on with no global.domain rendered"; exit 1; \
	elif ! grep -q "global.domain is empty" /tmp/vw-bs-nodomain.out; then \
		echo "FAIL: the Backstage domain guard failed for the wrong reason"; cat /tmp/vw-bs-nodomain.out; exit 1; \
	else echo "ok: Backstage domain guard"; fi
	@echo "--> mcp-kubernetes on: the MCPServer with OAuth, the forwarded token and the kube audience; OAuth off drops the auth block; an empty audience drops requiredAudiences; muster off drops the CR"
	@helm template t $(CONNECTIVITY_DIR) $(VM) --namespace agent-platform --set components.mcp-kubernetes.enabled=true >/tmp/vw-mcpk.out 2>&1 || { cat /tmp/vw-mcpk.out; exit 1; }
	@awk '/^kind: MCPServer$$/,/^---/' /tmp/vw-mcpk.out >/tmp/vw-mcpk-cr.out
	@for pattern in '^  name: mcp-kubernetes$$' 'muster.giantswarm.io/type: mcp-kubernetes' 'agent-platform.giantswarm.io/tool-group: infrastructure' 'url: http://mcp-kubernetes.agent-platform.svc.cluster.local:8080/mcp' 'type: oauth' 'forwardToken: true' '- dex-k8s-authenticator'; do \
		grep -q -- "$$pattern" /tmp/vw-mcpk-cr.out || { echo "FAIL: the mcp-kubernetes MCPServer lacks $$pattern"; exit 1; }; \
	done
	@helm template t $(CONNECTIVITY_DIR) $(VM) --set components.mcp-kubernetes.enabled=true --set mcp-kubernetes.mcpKubernetes.oauth.enabled=false 2>/dev/null | awk '/^kind: MCPServer$$/,/^---/' >/tmp/vw-mcpk-noauth.out
	@if grep -q 'forwardToken' /tmp/vw-mcpk-noauth.out; then echo "FAIL: the MCPServer carries an auth block with the server's OAuth off"; exit 1; fi
	@helm template t $(CONNECTIVITY_DIR) $(VM) --set components.mcp-kubernetes.enabled=true --set mcp-kubernetes.kubernetesAudience= 2>/dev/null | awk '/^kind: MCPServer$$/,/^---/' >/tmp/vw-mcpk-noaud.out
	@if grep -q 'requiredAudiences' /tmp/vw-mcpk-noaud.out; then echo "FAIL: an empty kubernetesAudience still renders requiredAudiences"; exit 1; fi
	@grep -q 'forwardToken: true' /tmp/vw-mcpk-noaud.out || { echo "FAIL: the auth block went with the audience"; exit 1; }
	@helm template t $(CONNECTIVITY_DIR) $(VM) --set components.mcp-kubernetes.enabled=true --set components.muster.enabled=false >/tmp/vw-mcpk-nomuster.out 2>&1 || { cat /tmp/vw-mcpk-nomuster.out; exit 1; }
	@if grep -q 'kind: MCPServer' /tmp/vw-mcpk-nomuster.out; then echo "FAIL: the MCPServer renders with muster off (no CRD to map to)"; exit 1; fi
	@echo "ok: mcp-kubernetes MCPServer"
	@echo "--> modelServing on without the llm-d components (and no LLMInferenceService API) fails, naming the toggles; requireApi=false and a served API pass; the classic keys are refused"
	@if helm template t $(CONNECTIVITY_DIR) $(VM) --set components.modelServing.enabled=true >/tmp/vw-ms-guard.out 2>&1; then \
		echo "FAIL: modelServing rendered without the llm-d control plane"; exit 1; \
	elif ! grep -q "turn on components.kserve-llmisvc-crd, components.kserve-llmisvc-resources and components.kserve-runtime-configs" /tmp/vw-ms-guard.out; then \
		echo "FAIL: the modelServing guard failed for the wrong reason"; cat /tmp/vw-ms-guard.out; exit 1; \
	else echo "ok: modelServing needs the llm-d components"; fi
	@helm template t $(CONNECTIVITY_DIR) $(VM) --set components.modelServing.enabled=true --set modelServing.kserve.requireApi=false >/dev/null 2>&1 || { echo "FAIL: modelServing.kserve.requireApi=false must skip the check"; exit 1; }
	@helm template t $(CONNECTIVITY_DIR) $(VM) --set components.modelServing.enabled=true --api-versions serving.kserve.io/v1alpha2 >/dev/null 2>&1 || { echo "FAIL: a cluster that serves the LLMInferenceService API must satisfy the guard without the components"; exit 1; }
	@if helm template t $(CONNECTIVITY_DIR) $(VM) --set components.modelServing.enabled=true --api-versions serving.kserve.io/v1alpha1 --api-versions serving.kserve.io/v1beta1 >/dev/null 2>&1; then echo "FAIL: the classic KServe APIs alone must not satisfy the llm-d guard"; exit 1; fi
	$(call managers_must_fail,classic modelServing.runtime refused,$(WIRING_SERVING) --set modelServing.runtime.name=kserve-vllm,its keys are refused: modelServing.runtime)
	$(call managers_must_fail,classic additionalRuntimes refused,$(WIRING_SERVING) --set-json 'modelServing.additionalRuntimes=[{"name":"x"}]',modelServing.additionalRuntimes)
	$(call managers_must_fail,classic networkPolicy.predictor refused,$(WIRING_SERVING) --set modelServing.networkPolicy.predictor.port=8080,modelServing.networkPolicy.predictor)
	$(call managers_must_fail,classic serving.timeoutSeconds refused,$(WIRING_SERVING) --set modelServing.serving.timeoutSeconds=1800,modelServing.serving.timeoutSeconds)
	$(call managers_must_fail,classic components.kserve-resources refused,$(WIRING_SERVING) --set components.kserve-resources.enabled=true,its keys are refused: components.kserve-resources)
	$(call managers_must_fail,classic kserve-crd values block refused by the schema,$(WIRING_SERVING) --set kserve-crd.crd.keep=true,kserve-crd.*not allowed)
	@echo "ok: modelServing prerequisite guard; the classic keys are refused naming them"
	@echo "--> modelServing + llm-d components on (fleet shape, kagent on): namespace, discovery ConfigMap, presets, chat template, the cache claim's hook Job (no PVC object: #483) and its identity, the two Kyverno policies, the cilium policies incl. the agent egress, the llm-d controller policy; no serving.kserve.io object"
	@helm template t $(CONNECTIVITY_DIR) $(WIRING_SERVING) --set components.kagent.enabled=true >/tmp/vw-ms.out 2>&1 || { cat /tmp/vw-ms.out; exit 1; }
	@for pattern in '^  name: agent-platform-model-serving$$' '^  name: t-model-serving-cache$$' 'helm.sh/hook: post-install,post-upgrade' '"kind":"PersistentVolumeClaim"' '"name":"hf-cache","namespace":"model-serving"' 'kubectl apply --server-side' 'resources: \["persistentvolumeclaims"\]' 'agent-platform-serving-preset-qwen3-8-27b' 'agent-platform-serving-preset-gemma-4-12b' 'name: agent-platform-chat-template-qwen3-8-27b' '^  name: model-serving$$' 'kind: Namespace' 'name: agent-platform-connectivity-model-serving-pods' 'name: agent-platform-connectivity-model-serving-deployments' 'redirectPolicy: true' 'name: agent-platform-connectivity-model-serving-llmisvc-workload$$' 'name: agent-platform-connectivity-model-serving-download$$' 'name: agent-platform-connectivity-kagent-agents-to-model-serving' 'matchName: huggingface.co' 'matchPattern: "\*"' '- remote-node' 'name: agent-platform-connectivity-llmisvc-controller' 'control-plane: llmisvc-controller-manager' 'flavor: cilium' 'preset-source: "shipped"'; do \
		grep -q -e "$$pattern" /tmp/vw-ms.out || { echo "FAIL: the model serving render lacks $$pattern"; exit 1; }; \
	done
	@if grep -qE 'serving\.kserve\.io|ClusterServingRuntime|kind: InferenceService|kserve-controller-manager|spec\.runtime|^      runtimes?:' /tmp/vw-ms.out; then echo "FAIL: the serving render carries a classic serving object or key"; grep -nE 'serving\.kserve\.io|ClusterServingRuntime|InferenceService|kserve-controller-manager|runtimes?:' /tmp/vw-ms.out | head; exit 1; fi
	@[ "$$(grep -c 'agent-platform.giantswarm.io/serving-preset: "true"' /tmp/vw-ms.out)" = "13" ] || { echo "FAIL: expected the 13 shipped presets, got $$(grep -c 'agent-platform.giantswarm.io/serving-preset: "true"' /tmp/vw-ms.out)"; exit 1; }
	@if grep -q 'kind: NetworkPolicy' /tmp/vw-ms.out; then echo "FAIL: a kubernetes NetworkPolicy rendered under the cilium flavor"; exit 1; fi
	@if grep -q '^kind: PersistentVolumeClaim' /tmp/vw-ms.out; then echo "FAIL: the cache claim rendered as a release resource (Helm's wait would wait for a Bind only the first predictor brings: #483)"; exit 1; fi
	@echo "ok: model serving fleet shape"
	@echo "--> the vanilla shape (no served API groups): kubernetes policies, no Kyverno object, no Cilium object; policies.enabled=true without Kyverno fails"
	@helm template t $(CONNECTIVITY_DIR) --set 'ingress.parentRefs[0].name=x' --namespace agent-platform --set components.modelServing.enabled=true --set components.kserve-llmisvc-crd.enabled=true --set components.kserve-llmisvc-resources.enabled=true --set components.kagent.enabled=true >/tmp/vw-ms-vanilla.out 2>&1 || { cat /tmp/vw-ms-vanilla.out; exit 1; }
	@if grep -qE 'kyverno.io|cilium.io' /tmp/vw-ms-vanilla.out; then echo "FAIL: the vanilla render carries a Kyverno or Cilium object"; exit 1; fi
	@for pattern in 'name: agent-platform-connectivity-model-serving-llmisvc-workload-ingress' 'name: agent-platform-connectivity-model-serving-llmisvc-workload-egress' 'name: agent-platform-connectivity-model-serving-download-egress' 'name: agent-platform-connectivity-llmisvc-controller' 'redirectPolicy: false' 'flavor: kubernetes' 'Hugging Face: vanilla NetworkPolicy has no FQDN selector'; do \
		grep -q -e "$$pattern" /tmp/vw-ms-vanilla.out || { echo "FAIL: the vanilla model serving render lacks $$pattern"; exit 1; }; \
	done
	@if helm template t $(CONNECTIVITY_DIR) --set 'ingress.parentRefs[0].name=x' --set components.modelServing.enabled=true --set components.kserve-llmisvc-crd.enabled=true --set components.kserve-llmisvc-resources.enabled=true --set modelServing.policies.enabled=true >/tmp/vw-ms-pol.out 2>&1; then \
		echo "FAIL: modelServing.policies.enabled=true without Kyverno rendered"; exit 1; \
	elif ! grep -q "modelServing.policies.enabled is true but kyvernoPolicies.enabled resolves to false" /tmp/vw-ms-pol.out; then \
		echo "FAIL: the policies guard failed for the wrong reason"; cat /tmp/vw-ms-pol.out; exit 1; \
	else echo "ok: vanilla shape + policies guard"; fi
	@echo "--> presets: a values preset replaces a shipped one, an existing claim drops the PVC, shippedPresets.enabled=false drops the set, a bad preset fails"
	@helm template t $(CONNECTIVITY_DIR) $(WIRING_SERVING) --set-json 'modelServing.presets=[{"apiVersion":"agent-platform.giantswarm.io/v1alpha1","kind":"ServingPreset","metadata":{"name":"gemma-4-12b"},"spec":{"displayName":"Overridden","model":{"id":"google/gemma-4-12B-it-qat-w4a16-ct","storageUri":"oci://gsoci.azurecr.io/giantswarm/models/gemma-4-12b-qat-w4a16:1d2c2d7f2466"},"chatTemplate":{"content":"{{ messages }}"},"requirements":{"weightsGiB":10}}}]' --set modelServing.cache.pvc.existingClaim=models >/tmp/vw-ms-presets.out 2>&1 || { cat /tmp/vw-ms-presets.out; exit 1; }
	@grep -q 'displayName: Overridden' /tmp/vw-ms-presets.out || { echo "FAIL: a values preset did not replace the shipped one"; exit 1; }
	@grep -q 'preset-source: "values"' /tmp/vw-ms-presets.out || { echo "FAIL: the values preset is not labelled as such"; exit 1; }
	@grep -q 'name: agent-platform-chat-template-gemma-4-12b' /tmp/vw-ms-presets.out || { echo "FAIL: the inline chat template ConfigMap is missing"; exit 1; }
	@grep -q -- '--chat-template=/mnt/chat-template/chat-template.jinja' /tmp/vw-ms-presets.out || { echo "FAIL: the --chat-template flag was not appended"; exit 1; }
	@if grep -q 'PersistentVolumeClaim' /tmp/vw-ms-presets.out; then echo "FAIL: a claim (or its hook) rendered next to an existing claim"; exit 1; fi
	@grep -q 'claimName: models' /tmp/vw-ms-presets.out || { echo "FAIL: the existing claim is not published"; exit 1; }
	@helm template t $(CONNECTIVITY_DIR) $(WIRING_SERVING) --set modelServing.shippedPresets.enabled=false 2>/dev/null >/tmp/vw-ms-noship.out; if grep -q 'serving-preset: "true"' /tmp/vw-ms-noship.out; then echo "FAIL: shipped presets rendered while disabled"; exit 1; fi
	@if helm template t $(CONNECTIVITY_DIR) $(WIRING_SERVING) --set 'modelServing.presets[0].metadata.name=bad' >/dev/null 2>&1; then echo "FAIL: a preset without spec was accepted"; exit 1; fi
	@echo "ok: presets"
	@echo "--> the model-manager kserve backend must agree with the modelServing layer"
	@if helm template t $(CONNECTIVITY_DIR) $(WIRING_SERVING) --set components.kagent.enabled=true --set components.model-manager.enabled=true --set model-manager.backend=kserve --set model-manager.oauth.enabled=false --set model-manager.kserve.namespace=other >/tmp/vw-mm.out 2>&1; then \
		echo "FAIL: a model-manager kserve namespace that differs from modelServing.namespace.name rendered"; exit 1; \
	elif ! grep -q "must equal modelServing.namespace.name" /tmp/vw-mm.out; then \
		echo "FAIL: the model-manager/modelServing guard failed for the wrong reason"; cat /tmp/vw-mm.out; exit 1; \
	else echo "ok: model-manager agrees with modelServing"; fi
	@helm template t $(CONNECTIVITY_DIR) $(WIRING_SERVING) --set components.kagent.enabled=true --set components.model-manager.enabled=true --set model-manager.backend=kserve --set model-manager.oauth.enabled=false --set model-manager.kserve.namespace=model-serving --set model-manager.kserve.discovery.configMap=agent-platform-model-serving >/dev/null 2>&1 || { echo "FAIL: an agreeing model-manager kserve backend must pass"; exit 1; }
	@echo "--> the llm-d component guards: a non-Standard deployment mode, the shared objects switched off"
	$(call managers_must_fail,deployment mode must be Standard,$(VM) --set components.kserve-llmisvc-resources.enabled=true --set kserve-llmisvc-resources.kserve.controller.deploymentMode=Knative,must be Standard)
	$(call managers_must_fail,shared resources stay on,$(VM) --set components.kserve-llmisvc-resources.enabled=true --set kserve-llmisvc-resources.kserve.createSharedResources=false,createSharedResources must stay true)
	@echo "--> the meta chart: the switch renders no release, the roster and the blocks reach connectivity, the wiring keys never reach the component charts, the policies knob arrives resolved, backstage dependsOn connectivity, connectivity dependsOn muster"
	@helm template t $(CHART_DIR) -f $(CHART_DIR)/ci/ci-values.yaml $(FLEET_APIS) --set components.modelServing.enabled=true --set components.backstage.enabled=true --set components.mcp-kubernetes.enabled=true --set components.kserve-llmisvc-crd.enabled=true --set components.kserve-llmisvc-resources.enabled=true >/tmp/vw-meta.out 2>&1 || { cat /tmp/vw-meta.out; exit 1; }
	@if helm template t $(CHART_DIR) -f $(CHART_DIR)/ci/ci-values.yaml --set components.kserve-crd.enabled=true >/tmp/vw-meta-classic.out 2>&1; then \
		echo "FAIL: the meta chart accepted components.kserve-crd (the classic KServe controller was removed; a chartless entry would pass as a feature switch)"; exit 1; \
	elif ! grep -q "its keys are refused: components.kserve-crd" /tmp/vw-meta-classic.out; then \
		echo "FAIL: the meta chart's classic-component guard failed for the wrong reason"; cat /tmp/vw-meta-classic.out; exit 1; \
	else echo "ok: the meta chart refuses components.kserve-crd naming it"; fi
	@if grep -qE '^  name: modelServing$$' /tmp/vw-meta.out; then echo "FAIL: components.modelServing rendered a release; it is a feature switch"; exit 1; fi
	@awk '/^kind: HelmRelease$$/{h=1} h&&/^  name: agent-platform-connectivity$$/{f=1} f&&/^---/{exit} f' /tmp/vw-meta.out >/tmp/vw-meta-conn.out
	@grep -A1 '^      modelServing:$$' /tmp/vw-meta-conn.out | grep -q 'enabled: true' || { echo "FAIL: the roster forwarded to connectivity does not carry modelServing: enabled: true"; exit 1; }
	@for block in backstage mcp-kubernetes modelServing kserve-llmisvc-resources; do \
		grep -qE "^    $$block:" /tmp/vw-meta-conn.out || { echo "FAIL: the $$block block is held back from the connectivity release"; exit 1; }; \
	done
	@grep -q 'kubernetesAudience: dex-k8s-authenticator' /tmp/vw-meta-conn.out || { echo "FAIL: mcp-kubernetes.kubernetesAudience did not reach the connectivity release"; exit 1; }
	@grep -q 'installationName: agent-platform' /tmp/vw-meta-conn.out || { echo "FAIL: backstage.installationName did not reach the connectivity release"; exit 1; }
	@awk '/^      policies:$$/{f=1;next} f&&/^      [a-z]/{f=0} f' /tmp/vw-meta-conn.out | grep -q '^        enabled: true' || { echo "FAIL: modelServing.policies.enabled did not arrive resolved (true with kyverno.io served)"; exit 1; }
	@if grep -q 'enabled: auto' /tmp/vw-meta-conn.out; then echo "FAIL: an unresolved auto reached the connectivity release"; exit 1; fi
	@grep -A6 '^  dependsOn:' /tmp/vw-meta-conn.out | grep -q 'name: muster' || { echo "FAIL: connectivity does not dependsOn muster (its MCPServer needs the CRD)"; exit 1; }
	@awk '/^kind: HelmRelease$$/{h=1} h&&/^  name: backstage$$/{f=1} f&&/^---/{exit} f' /tmp/vw-meta.out >/tmp/vw-meta-bs.out
	@for key in hostname parentRefs installationName extraScopes startUrlSearchParams enabledExtensions disabledExtensions skillsRepositories catalogs configReload; do \
		if grep -qE "^    $$key:" /tmp/vw-meta-bs.out; then echo "FAIL: the wiring key $$key reached the backstage chart, whose schema rejects it"; exit 1; fi; \
	done
	@grep -A3 '^  dependsOn:' /tmp/vw-meta-bs.out | grep -q 'name: agent-platform-connectivity' || { echo "FAIL: backstage does not dependsOn connectivity (its pod mounts the app-config rendered there)"; exit 1; }
	@awk '/^kind: HelmRelease$$/{h=1} h&&/^  name: mcp-kubernetes$$/{f=1} f&&/^---/{exit} f' /tmp/vw-meta.out >/tmp/vw-meta-mcpk.out
	@if grep -q 'kubernetesAudience' /tmp/vw-meta-mcpk.out; then echo "FAIL: kubernetesAudience reached the mcp-kubernetes chart, whose schema rejects it"; exit 1; fi
	@echo "ok: meta forwards"
	@echo "--> the switch off (the fleet): the modelServing block is NOT forwarded (a live connectivity chart that predates it would reject it); on, it arrives with the policies knob resolved to false on the vanilla render"
	@helm template t $(CHART_DIR) -f $(CHART_DIR)/ci/ci-values.yaml $(FLEET_APIS) 2>/dev/null | awk '/^kind: HelmRelease$$/{h=1} h&&/^  name: agent-platform-connectivity$$/{f=1} f&&/^---/{exit} f' >/tmp/vw-meta-off.out
	@if grep -qE '^    modelServing:' /tmp/vw-meta-off.out; then echo "FAIL: the modelServing block is forwarded while the switch is off"; exit 1; fi
	@grep -A1 '^      modelServing:$$' /tmp/vw-meta-off.out | grep -q 'enabled: false' || { echo "FAIL: the roster forwarded to connectivity lacks modelServing: enabled: false"; exit 1; }
	@helm template t $(CHART_DIR) -f $(CHART_DIR)/ci/ci-values.yaml --set components.modelServing.enabled=true 2>/dev/null | awk '/^kind: HelmRelease$$/{h=1} h&&/^  name: agent-platform-connectivity$$/{f=1} f&&/^---/{exit} f' | awk '/^      policies:$$/{f=1;next} f&&/^      [a-z]/{f=0} f' | grep -q '^        enabled: false' || { echo "FAIL: policies knob not false on the vanilla render with the switch on"; exit 1; }
	@echo "ok: switch block forwarded only while on, policies knob resolved"
	@echo "--> the platform Postgres Cluster: imagePullSecrets and affinity reach it; unset renders no field"
	@helm template t $(CONNECTIVITY_DIR) $(WIRING_PG_SET) 2>/dev/null | awk '/^kind: Cluster$$/,/^---/' >/tmp/vw-pg-set.out
	@grep -q 'name: mirror-pull-secret' /tmp/vw-pg-set.out || { echo "FAIL: postgres.imagePullSecrets did not reach the Cluster"; cat /tmp/vw-pg-set.out; exit 1; }
	@grep -q 'enablePodAntiAffinity: true' /tmp/vw-pg-set.out || { echo "FAIL: postgres.affinity did not reach the Cluster"; exit 1; }
	@grep -q 'topologyKey: topology.kubernetes.io/zone' /tmp/vw-pg-set.out || { echo "FAIL: postgres.affinity.topologyKey did not reach the Cluster"; exit 1; }
	@helm template t $(CONNECTIVITY_DIR) $(WIRING_PG) 2>/dev/null | awk '/^kind: Cluster$$/,/^---/' >/tmp/vw-pg-unset.out
	@for key in imagePullSecrets affinity; do \
		if grep -qE "^  $$key:" /tmp/vw-pg-unset.out; then echo "FAIL: the Cluster renders $$key while it is unset"; exit 1; fi; \
	done
	@if [ -n "$(GOLDEN_REF)" ] && git rev-parse --verify -q $(GOLDEN_REF) >/dev/null; then \
		rm -rf /tmp/vw-pg-ref && git worktree add -q --detach /tmp/vw-pg-ref $(GOLDEN_REF) && \
		for flavor in cilium kubernetes; do \
			helm template t $(CONNECTIVITY_DIR) $(WIRING_PG) $(WIRING_PG_GOLDEN_HOLD) --set networkPolicy.flavor=$$flavor 2>/dev/null >/tmp/vw-pg-new-$$flavor.out; \
			helm template t /tmp/vw-pg-ref/$(CONNECTIVITY_DIR) $(WIRING_PG) $(WIRING_PG_GOLDEN_HOLD) --set networkPolicy.flavor=$$flavor 2>/dev/null >/tmp/vw-pg-old-$$flavor.out; \
			python3 -c 'import re,sys; ex=set(sys.argv[2].split()); docs=open(sys.argv[1]).read().split("\n---\n"); keep=[d for d in docs if not (re.search(r"^  name: (\S+)", d, re.M) and re.search(r"^  name: (\S+)", d, re.M).group(1) in ex)]; out="\n---\n".join(keep).lstrip("-\n"); open(sys.argv[1],"w").write("---\n"+out.rstrip("\n")+"\n")' /tmp/vw-pg-new-$$flavor.out "$(DASHBOARDS_GOLDEN_DROP)"; \
			python3 -c 'import re,sys; ex=set(sys.argv[2].split()); docs=open(sys.argv[1]).read().split("\n---\n"); keep=[d for d in docs if not (re.search(r"^  name: (\S+)", d, re.M) and re.search(r"^  name: (\S+)", d, re.M).group(1) in ex)]; out="\n---\n".join(keep).lstrip("-\n"); open(sys.argv[1],"w").write("---\n"+out.rstrip("\n")+"\n")' /tmp/vw-pg-old-$$flavor.out "$(DASHBOARDS_GOLDEN_DROP)"; \
			diff -u /tmp/vw-pg-old-$$flavor.out /tmp/vw-pg-new-$$flavor.out || { echo "FAIL: the $$flavor render changed with postgres.imagePullSecrets and .affinity unset"; git worktree remove --force /tmp/vw-pg-ref; exit 1; }; \
		done; \
		git worktree remove --force /tmp/vw-pg-ref; \
		echo "ok: unset = byte-identical against $(GOLDEN_REF), both flavors"; \
	else echo "skipped: no GOLDEN_REF"; fi
	@echo "--> the Backstage app pods' network policy renders in both flavors with the component and the policies on"
	@helm template t $(CONNECTIVITY_DIR) $(WIRING_BACKSTAGE_NETPOL) --set networkPolicy.flavor=cilium 2>/dev/null | awk '/^  name: agent-platform-connectivity-backstage$$/{f=1} f&&/^---$$/{exit} f' >/tmp/vw-bsnp-cilium.out
	@[ -s /tmp/vw-bsnp-cilium.out ] || { echo "FAIL: no cilium policy for the Backstage app pods"; exit 1; }
	@for pattern in 'app: backstage' 'component: backstage' 'port: "7007"' '- host' '- remote-node' 'k8s-app: kube-dns' 'kube-apiserver' 'matchName: dex.ci.example.com' 'app.kubernetes.io/name: muster'; do \
		grep -q -- "$$pattern" /tmp/vw-bsnp-cilium.out || { echo "FAIL: the cilium Backstage policy lacks $$pattern"; cat /tmp/vw-bsnp-cilium.out; exit 1; }; \
	done
	@echo "--> the ingress peer follows the Gateway the portal's route attaches to"
	@grep -q 'io.kubernetes.pod.namespace: envoy-gateway-system' /tmp/vw-bsnp-cilium.out || { echo "FAIL: the cilium Backstage policy does not admit the front Gateway that serves its route"; cat /tmp/vw-bsnp-cilium.out; exit 1; }
	@if grep -q 'gateway.networking.k8s.io/gateway-name' /tmp/vw-bsnp-cilium.out; then echo "FAIL: the cilium Backstage policy admits the agentgateway data plane, which is not in the route's path"; exit 1; fi
	@helm template t $(CONNECTIVITY_DIR) $(WIRING_BACKSTAGE_NETPOL_EDGE) --set networkPolicy.flavor=cilium 2>/dev/null | awk '/^  name: agent-platform-connectivity-backstage$$/{f=1} f&&/^---$$/{exit} f' >/tmp/vw-bsnp-edge.out
	@grep -q 'gateway.networking.k8s.io/gateway-name: agentgateway' /tmp/vw-bsnp-edge.out || { echo "FAIL: with the chart owning the edge the policy does not admit the data plane"; cat /tmp/vw-bsnp-edge.out; exit 1; }
	@if grep -q 'envoy-gateway-system' /tmp/vw-bsnp-edge.out; then echo "FAIL: with the chart owning the edge the policy still admits the front Gateway"; exit 1; fi
	@echo "--> the edge namespace follows the Gateway the route names as its parent, in both flavors"
	@for flavor in cilium kubernetes; do \
		helm template t $(CONNECTIVITY_DIR) $(WIRING_BACKSTAGE_NETPOL) --set networkPolicy.flavor=$$flavor --set 'global.gatewayApi.parentRefs[0].name=edge' --set 'global.gatewayApi.parentRefs[0].namespace=ingress' 2>/dev/null | awk '/^  name: agent-platform-connectivity-backstage$$/{f=1} f&&/^---$$/{exit} f' | grep -E '^ *(io.kubernetes.pod.namespace|kubernetes.io/metadata.name): ' >/tmp/vw-bsnp-ns-$$flavor.out; \
		grep -q ': ingress$$' /tmp/vw-bsnp-ns-$$flavor.out || { echo "FAIL: the $$flavor Backstage ingress policy pins envoy-gateway-system instead of the parentRef's namespace"; cat /tmp/vw-bsnp-ns-$$flavor.out; exit 1; }; \
		if grep -q 'envoy-gateway-system' /tmp/vw-bsnp-ns-$$flavor.out; then echo "FAIL: the $$flavor Backstage ingress policy still names envoy-gateway-system with the parentRef elsewhere"; exit 1; fi; \
	done
	@helm template t $(CONNECTIVITY_DIR) $(WIRING_BACKSTAGE_NETPOL) --set networkPolicy.flavor=cilium --set 'backstage.parentRefs[0].name=own' --set 'backstage.parentRefs[0].namespace=portal-edge' 2>/dev/null | awk '/^  name: agent-platform-connectivity-backstage$$/{f=1} f&&/^---$$/{exit} f' | grep -q 'io.kubernetes.pod.namespace: portal-edge' || { echo "FAIL: a route pinned with backstage.parentRefs does not move the policy's edge namespace"; exit 1; }
	@echo "--> the egress edge follows the routes the app-config calls, not the portal's own route"
	@helm template t $(CONNECTIVITY_DIR) $(WIRING_BACKSTAGE_NETPOL) --set networkPolicy.flavor=kubernetes --set 'backstage.parentRefs[0].name=own' --set 'backstage.parentRefs[0].namespace=portal-edge' 2>/dev/null | awk '/^  name: agent-platform-connectivity-backstage-egress$$/{f=1} f&&/^---$$/{exit} f' >/tmp/vw-bsnp-k8s-eg-pin.out
	@grep -q 'kubernetes.io/metadata.name: envoy-gateway-system' /tmp/vw-bsnp-k8s-eg-pin.out || { echo "FAIL: the kubernetes Backstage egress leg to the edge does not follow ingress.parentRefs"; cat /tmp/vw-bsnp-k8s-eg-pin.out; exit 1; }
	@if grep -q 'portal-edge' /tmp/vw-bsnp-k8s-eg-pin.out; then echo "FAIL: the kubernetes Backstage egress leg follows backstage.parentRefs, which moves the portal's own route and not the routes the app-config calls"; exit 1; fi
	@echo "--> backstage.parentRefs wins over the chart-owned edge, as it does for the route, in both flavors"
	@for flavor in cilium kubernetes; do \
		helm template t $(CONNECTIVITY_DIR) $(WIRING_BACKSTAGE_NETPOL_EDGE) --set networkPolicy.flavor=$$flavor --set 'backstage.parentRefs[0].name=own' --set 'backstage.parentRefs[0].namespace=portal-edge' 2>/dev/null >/tmp/vw-bsnp-pin-$$flavor.out; \
		grep -q 'portal-edge' /tmp/vw-bsnp-pin-$$flavor.out || { echo "FAIL: the $$flavor Backstage policy follows the chart-owned edge while the route is pinned with backstage.parentRefs, so the portal is unreachable through the Gateway that serves it"; exit 1; }; \
		awk '/^  name: agent-platform-connectivity-backstage$$/{f=1} f&&/^---$$/{exit} f' /tmp/vw-bsnp-pin-$$flavor.out | grep -q 'gateway.networking.k8s.io/gateway-name: agentgateway' && { echo "FAIL: the $$flavor Backstage policy still admits the data plane while the route is pinned elsewhere"; exit 1; }; \
		true; \
	done
	@echo "--> the scaffolder catalog's egress follows backstage.catalogs.version; the portal's database gets a leg on the postgresql engine"
	@for pattern in 'matchName: github.com' 'matchName: raw.githubusercontent.com'; do \
		grep -q -- "$$pattern" /tmp/vw-bsnp-cilium.out || { echo "FAIL: the cilium Backstage policy lacks $$pattern, so the catalog location it fetches is denied"; cat /tmp/vw-bsnp-cilium.out; exit 1; }; \
	done
	@helm template t $(CONNECTIVITY_DIR) $(WIRING_BACKSTAGE_NETPOL) --set backstage.catalogs.version= --set networkPolicy.flavor=cilium 2>/dev/null | awk '/^  name: agent-platform-connectivity-backstage$$/{f=1} f&&/^---$$/{exit} f' >/tmp/vw-bsnp-nocat.out
	@if grep -q 'github' /tmp/vw-bsnp-nocat.out; then echo "FAIL: the cilium Backstage policy renders the catalog egress with backstage.catalogs.version empty"; exit 1; fi
	@if grep -q '5432' /tmp/vw-bsnp-cilium.out; then echo "FAIL: the cilium Backstage policy renders 5432 on the sqlite engine"; exit 1; fi
	@helm template t $(CONNECTIVITY_DIR) $(WIRING_BACKSTAGE_NETPOL_FULL) --set networkPolicy.flavor=cilium 2>/dev/null | awk '/^  name: agent-platform-connectivity-backstage$$/{f=1} f&&/^---$$/{exit} f' >/tmp/vw-bsnp-full.out
	@for pattern in 'backstage-cnpg' 'backstage-cnpg-restore' 'port: "5432"'; do \
		grep -q -- "$$pattern" /tmp/vw-bsnp-full.out || { echo "FAIL: the cilium Backstage policy lacks $$pattern"; cat /tmp/vw-bsnp-full.out; exit 1; }; \
	done
	@helm template t $(CONNECTIVITY_DIR) $(WIRING_BACKSTAGE_NETPOL) --set networkPolicy.flavor=kubernetes 2>/dev/null >/tmp/vw-bsnp-k8s-all.out
	@awk '/^  name: agent-platform-connectivity-backstage$$/{f=1} f&&/^---$$/{exit} f' /tmp/vw-bsnp-k8s-all.out >/tmp/vw-bsnp-k8s-in.out
	@awk '/^  name: agent-platform-connectivity-backstage-egress$$/{f=1} f&&/^---$$/{exit} f' /tmp/vw-bsnp-k8s-all.out >/tmp/vw-bsnp-k8s-eg.out
	@[ -s /tmp/vw-bsnp-k8s-in.out ] && [ -s /tmp/vw-bsnp-k8s-eg.out ] || { echo "FAIL: the kubernetes flavor renders no Backstage ingress/egress policy pair"; exit 1; }
	@grep -q 'port: 7007' /tmp/vw-bsnp-k8s-in.out || { echo "FAIL: the kubernetes Backstage ingress policy is not on the app port"; exit 1; }
	@for pattern in 'k8s-app' 'app.kubernetes.io/name: muster' '10.0.0.0/8' 'kubernetes.io/metadata.name: envoy-gateway-system'; do \
		grep -q -- "$$pattern" /tmp/vw-bsnp-k8s-eg.out || { echo "FAIL: the kubernetes Backstage egress policy lacks $$pattern"; cat /tmp/vw-bsnp-k8s-eg.out; exit 1; }; \
	done
	@echo "--> the Envoy edge is reached on 10443 too (a listener on 443 is a proxy pod on listener+10000), in both flavors"
	@awk '/app.kubernetes.io\/name: envoy$$/{f=1} f&&/^    - to:$$/{exit} f' /tmp/vw-bsnp-k8s-eg.out >/tmp/vw-bsnp-k8s-edge.out
	@for port in 443 10443; do \
		grep -q -- "- port: $$port$$" /tmp/vw-bsnp-k8s-edge.out || { echo "FAIL: the kubernetes Backstage egress leg to the Envoy edge does not open $$port, so the app-config's public hostnames are denied behind an Envoy Gateway (a listener on 443 is a proxy pod on 10443)"; cat /tmp/vw-bsnp-k8s-eg.out; exit 1; }; \
	done
	@awk '/^    - toEntities:$$/{f=1} f&&/^    - /&&!/toEntities/{f=0} f' /tmp/vw-bsnp-cilium.out | grep -q '10443' || { echo "FAIL: the cilium Backstage policy opens no cluster entity on 10443, so the edge is unreachable"; cat /tmp/vw-bsnp-cilium.out; exit 1; }
	@grep -q 'kubernetes.io/metadata.name: envoy-gateway-system' /tmp/vw-bsnp-k8s-in.out || { echo "FAIL: the kubernetes Backstage ingress policy does not name the front Gateway that serves its route"; cat /tmp/vw-bsnp-k8s-in.out; exit 1; }
	@echo "--> the kubernetes egress follows the same edge split as the ingress"
	@helm template t $(CONNECTIVITY_DIR) $(WIRING_BACKSTAGE_NETPOL_EDGE) --set networkPolicy.flavor=kubernetes 2>/dev/null | awk '/^  name: agent-platform-connectivity-backstage-egress$$/{f=1} f&&/^---$$/{exit} f' >/tmp/vw-bsnp-k8s-edge-eg.out
	@grep -q 'gateway.networking.k8s.io/gateway-name: agentgateway' /tmp/vw-bsnp-k8s-edge-eg.out || { echo "FAIL: with the chart owning the edge the kubernetes egress policy reaches no edge, so the public hostnames in the app-config are denied"; cat /tmp/vw-bsnp-k8s-edge-eg.out; exit 1; }
	@if grep -q 'envoy-gateway-system' /tmp/vw-bsnp-k8s-edge-eg.out; then echo "FAIL: with the chart owning the edge the kubernetes egress policy still names the front Gateway"; exit 1; fi
	@helm template t $(CONNECTIVITY_DIR) $(WIRING_BACKSTAGE_NETPOL_EDGE) --set networkPolicy.flavor=kubernetes 2>/dev/null | awk '/^  name: agent-platform-connectivity-backstage$$/{f=1} f&&/^---$$/{exit} f' | grep -q 'gateway.networking.k8s.io/gateway-name: agentgateway' || { echo "FAIL: with the chart owning the edge the kubernetes ingress policy does not name the data plane"; exit 1; }
	@helm template t $(CONNECTIVITY_DIR) $(WIRING_BACKSTAGE_NETPOL_FULL) --set networkPolicy.flavor=kubernetes 2>/dev/null | awk '/^  name: agent-platform-connectivity-backstage-egress$$/{f=1} f&&/^---$$/{exit} f' >/tmp/vw-bsnp-k8s-full.out
	@for pattern in 'backstage-cnpg' 'port: 5432'; do \
		grep -q -- "$$pattern" /tmp/vw-bsnp-k8s-full.out || { echo "FAIL: the kubernetes Backstage egress policy lacks $$pattern"; cat /tmp/vw-bsnp-k8s-full.out; exit 1; }; \
	done
	@if grep -q '5432' /tmp/vw-bsnp-k8s-eg.out; then echo "FAIL: the kubernetes Backstage egress policy renders 5432 with the engine unset"; exit 1; fi
	@echo "--> an empty worldExcludedCIDRs renders no except key"
	@printf 'networkPolicy:\n  kubernetes:\n    worldExcludedCIDRs: []\n' >/tmp/vw-bsnp-noexcept.yaml
	@helm template t $(CONNECTIVITY_DIR) $(WIRING_BACKSTAGE_NETPOL) -f /tmp/vw-bsnp-noexcept.yaml --set networkPolicy.flavor=kubernetes 2>/dev/null | awk '/^  name: agent-platform-connectivity-backstage-egress$$/{f=1} f&&/^---$$/{exit} f' >/tmp/vw-bsnp-k8s-noexcept.out
	@if grep -q 'except' /tmp/vw-bsnp-k8s-noexcept.out; then echo "FAIL: the kubernetes Backstage egress policy renders an empty except list"; cat /tmp/vw-bsnp-k8s-noexcept.out; exit 1; fi
	@echo "--> the app port follows backstage.port"
	@helm template t $(CONNECTIVITY_DIR) $(WIRING_BACKSTAGE_NETPOL) --set networkPolicy.flavor=cilium --set backstage.port=9999 2>/dev/null | awk '/^  name: agent-platform-connectivity-backstage$$/{f=1} f&&/^---$$/{exit} f' | grep -q 'port: "9999"' || { echo "FAIL: the Backstage policy pins 7007 instead of backstage.port"; exit 1; }
	@echo "--> Backstage off, or the policies off: no Backstage app policy in either flavor"
	@for flavor in cilium kubernetes; do \
		helm template t $(CONNECTIVITY_DIR) $(WIRING_OFF) --set networkPolicy.flavor=$$flavor 2>/dev/null | grep -qE '^  name: agent-platform-connectivity-backstage(-egress)?$$' && { echo "FAIL: a Backstage app policy rendered with the component off ($$flavor)"; exit 1; }; \
		helm template t $(CONNECTIVITY_DIR) $(WIRING_BACKSTAGE_NETPOL) --set networkPolicy.flavor=$$flavor --set networkPolicy.enabled=false 2>/dev/null | grep -qE '^  name: agent-platform-connectivity-backstage(-egress)?$$' && { echo "FAIL: a Backstage app policy rendered with networkPolicy.enabled=false ($$flavor)"; exit 1; }; \
		true; \
	done
	@echo "--> a core Affinity key, an unknown Affinity key and a nameless pull secret fail the render"
	@if helm template t $(CONNECTIVITY_DIR) $(WIRING_PG) --set 'postgres.affinity.podAntiAffinity.foo=bar' >/tmp/vw-pg-guard.out 2>&1; then echo "FAIL: postgres.affinity.podAntiAffinity rendered; the Cluster CRD rejects it"; exit 1; fi
	@grep -q 'postgres.affinity.podAntiAffinity is a core Kubernetes Affinity key' /tmp/vw-pg-guard.out || { echo "FAIL: the affinity guard does not name the key"; cat /tmp/vw-pg-guard.out; exit 1; }
	@if helm template t $(CONNECTIVITY_DIR) $(WIRING_PG) --set 'postgres.affinity.nodeSelektor.foo=bar' >/tmp/vw-pg-guard3.out 2>&1; then echo "FAIL: an unknown postgres.affinity key rendered; the Cluster CRD drops it"; exit 1; fi
	@grep -q 'postgres.affinity.nodeSelektor is not a key' /tmp/vw-pg-guard3.out || { echo "FAIL: the affinity guard does not reject an unknown key"; cat /tmp/vw-pg-guard3.out; exit 1; }
	@printf 'postgres:\n  imagePullSecrets:\n    - {}\n' >/tmp/vw-pg-noname.yaml
	@if helm template t $(CONNECTIVITY_DIR) $(WIRING_PG) -f /tmp/vw-pg-noname.yaml >/tmp/vw-pg-guard2.out 2>&1; then echo "FAIL: a nameless postgres.imagePullSecrets entry rendered"; exit 1; fi
	@grep -q 'postgres.imagePullSecrets\[0\] has no name' /tmp/vw-pg-guard2.out || { echo "FAIL: the pull-secret guard does not name the entry"; cat /tmp/vw-pg-guard2.out; exit 1; }
	@echo "--> the meta chart declares postgres.imagePullSecrets and postgres.affinity (schema symmetry)"
	@helm template t $(CHART_DIR) -f $(CHART_DIR)/ci/ci-values.yaml $(FLEET_APIS) --set 'postgres.imagePullSecrets[0].name=mirror-pull-secret' --set postgres.affinity.enablePodAntiAffinity=true >/tmp/vw-pg-meta.out 2>&1 || { echo "FAIL: the meta chart rejects postgres.imagePullSecrets / postgres.affinity"; cat /tmp/vw-pg-meta.out; exit 1; }
	@echo "ok: the Postgres Cluster knobs and the Backstage app policy verified"
	@echo "--> the controller's JWKS egress: an in-cluster issuer and in-cluster route hosts render today's policies, in both flavors and for both shapes"
	@for flavor in cilium kubernetes; do \
		for shape in "$(FLEET_APIS)" ""; do \
			helm template t $(CONNECTIVITY_DIR) $(JWKS_ISSUER_INCLUSTER) --set networkPolicy.flavor=$$flavor $$shape 2>/dev/null | $(CTRL_POLICY) >/tmp/vw-jwks-$$flavor.out; \
			[ -s /tmp/vw-jwks-$$flavor.out ] || { echo "FAIL: no controller policy rendered ($$flavor)"; exit 1; }; \
			for pattern in 'toFQDNs' 'rules:' '0.0.0.0/0$$'; do \
				if grep -q -- "$$pattern" /tmp/vw-jwks-$$flavor.out; then echo "FAIL: in-cluster JWKS hosts alone rendered $$pattern on the $$flavor controller policy"; exit 1; fi; \
			done; \
		done; \
	done
	@echo "--> the same values against $(GOLDEN_REF): the controller policies first (a difference is a regression), then the whole render (image references aside: a re-pin moves them by design; a difference on a branch that contains $(GOLDEN_REF) is its own change, on one that does not it is staleness)"
	@if [ -n "$(GOLDEN_REF)" ] && git rev-parse --verify -q $(GOLDEN_REF) >/dev/null; then \
		rm -rf /tmp/vw-jwks-ref && git worktree add -q --detach /tmp/vw-jwks-ref $(GOLDEN_REF) && \
		for flavor in cilium kubernetes; do \
			helm template t $(CONNECTIVITY_DIR) $(JWKS_ISSUER_INCLUSTER) --set networkPolicy.flavor=$$flavor 2>/dev/null >/tmp/vw-jwks-new-$$flavor.out; \
			helm template t /tmp/vw-jwks-ref/$(CONNECTIVITY_DIR) $(JWKS_ISSUER_INCLUSTER) --set networkPolicy.flavor=$$flavor 2>/dev/null >/tmp/vw-jwks-old-$$flavor.out; \
			$(CTRL_POLICY) /tmp/vw-jwks-old-$$flavor.out >/tmp/vw-jwks-old-pol-$$flavor.out; \
			$(CTRL_POLICY) /tmp/vw-jwks-new-$$flavor.out >/tmp/vw-jwks-new-pol-$$flavor.out; \
			diff -u /tmp/vw-jwks-old-pol-$$flavor.out /tmp/vw-jwks-new-pol-$$flavor.out || { echo "FAIL: the $$flavor CONTROLLER POLICY changed for in-cluster JWKS hosts (issuer and routes) - a regression in this slice"; git worktree remove --force /tmp/vw-jwks-ref; exit 1; }; \
			grep -vE '^ *image:' /tmp/vw-jwks-old-$$flavor.out >/tmp/vw-jwks-old-noimg-$$flavor.out; grep -vE '^ *image:' /tmp/vw-jwks-new-$$flavor.out >/tmp/vw-jwks-new-noimg-$$flavor.out; diff -u /tmp/vw-jwks-old-noimg-$$flavor.out /tmp/vw-jwks-new-noimg-$$flavor.out || { \
				if git merge-base --is-ancestor $(GOLDEN_REF) HEAD; then echo "note: the $$flavor render differs from $(GOLDEN_REF) outside the controller policy and the image references — this branch's own change ($(GOLDEN_REF) is an ancestor of HEAD; the policies above match)"; \
				else echo "FAIL: the $$flavor render changed for an in-cluster JWKS host, outside the controller policy and the image references (a re-pin moves those by design). The policies above match and $(GOLDEN_REF) is not an ancestor of HEAD, so the branch is behind $(GOLDEN_REF): merge it and run again."; git worktree remove --force /tmp/vw-jwks-ref; exit 1; fi; }; \
		done; \
		git worktree remove --force /tmp/vw-jwks-ref; \
		echo "ok: against $(GOLDEN_REF) — the controller policies identical, the render otherwise identical or the branch's own change"; \
	else echo "skipped: no GOLDEN_REF"; fi
	@echo "--> the platform's identity provider (giantswarm/agent-platform#505): a public global.identity.issuerUrl is opened from the controller policy on 443 whatever the routes name — the kagent route on an in-cluster host, and no route at all (the platform's release beside a serving slice, whose models policy takes the issuer on 443)"
	@for shape in JWKS_INCLUSTER JWKS_PLATFORM; do \
		case $$shape in JWKS_INCLUSTER) flags="$(JWKS_INCLUSTER)";; JWKS_PLATFORM) flags="$(JWKS_PLATFORM)";; esac; \
		helm template t $(CONNECTIVITY_DIR) $$flags --set networkPolicy.flavor=cilium 2>/dev/null | $(CTRL_POLICY) >/tmp/vw-jwks-issuer-cilium.out; \
		[ -s /tmp/vw-jwks-issuer-cilium.out ] || { echo "FAIL: no cilium controller policy rendered ($$shape)"; exit 1; }; \
		grep -q 'matchName: "dex.ci.example.com"' /tmp/vw-jwks-issuer-cilium.out || { echo "FAIL: $$shape: the platform's issuer is not a toFQDNs matchName on the cilium controller policy; the controller cannot fetch the models Gateway's JWKS"; cat /tmp/vw-jwks-issuer-cilium.out; exit 1; }; \
		awk '/matchName: "dex.ci.example.com"/{f=1} f&&/port:/{print;exit}' /tmp/vw-jwks-issuer-cilium.out | grep -q '"443"' || { echo "FAIL: $$shape: the platform's issuer is not opened on 443"; exit 1; }; \
		grep -q 'matchPattern: "\*"' /tmp/vw-jwks-issuer-cilium.out || { echo "FAIL: $$shape: the cilium controller policy has no DNS proxy rule, so the issuer's toFQDNs selector matches nothing"; exit 1; }; \
		if grep -q 'matchName: "dex.giantswarm.svc.cluster.local"' /tmp/vw-jwks-issuer-cilium.out; then echo "FAIL: $$shape: the in-cluster JWKS host rendered as a toFQDNs selector"; exit 1; fi; \
		helm template t $(CONNECTIVITY_DIR) $$flags --set networkPolicy.flavor=kubernetes 2>/dev/null | $(CTRL_POLICY) >/tmp/vw-jwks-issuer-kubernetes.out; \
		grep -q 'cidr: 0.0.0.0/0' /tmp/vw-jwks-issuer-kubernetes.out || { echo "FAIL: $$shape: the kubernetes controller policy has no wide rule for the platform's issuer"; cat /tmp/vw-jwks-issuer-kubernetes.out; exit 1; }; \
		sed -n '/cidr: 0.0.0.0\/0/,$$p' /tmp/vw-jwks-issuer-kubernetes.out | grep -q '^        - port: 443$$' || { echo "FAIL: $$shape: the kubernetes wide rule does not open 443 for the platform's issuer"; cat /tmp/vw-jwks-issuer-kubernetes.out; exit 1; }; \
	done
	@echo "--> the issuer URL's own port is the one opened; an in-cluster issuer adds no external rule"
	@helm template t $(CONNECTIVITY_DIR) $(JWKS_PLATFORM) --set global.identity.issuerUrl=https://dex.ci.example.com:8443/dex --set networkPolicy.flavor=cilium 2>/dev/null | $(CTRL_POLICY) | awk '/matchName: "dex.ci.example.com"/{f=1} f&&/port:/{print;exit}' | grep -q '"8443"' || { echo "FAIL: an issuer URL carrying a port is not opened on that port"; exit 1; }
	@helm template t $(CONNECTIVITY_DIR) $(JWKS_PLATFORM) --set global.identity.issuerUrl=https://dex.giantswarm.svc.cluster.local:5556 --set networkPolicy.flavor=cilium 2>/dev/null | $(CTRL_POLICY) >/tmp/vw-jwks-issuer-incluster.out
	@if grep -q 'toFQDNs\|rules:' /tmp/vw-jwks-issuer-incluster.out; then echo "FAIL: an in-cluster issuer rendered an external rule or the DNS proxy clause"; cat /tmp/vw-jwks-issuer-incluster.out; exit 1; fi
	@echo "ok: the platform's issuer is an external JWKS target of the controller policy in both flavors, with or without a route policy, on the URL's port; in-cluster it adds nothing"
	@echo "--> an external JWKS host (Google-shaped): the cilium controller policy names it on its port, behind the DNS proxy rule"
	@helm template t $(CONNECTIVITY_DIR) $(JWKS_EXTERNAL) --set networkPolicy.flavor=cilium 2>/dev/null | $(CTRL_POLICY) >/tmp/vw-jwks-ext-cilium.out
	@grep -q 'matchName: "www.googleapis.com"' /tmp/vw-jwks-ext-cilium.out || { echo "FAIL: the external JWKS host is not a toFQDNs matchName on the cilium controller policy"; cat /tmp/vw-jwks-ext-cilium.out; exit 1; }
	@awk '/matchName: "www.googleapis.com"/{f=1} f&&/port:/{print;exit}' /tmp/vw-jwks-ext-cilium.out | grep -q '"443"' || { echo "FAIL: the external JWKS host is not opened on its own port (443)"; exit 1; }
	@grep -q 'matchPattern: "\*"' /tmp/vw-jwks-ext-cilium.out || { echo "FAIL: the cilium controller policy has no DNS proxy rule, so its toFQDNs selector matches nothing"; exit 1; }
	@echo "--> the same host, kubernetes flavor: an address block on that port, minus worldExcludedCIDRs"
	@helm template t $(CONNECTIVITY_DIR) $(JWKS_EXTERNAL) --set networkPolicy.flavor=kubernetes 2>/dev/null | $(CTRL_POLICY) >/tmp/vw-jwks-ext-kubernetes.out
	@awk '/cidr: 0.0.0.0\/0$$/{f=1} f' /tmp/vw-jwks-ext-kubernetes.out | grep -q '10.0.0.0/8' || { echo "FAIL: the kubernetes controller policy does not exclude worldExcludedCIDRs from the JWKS egress"; cat /tmp/vw-jwks-ext-kubernetes.out; exit 1; }
	@awk '/cidr: 0.0.0.0\/0$$/{f=1} f&&/- port:/{print;exit}' /tmp/vw-jwks-ext-kubernetes.out | grep -q '443' || { echo "FAIL: the kubernetes controller JWKS egress is not on the JWKS port"; exit 1; }
	@echo "--> an address literal as jwks.host fails the render, in either family, and points at external.cidrs"
	@for host in 198.51.100.7 2001:db8::1; do \
		if helm template t $(CONNECTIVITY_DIR) $(JWKS_EXTERNAL) --set "kagent.controllerRoute.jwtAuthentication.jwks.host=$$host" --set kagent.controllerRoute.jwtAuthentication.jwks.port=8443 >/tmp/vw-jwks-addr.out 2>&1; then \
			echo "FAIL: the address literal $$host was accepted as a jwks.host"; exit 1; \
		elif ! grep -q 'an address literal' /tmp/vw-jwks-addr.out; then \
			echo "FAIL: the address literal $$host failed for the wrong reason"; cat /tmp/vw-jwks-addr.out; exit 1; fi; \
		grep -q 'gateway.jwksEgress.external.cidrs' /tmp/vw-jwks-addr.out || { echo "FAIL: the address-literal guard does not name gateway.jwksEgress.external.cidrs"; exit 1; }; \
	done
	@echo "--> gateway.jwksEgress.external.cidrs: its own rule in both flavors, on external.port"
	@helm template t $(CONNECTIVITY_DIR) $(JWKS_ISSUER_INCLUSTER) --set 'gateway.jwksEgress.external.cidrs[0]=10.20.30.0/24' --set networkPolicy.flavor=cilium 2>/dev/null | $(CTRL_POLICY) | grep -q -- '- 10.20.30.0/24' || { echo "FAIL: gateway.jwksEgress.external.cidrs did not reach the cilium controller policy"; exit 1; }
	@helm template t $(CONNECTIVITY_DIR) $(JWKS_ISSUER_INCLUSTER) --set 'gateway.jwksEgress.external.cidrs[0]=10.20.30.0/24' --set gateway.jwksEgress.external.port=8443 --set networkPolicy.flavor=kubernetes 2>/dev/null | $(CTRL_POLICY) | awk '/cidr: "10.20.30.0\/24"/{f=1} f&&/- port:/{print;exit}' | grep -q '8443' || { echo "FAIL: gateway.jwksEgress.external.cidrs/.port did not reach the kubernetes controller policy"; exit 1; }
	@echo "--> an in-cluster host with external.cidrs set renders no wide rule: the narrow form stays narrow"
	@if helm template t $(CONNECTIVITY_DIR) $(JWKS_ISSUER_INCLUSTER) --set 'gateway.jwksEgress.external.cidrs[0]=10.20.30.0/24' --set networkPolicy.flavor=kubernetes 2>/dev/null | $(CTRL_POLICY) | grep -q 'cidr: 0.0.0.0/0'; then \
		echo "FAIL: gateway.jwksEgress.external.cidrs alone opened every public destination"; exit 1; fi
	@echo "--> gateway.jwksEgress.external.fqdns: a cilium name selector on external.port, behind the DNS proxy rule; the kubernetes flavor ignores it"
	@helm template t $(CONNECTIVITY_DIR) $(JWKS_ISSUER_INCLUSTER) --set 'gateway.jwksEgress.external.fqdns[0].matchName=keys.example.com' --set gateway.jwksEgress.external.port=8443 --set networkPolicy.flavor=cilium 2>/dev/null | $(CTRL_POLICY) >/tmp/vw-jwks-extfqdn.out
	@grep -q 'matchName: keys.example.com' /tmp/vw-jwks-extfqdn.out || { echo "FAIL: gateway.jwksEgress.external.fqdns did not reach the cilium controller policy"; cat /tmp/vw-jwks-extfqdn.out; exit 1; }
	@awk '/matchName: keys.example.com/{f=1} f&&/port:/{print;exit}' /tmp/vw-jwks-extfqdn.out | grep -q '"8443"' || { echo "FAIL: gateway.jwksEgress.external.fqdns is not opened on external.port"; exit 1; }
	@grep -q 'matchPattern: "\*"' /tmp/vw-jwks-extfqdn.out || { echo "FAIL: gateway.jwksEgress.external.fqdns rendered no DNS proxy rule, so its selector matches nothing"; exit 1; }
	@if helm template t $(CONNECTIVITY_DIR) $(JWKS_ISSUER_INCLUSTER) --set 'gateway.jwksEgress.external.fqdns[0].matchName=keys.example.com' --set networkPolicy.flavor=kubernetes 2>/dev/null | $(CTRL_POLICY) | grep -q 'keys.example.com\|cidr: 0.0.0.0/0'; then \
		echo "FAIL: the kubernetes controller policy acted on gateway.jwksEgress.external.fqdns, which selects a name it cannot express"; exit 1; fi
	@echo "--> gateway.jwksEgress.external: null renders, and the schema types cidrs"
	@printf 'gateway:\n  jwksEgress:\n    enabled: true\n    external: null\n' >/tmp/vw-jwks-null-external.yaml
	$(call managers_must_pass,an explicitly null external block,$(JWKS_INCLUSTER) -f /tmp/vw-jwks-null-external.yaml)
	@for bad in 999.1.1.1/40 10.0.0.0/40 nonsense/24 ::::/64 2001:db8::/129 12345::/64 1:2:3:4:5:6:7:8:9/64 2001:db8::1; do \
		if helm template t $(CONNECTIVITY_DIR) $(JWKS_INCLUSTER) --set "gateway.jwksEgress.external.cidrs[0]=$$bad" >/dev/null 2>&1; then \
			echo "FAIL: gateway.jwksEgress.external.cidrs accepted $$bad"; exit 1; fi; \
	done
	@for good in 10.20.30.0/24 2001:db8::/64 ::/0 ::1/128 fe80::1/64 2001:0db8:85a3:0000:0000:8a2e:0370:7334/128 ::ffff:192.0.2.1/128; do \
		helm template t $(CONNECTIVITY_DIR) $(JWKS_INCLUSTER) --set "gateway.jwksEgress.external.cidrs[0]=$$good" >/dev/null 2>&1 || { \
			echo "FAIL: gateway.jwksEgress.external.cidrs refused the valid block $$good"; exit 1; }; \
	done
	@echo "--> the meta chart declares gateway.jwksEgress.external (schema symmetry)"
	@helm template t $(CHART_DIR) -f $(CHART_DIR)/ci/ci-values.yaml $(FLEET_APIS) --set 'gateway.jwksEgress.external.cidrs[0]=10.20.30.0/24' --set gateway.jwksEgress.external.port=8443 >/dev/null 2>&1 || { echo "FAIL: the meta chart rejects gateway.jwksEgress.external"; exit 1; }
	@echo "--> an external host needs no gateway.jwksEgress; an in-cluster one still does"
	@helm template t $(CONNECTIVITY_DIR) $(JWKS_EXTERNAL) >/dev/null 2>&1 || { echo "FAIL: an external JWKS host is refused without gateway.jwksEgress"; exit 1; }
	@if helm template t $(CONNECTIVITY_DIR) $(JWKS_BASE) --set global.identity.issuerUrl=https://dex.ci.example.com >/dev/null 2>&1; then \
		echo "FAIL: an in-cluster JWKS host was accepted without gateway.jwksEgress"; exit 1; fi
	@echo "--> port 443 implies TLS on all three routes, against the system trust: the platform CA is for an issuer jwks.tls.enabled names"
	@for route in kagent-controller model-manager agent-manager; do \
		case $$route in \
			kagent-controller) flags="$(JWKS_EXTERNAL)"; key=kagent.controllerRoute.jwtAuthentication;; \
			model-manager) flags="$(MANAGERS_ON) --set modelManager.route.enabled=true --set modelManager.route.jwtAuthentication.enabled=true --set modelManager.route.jwtAuthentication.jwks.host=www.googleapis.com --set modelManager.route.jwtAuthentication.jwks.port=443"; key=modelManager.route.jwtAuthentication;; \
			agent-manager) flags="$(MANAGERS_ON) --set agentManager.route.enabled=true --set agentManager.route.jwtAuthentication.enabled=true --set agentManager.route.jwtAuthentication.jwks.host=www.googleapis.com --set agentManager.route.jwtAuthentication.jwks.port=443"; key=agentManager.route.jwtAuthentication;; \
		esac; \
		helm template t $(CONNECTIVITY_DIR) $$flags --set global.identity.ca.secretName=platform-ca 2>/dev/null | awk "/^  name: $$route-jwks\$$/{f=1} f&&/^---\$$/{exit} f" >/tmp/vw-jwks-tls-$$route.out; \
		[ -s /tmp/vw-jwks-tls-$$route.out ] || { echo "FAIL: no $$route-jwks backend rendered"; exit 1; }; \
		grep -q 'tls:' /tmp/vw-jwks-tls-$$route.out || { echo "FAIL: $$route-jwks does not originate TLS on port 443, so the fetch stays plain HTTP"; cat /tmp/vw-jwks-tls-$$route.out; exit 1; }; \
		if grep -q 'name: platform-ca' /tmp/vw-jwks-tls-$$route.out; then echo "FAIL: $$route-jwks on implied TLS pins the platform identity CA, which never issued a public certificate"; cat /tmp/vw-jwks-tls-$$route.out; exit 1; fi; \
		helm template t $(CONNECTIVITY_DIR) $$flags --set global.identity.ca.secretName=platform-ca --set $$key.jwks.tls.enabled=true 2>/dev/null | awk "/^  name: $$route-jwks\$$/{f=1} f&&/^---\$$/{exit} f" | grep -q 'name: platform-ca' || { echo "FAIL: $$route-jwks with jwks.tls.enabled does not fall back to global.identity.ca.secretName"; exit 1; }; \
		helm template t $(CONNECTIVITY_DIR) $$flags --set global.identity.ca.secretName=platform-ca --set $$key.jwks.tls.caSecretName=route-ca 2>/dev/null | awk "/^  name: $$route-jwks\$$/{f=1} f&&/^---\$$/{exit} f" | grep -q 'name: route-ca' || { echo "FAIL: $$route-jwks ignores jwks.tls.caSecretName on implied TLS"; exit 1; }; \
	done
	@echo "--> an in-cluster plain-HTTP port keeps its plain fetch"
	@helm template t $(CONNECTIVITY_DIR) $(JWKS_INCLUSTER) --set global.identity.ca.secretName=platform-ca 2>/dev/null | awk '/^  name: kagent-controller-jwks$$/{f=1} f&&/^---$$/{exit} f' | grep -q 'policies:' && { echo "FAIL: the in-cluster Dex fetch on 5556 originates TLS"; exit 1; } || true
	@echo "--> a host of the wrong shape fails the render, on every route, with or without a policy"
	$(call managers_must_fail,an empty host,$(JWKS_INCLUSTER) --set kagent.controllerRoute.jwtAuthentication.jwks.host=,jwks.host is empty)
	$(call managers_must_fail,a host with the port glued on,$(JWKS_INCLUSTER) --set 'kagent.controllerRoute.jwtAuthentication.jwks.host=dex.example.com:5556',which carries a port)
	$(call managers_must_fail,an out-of-range dotted quad,$(JWKS_EXTERNAL) --set kagent.controllerRoute.jwtAuthentication.jwks.host=1.2.3.999 --set kagent.controllerRoute.jwtAuthentication.jwks.port=8443,an address literal)
	$(call managers_must_fail,a malformed host with the policies off,$(JWKS_INCLUSTER) --set networkPolicy.enabled=false --set 'kagent.controllerRoute.jwtAuthentication.jwks.host=dex.example.com:5556',which carries a port)
	$(call managers_must_fail,an address with the port glued on,$(JWKS_EXTERNAL) --set 'kagent.controllerRoute.jwtAuthentication.jwks.host=10.0.0.1:5556',which carries a port)
	$(call managers_must_fail,a bracketed IPv6 address with a port,$(JWKS_EXTERNAL) --set 'kagent.controllerRoute.jwtAuthentication.jwks.host=[2001:db8::1]:443',which carries a port)
	$(call managers_must_fail,a bare IPv6 literal,$(JWKS_EXTERNAL) --set 'kagent.controllerRoute.jwtAuthentication.jwks.host=2001:db8::1' --set kagent.controllerRoute.jwtAuthentication.jwks.port=8443,an address literal)
	@echo "--> a host that is not a hostname fails the render: the JWKS path and the empty label a wide rule would hide"
	$(call managers_must_fail,the JWKS path glued on,$(JWKS_EXTERNAL) --set 'kagent.controllerRoute.jwtAuthentication.jwks.host=accounts.google.com/keys',which is not a valid hostname)
	$(call managers_must_fail,an empty label,$(JWKS_EXTERNAL) --set kagent.controllerRoute.jwtAuthentication.jwks.host=a..b.example.com,which is not a valid hostname)
	$(call managers_must_fail,a label that ends with a hyphen,$(JWKS_EXTERNAL) --set kagent.controllerRoute.jwtAuthentication.jwks.host=keys-.example.com,which is not a valid hostname)
	$(call managers_must_fail,the same shape with the policies off,$(JWKS_INCLUSTER) --set networkPolicy.enabled=false --set 'kagent.controllerRoute.jwtAuthentication.jwks.host=accounts.google.com/keys',which is not a valid hostname)
	$(call managers_must_pass,a hostname carrying a hyphen and an underscore,$(JWKS_EXTERNAL) --set kagent.controllerRoute.jwtAuthentication.jwks.host=oidc_keys.my-idp.example.com)
	@echo "--> an svc label the search path never produces is a public host, not an in-cluster one"
	$(call managers_must_pass,a public host carrying an svc label,$(JWKS_EXTERNAL) --set kagent.controllerRoute.jwtAuthentication.jwks.host=a.b.svc.example.com)
	@helm template t $(CONNECTIVITY_DIR) $(JWKS_EXTERNAL) --set kagent.controllerRoute.jwtAuthentication.jwks.host=a.b.svc.example.com --set networkPolicy.flavor=cilium 2>/dev/null | $(CTRL_POLICY) | grep -q 'matchName: "a.b.svc.example.com"' || { echo "FAIL: a public host with an svc label was read as in-cluster and got no egress rule"; exit 1; }
	@for form in dex.giantswarm.svc dex.giantswarm.svc.cluster dex.giantswarm.svc.cluster.local dex.giantswarm.svc.cluster.local. DEX.Giantswarm.SVC.Cluster.Local; do \
		if helm template t $(CONNECTIVITY_DIR) $(JWKS_ISSUER_INCLUSTER) --set kagent.controllerRoute.jwtAuthentication.jwks.host=$$form --set networkPolicy.flavor=cilium 2>/dev/null | $(CTRL_POLICY) | grep -q 'matchName'; then \
			echo "FAIL: the in-cluster form $$form rendered an external name selector"; exit 1; fi; \
		if helm template t $(CONNECTIVITY_DIR) $(JWKS_ISSUER_INCLUSTER) --set kagent.controllerRoute.jwtAuthentication.jwks.host=$$form --set networkPolicy.flavor=kubernetes 2>/dev/null | $(CTRL_POLICY) | grep -q 'cidr: 0.0.0.0/0'; then \
			echo "FAIL: the in-cluster form $$form opened every public destination on the JWKS port"; exit 1; fi; \
	done
	@echo "--> an external host is selected in its normalized form: lower case, no root dot"
	@helm template t $(CONNECTIVITY_DIR) $(JWKS_EXTERNAL) --set kagent.controllerRoute.jwtAuthentication.jwks.host=WWW.GoogleAPIs.com. --set networkPolicy.flavor=cilium 2>/dev/null | $(CTRL_POLICY) | grep -q 'matchName: "www.googleapis.com"' || { echo "FAIL: the external JWKS host is not selected in its normalized form"; exit 1; }
	$(call managers_must_fail,the model-manager route rejects the same shape,$(MANAGERS_ON) --set modelManager.route.enabled=true --set modelManager.route.jwtAuthentication.enabled=true --set 'modelManager.route.jwtAuthentication.jwks.host=keys.example.com:443',which carries a port)
	$(call managers_must_fail,the agent-manager route rejects the same shape,$(MANAGERS_ON) --set agentManager.route.enabled=true --set agentManager.route.jwtAuthentication.enabled=true --set 'agentManager.route.jwtAuthentication.jwks.host=keys.example.com:443',which carries a port)
	@echo "--> a host of fewer than three labels fails the render, Service short name and public issuer alike"
	$(call managers_must_fail,a two-label public issuer,$(JWKS_INCLUSTER) --set kagent.controllerRoute.jwtAuthentication.jwks.host=okta.com --set kagent.controllerRoute.jwtAuthentication.jwks.port=5556,fewer than three labels)
	$(call managers_must_fail,a short Service name,$(JWKS_INCLUSTER) --set kagent.controllerRoute.jwtAuthentication.jwks.host=dex.giantswarm --set kagent.controllerRoute.jwtAuthentication.jwks.port=5556,fewer than three labels)
	$(call managers_must_fail,a single-label host,$(JWKS_INCLUSTER) --set kagent.controllerRoute.jwtAuthentication.jwks.host=dex --set kagent.controllerRoute.jwtAuthentication.jwks.port=5556,fewer than three labels)
	$(call managers_must_pass,the qualified form of that Service,$(JWKS_INCLUSTER) --set kagent.controllerRoute.jwtAuthentication.jwks.host=dex.giantswarm.svc.cluster.local)
	$(call managers_must_pass,its .svc form,$(JWKS_INCLUSTER) --set kagent.controllerRoute.jwtAuthentication.jwks.host=dex.giantswarm.svc)
	@echo "--> an empty jwks.port fails the render, with or without a policy"
	$(call managers_must_fail,an empty port,$(JWKS_INCLUSTER) --set kagent.controllerRoute.jwtAuthentication.jwks.port=null,jwks.port is empty)
	$(call managers_must_fail,an empty port with the policies off,$(JWKS_INCLUSTER) --set networkPolicy.enabled=false --set kagent.controllerRoute.jwtAuthentication.jwks.port=null,jwks.port is empty)
	@echo "--> gateway.jwksEgress.external.fqdns items are typed: a bare string fails the render"
	@if helm template t $(CONNECTIVITY_DIR) $(JWKS_INCLUSTER) --set 'gateway.jwksEgress.external.fqdns[0]=keys.example.com' >/dev/null 2>&1; then \
		echo "FAIL: gateway.jwksEgress.external.fqdns accepted a bare string, which the Cilium CRD refuses at apply"; exit 1; fi
	@if helm template t $(CHART_DIR) -f $(CHART_DIR)/ci/ci-values.yaml $(FLEET_APIS) --set 'gateway.jwksEgress.external.fqdns[0]=keys.example.com' >/dev/null 2>&1; then \
		echo "FAIL: the meta chart accepted a bare string in gateway.jwksEgress.external.fqdns"; exit 1; fi
	@echo "--> an in-cluster host outside gateway.jwksEgress's namespace or port fails the render"
	$(call managers_must_fail,an in-cluster host in another namespace,$(JWKS_INCLUSTER) --set kagent.controllerRoute.jwtAuthentication.jwks.host=keycloak.identity.svc.cluster.local --set kagent.controllerRoute.jwtAuthentication.jwks.port=5556,Set gateway.jwksEgress.namespace: identity)
	$(call managers_must_fail,an in-cluster host on another port,$(JWKS_INCLUSTER) --set kagent.controllerRoute.jwtAuthentication.jwks.port=8443,gateway.jwksEgress.port is 5556)
	$(call managers_must_pass,the matching namespace and port are accepted,$(JWKS_INCLUSTER) --set kagent.controllerRoute.jwtAuthentication.jwks.host=keycloak.identity.svc.cluster.local --set gateway.jwksEgress.namespace=identity)
	$(call managers_must_pass,an external host on a port jwksEgress does not name,$(JWKS_INCLUSTER) --set kagent.controllerRoute.jwtAuthentication.jwks.host=www.googleapis.com --set kagent.controllerRoute.jwtAuthentication.jwks.port=443)
	@echo "--> external.cidrs on the route's own port carries a second in-cluster issuer"
	$(call managers_must_pass,a mismatched namespace reached by address,$(JWKS_INCLUSTER) --set kagent.controllerRoute.jwtAuthentication.jwks.host=keycloak.identity.svc.cluster.local --set kagent.controllerRoute.jwtAuthentication.jwks.port=5556 --set 'gateway.jwksEgress.external.cidrs[0]=10.20.30.0/24' --set gateway.jwksEgress.external.port=5556)
	$(call managers_must_fail,the same blocks opened on another port,$(JWKS_INCLUSTER) --set kagent.controllerRoute.jwtAuthentication.jwks.host=keycloak.identity.svc.cluster.local --set kagent.controllerRoute.jwtAuthentication.jwks.port=5556 --set 'gateway.jwksEgress.external.cidrs[0]=10.20.30.0/24' --set gateway.jwksEgress.external.port=8443,Set gateway.jwksEgress.namespace: identity)
	@echo "--> the guards that only a rendered policy decides are silent without one"
	$(call managers_must_pass,a mismatched namespace with the policies off,$(JWKS_INCLUSTER) --set networkPolicy.enabled=false --set kagent.controllerRoute.jwtAuthentication.jwks.host=keycloak.identity.svc.cluster.local --set kagent.controllerRoute.jwtAuthentication.jwks.port=5556)
	$(call managers_must_pass,a two-label public issuer with the policies off,$(JWKS_INCLUSTER) --set networkPolicy.enabled=false --set kagent.controllerRoute.jwtAuthentication.jwks.host=okta.com --set kagent.controllerRoute.jwtAuthentication.jwks.port=5556)
	$(call managers_must_pass,a short Service name with the policies off,$(JWKS_INCLUSTER) --set networkPolicy.enabled=false --set kagent.controllerRoute.jwtAuthentication.jwks.host=dex.giantswarm --set kagent.controllerRoute.jwtAuthentication.jwks.port=5556)
	$(call managers_must_pass,an in-cluster host without jwksEgress and the policies off,$(JWKS_BASE) --set networkPolicy.enabled=false --set global.identity.issuerUrl=https://dex.ci.example.com)
	@echo "ok: the controller's JWKS egress verified"
	@echo "the standalone's ported wiring verified."

# The kagent API v2 cut-over of an installation (#346): the kagent_v2 database
# with its derived connection Secret (postgres.databases, the hook Job in
# templates/postgres/databases-hook.yaml) and the agent-manager migrate
# Job (agentManager.migration, templates/kagent/migrate-*.yaml). PICK selects one
# object of a render by kind and name (tests/pick-doc.py; an awk range picks the
# first object of a kind, wrong as soon as two Jobs render).
PICK := python3 tests/pick-doc.py
MIGRATION_ON := $(MANAGERS_MIN) --set components.agent-manager.enabled=true
# The migrate Job's image pin: agentManager.migration.image.tag — the meta chart's BOM pin for agent-manager,
# mirrored as the connectivity chart's default. Read from the values (Renovate bumps both files), never a literal.
MIGRATION_PIN = python3 -c "import yaml; print(yaml.safe_load(open('$(CHART_DIR)/values.yaml'))['agentManager']['migration']['image']['tag'])"
MIGRATION_PIN_CONNECTIVITY = python3 -c "import yaml; print(yaml.safe_load(open('$(CONNECTIVITY_DIR)/values.yaml'))['agentManager']['migration']['image']['tag'])"
MIGRATION_JOB := agent-platform-connectivity-agent-manager-migrate
# The destinations an egress policy names, normalized for a diff between two
# policies: cilium — FQDN selectors and CIDR blocks; kubernetes — ipBlock CIDRs;
# quotes dropped, sorted (verify-migration, #433).
EGRESS_NAMES := grep -E '^ *-? ?(matchName|matchPattern): |^ *- [0-9]+(\.[0-9]+){3}/[0-9]+$$' | sed -E "s/^ *-? ?//; s/['\"]//g" | sort
EGRESS_BLOCKS := grep -E '^ *cidr: ' | sed -E "s/^ *//; s/['\"]//g" | sort

.PHONY: verify-substrate-store
SUBSTRATE_STORE_CI := $(CONNECTIVITY_DIR)/ci/test-substrate-store-aws-values.yaml
# The account id apart: Helm applies --set-string after --set, so the numeric-id guard below builds on the base.
SUBSTRATE_STORE_META_BASE := helm template t $(CHART_DIR) -f $(CHART_DIR)/ci/ci-values.yaml $(ENGINE_OFF) --set kagent.harness.snapshotLocation= --set kagent.harness.snapshotStore.crossplane.enabled=true --set kagent.harness.snapshotStore.crossplane.providerConfigRef=ci --set kagent.harness.snapshotStore.crossplane.region=eu-central-1 --set kagent.harness.snapshotStore.crossplane.aws.bucketName=giantswarm-ci-substrate --set kagent.harness.snapshotStore.crossplane.aws.oidcProvider=irsa.ci.example.com
SUBSTRATE_STORE_META := $(SUBSTRATE_STORE_META_BASE) --set-string kagent.harness.snapshotStore.crossplane.aws.accountId=123456789012
SUBSTRATE_STORE_CAPZ_CI := $(CONNECTIVITY_DIR)/ci/test-substrate-store-capz-values.yaml
SUBSTRATE_STORE_CAPZ_SET := --set kagent.harness.snapshotStore.crossplane.provider=capz --set kagent.harness.snapshotStore.crossplane.region=westeurope --set kagent.harness.snapshotStore.crossplane.capz.storageAccountName=giantswarmcisubstrate --set kagent.harness.snapshotStore.crossplane.capz.containerName=giantswarm-ci-substrate --set kagent.harness.snapshotStore.crossplane.capz.resourceGroup=ci --set kagent.harness.snapshotStore.crossplane.capz.subscriptionId=00000000-0000-0000-0000-000000000000 --set kagent.harness.snapshotStore.crossplane.capz.workloadIdentity.oidcIssuerUrl=https://oidc.ci.example.com/ --set kagent.harness.snapshotStore.crossplane.capz.workloadIdentity.providerKubernetes.providerConfigRef=ci-kubernetes
SUBSTRATE_STORE_META_CAPZ := helm template t $(CHART_DIR) -f $(CHART_DIR)/ci/ci-values.yaml $(ENGINE_OFF) --set kagent.harness.snapshotLocation= --set kagent.harness.snapshotStore.crossplane.enabled=true --set kagent.harness.snapshotStore.crossplane.providerConfigRef=ci $(SUBSTRATE_STORE_CAPZ_SET)
# The façade alone: no Crossplane block, an account provisioned by hand (or a lab's Azurite) named in s3proxy.azure.*.
SUBSTRATE_STORE_S3PROXY := --set kagent.harness.snapshotLocation= --set kagent.harness.snapshotStore.s3proxy.enabled=true --set kagent.harness.snapshotStore.s3proxy.azure.endpoint=http://azurite.agent-platform.svc:10000/devstoreaccount1 --set kagent.harness.snapshotStore.s3proxy.azure.account=devstoreaccount1 --set kagent.harness.snapshotStore.s3proxy.azure.container=ate-snapshots --set kagent.harness.snapshotStore.s3proxy.azure.accountKeySecretRef.name=azurite --set kagent.harness.snapshotStore.s3proxy.azure.accountKeySecretRef.key=key
verify-substrate-store: ## Assert Agent Substrate's snapshot store (kagent.harness.snapshotStore, #411): off by default nothing renders; on, the connectivity chart renders the Crossplane Bucket (+ lifecycle, public-access block, TLS-only policy, never deleted, kept) and the IAM Role trusted by the atelet and ate-api-server ServiceAccounts in ate-system with the S3 policy on the bucket; the meta chart derives kagent.harness.snapshotLocation (s3://<bucket>/<prefix>) for the kagent release and holds the block back from it, forwards the role annotation to both ServiceAccounts of the substrate release next to an installation's own annotations; provider capz (#417) renders the Azure Account (TLS-only, private, soft delete, never deleted, kept), Container, lifecycle ManagementPolicy, the UserAssignedIdentity + FederatedIdentityCredential for the s3proxy ServiceAccount, the bridged RoleAssignment and client-id Secret, and the s3proxy façade (Deployment from gsoci with ephemeral-storage requests and limit and its two emptyDirs bounded by the limit (#438), Service, the key pair in both namespaces, PDB, network policies, Substrate's egress to it); the façade alone renders no Crossplane object and reads an account key; the meta chart derives s3://<container>/<prefix> and the façade's S3 environment on both substrate components next to an installation's own; the guards (a disagreeing explicit location, role or s3proxy.azure.*, an unknown provider, missing inputs, a numeric account id, a bad account name, the bundled store on, an own S3 variable, the façade without an ephemeral-storage request or limit). Off and aws render as before.
	@echo "====> $@"
	@echo "--> off by default: no Crossplane object of the store in the substrate render, nothing derived"
	@helm template t $(CONNECTIVITY_DIR) -f $(CONNECTIVITY_DIR)/ci/test-substrate-values.yaml >/tmp/vss-off.out 2>&1 || { cat /tmp/vss-off.out; exit 1; }
	@if grep -qE '^  name: giantswarm-ci-substrate$$|agent-platform-substrate' /tmp/vss-off.out; then echo "FAIL: the snapshot store renders with the block off"; exit 1; fi
	@helm template t $(CHART_DIR) -f $(CHART_DIR)/ci/ci-values.yaml $(ENGINE_OFF) >/tmp/vss-meta-off.out 2>&1 || { cat /tmp/vss-meta-off.out; exit 1; }
	@grep -q 'snapshotLocation: s3://ci-agent-snapshots/agents' /tmp/vss-meta-off.out || { echo "FAIL: the explicit snapshotLocation does not reach the kagent release with the store off"; exit 1; }
	@if grep -q 'eks.amazonaws.com/role-arn' /tmp/vss-meta-off.out; then echo "FAIL: a role annotation is derived with the store off"; exit 1; fi
	@echo "ok: off by default"
	@echo "--> connectivity, block on: bucket quartet + role"
	@helm template t $(CONNECTIVITY_DIR) -f $(SUBSTRATE_STORE_CI) >/tmp/vss-on.out 2>&1 || { cat /tmp/vss-on.out; exit 1; }
	@for kind in Bucket BucketLifecycleConfiguration BucketPublicAccessBlock BucketPolicy Role; do \
		grep -A3 "^kind: $$kind$$" /tmp/vss-on.out | grep -q '^  name: giantswarm-ci-substrate$$' || { echo "FAIL: $$kind giantswarm-ci-substrate missing from the store render"; exit 1; }; \
	done
	@awk '/^kind: Bucket$$/,/^---/' /tmp/vss-on.out >/tmp/vss-bucket.out
	@grep -q 'helm.sh/resource-policy: keep' /tmp/vss-bucket.out || { echo "FAIL: the bucket is not kept on uninstall"; exit 1; }
	@if grep -q '"\*"' /tmp/vss-bucket.out; then echo "FAIL: the bucket carries the full management policy (it must never be deleted by Crossplane)"; exit 1; fi
	@grep -q 'app: agent-platform-substrate' /tmp/vss-bucket.out || { echo "FAIL: the bucket does not carry the substrate app tag"; exit 1; }
	@grep -q 'installation: ci' /tmp/vss-bucket.out || { echo "FAIL: the installation's own tag is missing"; exit 1; }
	@awk '/^kind: BucketLifecycleConfiguration$$/,/^---/' /tmp/vss-on.out | grep -q 'days: 30' || { echo "FAIL: the default lifecycle is not 30 days"; exit 1; }
	@awk '/^kind: BucketPolicy$$/,/^---/' /tmp/vss-on.out | grep -q '"aws:SecureTransport": "false"' || { echo "FAIL: no TLS-only bucket policy"; exit 1; }
	@awk '/^kind: BucketPublicAccessBlock$$/,/^---/' /tmp/vss-on.out | grep -q 'restrictPublicBuckets: true' || { echo "FAIL: no public-access block"; exit 1; }
	@awk '/^kind: Role$$/,/^---/' /tmp/vss-on.out >/tmp/vss-role.out
	@grep -q '"irsa.ci.example.com:sub": "system:serviceaccount:ate-system:atelet"' /tmp/vss-role.out || { echo "FAIL: the role does not trust atelet"; exit 1; }
	@grep -q '"irsa.ci.example.com:sub": "system:serviceaccount:ate-system:ate-api-server"' /tmp/vss-role.out || { echo "FAIL: the role does not trust ate-api-server"; exit 1; }
	@[ "$$(grep -c 'sts:AssumeRoleWithWebIdentity' /tmp/vss-role.out)" = "2" ] || { echo "FAIL: the trust policy has not exactly two statements"; exit 1; }
	@grep -q '"arn:aws:s3:::giantswarm-ci-substrate/\*"' /tmp/vss-role.out || { echo "FAIL: the inline policy does not cover the bucket's objects"; exit 1; }
	@grep -q 'Federated": "arn:aws:iam::123456789012:oidc-provider/irsa.ci.example.com' /tmp/vss-role.out || { echo "FAIL: the trust policy does not name the OIDC provider"; exit 1; }
	@if grep -q 'kagent-pg' /tmp/vss-role.out /tmp/vss-bucket.out; then echo "FAIL: the store render leaks the postgres store's names"; exit 1; fi
	@helm template t $(CONNECTIVITY_DIR) -f $(SUBSTRATE_STORE_CI) --set kagent.harness.snapshotStore.crossplane.aws.roleName=substrate-snapshots --set kagent.harness.snapshotStore.crossplane.region=cn-north-1 --set kagent.harness.snapshotStore.crossplane.observeOnly=true >/tmp/vss-cn.out 2>&1 || { cat /tmp/vss-cn.out; exit 1; }
	@grep -A3 '^kind: Role$$' /tmp/vss-cn.out | grep -q '^  name: substrate-snapshots$$' || { echo "FAIL: aws.roleName does not name the role"; exit 1; }
	@grep -q '"arn:aws-cn:s3:::giantswarm-ci-substrate"' /tmp/vss-cn.out || { echo "FAIL: the China partition is not derived from the region"; exit 1; }
	@grep -q 'sts.amazonaws.com.cn' /tmp/vss-cn.out || { echo "FAIL: the China STS audience is missing"; exit 1; }
	@if awk '/^kind: Bucket$$/,/^---/' /tmp/vss-cn.out | grep -q '\- Create'; then echo "FAIL: observeOnly still creates"; exit 1; fi
	@echo "ok: connectivity renders the store"
	@echo "--> meta chart, block on: the derived location for kagent, the block held back, the role annotation on both substrate ServiceAccounts"
	@$(SUBSTRATE_STORE_META) >/tmp/vss-meta.out 2>&1 || { cat /tmp/vss-meta.out; exit 1; }
	@awk '/^  name: kagent$$/,/^---/' /tmp/vss-meta.out >/tmp/vss-meta-kagent.out
	@grep -q 'snapshotLocation: s3://giantswarm-ci-substrate/kagent' /tmp/vss-meta-kagent.out || { echo "FAIL: kagent.harness.snapshotLocation is not derived from the bucket and prefix"; exit 1; }
	@if grep -q 'snapshotStore' /tmp/vss-meta-kagent.out; then echo "FAIL: the store block is forwarded to the kagent chart (components.kagent.omitKeys harness.snapshotStore)"; exit 1; fi
	@awk '/^  name: substrate$$/,/^---/' /tmp/vss-meta.out >/tmp/vss-meta-substrate.out
	@[ "$$(grep -c 'eks.amazonaws.com/role-arn: arn:aws:iam::123456789012:role/giantswarm-ci-substrate' /tmp/vss-meta-substrate.out)" = "2" ] || { echo "FAIL: the role annotation does not reach both substrate ServiceAccounts"; cat /tmp/vss-meta-substrate.out; exit 1; }
	@for k in atelet ateApiServer; do sed -n "/^    $$k:/,/^    [a-zA-Z]*:/p" /tmp/vss-meta-substrate.out | grep -q 'eks.amazonaws.com/role-arn' || { echo "FAIL: substrate.$$k.serviceAccount.annotations lacks the role"; exit 1; }; done
	@awk '/^  name: agent-platform-connectivity$$/,/^---/' /tmp/vss-meta.out | grep -q 'bucketName: giantswarm-ci-substrate' || { echo "FAIL: the store block does not reach the connectivity release"; exit 1; }
	@$(SUBSTRATE_STORE_META) --set kagent.harness.snapshotStore.prefix= 2>&1 | grep -q 'snapshotLocation: s3://giantswarm-ci-substrate$$' || { echo "FAIL: an empty prefix does not derive s3://<bucket>"; exit 1; }
	@$(SUBSTRATE_STORE_META) --set kagent.harness.snapshotLocation=s3://giantswarm-ci-substrate/kagent >/dev/null 2>&1 || { echo "FAIL: an explicit location that agrees with the store is refused"; exit 1; }
	@$(SUBSTRATE_STORE_META) --set 'substrate.atelet.serviceAccount.annotations.foo=bar' >/tmp/vss-meta-own.out 2>&1 || { cat /tmp/vss-meta-own.out; exit 1; }
	@sed -n '/^    atelet:/,/^    [a-zA-Z]*:/p' /tmp/vss-meta-own.out | grep -q 'foo: bar' || { echo "FAIL: an installation's own atelet ServiceAccount annotation is dropped by the derivation"; exit 1; }
	@echo "ok: the meta chart derives the location and the annotations"
	@echo "--> connectivity, capz: account, container, lifecycle, identity, federated credential, the bridging Objects, the façade"
	@helm template t $(CONNECTIVITY_DIR) -f $(SUBSTRATE_STORE_CAPZ_CI) >/tmp/vss-capz.out 2>&1 || { cat /tmp/vss-capz.out; exit 1; }
	@for kind in Account ManagementPolicy; do grep -A3 "^kind: $$kind$$" /tmp/vss-capz.out | grep -q '^  name: giantswarmcisubstrate$$' || { echo "FAIL: $$kind giantswarmcisubstrate missing from the capz render"; exit 1; }; done
	@grep -A3 '^kind: Container$$' /tmp/vss-capz.out | grep -q '^  name: giantswarm-ci-substrate$$' || { echo "FAIL: the Container is missing"; exit 1; }
	@for kind in UserAssignedIdentity FederatedIdentityCredential; do grep -A3 "^kind: $$kind$$" /tmp/vss-capz.out | grep -q '^  name: giantswarm-ci-substrate-identity$$' || { echo "FAIL: $$kind giantswarm-ci-substrate-identity missing (workloadIdentity.identityName defaults to <containerName>-identity)"; exit 1; }; done
	@awk '/^kind: Account$$/,/^---/' /tmp/vss-capz.out >/tmp/vss-capz-account.out
	@grep -q 'helm.sh/resource-policy: keep' /tmp/vss-capz-account.out || { echo "FAIL: the account is not kept on uninstall"; exit 1; }
	@if grep -q '"\*"' /tmp/vss-capz-account.out; then echo "FAIL: the account carries the full management policy (it must never be deleted by Crossplane)"; exit 1; fi
	@grep -q 'enableHttpsTrafficOnly: true' /tmp/vss-capz-account.out || { echo "FAIL: the account is not TLS-only"; exit 1; }
	@grep -q 'allowNestedItemsToBePublic: false' /tmp/vss-capz-account.out || { echo "FAIL: the account allows public blobs"; exit 1; }
	@grep -q 'app: agent-platform-substrate' /tmp/vss-capz-account.out || { echo "FAIL: the account does not carry the substrate app tag"; exit 1; }
	@awk '/^kind: ManagementPolicy$$/,/^---/' /tmp/vss-capz.out | grep -q 'deleteAfterDaysSinceModificationGreaterThan: 30' || { echo "FAIL: the default capz lifecycle is not 30 days"; exit 1; }
	@awk '/^kind: FederatedIdentityCredential$$/,/^---/' /tmp/vss-capz.out | grep -q 'subject: "system:serviceaccount:default:substrate-s3proxy"' || { echo "FAIL: the federated credential does not name the s3proxy ServiceAccount"; exit 1; }
	@grep -q 'roleDefinitionName: Storage Blob Data Contributor' /tmp/vss-capz.out || { echo "FAIL: no Storage Blob Data Contributor assignment"; exit 1; }
	@grep -q 'scope: "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/ci/providers/Microsoft.Storage/storageAccounts/giantswarmcisubstrate/blobServices/default/containers/giantswarm-ci-substrate"' /tmp/vss-capz.out || { echo "FAIL: the assignment is not scoped to the container"; exit 1; }
	@grep -q 'toFieldPath: stringData.AZURE_CLIENT_ID' /tmp/vss-capz.out || { echo "FAIL: the identity's client id is not bridged into the pods' Secret"; exit 1; }
	@grep -q 'name: provider-kubernetes-ci' /tmp/vss-capz.out || { echo "FAIL: the provider-kubernetes RBAC does not name the ServiceAccount"; exit 1; }
	@awk '/^kind: Deployment$$/,/^---/' /tmp/vss-capz.out >/tmp/vss-capz-deploy.out
	@grep -q 'image: gsoci.azurecr.io/giantswarm/s3proxy:' /tmp/vss-capz-deploy.out || { echo "FAIL: the s3proxy image is not pulled from gsoci"; exit 1; }
	@grep -q 'azure.workload.identity/use: "true"' /tmp/vss-capz-deploy.out || { echo "FAIL: the s3proxy pods do not use Workload Identity"; exit 1; }
	@grep -q 'value: "https://giantswarmcisubstrate.blob.core.windows.net"' /tmp/vss-capz-deploy.out || { echo "FAIL: JCLOUDS_ENDPOINT is not the account's blob endpoint"; exit 1; }
	@grep -A1 'name: JCLOUDS_CREDENTIAL$$' /tmp/vss-capz-deploy.out | grep -q 'value: ""' || { echo "FAIL: capz does not set JCLOUDS_CREDENTIAL to the empty string (the image's default remote-credential would make s3proxy sign with a shared key instead of DefaultAzureCredential, #436)"; exit 1; }
	@if grep -A3 'name: JCLOUDS_CREDENTIAL$$' /tmp/vss-capz-deploy.out | grep -q 'secretKeyRef'; then echo "FAIL: capz hands s3proxy an account key (DefaultAzureCredential expected)"; exit 1; fi
	@grep -q 'name: AZURE_CLIENT_ID' /tmp/vss-capz-deploy.out || { echo "FAIL: AZURE_CLIENT_ID is not read from the bridged Secret"; exit 1; }
	@[ "$$(grep -c '^kind: Secret$$' /tmp/vss-capz.out)" = "2" ] || { echo "FAIL: the key pair is not rendered in both the release namespace and ate-system"; exit 1; }
	@[ "$$(awk '/^kind: Secret$$/,/^---/' /tmp/vss-capz.out | grep -c 'helm.sh/resource-policy: keep')" = "2" ] || { echo "FAIL: a key-pair Secret is not kept on uninstall"; exit 1; }
	@grep -q 'readOnlyRootFilesystem: true' /tmp/vss-capz-deploy.out || { echo "FAIL: the façade's root filesystem is writable"; exit 1; }
	@grep -q 'runAsNonRoot: true' /tmp/vss-capz-deploy.out || { echo "FAIL: the façade runs as root"; exit 1; }
	@grep -q 'automountServiceAccountToken: false' /tmp/vss-capz-deploy.out || { echo "FAIL: the façade mounts the default ServiceAccount token"; exit 1; }
	@grep -q 'name: S3PROXY_JAVA_OPTS' /tmp/vss-capz-deploy.out || { echo "FAIL: the façade's JVM options are not set"; exit 1; }
	@grep -A2 '^ *limits:$$' /tmp/vss-capz-deploy.out | grep -q 'ephemeral-storage: 1Gi' || { echo "FAIL: the façade has no ephemeral-storage limit (Kyverno require-emptydir-requests-and-limits, #438)"; exit 1; }
	@grep -A3 '^ *requests:$$' /tmp/vss-capz-deploy.out | grep -q 'ephemeral-storage: 256Mi' || { echo "FAIL: the façade has no ephemeral-storage request (Kyverno require-emptydir-requests-and-limits, #438)"; exit 1; }
	@[ "$$(grep -c 'sizeLimit: "1Gi"' /tmp/vss-capz-deploy.out)" = "2" ] || { echo "FAIL: the façade's two emptyDirs (/tmp, /data) are not both bounded by the ephemeral-storage limit"; exit 1; }
	@if grep -q 'emptyDir: {}' /tmp/vss-capz-deploy.out; then echo "FAIL: the façade mounts an unbounded emptyDir"; exit 1; fi
	@[ "$$(helm template t $(CONNECTIVITY_DIR) -f $(SUBSTRATE_STORE_CAPZ_CI) --set kagent.harness.snapshotStore.s3proxy.resources.limits.ephemeral-storage=2Gi 2>&1 | awk '/^kind: Deployment$$/,/^---/' | grep -c 'sizeLimit: "2Gi"')" = "2" ] || { echo "FAIL: the emptyDirs' sizeLimit does not follow s3proxy.resources.limits.ephemeral-storage"; exit 1; }
	@grep -A3 '^kind: Service$$' /tmp/vss-capz.out | grep -q '^  name: substrate-s3proxy$$' || { echo "FAIL: the s3proxy Service is missing"; exit 1; }
	@grep -q '^kind: PodDisruptionBudget$$' /tmp/vss-capz.out || { echo "FAIL: the façade has no PodDisruptionBudget"; exit 1; }
	@awk '/^  name: substrate-s3proxy-ingress$$/,/^---/' /tmp/vss-capz.out >/tmp/vss-capz-ingress.out
	@grep -q 'values: \[atelet, ate-api-server\]' /tmp/vss-capz-ingress.out || { echo "FAIL: the façade's ingress policy does not select exactly atelet and ate-api-server"; exit 1; }
	@grep -q 'kubernetes.io/metadata.name: ate-system' /tmp/vss-capz-ingress.out || { echo "FAIL: the façade's ingress policy is not scoped to ate-system"; exit 1; }
	@[ "$$(grep -c 'podSelector' /tmp/vss-capz-ingress.out)" = "2" ] || { echo "FAIL: the façade's ingress policy admits more than the two Substrate clients"; exit 1; }
	@helm template t $(CONNECTIVITY_DIR) -f $(SUBSTRATE_STORE_CAPZ_CI) --set networkPolicy.flavor=cilium >/tmp/vss-capz-cilium.out 2>&1 || { cat /tmp/vss-capz-cilium.out; exit 1; }
	@for c in substrate-atelet substrate-ate-api-server; do awk "/^  name: $$c$$/,/^---/" /tmp/vss-capz-cilium.out | grep -q 'app.kubernetes.io/name: s3proxy' || { echo "FAIL: $$c has no egress to the façade"; exit 1; }; done
	@awk '/^  name: substrate-s3proxy$$/,/^---/' /tmp/vss-capz-cilium.out | grep -q 'port: "443"' || { echo "FAIL: the façade has no egress to the blob endpoint"; exit 1; }
	@if awk '/^  name: substrate-s3proxy$$/,/^---/' /tmp/vss-capz-cilium.out | grep -A3 'toEntities' | grep -q -- '- cluster'; then echo "FAIL: with capz the façade's egress admits the cluster entity on 443 (the apiserver)"; exit 1; fi
	@helm template t $(CONNECTIVITY_DIR) -f $(CONNECTIVITY_DIR)/ci/test-substrate-values.yaml $(SUBSTRATE_STORE_S3PROXY) --set networkPolicy.flavor=cilium 2>&1 | awk '/^  name: substrate-s3proxy$$/,/^---/' | grep -A3 'toEntities' | grep -q -- '- cluster' || { echo "FAIL: the façade alone has no egress to an in-cluster store"; exit 1; }
	@python3 tests/yaml-no-duplicate-keys.py /tmp/vss-capz.out || { echo "FAIL: the capz render repeats a mapping key (helm template tolerates it, the install does not)"; exit 1; }
	@echo "ok: connectivity renders the capz store and the façade"
	@echo "--> the façade alone (an account provisioned by hand, a lab's Azurite): no Crossplane object, an account key"
	@helm template t $(CONNECTIVITY_DIR) -f $(CONNECTIVITY_DIR)/ci/test-substrate-values.yaml $(SUBSTRATE_STORE_S3PROXY) >/tmp/vss-s3p.out 2>&1 || { cat /tmp/vss-s3p.out; exit 1; }
	@if grep -q -E '^kind: (Account|Container|UserAssignedIdentity|FederatedIdentityCredential|Object)$$' /tmp/vss-s3p.out; then echo "FAIL: the façade alone renders Crossplane objects"; exit 1; fi
	@awk '/^kind: Deployment$$/,/^---/' /tmp/vss-s3p.out | grep -A3 'name: JCLOUDS_CREDENTIAL$$' | grep -q 'secretKeyRef' || { echo "FAIL: the façade alone does not read the account key from the Secret"; exit 1; }
	@if awk '/^kind: Deployment$$/,/^---/' /tmp/vss-s3p.out | grep -q 'azure.workload.identity'; then echo "FAIL: the façade alone claims Workload Identity"; exit 1; fi
	@awk '/^kind: Deployment$$/,/^---/' /tmp/vss-s3p.out >/tmp/vss-s3p-deploy.out
	@grep -A2 '^ *limits:$$' /tmp/vss-s3p-deploy.out | grep -q 'ephemeral-storage: 1Gi' || { echo "FAIL: the façade alone has no ephemeral-storage limit (#438)"; exit 1; }
	@grep -A3 '^ *requests:$$' /tmp/vss-s3p-deploy.out | grep -q 'ephemeral-storage: 256Mi' || { echo "FAIL: the façade alone has no ephemeral-storage request (#438)"; exit 1; }
	@[ "$$(grep -c 'sizeLimit: "1Gi"' /tmp/vss-s3p-deploy.out)" = "2" ] || { echo "FAIL: the façade alone's two emptyDirs are not both bounded by the ephemeral-storage limit"; exit 1; }
	@echo "ok: the façade alone"
	@echo "--> meta chart, capz: the derived location, the façade's S3 environment on both substrate components"
	@$(SUBSTRATE_STORE_META_CAPZ) >/tmp/vss-meta-capz.out 2>&1 || { cat /tmp/vss-meta-capz.out; exit 1; }
	@awk '/^  name: kagent$$/,/^---/' /tmp/vss-meta-capz.out | grep -q 'snapshotLocation: s3://giantswarm-ci-substrate/kagent' || { echo "FAIL: the location is not derived from the container and the prefix"; exit 1; }
	@if awk '/^  name: kagent$$/,/^---/' /tmp/vss-meta-capz.out | grep -q 's3proxy'; then echo "FAIL: the façade block is forwarded to the kagent chart"; exit 1; fi
	@awk '/^  name: substrate$$/,/^---/' /tmp/vss-meta-capz.out >/tmp/vss-meta-capz-substrate.out
	@[ "$$(grep -c 'value: http://substrate-s3proxy.default.svc:80' /tmp/vss-meta-capz-substrate.out)" = "2" ] || { echo "FAIL: AWS_ENDPOINT_URL does not reach both substrate components"; cat /tmp/vss-meta-capz-substrate.out; exit 1; }
	@[ "$$(grep -c 'name: substrate-s3proxy' /tmp/vss-meta-capz-substrate.out)" = "4" ] || { echo "FAIL: the key pair Secret is not read by both components"; exit 1; }
	@if grep -q 'eks.amazonaws.com/role-arn' /tmp/vss-meta-capz-substrate.out; then echo "FAIL: capz derives an IRSA annotation"; exit 1; fi
	@awk '/^  name: agent-platform-connectivity$$/,/^---/' /tmp/vss-meta-capz.out | grep -q 'ephemeral-storage: 1Gi' || { echo "FAIL: the meta chart's default s3proxy.resources (ephemeral-storage) does not reach the connectivity release"; exit 1; }
	@$(SUBSTRATE_STORE_META_CAPZ) --set 'substrate.atelet.extraEnv[0].name=FOO' --set 'substrate.atelet.extraEnv[0].value=bar' 2>&1 | awk '/^  name: substrate$$/,/^---/' | grep -q 'name: FOO' || { echo "FAIL: an installation's own atelet extraEnv entry is dropped by the derivation"; exit 1; }
	@helm template t $(CHART_DIR) -f $(CHART_DIR)/ci/ci-values.yaml $(ENGINE_OFF) $(SUBSTRATE_STORE_S3PROXY) 2>&1 | awk '/^  name: kagent$$/,/^---/' | grep -q 'snapshotLocation: s3://ate-snapshots/kagent' || { echo "FAIL: the façade alone does not derive the location at the meta chart"; exit 1; }
	@echo "ok: the meta chart wires the façade"
	@echo "--> guards"
	@if $(SUBSTRATE_STORE_META) --set kagent.harness.snapshotLocation=s3://other/agents >/tmp/vss-g1.out 2>&1; then echo "FAIL: a disagreeing explicit snapshotLocation rendered"; exit 1; fi
	@grep -q 'kagent.harness.snapshotLocation (s3://other/agents) differs from the location kagent.harness.snapshotStore renders (s3://giantswarm-ci-substrate/kagent)' /tmp/vss-g1.out || { echo "FAIL: the location guard does not name both"; cat /tmp/vss-g1.out; exit 1; }
	@if helm template t $(CONNECTIVITY_DIR) -f $(SUBSTRATE_STORE_CI) --set kagent.harness.snapshotLocation=s3://other/agents >/tmp/vss-g1c.out 2>&1; then echo "FAIL: connectivity rendered a disagreeing explicit snapshotLocation"; exit 1; fi
	@grep -q 'differs from the location kagent.harness.snapshotStore renders' /tmp/vss-g1c.out || { echo "FAIL: the connectivity location guard is silent"; exit 1; }
	@if $(SUBSTRATE_STORE_META) --set 'substrate.ateApiServer.serviceAccount.annotations.eks\.amazonaws\.com/role-arn=arn:aws:iam::123456789012:role/other' >/tmp/vss-g2.out 2>&1; then echo "FAIL: a disagreeing explicit role annotation rendered"; exit 1; fi
	@grep -q 'substrate.ateApiServer.serviceAccount.annotations\[eks.amazonaws.com/role-arn\] (arn:aws:iam::123456789012:role/other) differs from the IRSA role' /tmp/vss-g2.out || { echo "FAIL: the role guard does not name the key"; cat /tmp/vss-g2.out; exit 1; }
	@if helm template t $(CONNECTIVITY_DIR) -f $(SUBSTRATE_STORE_CI) --set kagent.harness.snapshotStore.crossplane.provider=azure >/tmp/vss-g3.out 2>&1; then echo "FAIL: provider azure rendered"; exit 1; fi
	@grep -q 'provider=azure is not supported' /tmp/vss-g3.out || { echo "FAIL: the provider guard is silent"; exit 1; }
	@for k in providerConfigRef region; do \
		if helm template t $(CONNECTIVITY_DIR) -f $(SUBSTRATE_STORE_CI) --set kagent.harness.snapshotStore.crossplane.$$k= >/tmp/vss-g4.out 2>&1; then echo "FAIL: crossplane.$$k empty rendered"; exit 1; fi; \
		grep -q "kagent.harness.snapshotStore.crossplane.$$k is required" /tmp/vss-g4.out || { echo "FAIL: the guard for crossplane.$$k is silent"; exit 1; }; \
	done
	@for k in bucketName accountId oidcProvider; do \
		if helm template t $(CONNECTIVITY_DIR) -f $(SUBSTRATE_STORE_CI) --set kagent.harness.snapshotStore.crossplane.aws.$$k= >/tmp/vss-g5.out 2>&1; then echo "FAIL: crossplane.aws.$$k empty rendered"; exit 1; fi; \
		grep -q "kagent.harness.snapshotStore.crossplane.aws.$$k is required" /tmp/vss-g5.out || { echo "FAIL: the guard for crossplane.aws.$$k is silent"; exit 1; }; \
	done
	@if helm template t $(CONNECTIVITY_DIR) -f $(SUBSTRATE_STORE_CI) --set kagent.harness.snapshotStore.crossplane.aws.accountId=123456789012 >/tmp/vss-g6.out 2>&1; then echo "FAIL: a numeric accountId rendered (it would print as a float in the ARN)"; exit 1; fi
	@grep -q 'must be the 12-digit AWS account id, quoted as a string' /tmp/vss-g6.out || { echo "FAIL: the accountId guard is silent"; cat /tmp/vss-g6.out; exit 1; }
	@if $(SUBSTRATE_STORE_META_BASE) --set kagent.harness.snapshotStore.crossplane.aws.accountId=123456789012 >/tmp/vss-g7.out 2>&1; then echo "FAIL: the meta chart rendered a numeric accountId"; exit 1; fi
	@grep -q 'must be the 12-digit AWS account id, quoted as a string' /tmp/vss-g7.out || { echo "FAIL: the meta accountId guard is silent"; cat /tmp/vss-g7.out; exit 1; }
	@if helm template t $(CONNECTIVITY_DIR) -f $(SUBSTRATE_STORE_CAPZ_CI) --set kagent.harness.snapshotStore.crossplane.capz.storageAccountName=Bad-Name >/tmp/vss-g8.out 2>&1; then echo "FAIL: a bad storage account name rendered"; exit 1; fi
	@grep -q 'must be 3 to 24 lowercase letters and digits' /tmp/vss-g8.out || { echo "FAIL: the account-name guard is silent"; cat /tmp/vss-g8.out; exit 1; }
	@for k in storageAccountName containerName resourceGroup subscriptionId; do \
		if helm template t $(CONNECTIVITY_DIR) -f $(SUBSTRATE_STORE_CAPZ_CI) --set kagent.harness.snapshotStore.crossplane.capz.$$k= >/tmp/vss-g9.out 2>&1; then echo "FAIL: crossplane.capz.$$k empty rendered"; exit 1; fi; \
		grep -q "kagent.harness.snapshotStore.crossplane.capz.$$k is required" /tmp/vss-g9.out || { echo "FAIL: the guard for crossplane.capz.$$k is silent"; exit 1; }; \
	done
	@for k in oidcIssuerUrl providerKubernetes.providerConfigRef; do \
		if helm template t $(CONNECTIVITY_DIR) -f $(SUBSTRATE_STORE_CAPZ_CI) --set kagent.harness.snapshotStore.crossplane.capz.workloadIdentity.$$k= >/tmp/vss-g10.out 2>&1; then echo "FAIL: capz.workloadIdentity.$$k empty rendered"; exit 1; fi; \
		grep -q "workloadIdentity.$$k is required" /tmp/vss-g10.out || { echo "FAIL: the guard for capz.workloadIdentity.$$k is silent"; exit 1; }; \
	done
	@if helm template t $(CONNECTIVITY_DIR) -f $(SUBSTRATE_STORE_CAPZ_CI) --set kagent.harness.snapshotStore.s3proxy.azure.account=other >/tmp/vss-g11.out 2>&1; then echo "FAIL: a disagreeing s3proxy.azure.account rendered"; exit 1; fi
	@grep -q 'differs from what kagent.harness.snapshotStore.crossplane.capz renders' /tmp/vss-g11.out || { echo "FAIL: the s3proxy.azure guard is silent"; exit 1; }
	@if $(SUBSTRATE_STORE_META_CAPZ) --set substrate.rustfs.enabled=true >/tmp/vss-g12.out 2>&1; then echo "FAIL: the bundled store rendered next to the façade"; exit 1; fi
	@grep -q 'substrate.rustfs.enabled is on while' /tmp/vss-g12.out || { echo "FAIL: the rustfs guard is silent"; exit 1; }
	@if $(SUBSTRATE_STORE_META_CAPZ) --set 'substrate.ateApiServer.extraEnv[0].name=AWS_ENDPOINT_URL' --set 'substrate.ateApiServer.extraEnv[0].value=http://other' >/tmp/vss-g13.out 2>&1; then echo "FAIL: an own AWS_ENDPOINT_URL rendered next to the derived one"; exit 1; fi
	@grep -q 'substrate.ateApiServer.extraEnv names AWS_ENDPOINT_URL' /tmp/vss-g13.out || { echo "FAIL: the extraEnv guard is silent"; exit 1; }
	@if helm template t $(CONNECTIVITY_DIR) -f $(CONNECTIVITY_DIR)/ci/test-substrate-values.yaml --set kagent.harness.snapshotLocation= --set kagent.harness.snapshotStore.s3proxy.enabled=true >/tmp/vss-g14.out 2>&1; then echo "FAIL: the façade alone rendered without an account"; exit 1; fi
	@grep -q 's3proxy.azure.endpoint is required' /tmp/vss-g14.out || { echo "FAIL: the façade's account guard is silent"; exit 1; }
	@if helm template t $(CONNECTIVITY_DIR) -f $(CONNECTIVITY_DIR)/ci/test-substrate-values.yaml $(SUBSTRATE_STORE_S3PROXY) --set kagent.harness.snapshotStore.s3proxy.azure.endpoint=azurite:10000 >/tmp/vss-g15.out 2>&1; then echo "FAIL: an endpoint without a scheme rendered"; exit 1; fi
	@grep -q 'must be an http(s) URL' /tmp/vss-g15.out || { echo "FAIL: the endpoint guard is silent"; exit 1; }
	@if helm template t $(CONNECTIVITY_DIR) -f $(CONNECTIVITY_DIR)/ci/test-substrate-values.yaml $(SUBSTRATE_STORE_S3PROXY) --set kagent.harness.snapshotStore.s3proxy.azure.accountKeySecretRef.name= >/tmp/vss-g16.out 2>&1; then echo "FAIL: the façade alone rendered without an account key"; exit 1; fi
	@grep -q 'accountKeySecretRef.name and .key are required' /tmp/vss-g16.out || { echo "FAIL: the account-key guard is silent"; exit 1; }
	@for k in requests limits; do \
		if helm template t $(CONNECTIVITY_DIR) -f $(SUBSTRATE_STORE_CAPZ_CI) --set kagent.harness.snapshotStore.s3proxy.resources.$$k.ephemeral-storage=null >/tmp/vss-g17.out 2>&1; then echo "FAIL: the façade rendered without resources.$$k.ephemeral-storage (Kyverno require-emptydir-requests-and-limits, #438)"; exit 1; fi; \
		grep -q "s3proxy.resources.$$k.ephemeral-storage is required" /tmp/vss-g17.out || { echo "FAIL: the ephemeral-storage guard for $$k is silent"; cat /tmp/vss-g17.out; exit 1; }; \
	done
	@if helm template t $(CONNECTIVITY_DIR) -f $(SUBSTRATE_STORE_CAPZ_CI) --set kagent.harness.snapshotStore.s3proxy.azure.accountKeySecretRef.name=x --set kagent.harness.snapshotStore.s3proxy.azure.accountKeySecretRef.key=k >/tmp/vss-g17.out 2>&1; then echo "FAIL: capz rendered with an account key"; exit 1; fi
	@grep -q 'accountKeySecretRef is set next to crossplane.provider capz' /tmp/vss-g17.out || { echo "FAIL: the capz account-key guard is silent"; exit 1; }
	@if helm template t $(CONNECTIVITY_DIR) -f $(SUBSTRATE_STORE_CI) --set kagent.harness.snapshotStore.s3proxy.enabled=true >/tmp/vss-g18.out 2>&1; then echo "FAIL: the façade rendered next to an aws bucket"; exit 1; fi
	@grep -q 's3proxy.enabled is on next to crossplane.provider aws' /tmp/vss-g18.out || { echo "FAIL: the aws+façade guard is silent"; exit 1; }
	@echo "ok: guards"
	@echo "====> $@ passed"

.PHONY: verify-postgres-kagent-v2
verify-postgres: verify-postgres-kagent-v2
verify-postgres-kagent-v2: ## Assert the kagent_v2 database entry (#346): the CNPG Database (name, owner, vector, retain) on the platform Cluster, the 30-day drop of the 0.10 database (applicationDatabase.ensure), the entry reaching the connectivity databases hook (its mechanism is verify-postgres' own, #342) to feed the derived Secret kagent-pg-kagent-v2-app, the controller mount example, the meta chart's forwarding, and the entry's component/postgres gating.
	@echo "====> $@ ($(CONNECTIVITY_DIR), $(CHART_DIR))"
	@echo "--> postgres on: ONE Database, kagent-pg-kagent-v2 (kagent_v2, owner kagent, vector, retain) on the existing Cluster; the 0.10 database is no Database of the chart by default"
	@helm template t $(CONNECTIVITY_DIR) $(PG_ON) >/tmp/vpv2.out 2>&1 || { cat /tmp/vpv2.out; exit 1; }
	@[ "$$(grep -c '^kind: Database$$' /tmp/vpv2.out)" = "1" ] || { echo "FAIL: expected exactly one Database in the default postgres shape"; grep -n -A3 '^kind: Database$$' /tmp/vpv2.out; exit 1; }
	@$(PICK) /tmp/vpv2.out Database kagent-pg-kagent-v2 kagent >/tmp/vpv2-db.out || { echo "FAIL: no Database kagent-pg-kagent-v2 in the kagent namespace"; exit 1; }
	@grep -q '^  name: kagent_v2$$' /tmp/vpv2-db.out || { echo "FAIL: spec.name is not kagent_v2"; exit 1; }
	@grep -q '^  owner: kagent$$' /tmp/vpv2-db.out || { echo "FAIL: the owner is not kagent (the bootstrap owner, whose Secret is derived)"; exit 1; }
	@grep -A1 '^  cluster:$$' /tmp/vpv2-db.out | grep -q 'name: kagent-pg' || { echo "FAIL: the Database does not name the existing Cluster kagent-pg"; exit 1; }
	@grep -q '^  databaseReclaimPolicy: retain$$' /tmp/vpv2-db.out || { echo "FAIL: databaseReclaimPolicy is not retain"; exit 1; }
	@grep -A1 '^  extensions:$$' /tmp/vpv2-db.out | grep -q 'name: vector' || { echo "FAIL: the vector extension is missing"; exit 1; }
	@if $(PICK) /tmp/vpv2.out Database kagent-pg-kagent kagent >/dev/null 2>&1; then echo "FAIL: the 0.10 database (kagent-pg-kagent) rendered as a Database by default (ImageVolume off)"; exit 1; fi
	@if ! grep -q '^kind: Cluster$$' /tmp/vpv2.out; then echo "FAIL: the Cluster is gone"; exit 1; fi
	@echo "ok: Database"
	@echo "--> ImageVolume mode: the existing kagent-pg-kagent Database (the bootstrap database's extension) renders untouched next to kagent-v2"
	@helm template t $(CONNECTIVITY_DIR) $(PG_ON) --set postgres.vector.enabled=true --set postgres.vector.extensionImage.reference=$(PGVECTOR_IMG) >/tmp/vpv2-iv.out 2>&1 || { cat /tmp/vpv2-iv.out; exit 1; }
	@[ "$$(grep -c '^kind: Database$$' /tmp/vpv2-iv.out)" = "2" ] || { echo "FAIL: expected the bootstrap database's Database and kagent-v2"; exit 1; }
	@$(PICK) /tmp/vpv2-iv.out Database kagent-pg-kagent kagent >/tmp/vpv2-iv-db.out || { echo "FAIL: the bootstrap database's Database kagent-pg-kagent is gone"; exit 1; }
	@grep -q '^  name: kagent$$' /tmp/vpv2-iv-db.out || { echo "FAIL: the bootstrap database's Database kagent-pg-kagent changed"; exit 1; }
	@if grep -q 'ensure:' /tmp/vpv2-iv-db.out; then echo "FAIL: ensure renders by default (the object must stay byte-identical)"; exit 1; fi
	@echo "--> the 30-day drop in that mode: postgres.applicationDatabase.ensure=absent renders ensure: absent on that object only"
	@helm template t $(CONNECTIVITY_DIR) $(PG_ON) --set postgres.vector.enabled=true --set postgres.vector.extensionImage.reference=$(PGVECTOR_IMG) --set postgres.applicationDatabase.ensure=absent >/tmp/vpv2-drop.out 2>&1 || { cat /tmp/vpv2-drop.out; exit 1; }
	@$(PICK) /tmp/vpv2-drop.out Database kagent-pg-kagent kagent | grep -q '^  ensure: absent$$' || { echo "FAIL: ensure=absent did not reach the 0.10 database's object"; exit 1; }
	@if $(PICK) /tmp/vpv2-drop.out Database kagent-pg-kagent-v2 kagent | grep -q 'ensure:'; then echo "FAIL: ensure=absent leaked onto kagent-v2"; exit 1; fi
	@if helm template t $(CONNECTIVITY_DIR) $(PG_ON) --set postgres.applicationDatabase.ensure=gone >/tmp/vpv2-g0.out 2>&1; then echo "FAIL: a bogus ensure rendered"; exit 1; \
	elif ! grep -q 'ensure' /tmp/vpv2-g0.out; then echo "FAIL: the ensure schema check failed for the wrong reason"; cat /tmp/vpv2-g0.out; exit 1; else echo "ok: ensure enum"; fi
	@echo "ok: ImageVolume mode"
	@echo "--> the entry reaches the connectivity databases hook (#342's mechanism, tested by verify-postgres): its derive line feeds the derived Secret kagent-pg-kagent-v2-app in the kagent namespace"
	@$(PICK) /tmp/vpv2.out Job t-postgres-databases >/tmp/vpv2-job.out || { echo "FAIL: no databases derive hook Job for the kagent-v2 entry"; exit 1; }
	@grep -q 'derive "kagent-v2" "kagent_v2" "kagent"' /tmp/vpv2-job.out || { echo "FAIL: the databases hook does not derive kagent-v2 (kagent_v2) in the kagent namespace"; grep derive /tmp/vpv2-job.out; exit 1; }
	@grep -q 'cluster="kagent-pg"' /tmp/vpv2-job.out || { echo "FAIL: the derive hook does not source the bootstrap Secret of the kagent-pg Cluster"; exit 1; }
	@echo "ok: derive hook carries kagent-v2"
	@echo "--> the controller mount follows the derived Secret kagent-pg-kagent-v2-app where the chart documents it"
	@grep -q 'secretName: kagent-pg-kagent-v2-app' $(CONNECTIVITY_DIR)/ci/test-postgres-values.yaml || { echo "FAIL: ci/test-postgres-values.yaml does not mount kagent-pg-kagent-v2-app"; exit 1; }
	@grep -q 'urlFile: /etc/cnpg/uri' $(CONNECTIVITY_DIR)/ci/test-postgres-values.yaml || { echo "FAIL: ci/test-postgres-values.yaml lost the urlFile mount"; exit 1; }
	@grep -q 'secretName: kagent-pg-kagent-v2-app' $(CONNECTIVITY_DIR)/values.yaml || { echo "FAIL: the connectivity values.yaml mount example still names the bootstrap Secret"; exit 1; }
	@grep -q 'secretName: kagent-pg-kagent-v2-app' $(CHART_DIR)/values.yaml || { echo "FAIL: the meta values.yaml mount example still names the bootstrap Secret"; exit 1; }
	@if grep -q 'secretName: kagent-pg-app' $(CONNECTIVITY_DIR)/values.yaml $(CHART_DIR)/values.yaml $(CONNECTIVITY_DIR)/ci/test-postgres-values.yaml; then echo "FAIL: a mount example still names the bootstrap Secret kagent-pg-app"; exit 1; fi
	@helm template t $(CONNECTIVITY_DIR) $(VM) -f $(CONNECTIVITY_DIR)/ci/test-postgres-values.yaml >/dev/null 2>&1 || { echo "FAIL: ci/test-postgres-values.yaml does not render"; exit 1; }
	@echo "ok: mount example"
	@echo "--> the meta chart declares and forwards the entry: the connectivity release's values carry postgres.databases.kagent-v2 and hooks.kubectlImage"
	@helm template t $(CHART_DIR) -f $(CHART_DIR)/ci/ci-values.yaml --set postgres.enabled=true >/tmp/vpv2-meta.out 2>&1 || { cat /tmp/vpv2-meta.out; exit 1; }
	@$(PICK) /tmp/vpv2-meta.out HelmRelease agent-platform-connectivity >/tmp/vpv2-meta-conn.out || { echo "FAIL: no connectivity HelmRelease"; exit 1; }
	@grep -A8 '^        kagent-v2:$$' /tmp/vpv2-meta-conn.out | grep -q 'name: kagent_v2' || { echo "FAIL: postgres.databases.kagent-v2 is not forwarded to the connectivity release"; grep -n -A8 'kagent-v2:' /tmp/vpv2-meta-conn.out | head -12; exit 1; }
	@grep -q 'repository: giantswarm/alpine-k8s' /tmp/vpv2-meta-conn.out || { echo "FAIL: hooks.kubectlImage is not forwarded to the connectivity release"; exit 1; }
	@echo "ok: forwarded"
	@echo "--> the kagent-v2 entry drops with a disabled entry, its component off, or postgres off (the map mechanism itself is verify-postgres' own)"
	@helm template t $(CONNECTIVITY_DIR) $(PG_ON) --set 'postgres.databases.kagent-v2.enabled=false' >/tmp/vpv2-disabled.out 2>&1 || { cat /tmp/vpv2-disabled.out; exit 1; }
	@if grep -q 'kagent-pg-kagent-v2' /tmp/vpv2-disabled.out; then echo "FAIL: a disabled kagent-v2 entry still renders its Database"; exit 1; fi
	@helm template t $(CONNECTIVITY_DIR) $(VM) --set postgres.enabled=true >/tmp/vpv2-comp.out 2>&1 || { cat /tmp/vpv2-comp.out; exit 1; }
	@if grep -q 'kagent-pg-kagent-v2' /tmp/vpv2-comp.out; then echo "FAIL: the kagent-v2 entry renders while its component (kagent) is off"; exit 1; fi
	@helm template t $(CONNECTIVITY_DIR) $(VM) --set components.kagent.enabled=true >/tmp/vpv2-off.out 2>&1 || { cat /tmp/vpv2-off.out; exit 1; }
	@if grep -q 'kagent-pg-kagent-v2' /tmp/vpv2-off.out; then echo "FAIL: the kagent-v2 Database renders with postgres off"; exit 1; fi
	@echo "ok: enabled, component, postgres off"
	@echo "ok: $@"

.PHONY: verify-identity-migration
verify-identity: verify-identity-migration
verify-identity-migration: ## Assert the migration's ClusterRoleBinding is the chart's only cluster-scoped binding (#346): exactly one when the migration is on — named, bound to the CRD ClusterRole (get, delete on the five removed CRDs, nothing more) and to the tenant ServiceAccount, following a renamed identity — and none when the migration or agent-manager is off.
	@echo "====> $@ ($(CONNECTIVITY_DIR))"
	@echo "--> migration on: exactly one ClusterRoleBinding and one ClusterRole, the CRD pair"
	@helm template t $(CONNECTIVITY_DIR) $(MIGRATION_ON) >/tmp/vim-on.out 2>&1 || { cat /tmp/vim-on.out; exit 1; }
	@[ "$$(grep -c '^kind: ClusterRoleBinding$$' /tmp/vim-on.out)" = "1" ] || { echo "FAIL: expected exactly one ClusterRoleBinding with the migration on"; grep -n -A3 '^kind: ClusterRoleBinding$$' /tmp/vim-on.out; exit 1; }
	@[ "$$(grep -c '^kind: ClusterRole$$' /tmp/vim-on.out)" = "1" ] || { echo "FAIL: expected exactly one ClusterRole with the migration on"; exit 1; }
	@$(PICK) /tmp/vim-on.out ClusterRoleBinding $(MIGRATION_JOB)-crds >/tmp/vim-crb.out || { echo "FAIL: the ClusterRoleBinding is not $(MIGRATION_JOB)-crds"; exit 1; }
	@grep -A3 '^roleRef:' /tmp/vim-crb.out | grep -q 'kind: ClusterRole' || { echo "FAIL: the roleRef is not a ClusterRole"; exit 1; }
	@grep -A3 '^roleRef:' /tmp/vim-crb.out | grep -q 'name: $(MIGRATION_JOB)-crds' || { echo "FAIL: the roleRef does not name the CRD ClusterRole"; exit 1; }
	@grep -A3 '^subjects:' /tmp/vim-crb.out | grep -q 'name: kagent-flux' || { echo "FAIL: the subject is not the tenant ServiceAccount"; exit 1; }
	@grep -A3 '^subjects:' /tmp/vim-crb.out | grep -q 'namespace: kagent' || { echo "FAIL: the subject is not in the kagent namespace"; exit 1; }
	@if grep -q 'helm.sh/hook' /tmp/vim-crb.out; then echo "FAIL: the binding is a hook resource; the migration's Job is plain and re-runs, its rights must outlive one hook event"; exit 1; fi
	@$(PICK) /tmp/vim-on.out ClusterRole $(MIGRATION_JOB)-crds >/tmp/vim-cr.out || { echo "FAIL: no CRD ClusterRole"; exit 1; }
	@for crd in agents.kagent.dev sandboxagents.kagent.dev agentharnesses.kagent.dev memories.kagent.dev toolservers.kagent.dev; do grep -q "^      - $$crd$$" /tmp/vim-cr.out || { echo "FAIL: the ClusterRole does not name $$crd"; exit 1; }; done
	@[ "$$(grep -c '^      - .*\.kagent\.dev$$' /tmp/vim-cr.out)" = "5" ] || { echo "FAIL: the ClusterRole names more or fewer than the five removed CRDs"; exit 1; }
	@grep -q 'verbs: \["get", "delete"\]' /tmp/vim-cr.out || { echo "FAIL: the ClusterRole's verbs are not exactly get, delete"; grep verbs /tmp/vim-cr.out; exit 1; }
	@grep -q 'resources: \["customresourcedefinitions"\]' /tmp/vim-cr.out || { echo "FAIL: the ClusterRole is not confined to customresourcedefinitions"; exit 1; }
	@echo "ok: the one ClusterRoleBinding"
	@echo "--> a renamed identity: the subject follows kagent.fluxServiceAccountName"
	@helm template t $(CONNECTIVITY_DIR) $(MIGRATION_ON) --set kagent.fluxServiceAccountName=tenant-x >/tmp/vim-x.out 2>&1 || { cat /tmp/vim-x.out; exit 1; }
	@$(PICK) /tmp/vim-x.out ClusterRoleBinding $(MIGRATION_JOB)-crds | grep -A3 '^subjects:' | grep -q 'name: tenant-x' || { echo "FAIL: the subject did not follow the renamed identity"; exit 1; }
	@if grep -q 'kagent-flux' /tmp/vim-x.out; then echo "FAIL: the old name survives with a renamed identity"; grep -n kagent-flux /tmp/vim-x.out; exit 1; fi
	@echo "ok: renamed identity"
	@echo "--> off: no ClusterRoleBinding with the migration off, with agent-manager off (verify-identity's own kagent-only case), with kagent off"
	@helm template t $(CONNECTIVITY_DIR) $(MIGRATION_ON) --set agentManager.migration.enabled=false >/tmp/vim-off.out 2>&1 || { cat /tmp/vim-off.out; exit 1; }
	@if grep -qE '^kind: ClusterRole(Binding)?$$' /tmp/vim-off.out; then echo "FAIL: a cluster-scoped object renders with the migration off"; exit 1; fi
	@helm template t $(CONNECTIVITY_DIR) $(MANAGERS_MIN) >/tmp/vim-noam.out 2>&1 || { cat /tmp/vim-noam.out; exit 1; }
	@if grep -qE '^kind: ClusterRole(Binding)?$$' /tmp/vim-noam.out; then echo "FAIL: a cluster-scoped object renders with agent-manager off"; exit 1; fi
	@echo "ok: none while off"
	@echo "ok: $@"

.PHONY: verify-migration
verify-migration: ## Assert the agent-manager migrate Job of the kagent API v2 cut-over (#346): off by default and with agent-manager off, on with kagent + agent-manager; a PLAIN Job named with the hash of its pod template — image, args, environment, identity, labels; a changed template renders a new Job, an unchanged one is re-applied as is (no hook: a migrate failure never fails the release, and the meta chart stops the connectivity HelmRelease from waiting on Jobs), the template byte-identical across chart versions while the Job's own labels follow them (Job.spec.template is immutable, #399), as the helper's ServiceAccount, from agent-manager's image at the value's tag, `migrate` (+ --dry-run), GITHUB_TOKEN optional from the value-named Secret, its inputs as environment; the RBAC set (the CRD pair, the per-namespace reads); the network policy in both flavors; the guards; the meta chart's forwarding.
	@echo "====> $@ ($(CONNECTIVITY_DIR), $(CHART_DIR))"
	@echo "--> off by default: kagent alone renders nothing of the migration; the ATS smoke keeps it off — its kagent is fresh, there is no 0.10 agent to migrate (agentlab#143 rehearses the migration)"
	@helm template t $(CONNECTIVITY_DIR) $(VM) --set components.kagent.enabled=true >/tmp/vmig-off.out 2>&1 || { cat /tmp/vmig-off.out; exit 1; }
	@if grep -q 'agent-manager-migrate' /tmp/vmig-off.out; then echo "FAIL: the migration renders with agent-manager off"; exit 1; else echo "ok: inert without agent-manager"; fi
	@echo "--> on: a PLAIN Job (no hook) in the kagent namespace, named with an 8-hex hash of its pod template (a changed image or identity renders a new name; identical renders the same), as the tenant identity, agent-manager's image, migrate, its inputs as environment, the optional token"
	@helm template t $(CONNECTIVITY_DIR) $(MIGRATION_ON) >/tmp/vmig-on.out 2>&1 || { cat /tmp/vmig-on.out; exit 1; }
	@$(PICK) /tmp/vmig-on.out Job '$(MIGRATION_JOB)-*' kagent >/tmp/vmig-job.out || { echo "FAIL: no Job $(MIGRATION_JOB)-<hash> in the kagent namespace"; exit 1; }
	@grep -qE '^  name: $(MIGRATION_JOB)-[0-9a-f]{8}$$' /tmp/vmig-job.out || { echo "FAIL: the Job's name does not carry the 8-hex spec hash"; grep '^  name:' /tmp/vmig-job.out; exit 1; }
	@if grep -q 'helm.sh/hook' /tmp/vmig-job.out; then echo "FAIL: the Job is a Helm hook; a migrate failure would fail the connectivity upgrade that renders the Harness"; exit 1; fi
	@grep -q 'ttlSecondsAfterFinished: 86400' /tmp/vmig-job.out || { echo "FAIL: the finished Job is not kept a day"; exit 1; }
	@helm template t $(CONNECTIVITY_DIR) $(MIGRATION_ON) --set agentManager.migration.image.tag=9.9.9 >/tmp/vmig-hash.out 2>&1 || { cat /tmp/vmig-hash.out; exit 1; }
	@[ "$$(grep -E '^  name: $(MIGRATION_JOB)-[0-9a-f]{8}$$' /tmp/vmig-hash.out)" != "$$(grep -E '^  name: $(MIGRATION_JOB)-[0-9a-f]{8}$$' /tmp/vmig-on.out)" ] || { echo "FAIL: a changed image did not change the Job's name (Job.spec.template is immutable)"; exit 1; }
	@helm template t $(CONNECTIVITY_DIR) $(MIGRATION_ON) --set kagent.fluxServiceAccountName=tenant-x >/tmp/vmig-hash-sa.out 2>&1 || { cat /tmp/vmig-hash-sa.out; exit 1; }
	@[ "$$(grep -E '^  name: $(MIGRATION_JOB)-[0-9a-f]{8}$$' /tmp/vmig-hash-sa.out)" != "$$(grep -E '^  name: $(MIGRATION_JOB)-[0-9a-f]{8}$$' /tmp/vmig-on.out)" ] || { echo "FAIL: a changed identity did not change the Job's name (the name hashes the whole pod template)"; exit 1; }
	@helm template t $(CONNECTIVITY_DIR) $(MIGRATION_ON) >/tmp/vmig-again.out 2>&1 || { cat /tmp/vmig-again.out; exit 1; }
	@[ "$$(grep -E '^  name: $(MIGRATION_JOB)-[0-9a-f]{8}$$' /tmp/vmig-again.out)" = "$$(grep -E '^  name: $(MIGRATION_JOB)-[0-9a-f]{8}$$' /tmp/vmig-on.out)" ] || { echo "FAIL: the Job's name is not stable across identical renders"; exit 1; }
	@grep -q 'serviceAccountName: kagent-flux' /tmp/vmig-job.out || { echo "FAIL: the Job does not run as the tenant identity"; exit 1; }
	@if grep -q 'kagent-flux' $(CONNECTIVITY_DIR)/templates/kagent/migrate-job.yaml $(CONNECTIVITY_DIR)/templates/kagent/migrate-rbac.yaml $(CONNECTIVITY_DIR)/templates/kagent/_migrate.tpl; then echo "FAIL: a migration template carries the literal kagent-flux; the identity comes from the helper"; exit 1; fi
	@pin=$$($(MIGRATION_PIN)); grep -q "image: \"gsoci.azurecr.io/giantswarm/agent-manager:$$pin\"" /tmp/vmig-job.out || { echo "FAIL: the image is not agent-manager at the BOM pin $$pin (agentManager.migration.image.tag)"; grep image: /tmp/vmig-job.out; exit 1; }
	@grep -q '^            - migrate$$' /tmp/vmig-job.out || { echo "FAIL: the Job does not run \`migrate\`"; exit 1; }
	@if grep -q -- '--dry-run' /tmp/vmig-job.out; then echo "FAIL: --dry-run renders by default"; exit 1; fi
	@grep -q 'restartPolicy: Never' /tmp/vmig-job.out || { echo "FAIL: restartPolicy"; exit 1; }
	@grep -q 'runAsNonRoot: true' /tmp/vmig-job.out || { echo "FAIL: the Job is not restricted (runAsNonRoot)"; exit 1; }
	@grep -q 'readOnlyRootFilesystem: true' /tmp/vmig-job.out || { echo "FAIL: the Job is not restricted (readOnlyRootFilesystem)"; exit 1; }
	@for env in 'KUBERNETES_IN_CLUSTER' 'KAGENT_NAMESPACE' 'AGENT_CHART_OCI_URL' 'AGENT_CHART_SEMVER' 'AGENT_HARNESS_NAME' 'GITHUB_TOKEN' 'HOME'; do grep -q "name: $$env$$" /tmp/vmig-job.out || { echo "FAIL: env $$env missing"; exit 1; }; done
	@grep -A1 'name: KAGENT_NAMESPACE' /tmp/vmig-job.out | grep -q 'value: kagent' || { echo "FAIL: KAGENT_NAMESPACE is not the kagent namespace"; exit 1; }
	@if grep -q 'AGENT_MANAGER_MIGRATE_GITOPS_NAMESPACES' /tmp/vmig-job.out; then echo "FAIL: AGENT_MANAGER_MIGRATE_GITOPS_NAMESPACES renders without gitopsNamespaces"; exit 1; fi
	@grep -A1 'name: AGENT_HARNESS_NAME' /tmp/vmig-job.out | grep -q 'value: kagent' || { echo "FAIL: AGENT_HARNESS_NAME is not the platform Harness"; exit 1; }
	@grep -A1 'name: AGENT_CHART_OCI_URL' /tmp/vmig-job.out | grep -q 'oci://gsoci.azurecr.io/charts/giantswarm/agent' || { echo "FAIL: AGENT_CHART_OCI_URL does not follow agent-manager.agentChart.ociUrl"; exit 1; }
	@grep -A5 'name: GITHUB_TOKEN' /tmp/vmig-job.out | grep -q 'name: kagent-skills-token' || { echo "FAIL: GITHUB_TOKEN does not read the default token Secret"; exit 1; }
	@grep -A5 'name: GITHUB_TOKEN' /tmp/vmig-job.out | grep -q 'key: token' || { echo "FAIL: GITHUB_TOKEN does not read the key token"; exit 1; }
	@grep -A5 'name: GITHUB_TOKEN' /tmp/vmig-job.out | grep -q 'optional: true' || { echo "FAIL: the token Secret is not optional"; exit 1; }
	@if grep -q 'AGENT_MANAGER_MANAGED_NAMESPACES' /tmp/vmig-job.out; then echo "FAIL: AGENT_MANAGER_MANAGED_NAMESPACES renders without additional namespaces"; exit 1; fi
	@echo "ok: the Job"
	@echo "--> stable across chart releases (#399): the chart packaged at two versions renders the same-named Job with a byte-identical spec.template — the chart version (helm.sh/chart, app.kubernetes.io/version) stays on the Job's own labels, never on the pod's; the pod keeps the selector labels, the component label the network policy selects on, the team label"
	@rm -rf /tmp/vmig-pkg && mkdir -p /tmp/vmig-pkg
	@for v in 4.0.0 4.0.1; do \
		helm package $(CONNECTIVITY_DIR) --version $$v --app-version $$v -d /tmp/vmig-pkg >/tmp/vmig-pkg/package-$$v.log 2>&1 || { cat /tmp/vmig-pkg/package-$$v.log; exit 1; }; \
		helm template t /tmp/vmig-pkg/agent-platform-connectivity-$$v.tgz $(MIGRATION_ON) >/tmp/vmig-pkg/render-$$v.out 2>&1 || { cat /tmp/vmig-pkg/render-$$v.out; exit 1; }; \
		$(PICK) /tmp/vmig-pkg/render-$$v.out Job '$(MIGRATION_JOB)-*' kagent >/tmp/vmig-pkg/job-$$v.out || { echo "FAIL: no Job at chart version $$v"; exit 1; }; \
		sed -n '/^  template:$$/,$$p' /tmp/vmig-pkg/job-$$v.out >/tmp/vmig-pkg/template-$$v.out; \
		sed -n '/^metadata:$$/,/^spec:$$/p' /tmp/vmig-pkg/job-$$v.out >/tmp/vmig-pkg/meta-$$v.out; \
	done
	@[ -s /tmp/vmig-pkg/template-4.0.0.out ] || { echo "FAIL: no spec.template picked from the Job"; exit 1; }
	@[ "$$(grep -E '^  name: $(MIGRATION_JOB)-[0-9a-f]{8}$$' /tmp/vmig-pkg/job-4.0.0.out)" = "$$(grep -E '^  name: $(MIGRATION_JOB)-[0-9a-f]{8}$$' /tmp/vmig-pkg/job-4.0.1.out)" ] || { echo "FAIL: the Job's name changed with the chart version alone (it hashes the pod template, which must not carry the chart version)"; exit 1; }
	@cmp -s /tmp/vmig-pkg/template-4.0.0.out /tmp/vmig-pkg/template-4.0.1.out || { echo "FAIL: the Job's spec.template differs between chart versions 4.0.0 and 4.0.1 — a chart release re-applies the same-named Job with a changed immutable template and the connectivity upgrade stalls (#399)"; diff /tmp/vmig-pkg/template-4.0.0.out /tmp/vmig-pkg/template-4.0.1.out; exit 1; }
	@if grep -qE 'helm.sh/chart|app.kubernetes.io/version' /tmp/vmig-pkg/template-4.0.0.out; then echo "FAIL: the pod template carries a chart-version label"; grep -E 'helm.sh/chart|app.kubernetes.io/version' /tmp/vmig-pkg/template-4.0.0.out; exit 1; fi
	@for l in 'app.kubernetes.io/name: "agent-platform-connectivity"' 'app.kubernetes.io/instance: "t"' 'app.kubernetes.io/component: agent-manager-migrate' 'application.giantswarm.io/team: "'; do grep -qF "$$l" /tmp/vmig-pkg/template-4.0.0.out || { echo "FAIL: the pod template lacks the stable label $$l"; exit 1; }; done
	@grep -q 'helm.sh/chart: "agent-platform-connectivity-4.0.0"' /tmp/vmig-pkg/meta-4.0.0.out || { echo "FAIL: the Job's own labels lost helm.sh/chart"; grep 'helm.sh/chart' /tmp/vmig-pkg/meta-4.0.0.out; exit 1; }
	@grep -q 'app.kubernetes.io/version: "4.0.0"' /tmp/vmig-pkg/meta-4.0.0.out || { echo "FAIL: the Job's own labels lost app.kubernetes.io/version"; exit 1; }
	@if cmp -s /tmp/vmig-pkg/meta-4.0.0.out /tmp/vmig-pkg/meta-4.0.1.out; then echo "FAIL: the Job's own labels do not follow the chart version"; exit 1; fi
	@echo "ok: stable pod template"
	@echo "--> the values: the tag follows agentManager.migration.image.tag, dryRun renders --dry-run, an empty secretName drops the token, additional namespaces reach the Job, a renamed identity follows"
	@helm template t $(CONNECTIVITY_DIR) $(MIGRATION_ON) --set agentManager.migration.image.tag=1.2.3 --set agentManager.migration.dryRun=true --set agentManager.migration.githubToken.secretName= --set 'agent-manager.kagent.additionalNamespaces[0]=team-a' --set 'agent-manager.kagent.additionalNamespaces[1]=team-b' --set kagent.fluxServiceAccountName=tenant-x >/tmp/vmig-vals.out 2>&1 || { cat /tmp/vmig-vals.out; exit 1; }
	@$(PICK) /tmp/vmig-vals.out Job '$(MIGRATION_JOB)-*' kagent >/tmp/vmig-vals-job.out || { echo "FAIL: no Job with the values set"; exit 1; }
	@grep -q 'agent-manager:1.2.3"' /tmp/vmig-vals-job.out || { echo "FAIL: the image tag does not follow the value"; exit 1; }
	@grep -q -- '- --dry-run' /tmp/vmig-vals-job.out || { echo "FAIL: dryRun does not render --dry-run"; exit 1; }
	@if grep -q 'GITHUB_TOKEN' /tmp/vmig-vals-job.out; then echo "FAIL: an empty secretName still renders GITHUB_TOKEN"; exit 1; fi
	@grep -A1 'name: AGENT_MANAGER_MANAGED_NAMESPACES' /tmp/vmig-vals-job.out | grep -q 'value: team-a,team-b' || { echo "FAIL: the additional namespaces do not reach the Job"; exit 1; }
	@grep -q 'serviceAccountName: tenant-x' /tmp/vmig-vals-job.out || { echo "FAIL: the Job's ServiceAccount did not follow the renamed identity"; exit 1; }
	@echo "ok: values"
	@echo "--> RBAC: the CRD pair (verify-identity-migration asserts its shape) and, per GitOps namespace, a Role + RoleBinding with get, list on helmreleases and ocirepositories — none without the list"
	@if $(PICK) /tmp/vmig-on.out Role $(MIGRATION_JOB) >/dev/null 2>&1; then echo "FAIL: a GitOps-namespace Role renders with an empty gitopsNamespaces"; exit 1; fi
	@helm template t $(CONNECTIVITY_DIR) $(MIGRATION_ON) --set 'agentManager.migration.gitopsNamespaces[0]=flux-giantswarm' --set 'agentManager.migration.gitopsNamespaces[1]=flux-team' >/tmp/vmig-gitops.out 2>&1 || { cat /tmp/vmig-gitops.out; exit 1; }
	@for ns in flux-giantswarm flux-team; do \
		$(PICK) /tmp/vmig-gitops.out Role $(MIGRATION_JOB) $$ns >/tmp/vmig-role-$$ns.out || { echo "FAIL: no Role in $$ns"; exit 1; }; \
		grep -q 'resources: \["helmreleases"\]' /tmp/vmig-role-$$ns.out || { echo "FAIL: the Role in $$ns does not read helmreleases"; exit 1; }; \
		grep -q 'resources: \["ocirepositories"\]' /tmp/vmig-role-$$ns.out || { echo "FAIL: the Role in $$ns does not read ocirepositories"; exit 1; }; \
		[ "$$(grep -c 'verbs: \["get", "list"\]' /tmp/vmig-role-$$ns.out)" = "2" ] || { echo "FAIL: the Role in $$ns grants more than get, list"; exit 1; }; \
		$(PICK) /tmp/vmig-gitops.out RoleBinding $(MIGRATION_JOB) $$ns | grep -A3 '^subjects:' | grep -q 'name: kagent-flux' || { echo "FAIL: the RoleBinding in $$ns does not bind the tenant identity"; exit 1; }; \
	done
	@[ "$$(grep -c '^kind: ClusterRoleBinding$$' /tmp/vmig-gitops.out)" = "1" ] || { echo "FAIL: the GitOps namespaces added a cluster-scoped binding"; exit 1; }
	@$(PICK) /tmp/vmig-gitops.out Job '$(MIGRATION_JOB)-*' kagent | grep -A1 'name: AGENT_MANAGER_MIGRATE_GITOPS_NAMESPACES' | grep -q 'value: flux-giantswarm,flux-team' || { echo "FAIL: the GitOps namespaces do not reach the command (AGENT_MANAGER_MIGRATE_GITOPS_NAMESPACES)"; exit 1; }
	@if grep -q 'helm.sh/hook' /tmp/vmig-role-flux-giantswarm.out; then echo "FAIL: the GitOps-namespace Role is a hook resource"; exit 1; fi
	@echo "ok: RBAC"
	@echo "--> network policy: cilium (DNS with the proxy clause, kube-apiserver, api.github.com, the agent chart registry and its blob-storage front by name on 443) and kubernetes (DNS, the API server CIDR, world on 443), selecting the Job's pods; none with networkPolicy off"
	@$(PICK) /tmp/vmig-on.out CiliumNetworkPolicy $(MIGRATION_JOB) kagent >/tmp/vmig-cnp.out || { echo "FAIL: no CiliumNetworkPolicy for the Job"; exit 1; }
	@grep -q 'app.kubernetes.io/component: agent-manager-migrate' /tmp/vmig-cnp.out || { echo "FAIL: the cilium policy does not select the Job's pods"; exit 1; }
	@grep -q 'matchName: api.github.com' /tmp/vmig-cnp.out || { echo "FAIL: the cilium policy has no GitHub API egress"; exit 1; }
	@grep -q 'matchName: gsoci.azurecr.io' /tmp/vmig-cnp.out || { echo "FAIL: the cilium policy has no agent chart registry egress"; exit 1; }
	@grep -qE "matchPattern: ['\"]\*\.blob\.core\.windows\.net['\"]" /tmp/vmig-cnp.out || { echo "FAIL: the cilium policy has no egress to the registry's blob-storage front (*.blob.core.windows.net) — ACR redirects chart blob downloads there and the Job rewrites nothing (#433)"; exit 1; }
	@grep -q -- '- kube-apiserver' /tmp/vmig-cnp.out || { echo "FAIL: the cilium policy has no API server egress"; exit 1; }
	@grep -B2 -A2 'matchPattern: "\*"' /tmp/vmig-cnp.out | grep -q 'dns:' || { echo "FAIL: the cilium policy has no DNS proxy clause for the FQDN selectors"; exit 1; }
	@if grep -q 'ingress:' /tmp/vmig-cnp.out; then echo "FAIL: the Job serves nothing; no ingress rule expected"; exit 1; fi
	@helm template t $(CONNECTIVITY_DIR) $(MIGRATION_ON) --set networkPolicy.flavor=kubernetes >/tmp/vmig-k8s.out 2>&1 || { cat /tmp/vmig-k8s.out; exit 1; }
	@$(PICK) /tmp/vmig-k8s.out NetworkPolicy $(MIGRATION_JOB) kagent >/tmp/vmig-np.out || { echo "FAIL: no NetworkPolicy for the Job in the kubernetes flavor"; exit 1; }
	@grep -q 'app.kubernetes.io/component: agent-manager-migrate' /tmp/vmig-np.out || { echo "FAIL: the kubernetes policy does not select the Job's pods"; exit 1; }
	@grep -q 'policyTypes: \[Egress\]' /tmp/vmig-np.out || { echo "FAIL: the kubernetes policy is not egress-only"; exit 1; }
	@grep -q 'cidr: 0.0.0.0/0' /tmp/vmig-np.out || { echo "FAIL: the kubernetes policy has no world egress (GitHub, the registry)"; exit 1; }
	@grep -q 'port: 6443' /tmp/vmig-np.out || { echo "FAIL: the kubernetes policy has no API server egress"; exit 1; }
	@if $(PICK) /tmp/vmig-k8s.out CiliumNetworkPolicy $(MIGRATION_JOB) >/dev/null 2>&1; then echo "FAIL: a cilium policy renders in the kubernetes flavor"; exit 1; fi
	@helm template t $(CONNECTIVITY_DIR) $(MIGRATION_ON) --set networkPolicy.enabled=false >/tmp/vmig-nonp.out 2>&1 || { cat /tmp/vmig-nonp.out; exit 1; }
	@if grep -qE 'kind: (CiliumNetworkPolicy|NetworkPolicy)' /tmp/vmig-nonp.out; then echo "FAIL: a network policy renders with networkPolicy off"; exit 1; fi
	@$(PICK) /tmp/vmig-nonp.out Job '$(MIGRATION_JOB)-*' kagent >/dev/null || { echo "FAIL: the Job is gone with networkPolicy off"; exit 1; }
	@echo "ok: network policy"
	@echo "--> the Job's egress is agent-manager's chart egress (#433): with agent-manager's oauth off (its IdP rule aside) and every knob set — agentManager.networkPolicy.egress.fqdns/.cidrs, networkPolicy.additionalEgressFQDNs/.additionalEgressCIDRs — the Job's policy and agent-manager's egress name exactly the same FQDN selectors and CIDR blocks (cilium) and ipBlocks (kubernetes, where .cidrs narrows the Job's world egress the way it narrows agent-manager's); neither template names a destination of its own"
	@helm template t $(CONNECTIVITY_DIR) $(MIGRATION_ON) --set agent-manager.oauth.enabled=false --set 'agentManager.networkPolicy.egress.fqdns[0].matchPattern=*.mirror.example.internal' --set 'agentManager.networkPolicy.egress.fqdns[1].matchName=api.github.com' --set 'agentManager.networkPolicy.egress.cidrs[0]=198.51.100.0/24' --set 'networkPolicy.additionalEgressFQDNs[0].matchName=extra.example.internal' --set 'networkPolicy.additionalEgressCIDRs[0]=203.0.113.0/24' >/tmp/vmig-par.out 2>&1 || { cat /tmp/vmig-par.out; exit 1; }
	@$(PICK) /tmp/vmig-par.out CiliumNetworkPolicy $(MIGRATION_JOB) kagent | $(EGRESS_NAMES) >/tmp/vmig-par-job.txt
	@$(PICK) /tmp/vmig-par.out CiliumNetworkPolicy agent-platform-connectivity-agent-manager-egress | $(EGRESS_NAMES) >/tmp/vmig-par-am.txt
	@[ -s /tmp/vmig-par-job.txt ] || { echo "FAIL: no destinations picked from the Job's cilium policy"; exit 1; }
	@cmp -s /tmp/vmig-par-job.txt /tmp/vmig-par-am.txt || { echo "FAIL: the Job's cilium egress and agent-manager's name different destinations (< the Job, > agent-manager)"; diff /tmp/vmig-par-job.txt /tmp/vmig-par-am.txt; exit 1; }
	@for d in 'matchName: gsoci.azurecr.io' 'matchPattern: *.mirror.example.internal' 'matchName: api.github.com' '198.51.100.0/24' 'matchName: extra.example.internal' '203.0.113.0/24'; do grep -qxF -- "$$d" /tmp/vmig-par-job.txt || { echo "FAIL: the Job's cilium egress lacks $$d"; cat /tmp/vmig-par-job.txt; exit 1; }; done
	@if grep -q 'dex.ci.example.com' /tmp/vmig-par-job.txt; then echo "FAIL: the Job's egress names the identity provider; the Job validates no token"; exit 1; fi
	@helm template t $(CONNECTIVITY_DIR) $(MIGRATION_ON) --set networkPolicy.flavor=kubernetes --set agent-manager.oauth.enabled=false --set 'agentManager.networkPolicy.egress.cidrs[0]=198.51.100.0/24' --set 'networkPolicy.additionalEgressCIDRs[0]=203.0.113.0/24' >/tmp/vmig-par-k8s.out 2>&1 || { cat /tmp/vmig-par-k8s.out; exit 1; }
	@$(PICK) /tmp/vmig-par-k8s.out NetworkPolicy $(MIGRATION_JOB) kagent | $(EGRESS_BLOCKS) >/tmp/vmig-par-k8s-job.txt
	@$(PICK) /tmp/vmig-par-k8s.out NetworkPolicy agent-platform-connectivity-agent-manager-egress | $(EGRESS_BLOCKS) >/tmp/vmig-par-k8s-am.txt
	@[ -s /tmp/vmig-par-k8s-job.txt ] || { echo "FAIL: no ipBlocks picked from the Job's kubernetes policy"; exit 1; }
	@cmp -s /tmp/vmig-par-k8s-job.txt /tmp/vmig-par-k8s-am.txt || { echo "FAIL: the Job's kubernetes egress and agent-manager's select different ipBlocks (< the Job, > agent-manager)"; diff /tmp/vmig-par-k8s-job.txt /tmp/vmig-par-k8s-am.txt; exit 1; }
	@for d in 'cidr: 198.51.100.0/24' 'cidr: 203.0.113.0/24'; do grep -qxF -- "$$d" /tmp/vmig-par-k8s-job.txt || { echo "FAIL: the Job's kubernetes egress lacks $$d"; cat /tmp/vmig-par-k8s-job.txt; exit 1; }; done
	@if $(PICK) /tmp/vmig-par-k8s.out NetworkPolicy $(MIGRATION_JOB) kagent | grep -q 'except:'; then echo "FAIL: agentManager.networkPolicy.egress.cidrs did not narrow the Job's kubernetes egress; it still opens every public destination"; exit 1; fi
	@if grep -qE 'api\.github\.com|azurecr|blob\.core' $(CONNECTIVITY_DIR)/templates/kagent/migrate-networkpolicy-cilium.yaml $(CONNECTIVITY_DIR)/templates/kagent/migrate-networkpolicy-kubernetes.yaml $(CONNECTIVITY_DIR)/templates/agent-manager/netpol.yaml; then echo "FAIL: a policy template names a chart destination of its own; the set comes from agent-platform.agentManager.chartSourcesEgress.<flavor> and agentManager.networkPolicy.egress"; exit 1; fi
	@echo "ok: one chart egress for agent-manager and the Job"
	@echo "--> guards: an empty tenant identity fails naming kagent.fluxServiceAccountName; an empty GitOps namespace fails"
	@if helm template t $(CONNECTIVITY_DIR) $(MIGRATION_ON) --set kagent.fluxServiceAccountName= >/tmp/vmig-g1.out 2>&1; then echo "FAIL: the migration rendered without a tenant identity"; exit 1; \
	elif ! grep -q 'kagent.fluxServiceAccountName is empty' /tmp/vmig-g1.out; then echo "FAIL: the identity guard failed for the wrong reason"; cat /tmp/vmig-g1.out; exit 1; else echo "ok: identity guard"; fi
	@helm template t $(CONNECTIVITY_DIR) $(MIGRATION_ON) --set kagent.fluxServiceAccountName= --set agentManager.migration.enabled=false >/dev/null 2>&1 || { echo "FAIL: an empty identity with the migration off must render"; exit 1; }
	@if helm template t $(CONNECTIVITY_DIR) $(MIGRATION_ON) --set 'agentManager.migration.gitopsNamespaces[0]=' >/tmp/vmig-g2.out 2>&1; then echo "FAIL: an empty GitOps namespace rendered"; exit 1; \
	elif ! grep -q 'non-empty namespace name' /tmp/vmig-g2.out; then echo "FAIL: the namespace guard failed for the wrong reason"; cat /tmp/vmig-g2.out; exit 1; else echo "ok: namespace guard"; fi
	@echo "--> the meta chart declares and forwards agentManager.migration (the BOM tag) to the connectivity release"
	@helm template t $(CHART_DIR) -f $(CHART_DIR)/ci/ci-values.yaml --set components.kagent.enabled=true --set components.agent-manager.enabled=true >/tmp/vmig-meta.out 2>&1 || { cat /tmp/vmig-meta.out; exit 1; }
	@$(PICK) /tmp/vmig-meta.out HelmRelease agent-platform-connectivity >/tmp/vmig-meta-conn.out || { echo "FAIL: no connectivity HelmRelease"; exit 1; }
	@pin=$$($(MIGRATION_PIN)); grep -A12 '^      migration:$$' /tmp/vmig-meta-conn.out | grep -q "tag: $$pin" || { echo "FAIL: agentManager.migration.image.tag (the BOM pin $$pin) is not forwarded to the connectivity release"; grep -n -A12 'migration:' /tmp/vmig-meta-conn.out | head -16; exit 1; }
	@pin=$$($(MIGRATION_PIN)); conn=$$($(MIGRATION_PIN_CONNECTIVITY)); [ -n "$$pin" ] && [ "$$pin" = "$$conn" ] || { echo "FAIL: the meta chart's BOM pin ($$pin) and the connectivity chart's default ($$conn) for agentManager.migration.image.tag differ"; exit 1; }
	@grep -q 'disableWaitForJobs: true' /tmp/vmig-meta-conn.out || { echo "FAIL: the connectivity HelmRelease waits for Jobs (components.agent-platform-connectivity.disableWaitForJobs); a migrate failure would fail the release"; exit 1; }
	@[ "$$(grep -c 'disableWaitForJobs: true' /tmp/vmig-meta-conn.out)" = "2" ] || { echo "FAIL: disableWaitForJobs must be rendered on install and upgrade"; exit 1; }
	@$(PICK) /tmp/vmig-meta.out HelmRelease agent-manager >/tmp/vmig-meta-am.out || { echo "FAIL: no agent-manager HelmRelease"; exit 1; }
	@if grep -q 'disableWaitForJobs' /tmp/vmig-meta-am.out; then echo "FAIL: disableWaitForJobs leaked onto another component's HelmRelease"; exit 1; fi
	@if grep -q 'migration' /tmp/vmig-meta-am.out; then echo "FAIL: agentManager.migration leaked into the agent-manager chart's values"; exit 1; fi
	@echo "ok: forwarded"
	@echo "ok: $@"

# The kagent CRDs' storage-version hooks (giantswarm/agent-platform#396): the meta chart's renders.
STORAGE_ON := -f $(CHART_DIR)/ci/ci-values.yaml
STORAGE_BACKUP := t-kagent-storage-version-backup
STORAGE_RESTORE := t-kagent-storage-version-restore
STORAGE_CM := kagent-storage-version-migration

.PHONY: verify-hooks-netpol
verify-hooks-netpol: ## Assert the hook identity's network policy (#413): with networkPolicy on, ONE policy selecting app.kubernetes.io/instance=<release> + component=hooks with egress to the apiserver only — a CiliumNetworkPolicy (kube-apiserver entity) when the flavour resolves to cilium, a NetworkPolicy (networkPolicy.kubernetes.apiServerCIDR, Egress only) otherwise — as a hook object at weight -10 with the identity's delete policy, at all five events with the engine on, at the storage-version hooks' four with the engine off; none with networkPolicy off, none when no hook renders (engine off, kagent off); every hook Job's pod carries the selected labels.
	@echo "====> $@ ($(CHART_DIR))"
	@echo "--> engine on, cilium served: a CiliumNetworkPolicy hook at -10, all five events"
	@helm template t $(CHART_DIR) $(STORAGE_ON) --api-versions cilium.io/v2 >/tmp/vhn-cil.out 2>&1 || { cat /tmp/vhn-cil.out; exit 1; }
	@$(PICK) /tmp/vhn-cil.out CiliumNetworkPolicy t-hooks >/tmp/vhn-cnp.out || { echo "FAIL: no CiliumNetworkPolicy t-hooks"; exit 1; }
	@if $(PICK) /tmp/vhn-cil.out NetworkPolicy t-hooks >/dev/null 2>&1; then echo "FAIL: the kubernetes-flavour policy renders next to the cilium one"; exit 1; fi
	@grep -q 'helm.sh/hook: pre-install,pre-upgrade,post-install,post-upgrade,pre-delete$$' /tmp/vhn-cnp.out || { echo "FAIL: engine on: the policy is not at all five hook events"; grep helm.sh/hook /tmp/vhn-cnp.out; exit 1; }
	@grep -q 'helm.sh/hook-weight: "-10"' /tmp/vhn-cnp.out || { echo "FAIL: the policy is not at weight -10 (with the identity, ahead of every hook Job)"; exit 1; }
	@grep -q 'helm.sh/hook-delete-policy: before-hook-creation,hook-succeeded' /tmp/vhn-cnp.out || { echo "FAIL: the policy does not carry the hook identity's delete policy"; exit 1; }
	@grep -q 'app.kubernetes.io/instance: "t"' /tmp/vhn-cnp.out || { echo "FAIL: the policy does not select the release's instance label"; exit 1; }
	@grep -q 'app.kubernetes.io/component: hooks' /tmp/vhn-cnp.out || { echo "FAIL: the policy does not select component=hooks"; exit 1; }
	@grep -q 'toEntities: \["kube-apiserver"\]' /tmp/vhn-cnp.out || { echo "FAIL: the cilium policy does not admit egress to the kube-apiserver entity"; exit 1; }
	@if grep -q 'ingress:\|toFQDNs\|toCIDR\|toEndpoints\|world' /tmp/vhn-cnp.out; then echo "FAIL: the cilium policy admits more than apiserver egress"; exit 1; fi
	@echo "--> every hook Job's pod carries the selected labels"
	@python3 -c 'import sys,yaml; docs=[d for d in yaml.safe_load_all(open("/tmp/vhn-cil.out")) if d and d.get("kind")=="Job" and "helm.sh/hook" in d["metadata"].get("annotations",{})]; assert docs, "no hook Job rendered"; bad=[d["metadata"]["name"] for d in docs if d["spec"]["template"]["metadata"]["labels"].get("app.kubernetes.io/component")!="hooks" or d["spec"]["template"]["metadata"]["labels"].get("app.kubernetes.io/instance")!="t"]; assert not bad, "hook Jobs the policy does not select: %s" % bad; print("ok: %d hook Jobs selected: %s" % (len(docs), sorted(d["metadata"]["name"] for d in docs)))'
	@echo "--> engine on, no cilium: a NetworkPolicy hook, Egress only, to networkPolicy.kubernetes.apiServerCIDR"
	@helm template t $(CHART_DIR) $(STORAGE_ON) --set networkPolicy.kubernetes.apiServerCIDR=10.9.0.1/32 >/tmp/vhn-k8s.out 2>&1 || { cat /tmp/vhn-k8s.out; exit 1; }
	@$(PICK) /tmp/vhn-k8s.out NetworkPolicy t-hooks >/tmp/vhn-np.out || { echo "FAIL: no NetworkPolicy t-hooks in the kubernetes flavour"; exit 1; }
	@if $(PICK) /tmp/vhn-k8s.out CiliumNetworkPolicy t-hooks >/dev/null 2>&1; then echo "FAIL: the cilium policy renders without cilium.io/v2"; exit 1; fi
	@grep -q 'helm.sh/hook: pre-install,pre-upgrade,post-install,post-upgrade,pre-delete$$' /tmp/vhn-np.out || { echo "FAIL: kubernetes flavour: the policy is not at all five hook events"; exit 1; }
	@grep -q 'policyTypes: \[Egress\]' /tmp/vhn-np.out || { echo "FAIL: the NetworkPolicy is not Egress only"; exit 1; }
	@grep -q 'cidr: "10.9.0.1/32"' /tmp/vhn-np.out || { echo "FAIL: the NetworkPolicy does not use networkPolicy.kubernetes.apiServerCIDR"; grep cidr /tmp/vhn-np.out; exit 1; }
	@echo "--> flavour forced: networkPolicy.flavor=cilium without the API renders the CiliumNetworkPolicy"
	@helm template t $(CHART_DIR) $(STORAGE_ON) --set networkPolicy.flavor=cilium >/tmp/vhn-forced.out 2>&1 || { cat /tmp/vhn-forced.out; exit 1; }
	@$(PICK) /tmp/vhn-forced.out CiliumNetworkPolicy t-hooks >/dev/null || { echo "FAIL: networkPolicy.flavor=cilium does not force the CiliumNetworkPolicy"; exit 1; }
	@echo "--> engine off (the fleet): the policy at the storage-version hooks' four events"
	@helm template t $(CHART_DIR) $(STORAGE_ON) --api-versions cilium.io/v2 --set components.flux.enabled=false >/tmp/vhn-off.out 2>&1 || { cat /tmp/vhn-off.out; exit 1; }
	@$(PICK) /tmp/vhn-off.out CiliumNetworkPolicy t-hooks | grep -q 'helm.sh/hook: pre-install,pre-upgrade,post-install,post-upgrade$$' || { echo "FAIL: engine off: the policy is not at pre-install,pre-upgrade,post-install,post-upgrade (the storage-version hooks' events, no pre-delete)"; $(PICK) /tmp/vhn-off.out CiliumNetworkPolicy t-hooks | grep helm.sh/hook; exit 1; }
	@echo "--> networkPolicy off: none; engine off + kagent off (no hook): none"
	@helm template t $(CHART_DIR) $(STORAGE_ON) --api-versions cilium.io/v2 --set networkPolicy.enabled=false >/tmp/vhn-npoff.out 2>&1 || { cat /tmp/vhn-npoff.out; exit 1; }
	@if $(PICK) /tmp/vhn-npoff.out CiliumNetworkPolicy t-hooks >/dev/null 2>&1 || $(PICK) /tmp/vhn-npoff.out NetworkPolicy t-hooks >/dev/null 2>&1; then echo "FAIL: the hook policy renders with networkPolicy.enabled=false"; exit 1; fi
	@helm template t $(CHART_DIR) $(STORAGE_ON) --api-versions cilium.io/v2 --set components.flux.enabled=false --set components.kagent.enabled=false >/tmp/vhn-none.out 2>&1 || { cat /tmp/vhn-none.out; exit 1; }
	@if grep -q 't-hooks' /tmp/vhn-none.out; then echo "FAIL: engine off, kagent off: the hook policy (or identity) renders with no hook to police"; exit 1; fi
	@echo "ok: $@"

# The connectivity chart's four hook Jobs, each with its switch on: the Substrate
# bootstrap, the derived Postgres Secrets, the managed cache claim and the
# pre-pull cleanup (model serving from its OCI ci values).
HOOKS_ALL := -f $(CONNECTIVITY_DIR)/ci/test-model-serving-oci-values.yaml $(VM) $(SUBSTRATE_ON) --set postgres.enabled=true --set modelServing.cache.enabled=true --set modelServing.prepull.enabled=true

.PHONY: verify-hooks-memory
verify-hooks-memory: ## Assert the connectivity hook Jobs' memory (#513): every hook container requests 10m/32Mi and is limited to agent-platform.hooks.job's default 128Mi, except the Substrate bootstrap's kubectl container at 256Mi (two kubectl processes at once next to its in-memory /work); all four hook Jobs render. Needs PyYAML.
	@echo "====> $@ ($(CONNECTIVITY_DIR))"
	@python3 -c 'import yaml' 2>/dev/null || { echo "FAIL: PyYAML is not installed (apt: python3-yaml, pip: pyyaml)"; exit 1; }
	@helm template t $(CONNECTIVITY_DIR) $(HOOKS_ALL) >/tmp/vhm.out 2>&1 || { cat /tmp/vhm.out; exit 1; }
	@python3 -c 'import sys,yaml; R={"cpu":"10m","memory":"32Mi"}; lim=lambda m: (R, {"memory": m}); want={"t-substrate-bootstrap": {"openssl": lim("128Mi"), "sh": lim("256Mi")}, "t-postgres-databases": {"sh": lim("128Mi")}, "t-model-serving-cache": {"sh": lim("128Mi")}, "t-model-serving-prepull-cleanup": {"sh": lim("128Mi")}}; jobs=[d for d in yaml.safe_load_all(open(sys.argv[1])) if d and d.get("kind")=="Job" and "helm.sh/hook" in d["metadata"].get("annotations",{})]; got={j["metadata"]["name"]: {c["name"]: (c["resources"]["requests"], c["resources"]["limits"]) for c in j["spec"]["template"]["spec"].get("initContainers",[])+j["spec"]["template"]["spec"]["containers"]} for j in jobs}; sys.exit("FAIL: hook containers (requests, limits) are %s, want %s" % (got, want)) if got!=want else print("ok: four hook Jobs, 10m/32Mi requests; t-substrate-bootstrap sh limited to 256Mi, every other hook container to 128Mi")' /tmp/vhm.out
	@echo "ok: $@"

# klaus-gateway on with the two egress policies that select its pod (a2a, OBO) in
# an agentgateway-* mode; the store knobs are the klaus-gateway chart's, forwarded
# by the meta chart, so the connectivity chart reads them at their defaults here.
KG_NETPOL := $(VM) --set components.klaus-gateway.enabled=true --set components.agentgateway.enabled=true --set ingress.mode=agentgateway-muster --set klausGateway.a2a.enabled=true --set klausGateway.obo.enabled=true
KG_OTLP_POLICY := agent-platform-connectivity-klausgateway-otlp-egress
KG_STORE_POLICY := agent-platform-connectivity-klausgateway-store-egress

.PHONY: verify-actor-telemetry-egress
# The actors' and the controller's OTLP egress (giantswarm/agent-platform#456):
# the on-state of verify-kagent-netpol (Substrate, cilium) with the monitoring
# API served, so kagent.otel.*.enabled auto resolves on; the kagent chart's
# default endpoint for both signals.
ATE_OTLP := $(KAGENT_NETPOL)
ATE_EGRESS := substrate-atenet-egress
CTRL_EGRESS := agent-platform-connectivity-kagent-controller-egress
verify-actor-telemetry-egress: ## Assert the OTLP egress of the actors (Substrate's egress gateway) and of the kagent controller (giantswarm/agent-platform#456): one rule per distinct destination of the kagent.otel signals that are on, selecting the pods of the endpoint's namespace (<svc>.<ns>.svc[.cluster.local]) on the endpoint's port — the kube-system OTLP gateway on 4317 by default, once for both signals; a second endpoint a second rule; an endpoint that is not a Service address the cluster entity on its port (443 for https without a port); nothing with both signals off, nothing when auto resolves off (no monitoring API), nothing with Substrate off for the actors; the worker pods keep only the egress gateway; the kubernetes flavour renders no egress policy (its egress is open).
	@echo "====> $@ ($(CONNECTIVITY_DIR))"
	@echo "--> default: the kube-system gateway on 4317, one rule on the egress gateway and one on the controller"
	@helm template t $(CONNECTIVITY_DIR) $(ATE_OTLP) >/tmp/vate-default.out 2>&1 || { cat /tmp/vate-default.out; exit 1; }
	@for pol in $(ATE_EGRESS) $(CTRL_EGRESS); do \
		$(PICK) /tmp/vate-default.out CiliumNetworkPolicy $$pol >/tmp/vate-default-$$pol.out || { echo "FAIL: no CiliumNetworkPolicy $$pol"; exit 1; }; \
		[ "$$(grep -c 'exporters send to (' /tmp/vate-default-$$pol.out)" = "1" ] || { echo "FAIL: $$pol does not carry exactly one OTLP rule (tracing and logging share the endpoint: one rule)"; grep -n 'exporters send to (' /tmp/vate-default-$$pol.out; exit 1; }; \
		grep -A7 'exporters send to (' /tmp/vate-default-$$pol.out | grep -q 'io.kubernetes.pod.namespace: kube-system$$' || { echo "FAIL: $$pol: the OTLP rule does not select the kube-system pods (the DNS rules aside)"; grep -A7 'exporters send to (' /tmp/vate-default-$$pol.out; exit 1; }; \
		grep -A7 'exporters send to (' /tmp/vate-default-$$pol.out | grep -q 'port: "4317"' || { echo "FAIL: $$pol: the OTLP rule is not on 4317"; exit 1; }; \
		if grep -B1 -A4 -- '- cluster$$' /tmp/vate-default-$$pol.out | grep -q 'port: "4317"'; then echo "FAIL: $$pol still opens 4317 on the cluster entity next to the namespace rule"; exit 1; fi; \
		echo "ok: $$pol -> kube-system:4317"; \
	done
	@grep -q "agent-platform#456" /tmp/vate-default-$(ATE_EGRESS).out || { echo "FAIL: the egress gateway's rule carries no explanation (the comment gated with the rule)"; exit 1; }
	@$(PICK) /tmp/vate-default.out CiliumNetworkPolicy substrate-workers >/tmp/vate-default-workers.out || { echo "FAIL: no substrate-workers policy"; exit 1; }
	@if grep -q '4317\|OTLP' /tmp/vate-default-workers.out; then echo "FAIL: the worker pods gained an OTLP rule; the actors' export leaves through the egress gateway"; exit 1; fi
	@echo "--> a second endpoint (logs on an http/protobuf collector elsewhere) is a second rule; tracing on a plain host is the cluster entity on 443"
	@helm template t $(CONNECTIVITY_DIR) $(ATE_OTLP) --set kagent.otel.logging.exporter.otlp.endpoint=http://collector.observability.svc.cluster.local:4318 --set kagent.otel.tracing.exporter.otlp.endpoint=https://otlp.example.com >/tmp/vate-split.out 2>&1 || { cat /tmp/vate-split.out; exit 1; }
	@$(PICK) /tmp/vate-split.out CiliumNetworkPolicy $(ATE_EGRESS) >/tmp/vate-split-egress.out
	@[ "$$(grep -c 'exporters send to (' /tmp/vate-split-egress.out)" = "2" ] || { echo "FAIL: two distinct endpoints are not two rules"; grep -n 'exporters send to (' /tmp/vate-split-egress.out; exit 1; }
	@grep -A5 'io.kubernetes.pod.namespace: observability$$' /tmp/vate-split-egress.out | grep -q 'port: "4318"' || { echo "FAIL: the logging endpoint's namespace and port (observability, 4318) are not a rule"; cat /tmp/vate-split-egress.out; exit 1; }
	@grep -A7 'otlp.example.com' /tmp/vate-split-egress.out | grep -q -- '- cluster$$' && grep -A7 'otlp.example.com' /tmp/vate-split-egress.out | grep -q 'port: "443"' || { echo "FAIL: an https endpoint on a plain host is not the cluster entity on 443"; grep -A7 'otlp.example.com' /tmp/vate-split-egress.out; exit 1; }
	@if grep -q 'port: "4317"' /tmp/vate-split-egress.out; then echo "FAIL: the default gateway rule remains after both endpoints moved"; exit 1; fi
	@echo "--> a port left out follows the protocol: grpc 4317, http/protobuf 4318"
	@helm template t $(CONNECTIVITY_DIR) $(ATE_OTLP) --set kagent.otel.tracing.exporter.otlp.endpoint=http://otlp-gateway.kube-system.svc --set kagent.otel.logging.exporter.otlp.endpoint=http://otlp-gateway.kube-system.svc 2>/dev/null | $(PICK) /dev/stdin CiliumNetworkPolicy $(ATE_EGRESS) | grep -A5 'kube-system$$' | grep -q 'port: "4317"' || { echo "FAIL: no port + grpc is not 4317"; exit 1; }
	@helm template t $(CONNECTIVITY_DIR) $(ATE_OTLP) --set kagent.otel.tracing.exporter.otlp.endpoint=http://otlp-gateway.kube-system.svc --set kagent.otel.logging.exporter.otlp.endpoint=http://otlp-gateway.kube-system.svc --set kagent.otel.tracing.exporter.otlp.protocol=http/protobuf 2>/dev/null | $(PICK) /dev/stdin CiliumNetworkPolicy $(ATE_EGRESS) | grep -A5 'kube-system$$' | grep -q 'port: "4318"' || { echo "FAIL: no port + http/protobuf is not 4318"; exit 1; }
	@echo "ok: ports"
	@echo "--> both signals off: no OTLP rule on either policy, no comment; the rest of the egress gateway unchanged"
	@helm template t $(CONNECTIVITY_DIR) $(ATE_OTLP) --set kagent.otel.tracing.enabled=false --set kagent.otel.logging.enabled=false >/tmp/vate-off.out 2>&1 || { cat /tmp/vate-off.out; exit 1; }
	@for pol in $(ATE_EGRESS) $(CTRL_EGRESS); do \
		$(PICK) /tmp/vate-off.out CiliumNetworkPolicy $$pol >/tmp/vate-off-$$pol.out; \
		if grep -q 'OTLP gateway\|port: "4317"' /tmp/vate-off-$$pol.out; then echo "FAIL: $$pol keeps an OTLP rule with both signals off"; exit 1; fi; \
	done
	@grep -q 'port: "8083"' /tmp/vate-off-$(ATE_EGRESS).out && grep -q 'port: "10443"' /tmp/vate-off-$(ATE_EGRESS).out || { echo "FAIL: the egress gateway lost its other rules with the signals off"; exit 1; }
	@echo "ok: off"
	@echo "--> auto with no monitoring API served resolves off: no rule"
	@helm template t $(CONNECTIVITY_DIR) --set ingress.parentRefs[0].name=x --set kagent.harness.snapshotLocation=s3://ci-agent-snapshots/agents --api-versions cilium.io/v2 --set components.kagent.enabled=true $(SUBSTRATE_ON) --set muster.enabled=true --set networkPolicy.flavor=cilium --set kagent.namespaceOverride=kagent >/tmp/vate-auto.out 2>&1 || { cat /tmp/vate-auto.out; exit 1; }
	@if $(PICK) /tmp/vate-auto.out CiliumNetworkPolicy $(ATE_EGRESS) | grep -q 'OTLP gateway'; then echo "FAIL: auto rendered the rule without the observability platform"; exit 1; else echo "ok: auto off"; fi
	@echo "--> one signal on is enough"
	@helm template t $(CONNECTIVITY_DIR) $(ATE_OTLP) --set kagent.otel.tracing.enabled=false 2>/dev/null | $(PICK) /dev/stdin CiliumNetworkPolicy $(ATE_EGRESS) | grep -A7 'exporters send to (' | grep -q 'port: "4317"' || { echo "FAIL: logging alone renders no rule"; exit 1; }
	@echo "--> kubernetes flavour: no Substrate egress policy (its egress is open), no cilium object"
	@helm template t $(CONNECTIVITY_DIR) $(ATE_OTLP) --set networkPolicy.flavor=kubernetes >/tmp/vate-k8s.out 2>&1 || { cat /tmp/vate-k8s.out; exit 1; }
	@if grep -q 'OTLP gateway\|cilium.io' /tmp/vate-k8s.out; then echo "FAIL: the kubernetes flavour renders an OTLP rule or a cilium object"; exit 1; fi
	@echo "--> the meta chart's Harness env (the actors' side of the same path) is tests/verify-kagent-harness.py"
	@echo "ok: $@"

.PHONY: verify-klausgateway-netpol
verify-klausgateway-netpol: ## Assert klaus-gateway's egress to its stores (#443) and to the API server for its team-review endpoint (#511): the -klausgateway-store-egress policy renders exactly while a store reaches beyond the pod — the platform's Valkey pods on their Service port with klausGateway.routing.store valkey (and the valkey component on; off = an out-of-band Valkey, no rule) and the kube-apiserver with klausGateway.obo.store secret and OBO on — or while klausGateway.reviews.enabled (a TokenReview per POST; the kube-apiserver whatever the link store, the rule rendered once next to the Secret store's) — as DNS + the rules in the cilium flavour and DNS + the pod selector / networkPolicy.kubernetes.apiServerCIDR (Egress only) in the kubernetes one, each rule only with its store, selecting the pod by klausGateway.fullnameOverride; the default shape (memory routing, the bolt link store, reviews off) renders none of it next to the unchanged a2a and OBO policies; none with the Secret store but OBO off, with networkPolicy off, or with the component off.
	@echo "====> $@ ($(CONNECTIVITY_DIR))"
	@echo "--> default stores (memory routing, bolt links): the a2a and OBO policies, no store policy"
	@helm template t $(CONNECTIVITY_DIR) $(KG_NETPOL) >/tmp/vkg-default.out 2>&1 || { cat /tmp/vkg-default.out; exit 1; }
	@$(PICK) /tmp/vkg-default.out CiliumNetworkPolicy agent-platform-connectivity-klausgateway-a2a-egress >/dev/null || { echo "FAIL: the a2a egress policy is gone"; exit 1; }
	@$(PICK) /tmp/vkg-default.out CiliumNetworkPolicy agent-platform-connectivity-klausgateway-obo-egress >/dev/null || { echo "FAIL: the OBO egress policy is gone"; exit 1; }
	@if grep -q '$(KG_STORE_POLICY)' /tmp/vkg-default.out; then echo "FAIL: the store egress policy renders on the default shape, which never reaches the API"; exit 1; fi
	@for pol in a2a obo; do \
		$(PICK) /tmp/vkg-default.out CiliumNetworkPolicy agent-platform-connectivity-klausgateway-$$pol-egress >/tmp/vkg-default-$$pol.out; \
		if grep -q 'kube-apiserver' /tmp/vkg-default-$$pol.out; then echo "FAIL: the $$pol egress policy names the kube-apiserver entity (the store policy owns that rule)"; exit 1; fi; \
	done
	@echo "--> cilium, the Secret link store: DNS + the kube-apiserver entity, nothing else, one policy"
	@helm template t $(CONNECTIVITY_DIR) $(KG_NETPOL) --set klausGateway.obo.store=secret >/tmp/vkg-secret.out 2>&1 || { cat /tmp/vkg-secret.out; exit 1; }
	@$(PICK) /tmp/vkg-secret.out CiliumNetworkPolicy $(KG_STORE_POLICY) >/tmp/vkg-secret-cnp.out || { echo "FAIL: no CiliumNetworkPolicy $(KG_STORE_POLICY) with klausGateway.obo.store=secret"; exit 1; }
	@if $(PICK) /tmp/vkg-secret.out NetworkPolicy $(KG_STORE_POLICY) >/dev/null 2>&1; then echo "FAIL: the kubernetes-flavour policy renders next to the cilium one"; exit 1; fi
	@[ "$$(grep -c '^  name: $(KG_STORE_POLICY)$$' /tmp/vkg-secret.out)" = "1" ] || { echo "FAIL: expected exactly one store egress policy"; grep -c '^  name: $(KG_STORE_POLICY)$$' /tmp/vkg-secret.out; exit 1; }
	@grep -q 'app.kubernetes.io/name: "klaus-gateway"' /tmp/vkg-secret-cnp.out || { echo "FAIL: the policy does not select the klaus-gateway pod"; exit 1; }
	@grep -q 'toEntities: \["kube-apiserver"\]' /tmp/vkg-secret-cnp.out || { echo "FAIL: the cilium policy does not admit egress to the kube-apiserver entity"; exit 1; }
	@grep -q 'k8s-app: kube-dns' /tmp/vkg-secret-cnp.out || { echo "FAIL: the cilium policy carries no DNS rule (the siblings do; alone it would leave the pod without names)"; exit 1; }
	@if grep -q 'ingress:\|toFQDNs\|toCIDR\|world\|toServices' /tmp/vkg-secret-cnp.out; then echo "FAIL: the cilium policy admits more than DNS and the apiserver"; exit 1; fi
	@echo "--> the Secret store with OBO off: none (the gateway builds no linker, reads no Secret)"
	@helm template t $(CONNECTIVITY_DIR) $(KG_NETPOL) --set klausGateway.obo.store=secret --set klausGateway.obo.enabled=false >/tmp/vkg-obooff.out 2>&1 || { cat /tmp/vkg-obooff.out; exit 1; }
	@if grep -q '$(KG_STORE_POLICY)' /tmp/vkg-obooff.out; then echo "FAIL: the store egress policy renders for obo.store=secret while OBO is off"; exit 1; fi
	@echo "--> the bolt routing store renders no policy"
	@helm template t $(CONNECTIVITY_DIR) $(KG_NETPOL) --set klausGateway.routing.store=bolt >/tmp/vkg-bolt.out 2>&1 || { cat /tmp/vkg-bolt.out; exit 1; }
	@if grep -q '$(KG_STORE_POLICY)' /tmp/vkg-bolt.out; then echo "FAIL: the store egress policy renders for the bolt routing store, which is a file"; exit 1; fi
	@echo "--> cilium, the Valkey routing store: the platform's Valkey pods on 6379 in the release namespace, no kube-apiserver"
	@helm template t $(CONNECTIVITY_DIR) $(KG_NETPOL) --set klausGateway.routing.store=valkey >/tmp/vkg-valkey.out 2>&1 || { cat /tmp/vkg-valkey.out; exit 1; }
	@$(PICK) /tmp/vkg-valkey.out CiliumNetworkPolicy $(KG_STORE_POLICY) >/tmp/vkg-valkey-cnp.out || { echo "FAIL: no CiliumNetworkPolicy $(KG_STORE_POLICY) with klausGateway.routing.store=valkey"; exit 1; }
	@grep -q 'app.kubernetes.io/name: valkey' /tmp/vkg-valkey-cnp.out && grep -q 'app.kubernetes.io/instance: valkey' /tmp/vkg-valkey-cnp.out || { echo "FAIL: the Valkey rule does not select the muster-valkey pods (the valkey subchart's selector labels)"; cat /tmp/vkg-valkey-cnp.out; exit 1; }
	@grep -q 'io.kubernetes.pod.namespace: default' /tmp/vkg-valkey-cnp.out || { echo "FAIL: the Valkey rule is not pinned to the release namespace"; exit 1; }
	@grep -q 'port: "6379"' /tmp/vkg-valkey-cnp.out || { echo "FAIL: the Valkey rule is not on 6379"; exit 1; }
	@if grep -q 'toEntities: \["kube-apiserver"\]' /tmp/vkg-valkey-cnp.out; then echo "FAIL: the Valkey store alone must not open the kube-apiserver"; exit 1; fi
	@echo "--> the Service port follows valkey.valkey.service.port; both stores on = both rules"
	@helm template t $(CONNECTIVITY_DIR) $(KG_NETPOL) --set klausGateway.routing.store=valkey --set klausGateway.obo.store=secret --set valkey.valkey.service.port=6380 >/tmp/vkg-both.out 2>&1 || { cat /tmp/vkg-both.out; exit 1; }
	@$(PICK) /tmp/vkg-both.out CiliumNetworkPolicy $(KG_STORE_POLICY) >/tmp/vkg-both-cnp.out || { echo "FAIL: no store policy with both stores on"; exit 1; }
	@grep -q 'port: "6380"' /tmp/vkg-both-cnp.out || { echo "FAIL: the Valkey rule does not follow valkey.valkey.service.port"; grep port: /tmp/vkg-both-cnp.out; exit 1; }
	@grep -q 'toEntities: \["kube-apiserver"\]' /tmp/vkg-both-cnp.out || { echo "FAIL: the Secret store's kube-apiserver rule is missing next to the Valkey rule"; exit 1; }
	@[ "$$(grep -c '^  name: $(KG_STORE_POLICY)$$' /tmp/vkg-both.out)" = "1" ] || { echo "FAIL: expected exactly one store egress policy with both stores on"; exit 1; }
	@echo "--> the Valkey store with the valkey component off (an out-of-band Valkey): no rule, no policy"
	@helm template t $(CONNECTIVITY_DIR) $(KG_NETPOL) --set klausGateway.routing.store=valkey --set components.valkey.enabled=false >/tmp/vkg-valkey-off.out 2>&1 || { cat /tmp/vkg-valkey-off.out; exit 1; }
	@if grep -q '$(KG_STORE_POLICY)' /tmp/vkg-valkey-off.out; then echo "FAIL: the store egress policy renders for an out-of-band Valkey (the component off) — nothing in this namespace to select"; exit 1; fi
	@echo "--> kubernetes flavour, the Valkey store: a pod selector on the valkey labels, 6379, no CIDR"
	@helm template t $(CONNECTIVITY_DIR) $(KG_NETPOL) --set klausGateway.routing.store=valkey --set networkPolicy.flavor=kubernetes --set networkPolicy.kubernetes.apiServerCIDR=10.9.0.1/32 >/tmp/vkg-k8s-valkey.out 2>&1 || { cat /tmp/vkg-k8s-valkey.out; exit 1; }
	@$(PICK) /tmp/vkg-k8s-valkey.out NetworkPolicy $(KG_STORE_POLICY) >/tmp/vkg-k8s-valkey-np.out || { echo "FAIL: no NetworkPolicy $(KG_STORE_POLICY) for the Valkey store in the kubernetes flavour"; exit 1; }
	@grep -q 'app.kubernetes.io/instance: valkey' /tmp/vkg-k8s-valkey-np.out && grep -q 'port: 6379' /tmp/vkg-k8s-valkey-np.out || { echo "FAIL: the kubernetes-flavour Valkey rule does not select the valkey pods on 6379"; cat /tmp/vkg-k8s-valkey-np.out; exit 1; }
	@if grep -q 'cidr:' /tmp/vkg-k8s-valkey-np.out; then echo "FAIL: the Valkey store alone must not open the apiserver CIDR"; exit 1; fi
	@echo "--> kubernetes flavour: a NetworkPolicy, Egress only, networkPolicy.kubernetes.apiServerCIDR + DNS"
	@helm template t $(CONNECTIVITY_DIR) $(KG_NETPOL) --set klausGateway.obo.store=secret --set networkPolicy.flavor=kubernetes --set networkPolicy.kubernetes.apiServerCIDR=10.9.0.1/32 >/tmp/vkg-k8s.out 2>&1 || { cat /tmp/vkg-k8s.out; exit 1; }
	@$(PICK) /tmp/vkg-k8s.out NetworkPolicy $(KG_STORE_POLICY) >/tmp/vkg-k8s-np.out || { echo "FAIL: no NetworkPolicy $(KG_STORE_POLICY) in the kubernetes flavour"; exit 1; }
	@if $(PICK) /tmp/vkg-k8s.out CiliumNetworkPolicy $(KG_STORE_POLICY) >/dev/null 2>&1; then echo "FAIL: the cilium policy renders in the kubernetes flavour"; exit 1; fi
	@grep -q 'policyTypes: \[Egress\]' /tmp/vkg-k8s-np.out || { echo "FAIL: the NetworkPolicy is not Egress only"; exit 1; }
	@grep -q 'cidr: "10.9.0.1/32"' /tmp/vkg-k8s-np.out || { echo "FAIL: the NetworkPolicy does not use networkPolicy.kubernetes.apiServerCIDR"; grep cidr /tmp/vkg-k8s-np.out; exit 1; }
	@grep -q 'values: \[kube-dns, coredns, k8s-dns-node-cache\]' /tmp/vkg-k8s-np.out || { echo "FAIL: the NetworkPolicy carries no DNS rule"; exit 1; }
	@echo "--> cilium, the team-review endpoint on the bolt link store (#511): DNS + the kube-apiserver entity, one policy, no Valkey rule"
	@helm template t $(CONNECTIVITY_DIR) $(KG_NETPOL) --set klausGateway.reviews.enabled=true --set klausGateway.obo.storePath=/var/lib/klaus-gateway/obo/links.bolt >/tmp/vkg-reviews.out 2>&1 || { cat /tmp/vkg-reviews.out; exit 1; }
	@$(PICK) /tmp/vkg-reviews.out CiliumNetworkPolicy $(KG_STORE_POLICY) >/tmp/vkg-reviews-cnp.out || { echo "FAIL: no CiliumNetworkPolicy $(KG_STORE_POLICY) with klausGateway.reviews.enabled=true on the bolt store (every TokenReview would time out, the endpoint answer 503)"; exit 1; }
	@[ "$$(grep -c '^  name: $(KG_STORE_POLICY)$$' /tmp/vkg-reviews.out)" = "1" ] || { echo "FAIL: expected exactly one store egress policy with reviews on"; exit 1; }
	@grep -q 'app.kubernetes.io/name: "klaus-gateway"' /tmp/vkg-reviews-cnp.out || { echo "FAIL: the reviews policy does not select the klaus-gateway pod"; exit 1; }
	@grep -q 'toEntities: \["kube-apiserver"\]' /tmp/vkg-reviews-cnp.out || { echo "FAIL: the cilium policy does not admit egress to the kube-apiserver entity with reviews on"; cat /tmp/vkg-reviews-cnp.out; exit 1; }
	@grep -q 'k8s-app: kube-dns' /tmp/vkg-reviews-cnp.out || { echo "FAIL: the reviews policy carries no DNS rule"; exit 1; }
	@if grep -q 'ingress:\|toFQDNs\|toCIDR\|world\|toServices\|app.kubernetes.io/name: valkey' /tmp/vkg-reviews-cnp.out; then echo "FAIL: the reviews policy admits more than DNS and the apiserver"; cat /tmp/vkg-reviews-cnp.out; exit 1; fi
	@echo "--> reviews on next to the Secret link store: the kube-apiserver rule renders once"
	@helm template t $(CONNECTIVITY_DIR) $(KG_NETPOL) --set klausGateway.reviews.enabled=true --set klausGateway.obo.store=secret >/tmp/vkg-reviews-secret.out 2>&1 || { cat /tmp/vkg-reviews-secret.out; exit 1; }
	@$(PICK) /tmp/vkg-reviews-secret.out CiliumNetworkPolicy $(KG_STORE_POLICY) >/tmp/vkg-reviews-secret-cnp.out || { echo "FAIL: no store policy with reviews on and the Secret store"; exit 1; }
	@[ "$$(grep -c 'toEntities: \["kube-apiserver"\]' /tmp/vkg-reviews-secret-cnp.out)" = "1" ] || { echo "FAIL: expected exactly one kube-apiserver rule with reviews on and the Secret store"; grep -c kube-apiserver /tmp/vkg-reviews-secret-cnp.out; exit 1; }
	@echo "--> reviews off (the chart default, the key declared): the bolt store renders no policy"
	@helm template t $(CONNECTIVITY_DIR) $(KG_NETPOL) --set klausGateway.reviews.enabled=false --set klausGateway.obo.storePath=/var/lib/klaus-gateway/obo/links.bolt >/tmp/vkg-reviews-off.out 2>&1 || { cat /tmp/vkg-reviews-off.out; exit 1; }
	@if grep -q '$(KG_STORE_POLICY)' /tmp/vkg-reviews-off.out; then echo "FAIL: the store egress policy renders with reviews off on the bolt store"; exit 1; fi
	@echo "--> kubernetes flavour, reviews on: a NetworkPolicy, Egress only, networkPolicy.kubernetes.apiServerCIDR + DNS"
	@helm template t $(CONNECTIVITY_DIR) $(KG_NETPOL) --set klausGateway.reviews.enabled=true --set networkPolicy.flavor=kubernetes --set networkPolicy.kubernetes.apiServerCIDR=10.9.0.1/32 >/tmp/vkg-k8s-reviews.out 2>&1 || { cat /tmp/vkg-k8s-reviews.out; exit 1; }
	@$(PICK) /tmp/vkg-k8s-reviews.out NetworkPolicy $(KG_STORE_POLICY) >/tmp/vkg-k8s-reviews-np.out || { echo "FAIL: no NetworkPolicy $(KG_STORE_POLICY) with reviews on in the kubernetes flavour"; exit 1; }
	@grep -q 'policyTypes: \[Egress\]' /tmp/vkg-k8s-reviews-np.out || { echo "FAIL: the reviews NetworkPolicy is not Egress only"; exit 1; }
	@grep -q 'cidr: "10.9.0.1/32"' /tmp/vkg-k8s-reviews-np.out || { echo "FAIL: the reviews NetworkPolicy does not use networkPolicy.kubernetes.apiServerCIDR"; grep cidr /tmp/vkg-k8s-reviews-np.out; exit 1; }
	@grep -q 'values: \[kube-dns, coredns, k8s-dns-node-cache\]' /tmp/vkg-k8s-reviews-np.out || { echo "FAIL: the reviews NetworkPolicy carries no DNS rule"; exit 1; }
	@echo "--> the selector follows klausGateway.fullnameOverride"
	@helm template t $(CONNECTIVITY_DIR) $(KG_NETPOL) --set klausGateway.obo.store=secret --set klausGateway.fullnameOverride=kg-renamed >/tmp/vkg-renamed.out 2>&1 || { cat /tmp/vkg-renamed.out; exit 1; }
	@$(PICK) /tmp/vkg-renamed.out CiliumNetworkPolicy $(KG_STORE_POLICY) | grep -q 'app.kubernetes.io/name: "kg-renamed"' || { echo "FAIL: the store egress policy does not select the renamed pod"; exit 1; }
	@if grep -q 'app.kubernetes.io/name: "klaus-gateway"' /tmp/vkg-renamed.out; then echo "FAIL: a klaus-gateway policy still selects the default name after fullnameOverride"; grep -n 'app.kubernetes.io/name: "klaus-gateway"' /tmp/vkg-renamed.out; exit 1; fi
	@echo "--> networkPolicy off: none; component off: none"
	@helm template t $(CONNECTIVITY_DIR) $(KG_NETPOL) --set klausGateway.obo.store=secret --set networkPolicy.enabled=false >/tmp/vkg-npoff.out 2>&1 || { cat /tmp/vkg-npoff.out; exit 1; }
	@if grep -q 'klausgateway-.*-egress' /tmp/vkg-npoff.out; then echo "FAIL: a klaus-gateway egress policy renders with networkPolicy.enabled=false"; exit 1; fi
	@helm template t $(CONNECTIVITY_DIR) $(KG_NETPOL) --set klausGateway.obo.store=secret --set components.klaus-gateway.enabled=false >/tmp/vkg-off.out 2>&1 || { cat /tmp/vkg-off.out; exit 1; }
	@if grep -q 'klausgateway' /tmp/vkg-off.out; then echo "FAIL: klaus-gateway wiring renders with the component off"; grep -n klausgateway /tmp/vkg-off.out | head; exit 1; fi
	@echo "ok: $@"

.PHONY: verify-klausgateway-otlp
verify-klausgateway-otlp: ## Assert klaus-gateway's trace export (giantswarm/klaus-gateway#263) and monitor: the meta chart's klausGateway.serviceMonitor.enabled auto resolves to a boolean the chart takes (true with monitors served, false without) and the ServiceMonitor carries the tenant label; the meta chart defaults klausGateway.observability to the platform's OTLP gateway with the tenant header and forwards endpoint + headers (never the enabled knob) to the klaus-gateway release; enabled auto follows the monitors (off = an empty endpoint, no headers), an explicit true keeps the endpoint without monitors, false empties it with them; the connectivity chart renders -klausgateway-otlp-egress exactly while the endpoint is set — DNS + the endpoint's namespace on its port in the cilium flavour (the cluster entity for a host that is not an in-cluster Service), a namespaceSelector on the port in the kubernetes one — selecting the pod by klausGateway.fullnameOverride; none with networkPolicy off or the component off; the kagent controller's OTLP rule is unchanged by the shared helper.
	@echo "====> $@ ($(CHART_DIR) + $(CONNECTIVITY_DIR))"
	@echo "--> meta chart, defaults: the klaus-gateway release carries the OTLP gateway and the tenant header, not the knob"
	@helm template t $(CHART_DIR) $(VM) --set components.klaus-gateway.enabled=true --set components.agentgateway.enabled=true >/tmp/vko-default.out 2>&1 || { cat /tmp/vko-default.out; exit 1; }
	@$(PICK) /tmp/vko-default.out HelmRelease klaus-gateway >/tmp/vko-default-hr.out || { echo "FAIL: no klaus-gateway HelmRelease"; exit 1; }
	@grep -q '^      otlpEndpoint: http://otlp-gateway.kube-system.svc:4317$$' /tmp/vko-default-hr.out || { echo "FAIL: the klaus-gateway release does not carry the OTLP gateway endpoint by default"; grep -n otlp /tmp/vko-default-hr.out; exit 1; }
	@grep -q '^        X-Scope-OrgID: giantswarm$$' /tmp/vko-default-hr.out || { echo "FAIL: the klaus-gateway release does not carry the tenant header"; grep -n -A3 otlpHeaders /tmp/vko-default-hr.out; exit 1; }
	@if awk '/^    observability:$$/,/^    [a-z]/' /tmp/vko-default-hr.out | grep -q '^      enabled:'; then echo "FAIL: klausGateway.observability.enabled reached the klaus-gateway release (its schema refuses it)"; exit 1; fi
	@grep -A3 '^    serviceMonitor:$$' /tmp/vko-default-hr.out | grep -q '^      enabled: true$$' || { echo "FAIL: klausGateway.serviceMonitor.enabled did not resolve to true with monitors served (the chart takes a boolean; a literal auto fails its schema)"; grep -n -A4 '^    serviceMonitor:' /tmp/vko-default-hr.out; exit 1; }
	@grep -A3 '^    serviceMonitor:$$' /tmp/vko-default-hr.out | grep -q '^        observability.giantswarm.io/tenant: giantswarm$$' || { echo "FAIL: the klaus-gateway ServiceMonitor carries no tenant label; Mimir routes its scrape to no tenant"; grep -n -A4 '^    serviceMonitor:' /tmp/vko-default-hr.out; exit 1; }
	@echo "ok: endpoint + header forwarded, the knob held back"
	@echo "--> monitors off (auto resolves off): an empty endpoint and no headers"
	@helm template t $(CHART_DIR) $(VM) --set components.klaus-gateway.enabled=true --set components.agentgateway.enabled=true --set global.observability.metrics.serviceMonitor.enabled=false >/tmp/vko-off.out 2>&1 || { cat /tmp/vko-off.out; exit 1; }
	@$(PICK) /tmp/vko-off.out HelmRelease klaus-gateway >/tmp/vko-off-hr.out
	@grep -q '^      otlpEndpoint: ""$$' /tmp/vko-off-hr.out || { echo "FAIL: with the monitors off the klaus-gateway release still names an OTLP endpoint"; grep -n otlp /tmp/vko-off-hr.out; exit 1; }
	@if grep -q 'X-Scope-OrgID' /tmp/vko-off-hr.out; then echo "FAIL: with the monitors off the klaus-gateway release still carries the tenant header"; exit 1; fi
	@grep -A3 '^    serviceMonitor:$$' /tmp/vko-off-hr.out | grep -q '^      enabled: false$$' || { echo "FAIL: with the monitors off klausGateway.serviceMonitor.enabled did not resolve to false; the release would render a ServiceMonitor against a missing CRD"; exit 1; }
	@echo "ok: monitors off -> no export"
	@echo "--> explicit true keeps the endpoint without monitors; explicit false empties it with them"
	@helm template t $(CHART_DIR) $(VM) --set components.klaus-gateway.enabled=true --set components.agentgateway.enabled=true --set global.observability.metrics.serviceMonitor.enabled=false --set klausGateway.observability.enabled=true >/tmp/vko-force.out 2>&1 || { cat /tmp/vko-force.out; exit 1; }
	@$(PICK) /tmp/vko-force.out HelmRelease klaus-gateway | grep -q '^      otlpEndpoint: http://otlp-gateway.kube-system.svc:4317$$' || { echo "FAIL: klausGateway.observability.enabled=true does not keep the endpoint without monitors"; exit 1; }
	@helm template t $(CHART_DIR) $(VM) --set components.klaus-gateway.enabled=true --set components.agentgateway.enabled=true --set klausGateway.observability.enabled=false >/tmp/vko-false.out 2>&1 || { cat /tmp/vko-false.out; exit 1; }
	@$(PICK) /tmp/vko-false.out HelmRelease klaus-gateway | grep -q '^      otlpEndpoint: ""$$' || { echo "FAIL: klausGateway.observability.enabled=false does not empty the endpoint"; exit 1; }
	@echo "ok: the explicit values win"
	@echo "--> connectivity, cilium: the OTLP egress policy renders with an endpoint, selects the pod, admits DNS + the endpoint's namespace on its port"
	@helm template t $(CONNECTIVITY_DIR) $(KG_NETPOL) --set klausGateway.observability.otlpEndpoint=http://otlp-gateway.kube-system.svc:4317 >/tmp/vko-cnp.out 2>&1 || { cat /tmp/vko-cnp.out; exit 1; }
	@$(PICK) /tmp/vko-cnp.out CiliumNetworkPolicy $(KG_OTLP_POLICY) >/tmp/vko-cnp-pol.out || { echo "FAIL: no CiliumNetworkPolicy $(KG_OTLP_POLICY) with an OTLP endpoint"; exit 1; }
	@grep -q 'app.kubernetes.io/name: "klaus-gateway"' /tmp/vko-cnp-pol.out || { echo "FAIL: the OTLP policy does not select the klaus-gateway pod"; exit 1; }
	@grep -q 'io.kubernetes.pod.namespace: kube-system' /tmp/vko-cnp-pol.out || { echo "FAIL: the OTLP policy does not select the endpoint's namespace"; cat /tmp/vko-cnp-pol.out; exit 1; }
	@grep -q 'port: "4317"' /tmp/vko-cnp-pol.out || { echo "FAIL: the OTLP policy is not on 4317"; exit 1; }
	@grep -q 'k8s-app: kube-dns' /tmp/vko-cnp-pol.out || { echo "FAIL: the OTLP policy carries no DNS rule"; exit 1; }
	@if grep -q 'world\|kube-apiserver\|toCIDR' /tmp/vko-cnp-pol.out; then echo "FAIL: the OTLP policy admits more than DNS and the collector"; exit 1; fi
	@echo "ok: cilium OTLP policy"
	@echo "--> a collector that is not an in-cluster Service: the cluster entity on the endpoint's port (443 for an https URL without one)"
	@helm template t $(CONNECTIVITY_DIR) $(KG_NETPOL) --set klausGateway.observability.otlpEndpoint=https://collector.example.com >/tmp/vko-ext.out 2>&1 || { cat /tmp/vko-ext.out; exit 1; }
	@$(PICK) /tmp/vko-ext.out CiliumNetworkPolicy $(KG_OTLP_POLICY) >/tmp/vko-ext-pol.out || { echo "FAIL: no OTLP policy for an external collector"; exit 1; }
	@grep -q 'toEntities:' /tmp/vko-ext-pol.out && grep -q '^        - cluster$$' /tmp/vko-ext-pol.out || { echo "FAIL: an external collector does not fall back to the cluster entity"; cat /tmp/vko-ext-pol.out; exit 1; }
	@grep -q 'port: "443"' /tmp/vko-ext-pol.out || { echo "FAIL: an https collector without a port is not on 443"; exit 1; }
	@echo "ok: external collector -> cluster entity on 443"
	@echo "--> kubernetes flavour: a NetworkPolicy, Egress only, the endpoint's namespace on the port + DNS"
	@helm template t $(CONNECTIVITY_DIR) $(KG_NETPOL) --set klausGateway.observability.otlpEndpoint=http://otlp-gateway.kube-system.svc:4317 --set networkPolicy.flavor=kubernetes --set networkPolicy.kubernetes.apiServerCIDR=10.9.0.1/32 >/tmp/vko-k8s.out 2>&1 || { cat /tmp/vko-k8s.out; exit 1; }
	@$(PICK) /tmp/vko-k8s.out NetworkPolicy $(KG_OTLP_POLICY) >/tmp/vko-k8s-pol.out || { echo "FAIL: no NetworkPolicy $(KG_OTLP_POLICY) in the kubernetes flavour"; exit 1; }
	@if $(PICK) /tmp/vko-k8s.out CiliumNetworkPolicy $(KG_OTLP_POLICY) >/dev/null 2>&1; then echo "FAIL: the cilium OTLP policy renders in the kubernetes flavour"; exit 1; fi
	@grep -q 'policyTypes: \[Egress\]' /tmp/vko-k8s-pol.out || { echo "FAIL: the NetworkPolicy is not Egress only"; exit 1; }
	@grep -q 'kubernetes.io/metadata.name: kube-system' /tmp/vko-k8s-pol.out || { echo "FAIL: the NetworkPolicy does not select the endpoint's namespace"; cat /tmp/vko-k8s-pol.out; exit 1; }
	@grep -q 'port: 4317' /tmp/vko-k8s-pol.out || { echo "FAIL: the NetworkPolicy is not on 4317"; exit 1; }
	@echo "ok: kubernetes OTLP policy"
	@echo "--> the selector follows klausGateway.fullnameOverride; no endpoint, networkPolicy off, component off: none"
	@helm template t $(CONNECTIVITY_DIR) $(KG_NETPOL) --set klausGateway.observability.otlpEndpoint=http://otlp-gateway.kube-system.svc:4317 --set klausGateway.fullnameOverride=kg-renamed 2>/dev/null | $(PICK) /dev/stdin CiliumNetworkPolicy $(KG_OTLP_POLICY) | grep -q 'app.kubernetes.io/name: "kg-renamed"' || { echo "FAIL: the OTLP policy does not select the renamed pod"; exit 1; }
	@helm template t $(CONNECTIVITY_DIR) $(KG_NETPOL) >/tmp/vko-none.out 2>&1 || { cat /tmp/vko-none.out; exit 1; }
	@if grep -q '$(KG_OTLP_POLICY)' /tmp/vko-none.out; then echo "FAIL: the OTLP policy renders without an endpoint (the connectivity chart's own default is empty)"; exit 1; fi
	@helm template t $(CONNECTIVITY_DIR) $(KG_NETPOL) --set klausGateway.observability.otlpEndpoint=http://otlp-gateway.kube-system.svc:4317 --set networkPolicy.enabled=false 2>/dev/null | grep -q '$(KG_OTLP_POLICY)' && { echo "FAIL: the OTLP policy renders with networkPolicy.enabled=false"; exit 1; } || true
	@helm template t $(CONNECTIVITY_DIR) $(KG_NETPOL) --set klausGateway.observability.otlpEndpoint=http://otlp-gateway.kube-system.svc:4317 --set components.klaus-gateway.enabled=false 2>/dev/null | grep -q '$(KG_OTLP_POLICY)' && { echo "FAIL: the OTLP policy renders with the component off"; exit 1; } || true
	@echo "ok: guards"
	@echo "--> the kagent controller's OTLP rule is unchanged by the shared helper"
	@helm template t $(CONNECTIVITY_DIR) $(VM) --set components.kagent.enabled=true --set kagent.otel.tracing.enabled=true --set kagent.otel.logging.enabled=true >/tmp/vko-kagent.out 2>&1 || { cat /tmp/vko-kagent.out; exit 1; }
	@$(PICK) /tmp/vko-kagent.out CiliumNetworkPolicy agent-platform-connectivity-kagent-controller-egress >/tmp/vko-kagent-pol.out || { echo "FAIL: no kagent controller egress policy"; exit 1; }
	@grep -q "The OTLP gateway kagent's exporters send to (http://otlp-gateway.kube-system.svc:4317)" /tmp/vko-kagent-pol.out || { echo "FAIL: the kagent controller's OTLP rule lost its comment"; grep -n -B1 -A6 "OTLP" /tmp/vko-kagent-pol.out; exit 1; }
	@grep -c "The OTLP gateway kagent's exporters send to" /tmp/vko-kagent-pol.out | grep -qx 1 || { echo "FAIL: expected exactly one OTLP rule on the kagent controller (tracing and logging share the destination)"; grep -c "The OTLP gateway" /tmp/vko-kagent-pol.out; exit 1; }
	@grep -q 'port: "4317"' /tmp/vko-kagent-pol.out || { echo "FAIL: the kagent controller's OTLP rule lost its port"; exit 1; }
	@echo "ok: $@"

MUSTER_OTLP_POLICY := muster-otlp-egress
MUSTER_OTLP_EP := http://otlp-gateway.kube-system.svc:4317

.PHONY: verify-muster-otlp
verify-muster-otlp: ## Assert muster's OTLP egress (giantswarm/giantswarm#36711): the meta chart forwards muster.muster.observability.otel.endpoint to the connectivity release; the connectivity chart renders -muster-otlp-egress exactly while it is set, selecting muster by name — DNS + the endpoint's namespace on its port in the cilium flavour, a namespaceSelector on the port in the kubernetes one; none with networkPolicy off or the component off.
	@echo "====> $@ ($(CHART_DIR) + $(CONNECTIVITY_DIR))"
	@helm template t $(CHART_DIR) $(VM) >/tmp/vmo-meta.out 2>&1 || { cat /tmp/vmo-meta.out; exit 1; }
	@$(PICK) /tmp/vmo-meta.out HelmRelease agent-platform-connectivity >/tmp/vmo-meta-hr.out || { echo "FAIL: no connectivity HelmRelease"; exit 1; }
	@test "$$(awk '/^    [^ ]/ { a = ($$0 == "    muster:"); b = c = d = 0; next } a && /^      [^ ]/ { b = ($$0 == "      muster:"); c = d = 0; next } b && /^        [^ ]/ { c = ($$0 == "        observability:"); d = 0; next } c && /^          [^ ]/ { d = ($$0 == "          otel:"); next } d && /^            endpoint: / { sub(/^            endpoint: /, ""); print; exit }' /tmp/vmo-meta-hr.out)" = "$(MUSTER_OTLP_EP)" || { echo "FAIL: the connectivity release does not receive muster.muster.observability.otel.endpoint"; exit 1; }
	@echo "ok: endpoint forwarded"
	@helm template t $(CONNECTIVITY_DIR) $(VM) --set muster.muster.observability.otel.endpoint=$(MUSTER_OTLP_EP) >/tmp/vmo-cnp.out 2>&1 || { cat /tmp/vmo-cnp.out; exit 1; }
	@$(PICK) /tmp/vmo-cnp.out CiliumNetworkPolicy $(MUSTER_OTLP_POLICY) >/tmp/vmo-cnp-pol.out || { echo "FAIL: no CiliumNetworkPolicy $(MUSTER_OTLP_POLICY) with an OTLP endpoint"; exit 1; }
	@grep -q 'app.kubernetes.io/name: muster$$' /tmp/vmo-cnp-pol.out || { echo "FAIL: the OTLP policy does not select muster"; exit 1; }
	@grep -q 'io.kubernetes.pod.namespace: kube-system$$' /tmp/vmo-cnp-pol.out || { echo "FAIL: the OTLP policy does not select the endpoint's namespace"; cat /tmp/vmo-cnp-pol.out; exit 1; }
	@grep -q 'port: "4317"' /tmp/vmo-cnp-pol.out || { echo "FAIL: the OTLP policy is not on 4317"; exit 1; }
	@grep -q 'k8s-app: kube-dns' /tmp/vmo-cnp-pol.out || { echo "FAIL: the OTLP policy carries no DNS rule"; exit 1; }
	@if grep -q 'world\|kube-apiserver\|toCIDR' /tmp/vmo-cnp-pol.out; then echo "FAIL: the OTLP policy admits more than DNS and the collector"; exit 1; fi
	@echo "ok: cilium OTLP policy"
	@helm template t $(CONNECTIVITY_DIR) $(VM) --set muster.muster.observability.otel.endpoint=$(MUSTER_OTLP_EP) --set networkPolicy.flavor=kubernetes --set networkPolicy.kubernetes.apiServerCIDR=10.9.0.1/32 >/tmp/vmo-k8s.out 2>&1 || { cat /tmp/vmo-k8s.out; exit 1; }
	@$(PICK) /tmp/vmo-k8s.out NetworkPolicy $(MUSTER_OTLP_POLICY) >/tmp/vmo-k8s-pol.out || { echo "FAIL: no NetworkPolicy $(MUSTER_OTLP_POLICY) in the kubernetes flavour"; exit 1; }
	@grep -q 'kubernetes.io/metadata.name: kube-system' /tmp/vmo-k8s-pol.out && grep -q 'port: 4317' /tmp/vmo-k8s-pol.out || { echo "FAIL: the NetworkPolicy does not select the endpoint's namespace on 4317"; cat /tmp/vmo-k8s-pol.out; exit 1; }
	@echo "ok: kubernetes OTLP policy"
	@helm template t $(CONNECTIVITY_DIR) $(VM) >/tmp/vmo-none.out 2>&1 || { cat /tmp/vmo-none.out; exit 1; }
	@if grep -q 'name: $(MUSTER_OTLP_POLICY)$$' /tmp/vmo-none.out; then echo "FAIL: the OTLP policy renders without an endpoint"; exit 1; fi
	@helm template t $(CONNECTIVITY_DIR) $(VM) --set muster.muster.observability.otel.endpoint=$(MUSTER_OTLP_EP) --set networkPolicy.enabled=false 2>/dev/null | grep -q 'name: $(MUSTER_OTLP_POLICY)$$' && { echo "FAIL: the OTLP policy renders with networkPolicy.enabled=false"; exit 1; } || true
	@helm template t $(CONNECTIVITY_DIR) $(VM) --set muster.muster.observability.otel.endpoint=$(MUSTER_OTLP_EP) --set components.muster.enabled=false 2>/dev/null | grep -q 'name: $(MUSTER_OTLP_POLICY)$$' && { echo "FAIL: the OTLP policy renders with the component off"; exit 1; } || true
	@echo "ok: $@"

SUBSTRATE_OTLP_EXPORTERS := substrate-ate-api-server substrate-ate-controller substrate-atelet substrate-atenet-router
SUBSTRATE_OTLP_VM := $(VM) --set components.substrate.enabled=true --set substrate.otel.endpoint=$(MUSTER_OTLP_EP)

.PHONY: verify-substrate-otlp
verify-substrate-otlp: ## Assert Substrate's OTLP export reaches a tenant (giantswarm/giantswarm#36711): the meta chart forwards substrate.otel.endpoint to the connectivity release and substrate.podLabels (the observability.giantswarm.io/tenant label otlp-gateway routes a headerless export by) to the substrate release; the connectivity chart's policies of the four exporters (ate-api-server, ate-controller, atelet, atenet-router) each open the endpoint's namespace on its port, no cluster-wide 4317 rule remains, a signal's own endpoint adds its destination and a disabled signal's does not, an endpoint that is not an in-cluster Service is the cluster entity on its port, and no endpoint opens nothing.
	@echo "====> $@ ($(CHART_DIR) + $(CONNECTIVITY_DIR))"
	@helm template t $(CHART_DIR) $(VM) $(SUBSTRATE_ON) >/tmp/vso-meta.out 2>&1 || { cat /tmp/vso-meta.out; exit 1; }
	@$(PICK) /tmp/vso-meta.out HelmRelease substrate | grep -A1 '^    podLabels:$$' | grep -q '^      observability.giantswarm.io/tenant: giantswarm$$' || { echo "FAIL: the substrate release does not receive the tenant pod label"; exit 1; }
	@$(PICK) /tmp/vso-meta.out HelmRelease agent-platform-connectivity | grep -A1 '^      otel:$$' | grep -q '^        endpoint: $(MUSTER_OTLP_EP)$$' || { echo "FAIL: the connectivity release does not receive substrate.otel.endpoint"; exit 1; }
	@echo "ok: meta chart forwards the endpoint and the tenant label"
	@helm template t $(CONNECTIVITY_DIR) $(SUBSTRATE_OTLP_VM) >/tmp/vso.out 2>&1 || { cat /tmp/vso.out; exit 1; }
	@for p in $(SUBSTRATE_OTLP_EXPORTERS); do \
		$(PICK) /tmp/vso.out CiliumNetworkPolicy $$p >/tmp/vso-pol.out || { echo "FAIL: no CiliumNetworkPolicy $$p"; exit 1; }; \
		grep -B1 -A4 '^        - matchLabels:$$' /tmp/vso-pol.out | grep -A4 'io.kubernetes.pod.namespace: kube-system$$' | grep -q 'port: "4317"' || { echo "FAIL: $$p does not open kube-system:4317"; cat /tmp/vso-pol.out; exit 1; }; \
		if grep -B6 'port: "4317"' /tmp/vso-pol.out | grep -q -- '- cluster$$'; then echo "FAIL: $$p still opens 4317 to the whole cluster"; exit 1; fi; \
	done
	@if $(PICK) /tmp/vso.out CiliumNetworkPolicy substrate-atenet-egress | grep -q 'port: "4317"'; then echo "FAIL: atenet-egress exports nothing of its own, yet opens 4317 with kagent off"; exit 1; fi
	@echo "ok: the four exporters open the endpoint"
	@helm template t $(CONNECTIVITY_DIR) $(SUBSTRATE_OTLP_VM) --set substrate.otel.traces.endpoint=http://tempo-gw.tracing.svc:4317 >/tmp/vso-sig.out 2>&1 || { cat /tmp/vso-sig.out; exit 1; }
	@$(PICK) /tmp/vso-sig.out CiliumNetworkPolicy substrate-atelet | grep -q 'io.kubernetes.pod.namespace: tracing$$' || { echo "FAIL: a signal's own endpoint does not add its destination"; exit 1; }
	@$(PICK) /tmp/vso-sig.out CiliumNetworkPolicy substrate-atelet | grep -q 'io.kubernetes.pod.namespace: kube-system$$' || { echo "FAIL: the other signals' shared endpoint is gone"; exit 1; }
	@helm template t $(CONNECTIVITY_DIR) $(SUBSTRATE_OTLP_VM) --set substrate.otel.traces.endpoint=http://tempo-gw.tracing.svc:4317 --set substrate.otel.traces.enabled=false >/tmp/vso-off.out 2>&1 || { cat /tmp/vso-off.out; exit 1; }
	@if $(PICK) /tmp/vso-off.out CiliumNetworkPolicy substrate-atelet | grep -q 'io.kubernetes.pod.namespace: tracing$$'; then echo "FAIL: a disabled signal's endpoint is opened"; exit 1; fi
	@echo "ok: per-signal endpoints"
	@helm template t $(CONNECTIVITY_DIR) $(VM) --set components.substrate.enabled=true --set substrate.otel.endpoint=https://collector.example.com >/tmp/vso-ext.out 2>&1 || { cat /tmp/vso-ext.out; exit 1; }
	@$(PICK) /tmp/vso-ext.out CiliumNetworkPolicy substrate-ate-controller | grep -A4 -- '- cluster$$' | grep -q 'port: "443"' || { echo "FAIL: an external collector is not the cluster entity on 443"; exit 1; }
	@helm template t $(CONNECTIVITY_DIR) $(VM) --set components.substrate.enabled=true >/tmp/vso-none.out 2>&1 || { cat /tmp/vso-none.out; exit 1; }
	@for p in $(SUBSTRATE_OTLP_EXPORTERS); do \
		if $(PICK) /tmp/vso-none.out CiliumNetworkPolicy $$p | grep -q 'OTLP gateway\|port: "4317"'; then echo "FAIL: $$p opens an OTLP destination with no endpoint"; exit 1; fi; \
	done
	@echo "ok: $@"

.PHONY: verify-kagent-storage-version
verify-kagent-storage-version: ## Assert the kagent CRDs' storage-version hooks of the 3.x → 4.x cut-over (#396): with kagent on, the backup Job (pre-install,pre-upgrade, -7: records the objects of modelconfigs/modelproviderconfigs/remotemcpservers.kagent.dev still stored at v1alpha2 into the migration ConfigMap, sets the crds policy of the HelmRelease the CRDs' Flux labels name to Skip (#416), deletes those CRDs and watches them stay absent for 60 s — a re-created one is deleted again and fails the hook naming the owner) and the restore Job (post-install,post-upgrade, 0: waits for modelconfigs.kagent.dev to serve v1alpha3, re-creates the recorded ModelConfigs no Helm release owned at kagent.dev/v1alpha3, tolerates AlreadyExists, marks restored-at) as the hook identity in the helm image, the identity at their events; with the engine off (the fleet) the same pair and nothing else; with kagent off none of it; the kagent namespace follows kagent.namespaceOverride; helm lint.
	@echo "====> $@ ($(CHART_DIR))"
	@echo "--> engine on, kagent on (ci-values): both hook Jobs, their events and weights, the identity at theirs"
	@helm template t $(CHART_DIR) $(STORAGE_ON) >/tmp/vsv-on.out 2>&1 || { cat /tmp/vsv-on.out; exit 1; }
	@$(PICK) /tmp/vsv-on.out Job $(STORAGE_BACKUP) >/tmp/vsv-backup.out || { echo "FAIL: no backup hook Job $(STORAGE_BACKUP)"; exit 1; }
	@grep -q 'helm.sh/hook: pre-install,pre-upgrade$$' /tmp/vsv-backup.out || { echo "FAIL: the backup hook is not pre-install,pre-upgrade"; grep helm.sh/hook /tmp/vsv-backup.out; exit 1; }
	@grep -q 'helm.sh/hook-weight: "-7"' /tmp/vsv-backup.out || { echo "FAIL: the backup hook is not at weight -7 (after the kagent namespace hook at -8, ahead of the self hooks at -6)"; exit 1; }
	@$(PICK) /tmp/vsv-on.out Job $(STORAGE_RESTORE) >/tmp/vsv-restore.out || { echo "FAIL: no restore hook Job $(STORAGE_RESTORE)"; exit 1; }
	@grep -q 'helm.sh/hook: post-install,post-upgrade$$' /tmp/vsv-restore.out || { echo "FAIL: the restore hook is not post-install,post-upgrade"; grep helm.sh/hook /tmp/vsv-restore.out; exit 1; }
	@grep -q 'helm.sh/hook-weight: "0"' /tmp/vsv-restore.out || { echo "FAIL: the restore hook is not at weight 0"; exit 1; }
	@for f in /tmp/vsv-backup.out /tmp/vsv-restore.out; do \
		grep -q 'helm.sh/hook-delete-policy: before-hook-creation,hook-succeeded' $$f || { echo "FAIL: $$f: the hook delete policy is not before-hook-creation,hook-succeeded"; exit 1; }; \
		grep -q 'serviceAccountName: t-hooks' $$f || { echo "FAIL: $$f: the Job does not run as the hook identity t-hooks"; exit 1; }; \
		grep -q 'image: "gsoci.azurecr.io/giantswarm/alpine-k8s:' $$f || { echo "FAIL: $$f: the Job does not run the helm image (a script needs sh, kubectl and jq)"; exit 1; }; \
		grep -q 'command: \["/bin/sh", "-eu", "-c"\]' $$f || { echo "FAIL: $$f: the Job is not a script under sh -eu"; exit 1; }; \
		grep -q 'ns="kagent"' $$f || { echo "FAIL: $$f: the script does not name the kagent namespace"; exit 1; }; \
		grep -q 'cm="$(STORAGE_CM)"' $$f || { echo "FAIL: $$f: the script does not name the ConfigMap $(STORAGE_CM)"; exit 1; }; \
		for needle in 'runAsNonRoot: true' 'readOnlyRootFilesystem: true' 'allowPrivilegeEscalation: false' 'type: RuntimeDefault' 'restartPolicy: Never'; do grep -q "$$needle" $$f || { echo "FAIL: $$f lacks $$needle"; exit 1; }; done; \
	done
	@echo "ok: two hook Jobs as t-hooks in the helm image, restricted pods"
	@echo "--> the hook pods' memory (#593): requests stay small, the limit is headroom for a kubectl run's transient — 512Mi, never 128Mi again"
	@for f in /tmp/vsv-backup.out /tmp/vsv-restore.out; do \
		python3 -c 'import sys,yaml; d=[x for x in yaml.safe_load_all(open(sys.argv[1])) if x][0]; r=d["spec"]["template"]["spec"]["containers"][0]["resources"]; assert r["requests"]=={"cpu":"10m","memory":"32Mi"}, r; assert r["limits"]=={"memory":"512Mi"}, r; print("ok: %s requests 10m/32Mi, limit 512Mi" % d["metadata"]["name"])' $$f || exit 1; \
	done
	@echo "--> the backup script: the three CRDs, v1alpha2 as the stale version, status and server-set metadata stripped, the record written before the delete, kubectl delete crd --wait"
	@grep -q 'for crd in modelconfigs.kagent.dev modelproviderconfigs.kagent.dev remotemcpservers.kagent.dev; do' /tmp/vsv-backup.out || { echo "FAIL: the backup does not walk exactly the three CRDs"; exit 1; }
	@grep -q 'stale="v1alpha2"' /tmp/vsv-backup.out || { echo "FAIL: the backup does not name v1alpha2 as the stale storage version"; exit 1; }
	@grep -q "jsonpath='{.status.storedVersions}'" /tmp/vsv-backup.out || { echo "FAIL: the backup does not read status.storedVersions"; exit 1; }
	@grep -q 'del(.status, .metadata.managedFields, .metadata.resourceVersion, .metadata.uid, .metadata.creationTimestamp, .metadata.generation)' /tmp/vsv-backup.out || { echo "FAIL: the record does not strip status and the server-set metadata (the 1 MiB ConfigMap limit)"; exit 1; }
	@grep -q '\.data\["recorded-at"\] = \$$at' /tmp/vsv-backup.out || { echo "FAIL: the record carries no recorded-at"; exit 1; }
	@grep -q 'kubectl delete customresourcedefinitions.apiextensions.k8s.io "\$$crd" --wait --timeout=3m' /tmp/vsv-backup.out || { echo "FAIL: the backup does not delete the CRD with --wait"; exit 1; }
	@[ "$$(grep -n 'kubectl create -f /tmp/cm.json' /tmp/vsv-backup.out | cut -d: -f1)" -lt "$$(grep -n 'kubectl delete customresourcedefinitions' /tmp/vsv-backup.out | head -1 | cut -d: -f1)" ] || { echo "FAIL: the backup deletes a CRD before the record is written"; exit 1; }
	@grep -q 'nothing to migrate' /tmp/vsv-backup.out || { echo "FAIL: the backup has no no-op branch (a fresh install, a second run)"; exit 1; }
	@echo "--> the backup stops the HelmRelease the CRDs' helm.toolkit.fluxcd.io labels name from re-applying them before the delete (#416): crds Skip on install and upgrade, a gone owner tolerated; then watches the CRDs stay absent for 60 s, deletes a re-created one again and fails naming the owner"
	@grep -qF 'helm\.toolkit\.fluxcd\.io/namespace}|{.metadata.labels.helm\.toolkit\.fluxcd\.io/name}' /tmp/vsv-backup.out || { echo "FAIL: the backup does not read the owner HelmRelease from the CRD's helm.toolkit.fluxcd.io/namespace + name labels"; exit 1; }
	@grep -q 'kubectl patch helmreleases.helm.toolkit.fluxcd.io -n "\$$ons" "\$$oname" --type merge -p' /tmp/vsv-backup.out || { echo "FAIL: the backup does not patch the owner HelmRelease"; exit 1; }
	@grep -qF '{"spec":{"install":{"crds":"Skip"},"upgrade":{"crds":"Skip"}}}' /tmp/vsv-backup.out || { echo "FAIL: the patch does not set spec.install.crds and spec.upgrade.crds to Skip"; exit 1; }
	@grep -q 'kubectl get helmreleases.helm.toolkit.fluxcd.io -n "\$$ons" "\$$oname" -o jsonpath=.*--ignore-not-found' /tmp/vsv-backup.out || { echo "FAIL: a gone owner HelmRelease must be tolerated (--ignore-not-found), not fail the hook"; exit 1; }
	@grep -q 'is gone; nothing to stop' /tmp/vsv-backup.out || { echo "FAIL: the backup does not report a gone owner"; exit 1; }
	@[ "$$(grep -n 'kubectl patch helmreleases' /tmp/vsv-backup.out | cut -d: -f1)" -lt "$$(grep -n 'kubectl delete customresourcedefinitions' /tmp/vsv-backup.out | head -1 | cut -d: -f1)" ] || { echo "FAIL: the owner must be patched before the first CRD is deleted"; exit 1; }
	@[ "$$(grep -n 'kubectl create -f /tmp/cm.json' /tmp/vsv-backup.out | cut -d: -f1)" -lt "$$(grep -n 'kubectl patch helmreleases' /tmp/vsv-backup.out | cut -d: -f1)" ] || { echo "FAIL: the record must be written before the owner is touched"; exit 1; }
	@grep -q 'deadline=\$$(( \$$(date +%s) + 60 ))' /tmp/vsv-backup.out || { echo "FAIL: the backup's watch is not 60 s"; exit 1; }
	@[ "$$(grep -c 'kubectl delete customresourcedefinitions.apiextensions.k8s.io "\$$crd" --wait --timeout=3m' /tmp/vsv-backup.out)" = "2" ] || { echo "FAIL: the backup must delete a CRD twice: once after the record, once more in the watch when it comes back"; grep -n 'kubectl delete customresourcedefinitions' /tmp/vsv-backup.out; exit 1; }
	@[ "$$(grep -n 'kubectl delete customresourcedefinitions' /tmp/vsv-backup.out | head -1 | cut -d: -f1)" -lt "$$(grep -n 'deadline=\$$(( \$$(date +%s) + 60 ))' /tmp/vsv-backup.out | cut -d: -f1)" ] || { echo "FAIL: the watch must follow the deletes"; exit 1; }
	@grep -q 're-created at \$$state' /tmp/vsv-backup.out || { echo "FAIL: a re-created CRD is not reported"; exit 1; }
	@grep -qF 'by HelmRelease {.metadata.labels.helm\.toolkit\.fluxcd\.io/namespace}/{.metadata.labels.helm\.toolkit\.fluxcd\.io/name}' /tmp/vsv-backup.out || { echo "FAIL: the re-creation report does not name the owner HelmRelease from the CRD's labels"; exit 1; }
	@grep -A1 're-created after the backup deleted them' /tmp/vsv-backup.out | grep -q 'exit 1' || { echo "FAIL: a re-creation must fail the hook (loudly, after deleting again)"; exit 1; }
	@grep -q 'stayed absent for 60 s' /tmp/vsv-backup.out || { echo "FAIL: the watch does not report the CRDs stayed absent"; exit 1; }
	@grep -q 'about: "record the objects .* stop the HelmRelease that applied them from applying them again (crds: Skip) and delete those CRDs' /tmp/vsv-backup.out || { echo "FAIL: the backup Job's about line does not say it stops the owner"; grep about: /tmp/vsv-backup.out; exit 1; }
	@echo "--> the backup re-points the three kinds to v1alpha3 in every Helm release manifest that still names them at v1alpha2 (Helm reads a manifest back through a served version), on every run, one Secret at a time, the patch through a file"
	@grep -q "kubectl get secrets -A -l owner=helm --field-selector type=helm.sh/release.v1 --no-headers --chunk-size=100 | awk '{print \$$1, \$$2}' > /tmp/releases.txt" /tmp/vsv-backup.out || { echo "FAIL: the backup does not list the Helm release Secrets through the server-side Table (names only; a client-side printer collects every payload first and is OOM-killed at 128Mi, #414)"; grep -n 'kubectl get secrets' /tmp/vsv-backup.out; exit 1; }
	@if grep -q 'kubectl get secrets.*-o jsonpath\|kubectl get secrets.*-o custom-columns\|kubectl get secrets.*-o name' /tmp/vsv-backup.out; then echo "FAIL: the release listing uses a client-side printer (collects every Secret whole, #414)"; exit 1; fi
	@grep -qF "grep -qF 'apiVersion: kagent.dev/v1alpha2\n' /tmp/release.json || continue" /tmp/vsv-backup.out || { echo "FAIL: jq parses every release; a release whose manifest names no kagent.dev/v1alpha2 object must be skipped before jq on the encoded-newline marker (#414, #593)"; exit 1; }
	@if grep -q "grep -q 'kagent.dev/v1alpha2' /tmp/release.json" /tmp/vsv-backup.out; then echo "FAIL: a plain grep for the version matches this chart's own release (the hook script under .hooks[] carries it) and sends every revision of it through jq on every upgrade (#593)"; exit 1; fi
	@[ "$$(grep -n "grep -qF 'apiVersion: kagent.dev/v1alpha2" /tmp/vsv-backup.out | cut -d: -f1)" -lt "$$(grep -n 'n="$$(jq -r --arg k "$$kinds"' /tmp/vsv-backup.out | cut -d: -f1)" ] || { echo "FAIL: the grep pre-filter must run before jq"; exit 1; }
	@echo "--> the marker against fixtures: this chart's own release (the rendered backup Job as a stored hook, a manifest without kagent objects) is skipped; a manifest naming a v1alpha2 object is not"
	@python3 -c 'import json; hook=open("/tmp/vsv-backup.out").read(); assert "kagent.dev/v1alpha2" in hook; neg=json.dumps({"manifest":"---\napiVersion: v1\nkind: ConfigMap\nmetadata:\n  name: x\n","hooks":[{"manifest":hook}]}); pos=json.dumps({"manifest":"---\napiVersion: kagent.dev/v1alpha2\nkind: ModelConfig\nmetadata:\n  name: x\n","hooks":[]}); open("/tmp/vsv-neg.json","w").write(neg); open("/tmp/vsv-pos.json","w").write(pos); print("fixtures: negative carries the version %d times in %d bytes, positive %d bytes" % (neg.count("kagent.dev/v1alpha2"), len(neg), len(pos)))'
	@if grep -qF 'apiVersion: kagent.dev/v1alpha2\n' /tmp/vsv-neg.json; then echo "FAIL: the marker matches a release whose hook script carries the version but whose manifest names no v1alpha2 object"; exit 1; fi
	@grep -qF 'apiVersion: kagent.dev/v1alpha2\n' /tmp/vsv-pos.json || { echo "FAIL: the marker misses a manifest that names a kagent.dev/v1alpha2 object"; exit 1; }
	@echo "ok: marker pre-filter"
	@grep -qF "kinds='(^|\n)kind: (ModelConfig|ModelProviderConfig|RemoteMCPServer)\n'" /tmp/vsv-backup.out || { echo "FAIL: the re-point is not confined to documents of the three kinds"; grep -n "kinds=" /tmp/vsv-backup.out; exit 1; }
	@grep -qF 'gsub("apiVersion: kagent.dev/v1alpha2\n"; "apiVersion: kagent.dev/v1alpha3\n")' /tmp/vsv-backup.out || { echo "FAIL: the re-point does not swap kagent.dev/v1alpha2 for v1alpha3"; exit 1; }
	@grep -q 'base64 -d < /tmp/release.b64 | base64 -d | gzip -dc' /tmp/vsv-backup.out || { echo "FAIL: the re-point does not decode Helm storage (base64 twice, gzip)"; exit 1; }
	@grep -q 'kubectl patch secret -n "\$$sns" "\$$sname" --type merge --patch-file /tmp/release-patch.json' /tmp/vsv-backup.out || { echo "FAIL: the re-point does not patch the Secret through a file"; exit 1; }
	@[ "$$(grep -n 'kubectl delete customresourcedefinitions' /tmp/vsv-backup.out | tail -1 | cut -d: -f1)" -lt "$$(grep -n '^ *kinds=' /tmp/vsv-backup.out | cut -d: -f1)" ] || { echo "FAIL: the re-point must follow the CRD deletion and the watch"; exit 1; }
	@if grep -qE '^ *exit 0' /tmp/vsv-backup.out; then echo "FAIL: the backup exits early; the re-point must run on every backup, also when no CRD stores v1alpha2 any more (a re-run after a failed first attempt)"; exit 1; fi
	@echo "ok: backup script"
	@echo "--> the restore script: waits for modelconfigs.kagent.dev Established serving v1alpha3, skips Helm-owned objects, swaps the apiVersion, tolerates AlreadyExists, fails otherwise, marks restored-at and runs once"
	@grep -q 'crd="modelconfigs.kagent.dev"' /tmp/vsv-restore.out || { echo "FAIL: the restore does not wait on modelconfigs.kagent.dev"; exit 1; }
	@grep -q 'version="v1alpha3"' /tmp/vsv-restore.out || { echo "FAIL: the restore does not name v1alpha3"; exit 1; }
	@grep -q 'type=="Established"' /tmp/vsv-restore.out || { echo "FAIL: the restore does not wait for the CRD to be Established"; exit 1; }
	@grep -q 'deadline=\$$(( \$$(date +%s) + 480 ))' /tmp/vsv-restore.out || { echo "FAIL: the restore's wait is not bounded at 480 s"; exit 1; }
	@grep -q 'select(.metadata.annotations\["meta.helm.sh/release-name"\] == null)' /tmp/vsv-restore.out || { echo "FAIL: the restore does not skip the Helm-owned ModelConfigs (they come back from their releases)"; exit 1; }
	@grep -q '{apiVersion: \$$v, kind, metadata: (.metadata | {name, namespace, labels, annotations} | with_entries(select(.value != null))), spec}' /tmp/vsv-restore.out || { echo "FAIL: the restore does not re-create name, namespace, labels, annotations and spec at the new apiVersion"; exit 1; }
	@grep -q '\*AlreadyExists\*) echo "ModelConfig \$$ref: already present (re-created by its owner)"' /tmp/vsv-restore.out || { echo "FAIL: the restore does not tolerate AlreadyExists"; exit 1; }
	@grep -q '\*) echo "ModelConfig \$$ref: \$$out" >&2; exit 1 ;;' /tmp/vsv-restore.out || { echo "FAIL: the restore does not fail the Job on another refusal"; exit 1; }
	@grep -q 'restored-at' /tmp/vsv-restore.out || { echo "FAIL: the restore does not mark restored-at"; exit 1; }
	@grep -q 'restored at \$$restored; nothing to do' /tmp/vsv-restore.out || { echo "FAIL: the restore does not run once (a ModelConfig removed after the cut-over would come back)"; exit 1; }
	@grep -q 'recorded, not restored: \$$n \$$k' /tmp/vsv-restore.out || { echo "FAIL: the restore does not report the recorded RemoteMCPServers / ModelProviderConfigs"; exit 1; }
	@echo "ok: restore script"
	@echo "--> the hook identity is created for the hooks' events (pre-install,pre-upgrade,post-install,post-upgrade) and the engine's pre-delete"
	@for kind in ServiceAccount ClusterRoleBinding; do \
		$(PICK) /tmp/vsv-on.out $$kind t-hooks | grep -q 'helm.sh/hook: pre-install,pre-upgrade,post-install,post-upgrade,pre-delete$$' || { echo "FAIL: the hook $$kind t-hooks is not created for pre-install,pre-upgrade,post-install,post-upgrade,pre-delete"; $(PICK) /tmp/vsv-on.out $$kind t-hooks | grep helm.sh/hook; exit 1; }; \
	done
	@echo "ok: identity events"
	@echo "--> engine off (the fleet, a cluster's own Flux): the same pair and the identity at their events, nothing else hooked"
	@helm template t $(CHART_DIR) $(STORAGE_ON) --set components.flux.enabled=false >/tmp/vsv-off.out 2>&1 || { cat /tmp/vsv-off.out; exit 1; }
	@$(PICK) /tmp/vsv-off.out Job $(STORAGE_BACKUP) >/dev/null || { echo "FAIL: the backup hook is gone with the engine off (every installation that ran kagent 0.10 needs it)"; exit 1; }
	@$(PICK) /tmp/vsv-off.out Job $(STORAGE_RESTORE) >/dev/null || { echo "FAIL: the restore hook is gone with the engine off"; exit 1; }
	@[ "$$(grep -c '^    helm.sh/hook: ' /tmp/vsv-off.out)" = "5" ] || { echo "FAIL: engine off must hook exactly the two Jobs, the identity (SA + CRB) and its network policy (#413)"; grep -n 'helm.sh/hook: ' /tmp/vsv-off.out; exit 1; }
	@for kind in ServiceAccount ClusterRoleBinding; do \
		$(PICK) /tmp/vsv-off.out $$kind t-hooks | grep -q 'helm.sh/hook: pre-install,pre-upgrade,post-install,post-upgrade$$' || { echo "FAIL: engine off: the hook $$kind t-hooks is not at pre-install,pre-upgrade,post-install,post-upgrade (no pre-delete without the engine)"; exit 1; }; \
	done
	@if grep -q 'helm.sh/hook: .*pre-delete' /tmp/vsv-off.out; then echo "FAIL: engine off renders a pre-delete hook"; exit 1; fi
	@echo "ok: engine off"
	@echo "--> kagent off: none of it; the identity back to pre-delete (engine on), gone (engine off)"
	@helm template t $(CHART_DIR) $(STORAGE_ON) --set components.kagent.enabled=false >/tmp/vsv-kagoff.out 2>&1 || { cat /tmp/vsv-kagoff.out; exit 1; }
	@if grep -q 'kagent-storage-version' /tmp/vsv-kagoff.out; then echo "FAIL: the storage-version hooks render with kagent off"; exit 1; fi
	@$(PICK) /tmp/vsv-kagoff.out ServiceAccount t-hooks | grep -q 'helm.sh/hook: pre-delete$$' || { echo "FAIL: kagent off: the hook identity is not back to pre-delete only"; exit 1; }
	@helm template t $(CHART_DIR) $(STORAGE_ON) --set components.kagent.enabled=false --set components.flux.enabled=false >/tmp/vsv-alloff.out 2>&1 || { cat /tmp/vsv-alloff.out; exit 1; }
	@if grep -q 'helm.sh/hook\|t-hooks' /tmp/vsv-alloff.out; then echo "FAIL: engine off, kagent off: a hook or the hook identity renders (the pure app-of-apps render)"; exit 1; fi
	@echo "ok: kagent off"
	@echo "--> the kagent namespace: kagent.namespaceOverride, else the HelmReleases' target namespace"
	@helm template t $(CHART_DIR) $(STORAGE_ON) --set kagent.namespaceOverride=models >/tmp/vsv-ns.out 2>&1 || { cat /tmp/vsv-ns.out; exit 1; }
	@[ "$$(grep -c 'ns="models"' /tmp/vsv-ns.out)" -ge 2 ] || { echo "FAIL: the hooks do not follow kagent.namespaceOverride"; exit 1; }
	@helm template t $(CHART_DIR) $(STORAGE_ON) --set kagent.namespaceOverride= --set gitops.targetNamespace=plat --set components.flux.enabled=false >/tmp/vsv-ns2.out 2>&1 || { cat /tmp/vsv-ns2.out; exit 1; }
	@[ "$$(grep -c 'ns="plat"' /tmp/vsv-ns2.out)" = "2" ] || { echo "FAIL: without an override the hooks do not fall back to gitops.targetNamespace"; grep -n 'ns=' /tmp/vsv-ns2.out; exit 1; }
	@echo "ok: namespace"
	@helm lint $(CHART_DIR) $(STORAGE_ON) >/tmp/vsv-lint.out 2>&1 || { cat /tmp/vsv-lint.out; exit 1; }
	@echo "ok: helm lint"
	@echo "ok: $@"

# --- e2e ---------------------------------------------------------------------
# One ATS scenario against any cluster its kubeconfig points at. The suite is
# already kubeconfig-driven; this target only packages the chart and hands the
# scenario its inputs (tests/ats/scenarios.py).
#
#   make e2e KUBECONFIG=~/.kube/lab.yaml
#   make e2e KUBECONFIG=… SCENARIO=functional
#   make e2e KUBECONFIG=… CLUSTER_TYPE=eks VALUES=helm/agent-platform/examples/managed-cloud-gs.yaml
#
# SECRETS AND THE DOMAIN STAY OUT OF THE REPOSITORY. The example files carry
# placeholders; tests/e2e_overlay.py turns the environment into an overlay,
# which this target writes to a temporary file that is removed on exit, and it
# never prints a value.
SCENARIO ?= smoke
CLUSTER_TYPE ?= kind
# The kubeconfig with a leading `~` expanded. zsh leaves the tilde of an
# argument of the form KUBECONFIG=~/.kube/lab.yaml alone, and the recipe quotes
# the value, so the shell never expands it either.
E2E_KUBECONFIG := $(abspath $(subst ~,$(HOME),$(KUBECONFIG)))
# The version the chart is packaged and installed under. A prerelease keeps a
# published self HelmRelease from ever matching it.
E2E_VERSION ?= 3.99.0-dev.local
E2E_DIST ?= dist
# The values files of the run, colon-separated, in Helm's order. This REPLACES
# the smoke's own list, so it must be complete: the kind smoke needs
# helm/agent-platform/examples/kind-lab-dex.yaml plus tests/ats/values-kagent.yaml
# and tests/ats/values-round-trips.yaml. Empty (the default) keeps the
# scenario's list, which is what a kind run wants. The overlay this target
# writes from the environment is layered after these, whatever they are.
# SCENARIO=functional installs the first of these files plus
# tests/ats/values-kagent.yaml, with the overlay last: that scenario's shape is
# the example with the engine off, not the smoke's round trips.
VALUES ?=
# The overlay this target writes from the environment, when any of these is set.
# None of them is a credential: E2E_IDP_SECRET_NAME and E2E_IDP_CA_SECRET are
# the NAMES of Secrets already on the cluster. The client secret itself never
# passes through a command line; it lives in the Secret the chart reads
# (global.identity.existingSecret) and, for the tests' own logins, in
# ATS_CLIENT_SECRET in the caller's environment.
#
# Each of these names one fact, which the chart and the suite both need, so the
# run names it once: the target passes them through, scenarios.load() reads each
# as the fallback of the matching ATS_ variable, and an ATS_ variable that the
# caller sets wins.
E2E_DOMAIN ?=
E2E_ISSUER_URL ?=
E2E_CLIENT_ID ?=
E2E_IDP_SECRET_NAME ?=
E2E_IDP_CA_SECRET ?=

.PHONY: e2e
e2e: ## Run one ATS scenario against any cluster (KUBECONFIG=… [SCENARIO=smoke|functional] [CLUSTER_TYPE=kind|eks] [VALUES=a.yaml:b.yaml]). Packages the chart first; builds a values overlay from the environment so no secret or domain is committed.
	@echo "====> $@ (scenario $(SCENARIO), cluster type $(CLUSTER_TYPE))"
	@test -n "$(KUBECONFIG)" || { echo "FAIL: KUBECONFIG is required, e.g. make e2e KUBECONFIG=~/.kube/lab.yaml"; exit 1; }
	@test -r "$(E2E_KUBECONFIG)" || { echo "FAIL: cannot read KUBECONFIG=$(KUBECONFIG)"; exit 1; }
	@mkdir -p $(E2E_DIST)
	@helm package $(CHART_DIR) --version $(E2E_VERSION) -d $(E2E_DIST) >/dev/null
	@archive=$(abspath $(E2E_DIST))/agent-platform-$(E2E_VERSION).tgz; \
	test -f "$$archive" || { echo "FAIL: helm package produced no $$archive"; exit 1; }; \
	overlay=""; \
	identity="$(E2E_ISSUER_URL)$(E2E_CLIENT_ID)$(E2E_IDP_SECRET_NAME)$(E2E_IDP_CA_SECRET)"; \
	if [ -n "$(E2E_DOMAIN)$$identity" ]; then \
		overlay=$$(mktemp "$${TMPDIR:-/tmp}/agent-platform-e2e-XXXXXX.yaml"); \
		trap 'rm -f "$$overlay"' EXIT INT TERM; \
		E2E_DOMAIN="$(E2E_DOMAIN)" \
		E2E_ISSUER_URL="$(E2E_ISSUER_URL)" \
		E2E_CLIENT_ID="$(E2E_CLIENT_ID)" \
		E2E_IDP_SECRET_NAME="$(E2E_IDP_SECRET_NAME)" \
		E2E_IDP_CA_SECRET="$(E2E_IDP_CA_SECRET)" \
		python3 $(CURDIR)/tests/e2e_overlay.py > "$$overlay"; \
		echo "--> values overlay written from the environment ($$(grep -c . "$$overlay") lines; values not printed)"; \
	fi; \
	cd tests/ats && uv sync --quiet && \
	KUBECONFIG="$(E2E_KUBECONFIG)" \
	ATS_CHART_PATH="$$archive" \
	ATS_CHART_VERSION=$(E2E_VERSION) \
	ATS_CLUSTER_TYPE=$(CLUSTER_TYPE) \
	ATS_OVERLAY_VALUES="$$overlay" \
	ATS_VALUES="$(VALUES)" \
	E2E_DOMAIN="$(E2E_DOMAIN)" \
	E2E_ISSUER_URL="$(E2E_ISSUER_URL)" \
	E2E_CLIENT_ID="$(E2E_CLIENT_ID)" \
	E2E_IDP_CA_SECRET="$(E2E_IDP_CA_SECRET)" \
	uv run pytest -m $(SCENARIO) --log-cli-level info -o log_cli=true

.PHONY: verify-images
verify-images: ## Assert every image reference in the rendered defaults of both charts is on gsoci.azurecr.io (giantswarm/agent-platform#575): the meta chart with its defaults and with the engine and every component on under the fleet's API groups, the connectivity chart with its defaults and with every component and model serving on — every container image of every pod template (a ClusterServingRuntime's included), every OCIRepository url, every registry / imageRegistry value the meta chart forwards, every oci:// reference and every string naming a public registry with a path; a reference whose gsoci copy is not published yet is tolerated by name (tests/verify-images.py PENDING, each with the issue that lands it) and a stale entry fails. Needs PyYAML. HELM selects the binary.
	@echo "====> $@ ($(CHART_DIR), $(CONNECTIVITY_DIR))"
	@python3 tests/verify-images.py $(CHART_DIR) $(CONNECTIVITY_DIR)
	@echo "ok: $@"

.PHONY: verify-substrate-images
verify-substrate-images: ## Assert the substrate chart's third-party images (giantswarm/agent-platform#575, #580, #654) for every Substrate release components.substrate.versionRange admits: each default of the release's images: map is a gsoci.azurecr.io reference the registry publishes (images.agentgateway a release under agentgateway.proxy.image's repository; images.awsCli the tolerated short name); the release rendered with the values the meta chart forwards, the bundled store and database on, runs no third-party image off gsoci and runs its own data plane. values.yaml's substrate.images forwards no agentgateway (the atenet data plane follows the release it was written for) and holds its other keys to the floor's defaults only — the digest (or tag) under gsoci, a key the floor knows, awsCli exactly the floor's value — so a Substrate patch that moves a default keeps main green. Network: gsoci.azurecr.io. Needs PyYAML.
	@echo "====> $@ ($(CHART_DIR))"
	@python3 -c 'import yaml' 2>/dev/null || { echo "FAIL: PyYAML is not installed (apt: python3-yaml, pip: pyyaml)"; exit 1; }
	@python3 tests/verify-substrate-images.py $(CHART_DIR)
	@echo "ok: $@"

.PHONY: verify-scenarios
verify-scenarios: ## Assert the ATS scenario inputs (tests/ats/scenarios.py) and the `make e2e` values overlay (tests/e2e_overlay.py) offline: the kind and eks defaults, every refusal naming its variable, the derived muster base URL, the base-URL --set, and the overlay's shapes.
	@echo "====> $@"
	@python3 tests/verify-scenarios.py
	@echo "the ATS scenario inputs verified."

# --- verify-all --------------------------------------------------------------
# Every offline assertion of this repository in one target, which is what CI
# runs. The list is read out of this file, so a new verify-* target reaches CI
# by existing; nothing names the set twice.
#
# Network: a few of these resolve a component chart (gsoci.azurecr.io;
# ghcr.io for the CloudNativePG chart). Some need PyYAML. Each target's own help line says so.
VERIFY_MK := $(lastword $(MAKEFILE_LIST))
# Every target this file defines, less verify-all itself and the ones another
# verify target already chains as a prerequisite.
VERIFY_ALL_DEFINED := $(sort $(shell sed -n 's/^\(verify-[a-z0-9-]*\):.*/\1/p' $(VERIFY_MK)))
VERIFY_CHAINED := $(sort $(shell sed -n 's/^verify-[a-z0-9-]*: *\(verify-.*\)$$/\1/p' $(VERIFY_MK)))
# verify-release-floors is the tag pipeline's, not a branch assertion: a branch
# renders against UNRELEASED and RENDER_AGAINST by design (giantswarm/agent-platform#624).
VERIFY_RELEASE_ONLY := verify-release-floors
VERIFY_TARGETS := $(filter-out verify-all $(VERIFY_CHAINED) $(VERIFY_RELEASE_ONLY),$(VERIFY_ALL_DEFINED))

.PHONY: verify-all
verify-all: ## Run every verify-* target of this file, the set CI runs. Some resolve a component chart over the network.
	@echo "====> $@ ($(words $(VERIFY_TARGETS)) targets)"
	@for target in $(VERIFY_TARGETS); do \
		$(MAKE) --no-print-directory $$target || { echo "FAIL: $$target"; exit 1; }; \
	done
	@echo "all $(words $(VERIFY_TARGETS)) verify targets passed."
