import { esc } from "./ui.js";
import { authGate, head } from "./platform-ui.js";
import { checkedRefundCase } from "./refund-workflow-model.js";
import {
  checkedRefundContext,
  refundReadError,
} from "./refund-context-model.js";
import { refundContextHTML } from "./refund-context-view.js";
import { createRefundSession } from "./refund-workflow-session.js";
import {
  blankRefundDraft,
  refundWorkflowBody,
  refundRecoveryHTML,
} from "./refund-workflow-view.js";

export async function refundWorkflowPage(orderId, operator, ctx) {
  if (!ctx.user)
    return authGate(ctx, "문의한 계정으로 주문의 처리 이력을 확인해주세요.");
  const ownerId = ctx.user.id;
  let value;
  try {
    const data = await ctx.api(
      `${operator ? "/api/manage/purchases" : "/api/checkout/orders"}/${encodeURIComponent(orderId)}/refund-case`,
      { ownerId },
    );
    value = checkedRefundCase(data.refundCase, orderId, ownerId, operator);
  } catch (error) {
    return {
      html: `<section class="container p-page">${head("REFUND DESK", "주문을 다시 확인해주세요.", refundReadError(error))}<a class="btn btn-outline" href="#/${operator ? "manage?tab=purchases" : "purchases"}">주문 목록으로</a></section>`,
    };
  }
  return {
    html: `<section class="container p-page rf-page" id="refund-workflow"><div class="rf-topline"><a class="back-link" href="#/${operator ? "manage?tab=purchases" : "purchases"}">← ${operator ? "주문 운영" : "내 주문"}</a><button type="button" class="btn btn-outline btn-sm" data-rf-refresh>현재 기록 다시 조회</button></div><div data-rf-body>${refundWorkflowBody(value)}</div><div class="rf-feedback" role="status" aria-live="polite" tabindex="-1" data-rf-feedback></div><div data-rf-recovery></div></section>`,
    bind() {
      mountRefundWorkflow(
        document.querySelector("#refund-workflow"),
        value,
        ctx,
      );
    },
  };
}

