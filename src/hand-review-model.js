/** Recorded hand facts and bound packets. No strategy or pot inference. */
export const positions = ['UTG', 'UTG+1', 'UTG+2', 'LJ', 'HJ', 'CO', 'BTN', 'SB', 'BB'];
export const streets = { preflop: '프리플랍', flop: '플랍', turn: '턴', river: '리버' };
export const games = { live_cash: '라이브 캐시', tournament: '토너먼트', online_cash: '온라인 캐시' };
export const actions = { post_blind: '블라인드', post_ante: '앤티', check: '체크', bet: '베팅', call: '콜', raise: '레이즈', fold: '폴드', all_in: '올인' };
export const states = { submitted: '접수', needs_information: '보완 요청', in_review: '검토 중', answered: '답변 완료', cancelled: '취소', legacy_unknown: '기존 상태 확인' };
export const assetStates = { pending: '검사 대기', approved: '검사 통과', rejected: '검사 거절', blocked: '열람 차단', deleted: '정리 완료', expired: '보관 기한 종료' };
export const reviewID = /^[a-z0-9_]{1,100}$/;
export const assetID = /^hrattachment_[a-f0-9]{32}$/;
export const keyID = /^[A-Za-z0-9_-]{8,128}$/;
const hashID = /^[a-f0-9]{64}$/;
const record = (v) => v && typeof v === 'object' && !Array.isArray(v);
const integer = (v, min = 1, max = Number.MAX_SAFE_INTEGER) => Number.isSafeInteger(v) && v >= min && v <= max;
const text = (v, min, max) => typeof v === 'string' && v.length >= min && v.length <= max;
export function inputError(message, field = '') { const e = new Error(message); e.field = field; return e; }
export function checkedCommandSize(body) {
  if (new TextEncoder().encode(JSON.stringify(body)).byteLength > 65536) {
    const e = inputError('기록 내용이 너무 깁니다. 액션 메모를 줄인 뒤 다시 제출해주세요.');
    e.status = 413; e.localSizeLimit = true; throw e;
  }
  return body;
}
function check(test, message = '현재 리뷰의 내용을 다시 확인해주세요.', field = '') { if (!test) throw inputError(message, field); }

