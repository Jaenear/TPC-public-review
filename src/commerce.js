import { startPage } from "./learning-start.js";
import { isFreeCourse } from "./price-model.js";
import { checkoutPage } from "./checkout.js";
import { readCheckoutRecovery } from "./checkout-model.js";
import { purchaseCheckoutNotice } from "./checkout-view.js";
import { paymentOrderPage } from "./payment-order.js";
import { paymentResultPage } from "./payment-result.js";
import { bindPriceSnapshots } from "./pricing.js";
import { bindPurchaseSnapshots } from "./purchase-snapshot.js";
import { esc, money } from "./ui.js";
import { bindOrderConditions } from "./policies.js";
import { bindRefundContexts } from "./refund-context.js";
import { refundWorkflowPage } from "./refund-workflow.js";
import {
  head,
  empty,
  authGate,
  bindForm,
  date,
  tag,
  statusLabel,
} from "./platform-ui.js";
export async function renderCommerce(path, params, ctx) {
  if (path.startsWith("/refund/"))
    return refundWorkflowPage(path.slice(8), params.get("desk") === "1", ctx);
  if (path.startsWith("/checkout/")) return checkout(path.slice(10), ctx);
  if (path === "/purchases") return purchases(ctx);
  if (path.startsWith("/payment/order/"))
    return paymentOrderPage(path.slice(15), ctx);
  if (path === "/payment/success")
    return paymentResultPage("success", params, ctx);
  if (path === "/payment/fail") return paymentResultPage("fail", params, ctx);
  return null;
}
async function checkout(id, ctx) {
  const ownerId = ctx.user?.id || null;
  const fresh = await ctx.api("/api/catalog", { ownerId });
  const course = fresh.courses.find((c) => c.id === id);
  if (!course)
    return {
      html: `<section class="container p-page">${empty("현재 구매 가능한 강의를 찾을 수 없습니다.", "라이브러리에서 강의를 다시 선택해주세요.", '<a class="btn btn-primary" href="#/library">강의 라이브러리</a>')}</section>`,
    };
  const { start } = await ctx.api(
    `/api/courses/${encodeURIComponent(id)}/start`,
    { ownerId },
  );
  if (
    start.access.canAccess ||
    [
      "revoked",
      "expired",
      "scheduled",
      "order_pending",
      "inquiry_pending",
      "content_wait",
    ].includes(start.state) ||
    (course.pilot && isFreeCourse(course))
  )
    return startPage(start, ctx);
  const payment = (await ctx.api("/api/checkout/config", { ownerId })).payment;
  const policy = ["test", "live"].includes(payment.mode)
    ? (
        await ctx.api(
          `/api/policies/current?context=checkout&mode=${payment.mode}&courseId=${encodeURIComponent(id)}`,
          { ownerId },
        )
      ).policies
    : { ready: false, documents: [], requiredPolicyIds: [] };
  return checkoutPage(course, payment, policy, ctx);
}

async function purchases(ctx) {
  if (!ctx.user) return authGate(ctx, "내 주문과 수강권을 확인하세요.");
  const ownerId = ctx.user.id;
  const orders = (await ctx.api("/api/checkout/orders", { ownerId })).orders;
  const recoveries = new Map(
    [...new Set(orders.map((order) => order.courseId))].map((courseId) => [
      courseId,
      readCheckoutRecovery(safeCheckoutStorage(), ownerId, courseId),
    ]),
  );
  return {
    html: `<section class="container p-page p-thread">${head("YOUR PURCHASES", "결제에서 배움까지, 한 기록으로.", "결제사 승인 상태와 취소 문의를 확인합니다.")}<div class="p-purchase-list">${orders.map((order) => `<article class="p-panel"><div class="p-inline-heading"><h2>${esc(order.title)}</h2>${tag(statusLabel(order.status), order.status === "paid" ? "mint" : "")}</div><p class="p-muted">주문 ${esc(order.id)} · ${order.mode === "test" ? "테스트 결제 · " : ""}${date(order.createdAt)}</p><div class="p-purchase-total">${money(order.amount)}</div>${purchaseCheckoutNotice(order, recoveries.get(order.courseId))}${order.refundRequest ? `<div class="p-notice"><strong>취소 문의 · ${{ requested: "확인 대기", approved: "전액 취소 완료", rejected: "답변 등록" }[order.refundRequest.status] || ""}</strong><p>${esc(order.refundRequest.reason)}</p>${order.refundRequest.resolution ? `<p>운영 답변: ${esc(order.refundRequest.resolution)}</p>` : ""}</div>` : ""}<div class="p-form-actions"><a class="btn btn-outline btn-sm" href="#/payment/order/${esc(order.id)}">처리 상태·복구</a><button class="btn btn-outline btn-sm" data-purchase-snapshot="${esc(order.id)}">주문 상세</button><button class="btn btn-outline btn-sm" data-order-conditions="${esc(order.id)}">구매 당시 조건</button><button class="btn btn-outline btn-sm" data-order-price="${esc(order.id)}">당시 가격</button><button class="btn btn-outline btn-sm" data-refund-context="${esc(order.id)}">환불 검토 근거</button><a class="btn btn-outline btn-sm" href="#/refund/${esc(order.id)}">문의·처리 이력</a>${order.status === "paid" ? `<a href="#/start/${esc(order.courseId)}" class="btn btn-primary btn-sm">수강 시작·학습 확인</a>${!order.refundRequest ? `<a class="btn btn-outline btn-sm" href="#/refund/${esc(order.id)}">전액 취소 문의</a>` : ""}` : order.status === "created" ? `<a href="#/payment/order/${esc(order.id)}" class="btn btn-outline btn-sm">${recoveries.get(order.courseId)?.paymentRequested ? "인증 요청 결과 먼저 확인" : recoveries.get(order.courseId) ? "주문 준비 결과 확인" : "결제 이어가기"}</a>` : ["pending", "confirming"].includes(order.status) ? `<button class="btn btn-outline btn-sm" data-reconcile-order="${esc(order.id)}">같은 주문 승인 결과 재확인</button>` : ""}</div></article>`).join("") || empty("아직 결제 주문이 없습니다.", "파일럿 수강 문의는 마이 캠프의 수강 신청 탭에서 확인할 수 있습니다.", `<a class="btn btn-outline" href="#/my?tab=orders">파일럿 신청 내역</a>`)}</div></section>`,
    bind() {
      bindOrderConditions(ctx);
      bindPriceSnapshots(ctx);
      bindRefundContexts(ctx);
      bindPurchaseSnapshots(ctx);
      document.querySelectorAll("[data-reconcile-order]").forEach(
        (button) =>
          (button.onclick = async () => {
            button.disabled = true;
            try {
              const result = await ctx.api(
                `/api/checkout/orders/${button.dataset.reconcileOrder}/reconcile`,
                { method: "POST", ownerId, body: {} },
              );
              if (result.order?.status !== "paid") {
                ctx.toast(
                  "현재 결제사 상태를 확인했습니다. 같은 주문에서 처리 결과를 확인해주세요.",
                );
                ctx.navigate(`/payment/order/${button.dataset.reconcileOrder}`);
                return;
              }
              await ctx.refreshCatalog();
              ctx.toast("주문 승인 결과와 수강권을 확인했습니다.");
              ctx.reload();
            } catch (error) {
              ctx.toast(error.message, "error");
              button.disabled = false;
            }
          }),
      );
    },
  };
}

function safeCheckoutStorage() {
  try {
    return window.sessionStorage;
  } catch {
    return null;
  }
}
