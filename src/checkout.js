import { checkedPolicies } from "./policies.js";
import { consentPayload } from "./policy-model.js";
import { money } from "./ui.js";
import {
  checkoutIntent,
  checkedPreparation,
  checkedCheckoutPreview,
  saveCheckoutRecovery,
  readCheckoutRecovery,
  clearCheckoutRecovery,
  requestedCheckout,
} from "./checkout-model.js";
import { mountCheckoutSDK } from "./checkout-sdk.js";
import { checkedOrderState } from "./payment-order-model.js";
import {
  checkoutHTML,
  checkoutChoices,
  checkoutQuote,
  checkoutRecord,
} from "./checkout-view.js";

export function checkoutPage(course, payment, policy, ctx) {
  return {
    html: checkoutHTML(course, payment, policy),
    bind() {
      bindCheckout(
        document.querySelector("#course-checkout"),
        { course, payment, policy },
        ctx,
      );
    },
  };
}

export function bindCheckout(
  root,
  initial,
  ctx,
  { storage, mount = mountCheckoutSDK } = {},
) {
  if (storage === undefined) {
    try {
      storage = window.sessionStorage;
    } catch {
      storage = null;
    }
  }
  const ownerId = ctx.user?.id || null,
    courseId = initial.course.id,
    route = location.hash;
  const abort = new AbortController(),
    options = { signal: abort.signal };
  const node = (selector) => root.querySelector(selector);
  const form = node("[data-checkout-choices]"),
    fieldset = node("[data-checkout-fieldset]"),
    policyArea = node("[data-checkout-policy]"),
    record = node("[data-checkout-record]"),
    feedback = node("[data-checkout-feedback]");
  const buttons = Object.fromEntries(
    [
      "prepare",
      "recover",
      "retry-request",
      "load-methods",
      "pay",
      "refresh-conditions",
    ].map((key) => [key, node(`[data-${key}]`)]),
  );
  let payment = initial.payment,
    policy = initial.policy,
    intent = readCheckoutRecovery(storage, ownerId, courseId),
    prepared = null,
    preview = null,
    controls = null,
    phase = intent ? "unconfirmed" : "review",
    busy = false,
    disposed = false;
  const active = () => !disposed && root.isConnected && location.hash === route;
  const remember = (value) => {
    if (!saveCheckoutRecovery(storage, value))
      throw new Error(
        "주문 복구 정보를 이 브라우저에 저장할 수 없습니다. 저장 공간을 확인한 후 다시 시도해주세요.",
      );
    intent = value;
  };
  function show(message, error = false) {
    if (!active()) return;
    feedback.textContent = message;
    feedback.classList.toggle("is-error", error);
  }
  function sync() {
    if (!active()) return;
    const choices = checkedPolicies(form),
      total = policy.requiredPolicyIds?.length || 0;
    node("[data-checkout-count]").textContent =
      policy.ready && total
        ? `필수 문서 ${choices.length} / ${total} 확인`
        : "구매 조건 준비 중";
    fieldset.disabled = busy || phase !== "review";
    form.hidden = Boolean(prepared);
    record.hidden = !prepared;
    const visibility = {
      prepare: phase === "review",
      recover: phase === "unconfirmed",
      "retry-request": phase === "unconfirmed" && !intent?.paymentRequested,
      "load-methods": phase === "prepared",
      pay: phase === "ready",
      "refresh-conditions": true,
    };
    for (const [key, button] of Object.entries(buttons)) {
      button.hidden = !visibility[key];
      button.disabled = busy || phase === "denied";
    }
    buttons.prepare.disabled ||=
      !policy.ready ||
      (!payment.configured && policy.course?.amount !== 0) ||
      total !== 4 ||
      choices.length !== total;
    buttons.prepare.textContent =
      policy.course?.amount === 0 ? "무료 수강 등록" : "주문 준비하기 →";
    buttons["refresh-conditions"].disabled ||=
      phase === "unconfirmed" || Boolean(intent?.paymentRequested);
    const step = ["ready", "requesting", "payment_unknown"].includes(phase)
      ? 2
      : phase === "review"
        ? 0
        : 1;
    root.querySelectorAll("[data-checkout-step]").forEach((el, i) => {
      el.classList.toggle("is-current", i === step);
      el.classList.toggle("is-complete", i < step);
      if (i === step) el.setAttribute("aria-current", "step");
      else el.removeAttribute("aria-current");
    });
    node("[data-checkout-sdk]").hidden = !["ready", "requesting"].includes(
      phase,
    );
    node("[data-view-orders]").hidden = ![
      "unconfirmed",
      "payment_unknown",
      "changed",
    ].includes(phase);
    node("[data-view-orders]").setAttribute(
      "href",
      prepared ? `#/payment/order/${prepared.order.id}` : "#/purchases",
    );
  }
  async function destroyControls() {
    const previous = controls;
    controls = null;
    if (previous) await previous.destroy();
    node("[data-toss-methods]").replaceChildren();
    node("[data-toss-agreements]").replaceChildren();
  }
  async function deny(error) {
    phase = "denied";
    prepared = null;
    intent = null;
    preview = null;
    record.replaceChildren();
    form
      .querySelectorAll("[data-policy-choice]")
      .forEach((c) => (c.checked = false));
    await destroyControls();
    show(
      error.message + " 현재 계정으로 로그인한 뒤 조건을 다시 확인해주세요.",
      true,
    );
    sync();
  }
  async function errorState(error, kind) {
    if (error.sessionChanged || [401, 403].includes(error.status))
      return deny(error);
    if (!active()) return;
    await destroyControls();
    if (
      kind === "prepare" &&
      [400, 409].includes(error.status) &&
      !intent?.paymentRequested
    ) {
      clearCheckoutRecovery(storage, ownerId, courseId);
      intent = null;
      prepared = null;
      phase = "changed";
      form
        .querySelectorAll("[data-policy-choice]")
        .forEach((c) => (c.checked = false));
      show(
        error.message + " 최신 가격·제공 조건을 확인하고 다시 체크해주세요.",
        true,
      );
    } else {
      phase = intent?.paymentRequested
        ? "payment_unknown"
        : prepared
          ? "prepared"
          : intent
            ? "unconfirmed"
            : "review";
      show(
        error.status === 404 && kind === "recover"
          ? "아직 확인된 준비 기록이 없습니다. 같은 요청으로 다시 확인해주세요. 새 주문은 자동으로 만들지 않습니다."
          : error.message,
        true,
      );
    }
  }
  async function preflight() {
    const result = await ctx.api(
      `/api/checkout/orders/${encodeURIComponent(prepared.order.id)}/preview`,
      { ownerId },
    );
    if (!active()) return false;
    preview = checkedCheckoutPreview(result, intent, prepared);
    record.innerHTML = checkoutRecord(prepared, preview);
    if (!preview.canStart) {
      phase = "changed";
      await destroyControls();
      if (preview.policies)
        node("[data-checkout-quote]").innerHTML = checkoutQuote(
          initial.course,
          preview.payment,
          preview.policies,
        );
      show(preview.blockers.map((b) => b.message).join(" "), true);
      return false;
    }
    if (intent.paymentRequested) {
      phase = "payment_unknown";
      await destroyControls();
      show(
        "이 주문의 인증 요청 결과를 확인해야 합니다. 결제를 다시 요청하지 말고 내 주문에서 처리 결과를 확인해주세요.",
        true,
      );
      return false;
    }
    return true;
  }
  async function accept(result) {
    if (!active()) return;
    prepared = checkedPreparation(result, intent);
    if (prepared.order.status === "abandoned") {
      // A server-confirmed terminal order can release this tab's conservative
      // unknown flag. Merely visiting a failure URL or clearing storage cannot.
      clearCheckoutRecovery(storage, ownerId, courseId);
      intent = requestedCheckout(intent, false);
    }
    record.innerHTML = checkoutRecord(prepared);
    if (await preflight()) {
      phase = "prepared";
      show("주문이 준비됐습니다. 이 주문의 결제수단을 불러와주세요.");
    }
  }
  async function operation(fn, kind) {
    if (!active() || busy || phase === "denied") return;
    busy = true;
    sync();
    try {
      await fn();
    } catch (error) {
      await errorState(error, kind);
    } finally {
      busy = false;
      sync();
    }
  }
  const prepare = () =>
    operation(async () => {
      if (!ownerId) {
        ctx.requireAuth();
        return;
      }
      if (policy.course?.amount === 0) {
        await ctx.api("/api/enrollments", {
          method: "POST",
          ownerId,
          body: {
            courseId,
            ...consentPayload(
              policy,
              checkedPolicies(form),
              "checkout",
              payment.mode,
              courseId,
            ),
          },
        });
        if (active()) ctx.navigate(`/start/${courseId}`);
        return;
      }
      if (!intent)
        remember(checkoutIntent(policy, checkedPolicies(form), ownerId));
      phase = "unconfirmed";
      sync();
      show("내 계정의 주문을 준비하고 있습니다…");
      await accept(
        await ctx.api("/api/checkout/orders", {
          method: "POST",
          ownerId,
          body: intent.body,
        }),
      );
    }, "prepare");
  buttons.prepare.addEventListener("click", prepare, options);
  buttons["retry-request"].addEventListener("click", prepare, options);
  buttons.recover.addEventListener(
    "click",
    () =>
      operation(async () => {
        show("원래 주문 준비 결과를 확인합니다…");
        await accept(
          await ctx.api(
            `/api/checkout/preparations/${encodeURIComponent(intent.body.requestKey)}`,
            { ownerId },
          ),
        );
      }, "recover"),
    options,
  );
  buttons["load-methods"].addEventListener(
    "click",
    () =>
      operation(async () => {
        if (!prepared || !(await preflight())) return;
        await destroyControls();
        show("이 주문의 결제수단을 불러옵니다…");
        controls = await mount({
          root,
          payment: preview.payment,
          order: prepared.order,
          ownerId,
          active,
        });
        if (!active()) {
          await destroyControls();
          return;
        }
        phase = "ready";
        buttons.pay.textContent = `${money(prepared.order.amount)}${prepared.order.mode === "test" ? " 테스트" : ""} 결제 요청`;
        show("선택한 결제수단과 금액을 확인한 뒤 직접 결제를 요청해주세요.");
      }, "sdk"),
    options,
  );
  buttons.pay.addEventListener(
    "click",
    () =>
      operation(async () => {
        if (!controls || !(await preflight())) return;
        if (preview.payment.clientKey !== controls.clientKey) {
          await destroyControls();
          phase = "prepared";
          show(
            "결제 연결 정보가 바뀌었습니다. 결제수단을 다시 불러와주세요.",
            true,
          );
          return;
        }
        remember(requestedCheckout(intent));
        phase = "requesting";
        sync();
        const started = checkedOrderState(
          await ctx.api(
            `/api/checkout/orders/${prepared.order.id}/authorization`,
            {
              method: "POST",
              ownerId,
              body: {
                version: prepared.order.version,
                preparationKey: intent.body.requestKey,
              },
            },
          ),
          ownerId,
          prepared.order.id,
        );
        if (!active()) return;
        if (
          started.started !== true ||
          !started.checkoutState.authorizationOpen
        ) {
          phase = "payment_unknown";
          await destroyControls();
          show(
            "다른 창이나 기기에서 이미 진행한 주문입니다. 같은 주문의 서버 상태를 확인해주세요.",
            true,
          );
          return;
        }
        prepared = { ...prepared, order: started.order };
        show(
          "결제사 인증을 요청했습니다. 결과를 확인할 때까지 새 주문을 시작하지 마세요.",
        );
        try {
          await controls.request();
          if (active()) {
            phase = "payment_unknown";
            await destroyControls();
            show(
              "인증 결과를 내 주문에서 확인해주세요. 승인 전에는 수강권이 발급되지 않습니다.",
              true,
            );
          }
        } catch (error) {
          if (["USER_CANCEL", "PAY_PROCESS_CANCELED"].includes(error.code)) {
            try {
              const closed = checkedOrderState(
                await ctx.api(
                  `/api/checkout/orders/${prepared.order.id}/close-authorization`,
                  {
                    method: "POST",
                    ownerId,
                    body: { version: prepared.order.version },
                  },
                ),
                ownerId,
                prepared.order.id,
              );
              if (!active()) return;
              if (closed.closed !== true || closed.order.status !== "abandoned")
                throw new Error("승인 요청 종료 결과를 다시 확인해주세요.");
              prepared = { ...prepared, order: closed.order };
              clearCheckoutRecovery(storage, ownerId, courseId);
              intent = null;
              phase = "changed";
              await destroyControls();
              show(
                "인증을 취소해 이 주문의 승인 요청을 종료했습니다. 다시 진행하려면 최신 조건을 확인하고 새로 체크해주세요.",
              );
            } catch (closeError) {
              if (
                closeError.sessionChanged ||
                [401, 403].includes(closeError.status)
              )
                return deny(closeError);
              phase = "payment_unknown";
              await destroyControls();
              show(
                "인증 화면은 닫혔지만 서버의 종료 결과를 확인하지 못했습니다. 같은 주문의 처리 상태를 확인해주세요.",
                true,
              );
            }
          } else {
            phase = "payment_unknown";
            await destroyControls();
            show(
              "결제사 인증 결과를 확인하지 못했습니다. 내 주문에서 같은 주문의 처리 결과를 확인해주세요.",
              true,
            );
          }
        }
      }, "payment"),
    options,
  );
  buttons["refresh-conditions"].addEventListener(
    "click",
    () =>
      operation(async () => {
        if (intent?.paymentRequested || phase === "unconfirmed") return;
        await destroyControls();
        const next = (await ctx.api("/api/checkout/config", { ownerId }))
          .payment;
        const current = ["test", "live"].includes(next.mode)
          ? (
              await ctx.api(
                `/api/policies/current?context=checkout&mode=${next.mode}&courseId=${encodeURIComponent(courseId)}`,
                { ownerId },
              )
            ).policies
          : { ready: false, documents: [], requiredPolicyIds: [] };
        if (!active()) return;
        clearCheckoutRecovery(storage, ownerId, courseId);
        intent = null;
        prepared = null;
        preview = null;
        payment = next;
        policy = current;
        phase = "review";
        record.replaceChildren();
        policyArea.innerHTML = checkoutChoices(policy);
        node("[data-checkout-quote]").innerHTML = checkoutQuote(
          initial.course,
          payment,
          policy,
        );
        show(
          "최신 가격과 문서를 불러왔습니다. 원문을 확인하고 각 항목을 다시 체크해주세요.",
        );
      }, "conditions"),
    options,
  );
  form.addEventListener("submit", (e) => e.preventDefault(), options);
  form.addEventListener("change", sync, options);
  node("[data-pilot-inquiry]")?.addEventListener(
    "click",
    () =>
      operation(async () => {
        if (!ownerId) {
          ctx.requireAuth();
          return;
        }
        await ctx.api("/api/orders", {
          method: "POST",
          ownerId,
          body: { courseId },
        });
        if (active()) ctx.navigate("/my?tab=orders");
      }, "inquiry"),
    options,
  );
  function dispose() {
    if (disposed) return;
    disposed = true;
    abort.abort();
    observer.disconnect();
    void destroyControls();
  }
  const observer = new MutationObserver(() => {
    if (!root.isConnected) dispose();
  });
  observer.observe(document.body, { childList: true, subtree: true });
  window.addEventListener(
    "hashchange",
    () => {
      if (location.hash !== route) dispose();
    },
    options,
  );
  window.addEventListener("pagehide", dispose, options);
  if (intent)
    show(
      intent.paymentRequested
        ? "이 계정에서 시작한 인증 결과를 먼저 확인해주세요. 결제를 자동으로 다시 요청하지 않습니다."
        : "이 계정의 주문 준비 요청이 남아 있습니다. 원래 결과를 직접 확인해주세요.",
    );
  sync();
  return { dispose };
}