export function milliBB(value, allowZero = false, field = '') {
  const raw = String(value ?? '').trim();
  check(raw.length <= 16 && /^(?:\d+(?:\.\d{0,3})?|\.\d{1,3})$/.test(raw), 'BB는 소수 셋째 자리까지 숫자로 입력해주세요.', field);
  const [whole, fraction = ''] = raw.split('.');
  const amount = Number(whole || '0') * 1000 + Number(fraction.padEnd(3, '0'));
  check(integer(amount, allowZero ? 0 : 1, 20000000), 'BB는 0.001부터 20,000까지 입력해주세요.', field);
  return amount;
}
export function bbText(value) {
  check(integer(value, 0, 20000000));
  return String(Math.floor(value / 1000)) + (value % 1000 ? '.' + String(value % 1000).padStart(3, '0').replace(/0+$/, '') : '');
}
export function parseCards(raw, field = '') {
  const value = String(raw || '').trim();
  if (!value) return [];
  return value.split(/[\s,]+/).map((token) => {
    const card = token.replace(/^10/i, 'T').replace(/[♠♥♦♣]/g, (s) => ({ '♠': 's', '♥': 'h', '♦': 'd', '♣': 'c' })[s]);
    check(/^[2-9TJQKA][shdc]$/i.test(card), '카드는 As Kh 또는 A♠ K♥처럼 입력해주세요.', field);
    return card[0].toUpperCase() + card[1].toLowerCase();
  });
}
export function checkedHand(p) {
  check(record(p));
  check(text(p.title, 2, 100), '핸드 제목은 2–100자로 입력해주세요.', 'title');
  check(Object.hasOwn(games, p.gameType) && positions.includes(p.heroPosition), '게임 유형과 내 포지션을 확인해주세요.', 'heroPosition');
  check(integer(p.effectiveStackMilliBB, 1, 20000000), '유효 스택을 확인해주세요.', 'effectiveStack');
  check(Array.isArray(p.heroHand) && p.heroHand.length === 2 && Array.isArray(p.board) && [0, 3, 4, 5].includes(p.board.length), '홀카드 2장과 보드 0·3·4·5장을 입력해주세요.', 'heroHand');
  const cards = [...p.heroHand, ...p.board];
  check(cards.every((c) => typeof c === 'string' && /^[2-9TJQKA][shdc]$/.test(c)) && new Set(cards).size === cards.length, '카드 표기와 중복을 확인해주세요.', 'board');
  check(Array.isArray(p.players) && p.players.length >= 2 && p.players.length <= 9, '참여 좌석은 2–9명으로 입력해주세요.', 'players');
  const remaining = new Map();
  p.players.forEach((player, i) => {
    check(record(player) && positions.includes(player.position) && !remaining.has(player.position), '참여 포지션을 중복 없이 선택해주세요.', 'player-' + i);
    check(integer(player.stackMilliBB, 1, 20000000), '좌석별 시작 스택을 확인해주세요.', 'player-' + i);
    remaining.set(player.position, player.stackMilliBB);
  });
  check(remaining.has(p.heroPosition) && remaining.get(p.heroPosition) >= p.effectiveStackMilliBB && p.players.some((v) => v.position !== p.heroPosition && v.stackMilliBB >= p.effectiveStackMilliBB), '내 좌석과 상대의 시작 스택에 맞는 유효 스택을 입력해주세요.', 'effectiveStack');
  check(Array.isArray(p.actions) && p.actions.length >= 1 && p.actions.length <= 200, '액션은 1–200개로 기록해주세요.', 'actions');
  let previous = -1; const finished = new Set(); const order = Object.keys(streets);
  p.actions.forEach((a, i) => {
    const field = 'action-' + i;
    check(record(a) && Object.hasOwn(streets, a.street) && remaining.has(a.position) && Object.hasOwn(actions, a.action), `${i + 1}번째 액션의 좌석과 종류를 확인해주세요.`, field);
    const index = order.indexOf(a.street);
    check(index >= previous && p.board.length >= [0, 3, 4, 5][index] && !finished.has(a.position) && (!a.action.startsWith('post_') || index === 0), `${i + 1}번째 액션의 보드·순서·종료된 좌석을 확인해주세요.`, field);
    const zero = ['check', 'fold'].includes(a.action);
    check(integer(a.amountMilliBB, zero ? 0 : 1, 20000000) && (!zero || a.amountMilliBB === 0) && a.amountMilliBB <= remaining.get(a.position) && (a.action !== 'all_in' || a.amountMilliBB === remaining.get(a.position)), `${i + 1}번째 액션의 추가 투입 금액과 남은 스택을 확인해주세요.`, field);
    check(a.note === undefined || text(a.note, 0, 500), '액션 메모는 500자 이내로 입력해주세요.', field);
    remaining.set(a.position, remaining.get(a.position) - a.amountMilliBB);
    if (a.action === 'fold' || remaining.get(a.position) === 0) finished.add(a.position);
    previous = index;
  });
  check(text(p.question, 5, 3000), '궁금한 지점은 5–3,000자로 입력해주세요.', 'question');
  check(Array.isArray(p.attachmentIds) && p.attachmentIds.length <= 10 && p.attachmentIds.every((id) => assetID.test(id)) && new Set(p.attachmentIds).size === p.attachmentIds.length, '이 리뷰의 자료를 중복 없이 최대 10개 선택해주세요.', 'attachments');
  return p;
}
export function payloadFromInput(raw) {
  check(record(raw) && Array.isArray(raw.players) && Array.isArray(raw.actions), '참여 좌석과 액션을 확인해주세요.');
  const p = { title: String(raw.title || '').trim(), gameType: raw.gameType,
    effectiveStackMilliBB: milliBB(raw.effectiveStack, false, 'effectiveStack'), heroPosition: raw.heroPosition,
    heroHand: parseCards(raw.heroHand, 'heroHand'), board: parseCards(raw.board, 'board'),
    players: raw.players.map((v, i) => ({ position: v.position, stackMilliBB: milliBB(v.stack, false, 'player-' + i) })),
    actions: raw.actions.map((v, i) => ({ street: v.street, position: v.position, action: v.action, amountMilliBB: milliBB(v.amount, ['check', 'fold'].includes(v.action), 'action-' + i), note: String(v.note || '').trim() })),
    question: String(raw.question || '').trim(), attachmentIds: [...(raw.attachmentIds || [])].sort() };
  return checkedHand(p);
}
export function checkedScope(scope) {
  check(record(scope) && scope.kind === 'hand_review_api' && scope.privateMaterialsOnly === true && scope.solverConnected === false && scope.commercialPaymentConnected === false && typeof scope.attachmentsConfigured === 'boolean');
  const p = scope.attachmentPolicy;
  if (p != null) {
    check(scope.attachmentsConfigured && record(p) && hashID.test(p.policyHash) && integer(p.maxBytes, 1, 8388608) && integer(p.maxTotalBytes, p.maxBytes) && integer(p.maxPending, 1, 30) && integer(p.pendingSeconds, 60, 604800));
    check(Array.isArray(p.formats) && p.formats.length >= 1 && p.formats.length <= 2 && p.formats.every((f) => record(f) && ({ '.txt': 'text/plain', '.json': 'application/json' })[f.suffix] === f.mediaType) && new Set(p.formats.map((f) => f.suffix)).size === p.formats.length);
  }
  return scope;
}
export function checkedAsset(a, rid) {
  check(record(a) && assetID.test(a.id) && a.reviewId === rid && text(a.filename, 1, 255) && integer(a.size) && hashID.test(a.sha256) && integer(a.version) && Object.hasOwn(assetStates, a.state) && typeof a.canDownload === 'boolean');
  check(a.canDownload ? a.state === 'approved' && a.href === `/api/hand-reviews/${rid}/attachments/${a.id}/file` : a.href == null);
  if (a.canReference !== undefined) check(typeof a.canReference === 'boolean' && (!a.canReference || a.state === 'approved'));
  if (a.actions !== undefined) check(record(a.actions) && ['inspect', 'block', 'delete', 'cancel', 'expire'].every((k) => typeof a.actions[k] === 'boolean'));
  return a;
}
export function checkedRevision(r) {
  check(record(r) && /^hrrevision_[a-f0-9]{32}$/.test(r.id) && integer(r.number) && ['structured', 'legacy'].includes(r.kind) && hashID.test(r.hash) && record(r.payload));
  if (r.kind === 'structured') checkedHand(r.payload);
  return r;
}
export function checkedDetail(v, { id = null, actorId = null, own = false } = {}) {
  check(record(v)); checkedScope(v.scope); const r = v.review, c = v.capabilities;
  check(record(r) && reviewID.test(r.id) && (!id || r.id === id) && text(r.memberId, 1, 100) && typeof r.managed === 'boolean' && Object.hasOwn(states, r.state) && hashID.test(r.legacyHash));
  check(record(r.original) && r.original.id === r.id && r.original.user_id === r.memberId);
  check(r.managed ? integer(r.version) && record(r.revision) : r.version === null && r.revision === null);
  if (r.revision) {
    checkedRevision(r.revision);
    if (r.revision.kind === 'legacy') check(r.revision.payload.id === r.id && r.revision.payload.user_id === r.memberId);
  }
  check(record(c) && ['isMember', 'isAdmin', 'isOperator', 'canAdopt', 'canRevise', 'canAssign', 'canUnassign', 'canStart', 'canRequestInformation', 'canAnswer', 'canCancel', 'canUpload'].every((key) => typeof c[key] === 'boolean'));
  if (actorId) check(c.isMember === (r.memberId === actorId));
  if (own) check(c.isMember && r.memberId === actorId);
  if (r.assignment) check(/^hrassignment_[a-f0-9]{32}$/.test(r.assignment.id) && text(r.assignment.coachId, 1, 100) && text(r.assignment.ownerId, 1, 100));
  if (r.response) check(/^hrresponse_[a-f0-9]{32}$/.test(r.response.id) && r.response.reviewId === r.id && ['legacy', 'text'].includes(r.response.kind) && typeof r.response.body === 'string');
  check(Array.isArray(v.attachments) && v.attachments.length <= 20 && record(v.attachmentPage) && integer(v.attachmentPage.total, 0) && v.attachmentPage.page === 1 && v.attachmentPage.pageSize === 20);
  v.attachments.forEach((a) => checkedAsset(a, r.id));
  return v;
}
export function checkedReceipt(v, pending) {
  checkedDetail(v, { id: pending.reviewId, actorId: pending.ownerId, own: pending.command === 'create' });
  const r = v.receipt;
  check(record(r) && r.requestKey === pending.requestKey && r.command === pending.command && r.reviewId === v.review.id && record(r.result) && integer(r.result.version) && r.result.version <= v.review.version && Object.hasOwn(states, r.result.state));
  if (pending.baseVersion !== undefined) check(r.result.version === (pending.baseVersion === null ? 1 : pending.baseVersion + 1));
  check(/^hrrevision_[a-f0-9]{32}$/.test(r.result.revisionId) && (r.result.assignmentId === null || /^hrassignment_[a-f0-9]{32}$/.test(r.result.assignmentId)) && (r.result.responseId === null || /^hrresponse_[a-f0-9]{32}$/.test(r.result.responseId)));
  if (r.result.version === v.review.version) check(r.result.state === v.review.state && r.result.revisionId === v.review.revision.id && r.result.assignmentId === (v.review.assignment?.id || null) && r.result.responseId === (v.review.response?.id || null));
  if (pending.attachmentId) check(r.result.attachmentId === pending.attachmentId);
  if (pending.command.startsWith('attachment.')) check(assetID.test(r.result.attachmentId) && integer(r.result.attachmentVersion) && Object.hasOwn(assetStates, r.result.attachmentState));
  if (pending.command === 'attachment.upload') check(r.result.attachmentVersion === 1);
  if (pending.attachmentVersion !== undefined) check(r.result.attachmentVersion === pending.attachmentVersion + 1);
  return v;
}
export function checkedList(v, queue, page, actorId) {
  checkedScope(v?.scope);
  check(v.queue === queue && v.page === page && v.pageSize === 20 && integer(v.total, 0) && Array.isArray(v.reviews) && v.reviews.length <= 20);
  v.reviews.forEach((r) => check(record(r) && reviewID.test(r.id) && text(r.title, 0, 1000) && text(r.memberId, 1, 100) && (queue !== 'member' || r.memberId === actorId) && Object.hasOwn(states, r.state) && typeof r.managed === 'boolean' && (r.managed ? integer(r.version) : r.version === null)));
  return v;
}
export function checkedPage(v, detail, kind, page) {
  checkedScope(v?.scope);
  check(v.reviewId === detail.review.id);
  if (v.version !== detail.review.version) throw Object.assign(new Error('기록이 갱신됐습니다. 현재 내용을 다시 불러와주세요.'), { status: 409 });
  check(v.page === page && v.pageSize === 20 && integer(v.total, 0) && Array.isArray(v[kind]) && v[kind].length <= 20);
  if (kind === 'revisions') v[kind].forEach((r) => { checkedRevision(r); if (r.kind === 'legacy') check(r.payload.id === detail.review.id && r.payload.user_id === detail.review.memberId); });
  else if (kind === 'attachments') v[kind].forEach((a) => checkedAsset(a, v.reviewId));
  else v[kind].forEach((e) => check(record(e) && /^hrevent_[a-f0-9]{32}$/.test(e.id) && integer(e.version) && text(e.action, 1, 100) && typeof e.reason === 'string'));
  return v;
}
export function reviewError(error) {
  if (error?.sessionChanged || error?.status === 401) return '로그인 계정이 변경됐습니다. 현재 계정에서 다시 열어주세요.';
  if ([403, 404].includes(error?.status)) return '현재 접근할 수 없는 리뷰입니다. 담당 또는 권한을 다시 확인해주세요.';
  if (error?.status === 409) return '리뷰나 자료가 변경됐습니다. 저장 결과를 확인한 뒤 현재 내용을 다시 불러와주세요.';
  if (error?.status === 429) return '요청이 많습니다. 잠시 후 다시 확인해주세요.';
  return error?.message || '현재 내용을 확인하지 못했습니다. 다시 시도해주세요.';
}
export const isCoach = (user) => user?.role === 'admin' || user?.roles?.includes('coach');
export const detailPath = (id, desk = false) => `${desk ? '/studio/hand-reviews' : '/reviews'}/${id}`;
export const reviewAPI = (id) => '/api/hand-reviews/' + id;
