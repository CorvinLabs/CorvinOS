{{/*
Standard Helm helpers for corvin-gateway.
*/}}

{{- define "corvin-gateway.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "corvin-gateway.fullname" -}}
{{- $name := default .Chart.Name .Values.nameOverride -}}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "corvin-gateway.labels" -}}
app.kubernetes.io/name: {{ include "corvin-gateway.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end -}}

{{- define "corvin-gateway.selectorLabels" -}}
app.kubernetes.io/name: {{ include "corvin-gateway.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}
