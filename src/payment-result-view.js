import { esc, icon, money } from "./ui.js";
import { head, empty, date, statusLabel } from "./platform-ui.js";
import { resultCopy } from "./payment-result-model.js";

const purchases =
  '<a class="btn btn-outline" href="#/purchases">현재 계정의 내 주문 확인</a>';

function steps(value, access) {
  const stage = value.resultHint.stage,
    received = [
      "approval_pending",
      "approval_processing",
      "paid",
      "cancellation_pending",
      "refunded",
    ].includes(stage),
    paid = stage === "paid";
  return `<ol class="pr-steps" aria-label="결제에서 수강까지"><li class="${received ? "is-done" : ""}"><span>01</span><div><strong>인증 결과 전달</strong><small>${received ? "서버 기록 확인" : stage === "authorization_closed" ? "승인 요청 종료" : "승인 전 단계"}</small></div></li><li class="${paid ? "is-done" : ""}"><span>02</span><div><strong>결제 승인 검증</strong><small>${paid ? "승인 확인" : stage === "refunded" ? "전액 취소 확인" : stage === "cancellation_pending" ? "취소 결과 확인 중" : "서버 결과 확인"}</small></div></li><li class="${paid && access?.canAccess ? "is-done" : ""}"><span>03</span><div><strong>현재 수강권</strong><small>${paid && access?.canAccess ? "수강 가능" : "수강 시작에서 별도 확인"}</small></div></li></ol>`;
}

