/** Safe operator DTOs, stable navigation, key-only recovery and bounded export. */
export const deskTabs = { attention: "확인 필요", active: "조회 진행", matched: "일치 확인", events: "수신 사건", mine: "내 담당 주문", all: "전체 대상" };
export const caseStates = { unclaimed: "담당 미지정", open: "검토 중", investigating: "별도 조사", resolved: "검토 종결" };
export const caseActions = { claim: "내 담당으로 시작", takeover: "내 담당으로 인계", release: "담당 해제", note: "검토 메모 저장", investigate: "별도 조사로 기록", resolve: "현재 근거로 검토 종결", reopen: "검토 다시 시작" };
export const issueLabels = { amount: "금액·통화 확인", reference: "참조·식별 확인", permission: "권한 연결 확인", exhausted: "기술 시도 소진", pending: "결과 대기", configuration: "상점·조회 환경", partial: "부분 취소 조사", conflict: "상충 기록", changed: "새 근거 재확인", unlinked: "미연결 사건", authentication: "수신 인증 미기록" };
export const jobStates = { queued: "조회 대기", running: "조회 중", retry: "재시도 대기", review: "운영 확인", matched: "조회 일치" };
export const outcomeLabels = {
  pg_match: "조회 식별·금액 검증", lookup_timeout: "조회 응답 기한 초과", lookup_error: "조회 연결 확인", scope_mismatch: "상점·환경 불일치", reference_mismatch: "주문 참조 불일치", amount_mismatch: "금액 불일치", currency_mismatch: "통화 불일치", payment_key_mismatch: "결제 식별 불일치", order_changed: "조회 중 주문 변경", unsupported_status: "상태 확인 필요", lease_expired: "실행권 기한 만료",
  payment_verified: "승인·결제 출처 권한 반영", payment_current: "현재 승인 기록 일치", access_reconciled: "결제 출처 연결 확인", cancellation_verified: "원 취소 작업 확인·반영", cancellation_current: "현재 전액 취소 기록 일치", external_cancellation_verified: "별도 조회의 전액 취소 반영", unpaid_terminal: "미승인 요청의 종료 확인", unpaid_closed: "닫힌 요청의 현재 상태 확인", provider_pending: "결제사 결과 대기", refund_pending: "원 취소 결과 대기", partial_cancellation: "부분 취소 별도 조사", provider_state_conflict: "기록된 상태와 상충", missing_purchase_evidence: "당시 구매 근거 확인", lookup_failed: "조회 연결 확인", lookup_unavailable: "조회 자격 준비 필요", configuration_changed: "조회 중 상점 설정 변경", identity_mismatch: "조회 식별·금액 불일치", notice_secret_mismatch: "입금 통지 참조 불일치", notice_key_mismatch: "통지 식별 참조 불일치", notice_auth_missing: "수신 인증 근거 미기록", apply_failed: "업무 반영 보류·전체 롤백",
};
export const timelineLabels = { scope: "상점 범위 관찰", receipt: "외부 통지 수신", link: "원 주문 참조 연결", work: "영속 조회 작업", query_started: "조회 시도 시작", query_finished: "조회 시도 결과", financial_record: "당시 금융 반영 기록", operator_review: "운영 검토 기록", content_conflict: "같은 사건의 내용 충돌" };
const own = (value, key) => Object.prototype.hasOwnProperty.call(value || {}, key);
const str = (v, max = 2000) => typeof v === "string" && v.length <= max;
const id = (v) => typeof v === "string" && /^[a-z0-9_-]{1,120}$/.test(v);
const int = (v, minimum = 0) => Number.isSafeInteger(v) && v >= minimum;
const digest = (v) => typeof v === "string" && /^[a-f0-9]{64}$/.test(v);
const nullable = (v, predicate) => v === null || predicate(v);
const fail = () => { throw new Error("대사 기록의 대상·판본을 확인하지 못했습니다. 현재 기록을 다시 불러와주세요."); };

