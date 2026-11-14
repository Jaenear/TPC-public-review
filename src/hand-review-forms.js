import { esc } from './ui.js';
import { positions, games, streets, actions, bbText, payloadFromInput, inputError } from './hand-review-model.js';

let sequence = 0;
const uid = (kind) => 'hr-' + kind + '-' + (++sequence);
const options = (items, selected, empty = '') => (empty ? `<option value="">${esc(empty)}</option>` : '') + Object.entries(items).map(([value, label]) => `<option value="${esc(value)}" ${value === selected ? 'selected' : ''}>${esc(label)}</option>`).join('');
const seats = Object.fromEntries(positions.map((p) => [p, p]));
const bbInput = (id, name, value, extra = '') => `<input id="${id}" ${name ? `name="${name}"` : ''} type="text" inputmode="decimal" maxlength="16" value="${esc(value ?? '')}" placeholder="100" autocomplete="off" required ${extra}>`;

export function editorInput(value) {
  const p = value?.review?.revision?.kind === 'structured' ? value.review.revision.payload : null;
  const o = value?.review?.original;
  const hero = p?.heroPosition || (positions.includes(o?.position) ? o.position : 'BTN');
  return {
    title: p?.title ?? o?.title ?? '', gameType: p?.gameType || Object.keys(games).find((k) => games[k] === o?.game_type) || 'live_cash',
    effectiveStack: p ? bbText(p.effectiveStackMilliBB) : o?.stack == null ? '' : String(o.stack), heroPosition: hero,
    heroHand: p ? p.heroHand.join(' ') : o?.hero_hand || '', board: p ? p.board.join(' ') : o?.board || '',
    players: p ? p.players.map((v) => ({ position: v.position, stack: bbText(v.stackMilliBB) })) : [{ position: hero, stack: '' }, { position: o ? '' : 'BB', stack: '' }],
    actions: p ? p.actions.map((v) => ({ ...v, amount: bbText(v.amountMilliBB) })) : [{ street: 'preflop', position: hero, action: '', amount: '', note: '' }],
    question: p?.question ?? o?.question ?? '', attachmentIds: p?.attachmentIds || [],
  };
}
export function playerRow(value = {}) {
  const id = uid('seat');
  return `<fieldset class="hr-player-row" data-hr-player><legend>참여 좌석 <span data-hr-row-number></span></legend><div class="hr-row-fields"><label for="${id}-position">포지션<select id="${id}-position" data-hr-player-position required>${options(seats, value.position, '포지션 선택')}</select></label><label for="${id}-stack">시작 스택 · BB${bbInput(id + '-stack', '', value.stack, 'data-hr-player-stack')}</label><button type="button" class="btn btn-ghost btn-sm" data-hr-remove-player>좌석 삭제</button></div></fieldset>`;
}
export function actionRow(street, value = {}) {
  const id = uid('action'), zero = ['check', 'fold'].includes(value.action);
  return `<fieldset class="hr-action-row" data-hr-action data-street="${esc(street)}"><legend>액션 <span data-hr-row-number></span></legend><div class="hr-action-fields"><label for="${id}-street">스트리트<select id="${id}-street" data-hr-action-street>${options(streets, street)}</select></label><label for="${id}-position">포지션<select id="${id}-position" data-hr-action-position required>${options(seats, value.position, '좌석 선택')}</select></label><label for="${id}-kind">액션<select id="${id}-kind" data-hr-action-kind required>${options(actions, value.action, '액션 선택')}</select></label><label for="${id}-amount">추가 투입 · BB${bbInput(id + '-amount', '', zero ? '0' : value.amount, `data-hr-action-amount ${zero ? 'readonly' : ''}`)}</label><label class="hr-action-note" for="${id}-note">짧은 메모 · 선택<input id="${id}-note" data-hr-action-note maxlength="500" value="${esc(value.note || '')}" placeholder="상대의 액션이나 판단 근거"></label></div><div class="hr-row-actions"><button type="button" class="btn btn-ghost btn-sm" data-hr-move="up">위로</button><button type="button" class="btn btn-ghost btn-sm" data-hr-move="down">아래로</button><button type="button" class="btn btn-ghost btn-sm" data-hr-remove-action>액션 삭제</button></div></fieldset>`;
}
export function editorHTML(value = null) {
  const raw = editorInput(value), revising = Boolean(value?.review);
  return `<form class="hr-editor" data-hr-editor data-no-session-draft><fieldset data-hr-lock><section class="hr-form-section"><div class="hr-section-head"><span>01</span><div><h2>테이블 상황</h2><p>액션을 시작할 때의 좌석과 스택을 기록하세요.</p></div></div><div class="hr-fields"><label class="hr-full" for="hr-title">핸드 제목<input id="hr-title" name="title" minlength="2" maxlength="100" value="${esc(raw.title)}" placeholder="BTN 오픈 후 턴에서 만난 체크레이즈" required></label><label for="hr-game">게임 유형<select id="hr-game" name="gameType">${options(games, raw.gameType)}</select></label><label for="hr-position">내 포지션<select id="hr-position" name="heroPosition">${options(seats, raw.heroPosition)}</select></label><label for="hr-stack">유효 스택 · BB${bbInput('hr-stack', 'effectiveStack', raw.effectiveStack)}<small>나와 상대가 실제로 사용할 수 있는 스택을 입력하세요.</small></label><label for="hr-hand">내 홀카드<input id="hr-hand" name="heroHand" value="${esc(raw.heroHand)}" maxlength="30" placeholder="As Kh" autocomplete="off" required><small>예: As Kh 또는 A♠ K♥</small></label><label class="hr-full" for="hr-board">보드 · 선택<input id="hr-board" name="board" value="${esc(raw.board)}" maxlength="80" placeholder="Qs Jh 2d / 턴과 리버는 뒤에 추가" autocomplete="off"><small>프리플랍은 빈칸, 플랍 3장·턴 4장·리버 5장입니다.</small></label></div><div class="hr-inline-head"><h3>참여 좌석</h3><button type="button" class="btn btn-outline btn-sm" data-hr-add-player>좌석 추가</button></div><div data-hr-players>${raw.players.map(playerRow).join('')}</div><p class="hr-hint">2–9명의 포지션과 시작 스택을 기록합니다. 같은 포지션은 한 번만 선택하세요.</p></section>
    <section class="hr-form-section"><div class="hr-section-head"><span>02</span><div><h2>스트리트별 액션</h2><p>기억한 순서대로, 각 액션에서 추가로 넣은 BB를 기록하세요.</p></div></div><p class="hr-hint">레이즈의 총액이 아닌 이번 액션의 추가 투입 금액입니다. 체크·폴드는 0, 올인은 남은 스택 전부입니다. 팟 크기는 자동 추론하지 않습니다.</p><div data-hr-actions>${Object.entries(streets).map(([street, label]) => {
      const rows = raw.actions.filter((a) => a.street === street);
      return `<details class="hr-street" data-hr-street="${street}" ${rows.length || street === 'preflop' ? 'open' : ''}><summary>${label}<span data-hr-street-count>${rows.length}개 액션</span></summary><div data-hr-action-rows>${rows.map((a) => actionRow(street, a)).join('')}</div><button type="button" class="btn btn-outline btn-sm" data-hr-add-action="${street}">${label} 액션 추가</button></details>`;
    }).join('')}</div><p class="hr-hint">최대 200개까지 기록할 수 있습니다. 스트리트를 바꾸면 해당 영역으로 이동합니다.</p></section>
    <section class="hr-form-section"><div class="hr-section-head"><span>03</span><div><h2>내 판단과 질문</h2><p>결과보다 선택의 이유를 남겨주세요.</p></div></div><div class="hr-fields"><label class="hr-full" for="hr-question"><span id="hr-question-label">가장 궁금한 지점</span><textarea id="hr-question" aria-labelledby="hr-question-label" name="question" rows="5" minlength="5" maxlength="3000" placeholder="어떤 선택 사이에서 고민했고, 상대를 어떻게 생각했나요?" required>${esc(raw.question)}</textarea></label><label class="hr-full" for="hr-reason">${revising ? '보완한 내용' : '제출 메모'}<textarea id="hr-reason" name="reason" rows="2" minlength="5" maxlength="2000" placeholder="${revising ? '턴 보드와 액션 금액을 보완했습니다.' : '턴의 베팅 판단을 함께 검토하고 싶습니다.'}" required></textarea></label></div><input type="hidden" data-hr-ref-values value="${esc(raw.attachmentIds.join(','))}"><p class="hr-hint" data-hr-ref-count>선택한 자료 ${raw.attachmentIds.length}개${revising ? ' · 자료 선택 변경은 보완 내용을 저장할 때 반영됩니다.' : ' · 본문을 접수한 뒤 자료를 추가할 수 있습니다.'}</p><label class="hr-confirm"><input type="checkbox" data-hr-confirm required><span>이 내용으로 비공개 코치 검토를 ${revising ? '다시 요청합니다' : '요청합니다'}. 다른 사람의 실명·연락처는 제외했습니다.</span></label><p class="hr-form-error" data-hr-form-error role="alert" tabindex="-1" hidden></p><div class="hr-editor-actions"><button type="button" class="btn btn-outline" data-hr-preview>내용 미리보기</button><button type="submit" class="btn btn-primary">${revising ? '보완 내용 제출' : '핸드 리뷰 제출'} ↗</button></div><div data-hr-preview-area></div></section></fieldset></form>`;
}
export function snapshotEditor(form) {
  const field = (name) => form.elements.namedItem(name)?.value || '';
  const raw = { title: field('title'), gameType: field('gameType'), heroPosition: field('heroPosition'), effectiveStack: field('effectiveStack'), heroHand: field('heroHand'), board: field('board'), question: field('question'),
    players: [...form.querySelectorAll('[data-hr-player]')].map((row) => ({ position: row.querySelector('[data-hr-player-position]').value, stack: row.querySelector('[data-hr-player-stack]').value })),
    actions: [...form.querySelectorAll('[data-hr-action]')].map((row) => ({ street: row.dataset.street, position: row.querySelector('[data-hr-action-position]').value, action: row.querySelector('[data-hr-action-kind]').value, amount: row.querySelector('[data-hr-action-amount]').value, note: row.querySelector('[data-hr-action-note]').value })),
    attachmentIds: (form.querySelector('[data-hr-ref-values]')?.value || '').split(',').filter(Boolean) };
  return { ...raw, reason: field('reason'), confirmed: Boolean(form.querySelector('[data-hr-confirm]').checked), openStreets: [...form.querySelectorAll('[data-hr-street][open]')].map((e) => e.dataset.hrStreet) };
}
export function restoreEditor(form, raw) {
  for (const key of ['title', 'gameType', 'heroPosition', 'effectiveStack', 'heroHand', 'board', 'question', 'reason']) form.elements.namedItem(key).value = raw[key];
  form.querySelector('[data-hr-players]').innerHTML = raw.players.map(playerRow).join('');
  for (const group of form.querySelectorAll('[data-hr-street]')) {
    const street = group.dataset.hrStreet;
    group.querySelector('[data-hr-action-rows]').innerHTML = raw.actions.filter((a) => a.street === street).map((a) => actionRow(street, a)).join('');
    group.open = raw.openStreets.includes(street);
  }
  form.querySelector('[data-hr-confirm]').checked = raw.confirmed;
  form.querySelector('[data-hr-ref-values]').value = raw.attachmentIds.join(',');
  renumberRows(form);
}
export function readEditor(form, submission = true) {
  const raw = snapshotEditor(form), payload = payloadFromInput(raw), reason = raw.reason.trim();
  if (submission && (reason.length < 5 || reason.length > 2000)) throw inputError('제출·보완 메모를 5–2,000자로 입력해주세요.', 'reason');
  if (submission && !form.querySelector('[data-hr-confirm]').checked) throw inputError('검토를 요청할 내용을 확인해주세요.', 'confirm');
  return { payload, reason };
}
export function renumberRows(form) {
  const players = [...form.querySelectorAll('[data-hr-player]')];
  players.forEach((row, i) => { row.querySelector('[data-hr-row-number]').textContent = i + 1; row.querySelector('[data-hr-remove-player]').disabled = players.length <= 2; });
  form.querySelector('[data-hr-add-player]').disabled = players.length >= 9;
  const all = [...form.querySelectorAll('[data-hr-action]')];
  all.forEach((row, i) => { row.querySelector('[data-hr-row-number]').textContent = i + 1; });
  for (const group of form.querySelectorAll('[data-hr-street]')) {
    const rows = [...group.querySelectorAll('[data-hr-action]')];
    group.querySelector('[data-hr-street-count]').textContent = rows.length + '개 액션';
    rows.forEach((row, i) => { row.querySelector('[data-hr-move="up"]').disabled = i === 0; row.querySelector('[data-hr-move="down"]').disabled = i === rows.length - 1; row.querySelector('[data-hr-remove-action]').disabled = all.length <= 1; });
    group.querySelector('[data-hr-add-action]').disabled = all.length >= 200;
  }
}
export function showFormError(form, error) {
  form.querySelectorAll('[aria-invalid]').forEach((e) => e.removeAttribute('aria-invalid'));
  const note = form.querySelector('[data-hr-form-error]'); note.hidden = false; note.textContent = error.message;
  let target;
  if (error.field?.startsWith('player-')) target = form.querySelectorAll('[data-hr-player]')[Number(error.field.slice(7))]?.querySelector('select');
  else if (error.field?.startsWith('action-')) target = form.querySelectorAll('[data-hr-action]')[Number(error.field.slice(7))]?.querySelector('[data-hr-action-amount]');
  else target = error.field === 'confirm' ? form.querySelector('[data-hr-confirm]') : form.elements.namedItem(error.field);
  target?.setAttribute('aria-invalid', 'true'); (target || note).focus();
}
export function clearFormError(form) {
  form.querySelectorAll('[aria-invalid]').forEach((e) => e.removeAttribute('aria-invalid'));
  const note = form.querySelector('[data-hr-form-error]'); note.hidden = true; note.textContent = '';
}
export async function captureUpload(file, policy, version) {
  if (!policy || !file || typeof file.arrayBuffer !== 'function' || !Number.isSafeInteger(version) || version < 1) throw new Error('현재 자료 제출 기준과 파일을 다시 확인해주세요.');
  const name = file.name;
  if (typeof name !== 'string' || !name.trim() || name !== name.trim() || name.length > 255 || /[\/\\\u0000-\u001f\u007f]/.test(name)) throw new Error('파일명에 경로나 제어 문자를 사용할 수 없습니다.');
  const suffix = '.' + name.split('.').pop().toLowerCase(), format = policy.formats.find((v) => v.suffix === suffix);
  if (!format) throw new Error('허용된 TXT/JSON 파일을 선택해주세요.');
  if (!Number.isSafeInteger(file.size) || file.size < 1 || file.size > policy.maxBytes) throw new Error('파일이 비어 있거나 허용 크기를 넘었습니다.');
  const buffer = (await file.arrayBuffer()).slice(0); if (buffer.byteLength !== file.size) throw new Error('파일을 끝까지 읽지 못했습니다.');
  let decoded;
  try { decoded = new TextDecoder('utf-8', { fatal: true }).decode(buffer).replace(/^\uFEFF/, ''); }
  catch { throw new Error('UTF-8 텍스트 파일을 선택해주세요.'); }
  if (!decoded.trim() || /[\u0000-\u0008\u000b\u000c\u000e-\u001f]/.test(decoded)) throw new Error('내용이 있는 UTF-8 텍스트를 선택해주세요.');
  if (suffix === '.json') {
    try { const data = JSON.parse(decoded); if (!data || typeof data !== 'object') throw new Error(); }
    catch { throw new Error('JSON 객체 또는 배열 파일을 선택해주세요.'); }
  } else if (/^(?:<|\{|\[|%PDF-|#!)/.test(decoded.trimStart())) throw new Error('파일 확장자와 텍스트 내용이 맞는지 확인해주세요.');
  const sha256 = [...new Uint8Array(await crypto.subtle.digest('SHA-256', buffer))].map((v) => v.toString(16).padStart(2, '0')).join('');
  return { body: new Blob([buffer], { type: format.mediaType }), policyHash: policy.policyHash, sha256, filename: name,
    headers: { 'Content-Type': format.mediaType, 'X-File-Name': encodeURIComponent(name), 'X-Review-Version': String(version) } };
}
