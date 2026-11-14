import {
  checkedRefundContext,
  refundReadError,
} from "./refund-context-model.js";
import {
  refundContextShell,
  refundContextHTML,
  refundOrdersDeniedHTML,
} from "./refund-context-view.js";

export function mountRefundContext(
  root,
  dialog,
  ctx,
  orderId,
  operator = false,
  onAccountDenied = () => {},
) {
  const ownerId = ctx.user?.id || null;
  const route = typeof location === "undefined" ? null : location.hash;
  const area = root.querySelector("[data-refund-context-content]");
  const feedback = root.querySelector("[data-refund-context-feedback]");
  const refresh = root.querySelector("[data-refund-context-refresh]");
  const abort = new AbortController();
  let disposed = false,
    busy = false;
  const active = () =>
    !disposed &&
    ownerId &&
    ctx.user?.id === ownerId &&
    root.isConnected &&
    dialog.open &&
    dialog.querySelector("[data-refund-context-root]") === root &&
    (route === null || location.hash === route);
  function dispose() {
    const ownsDialog =
      dialog.querySelector("[data-refund-context-root]") === root;
    disposed = true;
    abort.abort();
    area.replaceChildren();
    feedback.textContent = "";
    refresh.disabled = true;
    if (ownsDialog && dialog.open) dialog.close();
  }
  async function read() {
    if (!active() || busy) return;
    const doc = root.ownerDocument;
    const restoreFocus = doc?.activeElement === refresh;
    busy = true;
    refresh.disabled = true;
    // An older observation is never presented as the current basis of a review.
    area.replaceChildren();
    feedback.classList.remove("is-error");
    feedback.textContent = "현재 서버 기록을 조회합니다…";
    try {
      const data = await ctx.api(
        `${operator ? "/api/manage/purchases" : "/api/checkout/orders"}/${encodeURIComponent(orderId)}/refund-context`,
        { ownerId },
      );
      if (!active()) return dispose();
      const value = checkedRefundContext(
        data.refundContext,
        orderId,
        ownerId,
        operator,
      );
      area.innerHTML = refundContextHTML(value);
      feedback.textContent = "현재 조회 시점의 검토 근거를 확인했습니다.";
    } catch (error) {
      // api() can replace the dialog with reauthentication before rejecting.
      // Clear the old order workspace even when that replacement retired root.
      if (error.sessionChanged || [401, 403].includes(error.status))
        onAccountDenied();
      if (!active()) return dispose();
      area.replaceChildren();
      feedback.classList.add("is-error");
      feedback.textContent = refundReadError(error);
      if (error.sessionChanged || [401, 403].includes(error.status)) {
        disposed = true;
        abort.abort();
      }
    } finally {
      busy = false;
      if (active()) {
        refresh.disabled = false;
        // Native disabled buttons lose focus. Restore the initiating control
        // only if the user has not focused another element while awaiting GET.
        if (
          restoreFocus &&
          (!doc.activeElement ||
            [doc.body, dialog, root].includes(doc.activeElement))
        ) {
          refresh.focus({ preventScroll: true });
          refresh.scrollIntoView({ block: "nearest" });
        }
      }
    }
  }
  refresh.onclick = read;
  dialog.addEventListener("close", dispose, {
    once: true,
    signal: abort.signal,
  });
  if (typeof window !== "undefined")
    window.addEventListener("hashchange", dispose, {
      once: true,
      signal: abort.signal,
    });
  return { ready: read(), read, dispose };
}

export function bindRefundContexts(ctx, scope = document, operator = false) {
  scope.querySelectorAll("[data-refund-context]").forEach((button) => {
    button.onclick = async () => {
      if (!button.isConnected || button.disabled || !ctx.user?.id) return;
      let ready;
      const workspace = button.closest?.(".p-purchase-list");
      const clearPreviousOrders = () => {
        if (workspace?.isConnected)
          workspace.innerHTML = refundOrdersDeniedHTML();
      };
      ctx.modal(refundContextShell(), (dialog) => {
        const root = dialog.querySelector("[data-refund-context-root]");
        ready = mountRefundContext(
          root,
          dialog,
          ctx,
          button.dataset.refundContext,
          operator,
          clearPreviousOrders,
        ).ready;
      });
      await ready;
    };
  });
}
