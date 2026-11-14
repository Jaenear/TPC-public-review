import { checkedOrderState } from "./payment-order-model.js";

const stages = {
  created: "prepared",
  confirming: "approval_processing",
  paid: "paid",
  refunding: "cancellation_pending",
  refunded: "refunded",
  abandoned: "authorization_closed",
};

export function checkedPaymentResult(value, ownerId, orderId) {
  try {
    checkedOrderState(value, ownerId, orderId);
  } catch (error) {
    error.invalidPaymentResult = true;
    throw error;
  }
  const hint = value.resultHint,
    state = value.checkoutState,
    expected =
      state.status === "pending"
        ? state.authorizationOpen
          ? "authentication_open"
          : "approval_pending"
        : stages[state.status];
  if (
    !hint ||
    hint.schemaVersion !== 1 ||
    hint.orderId !== orderId ||
    hint.orderVersion !== state.version ||
    hint.stage !== expected
  )
    throw Object.assign(
      new Error(
        "주문 결과의 기준이 일치하지 않습니다. 같은 주문을 다시 읽어주세요.",
      ),
      { invalidPaymentResult: true },
    );
  return value;
}

export function confirmedPaymentResult(order, ownerId) {
  return checkedPaymentResult(
    {
      order,
      checkoutState: {
        orderId: order.id,
        courseId: order.courseId,
        version: order.version,
        status: "paid",
        authorizationOpen: false,
        canCloseAuthorization: false,
        canReconcile: false,
      },
      resultHint: {
        schemaVersion: 1,
        orderId: order.id,
        orderVersion: order.version,
        stage: "paid",
      },
    },
    ownerId,
    order.id,
  );
}

export const maySubmitCallback = (value) =>
  ["prepared", "authentication_open"].includes(value.resultHint.stage);
export const accountDenied = (error) =>
  Boolean(error?.sessionChanged || [401, 403].includes(error?.status));

export function resultFailure(error) {
  if (accountDenied(error))
    return {
      code: "account",
      message:
        "로그인 계정 또는 이용 권한이 바뀌어 개인 주문 정보를 표시하지 않습니다.",
    };
  return (
    {
      404: {
        code: "missing",
        message:
          "현재 계정에서 조회할 수 있는 주문을 찾지 못했습니다. 주문을 시작한 계정의 내 주문을 확인해주세요.",
      },
      400: {
        code: "conditions",
        message:
          "돌아온 정보와 현재 주문 조건을 다시 확인해야 합니다. 같은 주문의 기록을 먼저 확인해주세요.",
      },
      409: {
        code: "conditions",
        message:
          "주문 상태·가격 또는 제공 조건이 달라졌을 수 있습니다. 같은 주문의 처리 상태를 먼저 확인해주세요.",
      },
      429: {
        code: "limited",
        message:
          "확인 요청이 잠시 제한됐습니다. 잠시 기다린 뒤 같은 주문의 서버 기록을 읽어주세요.",
      },
      503: {
        code: "connection_wait",
        message:
          "현재 결제 연결을 사용할 수 없습니다. 새 결제를 시작하지 말고 같은 주문의 기록을 확인해주세요.",
      },
    }[error?.status] || {
      code: "unknown",
      message:
        "승인 요청의 결과를 확인하지 못했습니다. 결제 완료나 취소로 추측하지 말고 같은 주문의 서버 기록을 확인해주세요.",
    }
  );
}

export function callbackNotice(code) {
  return (
    {
      PAY_PROCESS_CANCELED: "인증 화면에서 취소 안내를 받았습니다.",
      PAY_PROCESS_ABORTED:
        "인증이 중단되었거나 완료되지 않았다는 안내를 받았습니다.",
    }[code] || "인증 화면에서 돌아왔습니다."
  );
}

export const resultCopy = {
  prepared: [
    "아직 승인을 시작하지 않은 주문입니다.",
    "서버에 주문과 조건만 준비되어 있습니다. 인증 결과가 있다면 같은 주문으로 승인 여부를 확인합니다.",
  ],
  authentication_open: [
    "인증 결과를 확인할 차례입니다.",
    "인증 요청 기록이 있습니다. 아직 서버가 결제 승인을 확인한 상태는 아닙니다.",
  ],
  approval_pending: [
    "같은 주문의 승인 결과를 확인해주세요.",
    "앞선 결제 요청의 결과를 확인해야 합니다. 새 결제를 시작하지 말고 같은 주문에서 확인을 이어가세요.",
  ],
  approval_processing: [
    "승인 확인이 진행 중입니다.",
    "서버에서 승인 결과를 확인하고 있습니다. 새로고침으로 승인을 다시 요청하지 않으며 같은 주문의 기록을 읽을 수 있습니다.",
  ],
  paid: [
    "결제 승인을 확인했습니다.",
    "서버에서 결제사 승인 결과를 검증했습니다. 현재 수강 가능 여부는 수강권 정보로 따로 확인합니다.",
  ],
  cancellation_pending: [
    "전액 취소 결과를 확인 중입니다.",
    "아직 취소 완료로 확정하지 않습니다. 같은 주문과 현재 수강권을 내 주문에서 확인해주세요.",
  ],
  refunded: [
    "전액 취소를 확인했습니다.",
    "기존 주문과 학습 기록은 보존됩니다. 현재 수강권은 다른 발급 출처에 따라 달라질 수 있습니다.",
  ],
  authorization_closed: [
    "이 주문의 승인 요청이 종료됐습니다.",
    "뒤늦게 돌아온 인증 결과로 이 주문을 승인하지 않습니다. 결제사의 결제 취소나 환불을 실행한 기록은 아닙니다.",
  ],
};
