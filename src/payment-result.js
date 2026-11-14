import { authGate } from "./platform-ui.js";
import {
  parsePaymentCallback,
  checkedConfirmedOrder,
} from "./payment-order-model.js";
import {
  checkedPaymentResult,
  confirmedPaymentResult,
  maySubmitCallback,
  accountDenied,
  resultFailure,
  callbackNotice,
} from "./payment-result-model.js";
import {
  paymentResultBody,
  paymentResultDocument,
} from "./payment-result-view.js";

// Only pending requests are shared. A later visit always reads the server.
const confirmations = new Map();
async function confirmOnce(body, ownerId, ctx) {
  const key = JSON.stringify([
    ownerId,
    body.orderId,
    body.paymentKey,
    body.amount,
  ]);
  let promise = confirmations.get(key);
  if (!promise) {
    promise = Promise.resolve().then(() =>
      ctx.api("/api/checkout/confirm", {
        method: "POST",
        ownerId,
        body,
      }),
    );
    confirmations.set(key, promise);
  }
  try {
    return checkedConfirmedOrder(await promise, ownerId, body);
  } finally {
    if (confirmations.get(key) === promise) confirmations.delete(key);
  }
}

const unavailable = (error) => ({
  type: accountDenied(error)
    ? "account"
    : error?.status === 404
      ? "missing"
      : "unknown",
  issue: resultFailure(error),
  canRead: !accountDenied(error) && error?.status !== 404,
});
const inactive = () => ({ type: "detached", canRead: false });

function previousRecord(value, ownerId, callback, kind, error) {
  if (
    !value ||
    accountDenied(error) ||
    error?.invalidPaymentResult ||
    !(
      error?.status === undefined ||
      error.status === 429 ||
      error.status >= 500
    )
  )
    return null;
  try {
    checkedPaymentResult(value, ownerId, callback.orderId);
  } catch {
    return null;
  }
  return {
    type: "order",
    value,
    stale: true,
    canRead: true,
    canConfirm: false,
    start: null,
    startError: value.resultHint.stage === "paid",
    notice: kind === "fail" ? callbackNotice(callback.code) : null,
    issue: {
      code: "stale",
      message:
        "최신 주문 상태를 읽지 못했습니다. 표시된 주문은 이전에 확인한 기록입니다. 현재 결과는 다시 조회해 확인해주세요.",
    },
  };
}

/** This loader never creates an order, opens an SDK, cancels or refunds. */
export async function loadPaymentResult(
  kind,
  callback,
  ctx,
  {
    allowInitial = false,
    isCurrent = () => true,
    previousIssue = null,
    previousValue = null,
  } = {},
) {
  const ownerId = ctx.user?.id;
  if (!ownerId) return { type: "account", canRead: false };
  const read = async () =>
    checkedPaymentResult(
      await ctx.api(
        `/api/checkout/orders/${encodeURIComponent(callback.orderId)}/state`,
        { ownerId },
      ),
      ownerId,
      callback.orderId,
    );
  let value,
    issue = previousIssue;
  try {
    value = await read();
  } catch (error) {
    return (
      previousRecord(previousValue, ownerId, callback, kind, error) ||
      unavailable(error)
    );
  }
  if (!isCurrent()) return inactive();
  if (issue?.code === "stale") issue = null;
  if (kind === "success" && value.order.amount !== callback.amount)
    return { type: "mismatch", canRead: true };

  if (kind === "success" && allowInitial && maySubmitCallback(value)) {
    let confirmed;
    try {
      // The read hint is advisory; the ordinary confirm transaction is final.
      if (!isCurrent()) return inactive();
      confirmed = confirmedPaymentResult(
        await confirmOnce(callback, ownerId, ctx),
        ownerId,
      );
      if (!isCurrent()) return inactive();
      issue = null;
    } catch (error) {
      if (!isCurrent()) return inactive();
      if (accountDenied(error)) return unavailable(error);
      issue = resultFailure(error);
    }
    // A committed approval with a lost HTTP response can be found by GET.
    try {
      value = await read();
    } catch (error) {
      if (accountDenied(error)) return unavailable(error);
      return (
        previousRecord(confirmed || value, ownerId, callback, kind, error) ||
        unavailable(error)
      );
    }
    if (!isCurrent()) return inactive();
  }
  const stage = value.resultHint.stage;
  if (
    [
      "paid",
      "refunded",
      "authorization_closed",
      "cancellation_pending",
    ].includes(stage)
  )
    issue = null;
  let start,
    startError = false;
  if (stage === "paid") {
    try {
      start = (
        await ctx.api(
          `/api/courses/${encodeURIComponent(value.order.courseId)}/start`,
          { ownerId },
        )
      ).start;
      if (
        start?.course?.id !== value.order.courseId ||
        typeof start?.access?.canAccess !== "boolean"
      )
        throw new Error("Invalid current access response");
    } catch (error) {
      if (accountDenied(error)) return unavailable(error);
      start = null;
      startError = true;
    }
    if (!isCurrent()) return inactive();
  }
  return {
    type: "order",
    value,
    start,
    startError,
    issue,
    canRead: true,
    notice: kind === "fail" ? callbackNotice(callback.code) : null,
    canConfirm:
      kind === "success" &&
      maySubmitCallback(value) &&
      (!issue || issue.code === "unknown"),
  };
}

