import { authGate } from './platform-ui.js';
import { esc } from './ui.js';
import { reviewID, isCoach, checkedDetail, checkedList, checkedPage, checkedAsset, detailPath, reviewAPI, reviewError, inputError } from './hand-review-model.js';
import { createHandReviewSession } from './hand-review-session.js';
import { editorHTML, playerRow, actionRow, readEditor, snapshotEditor, restoreEditor, renumberRows, showFormError, clearFormError, captureUpload } from './hand-review-forms.js';
import { pageHTML, unavailableHTML, listHTML, detailHTML, createAsideHTML, materialHTML, handHTML, historyHTML } from './hand-review-view.js';

function queryFor(params) {
  const q = new URLSearchParams();
  for (const key of params.keys()) {
    if (!['page', 'status', 'managed', 'q', 'coachId'].includes(key) || params.getAll(key).length !== 1) throw new Error('조회 조건을 확인해주세요.');
    q.set(key, params.get(key));
  }
  const page = q.get('page') || '1';
  if (!/^[1-9][0-9]{0,4}$/.test(page) || Number(page) > 10000) throw new Error('페이지 번호를 확인해주세요.');
  return { q, page: Number(page) };
}
export async function renderHandReviews(path, params, ctx) {
  const create = ['/review', '/reviews/new'].includes(path), desk = path.startsWith('/studio/hand-reviews');
  const listing = ['/reviews', '/studio/hand-reviews'].includes(path);
  const match = path.match(/^\/(?:reviews|studio\/hand-reviews)\/([a-z0-9_]{1,100})$/);
  if (!create && !listing && !match) return path.startsWith('/reviews/') || desk ? { html: unavailableHTML('현재 리뷰 주소를 확인해주세요.') } : null;
  if (!ctx.user) return authGate(ctx, '내 핸드의 다음 생각을 이어가세요.');
  if (desk && !isCoach(ctx.user)) return { html: unavailableHTML('현재 코치 또는 운영자만 작업함을 열 수 있습니다.') };
  const ownerId = ctx.user.id, admin = ctx.user.role === 'admin';
  const read = (url) => ctx.api(url, { ownerId });
  try {
    let kind, initial, load, validate, profiles = [], rid = null;
    if (create || listing) {
      const { q, page } = create ? { q: new URLSearchParams(), page: 1 } : queryFor(params);
      const queue = desk ? admin ? 'manage' : 'studio' : 'member';
      const endpoint = desk ? admin ? '/api/manage/hand-reviews' : '/api/studio/hand-reviews' : '/api/hand-reviews';
      load = async () => checkedList(await read(endpoint + (q.size ? '?' + q : '')), queue, page, ownerId);
      initial = await load();
      validate = (v) => v?.review ? checkedDetail(v, { actorId: ownerId, own: !desk }) : checkedList(v, queue, page, ownerId);
      if (desk && admin) profiles = (await read('/api/studio/profiles')).profiles;
      kind = create ? 'create' : 'list';
      const html = create ? `<div class="hr-compose-layout">${editorHTML()}${createAsideHTML(initial.scope)}</div>` : listHTML(initial, desk, q, profiles);
      return { html: pageHTML(create ? '한 핸드에서, 다음 선택의 기준을.' : desk ? '핸드 리뷰 작업함' : '나의 핸드 리뷰', create ? '당시의 상황과 선택을 기록하세요. 코치와 함께 판단의 이유를 정리합니다.' : desk ? '현재 담당한 요청과 자료를 확인하고 보완과 답변을 이어갑니다.' : '처음 남긴 기록부터 보완과 답변까지, 생각의 흐름을 이어가세요.', html, ctx, desk, create), bind() { mountHandReviews(document.querySelector('[data-hr-root]'), ctx, { kind, initial, load, validate, profiles, desk, params: q }); } };
    }
    rid = match[1]; if (!reviewID.test(rid)) throw new Error('리뷰 주소를 확인해주세요.');
    validate = (v) => checkedDetail(v, { id: rid, actorId: ownerId, own: !desk });
    load = async () => validate(await read(reviewAPI(rid)));
    initial = await load();
    if (initial.capabilities.isAdmin) profiles = (await read('/api/studio/profiles')).profiles;
    return { html: pageHTML(desk ? '검토의 근거를, 기록으로 연결하세요.' : '기록에서 다음 생각으로.', '최초 기록을 보존하며 현재의 질문과 보완, 답변을 확인합니다.', detailHTML(initial, profiles), ctx, desk), bind() { mountHandReviews(document.querySelector('[data-hr-root]'), ctx, { kind: 'detail', initial, load, validate, profiles, desk, rid }); } };
  } catch (e) { return { html: unavailableHTML(reviewError(e)) }; }
}
function workspaceLife(root, onDispose) {
  const route = location.hash, abort = new AbortController(); let ended = false, observer;
  const active = () => !ended && root.isConnected && location.hash === route;
  function end(clear = true) { if (ended) return; ended = true; abort.abort(); observer?.disconnect(); if (clear) root.querySelector('[data-hr-body]')?.replaceChildren(); onDispose(); }
  window.addEventListener('hashchange', () => end(), { once: true, signal: abort.signal });
  observer = new MutationObserver(() => { if (!root.isConnected) end(); }); observer.observe(root.ownerDocument.body, { childList: true, subtree: true });
  return { active, signal: abort.signal, dispose: end };
}
const safeStorage = () => { try { return window.sessionStorage; } catch { return null; } };

