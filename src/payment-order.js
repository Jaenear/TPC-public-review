import { esc, money } from "./ui.js";
import { authGate, head, date, statusLabel } from "./platform-ui.js";
import {
  checkedOrderState,
  purchaseStatusLabel,
  orderStateMessage,
} from "./payment-order-model.js";

function orderBody(value) {
  const { order: o, checkoutState: s } = value;
  return `<div class="p-panel ck-recovery-card"><div class="ck-recovery-status"><p class="eyebrow">${o.mode === "test" ? "TEST ORDER" : "YOUR ORDER"}</p><span class="status-tag ${s.status === "paid" ? "mint" : ""}">${esc(purchaseStatusLabel(s.status, statusLabel))}</span></div><h2>${esc(o.title)}</h2><strong class="ck-record-amount">${money(o.amount)}</strong><dl class="ck-summary"><div><dt>주문 번호</dt><dd>${esc(o.id)}</dd></div><div><dt>주문 생성</dt><dd>${date(o.createdAt)}</dd></div><div><dt>최근 기록</dt><dd>${date(o.updatedAt)}</dd></div></dl><p class="p-notice">${esc(orderStateMessage(s))}</p><div class="ck-actions"><button class="btn btn-outline" type="button" data-order-refresh>서버 상태 새로 확인</button>${s.canReconcile ? '<button class="btn btn-primary" type="button" data-order-reconcile>같은 주문의 결제사 결과 확인</button>' : ""}${s.status === "paid" ? `<a class="btn btn-primary" href="#/start/${esc(o.courseId)}">수강 시작·현재 수강권 확인 →</a>` : ""}${["created", "abandoned"].includes(s.status) ? `<a class="btn btn-outline" href="#/checkout/${esc(o.courseId)}">${s.status === "abandoned" ? "최신 조건으로 새 주문 준비" : "이 강의의 구매 조건 확인"}</a>` : ""}<a class="text-link" href="#/purchases">전체 주문 내역 ↗</a></div>${s.canCloseAuthorization ? `<details class="ck-close-request"><summary>아직 승인되지 않은 이 요청을 종료하려면</summary><p>다른 창의 인증 화면을 닫은 뒤 진행해주세요. 서버에 결제 식별자가 저장되었거나 승인이 시작된 주문은 여기서 종료할 수 없습니다.</p><label><input type="checkbox" data-order-close-confirm> 이 주문의 향후 승인을 중단하고 새 조건을 확인하겠습니다.</label><button class="btn btn-outline btn-block" type="button" data-order-close disabled>이 주문의 승인 요청 종료</button><p class="form-note">결제사의 결제 취소·환불 요청은 보내지 않습니다. 기록을 삭제하지 않으며 종료한 주문의 인증 결과는 이후 승인하지 않습니다.</p></details>` : ""}</div>`;
}

export async function paymentOrderPage(orderId, ctx) {
  if (!ctx.user)
    return authGate(ctx, "주문을 시작한 계정으로 처리 상태를 확인해주세요.");
  const ownerId = ctx.user.id;
  let initial;
  try {
    initial = checkedOrderState(
      await ctx.api(
        `/api/checkout/orders/${encodeURIComponent(orderId)}/state`,
        { ownerId },
      ),
      ownerId,
      orderId,
    );
  } catch (error) {
    return {
      html: `<section class="container p-page p-compose">${head("ORDER RECOVERY", "조회할 수 있는 주문을 확인해주세요.", "본인 계정의 주문만 표시합니다.")}<p class="p-notice">${esc(error.message)}</p><a class="btn btn-outline" href="#/purchases">내 주문 확인</a></section>`,
    };
  }
  return {
    html: `<section class="container p-page p-compose ck-recovery-page" id="payment-order"><a class="back-link" href="#/purchases">← 내 주문</a>${head("ONE ORDER, ONE RECORD", "진행 중인 주문을 같은 기록으로 확인하세요.", "다른 창·기기에서 시작했거나 인증 결과를 받지 못했을 때도 서버에 남은 상태를 확인합니다.")}<div data-order-body>${orderBody(initial)}</div><p class="ck-status" role="status" aria-live="polite" data-order-feedback>현재 서버 상태를 불러왔습니다. 결제사 조회·승인이나 새 인증은 자동으로 요청하지 않습니다.</p></section>`,
    bind() {
      bindOrderRecovery(document.querySelector("#payment-order"), initial, ctx);
    },
  };
}