export function deskAPI(kind, subjectId) {
  if (!id(subjectId) || !["order", "event"].includes(kind)) fail();
  return kind === "order" ? `/api/manage/purchases/${encodeURIComponent(subjectId)}/reconciliation/desk` : `/api/manage/reconciliation/desk/events/${encodeURIComponent(subjectId)}`;
}
export function deskSubjectHref(kind, subjectId) {
  deskAPI(kind, subjectId);
  return `#/manage/reconciliation/${kind}/${encodeURIComponent(subjectId)}`;
}
export function deskQuery(params = new URLSearchParams()) {
  const value = {};
  for (const key of ["tab", "mode", "issue", "review", "owner", "q", "before", "limit"]) {
    if (params.getAll(key).length > 1) fail();
    if (params.has(key)) value[key] = params.get(key);
  }
  value.tab ||= "attention"; value.mode ||= "all"; value.issue ||= "all"; value.review ||= "all"; value.owner ||= "all"; value.q ||= "";
  value.limit = Number(value.limit || 30);
  if (!own(deskTabs, value.tab) || !["all", "test", "live"].includes(value.mode) || !(value.issue === "all" || own(issueLabels, value.issue)) || !["all", "unassigned", "open", "investigating", "resolved", "changed"].includes(value.review) || !["all", "mine", "unassigned"].includes(value.owner) || (value.q && !/^[A-Za-z0-9_-]{1,120}$/.test(value.q)) || !int(value.limit, 1) || value.limit > 50) fail();
  if (value.before !== undefined && !checkedRowCursor(value.before)) fail();
  return value;
}
export function checkedRowCursor(value) {
  if (typeof value !== "string" || !/^-?[0-9]{1,19}$/.test(value)) return false;
  const n = BigInt(value); return n >= -(2n ** 63n) && n <= 2n ** 63n - 1n;
}
export function deskQueryString(query, patch = {}) {
  const next = { ...query, ...patch };
  if (Object.keys(patch).some((key) => key !== "before")) delete next.before;
  const checked = deskQuery(new URLSearchParams(Object.entries(next).filter(([, v]) => v !== undefined && v !== null).map(([k, v]) => [k, String(v)])));
  return new URLSearchParams(Object.entries(checked).filter(([k, v]) => k !== "q" || v).map(([k, v]) => [k, String(v)])).toString();
}
export function deskQueueHref(query, patch = {}) { return "#/manage/reconciliation?" + deskQueryString(query, patch); }

