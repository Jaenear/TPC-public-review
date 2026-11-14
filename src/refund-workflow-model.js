import { checkedRefundContext } from "./refund-context-model.js";

export const refundChecks = ["terms", "inquiry", "learning", "access"];
const id = (v) => typeof v === "string" && /^[a-z0-9_]{1,120}$/.test(v);
const key = (v) => typeof v === "string" && /^[A-Za-z0-9_-]{12,120}$/.test(v);
const integer = (v) => Number.isSafeInteger(v) && v >= 0;
const date = (v) => typeof v === "string" && Number.isFinite(Date.parse(v));
const text = (v, max) => typeof v === "string" && v.length <= max;
const bool = (v) => typeof v === "boolean";
const kinds = [
  "inquiry_submitted",
  "review_saved",
  "reply_sent",
  "cancellation_started",
  "cancellation_unverified",
  "cancellation_verified",
  "legacy_recovery_started",
];
const fail = () => {
  throw Object.assign(
    new Error(
      "환불 처리 기록의 연결을 확인하지 못했습니다. 다시 조회해주세요.",
    ),
    { invalidRefundCase: true },
  );
};

export function checkedRefundCase(value, orderId, ownerId, operator = false) {
  if (
    !value ||
    value.schemaVersion !== 1 ||
    value.viewerId !== ownerId ||
    value.audience !== (operator ? "operator" : "member") ||
    !integer(value.version)
  )
    fail();
  checkedRefundContext(value.context, orderId, ownerId, operator);
  if (
    !Array.isArray(value.events) ||
    value.events.length > 30 ||
    !value.page ||
    value.page.limit !== 30 ||
    !(
      value.page.nextBefore === null ||
      (integer(value.page.nextBefore) && value.page.nextBefore > 0)
    ) ||
    !["recorded", "legacy_state_only", "no_inquiry"].includes(
      value.historyCoverage,
    )
  )
    fail();
  if (
    value.events.some(
      (e, index) =>
        !e ||
        !id(e.id) ||
        !integer(e.version) ||
        e.version < 1 ||
        e.version > value.version ||
        (index > 0 && value.events[index - 1].version <= e.version) ||
        !kinds.includes(e.kind) ||
        !text(e.body, 4000) ||
        !date(e.createdAt) ||
        (!operator &&
          (e.kind === "review_saved" ||
            ["actorId", "reviewId", "operationId"].some((k) => k in e))),
    )
  )
    fail();
  if (
    value.page.nextBefore !== null &&
    value.page.nextBefore !== value.events.at(-1)?.version
  )
    fail();
  const o = value.operation,
    a = value.actions;
  if (
    !a ||
    (o !== null &&
      (!o ||
        !id(o.id) ||
        !["reviewed", "legacy_recovery"].includes(o.origin) ||
        !["checking", "unverified", "verified"].includes(o.status) ||
        !date(o.createdAt) ||
        !date(o.updatedAt) ||
        (o.status === "verified" ? o.recheckAt !== null : !date(o.recheckAt))))
  )
    fail();
  if (
    o &&
    (o.status === "verified"
      ? value.context.order.status !== "refunded"
      : value.context.order.status !== "refunding")
  )
    fail();
  if (!operator) {
    if (
      !bool(a.canInquire) ||
      a.canInquire !==
        (value.context.order.status === "paid" &&
          value.context.inquiry === null) ||
      ["currentReview", "gateway", "legacyRecovery"].some((k) => k in value)
    )
      fail();
  } else {
    const r = value.currentReview,
      g = value.gateway;
    if (
      !g ||
      !bool(g.configured) ||
      !["test", "live", "unconfigured"].includes(g.mode) ||
      !bool(g.matchesOrder) ||
      g.matchesOrder !==
        (g.configured && g.mode === value.context.order.mode) ||
      !bool(value.legacyRecovery) ||
      ![a.canReview, a.canExecute, a.canRecheck].every(bool) ||
      !integer(a.retryAfterSeconds) ||
      a.retryAfterSeconds > 30
    )
      fail();
    if (
      r !== null &&
      (!r ||
        !id(r.id) ||
        !id(r.reviewerId) ||
        !["full_cancel", "reply"].includes(r.decision) ||
        !text(r.basis, 4000) ||
        r.basis.length < 10 ||
        !text(r.operatorNote, 4000) ||
        !text(r.publicMessage, 2000) ||
        r.publicMessage.length < 5 ||
        !text(r.cancelReason, 200) ||
        (r.decision === "reply"
          ? r.cancelReason !== ""
          : r.cancelReason.length < 5) ||
        !Array.isArray(r.checks) ||
        r.checks.length !== 4 ||
        new Set(r.checks).size !== 4 ||
        r.checks.some((c) => !refundChecks.includes(c)) ||
        !/^[a-f0-9]{64}$/.test(r.contextHash) ||
        !integer(r.orderVersion) ||
        !date(r.createdAt) ||
        !bool(r.validForCurrentFacts))
    )
      fail();
    if (
      a.canReview !== (value.context.order.status === "paid" && o === null) ||
      a.canExecute !==
        Boolean(
          r?.validForCurrentFacts && (r.decision === "reply" || g.matchesOrder),
        ) ||
      (a.canRecheck &&
        (value.context.order.status !== "refunding" ||
          !g.matchesOrder ||
          a.retryAfterSeconds ||
          (!o && !value.legacyRecovery)))
    )
      fail();
  }
  return value;
}

export function checkedRefundReceipt(receipt, value, intent) {
  if (
    !receipt ||
    receipt.schemaVersion !== 1 ||
    !id(receipt.id) ||
    receipt.orderId !== value.context.order.id ||
    receipt.requestKey !== intent.requestKey ||
    receipt.kind !== intent.kind ||
    !["inquiry", "review", "execute", "recheck"].includes(receipt.kind) ||
    !integer(receipt.version) ||
    receipt.version > value.version ||
    !date(receipt.createdAt) ||
    !(receipt.reviewId === null || id(receipt.reviewId)) ||
    !(receipt.operationId === null || id(receipt.operationId))
  )
    fail();
  return receipt;
}

const prefix = "tpc.refund-command.v1:";
const storageKey = (ownerId, orderId, operator) =>
  prefix + ownerId + ":" + orderId + (operator ? ":desk" : ":member");
export function readRefundRecovery(storage, ownerId, orderId, operator) {
  try {
    // A new account may never recover the previous account's command key.
    for (let n = storage.length - 1; n >= 0; n--) {
      const k = storage.key(n);
      if (k?.startsWith(prefix) && !k.startsWith(prefix + ownerId + ":"))
        storage.removeItem(k);
    }
    const v = JSON.parse(
      storage.getItem(storageKey(ownerId, orderId, operator)) || "null",
    );
    return v &&
      v.ownerId === ownerId &&
      v.orderId === orderId &&
      v.operator === operator &&
      key(v.requestKey) &&
      ["inquiry", "review", "execute", "recheck"].includes(v.kind) &&
      (operator || v.kind === "inquiry")
      ? v
      : null;
  } catch {
    return null;
  }
}
export function rememberRefundRecovery(storage, value) {
  try {
    // Only the lookup key is persisted. Private notes and payment keys are not.
    const { ownerId, orderId, operator, requestKey, kind } = value;
    storage.setItem(
      storageKey(ownerId, orderId, operator),
      JSON.stringify({ ownerId, orderId, operator, requestKey, kind }),
    );
    return true;
  } catch {
    return false;
  }
}
export function clearRefundRecovery(storage, ownerId, orderId, operator) {
  try {
    storage.removeItem(storageKey(ownerId, orderId, operator));
  } catch {}
}
