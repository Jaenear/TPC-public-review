import { authGate, head } from "./platform-ui.js";
import { checkedDeskQueue, checkedDeskDetail, deskAPI, deskCSV, deskError, deskQuery, deskQueueHref, deskQueryString, deskTime } from "./reconciliation-model.js";
import { createDeskSession } from "./reconciliation-session.js";
import { blankDeskDraft, deskQueueHTML, deskDetailHTML, deskDetailBody, deskRecoveryHTML } from "./reconciliation-view.js";

const queuePath = "/manage/reconciliation";
function unavailable(error) {
  return { html: `<section class="container p-page">${head("RECONCILIATION DESK", "대사 기록을 확인해주세요.", deskError(error))}<a class="btn btn-outline" href="#/manage?tab=purchases">주문 운영으로</a></section>` };
}
function backLink(params) {
  try { return deskQueueHref(deskQuery(new URLSearchParams(params.get("from") || ""))); }
  catch { return "#" + queuePath; }
}
export async function renderReconciliation(path, params, ctx) {
  if (path !== queuePath && !path.startsWith(queuePath + "/")) return null;
  if (!ctx.user) return authGate(ctx, "운영 계정으로 대사 기록을 확인해주세요.");
  if (ctx.user.role !== "admin") return unavailable({ status: 403 });
  const ownerId = ctx.user.id;
  try {
    if (path === queuePath) {
      const q = deskQuery(params);
      const data = checkedDeskQueue(await ctx.api("/api/manage/reconciliation/desk?" + deskQueryString(q), { ownerId }), ownerId);
      return { html: deskQueueHTML(data, q, ctx), bind() { mountDeskQueue(document.querySelector("[data-rd-queue]"), data, q, ctx); } };
    }
    const match = path.match(/^\/manage\/reconciliation\/(order|event)\/([a-z0-9_-]{1,120})$/);
    if (!match) return unavailable({ status: 404 });
    const [, kind, id] = match;
    const value = checkedDeskDetail(await ctx.api(deskAPI(kind, id), { ownerId }), ownerId, kind, id);
    return { html: deskDetailHTML(value, ctx, backLink(params)), bind() { mountDeskDetail(document.querySelector("[data-rd-detail]"), value, ctx); } };
  } catch (error) { return unavailable(error); }
}

export function mountDeskQueue(root, data, q, ctx) {
  const route = location.hash, ownerId = ctx.user.id;
  const active = () => root.isConnected && location.hash === route && ctx.user?.id === ownerId && ctx.user?.role === "admin";
  root.querySelector("[data-rd-filter]").addEventListener("submit", (event) => {
    event.preventDefault(); if (!active() || !event.target.reportValidity()) return;
    try { ctx.navigate(deskQueueHref(q, Object.fromEntries(new FormData(event.target))).slice(1)); }
    catch { root.querySelector("[data-rd-feedback]").textContent = "필터의 정확한 내부 ID와 선택값을 확인해주세요."; }
  });
  root.querySelector("[data-rd-queue-refresh]").addEventListener("click", () => { if (active()) ctx.reload(); });
  root.querySelector("[data-rd-export]").addEventListener("click", () => {
    if (!active() || !data.items.length) return;
    const url = URL.createObjectURL(new Blob([deskCSV(data)], { type: "text/csv;charset=utf-8" }));
    const link = root.ownerDocument.createElement("a");
    link.href = url; link.download = `tpc-reconciliation-${q.tab}-${data.observedAt}.csv`;
    root.append(link); link.click(); link.remove(); URL.revokeObjectURL(url);
    root.querySelector("[data-rd-feedback]").textContent = `현재 내려받은 목록 ${data.items.length}건의 CSV를 만들었습니다.`;
  });
}
function safeStorage() { try { return window.sessionStorage; } catch { return null; } }