export async function paymentResultPage(kind, params, ctx) {
  let callback;
  try {
    callback = parsePaymentCallback(params, kind);
  } catch {
    return { html: paymentResultDocument({ type: "invalid", canRead: false }) };
  }
  if (kind === "success" && !ctx.user)
    return authGate(ctx, "결제한 계정으로 결과를 확인해주세요.");
  const route = globalThis.location?.hash,
    isCurrent = () =>
      route === undefined || globalThis.location?.hash === route;
  let screen;
  if (kind === "fail" && (!ctx.user || !callback.orderId))
    screen = {
      type: "guest",
      orderId: callback.orderId,
      notice: callbackNotice(callback.code),
      canRead: false,
    };
  else
    screen = await loadPaymentResult(kind, callback, ctx, {
      allowInitial: kind === "success",
      isCurrent,
    });
  return {
    html: paymentResultDocument(screen),
    bind() {
      bindPaymentResult(
        document.querySelector("#payment-return"),
        screen,
        kind,
        callback,
        ctx,
      );
    },
  };
}

export function bindPaymentResult(root, initial, kind, callback, ctx) {
  const route = location.hash,
    abort = new AbortController(),
    area = root.querySelector("[data-payment-result-body]"),
    feedback = root.querySelector("[data-payment-feedback]");
  let screen = initial,
    busy = false,
    disposed = false;
  const active = () => !disposed && root.isConnected && location.hash === route;
  function attach() {
    root
      .querySelectorAll("button")
      .forEach((button) => (button.disabled = busy));
    const refresh = root.querySelector("[data-payment-refresh]"),
      approve = root.querySelector("[data-payment-approve]");
    if (refresh) refresh.onclick = () => operate(false);
    if (approve) approve.onclick = () => operate(true);
  }
  async function operate(approve) {
    if (!active() || busy || !screen.canRead || (approve && !screen.canConfirm))
      return;
    const source = root.querySelector(
        approve ? "[data-payment-approve]" : "[data-payment-refresh]",
      ),
      hadFocus = source === document.activeElement;
    busy = true;
    attach();
    feedback.textContent = approve
      ? "같은 주문의 승인 여부를 확인합니다…"
      : "서버 기록을 다시 읽습니다…";
    const next = await loadPaymentResult(kind, callback, ctx, {
      allowInitial: approve,
      isCurrent: active,
      previousIssue: screen.issue,
      previousValue: screen.type === "order" ? screen.value : null,
    });
    if (!active()) return;
    const restoreFocus =
      hadFocus &&
      (document.activeElement === source ||
        document.activeElement === document.body);
    screen = next;
    area.innerHTML = paymentResultBody(screen);
    document.title = `${root.querySelector("h1")?.textContent || "결제 결과"} | TPC`;
    busy = false;
    attach();
    if (restoreFocus)
      (
        root.querySelector("[data-payment-refresh]") ||
        root.querySelector('a[href="#/purchases"]')
      )?.focus({ preventScroll: true });
    feedback.textContent =
      screen.type === "account"
        ? "개인 주문 정보를 지웠습니다. 현재 계정에서 내 주문을 다시 확인해주세요."
        : screen.stale
          ? "최신 기록을 읽지 못해 이전에 확인한 기록을 표시합니다."
          : screen.type !== "order"
            ? screen.issue?.message || "서버 기록을 확인하지 못했습니다."
            : screen.issue
              ? "현재 서버 기록을 읽었습니다. 승인 결과는 같은 주문에서 이어서 확인해주세요."
              : "현재 서버 기록을 확인했습니다. 새 주문·인증·취소를 자동으로 요청하지 않습니다.";
    feedback.classList.toggle(
      "is-error",
      Boolean(screen.stale || screen.issue || screen.type !== "order"),
    );
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
  attach();
  return { dispose };
}
