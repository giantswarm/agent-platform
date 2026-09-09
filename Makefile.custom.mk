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
FLEET_APIS := --api-versions kyverno.io/v1 --api-versions cilium.io/v2 --api-versions monitoring.coreos.com/v1 --api-versions gateway.networking.k8s.io/v1 --api-versions gateway.envoyproxy.io/v1alpha1
# parentRefs[0].name satisfies the all-modes ingress guard so a single guard is
# isolated under test, and the fleet's API groups are served so the fleet shape
# renders. Neither chart has subcharts anymore, so no `helm dependency build`
# and no subchart-fail quieting is needed.
VM := --set ingress.parentRefs[0].name=x $(FLEET_APIS)

# The components whose toggles gate a kyverno.io object: agentSandbox owns the
# pod-security ClusterPolicy; kagent owns none since kagent main (agents run as
# Substrate actors in gVisor worker pods — no Agent CR, per-agent Deployment or
# config Secret is left to mutate) and stays on so the render is the fleet's.
# The CNPG ImageVolume PolicyException needs postgres + a pgvector extension
# image and is asserted on its own below.
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
KYVERNO_GOLDEN := $(VM) --set components.kagent.enabled=true --set networkPolicy.flavor=kubernetes --set kagent.fluxServiceAccountName= --set muster.muster.oauth.server.enabled=false --set kagent.serviceMonitor.enabled=false --set kagent.namespaceOverride=default
# GOLDEN_REF's chart reads the same component toggle, so both sides render alike.
KYVERNO_GOLDEN_REF := $(KYVERNO_GOLDEN)
GOLDEN_REF ?= origin/main
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
	@echo "--> the default (kyverno) render carries this shape's one kyverno.io object, the agent-sandbox ClusterPolicy, and no kagent Agent mutation"
	@helm template t $(CONNECTIVITY_DIR) $(KYVERNO_ALL) >/tmp/vm-pe-kyverno.out 2>&1 || { cat /tmp/vm-pe-kyverno.out; exit 1; }
	@if [ "$$(grep -c '^apiVersion: kyverno.io/' /tmp/vm-pe-kyverno.out)" != "1" ]; then \
		echo "FAIL: expected 1 kyverno.io object, got $$(grep -c '^apiVersion: kyverno.io/' /tmp/vm-pe-kyverno.out)"; exit 1; \
	elif grep -qE 'kagent-declarative|kagent-srt-settings|kagent.dev/v1alpha2' /tmp/vm-pe-kyverno.out; then \
		echo "FAIL: a kagent v1alpha2 Agent mutation is back (no Agent CR, per-agent Deployment or config Secret exists on kagent main)"; exit 1; \
	else echo "ok: 1 kyverno.io object"; fi
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
	@echo "--> an exception naming no rule must fail (it would match nothing)"
	@if helm template t $(CONNECTIVITY_DIR) $(KYVERNO_ALL) --set postgres.enabled=true --set postgres.vector.enabled=true --set postgres.vector.extensionImage.reference=$(PGVECTOR_IMG) --set 'kyvernoPolicies.volumeTypesRuleNames[0]=' >/tmp/vm-pe-rule.out 2>&1; then \
		echo "FAIL: the empty-rule guard did not fire"; exit 1; \
	elif ! grep -q "volumeTypesRuleNames must name at least one non-empty rule" /tmp/vm-pe-rule.out; then \
		echo "FAIL: the empty-rule guard failed for the wrong reason"; cat /tmp/vm-pe-rule.out; exit 1; \
	else echo "ok: empty-rule guard"; fi
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
		helm template t $(CONNECTIVITY_DIR) $(KYVERNO_GOLDEN) >$$out/head 2>&1 \
			|| { echo "FAIL: the working-tree render failed"; cat $$out/head; exit 1; }; \
		if diff -u $$out/golden $$out/head; then echo "ok: default render unchanged"; \
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
	@echo "--> the default render keeps the ServiceMonitor and the CNPG PodMonitor (fleet behavior)"
	@helm template t $(CONNECTIVITY_DIR) $(VM) --set components.kagent.enabled=true --set postgres.enabled=true >/tmp/vg-mon-on.out 2>&1 || { cat /tmp/vg-mon-on.out; exit 1; }
	@grep -q 'kind: ServiceMonitor' /tmp/vg-mon-on.out || { echo "FAIL: default render lost the kagent ServiceMonitor"; exit 1; }
	@grep -q 'enablePodMonitor: true' /tmp/vg-mon-on.out || { echo "FAIL: default render lost the CNPG PodMonitor"; exit 1; }
	@grep -q 'observability.giantswarm.io/tenant: giantswarm' /tmp/vg-mon-on.out || { echo "FAIL: default render lost the tenant label"; exit 1; }
	@grep -q 'helm.sh/resource-policy: keep' /tmp/vg-mon-on.out || { echo "FAIL: the CNPG Cluster lost helm.sh/resource-policy: keep"; exit 1; }
	@echo "ok: fleet monitor defaults + CNPG keep"
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
	@helm template t $(CONNECTIVITY_DIR) $(EDGE_VM) --set components.kagent.enabled=true --set kagent.controllerRoute.enabled=true >/tmp/vg-edge.out 2>&1 || { cat /tmp/vg-edge.out; exit 1; }
	@grep -q 'hostname: "\*.ci.example.com"' /tmp/vg-edge.out || { echo "FAIL: edge HTTPS listener missing"; exit 1; }
	@grep -q 'sectionName: https' /tmp/vg-edge.out || { echo "FAIL: public routes not pinned to the HTTPS listener (plaintext 8080 would ride the LB)"; exit 1; }
	@grep -A2 '^  service:' /tmp/vg-edge.out | grep -q '^      type: LoadBalancer' || { echo "FAIL: edge data-plane Service type is not nested at spec.service.spec.type (the CRD prunes a bare spec.service.type)"; exit 1; }
	@if grep -q 'name: kagent-controller-public' /tmp/vg-edge.out; then echo "FAIL: layer-1 kagent route rendered with the edge as data plane"; exit 1; fi
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
INLINE_SECRET_PATHS := kagent.providers.anthropic.apiKey kagent.oauth2-proxy.config.clientSecret kagent.oauth2-proxy.config.cookieSecret muster.muster.oauth.server.dex.clientSecret muster.muster.oauth.server.registrationToken muster.muster.oauth.server.encryptionKeyValue muster.muster.oauth.server.storage.valkey.password valkey.valkey.auth.aclUsers.default.password klausGateway.slack.botToken klausGateway.slack.signingSecret klausGateway.obo.stateKey klausGateway.obo.storeKey model-manager.oauth.dex.clientSecret agent-manager.oauth.dex.clientSecret
INLINE_SECRET_SETS := --set kagent.providers.anthropic.apiKey=LEAK-CANARY-VALUE --set kagent.oauth2-proxy.config.clientSecret=LEAK-CANARY-VALUE --set kagent.oauth2-proxy.config.cookieSecret=LEAK-CANARY-VALUE --set muster.muster.oauth.server.dex.clientSecret=LEAK-CANARY-VALUE --set muster.muster.oauth.server.registrationToken=LEAK-CANARY-VALUE --set muster.muster.oauth.server.encryptionKeyValue=LEAK-CANARY-VALUE --set muster.muster.oauth.server.storage.valkey.password=LEAK-CANARY-VALUE --set valkey.valkey.auth.aclUsers.default.password=LEAK-CANARY-VALUE --set klausGateway.slack.botToken=LEAK-CANARY-VALUE --set klausGateway.slack.signingSecret=LEAK-CANARY-VALUE --set klausGateway.obo.stateKey=LEAK-CANARY-VALUE --set klausGateway.obo.storeKey=LEAK-CANARY-VALUE --set model-manager.oauth.dex.clientSecret=LEAK-CANARY-VALUE --set agent-manager.oauth.dex.clientSecret=LEAK-CANARY-VALUE
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
	@python3 -c 'import re,sys; docs=open("/tmp/ap-kag.out").read().split("\n---\n"); hr=[d for d in docs if "kind: HelmRelease" in d and re.search(r"^  name: kagent$$", d, re.M)]; sys.exit("FAIL: kagent HelmRelease not rendered") if not hr else None; sys.exit("FAIL: kagent install waits for the controller (install.disableWait missing)") if "disableWait: true" not in hr[0] else None; others=[d for d in docs if "kind: HelmRelease" in d and "disableWait: true" in d and not re.search(r"^  name: kagent$$", d, re.M)]; sys.exit("FAIL: disableWait leaked to "+", ".join(re.search(r"^  name: (.*)$$", d, re.M).group(1) for d in others)) if others else print("ok: kagent install.disableWait, no other component")'
	@echo "ok: flux render"
	@echo "--> agentgateway 2.x wiring: forwarded values are FLAT and carry no umbrella-only key"
	@./tests/verify-agentgateway-wiring.py /tmp/ap-flux.out
	@grep -q 'semver: "2.x"' /tmp/ap-flux.out || { echo "FAIL: agentgateway range is not 2.x (the flattened chart line)"; exit 1; }
	@echo "ok: agentgateway 2.x wiring"
	@echo "--> kagent 0.2.x wiring: forwarded values are FLAT and carry no umbrella-only key"
	@./tests/verify-kagent-wiring.py /tmp/ap-flux.out
	@grep -q 'semver: "0.2.x"' /tmp/ap-flux.out || { echo "FAIL: kagent range is not 0.2.x (the flattened chart line)"; exit 1; }
	@echo "ok: kagent 0.2.x wiring"
	@echo "--> PURE app-of-apps (engine off): root emits ONLY OCIRepository + HelmRelease (no raw CRs)"
	@if grep -E '^kind:' /tmp/ap-flux.out | grep -vqE '^kind: (OCIRepository|HelmRelease)$$'; then \
		echo "FAIL: root rendered a non-app-of-apps kind:"; grep -E '^kind:' /tmp/ap-flux.out | grep -vE '^kind: (OCIRepository|HelmRelease)$$'; exit 1; \
	else echo "ok: pure renderer (only OCIRepository/HelmRelease)"; fi
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
	@grep -q 'semver: "5.12.0"' /tmp/ap-bom.out || { echo "FAIL: BOM did not pin muster to 5.12.0"; exit 1; }
	@if grep -qE 'semver: "[0-9]+\.x"' /tmp/ap-bom.out; then echo "FAIL: BOM still contains an unpinned x-range"; exit 1; fi
	@echo "ok: customer BOM pinned"
	@echo "--> gitops.namespace routes the Flux CRs to an exempt ns, targetNamespace routes workloads"
	@helm template t $(CHART_DIR) -f $(CHART_DIR)/ci/ci-values.yaml $(ENGINE_OFF) --set gitops.namespace=flux-giantswarm --set gitops.targetNamespace=agent-platform >/tmp/ap-ns.out 2>&1 || { cat /tmp/ap-ns.out; exit 1; }
	@if grep -E '^  namespace:' /tmp/ap-ns.out | grep -vq 'flux-giantswarm'; then \
		echo "FAIL: a rendered CR is not in the gitops.namespace"; grep -E '^  namespace:' /tmp/ap-ns.out | grep -v 'flux-giantswarm'; exit 1; \
	else echo "ok: all CRs in flux-giantswarm"; fi
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
verify-llm-routing: ## Assert the llmRouting toggle: off renders nothing, on renders the listener + routing + metrics, and the guards fire.
	@echo "====> $@ ($(CONNECTIVITY_DIR))"
	@echo "--> off (default): no LLM listener, route, backend, policy or price ConfigMap"
	@helm template t $(CONNECTIVITY_DIR) $(VM) --set ingress.mode=agentgateway-muster --set components.agentgateway.enabled=true --set components.kagent.enabled=true >/tmp/vl-off.out 2>&1 || { cat /tmp/vl-off.out; exit 1; }
	@for pattern in 'AgentgatewayBackend' 'AgentgatewayPolicy' 'sectionName: llm' 'model-catalog' 'modelCatalog'; do \
		if grep -q "$$pattern" /tmp/vl-off.out; then echo "FAIL: llmRouting is off but the render still contains $$pattern"; exit 1; fi; \
	done
	@if grep -qE '^      port: 8081$$' /tmp/vl-off.out; then echo "FAIL: the LLM listener renders with llmRouting off"; exit 1; fi
	@echo "ok: nothing renders"
	@echo "--> off, agentgateway on: the data-plane PodMonitor still renders (the MCP path is scraped too)"
	@grep -q 'kind: PodMonitor' /tmp/vl-off.out || { echo "FAIL: the data-plane PodMonitor is gated on llmRouting; the MCP path would never be scraped"; exit 1; }
	@grep -A12 'agentgateway-dataplane' /tmp/vl-off.out | grep -q 'observability.giantswarm.io/tenant: giantswarm' || { echo "FAIL: the PodMonitor lost the tenant label; alloy-metrics would ignore it"; exit 1; }
	@grep -A32 'name: agent-platform-connectivity-dataplane$$' /tmp/vl-off.out | grep -q '"15020"' || { echo "FAIL: the data-plane policy does not admit the scrape port; the PodMonitor target reports up=0 and every metric is lost"; exit 1; }
	@echo "ok: PodMonitor + tenant label + scrape port"
	@echo "--> on: the llm listener, the pinned route, the AI backend, the Gateway policy and the price catalog"
	@helm template t $(CONNECTIVITY_DIR) $(LLM_VM) --set components.kagent.enabled=true >/tmp/vl-on.out 2>&1 || { cat /tmp/vl-on.out; exit 1; }
	@grep -q 'name: llm' /tmp/vl-on.out || { echo "FAIL: no llm listener on the Gateway"; exit 1; }
	@grep -q 'sectionName: llm' /tmp/vl-on.out || { echo "FAIL: the LLM route is not pinned to its listener; in edge mode it would attach to the public HTTPS listener"; exit 1; }
	@grep -A4 'kind: AgentgatewayBackend' /tmp/vl-on.out >/dev/null || { echo "FAIL: no AgentgatewayBackend"; exit 1; }
	@grep -A3 '^  ai:' /tmp/vl-on.out | grep -q 'anthropic: {}' || { echo "FAIL: the AI backend is not the Anthropic provider with its defaults"; exit 1; }
	@if grep -q 'policies:' /tmp/vl-on.out; then echo "FAIL: the AI backend carries backend policies; the gateway must hold no credential"; exit 1; fi
	@echo "ok: listener + pinned route + credential-free AI backend"
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
	@echo "--> the Gateway policy carries the route-type map INCLUDING the wildcard, and both metric labels"
	@grep -q '"/v1/messages": Messages' /tmp/vl-on.out || { echo "FAIL: no Messages route type; the gateway would parse Anthropic bodies as OpenAI Completions"; exit 1; }
	@grep -q '"/v1/messages/count_tokens": AnthropicTokenCount' /tmp/vl-on.out || { echo "FAIL: no AnthropicTokenCount route type"; exit 1; }
	@grep -q '"\*": Passthrough' /tmp/vl-on.out || { echo "FAIL: no wildcard route type; any other path would fall back to Completions parsing"; exit 1; }
	@grep -q 'expression: source.unverifiedWorkload.serviceAccount' /tmp/vl-on.out || { echo "FAIL: no agent attribution label"; exit 1; }
	@grep -q 'expression: source.unverifiedWorkload.namespace' /tmp/vl-on.out || { echo "FAIL: no agent_namespace attribution label"; exit 1; }
	@if [ "$$(grep -c 'kind: AgentgatewayPolicy' /tmp/vl-on.out)" != "1" ]; then \
		echo "FAIL: more than one AgentgatewayPolicy targets the Gateway; the loser is silently dropped"; exit 1; \
	else echo "ok: one policy, route-type map + metric labels"; fi
	@echo "--> the price ConfigMap renders and the AgentgatewayParameters references it"
	@grep -q 'name: t-model-catalog' /tmp/vl-on.out || { echo "FAIL: no model-price ConfigMap; every cost lookup would report NoCatalog"; exit 1; }
	@grep -A4 '^  modelCatalog:' /tmp/vl-on.out | grep -q 'key: catalog.json' || { echo "FAIL: AgentgatewayParameters does not reference the price ConfigMap"; exit 1; }
	@grep -q '"claude-sonnet-4-6"' /tmp/vl-on.out || { echo "FAIL: the platform's default model is unpriced"; exit 1; }
	@echo "ok: price catalog wired"
	@echo "--> the network policies admit the LLM port in both flavors"
	@grep -A24 'name: agent-platform-connectivity-dataplane$$' /tmp/vl-on.out | grep -q '"8081"' || { echo "FAIL: the cilium data-plane policy does not admit the LLM port"; exit 1; }
	@grep -A32 'name: agent-platform-connectivity-dataplane$$' /tmp/vl-on.out | grep -q '"15020"' || { echo "FAIL: the cilium data-plane policy does not admit the scrape port"; exit 1; }
	@grep -A40 'kagent-agent-muster-egress' /tmp/vl-on.out | grep -q 'gateway.networking.k8s.io/gateway-name: agentgateway' || { echo "FAIL: agent pods have no egress to the data plane's LLM port"; exit 1; }
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
	@grep -A3 'anthropic:' /tmp/vl-ci.out | grep -q 'baseUrl: "http://agentgateway.default.svc:8081"' || { echo "FAIL: kagent.modelConfigs[].baseUrl does not reach the rendered ModelConfig; that agent would bypass the listener"; exit 1; }
	@echo "ok: CI scenario"
	@echo "--> the meta chart forwards the cutover value to the kagent release"
	@helm template t $(CHART_DIR) -f $(CHART_DIR)/ci/ci-values.yaml --set kagent.providers.anthropic.config.baseUrl=http://agentgateway.default.svc:8081 >/tmp/vl-meta.out 2>&1 || { cat /tmp/vl-meta.out; exit 1; }
	@awk '/^kind: HelmRelease$$/{h=1} h&&/^  name: kagent$$/{f=1} f&&/^---/{exit} f' /tmp/vl-meta.out >/tmp/vl-meta-kagent.out
	@grep -A8 '^    providers:$$' /tmp/vl-meta-kagent.out | grep -q 'baseUrl: http://agentgateway.default.svc:8081' || { echo "FAIL: the cutover value never reaches the kagent HelmRelease (flat, kagent 0.2.x: providers.anthropic.config.baseUrl at the values root); the default ModelConfig would stay direct"; exit 1; }
	@echo "ok: cutover forwarded"
	@echo "All llmRouting behaviors verified."

