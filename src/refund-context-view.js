import { esc, money } from "./ui.js";
import { date, statusLabel } from "./platform-ui.js";

const reviewCopy = {
  operator_review_required: [
    "구매 당시 조건을 기준으로 검토합니다.",
    "환불 여부는 담당자가 당시 조건과 문의 내용을 함께 확인합니다. 아래 기록은 검토 근거입니다.",
  ],
  records_need_review: [
    "당시 조건 기록의 확인이 필요합니다.",
    "기록이 부족하다는 이유만으로 환불이 거절되거나 승인되지는 않습니다. 담당자가 원래 구매 조건을 확인해야 합니다.",
  ],
  payment_not_completed: [
    "결제 완료 기록을 먼저 확인해주세요.",
    "이 주문은 현재 결제 완료 상태가 아닙니다. 승인 결과 확인과 환불 문의를 구분해 안내합니다.",
  ],
  cancellation_pending: [
    "결제사 취소 결과를 확인 중입니다.",
    "아직 전액 취소가 확인되지 않았습니다. 현재 수강권은 아래의 별도 기록으로 확인합니다.",
  ],
  cancellation_verified: [
    "전액 취소 기록이 있습니다.",
    "해당 주문의 결제 출처 권한과 다른 출처의 권한을 구분해 확인합니다.",
  ],
};
const checks = {
  original_terms: [
    "구매 당시 환불·제공 조건",
    {
      recorded: "원문과 동의 연결 확인",
      unrecorded: "당시 기록 미수집",
      inconsistent: "기록 연결 확인 필요",
    },
  ],
  inquiry: [
    "취소 문의와 운영 답변",
    {
      none: "문의 없음",
      requested: "확인 대기",
      approved: "취소 처리 답변",
      rejected: "운영 답변 등록",
    },
  ],
  payment_result: [
    "현재 주문의 금융 처리 상태",
    Object.fromEntries(
      [
        "created",
        "pending",
        "confirming",
        "paid",
        "refunding",
        "refunded",
        "abandoned",
      ].map((s) => [s, statusLabel(s)]),
    ),
  ],
  learning_scope: [
    "학습 기록의 범위",
    { lifetime_records: "회원·강의 누적 기록", no_records: "저장된 기록 없음" },
  ],
  access_scope: [
    "이 주문의 권한과 다른 권한",
    {
      order_grants_recorded: "출처별로 구분",
      order_grants_missing: "주문 출처 권한 확인 필요",
    },
  ],
  commercial_decision: [
    "환불 조건·사유에 대한 판단",
    { operator_review_required: "담당자 검토 필요" },
  ],
};
const seconds = (v) =>
  `${Math.floor(v / 60)}:${String(Math.floor(v % 60)).padStart(2, "0")}`;
const grantLabel = {
  active: "현재 유효",
  scheduled: "시작 예정",
  expired: "기간 종료",
  revoked: "회수",
};
function grantCounts(value) {
  return Object.entries(grantLabel)
    .map(([key, label]) => `<span>${label} <b>${value[key]}</b></span>`)
    .join("");
}

export function refundContextShell() {
  return '<section class="rc-workspace" data-refund-context-root><header><p class="eyebrow">REFUND EVIDENCE</p><h2>환불 검토 근거</h2><p class="p-muted">구매 당시 조건과 현재 기록을 함께 확인합니다.</p></header><div data-refund-context-content></div><footer class="rc-footer"><p role="status" aria-live="polite" data-refund-context-feedback></p><button class="btn btn-outline btn-sm" type="button" data-refund-context-refresh>현재 기록 다시 조회</button><p class="p-muted">이 화면의 조회는 결제·취소 요청이나 문의 답변을 저장하지 않습니다.</p></footer></section>';
}

export function refundOrdersDeniedHTML() {
  return '<div class="p-notice" role="status"><strong>현재 계정에서 주문을 다시 확인해주세요.</strong><p>로그인 계정 또는 운영 권한이 바뀌어 이전 주문 목록과 처리 버튼을 지웠습니다.</p><a class="text-link" href="#/purchases">내 주문 다시 열기 ↗</a></div>';
}

