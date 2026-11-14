import { checkedDetail, checkedPage, holdSeconds, holdStates, apiPath, detailPath } from "./reservation-model.js";
import { holdHTML, reservationHTML, eventsHTML } from "./reservation-view.js";
import { mountReservationRuntime } from "./reservation-runtime.js";

export function mountReservationDetail(root, initial, kind, ctx) {
  const id = initial[kind].id, path = apiPath(kind, id); let value = initial, receivedAt = performance.now(), eventPage = 1, eventToken = 0;
  const runtime = mountReservationRuntime(root, ctx, { initial, validate: (v) => checkedDetail(v, kind, { id }), refresh: path,
    onValue(v) {
      if (kind === "hold" && v.reservation) { ctx.navigate(detailPath("reservation", v.reservation.id)); return; }
      value = checkedDetail(v, kind, { id }); receivedAt = performance.now(); ++eventToken; eventPage = 1;
      runtime.area.innerHTML = kind === "hold" ? holdHTML(v) : reservationHTML(v);
      if (kind === "reservation") void readEvents(1); else tick();
    }
  });
  function tick() {
    if (!runtime.life.active() || !value?.hold) return;
    const n = holdSeconds(value.hold, receivedAt, performance.now()), target = root.querySelector("[data-rv-countdown]");
    if (target) target.textContent = value.hold.state !== "active" ? holdStates[value.hold.state] : n > 0 ? `${Math.floor(n / 60)}:${String(n % 60).padStart(2, "0")}` : "기한 종료 · 현재 상태를 조회해주세요";
    if (n === 0) root.querySelectorAll('[data-rv-action="request"] input, [data-rv-action="request"] textarea, [data-rv-action="request"] button').forEach((e) => { e.setAttribute("data-rv-always-disabled", ""); e.disabled = true; });
  }
  const interval = kind === "hold" ? setInterval(tick, 1000) : null;
  runtime.life.signal.addEventListener("abort", () => { clearInterval(interval); value = null; }, { once: true });
  async function readEvents(page) {
    const token = ++eventToken;
    try {
      const v = await ctx.api(path + "/events?page=" + page, { ownerId: ctx.user.id, signal: runtime.life.signal });
      if (!runtime.life.active() || token !== eventToken) return;
      checkedPage(v, "events", page); if (v.reservationId !== id || v.reservationVersion !== value.reservation.version) { runtime.session.state.needsRefresh = true; runtime.session.state.message = "다른 진행 기록이 있습니다. 현재 내용을 새로고침해주세요."; runtime.paint(); return; }
      eventPage = page; root.querySelector("[data-rv-events]").innerHTML = eventsHTML(v.events);
      root.querySelector("[data-rv-event-pages]").innerHTML = v.total > 20 ? `<nav class="p-pagination" aria-label="진행 이력 페이지"><button type="button" class="btn btn-outline btn-sm" data-rv-events-page="${page - 1}" ${page === 1 ? "disabled" : ""}>이전 이력</button><span>${page} / ${Math.ceil(v.total / 20)}</span><button type="button" class="btn btn-outline btn-sm" data-rv-events-page="${page + 1}" ${page * 20 >= v.total ? "disabled" : ""}>다음 이력</button></nav>` : "";
    } catch (e) { if (runtime.life.active() && token === eventToken) runtime.error(e); }
  }
  root.addEventListener("click", (event) => { const b = event.target.closest("[data-rv-events-page]"); if (b && !b.disabled && runtime.canAct()) { const n = Number(b.dataset.rvEventsPage); if (n > 0 && n !== eventPage) void readEvents(n); } }, { signal: runtime.life.signal });
  root.addEventListener("submit", async (event) => {
    const form = event.target; if (!form.matches("[data-rv-action], [data-rv-session]")) return; event.preventDefault();
    if (!runtime.canAct() || !form.reportValidity()) return;
    const fields = Object.fromEntries(new FormData(form)), r = value[kind], cap = value.capabilities;
    let action = form.dataset.rvAction, target = path, command, resultKind = kind, resultId = id;
    const body = { version: r.version, reason: fields.reason };
    try {
      if (form.hasAttribute("data-rv-session")) {
        const ordinal = Number(form.dataset.rvSession), s = r.sessions.find((s) => s.ordinal === ordinal), c = cap.sessions.find((s) => s.ordinal === ordinal);
        const permission = { accept: "canAccept", reject: "canReject", complete: "canComplete", no_show: "canNoShow" }[fields.action];
        if (!s || !permission || !c?.[permission]) return;
        action = ["accept", "reject"].includes(fields.action) ? "respond" : "perform";
        target = "/api/studio/coaching/reservations/" + id;
        Object.assign(body, { sessionOrdinal: ordinal, sessionVersion: s.version, action: fields.action });
        command = "reservation." + action + ":" + id;
      } else if (action === "request") {
        if (!cap.canRequest || !holdSeconds(r, receivedAt, performance.now())) return;
        if (r.parentReservationId) body.reservationVersion = r.parentReservationVersion;
        command = "reservation.request:" + id; resultKind = "reservation"; resultId = r.parentReservationId;
      } else if (action === "release") { if (!cap.canRelease) return; command = "hold.release:" + id; }
      else if (action === "cancel") { if (!cap.canCancel || !cap.cancelCauses.includes(fields.cause)) return; body.cause = fields.cause; command = "reservation.cancel:" + id; }
      else if (action === "expire") { if (!cap.canExpire) return; target = "/api/manage/coaching/" + (kind === "hold" ? "holds/" : "reservations/") + id; command = kind + ".expire:" + id; }
      else return;
      await runtime.session.send(target + "/" + action, body, { entityType: resultKind, entityId: resultId, productId: r.productId, memberId: r.memberId, command });
      tick();
    } catch (e) { runtime.error(e); }
  }, { signal: runtime.life.signal });
  if (kind === "reservation") void readEvents(1); else tick();
  return runtime;
}