function checkedCase(value, detailed) {
  if (!value || !int(value.version) || !own(caseStates, value.state) || typeof value.changed !== "boolean" || typeof value.ownedByMe !== "boolean" || !nullable(value.id, id)) fail();
  if (value.state === "unclaimed" ? value.version !== 0 || value.id !== null || value.owner !== null : value.version < 1 || !id(value.id)) fail();
  if (value.owner && (!id(value.owner.id) || !nullable(value.owner.name, str) || typeof value.owner.active !== "boolean")) fail();
  if (detailed && (typeof value.reviewAllowed !== "boolean" || !Array.isArray(value.allowedActions) || value.allowedActions.some((action) => !own(caseActions, action)) || (!value.reviewAllowed && value.allowedActions.length))) fail();
  return value;
}
function checkedSubject(value, kind, subjectId, detailed) {
  if (!value || !["order", "event"].includes(value.kind) || !id(value.id) || (kind && value.kind !== kind) || (subjectId && value.id !== subjectId) || !["test", "live"].includes(value.mode) || !digest(value.contextHash) || !Array.isArray(value.issues) || value.issues.some((issue) => !own(issueLabels, issue))) fail();
  checkedCase(value.case, detailed);
  if (value.kind === "order") {
    if (!str(value.title) || !id(value.courseId) || !int(value.amount) || value.currency !== "KRW" || !int(value.version, 1) || !["created", "pending", "confirming", "paid", "refunding", "refunded", "abandoned"].includes(value.status) || typeof value.financialAligned !== "boolean" || !value.scope || typeof value.scope.recorded !== "boolean" || typeof value.scope.matchesCurrent !== "boolean") fail();
    if (!value.access || typeof value.access.canAccess !== "boolean" || !int(value.access.wrongParentCount)) fail();
    for (const group of [value.access.payment, value.access.other]) if (!group || ["total", "active", "scheduled", "expired", "revoked"].some((key) => !int(group[key]))) fail();
    const q = value.query;
    if (!q || !int(q.activeCount) || !int(q.totalJobs) || !int(q.totalAttempts)) fail();
    if (q.lastJob && (!id(q.lastJob.id) || !own(jobStates, q.lastJob.state) || !int(q.lastJob.version, 1) || !int(q.lastJob.attempts) || !int(q.lastJob.maxAttempts, 1))) fail();
    if (q.lastObservation && (!id(q.lastObservation.attemptId) || !id(q.lastObservation.jobId) || !str(q.lastObservation.code, 80) || !nullable(q.lastObservation.detail, (v) => str(v, 80)) || !nullable(q.lastObservation.providerStatus, (v) => str(v, 80)))) fail();
    if (detailed && (!value.queryControl || typeof value.queryControl.available !== "boolean" || !Array.isArray(value.queryControl.blockers) || typeof value.queryControl.requiresScopeAcknowledgment !== "boolean" || !["payment", "order"].includes(value.queryControl.lookupBy) || (value.queryControl.available && (q.activeCount || value.queryControl.blockers.length)))) fail();
  } else if (!str(value.eventType, 80) || !value.authentication || typeof value.authentication.recorded !== "boolean" || typeof value.hasOrderReference !== "boolean" || !int(value.conflicts) || (value.linkedOrder && (!id(value.linkedOrder.id) || !str(value.linkedOrder.title)))) fail();
  if (detailed && (typeof value.financialAligned !== "boolean" || (!value.financialAligned && value.case.allowedActions.includes("resolve")))) fail();
  return value;
}
function checkedConfiguration(value) {
  if (!value || value.provider !== "toss" || !["unconfigured", "test", "live"].includes(value.mode) || ["merchantConfigured", "webhookConfigured", "paymentLookupSupported", "orderLookupSupported", "workerRunning"].some((key) => typeof value[key] !== "boolean")) fail();
  return value;
}
function checkedPage(page, timeline = false) {
  if (!page || !int(page.limit, 1) || page.limit > 50 || !nullable(page.nextBefore, (v) => timeline ? str(v, 128) && /^[A-Za-z0-9_-]{4,128}$/.test(v) : checkedRowCursor(v))) fail();
}
export function checkedDeskQueue(data, ownerId) {
  if (!data || data.schemaVersion !== 1 || data.viewerId !== ownerId || !int(data.observedAt) || !Array.isArray(data.items) || data.items.length > 50 || !data.counts || ["total", "attention", "active", "matched", "mine", "events"].some((key) => !int(data.counts[key]))) fail();
  checkedConfiguration(data.configuration); checkedPage(data.page);
  data.items.forEach((item) => checkedSubject(item));
  if (new Set(data.items.map((i) => i.kind + ":" + i.id)).size !== data.items.length) fail();
  return data;
}
export function checkedDeskDetail(data, ownerId, kind, subjectId) {
  if (!data || data.schemaVersion !== 1 || data.viewerId !== ownerId || !int(data.observedAt)) fail();
  checkedConfiguration(data.configuration); checkedSubject(data.subject, kind, subjectId, true);
  const t = data.timeline;
  if (!t || !Array.isArray(t.items) || t.items.length > 50 || t.coverage !== "recorded_observations_only") fail();
  checkedPage(t.page, true);
  for (const item of t.items) if (!item || !str(item.id, 256) || !own(timelineLabels, item.kind) || !nullable(item.recordedAt, (v) => Number.isSafeInteger(v)) || !item.facts || typeof item.facts !== "object" || Array.isArray(item.facts)) fail();
  if (new Set(t.items.map((i) => i.id)).size !== t.items.length) fail();
  return data;
}
export function deskSnapshotKey(value) { return value.subject.contextHash + ":" + value.subject.case.version; }
export function deskTime(value) {
  if (value === null || value === undefined) return "시각 미기록";
  const date = new Date(typeof value === "number" ? value * 1000 : value);
  return Number.isNaN(date.getTime()) ? "시각 미기록" : new Intl.DateTimeFormat("ko-KR", { timeZone: "Asia/Seoul", dateStyle: "short", timeStyle: "medium" }).format(date);
}
export function deskError(error) {
  if (error?.sessionChanged || error?.status === 401) return "운영 계정으로 다시 접속한 뒤 현재 기록을 확인해주세요.";
  if (error?.status === 403) return "현재 운영 권한으로 이 기록을 확인할 수 없습니다.";
  if (error?.status === 409) return "담당·판본·업무 근거가 바뀌었습니다. 현재 내용을 다시 비교해주세요.";
  if (error?.status === 404) return "현재 확인할 수 있는 대사 기록이 없습니다.";
  return "연결 결과를 확인하지 못했습니다. 현재 기록이나 원 요청의 저장 결과부터 확인해주세요.";
}
export function timelineGroup(item) { return item.kind === "operator_review" ? "review" : item.kind === "financial_record" ? "financial" : ["work", "query_started", "query_finished"].includes(item.kind) ? "query" : "receipt"; }

