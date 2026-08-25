{{/*
Expand the name of the chart.
*/}}
{{- define "geodata-mcp.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{/*
Create a fully qualified app name.
We truncate at 63 chars minus the longest component suffix ("-postgres" = 9),
so component names like "<fullname>-postgres" stay within the 63-char label limit.
*/}}
{{- define "geodata-mcp.fullname" -}}
{{- if .Values.fullnameOverride -}}
{{- .Values.fullnameOverride | trunc 54 | trimSuffix "-" -}}
{{- else -}}
{{- $name := default .Chart.Name .Values.nameOverride -}}
{{- if contains $name .Release.Name -}}
{{- .Release.Name | trunc 54 | trimSuffix "-" -}}
{{- else -}}
{{- printf "%s-%s" .Release.Name $name | trunc 54 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{/*
Chart name and version, as used by the helm.sh/chart label.
*/}}
{{- define "geodata-mcp.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{/*
Common labels shared by every object.
*/}}
{{- define "geodata-mcp.labels" -}}
helm.sh/chart: {{ include "geodata-mcp.chart" . }}
{{ include "geodata-mcp.selectorLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/part-of: geodata-mcp
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end -}}

{{/*
Selector labels shared by every object.
*/}}
{{- define "geodata-mcp.selectorLabels" -}}
app.kubernetes.io/name: {{ include "geodata-mcp.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{/*
Per-component labels. Call: (dict "root" $ "component" "mcp")
*/}}
{{- define "geodata-mcp.componentLabels" -}}
{{ include "geodata-mcp.labels" .root }}
app.kubernetes.io/component: {{ .component }}
{{- end -}}

{{/*
Per-component selector labels. Call: (dict "root" $ "component" "mcp")
*/}}
{{- define "geodata-mcp.componentSelectorLabels" -}}
{{ include "geodata-mcp.selectorLabels" .root }}
app.kubernetes.io/component: {{ .component }}
{{- end -}}

{{/*
ServiceAccount name to use.
*/}}
{{- define "geodata-mcp.serviceAccountName" -}}
{{- if .Values.serviceAccount.create -}}
{{- default (include "geodata-mcp.fullname" .) .Values.serviceAccount.name -}}
{{- else -}}
{{- default "default" .Values.serviceAccount.name -}}
{{- end -}}
{{- end -}}

{{/*
Name of the Secret env is sourced from (buyer-managed existingSecret wins).
*/}}
{{- define "geodata-mcp.secretName" -}}
{{- if .Values.secrets.existingSecret -}}
{{- .Values.secrets.existingSecret -}}
{{- else -}}
{{- printf "%s-secret" (include "geodata-mcp.fullname" .) -}}
{{- end -}}
{{- end -}}

{{/*
Name of the application ConfigMap.
*/}}
{{- define "geodata-mcp.configMapName" -}}
{{- printf "%s-config" (include "geodata-mcp.fullname" .) -}}
{{- end -}}

{{/*
Service DNS names (in-cluster). Used to build connection URLs so they always
match the rendered Service names.
*/}}
{{- define "geodata-mcp.postgresHost" -}}
{{- printf "%s-postgres" (include "geodata-mcp.fullname" .) -}}
{{- end -}}
{{- define "geodata-mcp.minioHost" -}}
{{- printf "%s-minio" (include "geodata-mcp.fullname" .) -}}
{{- end -}}
{{- define "geodata-mcp.workerHost" -}}
{{- printf "%s-worker" (include "geodata-mcp.fullname" .) -}}
{{- end -}}
{{- define "geodata-mcp.mcpHost" -}}
{{- printf "%s-mcp" (include "geodata-mcp.fullname" .) -}}
{{- end -}}
{{- define "geodata-mcp.viewerHost" -}}
{{- printf "%s-viewer" (include "geodata-mcp.fullname" .) -}}
{{- end -}}
{{- define "geodata-mcp.caddyHost" -}}
{{- printf "%s-caddy" (include "geodata-mcp.fullname" .) -}}
{{- end -}}

{{/*
Fully-qualified image reference. Call: (dict "svc" .Values.mcp "root" $)
A per-service image.registry key (even "") overrides the global registry; an
absent key falls back to .Values.image.registry. Empty registry → no prefix
(Docker Hub). Tag defaults to the chart appVersion.
*/}}
{{- define "geodata-mcp.image" -}}
{{- $svc := .svc -}}
{{- $root := .root -}}
{{- $registry := $root.Values.image.registry -}}
{{- if hasKey $svc.image "registry" -}}
{{- $registry = $svc.image.registry -}}
{{- end -}}
{{- $repo := $svc.image.repository -}}
{{- $tag := $svc.image.tag | default $root.Chart.AppVersion -}}
{{- if $registry -}}
{{- printf "%s/%s:%s" $registry $repo $tag -}}
{{- else -}}
{{- printf "%s:%s" $repo $tag -}}
{{- end -}}
{{- end -}}

{{/*
imagePullSecrets block. Call with root context.
*/}}
{{- define "geodata-mcp.imagePullSecrets" -}}
{{- with .Values.image.pullSecrets }}
imagePullSecrets:
{{- range . }}
  - name: {{ . }}
{{- end }}
{{- end }}
{{- end -}}

{{/*
Prometheus scrape annotations for a stateless pod. Call sites guard this with
`if .Values.metrics.enabled`. Call: (dict "root" $ "port" 8000)
*/}}
{{- define "geodata-mcp.metricsAnnotations" -}}
prometheus.io/scrape: "true"
prometheus.io/path: {{ .root.Values.metrics.path | quote }}
prometheus.io/port: {{ .port | quote }}
{{- end -}}
