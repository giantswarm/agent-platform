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

.PHONY: verify-modes
verify-modes: ## Assert ingress.mode fail-guards fire (connectivity chart owns the wiring + guards).
	@echo "====> $@ ($(CONNECTIVITY_DIR))"
	@echo "--> muster-direct with empty parentRefs must fail"
	@if helm template t $(CONNECTIVITY_DIR) --set ingress.mode=muster-direct >/tmp/vm-parents.out 2>&1; then \
		echo "FAIL: empty-parentRefs guard did not fire (render succeeded)"; cat /tmp/vm-parents.out; exit 1; \
	elif ! grep -q "ingress.parentRefs is required in all modes" /tmp/vm-parents.out; then \
		echo "FAIL: empty-parentRefs check failed for the wrong reason"; cat /tmp/vm-parents.out; exit 1; \
	else echo "ok: empty-parentRefs guard"; fi
	@echo "--> agentgateway-direct must be blocked with the DCR message"
	@if helm template t $(CONNECTIVITY_DIR) $(VM) --set ingress.mode=agentgateway-direct >/tmp/vm-direct.out 2>&1; then \
		echo "FAIL: direct-mode guard did not fire (render succeeded)"; cat /tmp/vm-direct.out; exit 1; \
	elif ! grep -q "requires a DCR-capable IdP" /tmp/vm-direct.out; then \
		echo "FAIL: direct-mode failed for the wrong reason"; cat /tmp/vm-direct.out; exit 1; \
	else echo "ok: direct blocked"; fi
	@echo "--> agentgateway-muster + viaMuster:false must fail"
	@if helm template t $(CONNECTIVITY_DIR) $(VM) --set ingress.mode=agentgateway-muster --set agentgateway.enabled=true --set mcps.enabled=true --set agent-platform-mcps.agentgateway.viaMuster=false >/tmp/vm-via.out 2>&1; then \
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
	@echo "--> agentgateway-muster + agentgateway.enabled:false must fail"
	@if helm template t $(CONNECTIVITY_DIR) $(VM) --set ingress.mode=agentgateway-muster --set agentgateway.enabled=false >/tmp/vm-dep.out 2>&1; then \
		echo "FAIL: dep-condition guard did not fire"; exit 1; \
	elif ! grep -q "agentgateway.enabled must be true" /tmp/vm-dep.out; then \
		echo "FAIL: dep-condition check failed for the wrong reason"; cat /tmp/vm-dep.out; exit 1; \
	else echo "ok: dep-condition guard"; fi
	@echo "--> positive: a valid agentgateway-muster config must render"
	@if helm template t $(CONNECTIVITY_DIR) $(VM) --set ingress.mode=agentgateway-muster --set agentgateway.enabled=true --set mcps.enabled=true --set agent-platform-mcps.agentgateway.viaMuster=true >/dev/null 2>&1; then \
		echo "ok: valid config renders"; \
	else echo "FAIL: a valid agentgateway-muster config was rejected"; exit 1; fi
	@echo "All mode guards verified."

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
	@echo "--> connectivity chart owns the wiring (renders an HTTPRoute)"
	@helm template t $(CONNECTIVITY_DIR) -f $(CONNECTIVITY_DIR)/ci/ci-values.yaml >/tmp/ap-conn.out 2>&1 || { cat /tmp/ap-conn.out; exit 1; }
	@grep -q 'kind: HTTPRoute' /tmp/ap-conn.out || { echo "FAIL: connectivity did not render the muster HTTPRoute"; exit 1; }
	@echo "ok: connectivity wiring"
	@echo "meta-package render verified."
