/** Only bounded, non-sensitive retry metadata is exposed to API callers. */
export function retryAfterSeconds(value, now = Date.now()) {
  if (typeof value !== "string" || !value.trim()) return null;
  const trimmed = value.trim();
  if (/^\d+$/.test(trimmed)) return Math.min(86400, Number(trimmed));
  const parsed = Date.parse(trimmed);
  return Number.isFinite(parsed) ? Math.min(86400, Math.max(0, Math.ceil((parsed - now) / 1000))) : null;
}
