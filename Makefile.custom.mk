# Custom targets, auto-included by the root Makefile's `include Makefile.*.mk`.
# Lives outside the devctl-generated Makefile.gen.app.mk so it survives
# regeneration. DO NOT move these targets into the generated file.

##@ Custom

CHART_DIR ?= helm/agent-platform
CONNECTIVITY_DIR ?= helm/agent-platform-connectivity

# parentRefs[0].name satisfies the all-modes ingress guard so a single guard is
# isolated under test. Neither chart has subcharts anymore, so no `helm
# dependency build` and no subchart-fail quieting is needed.
VM := --set ingress.parentRefs[0].name=x

# The two components that own a kyverno.io object (kagent: two ClusterPolicies + the
# seccomp PolicyException; agentSandbox: the pod-security ClusterPolicy), so the
# kyvernoPolicies assertions below see all four objects.
KYVERNO_ALL := $(VM) --set components.kagent.enabled=true --set components.agent-sandbox.enabled=true
# The golden render deliberately uses the kubernetes networkPolicy flavor: the
# cilium flavor's CNPG section is now gated on postgres.enabled, the one intended
# render change (verify-global asserts that gate both ways). It also leaves
# agentSandbox off (see the 1.1.x note about its dropped resource-policy).
# GOLDEN_REF is the ref the rest of the default render must still match byte for
# byte; GOLDEN_REF= (empty) opts out for a clone that has no such ref.
KYVERNO_GOLDEN := $(VM) --set components.kagent.enabled=true --set networkPolicy.flavor=kubernetes
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
	@if helm template t $(CONNECTIVITY_DIR) $(KYVERNO_ALL) --set kyvernoPolicies.enabled=false >/tmp/vm-pe-guard.out 2>&1; then \
		echo "FAIL: the sandbox lost its only securityContext source and the render succeeded"; exit 1; \
	elif ! grep -q "agentSandbox.podSecurity.enabled requires kyvernoPolicies.enabled" /tmp/vm-pe-guard.out; then \
		echo "FAIL: the sandbox pod-security guard failed for the wrong reason"; cat /tmp/vm-pe-guard.out; exit 1; \
	else echo "ok: sandbox pod-security guard"; fi
	@echo "--> kyvernoPolicies.enabled=false renders no kyverno.io object"
	@helm template t $(CONNECTIVITY_DIR) $(KYVERNO_ALL) --set kyvernoPolicies.enabled=false --set agentSandbox.podSecurity.enabled=false >/tmp/vm-pe-none.out 2>&1 || { cat /tmp/vm-pe-none.out; exit 1; }
	@if grep -q "kyverno.io" /tmp/vm-pe-none.out; then \
		echo "FAIL: kyverno.io objects still render under kyvernoPolicies.enabled=false"; grep -n "kyverno.io" /tmp/vm-pe-none.out; exit 1; \
	else echo "ok: no kyverno.io kinds"; fi
	@echo "--> the default (kyverno) render still carries all four kyverno.io objects"
	@helm template t $(CONNECTIVITY_DIR) $(KYVERNO_ALL) >/tmp/vm-pe-kyverno.out 2>&1 || { cat /tmp/vm-pe-kyverno.out; exit 1; }
	@if [ "$$(grep -c '^apiVersion: kyverno.io/' /tmp/vm-pe-kyverno.out)" != "4" ]; then \
		echo "FAIL: expected 4 kyverno.io objects, got $$(grep -c '^apiVersion: kyverno.io/' /tmp/vm-pe-kyverno.out)"; exit 1; \
	else echo "ok: 4 kyverno.io objects"; fi
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
	@echo "--> the agent-sandbox policy carries no helm.sh/resource-policy (Helm must prune it)"
	@if grep -q "helm.sh/resource-policy" /tmp/vm-pe-kyverno.out; then \
		echo "FAIL: helm.sh/resource-policy is back; the policy would be orphaned on removal"; exit 1; \
	else echo "ok: prunable"; fi
	@echo "--> a component toggle left in its old per-chart block must fail loudly"
	@if helm template t $(CONNECTIVITY_DIR) $(VM) --set kagent.enabled=true >/tmp/vm-legacy.out 2>&1; then \
		echo "FAIL: a removed toggle rendered silently; the component would be off with no warning"; exit 1; \
	elif ! grep -q "components.kagent.enabled" /tmp/vm-legacy.out; then \
		echo "FAIL: the legacy-toggle guard failed for the wrong reason"; cat /tmp/vm-legacy.out; exit 1; \
	else echo "ok: legacy-toggle guard"; fi
	@echo "--> a legacy false under a component that is on must fail loudly (coalescing-safe probe)"
	@if helm template t $(CONNECTIVITY_DIR) $(VM) --set components.klaus-gateway.enabled=true --set klausGateway.enabled=false >/tmp/vm-legacy-on.out 2>&1; then \
		echo "FAIL: klausGateway.enabled=false rendered silently while components.klaus-gateway.enabled=true"; exit 1; \
	elif ! grep -q "components.klaus-gateway.enabled" /tmp/vm-legacy-on.out; then \
		echo "FAIL: the on+false legacy-toggle guard failed for the wrong reason"; cat /tmp/vm-legacy-on.out; exit 1; \
	else echo "ok: on+false legacy-toggle guard"; fi
	@echo "--> a legacy true under a component that is on passes (indistinguishable from a coalesced chart default)"
	@helm template t $(CONNECTIVITY_DIR) $(VM) --set components.klaus-gateway.enabled=true --set klausGateway.enabled=true >/tmp/vm-legacy-true.out 2>&1 || { \
		echo "FAIL: on+true must pass — a coalesced chart default would trip it on every install"; cat /tmp/vm-legacy-true.out; exit 1; }
	@echo "ok: on+true passes"
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
GLOBAL_VM := --set global.domain=ci.example.com --set 'global.gatewayApi.parentRefs[0].name=giantswarm-default' --set 'global.gatewayApi.parentRefs[0].namespace=envoy-gateway-system'
# A valid edge-mode config: the chart-owned Gateway is the public edge.
EDGE_VM := --set global.domain=ci.example.com --set ingress.mode=agentgateway-muster --set components.agentgateway.enabled=true --set gatewayApi.gateway.create=true --set gatewayApi.gateway.tls.secretName=wildcard-tls

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
	@helm template t $(CONNECTIVITY_DIR) $(VM) --set components.kagent.enabled=true --set kagent.controllerRoute.enabled=true --set kagent.controllerRoute.hostname=agw.example.com --set kagent.controllerRoute.jwtAuthentication.enabled=true --set gateway.jwksEgress.enabled=true --set global.identity.issuerUrl=https://dex.ci.example.com 2>/dev/null | grep -q 'issuer: "https://dex.ci.example.com"' || { echo "FAIL: JWT issuer not defaulted from global.identity"; exit 1; }
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