export function refundContextHTML(value) {
  const {
    order: o,
    policy: p,
    learning: l,
    access: a,
    inquiry: q,
    paymentEvidence: e,
  } = value;
  const [title, explanation] = reviewCopy[value.review.state];
  const policy =
    p.status === "recorded"
      ? `<div class="rc-documents">${p.documents.map((d) => `<details class="rc-document"><summary><span>${d.kind === "refund" ? "환불 안내" : "상품 제공 조건"}</span><span>판본 ${d.edition} · 원문 펼치기</span></summary><div class="rc-document-body"><h4>${esc(d.title)}</h4><p class="p-muted">동의 ${date(d.acceptedAt)} · 시행 ${date(d.effectiveAt)}${d.expiresAt ? ` · 당시 만료 지정 ${date(d.expiresAt)}` : ""}</p>${d.summary ? `<p>${esc(d.summary)}</p>` : ""}<div class="rc-original">${esc(d.body)}</div></div></details>`).join("")}</div><p class="p-muted">주문 준비 ${date(p.sealedAt)}에 보존한 원문입니다. 현재 판매 조건과 구분합니다.</p>`
      : `<div class="rc-notice is-pending"><strong>${p.status === "unrecorded" ? "구매 당시 조건이 수집되지 않은 이전 주문입니다." : "당시 원문·동의의 연결을 확인해야 합니다."}</strong><p>담당자에게 구매 당시 조건을 문의해주세요. 현재 정책으로 과거 기록을 대신하지 않습니다.</p></div>`;
  const inquiry = q
    ? `<div class="rc-inquiry"><strong>${{ requested: "취소 문의 · 확인 대기", approved: "취소 처리 답변", rejected: "운영 답변 등록" }[q.status]}</strong><p class="rc-original">${esc(q.reason)}</p>${q.resolution ? `<div><span class="rc-label">운영 답변</span><p class="rc-original">${esc(q.resolution)}</p></div>` : ""}<small>${date(q.updatedAt)} 갱신</small></div>`
    : '<p class="p-muted">이 주문에 접수된 취소 문의가 없습니다.</p>';
  const videos = l.videoRecords.length
    ? `<details class="rc-document"><summary><span>영상 위치 기록</span><span>${l.videoRecords.length} / ${l.totalVideoRecords}개 원본</span></summary><div class="rc-video-list">${l.videoRecords.map((v) => `<article><div><strong>${esc(v.title)}</strong><span class="rc-source">${v.source === "current" ? "현재 영상 원본" : "이전 영상 원본"}</span></div><p>저장 위치 ${seconds(v.resumeSeconds)} · 최대 도달 ${seconds(v.furthestSeconds)}${v.furthestPercent === null ? " · 길이/도달 비율 미확인" : ` · 도달 지점 ${esc(v.furthestPercent)}%`}</p><small>${date(v.updatedAt)}</small></article>`).join("")}</div></details>`
    : '<p class="p-muted">원본에 연결된 영상 위치 기록이 없습니다.</p>';
  return `<div class="rc-order"><div><span class="rc-label">${o.mode === "test" ? "테스트 주문" : "구매 주문"} · ${esc(statusLabel(o.status))}</span><h3>${esc(o.title)}</h3><p class="rc-order-id">${esc(o.id)}</p></div><strong class="rc-amount">${money(o.amount)}</strong></div><div class="rc-notice ${["cancellation_pending", "records_need_review"].includes(value.review.state) ? "is-pending" : ""}"><strong>${title}</strong><p>${explanation}</p></div><section class="rc-section"><div class="rc-section-heading"><span>01</span><h3>구매 당시 조건</h3></div>${policy}</section><section class="rc-section"><div class="rc-section-heading"><span>02</span><h3>문의와 결제 처리</h3></div>${inquiry}<dl class="rc-facts"><div><dt>승인 검증 이벤트</dt><dd>${e.verifiedApprovalAt ? date(e.verifiedApprovalAt) : "별도 기록 없음"}</dd></div><div><dt>전액 취소 검증 이벤트</dt><dd>${e.verifiedCancellationAt ? date(e.verifiedCancellationAt) : "별도 기록 없음"}</dd></div>${e.unverifiedCancellationAt ? `<div><dt>취소 결과 미확인 이력</dt><dd>${date(e.unverifiedCancellationAt)}</dd></div>` : ""}</dl><p class="p-muted">문의 접수와 운영 답변은 취소 완료를 뜻하지 않습니다. 취소 결과 미확인 이력은 이후의 검증 완료 기록과 함께 읽어주세요.</p></section><section class="rc-section"><div class="rc-section-heading"><span>03</span><h3>학습 기록의 범위</h3></div><div class="rc-metrics"><div><span>현재 완료 표시</span><strong>${l.completedLessons}<small>개 차시</small></strong></div><div><span>저장된 퀴즈 시도</span><strong>${l.quizAttempts}<small>회</small></strong></div><div><span>영상 위치 기록</span><strong>${l.totalVideoRecords}<small>개 원본</small></strong></div></div><p class="rc-scope-note">이 회원이 이 강의에서 남긴 누적 기록입니다. 다른 수강권·이전 구매의 기록이 포함될 수 있습니다. 영상 위치 이동은 실제 시청 시간을 뜻하지 않으며, 이 주문의 소비량이나 환불 자격을 자동 판정하지 않습니다.</p>${videos}${l.hasMoreVideos ? `<p class="p-muted">최근 ${l.videoLimit}개 원본을 표시합니다. 전체 ${l.totalVideoRecords}개 기록은 보존됩니다.</p>` : ""}${l.legacyResumeRecords ? `<p class="p-muted">별도로 보존된 구형 이어보기 기록 ${l.legacyResumeRecords}개에는 원본별 최대 도달 비율이 없습니다.</p>` : ""}</section><section class="rc-section"><div class="rc-section-heading"><span>04</span><h3>전액 취소와 수강권 범위</h3></div><p class="rc-access-status">현재 ${a.canAccess ? "유효한 수강권 있음" : "유효한 수강권 없음"} <small>· 조회 시점 기준</small></p><div class="rc-grants"><div><strong>이 주문이 발급한 권한 <b>${a.orderGrants.total}</b></strong><div>${grantCounts(a.orderGrants)}</div></div><div><strong>다른 출처의 권한 <b>${a.otherGrants.total}</b></strong><div>${grantCounts(a.otherGrants)}</div></div></div><p class="p-muted">검증된 전액 취소는 해당 주문의 결제 출처 권한만 회수합니다. 별도 발급·다른 주문의 권한은 각각의 조건을 따르며 학습 기록은 보존됩니다. 실제 학습 접속은 계정·강의 제공 상태를 함께 확인합니다.</p></section><section class="rc-section"><div class="rc-section-heading"><span>05</span><h3>담당자 검토 항목</h3></div><ol class="rc-checklist">${value.review.checklist.map((c) => `<li><span>${checks[c.id][0]}</span><strong>${checks[c.id][1][c.status]}</strong></li>`).join("")}</ol><p class="p-muted">확인된 기록과 담당자의 판단을 구분합니다. 이 화면은 환불 승인이나 거절을 저장하지 않습니다.</p></section><p class="rc-observed">조회 ${date(value.observedAt)}${o.paidAt ? ` · 주문 승인 기록 ${date(o.paidAt)}` : ""}</p>${value.audience === "operator" ? `<details class="rc-record-id"><summary>검토 근거 식별</summary><p>읽은 내용의 식별자입니다. 취소 승인 토큰이나 저장된 판단 이력이 아닙니다.</p><code>${esc(value.contextHash)}</code></details>` : ""}`;
}