.PHONY: verify-engine
verify-engine: ## Assert the bundled Flux engine's two shapes: engine off (pure renderer, no CRD/hook/operator/identity) and engine on (the eleven CRDs, operator, FluxInstance, agent-platform-flux on every HelmRelease, the teardown hooks). HELM selects the binary.
	@echo "====> $@ ($(CHART_DIR))"
	@python3 tests/verify-engine.py $(CHART_DIR)
	@echo "flux engine shapes verified."

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

.PHONY: verify-labels
verify-labels: ## Assert every label value stays valid at the versions the charts are installed under: helm-controller's +digest and a branch build's long prerelease, with the 63-character cut landing on each separator. HELM selects the binary.
	@echo "====> $@ ($(CHART_DIR), $(CONNECTIVITY_DIR))"
	@python3 tests/verify-labels.py $(CHART_DIR) $(CONNECTIVITY_DIR)
	@echo "label values verified."

.PHONY: verify-components
verify-components: ## Assert the roster entries of the standalone chart's extras (backstage, mcp-kubernetes, cloudnative-pg, the kserve charts): off by default, sources and ranges, CRD-before-CR dependsOn, BOM pins, the forwarded tree validates against the connectivity schema.
	@echo "====> $@ ($(CHART_DIR), $(CONNECTIVITY_DIR))"
	@python3 tests/verify-components.py $(CHART_DIR) $(CONNECTIVITY_DIR)
	@echo "component roster verified."