export function mountDeskDetail(root, initial, ctx) {
  const ownerId = ctx.user.id, kind = initial.subject.kind, subjectId = initial.subject.id, route = location.hash;
  const doc = root.ownerDocument, abort = new AbortController();
  const area = root.querySelector("[data-rd-detail-body]"), feedback = root.querySelector("[data-rd-feedback]"), recovery = root.querySelector("[data-rd-recovery]");
  let draft = blankDeskDraft(), group = "all", signature = "", draftVersion = 0, disposed = false, initiating = null;
  let session, timer, observer;
  const active = () => !disposed && root.isConnected && location.hash === route && ctx.user?.id === ownerId && ctx.user?.role === "admin";
  function capture() {
    for (const name of ["action", "note", "reason", "confirmCase", "confirmQuery", "acknowledgeScope"]) {
      const input = area.querySelector(`[name="${name}"]`);
      if (input) draft[name] = input.type === "checkbox" ? input.checked : input.value;
    }
  }
  function changed(s) {
    if (!active() || !s.value) return;
    capture();
    const cleared = s.clearDraft !== draftVersion;
    if (cleared) { draft = blankDeskDraft(); draftVersion = s.clearDraft; }
    if (s.resetChecks) { draft.confirmCase = false; draft.confirmQuery = false; draft.acknowledgeScope = false; }
    const next = JSON.stringify([s.value.subject, s.value.configuration, s.items, s.nextBefore, s.historical, group]);
    if (signature !== next || cleared || s.resetChecks) {
      const focused = doc.activeElement, name = area.contains(focused) ? focused.name : null;
      const selection = name && typeof focused.selectionStart === "number" ? [focused.selectionStart, focused.selectionEnd] : null;
      area.innerHTML = deskDetailBody(s.value, s, draft, group); signature = next;
      const replacement = name && area.querySelector(`[name="${name}"]`);
      if (replacement && !s.busy && !initiating) { replacement.focus({ preventScroll: true }); if (selection && !cleared) replacement.setSelectionRange?.(...selection); }
    }
    s.resetChecks = false;
    feedback.textContent = s.message; feedback.classList.toggle("is-error", s.error);
    recovery.innerHTML = deskRecoveryHTML(s);
    root.querySelector("[data-rd-observed]").textContent = deskTime(s.value.observedAt);
    root.querySelector("[data-rd-refresh]").disabled = Boolean(s.busy);
    area.querySelectorAll("form input,form textarea,form select,form button").forEach((input) => { input.disabled = Boolean(s.busy || s.pending || s.needsRefresh); });
    const caseButton = area.querySelector("[data-rd-case-form] button[type=submit]"), queryButton = area.querySelector("[data-rd-query-form] button[type=submit]");
    if (caseButton) caseButton.textContent = s.busy === "case" ? "검토 저장 중…" : "검토 기록 저장";
    if (queryButton) queryButton.textContent = s.busy === "query" ? "조회 요청 저장 중…" : "원 주문 재조회 요청";
    area.querySelectorAll("[data-rd-more]").forEach((input) => { input.disabled = Boolean(s.busy || s.pending); });
    if (!s.busy && initiating) {
      if (!doc.activeElement || doc.activeElement === doc.body || !doc.activeElement.isConnected) feedback.focus({ preventScroll: true });
      initiating = null;
    }
  }
  function dispose() {
    if (disposed) return;
    disposed = true; clearInterval(timer); observer?.disconnect(); abort.abort();
    session?.dispose(); draft = blankDeskDraft();
    area.replaceChildren(); recovery.replaceChildren();
  }
  session = createDeskSession({ api: ctx.api, ownerId, kind, subjectId, initial, storage: safeStorage(), active, changed,
    denied(error) { feedback.textContent = deskError(error); feedback.classList.add("is-error"); root.querySelector("[data-rd-refresh]").disabled = true; dispose(); } });
  root.addEventListener("submit", (event) => {
    const form = event.target.closest("[data-rd-case-form],[data-rd-query-form]");
    if (!form || !active()) return;
    event.preventDefault();
    if (session.state.busy || session.state.pending || session.state.needsRefresh || !form.reportValidity()) return;
    capture(); initiating = "submit";
    const query = form.hasAttribute("data-rd-query-form");
    session.submit(query ? "query" : "case", query ? { reason: draft.reason, confirm: draft.confirmQuery, acknowledgeScope: draft.acknowledgeScope } : { action: draft.action, note: draft.note, confirm: draft.confirmCase });
  }, { signal: abort.signal });
  root.addEventListener("click", (event) => {
    const button = event.target.closest("button"); if (!button || button.disabled || !active()) return;
    const choice = button.dataset.rdGroup;
    if (["all", "receipt", "query", "financial", "review"].includes(choice)) { group = choice; changed(session.state); return; }
    const action = ["refresh", "receipt", "retry", "release", "more"].find((a) => button.hasAttribute(`data-rd-${a}`));
    if (action) { initiating = action; session[action === "receipt" ? "lookup" : action](); }
  }, { signal: abort.signal });
  observer = new MutationObserver(() => { if (!root.isConnected) dispose(); });
  observer.observe(doc.body, { childList: true, subtree: true });
  window.addEventListener("hashchange", dispose, { once: true, signal: abort.signal });
  timer = setInterval(() => {
    if (!active()) { dispose(); return; }
    if (!doc.hidden && root.querySelector("[data-rd-auto]").checked && !session.state.busy && !session.state.pending) session.refresh();
  }, 10000);
  changed(session.state);
  return { ...session, dispose };
}
