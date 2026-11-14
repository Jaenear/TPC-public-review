/** Coaching view contracts. Candidate selection never creates a booking. */
export const studioBase = "/api/studio/coaching";
export const productPattern = /^cproduct_[a-f0-9]{32}$/;
export const coachPattern = /^[a-z0-9-]{1,100}$/;
export const statusLabels = { draft: "작성 중", submitted: "검수 대기", approved: "승인", rejected: "반려", active: "활성", paused: "일시 중지" };
export const weekdays = ["월", "화", "수", "목", "금", "토", "일"];
export const isCoach = (user) => user?.role === "admin" || user?.roles?.includes("coach");
export const localDay = (zone = "Asia/Seoul", now = new Date()) => new Intl.DateTimeFormat("en-CA", { timeZone: zone, year: "numeric", month: "2-digit", day: "2-digit" }).format(now);
export function nextDay(day, count = 14) { const d = new Date(day + "T12:00:00Z"); d.setUTCDate(d.getUTCDate() + count); return d.toISOString().slice(0, 10); }
export const rootOf = (value) => value.product || value.calendar;
export const openRevision = (value) => value.revisions?.find((r) => ["draft", "submitted"].includes(r.status));
export function checkedDetail(value, kind, id) {
  const r = value?.[kind];
  if (!value?.context || !coachPattern.test(value.context.coachId) || !Number.isInteger(value.context.profileVersion) || value.context.profileVersion < 1 ||
    (r && (r.id !== id || !["active", "paused"].includes(r.state) || !Number.isInteger(r.version) || !Number.isInteger(r.publicVersion))) ||
    (!r && kind !== "calendar") || (r && (!Array.isArray(value.revisions) || !Array.isArray(value.events)))) throw new Error("현재 상품·일정 원문을 확인할 수 없습니다.");
  return value;
}
export function checkedProduct(p, id = p?.id) {
  if (!p || !productPattern.test(p.id) || p.id !== id || !Number.isInteger(p.version) || p.version < 1 || !p.revisionId ||
      !p.availability || p.reservationEnabled !== false || p.paymentEnabled !== false || !Array.isArray(p.topics) || !Array.isArray(p.outcomes)) throw new Error("현재 공개 상품을 확인할 수 없습니다.");
  return p;
}
export function selectionBody(p, slot) {
  if (slot.productId !== p.id || slot.coachId !== p.coachId || slot.productRevisionId !== p.revisionId || slot.calendarRevisionId !== p.availability.calendarRevisionId || slot.version !== 1) throw new Error("상품과 일정이 변경되었습니다. 다시 조회해주세요.");
  return { productVersion: p.version, productRevisionId: p.revisionId, calendarVersion: p.availability.calendarVersion, calendarRevisionId: p.availability.calendarRevisionId, slotId: slot.id, slotVersion: slot.version };
}
function checkedSlotTimes(slot) {
  const keys = ["startsAt", "endsAt", "displayStartsAt", "displayEndsAt", "originalStartsAt", "originalEndsAt"];
  if (keys.some((k) => typeof slot[k] !== "string" || !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:Z|[+-]\d{2}:\d{2})$/.test(slot[k]) || !Number.isFinite(Date.parse(slot[k]))) ||
    Date.parse(slot.startsAt) >= Date.parse(slot.endsAt) || ["display", "original"].some((k) => Date.parse(slot[k + "StartsAt"]) !== Date.parse(slot.startsAt) || Date.parse(slot[k + "EndsAt"]) !== Date.parse(slot.endsAt)) ||
    ["displayTimeZone", "originalTimeZone"].some((k) => typeof slot[k] !== "string" || !/^[A-Za-z0-9_+/-]{1,64}$/.test(slot[k]))) throw new Error("후보의 원 시각과 표시 시각이 다릅니다. 다시 조회해주세요.");
}
export function checkedSlots(value, product, page) {
  if (value?.productVersion !== product.version || value?.productRevisionId !== product.revisionId || value?.calendarVersion !== product.availability.calendarVersion || value?.calendarRevisionId !== product.availability.calendarRevisionId) throw Object.assign(new Error("상품·가격 또는 가능 시간이 바뀌었습니다. 최신 상품에서 다시 확인해주세요."), { status: 409 });
  if (!Array.isArray(value.slots) || !Number.isInteger(value.total) || value.total < value.slots.length || value.page !== page || value.pageSize !== 50 || value.slots.length > 50 || value.reservationEnabled !== false) throw new Error("후보 응답의 범위와 상태를 확인할 수 없습니다.");
  value.slots.forEach((s) => { selectionBody(product, s); checkedSlotTimes(s); });
  return value;
}
export function slotTimeLabel(slot) {
  const a = slot.displayStartsAt, b = slot.displayEndsAt;
  return `${a.slice(0, 10)} · ${a.slice(11, 16)}–${a.slice(0, 10) === b.slice(0, 10) ? "" : b.slice(0, 10) + " "}${b.slice(11, 16)}`;
}
export function coachingError(error) {
  if (error?.sessionChanged || error?.status === 401) return "로그인 상태가 바뀌었습니다. 현재 계정으로 다시 열어주세요.";
  if (error?.status === 403) return "현재 담당자 또는 운영 권한이 없습니다.";
  if (error?.status === 404) return "현재 공개되거나 접근 가능한 원문이 없습니다.";
  if (error?.status === 409) return "다른 화면에서 조건·담당자·판본이 바뀌었습니다. 현재 원문을 다시 확인해주세요.";
  return String(error?.message || "연결 상태를 확인하고 다시 조회해주세요.").slice(0, 500);
}
export const emptyProduct = () => ({ title: "", description: "", topics: [], audience: "", outcomes: [], method: "online_live", deliverySummary: "", durationMinutes: 60, sessionCount: 1, price: 0, currency: "KRW", preparation: "", bufferBeforeMinutes: 0, bufferAfterMinutes: 0, bookingSequence: "coach_then_payment", cancellationSummary: "", rescheduleSummary: "", noShowSummary: "", coachAbsenceSummary: "", fulfillmentSummary: "", sessionValidityDays: 30 });
export const emptyCalendar = () => ({ timeZone: "Asia/Seoul", effectiveFrom: localDay(), effectiveUntil: nextDay(localDay(), 90), weekly: [], timeOff: [], leadTimeHours: 24, horizonDays: 90, stepMinutes: 30 });
