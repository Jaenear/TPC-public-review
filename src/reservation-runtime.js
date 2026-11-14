import { createReservationSession } from "./reservation-session.js";
import { userError } from "./reservation-model.js";
export const safeStorage = () => { try { return window.sessionStorage; } catch { return null; } };
const roleSignature = (user) => JSON.stringify([user?.id, user?.role, [...(user?.roles || [])].sort()]);
export function reservationLife(root, ctx, onDispose = () => {}) {
  const route = location.hash, identity = roleSignature(ctx.user), abort = new AbortController(); let ended = false, observer;
  const active = () => !ended && root.isConnected && location.hash === route && roleSignature(ctx.user) === identity;
  function end(clear) { if (ended) return; ended = true; abort.abort(); observer?.disconnect(); if (clear) root.replaceChildren(); onDispose(); }
  const dispose = () => end(true);
  window.addEventListener("hashchange", dispose, { once: true, signal: abort.signal });
  observer = new MutationObserver(() => { if (!root.isConnected) dispose(); }); observer.observe(root.ownerDocument.body, { childList: true, subtree: true });
  return { active, signal: abort.signal, dispose, stop: () => end(false) };
}
export function mountReservationRuntime(root, ctx, { initial, validate, onValue, refresh, scope = location.hash }) {
  const area = root.querySelector("[data-rv-body]"), feedback = root.querySelector("[data-rv-feedback]"), recovery = root.querySelector("[data-rv-recovery]");
  let session, painted = initial, disabled = false;
  const life = reservationLife(root, ctx, () => session?.dispose());
  function deny(e) { disabled = true; area.replaceChildren(); recovery.replaceChildren(); feedback.textContent = userError(e); root.querySelector("[data-rv-refresh]").disabled = true; life.stop(); }
  function paint(state) {
    if (!life.active() || disabled) return;
    if (state.value && painted !== state.value) { painted = state.value; onValue(state.value); if (!life.active()) return; }
    feedback.textContent = state.message; feedback.classList.toggle("is-error", state.error);
    recovery.innerHTML = state.pending ? `<section class="rv-recovery"><strong>원 요청 결과 확인이 필요합니다.</strong><p>결과를 확인할 때까지 새 예약 동작은 잠시 멈춥니다.</p><div class="rv-recovery-actions"><button type="button" data-rv-lookup class="btn btn-outline btn-sm" ${state.busy ? "disabled" : ""}>원 요청 결과 조회</button>${state.notFound ? `${session.canRetry() ? '<button type="button" data-rv-retry class="btn btn-outline btn-sm">같은 원 요청 다시 전송</button>' : '<p class="p-muted">이전에 입력한 본문은 이 화면에 보관되어 있지 않습니다. 자동 재전송하지 않습니다.</p>'}<button type="button" data-rv-release-pending class="btn btn-ghost btn-sm">미기록 요청 정리</button>` : ""}</div></section>` : "";
    const locked = Boolean(state.busy || state.pending || state.needsRefresh);
    area.querySelectorAll("form input, form select, form textarea, form button, [data-rv-slot], [data-rv-day], [data-rv-month], [data-rv-zone], [data-rv-slot-page], [data-rv-policy-choice], [data-rv-policies-page]").forEach((e) => { if (!e.hasAttribute("data-rv-always-disabled")) e.disabled = locked; });
    root.querySelector("[data-rv-refresh]").disabled = Boolean(state.busy || state.pending);
  }
  session = createReservationSession({ api: (path, options) => ctx.api(path, { ...options, signal: life.signal }), ownerId: ctx.user.id, scope, initial, validate, active: life.active, storage: safeStorage(), changed: paint, denied: deny });
  root.addEventListener("click", async (event) => {
    const target = event.target.closest("button"); if (!target || !life.active() || disabled) return;
    if (target.hasAttribute("data-rv-lookup")) await session.lookup();
    else if (target.hasAttribute("data-rv-retry")) await session.retry();
    else if (target.hasAttribute("data-rv-release-pending")) session.release();
    else if (target.hasAttribute("data-rv-refresh")) await session.refresh(refresh);
  }, { signal: life.signal });
  const error = (e) => { if (!life.active() || disabled) return; if (e?.sessionChanged || [401, 403, 404].includes(e?.status)) { session.dispose(); deny(e); } else { if (e?.status === 409) session.state.needsRefresh = true; session.state.error = true; session.state.message = userError(e); session.paint(); } };
  const canAct = () => life.active() && !disabled && !session.state.busy && !session.state.pending && !session.state.needsRefresh;
  session.paint();
  return { life, session, area, feedback, error, canAct, paint: () => session.paint() };
}
