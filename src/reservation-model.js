/** Private reservation read contracts and civil-calendar arithmetic. */
import { checkedProduct, checkedSlots, selectionBody, nextDay } from "./coaching-model.js";
export const ids = { hold: /^slothold_[a-f0-9]{32}$/, reservation: /^coachingreservation_[a-f0-9]{32}$/, policy: /^reservationpolicy_[a-f0-9]{32}$/ };
export const reservationStates = { awaiting_coach: "코치 확인 중", awaiting_payment: "결제 대기", confirmed: "진행 중", completed: "전체 회차 완료", cancelled: "취소", expired: "기한 만료", exception: "운영 확인 필요" };
export const holdStates = { active: "임시 확보", released: "확보 해제", consumed: "요청에 사용", expired: "확보 기한 만료" };
export const paymentStates = { not_required: "결제 대상 아님", unpaid: "미결제", paid: "결제 근거 확인", refund_pending: "환불 확인 필요", refunded: "환불 근거 확인", exception: "결제 확인 필요" };
export const sessionStates = { unscheduled: "일정 선택 전", requested: "코치 확인 중", request_expired: "요청 기한 만료", confirmed: "일정 확정", completed: "수행 완료", no_show: "불참 확인", cancelled: "취소" };
export const userError = (e) => e?.sessionChanged || e?.status === 401 ? "로그인 상태가 바뀌었습니다. 현재 계정으로 다시 열어주세요." : e?.selectionUnavailable ? String(e.message) : e?.status === 403 ? "현재 담당자 또는 관리자 권한이 없습니다." : e?.status === 404 ? "접근 가능한 원문이 없습니다. 현재 계정과 공개 상태를 확인해주세요." : e?.status === 409 ? "조건이나 진행 상태가 바뀌었습니다. 원 요청 결과와 현재 내용을 확인해주세요." : e?.status === 429 ? "요청이 잠시 많습니다. 잠시 후 현재 내용을 확인하고 다시 시도해주세요." : String(e?.message || "연결 상태를 확인하고 다시 조회해주세요.").slice(0, 400);
const invalid = () => { throw new Error("현재 예약 원문의 범위와 상태를 확인할 수 없습니다. 다시 조회해주세요."); };
const integer = (n, min = 1, max = Number.MAX_SAFE_INTEGER) => Number.isSafeInteger(n) && n >= min && n <= max;
const instant = (s) => typeof s === "string" && /^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:Z|[+-]\d\d:\d\d)$/.test(s) && Number.isFinite(Date.parse(s));
export function checkedScope(v) { if (v?.scope?.kind !== "reservation_api" || v.scope.technicalOnly !== true || v.scope.commercialBookingsEnabled !== false || v.scope.realGatewayConnected !== false) invalid(); return v; }
export function checkedPolicy(p) {
  const fields = ["holdSeconds", "coachSeconds", "paymentSeconds", "rescheduleBeforeHours", "cancelBeforeHours", "noShowConsumes", "refundOnCancellation", "rescheduleRequiresCoach", "validityAnchor", "revisionRule"];
  if (!p || Object.keys(p).length !== fields.length || fields.some((k) => !Object.hasOwn(p, k)) || !integer(p.holdSeconds, 30, 1800) || !integer(p.coachSeconds, 30, 604800) || !integer(p.paymentSeconds, 30, 604800) || !integer(p.rescheduleBeforeHours, 0, 720) || !integer(p.cancelBeforeHours, 0, 720) || ["noShowConsumes", "refundOnCancellation", "rescheduleRequiresCoach"].some((k) => typeof p[k] !== "boolean") || p.validityAnchor !== "confirmed_at" || p.revisionRule !== "revalidate_until_confirmation") invalid();
  return p;
}
export function checkedSlot(s) {
  if (!s || !/^[a-z0-9_-]{1,100}$/.test(s.id) || !instant(s.startsAt) || !instant(s.endsAt) || Date.parse(s.startsAt) >= Date.parse(s.endsAt) || ["display", "original"].some((prefix) => !instant(s[prefix + "StartsAt"]) || !instant(s[prefix + "EndsAt"]) || Date.parse(s[prefix + "StartsAt"]) !== Date.parse(s.startsAt) || Date.parse(s[prefix + "EndsAt"]) !== Date.parse(s.endsAt))) invalid();
  for (const k of ["originalTimeZone", "displayTimeZone"]) { try { new Intl.DateTimeFormat("ko", { timeZone: s[k] }).format(); } catch { invalid(); } }
  return s;
}
export function checkedQuote(q, productId) {
  if (q?.scope !== "technical" || !ids.policy.test(q.policyId)) invalid(); checkedProduct(q.product, productId); checkedProduct(q.candidateProduct, productId); checkedPolicy(q.policy); checkedSlot(q.slot);
  selectionBody(q.candidateProduct, q.slot);
  if (q.product.coachId !== q.candidateProduct.coachId || q.slot.coachId !== q.product.coachId) invalid(); return q;
}
export function checkedDetail(v, kind, expected = {}) {
  checkedScope(v); const r = v?.[kind], c = v?.capabilities;
  if (!ids[kind]?.test(r?.id) || expected.id && r.id !== expected.id || expected.productId && r.productId !== expected.productId || kind !== "policy" && !integer(r.version)) invalid();
  if (kind === "policy") { checkedPolicy(r.payload); if (r.scope !== "technical" || !integer(r.productVersion) || typeof r.approvalReference !== "string") invalid(); return v; }
  checkedQuote(r.quote, r.productId);
  if (r.policyId !== r.quote.policyId || r.coachId !== r.quote.product.coachId || typeof r.memberId !== "string" || expected.memberId && r.memberId !== expected.memberId || !c || c.version !== r.version || !instant(c.asOf) || ["isMember", "isOperator", "isAdmin", "canExpire"].some((k) => typeof c[k] !== "boolean")) invalid();
  if (kind === "hold") {
    if (!Object.hasOwn(holdStates, r.state) || !Object.hasOwn(holdStates, r.storedState) || !instant(r.expiresAt) || !integer(r.remainingSeconds, 0) || r.slotId !== r.quote.slot.id || ["canRelease", "canRequest"].some((k) => typeof c[k] !== "boolean") || (r.parentReservationId === null ? r.parentReservationVersion !== null || r.sessionOrdinal !== null : !ids.reservation.test(r.parentReservationId) || !integer(r.parentReservationVersion) || !integer(r.sessionOrdinal))) invalid();
    if (r.reservationId !== null && !ids.reservation.test(r.reservationId)) invalid();
  } else {
    if (!Object.hasOwn(reservationStates, r.state) || !Object.hasOwn(reservationStates, r.storedState) || !Object.hasOwn(paymentStates, r.paymentState) || !integer(r.sessionCount) || !integer(r.consumedSessionCount, 0, r.sessionCount) || !Array.isArray(r.sessions) || r.sessions.length !== r.sessionCount || !Array.isArray(v.events) || !Array.isArray(c.sessions) || c.sessions.length !== r.sessionCount || typeof c.canCancel !== "boolean" || !Array.isArray(c.cancelCauses) || c.cancelCauses.some((x) => !["member_cancel", "coach_absence", "operator_cancel"].includes(x))) invalid();
    r.sessions.forEach((s, i) => { const a = c.sessions[i]; if (s.ordinal !== i + 1 || !integer(s.version) || !Object.hasOwn(sessionStates, s.state) || a?.ordinal !== s.ordinal || a.version !== s.version || ["canAccept", "canReject", "canComplete", "canNoShow", "canReschedule", "canSchedule"].some((k) => typeof a[k] !== "boolean")) invalid(); if (s.slot) { checkedSlot(s.slot); if (s.slotId !== s.slot.id || s.slot.productId !== r.productId || s.slot.coachId !== r.coachId) invalid(); } else if (s.slotId !== null) invalid(); });
    for (const k of ["deadlineAt", "confirmedAt", "validUntil"]) if (r[k] !== null && !instant(r[k])) invalid();
  }
  return v;
}
export function checkedPolicies(v, product, page = 1) {
  checkedPage(v, "policies", page); if (v.productId !== product.id || v.productVersion !== product.version || v.productRevisionId !== product.revisionId) throw Object.assign(new Error("상품 조건이 변경되었습니다. 현재 상품을 다시 열어주세요."), { status: 409 });
  v.policies.forEach((p) => { if (!ids.policy.test(p.id) || p.productId !== product.id || p.productVersion !== product.version || p.productRevisionId !== product.revisionId || p.scope !== "technical" || !instant(p.createdAt)) invalid(); checkedPolicy(p.payload); }); return v;
}
export function checkedScheduling(v, value, page, window = null) {
  checkedScope(v); checkedSlots(v, value.product, page);
  const r = value.parent?.reservation, s = r?.sessions.find((s) => s.ordinal === value.ordinal), c = v.schedulingContext, mode = value.mode === "change" ? "reschedule" : "request";
  if (!r || !s || !c || !instant(v.asOf) || c.reservationId !== r.id || c.reservationVersion !== r.version || c.sessionOrdinal !== s.ordinal || c.sessionVersion !== s.version || c.mode !== mode || c.policyId !== r.policyId || c.validUntil !== r.validUntil || !instant(c.validUntil) || c.originalSlotId !== (mode === "reschedule" ? s.slotId : null) || v.slots.some((slot) => Date.parse(slot.endsAt) > Date.parse(c.validUntil) || mode === "reschedule" && (slot.id === s.slotId || Date.parse(slot.startsAt) === Date.parse(s.slot?.startsAt) && Date.parse(slot.endsAt) === Date.parse(s.slot?.endsAt)))) invalid();
  if (window && (v.fromDate !== window.day || v.toDate !== nextDay(window.day, 1) || v.timeZone !== window.zone || v.slots.some((slot) => slot.displayTimeZone !== window.zone || slot.displayStartsAt.slice(0, 10) !== window.day))) invalid();
  return v;
}
export function slotOffsets(slot, zone) {
  const format = new Intl.DateTimeFormat("en", { timeZone: zone, timeZoneName: "shortOffset" });
  const offset = (at) => format.formatToParts(new Date(at)).find((p) => p.type === "timeZoneName").value.replace("GMT", "UTC");
  const a = offset(slot.startsAt), b = offset(slot.endsAt); return a === b ? a : `${a} → ${b}`;
}
export function checkedPage(v, key, page = 1, size = 20) {
  checkedScope(v); if (!Array.isArray(v[key]) || !integer(v.total, 0) || v.total < v[key].length || v[key].length > size || v.page !== page || v.pageSize !== size || !instant(v.asOf)) invalid(); return v;
}
export function checkedList(v, kind, page, queue) {
  const key = kind === "hold" ? "holds" : "reservations"; checkedPage(v, key, page);
  if (v.queue !== queue) invalid(); v[key].forEach((r) => { if (!ids[kind].test(r.id) || !integer(r.version) || typeof r.productTitle !== "string" || !Object.hasOwn(kind === "hold" ? holdStates : reservationStates, r.state) || kind === "reservation" && (!Object.hasOwn(paymentStates, r.paymentState) || !Array.isArray(r.sessions) || !integer(r.sessionCount) || !integer(r.consumedSessionCount, 0, r.sessionCount))) invalid(); }); return v;
}
export function checkedReceipt(v, pending) {
  const receipt = v?.receipt; if (!receipt || receipt.requestKey !== pending.requestKey || receipt.command !== pending.command || receipt.entityType !== pending.entityType || pending.entityId && receipt.entityId !== pending.entityId) invalid();
  checkedDetail(v, pending.entityType, { id: receipt.entityId, productId: pending.productId, memberId: pending.memberId }); return v;
}
export function monthDays(day) {
  if (!/^\d{4}-\d\d-\d\d$/.test(day) || nextDay(day, 0) !== day) throw new Error("날짜를 확인해주세요.");
  const first = day.slice(0, 7) + "-01", start = new Date(first + "T12:00:00Z");
  const leading = (start.getUTCDay() + 6) % 7;
  return Array.from({ length: 42 }, (_, i) => ({ day: nextDay(first, i - leading), inMonth: nextDay(first, i - leading).slice(0, 7) === day.slice(0, 7) }));
}
export function shiftMonth(day, offset) { const d = new Date(day.slice(0, 7) + "-01T12:00:00Z"); d.setUTCMonth(d.getUTCMonth() + offset); return d.toISOString().slice(0, 10); }
export function holdSeconds(hold, receivedAt, now) { return Math.max(0, hold.remainingSeconds - Math.floor(Math.max(0, now - receivedAt) / 1000)); }
export const detailPath = (kind, id) => kind === "hold" ? "/reservations/holds/" + id : "/reservations/" + id;
export const apiPath = (kind, id) => "/api/coaching/" + (kind === "hold" ? "holds/" : "reservations/") + id;
