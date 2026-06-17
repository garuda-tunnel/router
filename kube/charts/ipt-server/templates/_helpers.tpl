{{- define "iptServer.labels" -}}
app.kubernetes.io/name: ipt-server
app.kubernetes.io/instance: {{ .Values.name | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/part-of: garuda
garuda.managed-by: helm
{{- range $k, $v := .Values.labels }}
{{ $k }}: {{ $v | quote }}
{{- end }}
{{- end -}}

{{- define "iptServer.selector" -}}
app.kubernetes.io/name: ipt-server
app.kubernetes.io/instance: {{ .Values.name | quote }}
{{- end -}}

{{/* Comma-separated Multus annotation: name@iface, name@iface. */}}
{{- define "iptServer.networks" -}}
{{- $items := list -}}
{{- range .Values.nicAttach -}}
{{- $items = append $items (printf "%s@%s" . .) -}}
{{- end -}}
{{- join "," $items -}}
{{- end -}}
