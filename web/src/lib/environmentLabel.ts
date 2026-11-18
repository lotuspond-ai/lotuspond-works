// The server's environment label (LOTUSPOND_ENVIRONMENT_LABEL), such as "Staging", read from the
// public health endpoint. Production leaves it unset, and then nothing is shown.
export function environmentLabel(health: unknown): string | null {
  if (!health || typeof health !== 'object') return null;
  const value = (health as { environment_label?: unknown }).environment_label;
  if (typeof value !== 'string') return null;
  const label = value.trim();
  return label && label.length <= 24 ? label : null;
}
