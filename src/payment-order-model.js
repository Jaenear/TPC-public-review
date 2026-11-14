const orderPattern = /^[a-z0-9_]{6,64}$/;
const validOrderId = (id) => typeof id === "string" && orderPattern.test(id);
const invalid = () =>
  new Error(
    "현재 계정의 주문 상태를 확인하지 못했습니다. 같은 주문을 다시 조회해주세요.",
  );

export function purchaseStatusLabel(status, fallback = (value) => value) {
  return status === "abandoned" ? "승인 요청 종료" : fallback(status);
}

export function checkedOrderState(result, ownerId, orderId) {
  const order = result?.order,
    state = result?.checkoutState;
  if (
    !order ||
    !state ||
    !ownerId ||
    order.userId !== ownerId ||
    !validOrderId(orderId) ||
    order.id !== orderId ||
    state.orderId !== orderId ||
    state.courseId !== order.courseId ||
    typeof order.courseId !== "string" ||
    !order.courseId ||
    typeof order.title !== "string" ||
    !Number.isSafeInteger(order.amount) ||
    order.amount <= 0 ||
    order.currency !== "KRW" ||
    !["test", "live"].includes(order.mode) ||
    !Number.isSafeInteger(order.version) ||
    order.version < 1 ||
    state.version !== order.version ||
    state.status !== order.status ||
    ![
      "created",
      "pending",
      "confirming",
      "paid",
      "refunding",
      "refunded",
      "abandoned",
    ].includes(state.status) ||
    ["authorizationOpen", "canCloseAuthorization", "canReconcile"].some(
      (key) => typeof state[key] !== "boolean",
    ) ||
    (state.authorizationOpen &&
      (state.status !== "pending" || !state.canCloseAuthorization)) ||
    (state.canCloseAuthorization &&
      !["created", "pending"].includes(state.status)) ||
    state.canReconcile !== ["pending", "confirming"].includes(state.status)
  )
    throw invalid();
  return result;
}

export function orderStateMessage(state) {
  if (state.authorizationOpen)
    return "이 계정에서 결제사 인증을 시작한 주문입니다. 다른 창이나 기기의 결과도 이 주문으로 확인합니다. 인증 시작은 결제 완료가 아닙니다.";
  return (
    {
      created:
        "상품과 조건을 확인한 주문입니다. 아직 결제사 인증을 시작하지 않았습니다.",
      pending:
        "서버가 결제 식별자를 보관하고 있습니다. 새 결제를 시작하지 말고 같은 주문의 승인 결과를 확인해주세요.",
      confirming:
        "서버에서 승인 결과를 확인 중입니다. 잠시 후 같은 주문을 다시 확인해주세요.",
      paid: "서버가 결제사 승인 결과를 검증한 주문입니다. 현재 수강권은 수강 시작 화면에서 확인할 수 있습니다.",
      refunding:
        "전액 취소 결과를 확인 중입니다. 취소 문의와 현재 수강권은 내 주문에서 확인해주세요.",
      refunded:
        "서버가 전액 취소를 확인한 주문입니다. 기존 주문과 학습 기록은 보존됩니다.",
      abandoned:
        "이 주문의 승인 요청을 종료했습니다. 이 주문으로 뒤늦게 돌아온 인증 결과는 승인하지 않습니다. 결제사의 결제 취소나 환불을 실행한 기록은 아닙니다.",
    }[state.status] || "현재 주문을 다시 확인해주세요."
  );
}

/** Callback text is untrusted; duplicate/ambiguous values never start approval. */
export function parsePaymentCallback(params, kind = "success") {
  const one = (key, required = false) => {
    const values = params.getAll(key);
    if (values.length > 1 || (required && values.length !== 1)) throw invalid();
    return values[0] ?? null;
  };
  const orderId = one("orderId", kind === "success"),
    expected = one("tpcOrderId");
  if (
    (orderId && !validOrderId(orderId)) ||
    (expected && !validOrderId(expected)) ||
    (expected && orderId && expected !== orderId)
  )
    throw invalid();
  if (kind === "fail")
    return {
      orderId: orderId || expected,
      code: (one("code") || "").slice(0, 80),
      message: (
        one("message") || "인증 화면이 종료되었거나 결과를 확인하지 못했습니다."
      ).slice(0, 300),
    };
  const paymentKey = one("paymentKey", true),
    rawAmount = one("amount", true);
  if (
    paymentKey.length < 6 ||
    paymentKey.length > 200 ||
    !/^[1-9]\d{0,14}$/.test(rawAmount) ||
    !Number.isSafeInteger(Number(rawAmount))
  )
    throw invalid();
  return Object.freeze({ orderId, paymentKey, amount: Number(rawAmount) });
}

export function checkedConfirmedOrder(result, ownerId, body) {
  const order = result?.order;
  if (
    !order ||
    order.userId !== ownerId ||
    order.id !== body.orderId ||
    order.amount !== body.amount ||
    order.currency !== "KRW" ||
    order.status !== "paid" ||
    typeof order.courseId !== "string" ||
    typeof order.title !== "string"
  )
    throw invalid();
  return order;
}
