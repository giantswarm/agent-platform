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
{{- fail "workspaces.enabled is true but workspaces.storage.storageClassName is empty: a workspace is one read-write-many volume (bare mirrors and a directory per Session, used by git), claimed from a StorageClass that keeps POSIX semantics — Amazon EFS, Azure Files over NFS 4.1, an NFS server — which a cluster rarely has by default; name the class the installation provides, or name a new one and set workspaces.storage.storageClass.create with a preset to render it" -}}
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
