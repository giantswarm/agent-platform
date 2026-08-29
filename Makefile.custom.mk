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
	@grep -q 'type: LoadBalancer' /tmp/vg-edge.out || { echo "FAIL: edge data-plane Service is not gatewayApi.gateway.serviceType"; exit 1; }
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
