import { esc, money, icon } from "./ui.js";
import { consentChecklist } from "./policies.js";
import { policyKinds } from "./policy-model.js";
import { date } from "./platform-ui.js";

export function purchaseCheckoutNotice(order, recovery) {
  if (order.status === "abandoned")
    return '<p class="p-notice">이 주문의 승인 요청이 종료되었습니다. 기록은 보존되며 최신 조건으로 새 주문을 준비할 수 있습니다.</p>';
  if (order.status === "pending")
    return '<p class="p-notice amber">이 주문의 인증·승인 결과를 확인해야 합니다. 저장 정보를 지웠거나 다른 기기에서 접속해도 서버의 같은 주문으로 처리 상태를 확인해주세요.</p>';
  if (order.status !== "created") return "";
  const text = recovery?.paymentRequested
    ? "이 브라우저에서 시작한 이 강의의 인증 결과를 먼저 확인해주세요. 결제를 자동으로 다시 요청하지 않습니다."
    : recovery
      ? "이 강의의 주문 준비 요청이 남아 있습니다. 원래 요청의 결과를 먼저 확인해주세요."
      : "주문 준비 상태입니다. 결제 완료로 확인된 주문은 아닙니다.";
  return `<p class="p-notice ${recovery ? "amber" : ""}">${text}</p>`;
}

export function checkoutChoices(policy) {
  return ["test", "live"].includes(policy?.mode) &&
    Array.isArray(policy?.documents) &&
    Array.isArray(policy?.blockers)
    ? consentChecklist(policy)
    : '<p class="p-notice">상품의 가격과 제공 조건을 준비 중입니다.</p>';
}

export function checkoutQuote(course, payment, policy) {
  const amount = policy?.course?.amount;
  return `<span class="eyebrow">YOUR NEXT CHAPTER</span><h2>${esc(policy?.course?.title || course.title)}</h2><p class="p-muted">${esc(course.subtitle)}</p><dl class="ck-summary"><div><dt>강의 구성</dt><dd>${course.lessons.length}개 차시</dd></div><div><dt>구매 방식</dt><dd>강의 단건 구매</dd></div><div class="ck-amount"><dt>${payment.mode === "test" ? "테스트 결제 금액" : "현재 확인 금액"}</dt><dd>${Number.isSafeInteger(amount) ? money(amount) : "가격 확인 중"}</dd></div></dl><p class="ck-mode ${payment.mode === "test" ? "is-test" : ""}">${payment.mode === "test" ? "테스트 환경 · 실제 금액이 차감되지 않습니다." : payment.configured ? "서버 승인 확인 후 수강권이 발급됩니다." : "결제 연결을 준비 중입니다. 현재 결제는 발생하지 않습니다."}</p>`;
}

export function checkoutRecord(prepared, preview) {
  const o = prepared.order,
    snapshot = preview?.purchaseSnapshot;
  return `<p class="eyebrow">PREPARED ORDER</p><h3>확인한 주문</h3><p>${esc(o.title)}</p><strong class="ck-record-amount">${money(o.amount)}</strong><dl class="ck-summary"><div><dt>주문 번호</dt><dd>${esc(o.id)}</dd></div><div><dt>준비 시각</dt><dd>${date(prepared.preparation.createdAt)}</dd></div><div><dt>결제 준비 기한</dt><dd>${date(o.policy.paymentDeadline)}</dd></div></dl>${snapshot?.recorded ? `<div class="ck-record-docs"><p>주문 준비 시 확인한 문서</p>${snapshot.policy.documents.map((d) => `<a target="_blank" rel="noopener" href="#/policies/${esc(d.id)}">${esc(policyKinds[d.kind])} · ${esc(d.edition)}판 ↗</a>`).join("")}</div>` : ""}<p class="form-note">주문 준비만으로 결제되거나 수강권이 발급되지 않습니다.</p>`;
}