export function mountRefundWorkflow(root, initial, ctx) {
  const ownerId = ctx.user.id,
    orderId = initial.context.order.id,
    operator = initial.audience === "operator",
    route = location.hash;
  const abort = new AbortController(),
    doc = root.ownerDocument;
  const area = root.querySelector("[data-rf-body]"),
    feedback = root.querySelector("[data-rf-feedback]"),
    recovery = root.querySelector("[data-rf-recovery]");
  let draft = blankRefundDraft(),
    last = null,
    lastEvents = "",
    disposed = false,
    initiating = null;
  const active = () =>
    !disposed &&
    root.isConnected &&
    location.hash === route &&
    ctx.user?.id === ownerId;
  function capture() {
    for (const form of root.querySelectorAll("[data-rf-form]")) {
      for (const name of [
        "reason",
        "decision",
        "basis",
        "operatorNote",
        "publicMessage",
        "cancelReason",
      ])
        if (form.elements.namedItem(name))
          draft[name] = form.elements.namedItem(name).value;
      if (form.dataset.rfForm === "review")
        draft.checks = [...form.querySelectorAll('[name="check"]:checked')].map(
          (c) => c.value,
        );
      if (form.elements.namedItem("confirm"))
        draft.confirm = form.elements.namedItem("confirm").checked;
    }
  }
  function disable(s) {
    root.querySelector("[data-rf-refresh]").disabled = s.busy;
    for (const form of root.querySelectorAll("[data-rf-form]")) {
      const permitted =
        s.value?.actions[
          {
            inquiry: "canInquire",
            review: "canReview",
            execute: "canExecute",
            recheck: "canRecheck",
          }[form.dataset.rfForm]
        ];
      form.querySelectorAll("input,textarea,select,button").forEach((e) => {
        e.disabled = Boolean(
          s.busy || s.pending || s.needsRefresh || !permitted,
        );
      });
    }
    root
      .querySelectorAll(
        "[data-rf-more],[data-rf-review-read],[data-rf-recover],[data-rf-retry],[data-rf-release]",
      )
      .forEach((b) => (b.disabled = s.busy));
  }
  function changed(s) {
    if (!active() || !s.value) return;
    const events = s.events.map((e) => e.id).join(",");
    if (last !== s.value || events !== lastEvents || s.resetChecks) {
      capture();
      if (
        (last &&
          (last.version !== s.value.version ||
            last.context.contextHash !== s.value.context.contextHash)) ||
        s.resetChecks
      ) {
        draft.checks = [];
        draft.confirm = false;
      }
      s.resetChecks = false;
      area.innerHTML = refundWorkflowBody(
        s.value,
        draft,
        s.events,
        s.nextBefore,
      );
      last = s.value;
      lastEvents = events;
    }
    feedback.textContent = s.message;
    feedback.classList.toggle("is-error", s.error);
    recovery.innerHTML = refundRecoveryHTML(s);
    disable(s);
    if (
      !s.busy &&
      initiating &&
      (!doc.activeElement || [doc.body, root].includes(doc.activeElement))
    ) {
      const target = root.querySelector(initiating) || feedback;
      target.focus({ preventScroll: true });
      target.scrollIntoView({ block: "nearest" });
    }
    if (!s.busy) initiating = null;
  }
  const session = createRefundSession({
    api: ctx.api,
    ownerId,
    orderId,
    operator,
    initial,
    storage: safeStorage(),
    active,
    changed,
    denied(error) {
      area.replaceChildren();
      recovery.replaceChildren();
      draft = blankRefundDraft();
      feedback.textContent = refundReadError(error);
      feedback.classList.add("is-error");
      root.querySelector("[data-rf-refresh]").disabled = true;
      dispose();
    },
  });
  function dispose() {
    if (disposed) return;
    disposed = true;
    session.dispose();
    abort.abort();
    observer.disconnect();
    draft = blankRefundDraft();
  }
  root.addEventListener(
    "submit",
    (event) => {
      const form = event.target.closest("[data-rf-form]");
      if (!form || !active()) return;
      event.preventDefault();
      if (session.state.busy || session.state.pending || !form.reportValidity())
        return;
      capture();
      const kind = form.dataset.rfForm;
      initiating = `[data-rf-form="${kind}"] [data-rf-submit]`;
      if (kind === "inquiry") session.submit(kind, { reason: draft.reason });
      else if (kind === "review")
        session.submit(kind, {
          decision: draft.decision,
          contextHash: session.state.value.context.contextHash,
          checks: [...draft.checks],
          basis: draft.basis,
          operatorNote: draft.operatorNote,
          publicMessage: draft.publicMessage,
          cancelReason: draft.decision === "reply" ? "" : draft.cancelReason,
        });
      else if (draft.confirm)
        session.submit(
          kind,
          kind === "execute"
            ? { action: kind, reviewId: session.state.value.currentReview.id }
            : { action: kind },
        );
    },
    { signal: abort.signal },
  );
  root.addEventListener(
    "change",
    (event) => {
      if (event.target.name !== "decision") return;
      capture();
      const cancel = root.querySelector("[data-rf-cancel-field]");
      cancel.hidden = draft.decision === "reply";
      cancel.querySelector("textarea").required = !cancel.hidden;
      cancel.querySelector("textarea").minLength = cancel.hidden ? 0 : 5;
    },
    { signal: abort.signal },
  );
  root.addEventListener(
    "click",
    async (event) => {
      const b = event.target.closest("button");
      if (!b || b.disabled || !active()) return;
      const action = ["refresh", "recover", "retry", "release", "more"].find(
        (a) => b.hasAttribute(`data-rf-${a}`),
      );
      if (action) {
        initiating = `[data-rf-${action}]`;
        await session[action]();
        return;
      }
      if (!b.dataset.rfReviewRead) return;
      b.disabled = true;
      try {
        const { reviewRecord: r } = await ctx.api(
          `/api/manage/purchases/${encodeURIComponent(orderId)}/refund-case/reviews/${encodeURIComponent(b.dataset.rfReviewRead)}`,
          { ownerId },
        );
        if (!active()) return;
        if (
          !r ||
          r.id !== b.dataset.rfReviewRead ||
          r.orderId !== orderId ||
          ![r.basis, r.operatorNote, r.publicMessage, r.cancelReason].every(
            (v) => typeof v === "string",
          )
        )
          throw new Error("저장 당시 검토 기록을 확인하지 못했습니다.");
        checkedRefundContext(r.context, orderId, r.reviewerId, true);
        ctx.modal(
          `<div class="rc-workspace"><p class="eyebrow">SAVED REVIEW · INTERNAL</p><h2>저장 당시 검토 원본</h2><p class="p-muted">현재 판단을 자동 갱신하거나 실행하지 않는 보존 기록입니다.</p><h3>검토 근거 · 운영자 전용</h3><p class="rf-preserve">${esc(r.basis)}</p>${r.operatorNote ? `<h3>추가 메모 · 운영자 전용</h3><p class="rf-preserve">${esc(r.operatorNote)}</p>` : ""}<h3>회원 안내 초안</h3><p class="rf-preserve">${esc(r.publicMessage)}</p>${r.cancelReason ? `<h3>회원·결제사 취소 사유</h3><p class="rf-preserve">${esc(r.cancelReason)}</p>` : ""}${refundContextHTML(r.context)}</div>`,
        );
      } catch (e) {
        if (e.sessionChanged || [401, 403].includes(e.status)) {
          area.replaceChildren();
          recovery.replaceChildren();
          feedback.textContent = refundReadError(e);
          dispose();
        } else if (active()) {
          feedback.textContent = e.message;
          feedback.classList.add("is-error");
        }
      } finally {
        if (active() && b.isConnected) b.disabled = false;
      }
    },
    { signal: abort.signal },
  );
  const observer = new MutationObserver(() => {
    if (!root.isConnected) dispose();
  });
  observer.observe(doc.body, { childList: true, subtree: true });
  window.addEventListener("hashchange", dispose, {
    once: true,
    signal: abort.signal,
  });
  changed(session.state);
  if (session.state.pending) session.recover();
  return { ...session, dispose };
}
function safeStorage() {
  try {
    return window.sessionStorage;
  } catch {
    return null;
  }
}
