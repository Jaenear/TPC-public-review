import { esc, money } from "./ui.js";
import { date, statusLabel, tag } from "./platform-ui.js";
import { refundContextHTML } from "./refund-context-view.js";

export const blankRefundDraft = () => ({
  reason: "",
  decision: "",
  basis: "",
  operatorNote: "",
  publicMessage: "",
  cancelReason: "",
  checks: [],
  confirm: false,
});
const titles = {
  inquiry_submitted: "취소 문의 접수",
  review_saved: "운영 검토 저장 · 내부 기록",
  reply_sent: "운영 답변 전달",
  cancellation_started: "같은 주문의 취소 결과 확인",
  cancellation_unverified: "결제사 결과 미확인",
  cancellation_verified: "전액 취소 확인",
  legacy_recovery_started: "이전 취소 작업 복구 시작",
};
const field = (name, label, value, min, max, rows = 3) =>
  `<label class="field">${label}<textarea name="${name}" rows="${rows}" minlength="${min}" maxlength="${max}" ${min ? "required" : ""}>${esc(value)}</textarea></label>`;
const labels = {
  terms: "구매 당시 조건과 누락된 기록을 확인했습니다.",
  inquiry: "회원 문의와 이전 답변을 확인했습니다.",
  learning: "학습 기록의 범위와 한계를 확인했습니다.",
  access: "이 주문과 다른 출처의 수강권을 구분했습니다.",
};