export function checkoutHTML(course, payment, policy) {
  return `<section class="container p-page ck-page" id="course-checkout"><a class="back-link" href="#/course/${esc(course.id)}">← 강의 상세</a><header class="ck-heading"><p class="eyebrow">CONTINUE YOUR LEARNING</p><h1>다음 배움을,<br><span>차근차근 준비하세요.</span></h1><p>제공 조건을 확인하고, 내 계정의 주문으로 연결합니다.</p></header><ol class="ck-steps" aria-label="주문 준비 단계"><li data-checkout-step="0"><span>01</span><div>조건 확인<small>가격과 제공 문서</small></div></li><li data-checkout-step="1"><span>02</span><div>주문 준비<small>내 계정에 기록</small></div></li><li data-checkout-step="2"><span>03</span><div>결제 요청<small>결제사 인증·서버 확인</small></div></li></ol><div class="ck-layout"><div class="ck-main"><article class="p-panel ck-workspace"><form data-checkout-choices data-no-session-draft><fieldset data-checkout-fieldset><div data-checkout-policy>${checkoutChoices(policy)}</div><p class="ck-count" data-checkout-count>${policy.ready && policy.requiredPolicyIds?.length ? `필수 문서 0 / ${policy.requiredPolicyIds.length} 확인` : "구매 조건 준비 중"}</p></fieldset></form><div class="ck-record" data-checkout-record hidden></div><div class="ck-status" data-checkout-feedback role="status" aria-live="polite">${policy.ready && payment.configured ? "필수 문서를 읽고 각 항목을 직접 체크해주세요." : "현재 구매 준비 상태를 확인해주세요."}</div><div class="ck-actions"><button type="button" class="btn btn-primary btn-block" data-prepare disabled>주문 준비하기 ${icon("arrow", 18)}</button><button type="button" class="btn btn-primary btn-block" data-recover hidden>원래 주문 준비 결과 확인</button><button type="button" class="btn btn-outline btn-block" data-retry-request hidden>같은 주문 준비 요청 다시 확인</button><button type="button" class="btn btn-primary btn-block" data-load-methods hidden>이 주문의 결제수단 불러오기</button><button type="button" class="btn btn-primary btn-block" data-pay hidden>결제 요청</button><a class="btn btn-primary btn-block" href="#/purchases" data-view-orders hidden>내 주문·처리 결과 확인 ↗</a><button type="button" class="btn btn-outline btn-block" data-refresh-conditions>최신 가격·제공 조건 다시 확인</button></div><div class="ck-sdk" data-checkout-sdk hidden><div data-toss-methods></div><div data-toss-agreements></div></div><p class="form-note">연결이 복구되거나 다시 로그인해도 결제 요청을 자동으로 재전송하지 않습니다. 결과가 불확실할 때는 같은 주문을 확인해주세요.</p></article>${course.pilot ? '<article class="p-panel"><p class="eyebrow">PILOT ENROLLMENT</p><h2>먼저 수강을 문의할 수 있어요.</h2><p class="p-muted">파일럿 신청은 결제 없이 접수하며, 운영자가 제공 조건을 확인합니다.</p><button type="button" class="btn btn-outline" data-pilot-inquiry>결제 없이 파일럿 수강 신청</button></article>' : ""}</div><aside class="ck-aside"><article class="p-panel" data-checkout-quote>${checkoutQuote(course, payment, policy)}</article><div class="ck-next"><h3>배움은 이렇게 이어집니다.</h3><ol><li>내 계정에서 상품과 조건 확인</li><li>결제사 인증 후 서버 승인 검증</li><li>수강권 발급·차시 학습과 Q/A</li></ol><a class="text-link" href="#/purchases">내 주문·처리 결과 확인 ↗</a><p class="form-note">취소 문의와 확인 중인 결제 결과도 내 주문에서 확인할 수 있습니다.</p></div></aside></div></section>`;
}