.PHONY: verify-components-charts
verify-components-charts: ## Pull the seven component charts (at the range's resolution and at the BOM pin) and render each with the values the meta chart forwards to it. Network: gsoci.azurecr.io, ghcr.io.
	@echo "====> $@ ($(CHART_DIR))"
	@python3 tests/verify-components-charts.py $(CHART_DIR)
	@echo "component charts accept the forwarded values."

# The two platform services the connectivity chart wires — model-manager and
# agent-manager (route + JWT policy + network policies + render-time guards). A
# valid configuration of both on the agentgateway topology, with the identity
# contract set so the OAuth guards are satisfied.
MANAGERS_ON := $(VM) --namespace agent-platform --set ingress.mode=agentgateway-muster --set components.agentgateway.enabled=true --set components.kagent.enabled=true --set components.model-manager.enabled=true --set components.agent-manager.enabled=true --set model-manager.ollama.endpoint=http://10.0.0.1:11434 --set global.domain=ci.example.com --set global.identity.issuerUrl=https://dex.ci.example.com --set global.identity.clientId=platform --set global.identity.existingSecret=platform-oauth --set gateway.jwksEgress.enabled=true
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
# and off in the golden render, so the default render is unchanged.
KAGENT_NETPOL := $(VM) --set components.kagent.enabled=true --set muster.enabled=true --set networkPolicy.flavor=cilium --set kagent.namespaceOverride=kagent
.PHONY: verify-kagent-netpol
verify-kagent-netpol: ## Assert the kagent controller/agent egress to the built-in tool server renders iff kagent.kagent-tools.enabled, in the tools namespace and port; and the oauth2-proxy ingress admits kagent.oauth2ProxyIngress.additionalPeers on the proxy port only.
	@echo "====> $@ ($(CONNECTIVITY_DIR))"
	@echo "--> kagent-tools off (the default): no tool-server egress"
	@helm template t $(CONNECTIVITY_DIR) $(KAGENT_NETPOL) >/tmp/vkn-off.out 2>&1 || { cat /tmp/vkn-off.out; exit 1; }
	@if grep -q 'kagent-tools' /tmp/vkn-off.out; then echo "FAIL: tool-server egress renders while kagent-tools is off"; grep -n 'kagent-tools' /tmp/vkn-off.out | head; exit 1; else echo "ok: inert while off"; fi
	@echo "--> kagent-tools on: controller and agent egress to the kagent-tools pods on 8084 in the kagent namespace"
	@helm template t $(CONNECTIVITY_DIR) $(KAGENT_NETPOL) --set kagent.kagent-tools.enabled=true >/tmp/vkn-on.out 2>&1 || { cat /tmp/vkn-on.out; exit 1; }
	@for n in kagent-controller-egress kagent-agent-muster-egress; do \
		awk "/^  name: agent-platform-connectivity-$$n$$/,/^---/" /tmp/vkn-on.out >/tmp/vkn-on-$$n.out; \
		grep -q 'app.kubernetes.io/name: kagent-tools' /tmp/vkn-on-$$n.out || { echo "FAIL: $$n has no egress to the kagent-tools pods"; exit 1; }; \
		grep -A1 'app.kubernetes.io/name: kagent-tools' /tmp/vkn-on-$$n.out | grep -q 'io.kubernetes.pod.namespace: kagent$$' || { echo "FAIL: $$n tool-server egress is not pinned to the kagent namespace"; exit 1; }; \
		grep -A4 'app.kubernetes.io/name: kagent-tools' /tmp/vkn-on-$$n.out | grep -q 'port: "8084"' || { echo "FAIL: $$n tool-server egress does not open port 8084"; exit 1; }; \
	done
	@echo "ok: both policies open the tool server"
	@echo "--> an explicit kagent.kagent-tools.namespaceOverride / service.ports.tools.targetPort follows into the rules"
	@helm template t $(CONNECTIVITY_DIR) $(KAGENT_NETPOL) --set kagent.kagent-tools.enabled=true --set kagent.kagent-tools.namespaceOverride=tools-ns --set kagent.kagent-tools.service.ports.tools.targetPort=9084 >/tmp/vkn-override.out 2>&1 || { cat /tmp/vkn-override.out; exit 1; }
	@[ "$$(grep -A1 'app.kubernetes.io/name: kagent-tools' /tmp/vkn-override.out | grep -c 'io.kubernetes.pod.namespace: tools-ns$$')" = "2" ] || { echo "FAIL: the tool-server egress does not follow kagent.kagent-tools.namespaceOverride"; exit 1; }
	@[ "$$(grep -A4 'app.kubernetes.io/name: kagent-tools' /tmp/vkn-override.out | grep -c 'port: "9084"')" = "2" ] || { echo "FAIL: the tool-server egress does not follow the tools targetPort"; exit 1; }
	@echo "ok: namespace and port overrides"
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