function timeline(events, more) {
  return `<section class="rf-card"><div class="rf-card-head"><div><p class="rf-kicker">ACTIVITY</p><h2>문의부터 처리까지</h2></div><span class="rf-mini">최신순</span></div>${events.length ? `<ol class="rf-timeline">${events.map((e) => `<li class="${e.kind === "cancellation_verified" ? "is-complete" : ""}"><div class="rf-event-head"><strong>${titles[e.kind]}</strong><time datetime="${esc(e.createdAt)}">${date(e.createdAt)}</time></div><p>${esc(e.body)}</p>${e.kind === "review_saved" && e.reviewId ? `<button class="text-link" type="button" data-rf-review-read="${esc(e.reviewId)}">저장 당시 검토 원본 보기 ↗</button>` : ""}</li>`).join("")}</ol>` : '<div class="rf-empty"><span>01</span><h3>이 주문의 다음 기록을 기다립니다.</h3><p>문의를 접수하거나 담당자가 처리하면 이곳에 쌓입니다.</p></div>'}${more ? '<button class="btn btn-outline btn-sm" type="button" data-rf-more>이전 처리 이력 더 보기</button>' : ""}</section>`;
}
function inquiryForm(value, d) {
  if (!value.actions.canInquire)
    return `<section class="rf-card rf-side"><p class="rf-kicker">YOUR REQUEST</p><h2>${value.context.inquiry ? "문의와 답변을 한곳에서." : "현재 주문 상태를 확인하세요."}</h2><p class="p-muted">${value.context.inquiry ? "접수한 문의는 원래 사유와 함께 보존됩니다. 운영 답변과 취소 확인 결과가 왼쪽 이력에 표시됩니다." : "결제 완료된 주문에서 취소 문의를 접수할 수 있습니다."}</p>${value.context.inquiry ? `<div class="rf-public"><strong>접수한 문의</strong><p>${esc(value.context.inquiry.reason)}</p>${value.context.inquiry.resolution ? `<strong>운영 답변</strong><p>${esc(value.context.inquiry.resolution)}</p>` : ""}</div>` : ""}<a class="text-link" href="#/payment/order/${esc(value.context.order.id)}">주문 처리 상태 확인 ↗</a></section>`;
  return `<section class="rf-card rf-side"><p class="rf-kicker">CONTACT THE CAMP</p><h2>어떤 도움이 필요한가요?</h2><p class="p-muted">구매 당시 조건과 문의 내용을 함께 확인합니다. 접수만으로 결제가 취소되지는 않습니다.</p><form data-rf-form="inquiry">${field("reason", "취소 문의 사유", d.reason, 5, 2000, 5)}<button class="btn btn-primary" type="submit" data-rf-submit>문의 접수</button><p class="form-note">5–2,000자 · 접수 기록은 내 계정에 연결됩니다.</p></form></section>`;
}
function reviewForm(v, d) {
  if (!v.actions.canReview) return "";
  const reply = d.decision === "reply";
  return `<section class="rf-card rf-side"><p class="rf-kicker">01 / OPERATOR REVIEW</p><h2>기록을 확인하고 판단을 남깁니다.</h2><p class="p-muted">저장은 내부 검토 단계입니다. 회원 답변 또는 결제사 취소는 저장 후 별도로 실행합니다.</p><form data-rf-form="review"><label class="field">검토 방향<select name="decision" required><option value="">직접 선택해주세요</option><option value="full_cancel" ${d.decision === "full_cancel" ? "selected" : ""}>전액 취소 검토</option><option value="reply" ${reply ? "selected" : ""} ${v.context.inquiry?.status !== "requested" ? "disabled" : ""}>취소하지 않고 문의에 답변</option></select></label><fieldset class="rf-checks"><legend>현재 근거를 직접 확인</legend>${Object.entries(
    labels,
  )
    .map(
      ([key, label]) =>
        `<label><input type="checkbox" name="check" value="${key}" ${d.checks.includes(key) ? "checked" : ""} required><span>${label}</span></label>`,
    )
    .join(
      "",
    )}</fieldset>${field("basis", "검토 근거 · 운영자 전용", d.basis, 10, 4000)}${field("operatorNote", "추가 메모 · 운영자 전용, 선택", d.operatorNote, 0, 4000, 2)}${field("publicMessage", "회원에게 전달할 안내", d.publicMessage, 5, 2000)}<div data-rf-cancel-field ${reply ? "hidden" : ""}>${field("cancelReason", "회원·결제사에 남기는 취소 사유", d.cancelReason, reply ? 0 : 5, 200, 2)}</div><p class="form-note">회원 안내는 답변 실행 또는 검증된 취소 완료 후 공개됩니다. 검토 근거와 추가 메모는 회원에게 공개하지 않습니다.</p><button class="btn btn-primary" type="submit" data-rf-submit>검토만 저장</button></form></section>`;
}
function reviewSent(v) {
  return v.events.some(
    (e) => e.kind === "reply_sent" && e.reviewId === v.currentReview?.id,
  );
}
function execution(v, d) {
  const r = v.currentReview;
  if (v.operation || v.legacyRecovery)
    return `<section class="rf-card rf-side"><p class="rf-kicker">02 / CANCELLATION RESULT</p><h2>${v.operation?.status === "verified" ? "전액 취소가 확인됐습니다." : "원래 작업의 결과를 확인합니다."}</h2><p class="p-muted">${v.operation?.status === "verified" ? "해당 주문의 결제 출처 수강권에만 취소 결과를 반영했습니다. 학습 기록과 다른 출처의 수강권은 각각 보존됩니다." : "원래 사유와 같은 결제 식별자로 확인합니다. 새 사유나 새 주문으로 취소를 만들지 않습니다."}</p>${v.operation?.status !== "verified" ? `<p class="rf-wait">${v.actions.retryAfterSeconds ? `서버 재확인 대기 ${v.actions.retryAfterSeconds}초 · 시각 이후 현재 기록을 다시 조회해주세요.` : "현재 서버 기록에서 재확인 가능 여부를 확인했습니다."}${v.operation?.recheckAt ? `<br>재확인 시각 ${date(v.operation.recheckAt)}` : ""}</p><form data-rf-form="recheck"><label class="rf-confirm"><input type="checkbox" name="confirm" ${d.confirm ? "checked" : ""} required><span>같은 취소 작업의 결과를 결제사에 다시 확인하겠습니다.</span></label><button class="btn btn-outline" type="submit" data-rf-submit ${v.actions.canRecheck ? "" : "disabled"}>같은 작업 결과 재확인</button></form>` : ""}</section>`;
  if (!r) return "";
  const cancel = r.decision === "full_cancel";
  return `<section class="rf-card rf-side rf-execution"><p class="rf-kicker">02 / EXPLICIT ACTION</p><h2>${reviewSent(v) ? "회원에게 안내를 전달했습니다." : "저장된 검토를 실행할까요?"}</h2>${tag(reviewSent(v) ? "운영 답변 전달 완료" : r.validForCurrentFacts ? "현재 근거와 일치" : "근거 변경 · 새 검토 필요", reviewSent(v) || r.validForCurrentFacts ? "mint" : "amber")}<dl class="rf-review-summary"><div><dt>검토 방향</dt><dd>${cancel ? "결제사 전액 취소" : "취소 없이 운영 답변"}</dd></div><div><dt>저장</dt><dd>${date(r.createdAt)}</dd></div></dl><div class="rf-public"><strong>회원에게 전달할 안내</strong><p>${esc(r.publicMessage)}</p>${cancel ? `<strong>회원·결제사 취소 사유</strong><p>${esc(r.cancelReason)}</p>` : ""}</div><button type="button" class="text-link" data-rf-review-read="${esc(r.id)}">검토 원본 확인 ↗</button>${reviewSent(v) ? "" : `<form data-rf-form="execute"><label class="rf-confirm"><input type="checkbox" name="confirm" ${d.confirm ? "checked" : ""} required><span>${cancel ? `${money(v.context.order.amount)} 전액 취소를 결제사에 요청하고, 검증 완료 후 이 주문의 수강권을 회수하겠습니다.` : "결제 취소 없이 위 안내를 회원에게 전달하겠습니다."}</span></label><button class="btn ${cancel ? "btn-danger" : "btn-primary"}" type="submit" data-rf-submit ${v.actions.canExecute ? "" : "disabled"}>${cancel ? "저장된 검토로 전액 취소 실행" : "저장된 안내를 회원에게 전달"}</button></form>`}</section>`;
}
export function refundWorkflowBody(
  v,
  d = blankRefundDraft(),
  events = v.events,
  more = v.page.nextBefore,
) {
  const o = v.context.order,
    operator = v.audience === "operator";
  const step =
    o.status === "refunded"
      ? 3
      : o.status === "refunding"
        ? 2
        : v.context.inquiry
          ? 1
          : 0;
  return `<div class="rf-order"><div><p class="rf-kicker">${operator ? "COMMERCE DESK" : "YOUR PURCHASE"} ${o.mode === "test" ? " / TEST" : ""}</p><h1>${esc(o.title)}</h1><p class="rf-order-id">${esc(o.id)}</p></div><div class="rf-total">${tag(statusLabel(o.status), o.status === "refunded" ? "mint" : "")}<strong>${money(o.amount)}</strong></div></div><ol class="rf-steps" aria-label="문의 처리 단계">${["문의 접수", "운영 검토", "결제사 확인", "취소 완료"].map((label, i) => `<li class="${step >= i ? "is-current" : ""}"><span>0${i + 1}</span><strong>${label}</strong></li>`).join("")}</ol>${!["paid", "refunding", "refunded"].includes(o.status) ? '<p class="rc-notice is-pending">결제 완료 여부를 먼저 확인해주세요. 현재 주문에서는 취소 문의나 새 검토를 저장할 수 없습니다.</p>' : ""}${operator && !v.gateway.matchesOrder ? '<div class="rc-notice is-pending"><strong>이 주문과 연결된 결제 상점 설정을 확인해야 합니다.</strong><p>현재 기록 조회와 검토 저장은 가능합니다. 전액 취소·결제사 재확인은 상점과 결제 모드가 준비된 뒤 실행할 수 있습니다.</p></div>' : ""}${v.historyCoverage === "legacy_state_only" ? '<p class="rf-history-note">이전 문의의 현재 상태만 보존된 주문입니다. 과거 단계나 검토 이력을 새로 만들어 표시하지 않습니다.</p>' : ""}<div class="rf-grid"><div class="rf-main">${timeline(events, more)}<details class="rf-card rf-evidence"><summary><div><p class="rf-kicker">ORIGINAL RECORDS</p><h2>구매 조건·학습·수강권 근거</h2><p>구매 당시 원문과 현재 기록을 펼쳐 확인하세요.</p></div><span aria-hidden="true">＋</span></summary><div class="rc-workspace">${refundContextHTML(v.context)}</div></details></div><aside class="rf-aside" aria-label="${operator ? "검토 및 실행" : "내 문의"}">${operator ? execution(v, d) + (v.currentReview && v.actions.canReview ? `<details class="rf-new-review" ${v.currentReview.validForCurrentFacts || reviewSent(v) ? "" : "open"}><summary>현재 근거로 새 검토 작성</summary>${reviewForm(v, d)}</details>` : reviewForm(v, d)) : inquiryForm(v, d)}${operator && !v.actions.canReview && !v.operation && !v.legacyRecovery ? '<div class="rf-card"><h2>현재는 조회할 수 있는 주문입니다.</h2><p class="p-muted">금융 상태와 기존 문의 이력을 확인해주세요.</p></div>' : ""}</aside></div>`;
}
export function refundRecoveryHTML(s) {
  if (!s.pending) return "";
  return `<div class="rf-recovery"><strong>원래 요청의 접수 기록을 확인해주세요.</strong><p>요청 번호 <code>${esc(s.pending.requestKey)}</code></p><p>이 화면은 미확인 요청을 자동으로 다시 실행하지 않습니다.</p><div class="p-form-actions"><button type="button" class="btn btn-outline btn-sm" data-rf-recover>접수 기록 다시 확인</button>${s.canRetry ? '<button type="button" class="btn btn-outline btn-sm" data-rf-retry>원래 내용·같은 번호로 다시 접수</button>' : ""}${s.notFound ? '<button type="button" class="text-link" data-rf-release>미접수 조회 확인 · 최신 상태에서 다시 작성</button>' : ""}</div></div>`;
}