const recoveryPrefix = "tpc:reconciliation:1:";
const storageKey = (owner, kind, subject) => recoveryPrefix + owner + ":" + kind + ":" + subject;
export function recoveryOf(ownerId, kind, subjectId, type, body, now = Date.now()) {
  if (!id(ownerId) || !id(subjectId) || !["order", "event"].includes(kind) || !["case", "query"].includes(type) || !/^[A-Za-z0-9_-]{12,120}$/.test(body.requestKey) || !int(now)) fail();
  if (type === "case" ? !own(caseActions, body.action) || !int(body.version) : kind !== "order" || !int(body.expectedOrderVersion, 1)) fail();
  return { schemaVersion: 1, ownerId, subjectKind: kind, subjectId, type, requestKey: body.requestKey, at: now,
    ...(type === "case" ? { action: body.action, version: body.version, contextHash: body.contextHash } : { expectedOrderVersion: body.expectedOrderVersion, ...(body.jobId ? { jobId: body.jobId, expectedJobVersion: body.expectedJobVersion } : {}) }) };
}
export function rememberDeskRecovery(storage, value) { try { storage?.setItem(storageKey(value.ownerId, value.subjectKind, value.subjectId), JSON.stringify(value)); } catch {} }
export function clearDeskRecovery(storage, owner, kind, subject) { try { storage?.removeItem(storageKey(owner, kind, subject)); } catch {} }
export function readDeskRecovery(storage, owner, kind, subject, now = Date.now()) {
  try {
    const raw = JSON.parse(storage?.getItem(storageKey(owner, kind, subject)) || "null");
    if (!raw) return null;
    if (raw.ownerId !== owner || raw.subjectKind !== kind || raw.subjectId !== subject || raw.schemaVersion !== 1 || !int(raw.at) || raw.at > now + 60000 || now - raw.at > 86400000) throw new Error();
    const body = { requestKey: raw.requestKey, action: raw.action, version: raw.version, contextHash: raw.contextHash, expectedOrderVersion: raw.expectedOrderVersion, jobId: raw.jobId, expectedJobVersion: raw.expectedJobVersion };
    const safe = recoveryOf(owner, kind, subject, raw.type, body, raw.at);
    if (safe.type === "case" && !digest(safe.contextHash)) throw new Error();
    if (safe.jobId && (!id(safe.jobId) || !int(safe.expectedJobVersion, 1))) throw new Error();
    return safe;
  } catch { clearDeskRecovery(storage, owner, kind, subject); return null; }
}
export function checkedDeskReceipt(data, pending, fromLookup = false) {
  if (!data || (fromLookup && (data.schemaVersion !== 1 || data.viewerId !== pending.ownerId || data.kind !== pending.type || data.subjectKind !== pending.subjectKind || data.subjectId !== pending.subjectId || typeof data.found !== "boolean"))) fail();
  if (fromLookup && !data.found) { if (data.receipt !== null) fail(); return null; }
  const r = data.receipt;
  if (!r || !int(r.createdAt)) fail();
  if (pending.type === "case") {
    if (!id(r.id) || !id(r.caseId) || r.subjectKind !== pending.subjectKind || r.subjectId !== pending.subjectId || r.action !== pending.action || r.version !== pending.version + 1) fail();
  } else if (r.orderId !== pending.subjectId || !id(r.jobId) || r.expectedOrderVersion !== pending.expectedOrderVersion || (pending.jobId && (r.priorJobId !== pending.jobId || r.expectedJobVersion !== pending.expectedJobVersion)) || !data.job || data.job.id !== r.jobId || data.job.orderId !== pending.subjectId) fail();
  return r;
}
function csvCell(value) {
  let text = String(value ?? "");
  if (/^[\s]*[=+\-@\t\r]/.test(text)) text = "'" + text;
  return '"' + text.replaceAll('"', '""') + '"';
}
export function deskCSV(data) {
  const rows = [["현재 내려받은 목록만", "전체 페이지 내보내기 아님", "조회 시각", new Date(data.observedAt * 1000).toISOString()],
    ["종류", "내부 ID", "환경", "강의·유형", "현재 주문", "최근 조회", "확인 유형", "운영 검토", "담당 내부 ID"]];
  for (const item of data.items) rows.push([item.kind === "order" ? "주문" : "수신 사건", item.id, item.mode, item.kind === "order" ? item.title : item.eventType, item.status || "", item.query?.lastObservation?.state || "", item.issues.map((i) => issueLabels[i]).join(" · "), caseStates[item.case.state], item.case.owner?.id || ""]);
  return "\uFEFF" + rows.map((row) => row.map(csvCell).join(",")).join("\r\n");
}