export function mountHandReviews(root, ctx, options) {
  const { kind, initial, load, validate, profiles, desk, rid, params } = options;
  const area = root.querySelector('[data-hr-body]'), feedback = root.querySelector('[data-hr-feedback]'), recovery = root.querySelector('[data-hr-recovery]');
  let session, painted = initial, resourceBusy = false, checkingAccess = false, generation = 0, privateGone = false, needsAccessCheck = Boolean(document.hidden), accessScheduled = false;
  let assets = new Map(), selected = new Set(), materialPage = null;
  let materialDraft = null;
  const urls = new Set();
  const revoke = () => { urls.forEach((url) => URL.revokeObjectURL(url)); urls.clear(); };
  const life = workspaceLife(root, () => { session?.dispose(); assets.clear(); selected.clear(); materialPage = null; materialDraft = null; ++generation; revoke(); });
  const read = (url, extra = {}) => ctx.api(url, { ...extra, ownerId: ctx.user.id, signal: life.signal });
  function deny(e) {
    if (privateGone) return; privateGone = true; ++generation; area.replaceChildren(); recovery.replaceChildren(); feedback.textContent = reviewError(e); feedback.classList.add('is-error'); root.querySelector('[data-hr-refresh]').disabled = true; revoke(); life.dispose(false);
  }
  function syncRefs(changed = false) {
    const form = area.querySelector('[data-hr-editor]');
    if (form) { form.querySelector('[data-hr-ref-values]').value = [...selected].sort().join(','); form.querySelector('[data-hr-ref-count]').textContent = `선택한 자료 ${selected.size}개 · 자료 선택 변경은 보완 내용을 저장할 때 반영됩니다.`; if (changed) { form.querySelector('[data-hr-preview-area]').replaceChildren(); form.querySelector('[data-hr-confirm]').checked = false; } }
  }
  function drawMaterials(page) {
    const value = session.state.value, target = area.querySelector('[data-hr-material-area]'); if (!target || !value?.review) return;
    materialPage = page; page.attachments.forEach((a) => assets.set(a.id, a));
    target.innerHTML = materialHTML(value, page, selected);
    syncRefs();
  }
  function prepare() {
    const v = session.state.value;
    assets.clear(); selected = new Set(v?.review?.revision?.kind === 'structured' ? v.review.revision.payload.attachmentIds : []);
    const form = area.querySelector('[data-hr-editor]'); if (form) renumberRows(form);
    if (v?.review) drawMaterials({ attachments: v.attachments, ...v.attachmentPage });
  }
  function renderValue(v) {
    ++generation; revoke();
    if (kind === 'create' && v?.review) { area.innerHTML = '<section class="hr-empty"><h2>핸드 기록을 접수했습니다.</h2><p>저장한 원문으로 이동합니다.</p></section>'; return; }
    if (kind === 'create') area.innerHTML = `<div class="hr-compose-layout">${editorHTML()}${createAsideHTML(v.scope)}</div>`;
    else if (kind === 'list') area.innerHTML = listHTML(v, desk, params, profiles);
    else area.innerHTML = detailHTML(v, profiles);
    prepare();
    const form = area.querySelector('[data-hr-editor]');
    if (materialDraft && v.receipt?.command?.startsWith('attachment.') && form && v.review?.id === materialDraft.reviewId && v.capabilities?.isMember && v.capabilities.canRevise) {
      restoreEditor(form, materialDraft.input); selected = new Set(materialDraft.input.attachmentIds); if (materialPage) drawMaterials(materialPage);
    }
    materialDraft = null;
  }
  function paint(state) {
    if (!life.active() || privateGone) return;
    if (state.value && painted !== state.value) { painted = state.value; renderValue(state.value); }
    feedback.textContent = state.message; feedback.classList.toggle('is-error', state.error);
    const locked = state.busy || state.pending || state.needsRefresh || resourceBusy || checkingAccess;
    area.querySelectorAll('[data-hr-lock]').forEach((f) => { f.disabled = Boolean(locked); });
    area.hidden = Boolean(document.hidden || checkingAccess || needsAccessCheck || state.needsRefresh);
    recovery.innerHTML = state.pending ? `<section class="hr-recovery"><h2>이전 저장 결과를 확인해주세요.</h2><p>결과를 확인할 때까지 새 변경은 잠시 멈춥니다.</p><div><button type="button" class="btn btn-outline btn-sm" data-hr-lookup ${state.busy ? 'disabled' : ''}>저장 결과 조회</button>${state.notFound ? `${state.canRetry ? '<button type="button" class="btn btn-outline btn-sm" data-hr-retry>같은 요청 다시 전송</button>' : '<p>이 탭에 이전 입력 내용이 없거나 현재 기록이 변경됐습니다. 자동으로 다시 보내지 않습니다.</p>'}<button type="button" class="btn btn-ghost btn-sm" data-hr-release ${state.busy ? 'disabled' : ''}>이전 요청 표시 정리</button>` : ''}</div></section>` : '';
    root.querySelector('[data-hr-refresh]').disabled = Boolean(state.busy || state.pending || resourceBusy || checkingAccess);
    if (needsAccessCheck && !document.hidden && !checkingAccess && !state.busy && !resourceBusy && !accessScheduled) {
      accessScheduled = true; queueMicrotask(runAccessCheck);
    }
  }
  session = createHandReviewSession({ api: read, ownerId: ctx.user.id, scope: rid ? 'review:' + rid : kind === 'create' ? 'new' : 'list:' + location.hash, initial, validate, guard: load, active: life.active, changed: paint, denied: deny, storage: safeStorage() });
  const canAct = () => life.active() && !privateGone && !resourceBusy && !checkingAccess && session.canAct();
  function error(e, form = null) {
    if (!life.active()) return;
    if (e?.sessionChanged || [401, 403, 404, 409].includes(e?.status)) { session.reject(e); return; }
    if (form?.hasAttribute('data-hr-editor')) showFormError(form, e);
    feedback.textContent = reviewError(e); feedback.classList.add('is-error');
  }
  async function runAccessCheck() {
    accessScheduled = false;
    if (!life.active() || privateGone || document.hidden || checkingAccess || session.state.busy || resourceBusy) return;
    checkingAccess = true; needsAccessCheck = false; paint(session.state);
    try { await session.checkAccess(); }
    finally { checkingAccess = false; paint(session.state); }
  }
  function requestAccessCheck() { needsAccessCheck = true; if (document.hidden) { area.hidden = true; ++generation; revoke(); } else paint(session.state); }
  document.addEventListener('visibilitychange', requestAccessCheck, { signal: life.signal });
  window.addEventListener('focus', requestAccessCheck, { signal: life.signal });
  window.addEventListener('pageshow', requestAccessCheck, { signal: life.signal });

  async function resource(kindName, page = 1, downloadId = null) {
    if (!canAct() || !session.state.value?.review) return;
    if (!await session.checkAccess() || !life.active()) return;
    const value = session.state.value, token = generation;
    resourceBusy = true; paint(session.state);
    try {
      if (downloadId) {
        const packet = await read(reviewAPI(value.review.id) + '/attachments/' + downloadId);
        if (!life.active() || token !== generation) return;
        if (packet.reviewId !== value.review.id || packet.version !== value.review.version) throw Object.assign(new Error('현재 자료를 다시 확인해주세요.'), { status: 409 });
        const asset = checkedAsset(packet.attachment, value.review.id);
        if (!asset.canDownload) { assets.set(asset.id, asset); feedback.textContent = '현재 열람할 수 없는 자료입니다. 자료 목록을 다시 확인해주세요.'; return; }
        const blob = await read(asset.href, { responseType: 'blob' });
        if (!life.active() || token !== generation) return;
        const url = URL.createObjectURL(blob); urls.add(url); const link = document.createElement('a'); link.href = url; link.download = asset.filename; link.click(); setTimeout(() => { URL.revokeObjectURL(url); urls.delete(url); }, 60000);
      } else {
        const data = checkedPage(await read(reviewAPI(value.review.id) + '/' + kindName + '?page=' + page), value, kindName, page);
        if (!life.active() || token !== generation) return;
        if (kindName === 'attachments') drawMaterials(data);
        else area.querySelector('[data-hr-history-area]').innerHTML = historyHTML(data, kindName);
      }
    } catch (e) { error(e); }
    finally { resourceBusy = false; paint(session.state); }
  }
  root.addEventListener('submit', async (event) => {
    const form = event.target; if (form.tagName !== 'FORM') return;
    event.preventDefault(); if (!canAct() || !form.reportValidity()) return;
    const value = session.state.value;
    try {
      if (form.hasAttribute('data-hr-filter')) {
        const q = new URLSearchParams(); for (const [key, val] of new FormData(form)) if (val && val !== 'all') q.set(key, val.trim());
        ctx.navigate((desk ? '/studio/hand-reviews' : '/reviews') + (q.size ? '?' + q : '')); return;
      }
      if (form.hasAttribute('data-hr-editor')) {
        const data = readEditor(form), creating = kind === 'create';
        clearFormError(form);
        if (!creating && !value.capabilities.canRevise) throw Object.assign(new Error('현재 보완 가능한 상태를 확인해주세요.'), { status: 409 });
        const result = await session.send(creating ? '/api/hand-reviews' : reviewAPI(value.review.id) + '/revise', { ...data, ...(creating ? {} : { version: value.review.version }) }, { command: creating ? 'create' : 'revise', reviewId: creating ? null : value.review.id });
        if (result && creating && life.active()) ctx.navigate(detailPath(session.state.value.review.id));
      } else if (form.hasAttribute('data-hr-command')) {
        const action = form.dataset.hrCommand, fields = Object.fromEntries(new FormData(form));
        const body = { reason: fields.reason.trim(), ...(action === 'adopt' ? { legacyHash: value.review.legacyHash } : { version: value.review.version }) };
        if (action === 'assign') body.coachId = fields.coachId;
        if (action === 'answer') body.body = fields.body.trim();
        const base = action === 'adopt' || action === 'cancel' && value.capabilities.isMember ? reviewAPI(value.review.id) : '/api/' + (value.capabilities.isAdmin ? 'manage' : 'studio') + '/hand-reviews/' + value.review.id;
        await session.send(base + '/' + action, body, { command: action, reviewId: value.review.id });
      } else if (form.hasAttribute('data-hr-upload')) {
        const file = form.elements.namedItem('material').files[0], version = value.review.version;
        const captured = await captureUpload(file, value.scope.attachmentPolicy, version);
        if (!canAct() || session.state.value !== value) return;
        const editor = area.querySelector('[data-hr-editor]'); materialDraft = editor ? { reviewId: value.review.id, input: snapshotEditor(editor) } : null;
        await session.send(reviewAPI(value.review.id) + '/attachments', { version, policyHash: captured.policyHash }, { command: 'attachment.upload', reviewId: value.review.id }, captured);
      } else if (form.hasAttribute('data-hr-material-command')) {
        const aid = form.dataset.hrMaterialCommand, fields = Object.fromEntries(new FormData(form));
        const meta = await read(reviewAPI(value.review.id) + '/attachments/' + aid);
        if (!canAct() || session.state.value !== value) return;
        const asset = checkedAsset(meta.attachment, value.review.id);
        if (meta.version !== value.review.version) throw Object.assign(new Error('현재 자료를 다시 불러와주세요.'), { status: 409 });
        if (!asset.actions?.[fields.action]) { assets.set(aid, asset); feedback.textContent = '자료의 현재 상태에서 가능한 처리를 다시 확인해주세요.'; return; }
        const editor = area.querySelector('[data-hr-editor]'); materialDraft = editor ? { reviewId: value.review.id, input: snapshotEditor(editor) } : null;
        await session.send(reviewAPI(value.review.id) + '/attachments/' + aid + '/' + fields.action, { version: value.review.version, attachmentVersion: asset.version, reason: fields.reason.trim() }, { command: 'attachment.' + fields.action, reviewId: value.review.id, attachmentId: aid });
      }
    } catch (e) { error(e, form); }
  }, { signal: life.signal });
  root.addEventListener('click', async (event) => {
    const button = event.target.closest('button'); if (!button || button.disabled || !life.active() || privateGone) return;
    if (button.hasAttribute('data-hr-lookup')) { const result = await session.lookup(); if (result && kind === 'create' && life.active()) ctx.navigate(detailPath(session.state.value.review.id)); return; }
    if (button.hasAttribute('data-hr-retry')) { const result = await session.retry(); if (result && kind === 'create' && life.active()) ctx.navigate(detailPath(session.state.value.review.id)); return; }
    if (button.hasAttribute('data-hr-release')) return session.release();
    if (button.hasAttribute('data-hr-refresh')) {
      if (resourceBusy || checkingAccess) return;
      materialDraft = null;
      try {
        if (session.state.value?.capabilities?.isAdmin) {
          resourceBusy = true; paint(session.state);
          const result = await read('/api/studio/profiles'); if (!life.active()) return; profiles.splice(0, profiles.length, ...result.profiles);
        }
      } catch (e) { error(e); return; }
      finally { resourceBusy = false; paint(session.state); }
      return session.refresh();
    }
    if (!canAct()) return;
    const form = button.closest('[data-hr-editor]');
    try {
      if (button.hasAttribute('data-hr-history')) return resource(button.dataset.hrHistory);
      if (button.hasAttribute('data-hr-page')) return resource(button.dataset.hrPage, Number(button.dataset.page));
      if (button.hasAttribute('data-hr-download')) return resource(null, 1, button.dataset.hrDownload);
      if (!form) return;
      if (button.hasAttribute('data-hr-preview')) { const { payload } = readEditor(form, false); clearFormError(form); feedback.classList.remove('is-error'); feedback.textContent = '현재 입력한 내용으로 미리보기를 준비했습니다.'; form.querySelector('[data-hr-preview-area]').innerHTML = '<section class="hr-preview"><h3>제출할 핸드 미리보기</h3>' + handHTML(payload) + '</section>'; return; }
      let movedFocus = null;
      if (button.hasAttribute('data-hr-add-player') && form.querySelectorAll('[data-hr-player]').length < 9) { const rows = form.querySelector('[data-hr-players]'); rows.insertAdjacentHTML('beforeend', playerRow()); rows.lastElementChild.querySelector('select').focus(); }
      if (button.hasAttribute('data-hr-remove-player') && form.querySelectorAll('[data-hr-player]').length > 2) { button.closest('[data-hr-player]').remove(); form.querySelector('[data-hr-add-player]').focus(); }
      if (button.hasAttribute('data-hr-add-action') && form.querySelectorAll('[data-hr-action]').length < 200) { const group = form.querySelector(`[data-hr-street="${button.dataset.hrAddAction}"]`), rows = group.querySelector('[data-hr-action-rows]'); rows.insertAdjacentHTML('beforeend', actionRow(button.dataset.hrAddAction, { position: form.elements.namedItem('heroPosition').value })); group.open = true; rows.lastElementChild.querySelector('[data-hr-action-kind]').focus(); }
      if (button.hasAttribute('data-hr-remove-action') && form.querySelectorAll('[data-hr-action]').length > 1) { const group = button.closest('[data-hr-street]'); button.closest('[data-hr-action]').remove(); group.querySelector('[data-hr-add-action]').focus(); }
      if (button.hasAttribute('data-hr-move')) { const row = button.closest('[data-hr-action]'), sibling = button.dataset.hrMove === 'up' ? row.previousElementSibling : row.nextElementSibling; if (sibling) { if (button.dataset.hrMove === 'up') sibling.before(row); else sibling.after(row); movedFocus = row.querySelector('[data-hr-action-kind]'); } }
      renumberRows(form);
      if (button.hasAttribute('data-hr-move') || button.hasAttribute('data-hr-remove-player') || button.hasAttribute('data-hr-add-player') || button.hasAttribute('data-hr-add-action') || button.hasAttribute('data-hr-remove-action')) form.querySelector('[data-hr-preview-area]').replaceChildren();
      movedFocus?.focus();
    } catch (e) { error(e, form); }
  }, { signal: life.signal });
  root.addEventListener('change', (event) => {
    if (!canAct()) return; const target = event.target, form = target.closest('[data-hr-editor]');
    if (target.hasAttribute('data-hr-ref')) {
      const asset = assets.get(target.dataset.hrRef);
      if (target.checked && (!asset?.canReference || selected.size >= 10)) { target.checked = false; feedback.textContent = '현재 연결 가능한 자료를 최대 10개 선택해주세요.'; return; }
      if (target.checked) selected.add(target.dataset.hrRef); else selected.delete(target.dataset.hrRef); syncRefs(true);
    }
    if (!form) return;
    if (target.hasAttribute('data-hr-action-kind')) { const amount = target.closest('[data-hr-action]').querySelector('[data-hr-action-amount]'); amount.readOnly = ['check', 'fold'].includes(target.value); if (amount.readOnly) amount.value = '0'; else if (amount.value === '0') amount.value = ''; }
    if (target.hasAttribute('data-hr-action-street')) { const row = target.closest('[data-hr-action]'), group = form.querySelector(`[data-hr-street="${target.value}"]`); row.dataset.street = target.value; group.querySelector('[data-hr-action-rows]').append(row); group.open = true; target.focus(); }
    if (target.name !== 'reason' && !target.hasAttribute('data-hr-confirm')) form.querySelector('[data-hr-preview-area]').replaceChildren();
    renumberRows(form);
  }, { signal: life.signal });
  root.addEventListener('input', (event) => { event.target.removeAttribute('aria-invalid'); const form = event.target.closest('[data-hr-editor]'); if (form && event.target.name !== 'reason' && !event.target.hasAttribute('data-hr-confirm')) form.querySelector('[data-hr-preview-area]').replaceChildren(); }, { signal: life.signal });
  prepare(); session.paint();
  return { session, life, canAct };
}