export function paymentResultBody(screen) {
  const refresh = screen.canRead
    ? '<button type="button" class="btn btn-outline" data-payment-refresh>서버 기록 다시 읽기</button>'
    : "";
  if (screen.type !== "order") {
    const copy = {
      invalid: [
        "돌아온 결제 정보를 확인해주세요.",
        "주문 번호와 금액이 불명확한 요청은 승인하지 않습니다.",
        "승인 요청을 보내지 않았습니다.",
      ],
      account: [
        "현재 계정에서 결제 결과를 확인해주세요.",
        "로그인 계정 또는 이용 권한이 바뀌어 개인 주문 정보를 표시하지 않습니다.",
        "주문을 시작한 계정에서 확인해주세요.",
      ],
      missing: [
        "현재 계정의 주문을 확인해주세요.",
        "조회할 수 있는 본인 주문을 찾지 못했습니다.",
        "새 결제를 시작하기 전에 내 주문을 확인하세요.",
      ],
      mismatch: [
        "돌아온 금액과 주문 기록이 다릅니다.",
        "서버에 기록된 주문과 일치하지 않아 승인 요청을 보내지 않았습니다.",
        "같은 주문의 서버 기록을 확인해주세요.",
      ],
      guest: [
        "인증 화면에서 돌아왔습니다.",
        "이 화면의 문구나 오류 코드만으로 승인·취소·환불 여부를 확정하지 않습니다.",
        "주문을 시작한 계정으로 확인해주세요.",
      ],
      detached: [
        "현재 화면에서 다시 확인해주세요.",
        "이전 화면의 결제 요청을 새로 실행하지 않았습니다.",
        "내 주문에서 확인할 수 있습니다.",
      ],
      unknown: [
        "결제 확인이 끝나지 않았습니다.",
        "아직 결제 완료나 취소로 확정할 수 없습니다.",
        "같은 주문의 서버 기록을 다시 읽어주세요.",
      ],
    }[screen.type] || [
      "현재 주문을 다시 확인해주세요.",
      "서버 기록을 불러오지 못했습니다.",
      "내 주문에서 확인할 수 있습니다.",
    ];
    const orderLink =
      screen.type === "guest" && screen.orderId
        ? `<a class="btn btn-primary" href="#/payment/order/${esc(screen.orderId)}">이 주문의 처리 상태·복구</a>`
        : "";
    return `${head("PAYMENT RETURN", copy[0], copy[1])}${empty(copy[2], screen.issue?.message || screen.notice || "기존 주문을 새로 결제하지 말고 해당 계정의 서버 기록을 확인해주세요.", `${refresh}${orderLink}${purchases}`)}`;
  }
  const { order, resultHint } = screen.value,
    stage = resultHint.stage,
    [title, description] = resultCopy[stage],
    access = screen.stale ? null : screen.start?.access;
  const heading = head(
    stage === "paid" ? "PAYMENT VERIFIED" : "ONE ORDER, ONE RECORD",
    screen.stale
      ? stage === "paid"
        ? "확인한 승인 기록을 보관하고 있습니다."
        : "최근 확인한 주문 기록입니다."
      : stage === "paid" && access?.canAccess
        ? "수강권이 열렸습니다."
        : title,
    screen.stale
      ? "최신 상태를 불러오지 못해 이전 기록을 표시합니다. 새 결제를 요청하지 않습니다."
      : description,
  );
  let actions = `<button type="button" class="btn btn-outline" data-payment-refresh>서버 기록 다시 읽기</button>`;
  if (screen.canConfirm)
    actions +=
      '<button type="button" class="btn btn-primary" data-payment-approve>이 주문의 승인 결과 확인</button>';
  if (
    ["approval_pending", "approval_processing", "authentication_open"].includes(
      stage,
    )
  )
    actions += `<a class="btn btn-primary" href="#/payment/order/${esc(order.id)}">같은 주문 확인·복구 ${icon("arrow", 17)}</a>`;
  if (stage === "paid")
    actions += `<a class="btn btn-primary" href="#/start/${esc(order.courseId)}">수강 시작·현재 수강권 확인 ${icon("arrow", 17)}</a>`;
  if (["prepared", "authorization_closed"].includes(stage))
    actions += `<a class="btn btn-outline" href="#/payment/order/${esc(order.id)}">${stage === "prepared" ? "구매 조건·주문 상태 확인" : "종료 기록·새 조건 확인"}</a>`;
  actions += purchases;
  return `${heading}${screen.notice ? `<p class="pr-return-note">${esc(screen.notice)} 인증 화면의 안내가 최종 결제 결과를 확정하지 않습니다.</p>` : ""}<article class="p-panel pr-result-card"><div class="pr-result-heading"><p class="eyebrow">${order.mode === "test" ? "TEST ORDER" : "YOUR ORDER"}</p><span class="status-tag ${stage === "paid" ? "mint" : ""}">${esc(statusLabel(order.status))}</span></div><h2>${esc(order.title)}</h2><strong class="pr-result-amount">${money(order.amount)}</strong><dl class="ck-summary"><div><dt>주문 번호</dt><dd>${esc(order.id)}</dd></div><div><dt>서버 최근 기록</dt><dd>${date(order.updatedAt)}</dd></div></dl>${steps(screen.value, access)}${screen.issue ? `<div class="pr-result-notice" role="status"><strong>확인이 더 필요합니다.</strong><p>${esc(screen.issue.message)}</p></div>` : ""}${stage === "paid" ? `<div class="pr-access-note"><strong>${access?.canAccess ? "현재 수강 가능한 상태입니다." : "결제 승인과 현재 수강권은 별도로 확인합니다."}</strong><p>${esc(screen.startError ? "현재 수강권 정보를 불러오지 못했습니다. 이 주문의 결제 승인은 서버에서 확인했습니다." : screen.start?.message || "수강 시작에서 현재 수강권과 다음 차시를 확인하세요.")}</p></div>` : ""}<div class="pr-result-actions">${actions}</div><p class="form-note">서버 기록 읽기는 새 결제·승인·취소를 요청하지 않습니다. 이미 시작된 승인은 같은 주문의 복구 화면에서 직접 확인합니다.</p></article>`;
}

export function paymentResultDocument(screen) {
  return `<section class="container p-page p-compose pr-result-page" id="payment-return"><div data-payment-result-body>${paymentResultBody(screen)}</div><p class="ck-status" data-payment-feedback role="status" aria-live="polite"></p></section>`;
}