.PHONY: verify-meta
verify-meta: ## Assert the app-of-apps meta-package render (pure renderer, ranges as values, both engines, pinned BOM).
	@echo "====> $@ ($(CHART_DIR))"
	@echo "--> meta-package has NO Chart.yaml dependencies (no package-time pins)"
	@if grep -q '^dependencies:' $(CHART_DIR)/Chart.yaml; then \
		echo "FAIL: Chart.yaml still pins component versions as dependencies"; exit 1; \
	else echo "ok: zero pinned dependencies"; fi
	@echo "--> flux engine renders OCIRepository + HelmRelease with version RANGES + app-owned CRDs"
	@helm template t $(CHART_DIR) -f $(CHART_DIR)/ci/ci-values.yaml >/tmp/ap-flux.out 2>&1 || { cat /tmp/ap-flux.out; exit 1; }
	@grep -q 'kind: OCIRepository' /tmp/ap-flux.out || { echo "FAIL: no OCIRepository"; exit 1; }
	@grep -q 'kind: HelmRelease'   /tmp/ap-flux.out || { echo "FAIL: no HelmRelease"; exit 1; }
	@grep -q 'semver: "0.x"'       /tmp/ap-flux.out || { echo "FAIL: muster range not rendered as a value"; exit 1; }
	@grep -q 'name: agent-platform-connectivity' /tmp/ap-flux.out || { echo "FAIL: connectivity release missing"; exit 1; }
	@grep -qE '^  name: dicebear$$' /tmp/ap-flux.out || { echo "FAIL: dicebear avatar component not rendered"; exit 1; }
	@if grep -q 'platform-crds' /tmp/ap-flux.out; then echo "FAIL: retired platform-crds bundle still referenced"; exit 1; else echo "ok: no platform-crds bundle (app-owned CRDs)"; fi
	@grep -q 'crds: CreateReplace' /tmp/ap-flux.out || { echo "FAIL: app-owned CRDs (crds: CreateReplace) not rendered"; exit 1; }
	@grep -qE '^    - name: agentgateway$$' /tmp/ap-flux.out || { echo "FAIL: a CR consumer no longer dependsOn its CRD-owning component (agentgateway)"; exit 1; }
	@echo "ok: flux render"
	@echo "--> agentgateway 2.x wiring: forwarded values are FLAT and carry no umbrella-only key"
	@./tests/verify-agentgateway-wiring.py /tmp/ap-flux.out
	@grep -q 'semver: "2.x"' /tmp/ap-flux.out || { echo "FAIL: agentgateway range is not 2.x (the flattened chart line)"; exit 1; }
	@echo "ok: agentgateway 2.x wiring"
	@echo "--> PURE app-of-apps: root emits ONLY OCIRepository + HelmRelease (no raw CRs)"
	@if grep -E '^kind:' /tmp/ap-flux.out | grep -vqE '^kind: (OCIRepository|HelmRelease)$$'; then \
		echo "FAIL: root rendered a non-app-of-apps kind:"; grep -E '^kind:' /tmp/ap-flux.out | grep -vE '^kind: (OCIRepository|HelmRelease)$$'; exit 1; \
	else echo "ok: pure renderer (only OCIRepository/HelmRelease)"; fi
	@echo "--> argo engine renders Applications with CRD-first sync-waves"
	@helm template t $(CHART_DIR) -f $(CHART_DIR)/ci/ci-values.yaml --set gitops.engine=argo >/tmp/ap-argo.out 2>&1 || { cat /tmp/ap-argo.out; exit 1; }
	@grep -q 'kind: Application' /tmp/ap-argo.out || { echo "FAIL: no Argo Application"; exit 1; }
	@grep -q 'sync-wave: "0"'    /tmp/ap-argo.out || { echo "FAIL: CRDs not in sync-wave 0"; exit 1; }
	@echo "ok: argo render"
	@echo "--> bogus engine must fail"
	@if helm template t $(CHART_DIR) --set gitops.engine=bogus >/tmp/ap-eng.out 2>&1; then \
		echo "FAIL: engine guard did not fire"; exit 1; \
	elif ! grep -q "must be one of: flux, argo" /tmp/ap-eng.out; then \
		echo "FAIL: engine guard failed for the wrong reason"; cat /tmp/ap-eng.out; exit 1; \
	else echo "ok: engine guard"; fi
	@echo "--> customer BOM pins every range to an exact version"
	@helm template t $(CHART_DIR) -f $(CHART_DIR)/ci/ci-values.yaml -f $(CHART_DIR)/examples/customer-bom.yaml >/tmp/ap-bom.out 2>&1 || { cat /tmp/ap-bom.out; exit 1; }
	@grep -q 'semver: "0.9.0"' /tmp/ap-bom.out || { echo "FAIL: BOM did not pin muster to 0.9.0"; exit 1; }
	@if grep -qE 'semver: "[0-9]+\.x"' /tmp/ap-bom.out; then echo "FAIL: BOM still contains an unpinned x-range"; exit 1; fi
	@echo "ok: customer BOM pinned"
	@echo "--> gitops.namespace routes the Flux CRs to an exempt ns, targetNamespace routes workloads"
	@helm template t $(CHART_DIR) -f $(CHART_DIR)/ci/ci-values.yaml --set gitops.namespace=flux-giantswarm --set gitops.targetNamespace=agent-platform >/tmp/ap-ns.out 2>&1 || { cat /tmp/ap-ns.out; exit 1; }
	@if grep -E '^  namespace:' /tmp/ap-ns.out | grep -vq 'flux-giantswarm'; then \
		echo "FAIL: a rendered CR is not in the gitops.namespace"; grep -E '^  namespace:' /tmp/ap-ns.out | grep -v 'flux-giantswarm'; exit 1; \
	else echo "ok: all CRs in flux-giantswarm"; fi
	@grep -q 'targetNamespace: agent-platform' /tmp/ap-ns.out || { echo "FAIL: HelmRelease targetNamespace not routed"; exit 1; }
	@echo "ok: gitops namespace routing"
	@echo "--> components.<name>.enabled=false skips that component's release"
	@helm template t $(CHART_DIR) -f $(CHART_DIR)/ci/ci-values.yaml --set components.kagent.enabled=false >/tmp/ap-noc.out 2>&1 || { cat /tmp/ap-noc.out; exit 1; }
	@if grep -qE '^  name: kagent$$' /tmp/ap-noc.out; then echo "FAIL: kagent still rendered when disabled"; exit 1; else echo "ok: kagent component skipped"; fi
	@grep -q 'name: muster' /tmp/ap-noc.out || { echo "FAIL: disabling kagent dropped other components"; exit 1; }
	@echo "--> a dependsOn ref to a disabled component is dropped (no dangling dependency)"
	@if grep -qE '^    - name: kagent$$' /tmp/ap-noc.out; then echo "FAIL: connectivity still dependsOn disabled kagent (would block forever)"; exit 1; else echo "ok: dangling dependsOn dropped"; fi
	@echo "--> the meta chart forwards the RESOLVED enablement to the connectivity chart"
	@python3 tests/verify-component-enablement.py $(CHART_DIR) $(CONNECTIVITY_DIR)
	@echo "ok: a disabled component renders neither a release nor its wiring"
	@echo "--> every connectivity top-level key is settable through the meta chart"
	@python3 -c 'import json,sys; m=set(json.load(open("$(CHART_DIR)/values.schema.json"))["properties"]); c=set(json.load(open("$(CONNECTIVITY_DIR)/values.schema.json"))["properties"]); miss=sorted(c-m); sys.exit("FAIL: connectivity keys the meta chart schema rejects (root is additionalProperties:false, so forwardAllValues cannot reach them): "+", ".join(miss) if miss else 0)'
	@echo "ok: no unreachable connectivity keys"
	@echo "--> connectivity chart owns the wiring (renders an HTTPRoute)"
	@helm template t $(CONNECTIVITY_DIR) -f $(CONNECTIVITY_DIR)/ci/ci-values.yaml >/tmp/ap-conn.out 2>&1 || { cat /tmp/ap-conn.out; exit 1; }
	@grep -q 'kind: HTTPRoute' /tmp/ap-conn.out || { echo "FAIL: connectivity did not render the muster HTTPRoute"; exit 1; }
	@echo "ok: connectivity wiring"
	@echo "meta-package render verified."

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
