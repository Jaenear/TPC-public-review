/** One explicit command at a time. Lost replies are recovered by GET, never auto-posted. */
import { studioBase, rootOf, coachingError, checkedDetail, productPattern } from "./coaching-model.js";
const keyFor = (owner, scope) => `tpc.coaching.command.v1:${owner}:${scope}`;
export function createCoachingSession({ api, ownerId, scope, initial, active = () => true, changed = () => {}, denied = () => {}, storage = null, makeKey = () => crypto.randomUUID() }) {
  const storageKey = keyFor(ownerId, scope);
  const kind = Object.hasOwn(initial, "product") ? "product" : "calendar", expectedId = initial[kind]?.id;
  function validate(value, receiptKey = null) {
    const root = value?.[kind];
    if (!root && kind === "product" && !expectedId && !receiptKey && value?.context?.coachId === initial.context.coachId) return value;
    checkedDetail(value, kind, expectedId || root?.id);
    if (value.context.coachId !== initial.context.coachId || (kind === "product" && !productPattern.test(root?.id))) throw new Error("현재 화면과 다른 원문입니다.");
    if (receiptKey && (value.receipt?.requestKey !== receiptKey || value.receipt.entityType !== kind || value.receipt.entityId !== root.id)) throw new Error("원 요청과 다른 영수증입니다.");
    return value;
  }
  let alive = true, original = null;
  let pending = null;
  try { const saved = JSON.parse(storage?.getItem(storageKey) || "null"); if (saved?.ownerId === ownerId && saved.scope === scope && /^[A-Za-z0-9_-]{8,128}$/.test(saved.requestKey)) pending = saved; } catch { /* Storage is optional; no body or operator notes are persisted. */ }
  const state = { value: initial, pending, busy: false, needsRefresh: false, notFound: false, message: pending ? "이 화면의 원 요청이 남아 있습니다. 저장 결과부터 확인해주세요." : "현재 원문을 확인한 뒤 변경을 저장해주세요.", error: false, receipt: null };
  const owns = () => alive && active();
  const emit = () => { if (owns()) changed(state); };
  function clear() { try { storage?.removeItem(storageKey); } catch {} state.pending = null; original = null; state.notFound = false; }
  function fail(error) {
    if (!owns()) return false;
    if (error?.sessionChanged || [401, 403, 404].includes(error?.status)) { clear(); state.value = null; alive = false; denied(error); return false; }
    state.error = true; state.needsRefresh = true; state.message = coachingError(error); return false;
  }
  async function send(path, method, body, retry = false) {
    if (!owns() || state.busy || (!retry && (state.pending || state.needsRefresh))) return false;
    if (!retry) {
      body = { ...body, requestKey: makeKey() }; original = { path, method, body };
      state.pending = { ownerId, scope, requestKey: body.requestKey };
      try { storage?.setItem(storageKey, JSON.stringify(state.pending)); } catch {}
    }
    state.busy = true; state.error = false; state.notFound = false; emit();
    try {
      const result = await api(path, { method, body, ownerId }); if (!owns()) return false;
      validate(result, body.requestKey);
      state.value = result; state.receipt = result.receipt; clear(); state.needsRefresh = false;
      state.message = result.replayed ? "원 요청의 저장 결과와 현재 판본을 확인했습니다." : "변경을 저장하고 현재 판본을 확인했습니다."; return true;
    } catch (error) {
      if (!owns()) return false;
      if (error?.sessionChanged || [401, 403, 404].includes(error?.status)) return fail(error);
      if ([400, 413, 415, 422, 429].includes(error?.status)) { clear(); state.error = true; state.message = coachingError(error); return false; }
      state.error = true; state.needsRefresh = true; state.message = "응답이 확인되지 않았습니다. 새 변경 전에 원 요청의 저장 결과를 조회해주세요."; return false;
    } finally { state.busy = false; emit(); }
  }
  async function lookup() {
    if (!owns() || state.busy || !state.pending) return false;
    state.busy = true; emit(); const key = state.pending.requestKey;
    try {
      const result = await api(studioBase + "/commands/" + key, { ownerId }); if (!owns()) return false;
      validate(result, key);
      state.value = result; state.receipt = result.receipt; clear(); state.needsRefresh = false; state.error = false;
      state.message = "원 요청의 저장 결과를 복구했습니다. 현재 판본을 표시합니다."; return true;
    } catch (error) {
      if (!owns()) return false;
      if (error?.status === 404 && !error.sessionChanged) { state.notFound = true; state.message = "본인의 원 요청 기록이 없습니다. 원 요청을 직접 다시 보내거나, 현재 원문을 다시 연 뒤 새로 작성해주세요."; return false; }
      return fail(error);
    } finally { state.busy = false; emit(); }
  }
  async function retry() { if (!state.notFound || !original || !owns() || state.busy) return false; return send(original.path, original.method, original.body, true); }
  async function refresh(path) {
    if (!owns() || state.busy || state.pending) return false;
    state.busy = true; emit();
    try { const value = await api(path, { ownerId }); if (!owns()) return false; state.value = validate(value); state.needsRefresh = false; state.error = false; state.message = "현재 판본을 다시 불러왔습니다. 저장하지 않은 입력은 적용되지 않았습니다."; return true; }
    catch (e) { return fail(e); } finally { state.busy = false; emit(); }
  }
  function release() { if (!owns() || state.busy || !state.notFound) return false; clear(); state.needsRefresh = true; state.message = "미기록 요청을 정리했습니다. 현재 원문을 다시 조회한 뒤 작성해주세요."; emit(); return true; }
  return { state, send, lookup, retry, refresh, release, canRetry: () => Boolean(original && state.notFound), dispose() { alive = false; original = null; state.value = null; state.receipt = null; state.pending = null; } };
}
