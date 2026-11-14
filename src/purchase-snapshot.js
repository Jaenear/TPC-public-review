import { esc, money } from "./ui.js";
import { date } from "./platform-ui.js";

const names = {
  terms: "이용 조건",
  privacy: "개인정보 안내",
  refund: "환불 안내",
  course_terms: "상품 제공 조건",
};

export function checkedPurchaseSnapshot(value, orderId) {
  const fail = () => {
    throw new Error(
      "주문 상세의 연결된 기록을 확인하지 못했습니다. 다시 확인해주세요.",
    );
  };
  if (
    !value ||
    value.schemaVersion !== 1 ||
    value.orderId !== orderId ||
    typeof value.recorded !== "boolean"
  )
    fail();
  if (!value.recorded) {
    if (
      value.classification !== "legacy_without_product_descriptor" ||
      value.product !== null
    )
      fail();
    return value;
  }
  const p = value.product,
    docs = value.policy?.documents;
  if (
    !p ||
    p.type !== "course" ||
    p.schemaVersion !== 1 ||
    p.quantity !== 1 ||
    p.fulfillment !== "course_access" ||
    p.billing !== "one_time" ||
    p.provider !== "toss" ||
    !["test", "live"].includes(p.mode) ||
    p.currency !== "KRW" ||
    !Number.isSafeInteger(p.amount) ||
    p.amount <= 0 ||
    typeof p.id !== "string" ||
    !p.id ||
    value.classification !== "typed_course" ||
    typeof p.title !== "string" ||
    !value.price ||
    !["pilot", "versioned"].includes(value.price.classification) ||
    (value.price.classification === "versioned" &&
      (!Number.isSafeInteger(value.price.edition) ||
        value.price.edition < 1 ||
        typeof value.price.versionId !== "string")) ||
    !Number.isFinite(Date.parse(value.policy?.sealedAt)) ||
    !Number.isFinite(Date.parse(value.policy?.paymentDeadline)) ||
    !Array.isArray(docs) ||
    docs.length !== 4 ||
    docs.some((d) => !d || typeof d !== "object") ||
    new Set(docs.map((d) => d.kind)).size !== 4 ||
    docs.some(
      (d) =>
        !Object.hasOwn(names, d.kind) ||
        typeof d.id !== "string" ||
        !Number.isFinite(Date.parse(d.acceptedAt)),
    )
  )
    fail();
  return value;
}

export function purchaseSnapshotHTML(snapshot) {
  if (!snapshot.recorded)
    return '<p class="eyebrow">PURCHASE RECORD</p><h2>이전 주문의 기록</h2><p class="p-muted">이 주문에는 통합 상품 기록이 없습니다. 기존 구매 조건·당시 가격 메뉴에서 기록 여부를 확인해주세요.</p>';
  const p = snapshot.product;
  return `<p class="eyebrow">YOUR PURCHASE DETAILS</p><h2>${esc(p.title)}</h2><p class="p-muted">주문 준비 시 기록한 상품과 적용 조건입니다. 현재 판매 가격과 별도로 보존합니다.</p><div class="p-notice ${p.mode === "test" ? "amber" : ""}">${p.mode === "test" ? "테스트 환경에서 준비한 주문입니다." : "결제사 승인 결과는 내 주문의 처리 상태에서 확인해주세요."}</div><dl class="ps-summary"><div><dt>구매 상품</dt><dd>강의 단건 구매 · ${esc(p.quantity)}개</dd></div><div class="ps-price"><dt>주문 금액</dt><dd>${money(p.amount)} · ${esc(p.currency)}</dd></div><div><dt>가격 기준</dt><dd>${snapshot.price.classification === "pilot" ? "파일럿 예시 가격" : `가격 판본 ${esc(snapshot.price.edition)}`}</dd></div><div><dt>주문 준비</dt><dd>${date(snapshot.policy.sealedAt)}</dd></div><div><dt>결제 준비 기한</dt><dd>${date(snapshot.policy.paymentDeadline)}</dd></div></dl><h3>확인한 제공 조건</h3><div class="p-form-stack">${snapshot.policy.documents.map((d) => `<div class="p-notice"><strong>${esc(names[d.kind] || d.kind)} · 판본 ${esc(d.edition)}</strong><p>확인 ${date(d.acceptedAt)}</p><a class="text-link" data-purchase-document href="#/policies/${esc(d.id)}">당시 문서 보기 ↗</a></div>`).join("")}</div><p class="p-muted">주문 번호 ${esc(snapshot.orderId)}</p>`;
}

export function bindPurchaseSnapshots(ctx, root = document) {
  const ownerId = ctx.user?.id;
  root.querySelectorAll("[data-purchase-snapshot]").forEach((button) => {
    button.onclick = async () => {
      if (!ownerId || !button.isConnected || button.disabled) return;
      button.disabled = true;
      try {
        const { purchaseSnapshot } = await ctx.api(
          `/api/checkout/orders/${encodeURIComponent(button.dataset.purchaseSnapshot)}/snapshot`,
          { ownerId },
        );
        if (!button.isConnected) return;
        ctx.modal(
          purchaseSnapshotHTML(
            checkedPurchaseSnapshot(
              purchaseSnapshot,
              button.dataset.purchaseSnapshot,
            ),
          ),
          (dialog) => {
            dialog
              .querySelectorAll("[data-purchase-document]")
              .forEach((link) => {
                link.onclick = () => dialog.close();
              });
          },
        );
      } catch (error) {
        if (button.isConnected) ctx.toast(error.message, "error");
      } finally {
        if (button.isConnected) button.disabled = false;
      }
    };
  });
}
