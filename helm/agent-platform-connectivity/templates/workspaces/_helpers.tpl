{{/* vim: set filetype=mustache: */}}
{{/*
Helpers of the workspace storage (templates/workspaces/): the read-write-many
StorageClass the workspace volumes are claimed from, workspaces.storage, which
the meta chart forwards while workspaces are on (giantswarm/agent-platform#900).
*/}}

{{/*
Truthy while workspaces are on (workspaces.enabled). The meta chart forwards
the block only then, so a missing block is off.
*/}}
{{- define "agent-platform.workspaces.enabled" -}}
{{- if dig "enabled" false (.Values.workspaces | default dict) }}true{{ end -}}
{{- end -}}

{{/*
The workspaces.storage block (a dict; empty when unset), as JSON.
*/}}
{{- define "agent-platform.workspaces.storage" -}}
{{- dig "storage" dict (.Values.workspaces | default dict) | toJson -}}
{{- end -}}

{{/*
The template-time guards of the workspace storage (workspaces.storage,
giantswarm/agent-platform#900), run while workspaces are on, by this chart and
by the meta chart alike (its templates/_helpers.tpl carries the same
definition): the class is named; a rendered class names a preset and the
preset's required input; the azureFiles preset keeps protocol nfs, since an
SMB share does not serve git. Emits nothing; fails naming the key.
*/}}
{{- define "agent-platform.workspaces.storage.validate" -}}
{{- $storage := dig "storage" dict (.Values.workspaces | default dict) -}}
{{- if not ($storage.storageClassName | default "") -}}
{{- fail "workspaces.enabled is true but workspaces.storage.storageClassName is empty: a workspace is one read-write-many volume (bare mirrors and a directory per Session, used by git), claimed from a StorageClass that keeps POSIX semantics — Amazon EFS, Azure Files over NFS 4.1, an NFS server — which a cluster rarely has by default; leave the default, workspaces-rwx, the name every installation gives the class it provides, name a class of another name it provides, or name a new one and set workspaces.storage.storageClass.create with a preset to render it" -}}
{{- end -}}
{{- $sc := $storage.storageClass | default dict -}}
{{- if $sc.create -}}
{{- $preset := toString ($sc.preset | default "") -}}
{{- if not (has $preset (list "efs" "azureFiles" "nfs")) -}}
{{- fail (printf "workspaces.storage.storageClass.create is true but workspaces.storage.storageClass.preset is %q: the presets are efs (Amazon EFS, an access point per volume), azureFiles (Azure Files over NFS 4.1) and nfs (an NFS server through the NFS CSI driver)" $preset) -}}
{{- end -}}
{{- if and (eq $preset "efs") (not (dig "efs" "fileSystemId" "" $sc)) -}}
{{- fail "workspaces.storage.storageClass.preset is efs but workspaces.storage.storageClass.efs.fileSystemId is empty: every volume is an access point on that file system (fs-…), which the installation provides" -}}
{{- end -}}
{{- if and (eq $preset "nfs") (not (dig "nfs" "server" "" $sc)) -}}
{{- fail "workspaces.storage.storageClass.preset is nfs but workspaces.storage.storageClass.nfs.server is empty: every volume is a directory of that server's export (nfs.share)" -}}
{{- end -}}
{{- $protocol := toString (dig "parameters" "protocol" "nfs" $sc) -}}
{{- if and (eq $preset "azureFiles") (ne $protocol "nfs") -}}
{{- fail (printf "workspaces.storage.storageClass.parameters.protocol is %q on the azureFiles preset: an Azure Files share over SMB fixes every file's mode at mount and has no symbolic links, so git does not work on it; the workspaces need protocol nfs, the preset's own" $protocol) -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{/*
The rendered class's provisioner, parameters and mount options from the preset,
with the block's own parameters merged over the preset's (a key of the block
wins) and its mountOptions replacing the preset's when set, as JSON
{provisioner, parameters, mountOptions}. For a validated block only.
*/}}
{{- define "agent-platform.workspaces.storageClass.spec" -}}
{{- $sc := dig "storageClass" dict (include "agent-platform.workspaces.storage" . | fromJson) -}}
{{- $preset := toString ($sc.preset | default "") -}}
{{- $provisioner := "" -}}
{{- $params := dict -}}
{{- $mount := list -}}
{{- if eq $preset "efs" -}}
{{- $efs := $sc.efs | default dict -}}
{{- $provisioner = "efs.csi.aws.com" -}}
{{- $params = dict "provisioningMode" "efs-ap" "fileSystemId" (toString $efs.fileSystemId) "basePath" (toString ($efs.basePath | default "/workspaces")) "directoryPerms" (toString ($efs.directoryPerms | default "700")) -}}
{{- $mount = list "tls" -}}
{{- else if eq $preset "azureFiles" -}}
{{- $az := $sc.azureFiles | default dict -}}
{{- $provisioner = "file.csi.azure.com" -}}
{{- $params = dict "protocol" "nfs" "skuName" (toString ($az.skuName | default "PremiumV2_LRS")) -}}
{{- with $az.networkEndpointType -}}{{- $_ := set $params "networkEndpointType" (toString .) -}}{{- end -}}
{{- $mount = list "nconnect=4" "actimeo=30" -}}
{{- else if eq $preset "nfs" -}}
{{- $nfs := $sc.nfs | default dict -}}
{{- $provisioner = "nfs.csi.k8s.io" -}}
{{- $params = dict "server" (toString $nfs.server) "share" (toString ($nfs.share | default "/")) "mountPermissions" "0777" -}}
{{- $mount = list "nfsvers=4.1" "hard" -}}
{{- end -}}
{{- range $k, $v := ($sc.parameters | default dict) -}}{{- $_ := set $params $k (toString $v) -}}{{- end -}}
{{- with $sc.mountOptions -}}{{- $mount = . -}}{{- end -}}
{{- dict "provisioner" $provisioner "parameters" $params "mountOptions" $mount | toJson -}}
{{- end -}}

{{/*
The template-time guards of Substrate's workspace driver
(workspaces.substrate.csiDriver, giantswarm/agent-platform#888), run while
workspaces are on, by this chart and by the meta chart alike (its templates/_helpers.tpl
carries the same definition): a driver without a name sets nothing; one with a
name has a controller endpoint with a port (the network policy admits it); TLS
takes Substrate's pod identity, the CSIDriverConfig's only client certificate;
a class rendered from a preset is provisioned by the driver — the named one, or
the companion's (giantswarm/agent-platform#889), whose own guards run here too.
Emits nothing; fails naming the key.
*/}}
{{- define "agent-platform.workspaces.substrate.validate" -}}
{{- $csi := dig "substrate" "csiDriver" dict (.Values.workspaces | default dict) -}}
{{- $name := toString ($csi.name | default "") -}}
{{- $endpoint := toString ($csi.controllerEndpoint | default "") -}}
{{- if not $name -}}
{{- if or $endpoint ($csi.nodeSocketOverride | default "") ($csi.tls | default dict).serverName -}}
{{- fail "workspaces.substrate.csiDriver.name is empty but other csiDriver keys are set: the CSIDriverConfig is named after the driver and renders only with a name; name the workspace StorageClass's provisioner, or unset the driver's keys" -}}
{{- end -}}
{{- else -}}
{{- if not (regexMatch "^(tcp|dns)://.+:[0-9]+$" $endpoint) -}}
{{- fail (printf "workspaces.substrate.csiDriver.controllerEndpoint is %q: Substrate's ate-api-server calls the driver's controller service at tcp://<host>:<port> or dns://<host>:<port>, and the network policy admits that port" $endpoint) -}}
{{- end -}}
{{- $tls := $csi.tls | default dict -}}
{{- if and $tls.enabled (not $tls.usePodIdentity) -}}
{{- fail "workspaces.substrate.csiDriver.tls.enabled is true but tls.usePodIdentity is not: Substrate presents its own pod identity to the controller endpoint, the only client certificate a CSIDriverConfig takes" -}}
{{- end -}}
{{- end -}}
{{- include "agent-platform.workspaces.csiCompanion.validate" . -}}
{{- $resolved := toString ((include "agent-platform.workspaces.csiDriver.resolved" . | fromJson).name | default "") -}}
{{- if $resolved -}}
{{- $sc := dig "storage" "storageClass" dict (.Values.workspaces | default dict) -}}
{{- $provisioner := get (dict "efs" "efs.csi.aws.com" "azureFiles" "file.csi.azure.com" "nfs" "nfs.csi.k8s.io") (toString ($sc.preset | default "")) -}}
{{- if and $sc.create (ne $provisioner $resolved) -}}
{{- fail (printf "workspaces.substrate.csiDriver.name (%s) differs from the provisioner of the workspace StorageClass rendered from preset %s (%s): Substrate mounts a workspace's volume through the driver that provisioned it" $resolved $sc.preset $provisioner) -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{/*
Truthy while the CSI companion renders (giantswarm/agent-platform#889):
workspaces on and workspaces.substrate.csiCompanion.enabled.
*/}}
{{- define "agent-platform.workspaces.csiCompanion.enabled" -}}
{{- if and (include "agent-platform.workspaces.enabled" .) (dig "substrate" "csiCompanion" "enabled" false (.Values.workspaces | default dict)) }}true{{ end -}}
{{- end -}}

{{/*
The companion's presets: the driver each stands in for, its image (the gsoci
mirror, pinned) and the driver's arguments in controller and node mode, as
JSON {<preset>: {provisioner, repository, tag, controllerArgs, nodeArgs}}.
The socket is always --endpoint=$(CSI_ENDPOINT), the chart's own.
  efs: aws-efs-csi-driver — controller: never deletes an access point's root
    directory (nothing of Substrate's creates one); node: no volume metrics.
  azureFiles: azurefile-csi — no cloud configuration Secret (the node reads the
    node's /etc/kubernetes/azure.json through AZURE_CREDENTIAL_FILE where it is;
    both run without one: --allow-empty-cloud-config), the share's root chmod
    the cluster's own node plugin applies (--mount-permissions=0777).
*/}}
{{- define "agent-platform.workspaces.csiCompanion.presets" -}}
{{- dict
  "efs" (dict "provisioner" "efs.csi.aws.com" "repository" "giantswarm/aws-efs-csi-driver" "tag" "v3.5.0"
    "controllerArgs" (list "--endpoint=$(CSI_ENDPOINT)" "--logtostderr" "--v=2" "--delete-access-point-root-dir=false")
    "nodeArgs" (list "--endpoint=$(CSI_ENDPOINT)" "--logtostderr" "--v=2" "--vol-metrics-opt-in=false"))
  "azureFiles" (dict "provisioner" "file.csi.azure.com" "repository" "giantswarm/azurefile-csi" "tag" "v1.35.7"
    "controllerArgs" (list "--v=2" "--endpoint=$(CSI_ENDPOINT)" "--drivername=file.csi.azure.com" "--cloud-config-secret-name=" "--cloud-config-secret-namespace=" "--allow-empty-cloud-config=true" "--user-agent-suffix=agent-platform")
    "nodeArgs" (list "--v=2" "--endpoint=$(CSI_ENDPOINT)" "--nodeid=$(KUBE_NODE_NAME)" "--drivername=file.csi.azure.com" "--cloud-config-secret-name=" "--cloud-config-secret-namespace=" "--allow-empty-cloud-config=true" "--mount-permissions=0777" "--user-agent-suffix=agent-platform"))
  | toJson -}}
{{- end -}}

{{/*
The companion as rendered (templates/workspaces/csi-companion.yaml), as JSON:
the preset and its provisioner, the driver image, the arguments of both modes
(the preset's unless set, extraArgs appended), the object names, the proxy's
and the Service's ports, the node socket and Substrate directory, the derived
controller endpoint and TLS server name (the Service's <name>.<namespace>.svc,
the one name Substrate's service-DNS signer puts into its certificate), the
node selector and tolerations (the WorkerPool template's unless set, the os
label always). For a validated block only.
*/}}
{{- define "agent-platform.workspaces.csiCompanion.spec" -}}
{{- $c := dig "substrate" "csiCompanion" dict (.Values.workspaces | default dict) -}}
{{- $preset := get (include "agent-platform.workspaces.csiCompanion.presets" . | fromJson) (toString ($c.preset | default "")) | default dict -}}
{{- $image := $c.image | default dict -}}
{{- $controller := $c.controller | default dict -}}
{{- $node := $c.node | default dict -}}
{{- $proxy := $c.proxy | default dict -}}
{{- $probe := $c.livenessProbe | default dict -}}
{{- $pool := dig "substrateWorkerPool" "template" dict (.Values.kagent | default dict) -}}
{{- $name := "csi-companion-controller" -}}
{{- $servicePort := dig "service" "port" 50051 $c | int -}}
{{- $socketDir := toString ($node.socketDir | default "/var/lib/kubelet/plugins/agent-platform-csi-companion") | trimSuffix "/" -}}
{{- $nodeSelector := merge (dict "kubernetes.io/os" "linux") (ternary $node.nodeSelector ($pool.nodeSelector | default dict) (not (empty $node.nodeSelector))) -}}
{{- dict
  "preset" (toString $c.preset)
  "provisioner" $preset.provisioner
  "image" (printf "%s/%s:%s" (toString ($image.registry | default "gsoci.azurecr.io")) (toString ($image.repository | default $preset.repository)) (toString ($image.tag | default $preset.tag)))
  "controllerArgs" (concat (ternary $controller.args ($preset.controllerArgs | default list) (not (empty $controller.args))) ($controller.extraArgs | default list))
  "nodeArgs" (concat (ternary $node.args ($preset.nodeArgs | default list) (not (empty $node.args))) ($node.extraArgs | default list))
  "controllerName" $name
  "nodeName" "csi-companion-node"
  "proxyConfigName" "csi-companion-proxy"
  "serviceName" $name
  "servicePort" $servicePort
  "proxyPort" (dig "port" 10000 $proxy | int)
  "proxyImage" (printf "%s/%s:%s" (toString (dig "image" "registry" "gsoci.azurecr.io" $proxy)) (toString (dig "image" "repository" "giantswarm/envoy" $proxy)) (toString (dig "image" "tag" "" $proxy)))
  "clientIdentity" (toString ($proxy.clientIdentity | default "spiffe://cluster.local/ns/ate-system/sa/ate-api-server"))
  "probeImage" (printf "%s/%s:%s" (toString (dig "image" "registry" "gsoci.azurecr.io" $probe)) (toString (dig "image" "repository" "giantswarm/livenessprobe" $probe)) (toString (dig "image" "tag" "" $probe)))
  "probeNodePort" (dig "nodePort" 29662 $probe | int)
  "socketDir" $socketDir
  "nodeSocket" (printf "unix://%s/csi.sock" $socketDir)
  "substrateDir" (toString ($node.substrateDir | default "/var/lib/ate") | trimSuffix "/")
  "efsStateDir" (toString (dig "efs" "stateDir" "/var/run/agent-platform-csi-companion/efs" $node))
  "efsConfigDir" (toString (dig "efs" "configDir" "/var/lib/agent-platform-csi-companion/efs-utils" $node))
  "controllerEndpoint" (printf "tcp://%s.%s.svc.cluster.local:%d" $name .Release.Namespace $servicePort)
  "serverName" (printf "%s.%s.svc" $name .Release.Namespace)
  "nodeSelector" $nodeSelector
  "nodeTolerations" (ternary $node.tolerations ($pool.tolerations | default list) (not (empty $node.tolerations)))
  "awsRoleARN" (toString (dig "cloudIdentity" "aws" "roleARN" "" $c))
  "azureClientID" (toString (dig "cloudIdentity" "azure" "workloadIdentityClientID" "" $c))
  | toJson -}}
{{- end -}}

{{/*
The template-time guards of the CSI companion, run while workspaces are on by
this chart and by the meta chart alike: a companion names a preset of the
driver it stands in for; a csiDriver the installation names beside it is that
driver (the companion is for the driver the workspace volumes are provisioned
with, and the registration is one object); the proxy admits one identity.
Emits nothing; fails naming the key.
*/}}
{{- define "agent-platform.workspaces.csiCompanion.validate" -}}
{{- if include "agent-platform.workspaces.csiCompanion.enabled" . -}}
{{- $c := dig "substrate" "csiCompanion" dict (.Values.workspaces | default dict) -}}
{{- $preset := toString ($c.preset | default "") -}}
{{- if not (hasKey (include "agent-platform.workspaces.csiCompanion.presets" . | fromJson) $preset) -}}
{{- fail (printf "workspaces.substrate.csiCompanion.enabled is true but workspaces.substrate.csiCompanion.preset is %q: the companion runs the cluster's file driver for Substrate — efs (Amazon EFS, efs.csi.aws.com) or azureFiles (Azure Files over NFS, file.csi.azure.com); name the driver the workspace StorageClass is provisioned with" $preset) -}}
{{- end -}}
{{- $spec := include "agent-platform.workspaces.csiCompanion.spec" . | fromJson -}}
{{- $own := toString (dig "substrate" "csiDriver" "name" "" (.Values.workspaces | default dict)) -}}
{{- if and $own (ne $own $spec.provisioner) -}}
{{- fail (printf "workspaces.substrate.csiDriver.name (%s) is not the driver of the CSI companion's preset %s (%s): Substrate registers one driver for the workspace volumes; set the companion's preset to that driver, or leave csiDriver.name empty and the registration is derived from the companion" $own $spec.preset $spec.provisioner) -}}
{{- end -}}
{{- if not (hasPrefix "spiffe://" $spec.clientIdentity) -}}
{{- fail (printf "workspaces.substrate.csiCompanion.proxy.clientIdentity is %q: the proxy admits the client certificate that carries this SPIFFE ID, ate-api-server's (spiffe://<trust domain>/ns/<namespace>/sa/ate-api-server)" $spec.clientIdentity) -}}
{{- end -}}
{{- if not (hasPrefix "/var/lib/kubelet/plugins/" $spec.socketDir) -}}
{{- fail (printf "workspaces.substrate.csiCompanion.node.socketDir is %q: atelet reaches a node plugin's socket under /var/lib/kubelet/plugins, the directory it mounts" $spec.socketDir) -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{/*
Substrate's workspace driver registration as JSON {name, controllerEndpoint,
nodeSocketOverride, tls: {enabled, usePodIdentity, serverName}}: the
installation's own (workspaces.substrate.csiDriver) while its name is set, else
derived from the CSI companion while that renders — the preset's driver, the
companion's Service as the controller endpoint, its node socket, mTLS through
the pod identity with the Service's name verified — else the own block, empty.
*/}}
{{- define "agent-platform.workspaces.csiDriver.resolved" -}}
{{- $own := dig "substrate" "csiDriver" dict (.Values.workspaces | default dict) -}}
{{- if and (not ($own.name | default "")) (include "agent-platform.workspaces.csiCompanion.enabled" .) -}}
{{- $spec := include "agent-platform.workspaces.csiCompanion.spec" . | fromJson -}}
{{- dict "name" $spec.provisioner "controllerEndpoint" $spec.controllerEndpoint "nodeSocketOverride" $spec.nodeSocket "tls" (dict "enabled" true "usePodIdentity" true "serverName" $spec.serverName) | toJson -}}
{{- else -}}
{{- $own | toJson -}}
{{- end -}}
{{- end -}}

{{/*
Truthy when the CSIDriverConfig renders (templates/workspaces/csidriverconfig.yaml):
workspaces on, a driver registered (workspaces.substrate.csiDriver.name, or the
companion's), and Substrate's CRDs there — the substrate component on (this
release dependsOn substrate-crds, which renders them) or the cluster serving
ate.dev/v1alpha1 CSIDriverConfig (a Substrate the installation provides). The
network policy that admits ate-api-server's egress to the controller endpoint
follows it.
*/}}
{{- define "agent-platform.workspaces.csiDriverConfig.enabled" -}}
{{- if and (include "agent-platform.workspaces.enabled" .) ((include "agent-platform.workspaces.csiDriver.resolved" . | fromJson).name | default "") -}}
{{- if or (include "agent-platform.substrate.enabled" .) (.Capabilities.APIVersions.Has "ate.dev/v1alpha1/CSIDriverConfig") }}true{{ end -}}
{{- end -}}
{{- end -}}

{{/*
Truthy when the registered driver is the CSI companion's: the companion renders
and the installation names no csiDriver of its own.
*/}}
{{- define "agent-platform.workspaces.csiCompanion.registered" -}}
{{- if and (include "agent-platform.workspaces.csiCompanion.enabled" .) (not (dig "substrate" "csiDriver" "name" "" (.Values.workspaces | default dict))) }}true{{ end -}}
{{- end -}}

{{/*
The port of the registered driver's controller endpoint (tcp|dns://<host>:<port>,
validated), which ate-api-server's egress admits for an installation's own driver.
*/}}
{{- define "agent-platform.workspaces.csiDriver.port" -}}
{{- regexFind "[0-9]+$" (toString (include "agent-platform.workspaces.csiDriver.resolved" . | fromJson).controllerEndpoint) -}}
{{- end -}}
