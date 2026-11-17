{{/*
Shared snippets. `include` renders one; `nindent N` indents it to fit where it's used.
*/}}

{{/* "<release>-fabpilot", or just "<release>" if it already contains the chart name; 63 chars max (DNS label). */}}
{{- define "fabpilot.fullname" -}}
{{- if contains .Chart.Name .Release.Name -}}
{{- .Release.Name | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- printf "%s-%s" .Release.Name .Chart.Name | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}

{{/*
Selector labels: what a Service / Deployment uses to FIND its pods. They must never change after
the first install (a Deployment's selector is immutable), so only stable values go in here.
Call with a dict: (dict "ctx" . "component" "api").
*/}}
{{- define "fabpilot.selectorLabels" -}}
app.kubernetes.io/name: {{ .ctx.Chart.Name }}
app.kubernetes.io/instance: {{ .ctx.Release.Name }}
app.kubernetes.io/component: {{ .component }}
{{- end -}}

{{/* All labels: the selector ones plus informational ones that MAY change (versions). */}}
{{- define "fabpilot.labels" -}}
{{ include "fabpilot.selectorLabels" . }}
app.kubernetes.io/part-of: fabpilot
app.kubernetes.io/version: {{ .ctx.Values.image.tag | quote }}
app.kubernetes.io/managed-by: {{ .ctx.Release.Service }}
helm.sh/chart: {{ printf "%s-%s" .ctx.Chart.Name .ctx.Chart.Version }}
{{- end -}}

{{/* The DB host: an external one from values, else this release's own StatefulSet (10.4c). */}}
{{- define "fabpilot.dbHost" -}}
{{- .Values.database.host | default (printf "%s-db" (include "fabpilot.fullname" .)) -}}
{{- end -}}

{{/*
The env every Python pod gets (api, migrate, seed, simulator): ConfigMap + Secret, then
DATABASE_URL assembled by KUBERNETES from them: $(VAR) expands a variable defined before it
(envFrom included), so the password never appears in a template, a value or the release history.
*/}}
{{- define "fabpilot.pythonEnv" -}}
envFrom:
  - configMapRef:
      name: {{ include "fabpilot.fullname" . }}-config
  - secretRef:
      name: {{ .Values.existingSecret }}
env:
  - name: DATABASE_URL
    value: "postgresql+psycopg://{{ .Values.database.user }}:$(POSTGRES_PASSWORD)@{{ include "fabpilot.dbHost" . }}:{{ .Values.database.port }}/{{ .Values.database.name }}"
{{- end -}}

{{/*
The security context of every Python container, matching the image (10.1): uid 10001, code
owned by root. readOnlyRootFilesystem: nothing in the container may be written except the
volumes we mount (/tmp), so an intruder can't drop or change files.
*/}}
{{- define "fabpilot.containerSecurity" -}}
securityContext:
  runAsNonRoot: true
  runAsUser: 10001
  runAsGroup: 10001
  allowPrivilegeEscalation: false
  readOnlyRootFilesystem: true
  capabilities:
    drop: ["ALL"]
  seccompProfile:
    type: RuntimeDefault
{{- end -}}