.PHONY: verify-kagent-discovery
verify-kagent-discovery: ## Assert the shared muster RemoteMCPServer opts out of controller-side tool discovery (kagent.dev/discovery=disabled) iff muster runs with OAuth on, carries no headersFrom, and the operator-defined kagent.remoteMcpServers are untouched.
	@echo "====> $@ ($(CONNECTIVITY_DIR))"
	@echo "--> muster OAuth on (the default): the muster RemoteMCPServer carries the opt-out label and no headersFrom"
	@helm template t $(CONNECTIVITY_DIR) $(KAGENT_NETPOL) --set-json 'kagent.remoteMcpServers=[{"name":"external","url":"https://external.example/mcp","tokenSecret":"external-token"}]' >/tmp/vkd-on.out 2>&1 || { cat /tmp/vkd-on.out; exit 1; }
	@awk 'BEGIN{RS="\n---\n"} /\nkind: RemoteMCPServer\n/ && /\n  name: muster\n/' /tmp/vkd-on.out >/tmp/vkd-on-muster.out
	@grep -q 'kind: RemoteMCPServer' /tmp/vkd-on.out || { echo "FAIL: no RemoteMCPServer rendered"; exit 1; }
	@grep -q '^  name: muster$$' /tmp/vkd-on-muster.out || { echo "FAIL: no muster RemoteMCPServer rendered"; cat /tmp/vkd-on.out | grep -n 'RemoteMCPServer' ; exit 1; }
	@echo "--> kagent main: v1alpha3, in the kagent namespace with the AgentTemplates that bind it, no allowedNamespaces"
	@grep -q '^apiVersion: kagent.dev/v1alpha3$$' /tmp/vkd-on-muster.out || { echo "FAIL: the muster RemoteMCPServer is not kagent.dev/v1alpha3"; cat /tmp/vkd-on-muster.out; exit 1; }
	@grep -q '^  namespace: kagent$$' /tmp/vkd-on-muster.out || { echo "FAIL: the muster RemoteMCPServer is not in the kagent namespace — an AgentTemplate binds a same-namespace server only"; cat /tmp/vkd-on-muster.out; exit 1; }
	@if grep -q 'allowedNamespaces' /tmp/vkd-on-muster.out; then echo "FAIL: allowedNamespaces is back on the muster RemoteMCPServer (a v1alpha2 cross-namespace grant, inert on main)"; cat /tmp/vkd-on-muster.out; exit 1; fi
	@if grep -q '^apiVersion: kagent.dev/v1alpha2$$' /tmp/vkd-on.out; then echo "FAIL: a kagent.dev/v1alpha2 object renders"; grep -n 'v1alpha2' /tmp/vkd-on.out; exit 1; fi
	@echo "ok: v1alpha3 in the kagent namespace"
	@grep -q '^    kagent.dev/discovery: disabled$$' /tmp/vkd-on-muster.out || { echo "FAIL: the muster RemoteMCPServer does not opt out of controller-side discovery while muster OAuth is on"; cat /tmp/vkd-on-muster.out; exit 1; }
	@if grep -q 'headersFrom' /tmp/vkd-on-muster.out; then echo "FAIL: the muster RemoteMCPServer carries headersFrom — a static header there overrides the propagated caller token in every agent"; cat /tmp/vkd-on-muster.out; exit 1; fi
	@echo "ok: muster opts out, no static header"
	@echo "--> operator-defined kagent.remoteMcpServers: no opt-out label, tokenSecret still renders headersFrom"
	@awk "/^kind: RemoteMCPServer$$/,/^---/" /tmp/vkd-on.out | awk "/^  name: \"external\"$$/,/^---/" >/tmp/vkd-on-external.out
	@grep -q '^  name: "external"$$' /tmp/vkd-on-external.out || { echo "FAIL: the operator-defined RemoteMCPServer did not render"; grep -n 'name:' /tmp/vkd-on.out | grep -i remote; exit 1; }
	@if grep -q 'kagent.dev/discovery' /tmp/vkd-on-external.out; then echo "FAIL: the opt-out label leaked onto an operator-defined RemoteMCPServer"; cat /tmp/vkd-on-external.out; exit 1; fi
	@grep -q 'headersFrom' /tmp/vkd-on-external.out || { echo "FAIL: tokenSecret no longer renders headersFrom on an operator-defined RemoteMCPServer"; cat /tmp/vkd-on-external.out; exit 1; }
	@echo "ok: operator-defined servers untouched"
	@echo "--> muster OAuth off: the controller can list tools anonymously, no opt-out label"
	@helm template t $(CONNECTIVITY_DIR) $(KAGENT_NETPOL) --set muster.muster.oauth.server.enabled=false >/tmp/vkd-off.out 2>&1 || { cat /tmp/vkd-off.out; exit 1; }
	@awk "/^kind: RemoteMCPServer$$/,/^---/" /tmp/vkd-off.out | awk "/^  name: muster$$/,/^---/" >/tmp/vkd-off-muster.out
	@grep -q '^  name: muster$$' /tmp/vkd-off-muster.out || { echo "FAIL: no muster RemoteMCPServer rendered with OAuth off"; exit 1; }
	@if grep -q 'kagent.dev/discovery' /tmp/vkd-off-muster.out; then echo "FAIL: the opt-out label renders while muster OAuth is off"; cat /tmp/vkd-off-muster.out; exit 1; fi
	@echo "ok: no label with OAuth off"
	@echo "--> kagent off: no RemoteMCPServer at all"
	@if helm template t $(CONNECTIVITY_DIR) $(VM) --set muster.enabled=true 2>&1 | grep -q 'kind: RemoteMCPServer'; then echo "FAIL: a RemoteMCPServer renders while kagent is off"; exit 1; else echo "ok: inert while kagent is off"; fi

