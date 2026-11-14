import { consentPayload } from "./policy-model.js";
import { checkedPurchaseSnapshot } from "./purchase-snapshot.js";

const prefix = "tpc.checkout.preparation.v1:";
const keyPattern = /^[A-Za-z0-9_-]{12,120}$/;
const digestPattern = /^[a-f0-9]{64}$/;
const positive = (value) => Number.isSafeInteger(value) && value > 0;
const invalid = () =>
  new Error(
    "원래 요청과 주문의 연결을 확인하지 못했습니다. 같은 요청의 결과를 다시 확인해주세요.",
  );

export function checkoutIntent(
  bundle,
  accepted,
  ownerId,
  key = crypto.randomUUID(),
) {
  if (!ownerId || typeof key !== "string" || !keyPattern.test(key))
    throw invalid();
  const body = Object.freeze({
    courseId: bundle.courseId,
    product: Object.freeze({ type: "course", id: bundle.courseId }),
    ...consentPayload(
      bundle,
      accepted,
      "checkout",
      bundle.mode,
      bundle.courseId,
    ),
    requestKey: key,
  });
  Object.freeze(body.acceptedPolicyIds);
  return Object.freeze({
    ownerId,
    courseId: bundle.courseId,
    body,
    paymentRequested: false,
  });
}

export function saveCheckoutRecovery(storage, intent) {
  try {
    storage.setItem(
      prefix + intent.ownerId + ":" + intent.courseId,
      JSON.stringify({ schemaVersion: 1, ...intent }),
    );
    return true;
  } catch {
    return false;
  }
}

export function clearCheckoutRecovery(storage, ownerId, courseId) {
  try {
    storage.removeItem(prefix + ownerId + ":" + courseId);
  } catch {
    /* unavailable storage */
  }
}

/** Recovery is a pending request, never a restored checkbox or payment instruction. */
export function readCheckoutRecovery(storage, ownerId, courseId) {
  try {
    for (let i = ownerId ? storage.length - 1 : -1; i >= 0; i--) {
      const key = storage.key(i);
      if (key?.startsWith(prefix) && !key.startsWith(prefix + ownerId + ":"))
        storage.removeItem(key);
    }
    const key = prefix + ownerId + ":" + courseId;
    const value = JSON.parse(storage.getItem(key) || "null");
    if (!value) return null;
    const body = value.body;
    if (
      value.schemaVersion !== 1 ||
      !ownerId ||
      value.ownerId !== ownerId ||
      value.courseId !== courseId ||
      typeof value.paymentRequested !== "boolean" ||
      !body ||
      body.courseId !== courseId ||
      body.product?.type !== "course" ||
      body.product.id !== courseId ||
      typeof body.requestKey !== "string" ||
      !keyPattern.test(body.requestKey) ||
      typeof body.policyDigest !== "string" ||
      !digestPattern.test(body.policyDigest) ||
      !Array.isArray(body.acceptedPolicyIds) ||
      body.acceptedPolicyIds.length !== 4 ||
      new Set(body.acceptedPolicyIds).size !== 4 ||
      body.acceptedPolicyIds.some(
        (p) => typeof p !== "string" || p.length > 100 || !p,
      )
    ) {
      storage.removeItem(key);
      return null;
    }
    return Object.freeze({
      ownerId,
      courseId,
      body: Object.freeze({
        courseId,
        product: Object.freeze({ type: "course", id: courseId }),
        requestKey: body.requestKey,
        policyDigest: body.policyDigest,
        acceptedPolicyIds: Object.freeze([...body.acceptedPolicyIds]),
      }),
      paymentRequested: value.paymentRequested,
    });
  } catch {
    return null;
  }
}

export function checkedPreparation(result, intent) {
  const o = result?.order,
    p = o?.product,
    r = result?.preparation;
  if (
    !o ||
    o.userId !== intent.ownerId ||
    o.courseId !== intent.courseId ||
    typeof o.id !== "string" ||
    !/^[a-z0-9_]{6,64}$/.test(o.id) ||
    !positive(o.amount) ||
    o.currency !== "KRW" ||
    !p ||
    p.schemaVersion !== 1 ||
    p.type !== "course" ||
    p.id !== o.courseId ||
    p.title !== o.title ||
    p.amount !== o.amount ||
    p.currency !== o.currency ||
    p.mode !== o.mode ||
    !["test", "live"].includes(p.mode) ||
    !positive(p.version) ||
    p.quantity !== 1 ||
    p.fulfillment !== "course_access" ||
    p.billing !== "one_time" ||
    p.provider !== "toss" ||
    o.policy?.fingerprint !== intent.body.policyDigest ||
    !r ||
    r.orderId !== o.id ||
    r.requestKey !== intent.body.requestKey ||
    r.courseId !== intent.courseId ||
    r.policyDigest !== intent.body.policyDigest ||
    !digestPattern.test(r.requestHash) ||
    !result.payment
  )
    throw invalid();
  return result;
}

export function checkedCheckoutPreview(result, intent, prepared) {
  const preview = result?.checkoutPreview;
  if (
    !preview ||
    typeof preview.canStart !== "boolean" ||
    !Array.isArray(preview.blockers) ||
    preview.canStart !== (preview.blockers.length === 0) ||
    !Number.isFinite(Date.parse(preview.asOf)) ||
    preview.blockers.some(
      (b) => typeof b.code !== "string" || typeof b.message !== "string",
    )
  )
    throw invalid();
  const snapshot = checkedPurchaseSnapshot(
    preview.purchaseSnapshot,
    prepared.order.id,
  );
  if (
    !snapshot.recorded ||
    snapshot.product.id !== intent.courseId ||
    snapshot.product.amount !== prepared.order.amount ||
    snapshot.product.mode !== prepared.order.mode ||
    snapshot.product.title !== prepared.order.title ||
    snapshot.product.version !== prepared.order.product.version ||
    snapshot.policy.fingerprint !== intent.body.policyDigest ||
    snapshot.policy.documents.length !== intent.body.acceptedPolicyIds.length ||
    new Set(snapshot.policy.documents.map((d) => d.id)).size !==
      intent.body.acceptedPolicyIds.length ||
    snapshot.policy.documents.some(
      (d) => !intent.body.acceptedPolicyIds.includes(d.id),
    )
  )
    throw invalid();
  if (
    preview.canStart &&
    (!preview.payment?.configured ||
      preview.payment.mode !== prepared.order.mode ||
      !preview.payment.clientKey ||
      !preview.policies?.ready ||
      preview.policies.fingerprint !== intent.body.policyDigest ||
      Date.parse(preview.asOf) >= Date.parse(snapshot.policy.paymentDeadline))
  )
    throw invalid();
  return preview;
}

export function requestedCheckout(intent, value = true) {
  return Object.freeze({ ...intent, paymentRequested: value });
}
