/** One deliberate command; uncertain replies require an original-receipt GET. */
import { checkedReceipt, userError } from "./reservation-model.js";
const pattern = /^[A-Za-z0-9_-]{8,128}$/;
export function createReservationSession({ api, ownerId, scope, initial = null, validate = (v) => v, active = () => true, changed = () => {}, denied = () => {}, storage = null, makeKey = () => crypto.randomUUID() }) {
  const storageKey = `tpc.reservation.command.v1:${ownerId}:${scope}`;
  let alive = true, original = null, pending = null;
  try { const p = JSON.parse(storage?.getItem(storageKey) || "null"); if (p?.ownerId === ownerId && p.scope === scope && pattern.test(p.requestKey) && ["hold", "reservation", "policy"].includes(p.entityType) && typeof p.command === "string") pending = p; } catch {}
  const state = { value: initial, pending, busy: false, needsRefresh: false, notFound: false, receipt: null, error: false, message: pending ? "이 화면의 저장 결과를 먼저 확인해주세요." : "현재 내용을 확인한 뒤 진행해주세요." };
  const owns = () => alive && active();
  const emit = () => { if (owns()) changed(state); };
  function clear() { try { storage?.removeItem(storageKey); } catch {} state.pending = null; original = null; state.notFound = false; }
  function fail(e) { if (!owns()) return false; if (e?.sessionChanged || [401, 403, 404].includes(e?.status)) { clear(); state.value = null; state.receipt = null; alive = false; denied(e); return false; } state.error = true; state.needsRefresh = true; state.message = userError(e); return false; }
  async function send(path, body, result, retry = false) {
    if (!owns() || state.busy || !retry && (state.pending || state.needsRefresh)) return false;
    if (!retry) {
      body = JSON.parse(JSON.stringify({ ...body, requestKey: makeKey() }));
      if (!pattern.test(body.requestKey)) throw new Error("원 요청을 준비할 수 없습니다.");
      original = { path, body, result };
      state.pending = { ownerId, scope, requestKey: body.requestKey, entityType: result.entityType, entityId: result.entityId || null, productId: result.productId, memberId: result.memberId, command: result.command };
      try { storage?.setItem(storageKey, JSON.stringify(state.pending)); } catch {}
    }
    state.busy = true; state.error = false; state.notFound = false; emit();
    try {
      const v = await api(path, { method: "POST", body, ownerId, headers: { "X-TPC-Reservation-Scope": "technical" } }); if (!owns()) return false;
      checkedReceipt(v, state.pending); state.value = v; state.receipt = v.receipt; clear(); state.needsRefresh = false; state.message = "저장 결과와 현재 진행 상태를 확인했습니다."; return true;
    } catch (e) {
      if (!owns()) return false;
      if (e?.sessionChanged || [401, 403, 404].includes(e?.status)) return fail(e);
      if ([400, 413, 415, 422, 429].includes(e?.status)) { clear(); state.error = true; state.message = userError(e); return false; }
      state.error = true; state.needsRefresh = true; state.message = "응답이 확인되지 않았습니다. 새 요청 전에 원 요청 결과를 조회해주세요."; return false;
    } finally { state.busy = false; emit(); }
  }
  async function lookup() {
    if (!owns() || state.busy || !state.pending) return false;
    state.busy = true; emit(); const p = state.pending;
    try { const v = await api("/api/coaching/reservation-commands/" + p.requestKey, { ownerId }); if (!owns()) return false; checkedReceipt(v, p); state.value = v; state.receipt = v.receipt; clear(); state.needsRefresh = false; state.error = false; state.message = "원 요청 결과를 복구했습니다. 현재 상태를 표시합니다."; return true; }
    catch (e) { if (!owns()) return false; if (e?.status === 404 && !e.sessionChanged) { state.notFound = true; state.message = "원 요청 기록이 없습니다. 같은 요청을 직접 재시도하거나 미기록 요청을 정리한 뒤 현재 내용을 조회해주세요."; return false; } return fail(e); }
    finally { state.busy = false; emit(); }
  }
  async function refresh(path) {
    if (!owns() || state.busy || state.pending) return false; state.busy = true; emit();
    try { const v = typeof path === "function" ? await path() : await api(path, { ownerId }); if (!owns()) return false; state.value = validate(v); state.needsRefresh = false; state.error = false; state.message = "현재 내용을 다시 조회했습니다. 입력을 새로 확인해주세요."; return true; }
    catch (e) { return fail(e); } finally { state.busy = false; emit(); }
  }
  const retry = () => owns() && state.notFound && original && !state.busy ? send(original.path, original.body, original.result, true) : false;
  function release() { if (!owns() || state.busy || !state.notFound) return false; clear(); state.needsRefresh = true; state.message = "미기록 요청을 정리했습니다. 현재 내용을 다시 조회한 뒤 진행해주세요."; emit(); return true; }
  return { state, send, lookup, retry, refresh, release, canRetry: () => Boolean(original && state.notFound), paint: emit, dispose() { alive = false; original = null; state.value = null; state.receipt = null; state.pending = null; } };
}