# Two Harnesses in the lab's shape: the Go ADK runtime with the token-propagation
# env, and the Claude adapter with an explicit selector. The digest is a
# placeholder; the assertions read the shape, not the image.
HARNESS_DIGEST := sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef
KAGENT_HARNESSES := $(KAGENT_NETPOL) --set-json 'kagent.harnesses=[{"name":"kagent","type":"kagent","image":"registry.example/kagent/golang-adk@$(HARNESS_DIGEST)","env":[{"name":"KAGENT_PROPAGATE_TOKEN","value":"true"}],"workerPool":"kagent-default","snapshotLocation":"s3://ate-snapshots/kagent"},{"name":"claude","type":"claude","image":"registry.example/kagent/claude-harness@$(HARNESS_DIGEST)","workerPool":"kagent-default","snapshotLocation":"s3://ate-snapshots/claude","selector":{"team":"x"}}]'
.PHONY: verify-kagent-harnesses
verify-kagent-harnesses: ## Assert kagent.harnesses[] renders kagent.dev/v1alpha3 Harness objects in the kagent namespace (runtime type, digest-pinned image, env, Substrate worker pool and snapshot location, the kagent.dev/harness selector by default) and that a tag, an unknown type, a byo without command or a missing worker pool fails the render.
	@echo "====> $@ ($(CONNECTIVITY_DIR))"
	@echo "--> no harnesses (the default): no Harness object"
	@helm template t $(CONNECTIVITY_DIR) $(KAGENT_NETPOL) >/tmp/vkh-off.out 2>&1 || { cat /tmp/vkh-off.out; exit 1; }
	@if grep -q '^kind: Harness$$' /tmp/vkh-off.out; then echo "FAIL: a Harness renders with kagent.harnesses empty"; exit 1; else echo "ok: inert while empty"; fi
	@echo "--> two entries: two v1alpha3 Harnesses in the kagent namespace, on the kagent-default WorkerPool"
	@helm template t $(CONNECTIVITY_DIR) $(KAGENT_HARNESSES) >/tmp/vkh-on.out 2>&1 || { cat /tmp/vkh-on.out; exit 1; }
	@[ "$$(grep -c '^kind: Harness$$' /tmp/vkh-on.out)" = "2" ] || { echo "FAIL: expected 2 Harness objects, got $$(grep -c '^kind: Harness$$' /tmp/vkh-on.out)"; exit 1; }
	@awk 'BEGIN{RS="\n---\n"} /\nkind: Harness\n/ && /\n  name: "kagent"\n/' /tmp/vkh-on.out >/tmp/vkh-kagent.out
	@awk 'BEGIN{RS="\n---\n"} /\nkind: Harness\n/ && /\n  name: "claude"\n/' /tmp/vkh-on.out >/tmp/vkh-claude.out
	@[ -s /tmp/vkh-kagent.out ] && [ -s /tmp/vkh-claude.out ] || { echo "FAIL: the Harnesses are not named after their entries"; grep -n 'name:' /tmp/vkh-on.out; exit 1; }
	@for f in /tmp/vkh-kagent.out /tmp/vkh-claude.out; do \
		grep -q '^apiVersion: kagent.dev/v1alpha3$$' $$f || { echo "FAIL: $$f is not kagent.dev/v1alpha3"; cat $$f; exit 1; }; \
		grep -q '^  namespace: kagent$$' $$f || { echo "FAIL: $$f is not in the kagent namespace (its WorkerPool and AgentTemplates resolve there only)"; cat $$f; exit 1; }; \
		grep -A1 '^    workerPoolRef:$$' $$f | grep -q 'name: "kagent-default"' || { echo "FAIL: $$f lacks substrate.workerPoolRef.name"; cat $$f; exit 1; }; \
	done
	@echo "--> the kagent entry: the kagent runtime, the digest-pinned image, the env verbatim, the snapshot location, the default kagent.dev/harness selector, no command/args"
	@grep -q '^  kagent: {}$$' /tmp/vkh-kagent.out || { echo "FAIL: type kagent did not render spec.kagent: {}"; cat /tmp/vkh-kagent.out; exit 1; }
	@grep -q '^    image: "registry.example/kagent/golang-adk@$(HARNESS_DIGEST)"$$' /tmp/vkh-kagent.out || { echo "FAIL: workload.image is not the digest-pinned reference"; cat /tmp/vkh-kagent.out; exit 1; }
	@grep -A1 'name: KAGENT_PROPAGATE_TOKEN' /tmp/vkh-kagent.out | grep -q 'value: "true"' || { echo "FAIL: env is not passed through verbatim"; cat /tmp/vkh-kagent.out; exit 1; }
	@grep -A1 '^    snapshotPolicy:$$' /tmp/vkh-kagent.out | grep -q 'location: "s3://ate-snapshots/kagent"' || { echo "FAIL: substrate.snapshotPolicy.location lost"; cat /tmp/vkh-kagent.out; exit 1; }
	@grep -A1 '^      matchLabels:$$' /tmp/vkh-kagent.out | grep -q '^        kagent.dev/harness: kagent$$' || { echo "FAIL: the default selector is not kagent.dev/harness: <name>"; cat /tmp/vkh-kagent.out; exit 1; }
	@if grep -qE '^    (command|args):' /tmp/vkh-kagent.out; then echo "FAIL: command/args render without being set"; cat /tmp/vkh-kagent.out; exit 1; fi
	@echo "ok: kagent Harness"
	@echo "--> the claude entry: the claude runtime, no env, the explicit selector replaces the default"
	@grep -q '^  claude: {}$$' /tmp/vkh-claude.out || { echo "FAIL: type claude did not render spec.claude: {}"; cat /tmp/vkh-claude.out; exit 1; }
	@if grep -q '^  env:' /tmp/vkh-claude.out; then echo "FAIL: env renders without being set"; cat /tmp/vkh-claude.out; exit 1; fi
	@grep -A1 '^      matchLabels:$$' /tmp/vkh-claude.out | grep -q '^        team: x$$' || { echo "FAIL: an explicit selector is not rendered"; cat /tmp/vkh-claude.out; exit 1; }
	@if grep -q 'kagent.dev/harness' /tmp/vkh-claude.out; then echo "FAIL: the default selector leaks onto an entry with its own"; cat /tmp/vkh-claude.out; exit 1; fi
	@echo "ok: claude Harness"
	@echo "--> a byo entry renders spec.byo and its command"
	@helm template t $(CONNECTIVITY_DIR) $(KAGENT_NETPOL) --set-json 'kagent.harnesses=[{"name":"x","type":"byo","image":"registry.example/x@$(HARNESS_DIGEST)","command":["/run"],"args":["--a2a"],"workerPool":"p","snapshotLocation":"s3://b/x"}]' >/tmp/vkh-byo-ok.out 2>&1 || { cat /tmp/vkh-byo-ok.out; exit 1; }
	@grep -q '^  byo: {}$$' /tmp/vkh-byo-ok.out && grep -A1 '^    command:$$' /tmp/vkh-byo-ok.out | grep -q -- '- /run' && grep -A1 '^    args:$$' /tmp/vkh-byo-ok.out | grep -q -- '- --a2a' || { echo "FAIL: the byo Harness lost its type, command or args"; cat /tmp/vkh-byo-ok.out; exit 1; }
	@echo "ok: byo Harness"
	@echo "--> guards: a tag instead of a digest, an unknown type, byo without command, a missing worker pool"
	@if helm template t $(CONNECTIVITY_DIR) $(KAGENT_NETPOL) --set-json 'kagent.harnesses=[{"name":"x","type":"kagent","image":"registry.example/x:v1","workerPool":"p","snapshotLocation":"s3://b/x"}]' >/tmp/vkh-tag.out 2>&1; then \
		echo "FAIL: a tagged Harness image was accepted (the CRD would reject it at admission)"; exit 1; \
	elif ! grep -q 'Harness images are digest-pinned' /tmp/vkh-tag.out; then echo "FAIL: the digest guard failed for the wrong reason"; cat /tmp/vkh-tag.out; exit 1; fi
	@if helm template t $(CONNECTIVITY_DIR) $(KAGENT_NETPOL) --set-json 'kagent.harnesses=[{"name":"x","type":"python","image":"registry.example/x@$(HARNESS_DIGEST)","workerPool":"p","snapshotLocation":"s3://b/x"}]' >/tmp/vkh-type.out 2>&1; then \
		echo "FAIL: an unknown Harness type was accepted"; exit 1; \
	elif ! grep -q 'is not one of kagent, claude, codex, byo' /tmp/vkh-type.out; then echo "FAIL: the type guard failed for the wrong reason"; cat /tmp/vkh-type.out; exit 1; fi
	@if helm template t $(CONNECTIVITY_DIR) $(KAGENT_NETPOL) --set-json 'kagent.harnesses=[{"name":"x","type":"byo","image":"registry.example/x@$(HARNESS_DIGEST)","workerPool":"p","snapshotLocation":"s3://b/x"}]' >/tmp/vkh-byo.out 2>&1; then \
		echo "FAIL: a byo Harness without command was accepted"; exit 1; \
	elif ! grep -q 'byo Harness must set command' /tmp/vkh-byo.out; then echo "FAIL: the byo guard failed for the wrong reason"; cat /tmp/vkh-byo.out; exit 1; fi
	@if helm template t $(CONNECTIVITY_DIR) $(KAGENT_NETPOL) --set-json 'kagent.harnesses=[{"name":"x","type":"kagent","image":"registry.example/x@$(HARNESS_DIGEST)","snapshotLocation":"s3://b/x"}]' >/tmp/vkh-pool.out 2>&1; then \
		echo "FAIL: a Harness without workerPool was accepted"; exit 1; \
	elif ! grep -q 'kagent.harnesses\[x\].workerPool is required' /tmp/vkh-pool.out; then echo "FAIL: the workerPool guard failed for the wrong reason"; cat /tmp/vkh-pool.out; exit 1; fi
	@echo "ok: guards"
	@echo "--> kagent off: no Harness at all"
	@if helm template t $(CONNECTIVITY_DIR) $(VM) --set muster.enabled=true --set-json 'kagent.harnesses=[{"name":"x","type":"kagent","image":"registry.example/x@$(HARNESS_DIGEST)","workerPool":"p","snapshotLocation":"s3://b/x"}]' 2>&1 | grep -q '^kind: Harness$$'; then echo "FAIL: a Harness renders while kagent is off"; exit 1; else echo "ok: inert while kagent is off"; fi

