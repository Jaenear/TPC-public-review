/** One deliberate write. Persist only a bound key; private bodies stay in memory. */
import { checkedReceipt, checkedCommandSize, keyID, reviewID, assetID, reviewError } from './hand-review-model.js';
const commands = new Set(['create', 'adopt', 'revise', 'cancel', 'assign', 'unassign', 'start', 'request-information', 'answer', 'attachment.upload', 'attachment.inspect', 'attachment.block', 'attachment.delete', 'attachment.cancel', 'attachment.expire']);

export function createHandReviewSession({ api, ownerId, scope, initial, validate, guard, active = () => true, changed = () => {}, denied = () => {}, storage = null, makeKey = () => crypto.randomUUID() }) {
  const journal = `tpc.hand-review.command.v1:${ownerId}:${scope}`;
  let alive = true, original = null, pending = null;
  try {
    const saved = JSON.parse(storage?.getItem(journal) || 'null');
    const bound = scope === 'new' ? saved?.command === 'create' && saved.reviewId === null : saved?.reviewId === scope.slice('review:'.length);
    const validVersion = (v) => v === undefined || v === null || Number.isSafeInteger(v) && v >= 1;
    if (bound && saved?.ownerId === ownerId && saved.scope === scope && keyID.test(saved.requestKey) && commands.has(saved.command) && (saved.reviewId === null || reviewID.test(saved.reviewId)) && (!saved.attachmentId || assetID.test(saved.attachmentId)) && validVersion(saved.baseVersion) && validVersion(saved.attachmentVersion)) pending = { ownerId, scope, requestKey: saved.requestKey, command: saved.command, reviewId: saved.reviewId, attachmentId: saved.attachmentId || null, baseVersion: saved.baseVersion, attachmentVersion: saved.attachmentVersion ?? undefined };
  } catch { /* The journal is optional; no private draft is reconstructed. */ }
  const state = { value: initial, pending, busy: false, needsRefresh: false, notFound: false, canRetry: false, receipt: null, error: false, message: pending ? '이 화면의 이전 저장 결과부터 확인해주세요.' : '내용을 확인한 뒤 직접 저장해주세요.' };
  const owns = () => alive && active();
  const paint = () => { if (owns()) changed(state); };
  function clear() { try { storage?.removeItem(journal); } catch {} state.pending = null; original = null; state.notFound = false; state.canRetry = false; }
  function deny(e) { clear(); state.value = null; state.receipt = null; alive = false; denied(e); }
  function reject(e) {
    if (!owns()) return false;
    if (e?.sessionChanged || [401, 403, 404].includes(e?.status)) { deny(e); return false; }
    state.error = true; state.needsRefresh = true; state.message = reviewError(e); paint(); return false;
  }
  function conflict(message) { const e = new Error(message); e.status = 409; return e; }
  async function access(body, command, recovery = false) {
    const fresh = await guard();
    if (!owns()) return null;
    validate(fresh);
    if (fresh?.review && !recovery) {
      if (body?.version !== undefined && fresh.review.version !== body.version) throw conflict('현재 판본이 변경됐습니다.');
      if (command === 'adopt' && fresh.review.legacyHash !== body.legacyHash) throw conflict('기존 원문이 변경됐습니다.');
      if (command === 'attachment.upload' && fresh.scope.attachmentPolicy?.policyHash !== body.policyHash) throw conflict('자료 제출 기준이 변경됐습니다.');
    }
    return fresh;
  }
  async function send(path, body, intent, upload = null, retry = false) {
    if (!owns() || state.busy || !retry && (state.pending || state.needsRefresh)) return false;
    state.busy = true; state.error = false; state.notFound = false; state.canRetry = false; paint();
    let posted = false;
    try {
      const submitted = retry ? original?.body : JSON.parse(JSON.stringify(body));
      const purpose = { ...intent };
      const capturedUpload = upload ? { ...upload, headers: { ...upload.headers } } : null;
      if (!commands.has(purpose.command)) throw new Error('저장 동작을 확인해주세요.');
      await access(submitted, purpose.command); if (!owns()) return false;
      if (!retry) {
        const key = makeKey(); if (!keyID.test(key)) throw new Error('저장 요청을 준비할 수 없습니다.');
        const frozen = { ...submitted, requestKey: key };
        if (!capturedUpload) checkedCommandSize(frozen);
        state.pending = { ownerId, scope, requestKey: key, command: purpose.command, reviewId: purpose.reviewId ?? null, attachmentId: purpose.attachmentId ?? null, baseVersion: submitted.version ?? null, attachmentVersion: submitted.attachmentVersion };
        original = { path, body: frozen, intent: purpose, upload: capturedUpload };
        try { storage?.setItem(journal, JSON.stringify(state.pending)); } catch {}
      }
      const saved = original;
      if (!saved || !state.pending) throw new Error('이 탭에 원래 입력 내용이 없습니다. 저장 결과를 조회해주세요.');
      posted = true; paint();
      const options = saved.upload
        ? { rawBody: saved.upload.body, headers: { ...saved.upload.headers, 'X-Request-Key': state.pending.requestKey } }
        : { body: saved.body };
      const result = await api(saved.path, { method: 'POST', ownerId, ...options });
      if (!owns()) return false;
      checkedReceipt(result, state.pending); validate(result);
      if (state.value?.review?.id === result.review.id && result.review.version < state.value.review.version) throw new Error('이전 판본의 응답입니다.');
      state.value = result; state.receipt = result.receipt; clear(); state.needsRefresh = false;
      state.message = result.replayed ? '이전 저장 결과와 현재 내용을 확인했습니다.' : '저장했습니다. 현재 내용을 확인해주세요.'; return true;
    } catch (e) {
      if (!owns()) return false;
      if (e?.sessionChanged || [401, 403, 404].includes(e?.status)) { deny(e); return false; }
      if (!posted) { state.error = true; state.needsRefresh = !e?.localSizeLimit; state.message = e?.localSizeLimit ? e.message : reviewError(e); return false; }
      if ([400, 413, 415, 422, 429].includes(e?.status)) { clear(); state.error = true; state.message = reviewError(e); return false; }
      state.error = true; state.needsRefresh = true; state.message = '저장 응답을 확인하지 못했습니다. 다른 변경 전에 이전 저장 결과를 조회해주세요.'; return false;
    } finally { state.busy = false; paint(); }
  }
  async function lookup() {
    if (!owns() || state.busy || !state.pending) return false;
    state.busy = true; state.error = false; paint();
    try {
      const fresh = await access(null, null, true); if (!owns()) return false;
      try {
        const value = await api('/api/hand-review-commands/' + state.pending.requestKey, { ownerId });
        if (!owns()) return false;
        checkedReceipt(value, state.pending); validate(value);
        if (state.value?.review?.id === value.review.id && value.review.version < state.value.review.version) throw new Error('이전 판본의 응답입니다.');
        state.value = value; state.receipt = value.receipt; clear(); state.needsRefresh = false;
        state.message = '이전 저장 결과를 복구하고 현재 내용을 확인했습니다.'; return true;
      } catch (e) {
        if (!owns()) return false;
        if (e?.status !== 404 || e.sessionChanged) return reject(e);
        const current = await access(null, null, true); if (!owns()) return false;
        state.notFound = true;
        state.canRetry = Boolean(original && (!current?.review || original.body.version === undefined || original.body.version === current.review.version));
        state.message = '현재 접근 권한은 확인했지만 이 저장 기록은 없습니다. 같은 요청을 다시 보내거나, 기록을 정리한 뒤 현재 내용을 확인해주세요.'; return false;
      }
    } catch (e) { return reject(e); }
    finally { state.busy = false; paint(); }
  }
  async function refresh() {
    if (!owns() || state.busy || state.pending) return false;
    state.busy = true; paint();
    try {
      const fresh = await access(null, null, true); if (!owns()) return false;
      validate(fresh);
      if (fresh?.review && state.value?.review && fresh.review.version < state.value.review.version) throw new Error('이전 판본은 표시하지 않습니다.');
      state.value = fresh; state.receipt = null; state.needsRefresh = false; state.error = false;
      state.message = '현재 내용을 다시 불러왔습니다. 저장하지 않은 입력은 반영되지 않았습니다.'; return true;
    } catch (e) { return reject(e); }
    finally { state.busy = false; paint(); }
  }
  async function checkAccess() {
    if (!owns() || state.busy) return false;
    state.busy = true; paint();
    try {
      const fresh = await access(null, null, true); if (!owns()) return false;
      validate(fresh);
      if (fresh?.review && state.value?.review) {
        if (fresh.review.version !== state.value.review.version) { state.needsRefresh = true; state.message = '리뷰가 갱신됐습니다. 현재 내용을 다시 불러와주세요.'; return false; }
        if (JSON.stringify(fresh.capabilities) !== JSON.stringify(state.value.capabilities) || JSON.stringify(fresh.scope) !== JSON.stringify(state.value.scope)) state.value = fresh;
      }
      return true;
    } catch (e) { return reject(e); }
    finally { state.busy = false; paint(); }
  }
  function release() {
    if (!owns() || state.busy || !state.notFound) return false;
    clear(); state.needsRefresh = true; state.message = '이전 요청 표시를 정리했습니다. 현재 내용을 다시 불러온 뒤 새로 작성해주세요.'; paint(); return true;
  }
  const retry = () => state.canRetry && original && owns() && !state.busy ? send(original.path, original.body, original.intent, original.upload, true) : false;
  return { state, send, lookup, refresh, checkAccess, release, retry, reject, paint,
    canAct: () => owns() && !state.busy && !state.pending && !state.needsRefresh,
    dispose() { alive = false; original = null; state.value = null; state.receipt = null; state.pending = null; } };
}