export function bindOrderRecovery(root, initial, ctx) {
  const ownerId = ctx.user.id,
    orderId = initial.order.id,
    route = location.hash,
    abort = new AbortController();
  let value = initial,
    busy = false,
    disposed = false;
  const active = () => !disposed && root.isConnected && location.hash === route;
  const area = root.querySelector("[data-order-body]"),
    feedback = root.querySelector("[data-order-feedback]");
  function message(text, error = false) {
    if (!active()) return;
    feedback.textContent = text;
    feedback.classList.toggle("is-error", error);
  }
  function sync() {
    if (!active()) return;
    root.querySelectorAll("button").forEach((b) => (b.disabled = busy));
    const confirm = root.querySelector("[data-order-close-confirm]"),
      close = root.querySelector("[data-order-close]");
    if (confirm) confirm.disabled = busy;
    if (close) close.disabled = busy || !confirm?.checked;
  }
  function draw() {
    area.innerHTML = orderBody(value);
    root.querySelector("[data-order-refresh]").onclick = () => operate("read");
    const reconcile = root.querySelector("[data-order-reconcile]");
    if (reconcile) reconcile.onclick = () => operate("reconcile");
    const close = root.querySelector("[data-order-close]");
    if (close) close.onclick = () => operate("close");
    root
      .querySelector("[data-order-close-confirm]")
      ?.addEventListener("change", sync, { signal: abort.signal });
    sync();
  }
  async function operate(kind) {
    if (!active() || busy) return;
    if (
      kind === "close" &&
      !root.querySelector("[data-order-close-confirm]")?.checked
    )
      return;
    busy = true;
    sync();
    message(
      kind === "read"
        ? "현재 서버 상태를 확인합니다…"
        : kind === "close"
          ? "승인이 시작되지 않은 주문인지 확인하고 요청을 종료합니다…"
          : "같은 주문의 결제사 결과를 확인합니다…",
    );
    try {
      let observation;
      if (kind === "close")
        await ctx.api(`/api/checkout/orders/${orderId}/close-authorization`, {
          method: "POST",
          ownerId,
          body: { version: value.checkoutState.version },
        });
      else if (kind === "reconcile")
        observation = (
          await ctx.api(`/api/checkout/orders/${orderId}/reconcile`, {
            method: "POST",
            ownerId,
            body: {},
          })
        ).providerObservation;
      if (!active()) return;
      const next = checkedOrderState(
        await ctx.api(`/api/checkout/orders/${orderId}/state`, { ownerId }),
        ownerId,
        orderId,
      );
      if (!active()) return;
      value = next;
      draw();
      const providerNote = {
        READY:
          "결제사에서 아직 인증 전으로 조회했습니다. 새 인증을 자동 요청하지 않습니다.",
        WAITING_FOR_DEPOSIT:
          "결제사에서 입금 대기로 조회했습니다. 수강권은 승인 완료 검증 전까지 발급하지 않습니다.",
        PARTIAL_CANCELED:
          "결제사에서 부분 취소 상태를 반환했습니다. 운영자에게 같은 주문을 문의해주세요.",
      };
      message(
        providerNote[observation?.status] ||
          "현재 서버 기록을 확인했습니다. " +
            orderStateMessage(value.checkoutState),
      );
    } catch (error) {
      if (!active()) return;
      if (error.sessionChanged || [401, 403].includes(error.status)) {
        area.replaceChildren();
        observer.disconnect();
        disposed = true;
        abort.abort();
        feedback.textContent =
          "로그인 계정 또는 이용 권한이 바뀌었습니다. 현재 계정에서 내 주문을 다시 열어주세요.";
        feedback.classList.add("is-error");
      } else
        message(
          error.message +
            " 요청 결과가 불확실하면 서버 상태를 다시 확인해주세요.",
          true,
        );
    } finally {
      busy = false;
      sync();
    }
  }
  const dispose = () => {
    if (disposed) return;
    disposed = true;
    abort.abort();
    observer.disconnect();
  };
  const observer = new MutationObserver(() => {
    if (!root.isConnected) dispose();
  });
  observer.observe(document.body, { childList: true, subtree: true });
  window.addEventListener(
    "hashchange",
    () => {
      if (location.hash !== route) dispose();
    },
    { signal: abort.signal },
  );
  window.addEventListener("pagehide", dispose, { signal: abort.signal });
  draw();
  return { dispose };
}