.PHONY: verify-managers
verify-managers: ## Assert the model-manager / agent-manager wiring (routes, JWT policies, network policies in both flavors) and its guards.
	@echo "====> $@ ($(CONNECTIVITY_DIR))"
	@echo "--> both components off (the default) render nothing of theirs"
	@helm template t $(CONNECTIVITY_DIR) $(VM) --set components.kagent.enabled=true >/tmp/vmg-off.out 2>&1 || { cat /tmp/vmg-off.out; exit 1; }
	@if grep -qE 'model-manager|agent-manager' /tmp/vmg-off.out; then echo "FAIL: model-manager / agent-manager objects render while the components are off"; grep -nE 'model-manager|agent-manager' /tmp/vmg-off.out | head; exit 1; else echo "ok: inert while off"; fi
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
	$(call managers_must_fail,ollama endpoint required,$(MANAGERS_MIN) --set components.model-manager.enabled=true,model-manager.ollama.endpoint is empty)
	$(call managers_must_fail,backend enum,$(MANAGERS_MIN) --set components.model-manager.enabled=true --set model-manager.backend=bogus,must be one of: ollama)
	$(call managers_must_fail,lemonade endpoint required,$(MANAGERS_MIN) --set components.model-manager.enabled=true --set model-manager.backend=lemonade,model-manager.lemonade.endpoint is empty)
	$(call managers_must_fail,lemonade endpoint must be a URL,$(MANAGERS_MIN) --set components.model-manager.enabled=true --set model-manager.backend=lemonade --set model-manager.lemonade.endpoint=172.21.0.1:13305,must be an http(s) URL)
	$(call managers_must_fail,kserve API required,$(MANAGERS_MIN) --set components.model-manager.enabled=true --set model-manager.backend=kserve,serving.kserve.io/v1beta1 API)
	$(call managers_must_fail,backends list name enum,$(MANAGERS_MIN) --set components.model-manager.enabled=true --set model-manager.ollama.endpoint=http://10.0.0.1:11434 --set 'model-manager.backends[0]=ollama' --set 'model-manager.backends[1]=bogus',must be one of: ollama)
	$(call managers_must_fail,backends list duplicate,$(MANAGERS_MIN) --set components.model-manager.enabled=true --set model-manager.ollama.endpoint=http://10.0.0.1:11434 --set 'model-manager.backends[0]=ollama' --set 'model-manager.backends[1]=ollama',lists a driver twice)
	$(call managers_must_fail,backends list lemonade endpoint required,$(MANAGERS_MIN) --set components.model-manager.enabled=true --set model-manager.ollama.endpoint=http://10.0.0.1:11434 --set 'model-manager.backends[0]=ollama' --set 'model-manager.backends[1]=lemonade',model-manager.lemonade.endpoint is empty)
	$(call managers_must_fail,backends list ollama endpoint required,$(MANAGERS_MIN) --set components.model-manager.enabled=true --set 'model-manager.backends[0]=lemonade' --set 'model-manager.backends[1]=ollama' --set model-manager.lemonade.endpoint=http://10.0.0.2:13305,model-manager.ollama.endpoint is empty)
	$(call managers_must_fail,backends list kserve API required,$(MANAGERS_MIN) --set components.model-manager.enabled=true --set model-manager.ollama.endpoint=http://10.0.0.1:11434 --set 'model-manager.backends[0]=ollama' --set 'model-manager.backends[1]=kserve',serving.kserve.io/v1beta1 API)
	$(call managers_must_pass,backends list kserve API present,$(MANAGERS_MIN) --set components.model-manager.enabled=true --set model-manager.ollama.endpoint=http://10.0.0.1:11434 --set 'model-manager.backends[0]=ollama' --set 'model-manager.backends[1]=kserve' --api-versions serving.kserve.io/v1beta1)
	$(call managers_must_pass,kserve API present,$(MANAGERS_MIN) --set components.model-manager.enabled=true --set model-manager.backend=kserve --api-versions serving.kserve.io/v1beta1)
	$(call managers_must_fail,model-manager wiring needs kagent,$(VM) --set components.model-manager.enabled=true --set model-manager.ollama.endpoint=http://10.0.0.1:11434 --set global.identity.issuerUrl=https://dex.ci.example.com --set global.identity.clientId=platform --set global.identity.existingSecret=s --set global.domain=ci.example.com,model-manager wires kagent ModelConfigs but components.kagent.enabled is false)
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
	@for case in "kagent.controllerRoute:--set components.kagent.enabled=true --set kagent.controllerRoute.enabled=true" \
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
	@helm template t $(CONNECTIVITY_DIR) $(MANAGERS_MIN) --set components.model-manager.enabled=true --set model-manager.backend=kserve --set components.kserve-resources.enabled=true >/dev/null 2>&1 || { echo "FAIL: the kserve API guard must defer to the bundled kserve-resources component"; exit 1; }
	@helm template t $(CONNECTIVITY_DIR) $(MANAGERS_MIN) --set components.model-manager.enabled=true --set model-manager.backend=kserve --set components.modelServing.enabled=true --set modelServing.kserve.requireApi=false >/dev/null 2>&1 || { echo "FAIL: the kserve API guard must defer to the modelServing component (its own prerequisite check skipped here to isolate the deferral)"; exit 1; }
	@if helm template t $(CONNECTIVITY_DIR) $(MANAGERS_MIN) --set components.model-manager.enabled=true --set model-manager.backend=kserve --set components.kserve-resources.enabled=false >/tmp/vid-kserve.out 2>&1; then echo "FAIL: a kserve-resources component that is OFF must not satisfy the kserve API guard"; exit 1; fi
	@grep -q 'serving.kserve.io/v1beta1 API' /tmp/vid-kserve.out || { echo "FAIL: the kserve guard failed for the wrong reason"; cat /tmp/vid-kserve.out; exit 1; }
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
	@echo "ok: $@"

# The muster chart version the platform toolset presets need: the first with the
# `label:` preset rule (muster#1168). It is the floor of
# components.muster.versionRange; a muster before it refuses to start on the
# presets. The chart is pulled anonymously from gsoci to render its ConfigMap
# with the values the meta chart forwards, so the check reads the real schema
# and template of that version, not a copy.
PRESETS_MUSTER_VERSION := 5.12.0
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
# (components.modelServing, a feature switch with no chart, on the kserve-crd +
# kserve-resources components) and the KServe controllers' guards and network
# policies. The quick-start inputs Backstage takes by design: global.domain,
# global.identity and a public Gateway for its route.
WIRING_QUICKSTART := --set global.domain=ci.example.com --set global.identity.issuerUrl=https://dex.ci.example.com --set global.identity.clientId=agent-platform --set global.identity.existingSecret=agent-platform-idp --set 'global.gatewayApi.parentRefs[0].name=giantswarm-default' --set 'global.gatewayApi.parentRefs[0].namespace=envoy-gateway-system'
WIRING_BACKSTAGE := $(VM) --namespace agent-platform $(WIRING_QUICKSTART) --set components.backstage.enabled=true
WIRING_SERVING := $(VM) --namespace agent-platform --set components.modelServing.enabled=true --set components.kserve-crd.enabled=true --set components.kserve-resources.enabled=true
# The fleet-shape render with every toggle of this slice off: byte-identical to origin/main's.
WIRING_OFF := $(VM) --namespace agent-platform --set components.kagent.enabled=true

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
	@helm template t $(CONNECTIVITY_DIR) $(WIRING_BACKSTAGE) --set components.kagent.enabled=true --set kagent.controllerRoute.enabled=true --set ingress.mode=agentgateway-muster --set components.agentgateway.enabled=true --set components.model-manager.enabled=true --set model-manager.ollama.endpoint=http://10.0.0.1:11434 --set modelManager.route.enabled=true --set gateway.jwksEgress.enabled=true >/tmp/vw-bs.out 2>&1 || { cat /tmp/vw-bs.out; exit 1; }
	@awk '/^kind: ConfigMap$$/,/^---/' /tmp/vw-bs.out | awk '/name: agent-platform-backstage-app-config$$/,/^---/' >/tmp/vw-bs-cm.out
	@[ -s /tmp/vw-bs-cm.out ] || { echo "FAIL: no ConfigMap agent-platform-backstage-app-config (the backstage: block's extraAppConfig mounts exactly this name)"; exit 1; }
	@for pattern in 'baseUrl: https://backstage.ci.example.com' 'metadataUrl: https://dex.ci.example.com/.well-known/openid-configuration' 'clientId: agent-platform' 'url: https://muster.ci.example.com/mcp' 'baseDomain: ci.example.com' '^        agent-platform:$$' 'name: agent-platform$$' 'fluxServiceAccountName: kagent-flux' 'apiBaseUrl: https://agentgateway.ci.example.com/kagent$$' 'apiBaseUrl: https://agentgateway.ci.example.com/model-manager' 'https://avatars.ci.example.com' 'repositories:' 'templates/agent-deployment/template.yaml' 'rootRedirect: /agent-platform'; do \
		grep -q -- "$$pattern" /tmp/vw-bs-cm.out || { echo "FAIL: the Backstage app-config lacks $$pattern"; exit 1; }; \
	done
	@if grep -q 'client: pg' /tmp/vw-bs-cm.out; then echo "FAIL: the pg database block rendered with the chart's sqlite default"; exit 1; fi
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
	@echo "--> modelServing on without the kserve components (and no serving API) fails, naming the toggles; requireApi=false and a served API pass"
	@if helm template t $(CONNECTIVITY_DIR) $(VM) --set components.modelServing.enabled=true >/tmp/vw-ms-guard.out 2>&1; then \
		echo "FAIL: modelServing rendered without the KServe control plane"; exit 1; \
	elif ! grep -q "turn on components.kserve-crd and components.kserve-resources" /tmp/vw-ms-guard.out; then \
		echo "FAIL: the modelServing guard failed for the wrong reason"; cat /tmp/vw-ms-guard.out; exit 1; \
	else echo "ok: modelServing needs the kserve components"; fi
	@helm template t $(CONNECTIVITY_DIR) $(VM) --set components.modelServing.enabled=true --set modelServing.kserve.requireApi=false >/dev/null 2>&1 || { echo "FAIL: modelServing.kserve.requireApi=false must skip the check"; exit 1; }
	@helm template t $(CONNECTIVITY_DIR) $(VM) --set components.modelServing.enabled=true --api-versions serving.kserve.io/v1alpha1 --api-versions serving.kserve.io/v1beta1 >/dev/null 2>&1 || { echo "FAIL: a cluster that serves the KServe APIs must satisfy the guard without the components"; exit 1; }
	@echo "ok: modelServing prerequisite guard"
	@echo "--> modelServing + kserve components on (fleet shape, kagent on): runtime, namespace, discovery ConfigMap, presets, chat template, cache PVC, the two Kyverno policies, the cilium policies incl. the agent egress, the kserve controller policy"
	@helm template t $(CONNECTIVITY_DIR) $(WIRING_SERVING) --set components.kagent.enabled=true --set components.kserve-llmisvc-resources.enabled=true >/tmp/vw-ms.out 2>&1 || { cat /tmp/vw-ms.out; exit 1; }
	@for pattern in 'kind: ClusterServingRuntime' '^  name: kserve-vllm$$' 'image: "docker.io/vllm/vllm-openai:' '^  name: agent-platform-model-serving$$' 'kind: PersistentVolumeClaim' '^  name: hf-cache$$' 'agent-platform-serving-preset-qwen3-8-27b' 'agent-platform-serving-preset-qwen3-14b' 'name: agent-platform-chat-template-qwen3-8-27b' '^  name: model-serving$$' 'kind: Namespace' 'name: agent-platform-connectivity-model-serving-pods' 'name: agent-platform-connectivity-model-serving-deployments' 'redirectPolicy: true' 'name: agent-platform-connectivity-model-serving-predictor$$' 'name: agent-platform-connectivity-model-serving-download$$' 'name: agent-platform-connectivity-kagent-agents-to-model-serving' 'matchName: huggingface.co' 'matchPattern: "\*"' '- remote-node' 'name: agent-platform-connectivity-kserve-controller' 'name: agent-platform-connectivity-llmisvc-controller' 'control-plane: kserve-controller-manager' 'flavor: cilium' 'preset-source: "shipped"'; do \
		grep -q -e "$$pattern" /tmp/vw-ms.out || { echo "FAIL: the model serving render lacks $$pattern"; exit 1; }; \
	done
	@[ "$$(grep -c 'agent-platform.giantswarm.io/serving-preset: "true"' /tmp/vw-ms.out)" = "7" ] || { echo "FAIL: expected the 7 shipped presets, got $$(grep -c 'agent-platform.giantswarm.io/serving-preset: "true"' /tmp/vw-ms.out)"; exit 1; }
	@if grep -q 'kind: NetworkPolicy' /tmp/vw-ms.out; then echo "FAIL: a kubernetes NetworkPolicy rendered under the cilium flavor"; exit 1; fi
	@echo "ok: model serving fleet shape"
	@echo "--> the vanilla shape (no served API groups): kubernetes policies, no Kyverno object, no Cilium object; policies.enabled=true without Kyverno fails"
	@helm template t $(CONNECTIVITY_DIR) --set 'ingress.parentRefs[0].name=x' --namespace agent-platform --set components.modelServing.enabled=true --set components.kserve-crd.enabled=true --set components.kserve-resources.enabled=true --set components.kagent.enabled=true >/tmp/vw-ms-vanilla.out 2>&1 || { cat /tmp/vw-ms-vanilla.out; exit 1; }
	@if grep -qE 'kyverno.io|cilium.io' /tmp/vw-ms-vanilla.out; then echo "FAIL: the vanilla render carries a Kyverno or Cilium object"; exit 1; fi
	@for pattern in 'name: agent-platform-connectivity-model-serving-predictor-ingress' 'name: agent-platform-connectivity-model-serving-predictor-egress' 'name: agent-platform-connectivity-model-serving-download-egress' 'name: agent-platform-connectivity-kserve-controller' 'redirectPolicy: false' 'flavor: kubernetes' 'Hugging Face: vanilla NetworkPolicy has no FQDN selector'; do \
		grep -q -e "$$pattern" /tmp/vw-ms-vanilla.out || { echo "FAIL: the vanilla model serving render lacks $$pattern"; exit 1; }; \
	done
	@if helm template t $(CONNECTIVITY_DIR) --set 'ingress.parentRefs[0].name=x' --set components.modelServing.enabled=true --set components.kserve-crd.enabled=true --set components.kserve-resources.enabled=true --set modelServing.policies.enabled=true >/tmp/vw-ms-pol.out 2>&1; then \
		echo "FAIL: modelServing.policies.enabled=true without Kyverno rendered"; exit 1; \
	elif ! grep -q "modelServing.policies.enabled is true but kyvernoPolicies.enabled resolves to false" /tmp/vw-ms-pol.out; then \
		echo "FAIL: the policies guard failed for the wrong reason"; cat /tmp/vw-ms-pol.out; exit 1; \
	else echo "ok: vanilla shape + policies guard"; fi
	@echo "--> presets: a values preset replaces a shipped one, an existing claim drops the PVC, shippedPresets.enabled=false drops the set, a bad preset fails"
	@helm template t $(CONNECTIVITY_DIR) $(WIRING_SERVING) --set-json 'modelServing.presets=[{"apiVersion":"agent-platform.giantswarm.io/v1alpha1","kind":"ServingPreset","metadata":{"name":"qwen3-14b"},"spec":{"displayName":"Overridden","model":{"id":"Qwen/Qwen3-14B","storageUri":"hf://Qwen/Qwen3-14B"},"chatTemplate":{"content":"{{ messages }}"},"requirements":{"weightsGiB":28}}}]' --set modelServing.cache.pvc.existingClaim=models >/tmp/vw-ms-presets.out 2>&1 || { cat /tmp/vw-ms-presets.out; exit 1; }
	@grep -q 'displayName: Overridden' /tmp/vw-ms-presets.out || { echo "FAIL: a values preset did not replace the shipped one"; exit 1; }
	@grep -q 'preset-source: "values"' /tmp/vw-ms-presets.out || { echo "FAIL: the values preset is not labelled as such"; exit 1; }
	@grep -q 'name: agent-platform-chat-template-qwen3-14b' /tmp/vw-ms-presets.out || { echo "FAIL: the inline chat template ConfigMap is missing"; exit 1; }
	@grep -q -- '--chat-template=/mnt/chat-template/chat-template.jinja' /tmp/vw-ms-presets.out || { echo "FAIL: the --chat-template flag was not appended"; exit 1; }
	@if grep -q 'kind: PersistentVolumeClaim' /tmp/vw-ms-presets.out; then echo "FAIL: a PVC rendered next to an existing claim"; exit 1; fi
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
	@echo "--> the KServe component guards: llmisvc without the controller, a non-Standard deployment mode, shared resources twice"
	$(call managers_must_fail,llmisvc needs kserve-resources,$(VM) --set components.kserve-llmisvc-resources.enabled=true,components.kserve-resources.enabled is false)
	$(call managers_must_fail,deployment mode must be Standard,$(VM) --set components.kserve-resources.enabled=true --set kserve-resources.kserve.controller.deploymentMode=Knative,must be Standard)
	$(call managers_must_fail,shared resources rendered once,$(VM) --set components.kserve-resources.enabled=true --set components.kserve-llmisvc-resources.enabled=true --set kserve-llmisvc-resources.kserve.createSharedResources=true,createSharedResources must stay false)
	@echo "--> the meta chart: the switch renders no release, the roster and the blocks reach connectivity, the wiring keys never reach the component charts, the policies knob arrives resolved, backstage dependsOn connectivity, connectivity dependsOn muster"
	@helm template t $(CHART_DIR) -f $(CHART_DIR)/ci/ci-values.yaml $(FLEET_APIS) --set components.modelServing.enabled=true --set components.backstage.enabled=true --set components.mcp-kubernetes.enabled=true --set components.kserve-crd.enabled=true --set components.kserve-resources.enabled=true >/tmp/vw-meta.out 2>&1 || { cat /tmp/vw-meta.out; exit 1; }
	@if grep -qE '^  name: modelServing$$' /tmp/vw-meta.out; then echo "FAIL: components.modelServing rendered a release; it is a feature switch"; exit 1; fi
	@awk '/^kind: HelmRelease$$/{h=1} h&&/^  name: agent-platform-connectivity$$/{f=1} f&&/^---/{exit} f' /tmp/vw-meta.out >/tmp/vw-meta-conn.out
	@grep -A1 '^      modelServing:$$' /tmp/vw-meta-conn.out | grep -q 'enabled: true' || { echo "FAIL: the roster forwarded to connectivity does not carry modelServing: enabled: true"; exit 1; }
	@for block in backstage mcp-kubernetes modelServing kserve-resources kserve-llmisvc-resources; do \
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
	@echo "the standalone's ported wiring verified."
