/** Explicit operator commands. Recovery reads receipts; it never retries a POST. */
import {
  caseActions, checkedDeskDetail, checkedDeskReceipt, clearDeskRecovery,
  deskAPI, deskError, deskSnapshotKey, readDeskRecovery, recoveryOf, rememberDeskRecovery,
} from "./reconciliation-model.js";

export function createDeskSession({ api, ownerId, kind, subjectId, initial, storage = null,
  active = () => true, changed = () => {}, denied = () => {}, makeKey = () => crypto.randomUUID() }) {
  const base = deskAPI(kind, subjectId);
  const value = checkedDeskDetail(initial, ownerId, kind, subjectId);
  const state = { value, items: [...value.timeline.items], nextBefore: value.timeline.page.nextBefore,
    busy: null, pending: readDeskRecovery(storage, ownerId, kind, subjectId), notFound: false, retryable: false,
    receipt: null, error: false, needsRefresh: false, historical: false, resetChecks: false, clearDraft: 0,
    message: "현재 서버의 관찰 기록입니다. 새로고침은 금융 조회나 검토 저장을 실행하지 않습니다." };
  let alive = true, originalBody = null;
  const owns = () => alive && active();
  const emit = () => { if (owns()) changed(state); };
  function clearPending() {
    clearDeskRecovery(storage, ownerId, kind, subjectId);
    state.pending = null; state.notFound = false; state.retryable = false; originalBody = null;
  }
  function deny(error) {
    const visible = owns();
    clearPending(); state.value = null; state.items = []; state.nextBefore = null; state.receipt = null; state.busy = null;
    alive = false;
    if (visible) denied(error);
  }
  function failure(error) {
    if (error?.sessionChanged || [401, 403, 404].includes(error?.status)) { deny(error); return false; }
    state.error = true; state.needsRefresh = true;
    state.message = deskError(error); return false;
  }
  function accept(data) {
    const next = checkedDeskDetail(data, ownerId, kind, subjectId);
    if (state.value && deskSnapshotKey(next) !== deskSnapshotKey(state.value)) state.resetChecks = true;
    state.value = next; state.items = [...next.timeline.items]; state.nextBefore = next.timeline.page.nextBefore;
    state.historical = false; state.needsRefresh = false;
  }
  async function readCurrent() {
    const data = await api(base, { ownerId });
    if (!owns()) return false;
    accept(data); return true;
  }
  async function refresh() {
    if (!owns() || state.busy) return false;
    state.busy = "read"; emit();
    try {
      if (!await readCurrent()) return false;
      state.error = false;
      state.message = state.pending ? "현재 기록을 다시 읽었습니다. 원 요청의 저장 결과도 별도로 확인해주세요." : "현재 주문·조회·권한과 검토 기록을 다시 확인했습니다.";
      return true;
    } catch (error) { return failure(error); }
    finally { state.busy = null; emit(); }
  }
  const originalMatchesCurrent = () => {
    if (!originalBody || !state.value) return false;
    const s = state.value.subject;
    return state.pending?.type === "case" ? s.contextHash === originalBody.contextHash && s.case.version === originalBody.version
      : s.version === originalBody.expectedOrderVersion && s.queryControl.available && (!originalBody.jobId || (s.query.lastJob?.id === originalBody.jobId && s.query.lastJob.version === originalBody.expectedJobVersion));
  };
  async function finishReceipt(receipt, type) {
    state.receipt = receipt; clearPending(); state.clearDraft++; state.resetChecks = true; state.needsRefresh = true;
    try {
      if (!await readCurrent()) return false;
      state.error = false;
      state.message = type === "query" ? "원 주문의 조회 요청을 저장했습니다. 실제 처리 결과는 이 기록에서 확인할 수 있습니다." : "운영 검토 기록을 저장하고 현재 내용을 다시 확인했습니다.";
    } catch (error) {
      if (!failure(error)) {
        if (owns()) state.message = "원 요청의 저장은 확인했습니다. 현재 기록을 읽지 못했으므로 새 실행 전에 다시 조회해주세요.";
      }
    }
    return true;
  }
  async function send(type, body, pending) {
    state.pending = pending; originalBody = body;
    state.notFound = false; state.retryable = false; state.busy = type; state.error = false;
    rememberDeskRecovery(storage, pending); emit();
    try {
      const result = await api(base + (type === "case" ? "/actions" : "/query"), { method: "POST", ownerId, body });
      if (!owns()) return false;
      const receipt = checkedDeskReceipt(result, pending);
      return await finishReceipt(receipt, type);
    } catch (error) {
      if (error?.sessionChanged || [401, 403].includes(error?.status)) { deny(error); return false; }
      if (!owns()) return false;
      if ([400, 413, 415, 422, 429].includes(error?.status)) {
        clearPending(); state.error = true;
        state.message = [400, 429].includes(error.status) && typeof error.message === "string" ? error.message.slice(0, 500) : deskError(error);
        return false;
      }
      failure(error);
      if (owns()) state.message = error?.status === 409 ? deskError(error) + " 원 요청의 저장 결과부터 확인해주세요." : "응답을 확인하지 못했습니다. 새 요청을 만들기 전에 원 요청의 저장 영수증부터 확인해주세요.";
      return false;
    } finally { state.busy = null; emit(); }
  }
  async function submit(type, fields) {
    if (!owns() || state.busy || state.pending || state.needsRefresh) return false;
    const s = state.value.subject;
    let body;
    if (type === "case") {
      if (!Object.hasOwn(caseActions, fields.action) || !s.case.allowedActions.includes(fields.action) || !fields.confirm) return false;
      const note = String(fields.note || "").trim(); if (note.length < 10 || note.length > 1500) return false;
      body = { requestKey: makeKey(), version: s.case.version, contextHash: s.contextHash, action: fields.action, note };
    } else if (type === "query") {
      if (kind !== "order" || !s.queryControl.available || !fields.confirm || (s.queryControl.requiresScopeAcknowledgment && !fields.acknowledgeScope)) return false;
      const reason = String(fields.reason || "").trim(); if (reason.length < 10 || reason.length > 1000) return false;
      const prior = s.query.lastJob;
      body = { requestKey: makeKey(), expectedOrderVersion: s.version, reason,
        acknowledgeCurrentScope: s.queryControl.requiresScopeAcknowledgment && Boolean(fields.acknowledgeScope),
        ...(prior ? { jobId: prior.id, expectedJobVersion: prior.version } : {}) };
    } else return false;
    return send(type, body, recoveryOf(ownerId, kind, subjectId, type, body));
  }
  async function lookup() {
    if (!owns() || state.busy || !state.pending) return false;
    const pending = state.pending; state.busy = "receipt"; emit();
    try {
      const result = await api(base + "/receipt?" + new URLSearchParams({ kind: pending.type, requestKey: pending.requestKey }), { ownerId });
      if (!owns()) return false;
      const receipt = checkedDeskReceipt(result, pending, true);
      if (receipt) return await finishReceipt(receipt, pending.type);
      state.notFound = true; state.retryable = false;
      if (!await readCurrent()) return false;
      state.retryable = originalMatchesCurrent(); state.error = false;
      state.message = state.retryable ? "원 요청은 아직 기록되지 않았고 업무 근거도 같습니다. 원래 요청을 직접 다시 보낼 수 있습니다." : "원 요청이 기록되지 않은 것을 확인했습니다. 현재 근거로 검토하려면 이 요청을 정리한 뒤 다시 작성해주세요.";
      return false;
    } catch (error) { return failure(error); }
    finally { state.busy = null; emit(); }
  }
  async function retry() {
    if (!owns() || state.busy || !state.pending || !state.notFound || !state.retryable || state.needsRefresh || !originalMatchesCurrent()) return false;
    return send(state.pending.type, originalBody, state.pending);
  }
  function release() {
    if (!owns() || state.busy || !state.notFound || !state.pending) return false;
    clearPending(); state.resetChecks = true;
    state.message = "미기록 요청을 정리했습니다. 현재 근거를 확인하고 새 검토 내용을 직접 작성해주세요."; state.error = false; emit(); return true;
  }
  async function more() {
    if (!owns() || state.busy || !state.nextBefore || state.pending) return false;
    const before = state.nextBefore, prior = deskSnapshotKey(state.value);
    state.busy = "history"; emit();
    try {
      const result = await api(base + "?" + new URLSearchParams({ before, limit: "30" }), { ownerId });
      if (!owns()) return false;
      const next = checkedDeskDetail(result, ownerId, kind, subjectId);
      if (deskSnapshotKey(next) !== prior) {
        if (!await readCurrent()) return false;
        state.resetChecks = true; state.message = "기록이 바뀌어 최신 구간으로 돌아왔습니다. 현재 근거를 다시 확인해주세요.";
      } else {
        const seen = new Set(state.items.map((r) => r.id));
        const extra = next.timeline.items.filter((r) => !seen.has(r.id));
        if (state.items.length + extra.length > 200) { state.items = [...next.timeline.items]; state.historical = true; }
        else state.items.push(...extra);
        state.nextBefore = next.timeline.page.nextBefore;
        state.message = state.historical ? "과거 이력 구간입니다. 현재 기록 다시 조회로 최신 구간을 볼 수 있습니다." : "이전 관찰 기록을 이어서 불러왔습니다.";
      }
      state.error = false; return true;
    } catch (error) { return failure(error); }
    finally { state.busy = null; emit(); }
  }
  function dispose() { alive = false; originalBody = null; state.value = null; state.items = []; state.pending = null; state.receipt = null; }
  return { state, refresh, submit, lookup, retry, release, more, dispose };
}
