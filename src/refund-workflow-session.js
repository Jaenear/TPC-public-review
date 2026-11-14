import {
  checkedRefundCase,
  checkedRefundReceipt,
  readRefundRecovery,
  rememberRefundRecovery,
  clearRefundRecovery,
} from "./refund-workflow-model.js";

// No timers or automatic POST retries. All effects belong to the current page.
export function createRefundSession({
  api,
  ownerId,
  orderId,
  operator,
  initial,
  storage = null,
  active = () => true,
  changed = () => {},
  denied = () => {},
  makeKey = () => crypto.randomUUID(),
}) {
  const base = `${operator ? "/api/manage/purchases" : "/api/checkout/orders"}/${encodeURIComponent(orderId)}/refund-case`;
  const state = {
    value: checkedRefundCase(initial, orderId, ownerId, operator),
    events: [...initial.events],
    nextBefore: initial.page.nextBefore,
    busy: false,
    pending: readRefundRecovery(storage, ownerId, orderId, operator),
    notFound: false,
    canRetry: false,
    message:
      "현재 서버 기록입니다. 조회·새로고침은 취소나 답변을 실행하지 않습니다.",
    error: false,
    receipt: null,
    needsRefresh: false,
  };
  let body = null,
    alive = true;
  const owns = () => alive && active();
  const emit = () => {
    if (owns()) changed(state);
  };
  const accept = (data, intent = null) => {
    const v = checkedRefundCase(data.refundCase, orderId, ownerId, operator);
    if (intent) state.receipt = checkedRefundReceipt(data.receipt, v, intent);
    state.value = v;
    state.events = [...v.events];
    state.nextBefore = v.page.nextBefore;
    state.needsRefresh = false;
  };
  function clear() {
    clearRefundRecovery(storage, ownerId, orderId, operator);
    state.pending = null;
    state.notFound = false;
    state.canRetry = false;
    body = null;
  }
  function failure(e, missingOrder = false) {
    if (
      e.sessionChanged ||
      [401, 403].includes(e.status) ||
      (e.status === 404 && (missingOrder || !state.pending))
    ) {
      clear();
      state.value = null;
      state.events = [];
      alive = false;
      denied(e);
      return;
    }
    state.error = true;
    state.needsRefresh = true;
    state.message =
      e.message || "연결을 확인하지 못했습니다. 접수 기록을 먼저 조회해주세요.";
  }
  async function lookup() {
    try {
      const data = await api(
        `${base}/receipts/${encodeURIComponent(state.pending.requestKey)}`,
        { ownerId },
      );
      if (!owns()) return false;
      accept(data, state.pending);
      clear();
      state.error = false;
      state.message =
        "저장된 요청의 접수 기록을 확인했습니다. 같은 처리를 다시 실행하지 않았습니다.";
      return true;
    } catch (e) {
      if (!owns()) return false;
      if (e.status === 404) {
        // A missing receipt must not keep an inaccessible order on screen.
        try {
          const fresh = await api(base, { ownerId });
          if (!owns()) return false;
          accept(fresh);
        } catch (readError) {
          if (owns()) failure(readError, true);
          return false;
        }
        state.notFound = true;
        state.canRetry = Boolean(body);
        state.error = true;
        state.message =
          "조회 시점에 이 요청의 접수 기록이 없습니다. 응답이 늦을 수 있으므로 기록을 다시 확인할 수 있습니다.";
      } else failure(e);
      return false;
    }
  }
  async function run(work) {
    if (!owns() || state.busy) return;
    state.busy = true;
    emit();
    try {
      await work();
    } catch (e) {
      if (owns()) failure(e);
    } finally {
      state.busy = false;
      emit();
    }
  }
  async function post() {
    if (!owns() || !state.pending || !body) return;
    const kind = state.pending.kind,
      intent = state.pending;
    const target =
      kind === "inquiry"
        ? "inquiry"
        : kind === "review"
          ? "reviews"
          : "actions";
    try {
      const data = await api(`${base}/${target}`, {
        method: "POST",
        ownerId,
        body,
      });
      if (!owns()) return;
      accept(data, intent);
      clear();
      state.error = false;
      state.message =
        kind === "review"
          ? "검토를 저장했습니다. 회원 안내와 결제사 취소 요청은 아직 실행하지 않았습니다."
          : kind === "inquiry"
            ? "취소 문의를 접수했습니다. 운영 검토와 결제사 결과를 이 기록에서 확인할 수 있습니다."
            : data.providerOutcome === "unverified"
              ? "결제사 취소 결과가 아직 확인되지 않았습니다. 수강권을 유지하며 같은 작업의 결과를 다시 확인합니다."
              : "처리 결과를 서버 기록에서 확인했습니다.";
    } catch (e) {
      if (!owns()) return;
      if (e.sessionChanged || [401, 403].includes(e.status)) return failure(e);
      // A failed response is not evidence that the command did not commit.
      const found = await lookup();
      if (!owns() || found) return;
      if ([400, 409, 422].includes(e.status) && state.notFound) {
        clear();
        try {
          const data = await api(base, { ownerId });
          if (!owns()) return;
          accept(data);
        } catch (readError) {
          if (owns()) failure(readError);
        }
        if (!owns()) return;
        state.error = true;
        state.message =
          e.message +
          " 입력 내용은 유지했습니다. 최신 근거를 읽고 확인 항목을 다시 선택해주세요.";
        state.resetChecks = true;
      }
    }
  }
  return {
    state,
    dispose() {
      alive = false;
      body = null;
    },
    refresh: () =>
      run(async () => {
        if (state.pending) return lookup();
        const data = await api(base, { ownerId });
        if (!owns()) return;
        accept(data);
        state.resetChecks = true;
        state.error = false;
        state.message = operator
          ? "현재 기록을 다시 확인했습니다. 검토 확인 항목은 새 근거를 읽은 뒤 선택해주세요."
          : "현재 주문과 처리 이력을 다시 확인했습니다.";
      }),
    recover: () =>
      run(async () => {
        if (state.pending) await lookup();
      }),
    submit: (kind, fields) =>
      run(async () => {
        if (state.pending || state.needsRefresh) return;
        const requestKey = makeKey();
        body = { ...fields, requestKey, expectedVersion: state.value.version };
        state.pending = { ownerId, orderId, operator, requestKey, kind };
        rememberRefundRecovery(storage, state.pending);
        state.notFound = false;
        state.canRetry = false;
        state.error = false;
        state.message =
          "요청을 접수합니다. 응답이 끊기면 같은 요청의 기록을 먼저 확인합니다.";
        emit();
        await post();
      }),
    retry: () =>
      run(async () => {
        if (!state.pending || !body) return;
        state.notFound = false;
        if (!(await lookup()) && owns() && state.notFound) await post();
      }),
    release: () =>
      run(async () => {
        if (!state.pending || !state.notFound) return;
        // A read must succeed before another editable command becomes available.
        const data = await api(base, { ownerId });
        if (!owns()) return;
        accept(data);
        clear();
        state.resetChecks = true;
        state.error = false;
        state.message =
          "최신 기록을 읽었습니다. 남아 있는 작업 상태를 확인한 뒤 새 요청을 작성해주세요.";
      }),
    more: () =>
      run(async () => {
        if (!state.nextBefore) return;
        const cursor = state.nextBefore;
        const data = await api(`${base}?before=${cursor}`, { ownerId });
        if (!owns()) return;
        const v = checkedRefundCase(
          data.refundCase,
          orderId,
          ownerId,
          operator,
        );
        if (
          v.version !== state.value.version ||
          v.context.contextHash !== state.value.context.contextHash
        ) {
          state.resetChecks = true;
          // Page data cannot stand in for the newest timeline.
          const fresh = await api(base, { ownerId });
          if (owns()) {
            accept(fresh);
            state.message = "기록이 추가되어 최신 이력을 다시 읽었습니다.";
          }
          return;
        }
        if (v.events.some((e) => e.version >= cursor))
          throw new Error("이전 이력의 연결을 확인하지 못했습니다.");
        const ids = new Set(state.events.map((e) => e.id));
        state.events.push(...v.events.filter((e) => !ids.has(e.id)));
        state.nextBefore = v.page.nextBefore;
      }),
  };
}
