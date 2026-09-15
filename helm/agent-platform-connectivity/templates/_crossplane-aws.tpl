{{/*
The Crossplane (upbound provider-aws) objects every S3 store this chart
provisions is made of — the kagent-pg backup bucket (templates/postgres/
crossplane-aws.yaml) and Agent Substrate's snapshot store (templates/substrate/
crossplane-aws.yaml) render the same four bucket objects and the same trust
and access statements; only the role's subjects, description and lifecycle
default differ.

agent-platform.crossplane.aws.bucket — the Bucket (never deleted by Crossplane,
kept by Helm: the data outlives the release), its lifecycle expiration, the
public-access block and the TLS-only policy.
`bucketComment` and `lifecycleComment` are lists of comment lines, so each
caller says what its store holds.
Usage: include "agent-platform.crossplane.aws.bucket" (dict "root" $ "xp" $xp "bucket" $bucket "lifecycleDays" 45 "tags" $tags "bucketComment" (list "…") "lifecycleComment" (list "…"))
*/}}
{{- define "agent-platform.crossplane.aws.bucket" -}}
{{- $xp := .xp }}
{{- $bucket := .bucket }}
{{- $partition := include "agent-platform.crossplane.awsPartition" (dict "xp" $xp) }}
---
{{- range .bucketComment }}
# {{ . }}
{{- end }}
apiVersion: s3.aws.upbound.io/v1beta2
kind: Bucket
metadata:
  name: {{ $bucket }}
  labels:
    {{- include "labels.common" .root | nindent 4 }}
    app.kubernetes.io/component: storage
  annotations:
    crossplane.io/external-name: {{ $bucket }}
    helm.sh/resource-policy: keep
spec:
  managementPolicies:
    {{- include "agent-platform.crossplane.managementPoliciesNoDelete" (dict "xp" $xp) | nindent 4 }}
  forProvider:
    forceDestroy: false
    objectLockEnabled: false
    region: {{ $xp.region }}
    tags:
      {{- toYaml .tags | nindent 6 }}
  providerConfigRef:
    name: {{ $xp.providerConfigRef }}
---
{{- range .lifecycleComment }}
# {{ . }}
{{- end }}
apiVersion: s3.aws.upbound.io/v1beta1
kind: BucketLifecycleConfiguration
metadata:
  name: {{ $bucket }}
  labels:
    {{- include "labels.common" .root | nindent 4 }}
    app.kubernetes.io/component: storage
  annotations:
    crossplane.io/external-name: {{ $bucket }}
spec:
  managementPolicies:
    {{- include "agent-platform.crossplane.managementPolicies" (dict "xp" $xp) | nindent 4 }}
  forProvider:
    bucketRef:
      name: {{ $bucket }}
    region: {{ $xp.region }}
    rule:
      - id: Expiration
        status: Enabled
        expiration:
          - days: {{ .lifecycleDays | int }}
  providerConfigRef:
    name: {{ $xp.providerConfigRef }}
---
apiVersion: s3.aws.upbound.io/v1beta1
kind: BucketPublicAccessBlock
metadata:
  name: {{ $bucket }}
  labels:
    {{- include "labels.common" .root | nindent 4 }}
    app.kubernetes.io/component: storage
  annotations:
    crossplane.io/external-name: {{ $bucket }}
spec:
  managementPolicies:
    {{- include "agent-platform.crossplane.managementPolicies" (dict "xp" $xp) | nindent 4 }}
  forProvider:
    bucketRef:
      name: {{ $bucket }}
    region: {{ $xp.region }}
    blockPublicAcls: true
    blockPublicPolicy: true
    ignorePublicAcls: true
    restrictPublicBuckets: true
  providerConfigRef:
    name: {{ $xp.providerConfigRef }}
---
# TLS only.
apiVersion: s3.aws.upbound.io/v1beta1
kind: BucketPolicy
metadata:
  name: {{ $bucket }}
  labels:
    {{- include "labels.common" .root | nindent 4 }}
    app.kubernetes.io/component: storage
  annotations:
    crossplane.io/external-name: {{ $bucket }}
spec:
  managementPolicies:
    {{- include "agent-platform.crossplane.managementPolicies" (dict "xp" $xp) | nindent 4 }}
  forProvider:
    bucketRef:
      name: {{ $bucket }}
    region: {{ $xp.region }}
    policy: |
      {
        "Version": "2012-10-17",
        "Statement": [
          {
            "Sid": "EnforceSSLOnly",
            "Effect": "Deny",
            "Principal": "*",
            "Action": ["s3:*"],
            "Resource": [
              "{{ $partition }}:s3:::{{ $bucket }}",
              "{{ $partition }}:s3:::{{ $bucket }}/*"
            ],
            "Condition": {
              "Bool": {
                "aws:SecureTransport": "false"
              }
            }
          }
        ]
      }
  providerConfigRef:
    name: {{ $xp.providerConfigRef }}
{{- end -}}

{{/*
One statement of an IRSA role's trust policy: the OIDC provider may assume the
role for the ServiceAccount `sub` (StringEquals; `op` StringLike for a pattern).
Usage: include "agent-platform.crossplane.aws.trustStatement" (dict "xp" $xp "sub" "system:serviceaccount:ns:name" "op" "StringEquals")
*/}}
{{- define "agent-platform.crossplane.aws.trustStatement" -}}
{{- $partition := include "agent-platform.crossplane.awsPartition" (dict "xp" .xp) -}}
{{- $aws := .xp.aws -}}
{
  "Effect": "Allow",
  "Principal": {
    "Federated": "{{ $partition }}:iam::{{ $aws.accountId }}:oidc-provider/{{ $aws.oidcProvider }}"
  },
  "Action": "sts:AssumeRoleWithWebIdentity",
  "Condition": {
    "{{ .op | default "StringEquals" }}": {
      "{{ $aws.oidcProvider }}:sub": "{{ .sub }}",
      "{{ $aws.oidcProvider }}:aud": "sts.amazonaws.com{{- if eq $partition "arn:aws-cn" }}.cn{{- end }}"
    }
  }
}
{{- end -}}

{{/*
The inline policy of an IRSA role over one bucket: list, get, put, delete on
the bucket and its objects — what a store's client needs, no more — plus the
account-level reads the AWS SDKs probe.
Usage: include "agent-platform.crossplane.aws.s3Policy" (dict "xp" $xp "bucket" $bucket)
*/}}
{{- define "agent-platform.crossplane.aws.s3Policy" -}}
{{- $partition := include "agent-platform.crossplane.awsPartition" (dict "xp" .xp) -}}
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "s3:ListBucket",
        "s3:PutObject",
        "s3:GetObject",
        "s3:DeleteObject"
      ],
      "Resource": [
        "{{ $partition }}:s3:::{{ .bucket }}",
        "{{ $partition }}:s3:::{{ .bucket }}/*"
      ]
    },
    {
      "Effect": "Allow",
      "Action": [
        "s3:GetAccessPoint",
        "s3:GetAccountPublicAccessBlock",
        "s3:ListAccessPoints"
      ],
      "Resource": "*"
    }
  ]
}
{{- end -}}
