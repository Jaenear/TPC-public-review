import { checkedProduct, checkedSlots, selectionBody, localDay, nextDay } from "./coaching-model.js";
import { checkedDetail, checkedPolicies, checkedScheduling, monthDays, shiftMonth, detailPath } from "./reservation-model.js";
import { calendarHTML, monthHTML, slotsHTML, selectionHTML, rulesHTML } from "./reservation-view.js";
import { mountReservationRuntime } from "./reservation-runtime.js";

export async function loadReservationCalendar(ctx, productId, parentId = null, ordinal = null, mode = null) {
  const opts = { ownerId: ctx.user.id };
  const product = checkedProduct((await ctx.api("/api/coaching/products/" + productId, opts)).product, productId);
  let parent = null, policies;
  if (parentId) {
    parent = checkedDetail(await ctx.api("/api/coaching/reservations/" + parentId, opts), "reservation", { id: parentId, productId });
    const original = parent.reservation.quote.product;
    if (product.durationMinutes !== original.durationMinutes || product.method !== original.method) throw Object.assign(new Error("기존 예약과 현재 상품의 시간 또는 진행 방식이 다릅니다. 담당자의 제공 조건 확인이 필요합니다."), { status: 409, selectionUnavailable: true });
    const cap = parent.capabilities.sessions.find((s) => s.ordinal === ordinal);
    if (!cap) throw Object.assign(new Error("현재 예약의 회차를 확인해주세요."), { status: 404 });
    const rights = parent.capabilities, operating = ctx.user.role === "admin" || rights.isOperator && !rights.isMember;
    const permitted = mode === "change" ? operating || rights.isMember && !parent.reservation.quote.policy.rescheduleRequiresCoach : rights.isMember;
    if (!permitted) throw Object.assign(new Error("이 회차의 일정 처리 권한을 확인해주세요."), { status: 403 });
    if (!(mode === "change" ? cap.canReschedule : cap.canSchedule)) throw Object.assign(new Error("현재 회원·상품 조건이나 회차 진행 기한을 먼저 확인해야 합니다. 담당자의 진행 조건 확인이 필요합니다."), { status: 409, selectionUnavailable: true });
    policies = { policies: [{ id: parent.reservation.policyId, payload: parent.reservation.quote.policy }], page: 1, total: 1, pageSize: 20, asOf: parent.capabilities.asOf };
  } else policies = checkedPolicies(await ctx.api("/api/coaching/products/" + productId + "/reservation-policies", opts), product);
  return { product, parent, policies, ordinal, mode };
}
export function mountReservationCalendar(root, initial, ctx, options = {}) {
  let value = initial, selected = null, slots = null, policy = null, readToken = 0, policyToken = 0;
  let day = options.day || localDay("Asia/Seoul", new Date(initial.policies.asOf)), zone = "Asia/Seoul", page = 1;
  monthDays(day);
  const reload = () => loadReservationCalendar(ctx, initial.product.id, initial.parent?.reservation.id, initial.ordinal, initial.mode);
  const runtime = mountReservationRuntime(root, ctx, { initial, validate: (v) => v, refresh: reload,
    onValue(v) {
      if (v.hold || v.reservation) { ctx.navigate(detailPath(v.hold ? "hold" : "reservation", (v.hold || v.reservation).id)); return; }
      value = v; runtime.area.innerHTML = calendarHTML(v.product, v.parent, v.ordinal, v.mode); selected = null; policy = null; slots = null; ++policyToken; drawPolicies(); drawMonth(); void readSlots();
    }
  });
  const q = (s) => root.querySelector(s);
  runtime.life.signal.addEventListener("abort", () => { value = null; selected = null; slots = null; policy = null; ++readToken; ++policyToken; }, { once: true });
  function drawMonth(focusDay = null) {
    q("[data-rv-calendar]").innerHTML = monthHTML(day, zone, localDay(zone, new Date(value.policies.asOf)));
    if (focusDay) q(`[data-rv-day="${focusDay}"]`)?.focus();
    runtime.paint();
  }
  function drawPolicies() {
    policy = value.parent ? value.policies.policies[0] : null;
    const target = q("[data-rv-policies]");
    target.innerHTML = "";
    if (value.parent) target.innerHTML = '<p class="rv-notice">기존 예약의 정책을 유지합니다.</p>';
    else if (!value.policies.total) target.innerHTML = '<p class="rv-notice">아직 검토된 예약 정책이 없습니다. 담당자가 정책을 확인한 뒤 진행할 수 있습니다.</p>';
    else target.innerHTML = `<label>검토된 기술 정책<select data-rv-policy-choice><option value="">조건을 선택해 확인해주세요.</option>${value.policies.policies.map((p) => `<option value="${p.id}">${p.createdAt.slice(0, 10)} 검토 · 임시 확보 ${p.payload.holdSeconds}초</option>`).join("")}</select></label>${value.policies.total > 20 ? `<div class="rv-recovery-actions"><button type="button" class="btn btn-outline btn-sm" data-rv-policies-page="${value.policies.page - 1}" ${value.policies.page === 1 ? 'disabled data-rv-always-disabled' : ""}>이전 정책</button><span>${value.policies.page} / ${Math.ceil(value.policies.total / 20)}</span><button type="button" class="btn btn-outline btn-sm" data-rv-policies-page="${value.policies.page + 1}" ${value.policies.page * 20 >= value.policies.total ? 'disabled data-rv-always-disabled' : ""}>다음 정책</button></div>` : ""}<div data-rv-policy-rules></div>`;
  }
  function paintSelection() {
    const target = q("[data-rv-selection]");
    target.innerHTML = selected && policy ? selectionHTML(selected, zone, policy, value.mode) : '<p class="rv-placeholder">시간과 검토된 정책을 함께 선택해주세요.</p>';
    runtime.paint();
  }
  async function readSlots() {
    const token = ++readToken; selected = null; slots = null; paintSelection(); q("[data-rv-slots]").textContent = "선택한 날짜의 현재 가능한 시간을 조회하고 있습니다…";
    const params = new URLSearchParams({ productId: value.product.id, timeZone: zone, fromDate: day, toDate: nextDay(day, 1), page: String(page) });
    let path = "/api/coaching/slots?";
    if (value.parent) {
      const r = value.parent.reservation, s = r.sessions.find((s) => s.ordinal === value.ordinal);
      params.delete("productId"); params.set("version", r.version); params.set("sessionOrdinal", s.ordinal); params.set("sessionVersion", s.version); params.set("mode", value.mode === "change" ? "reschedule" : "request");
      path = "/api/coaching/reservations/" + r.id + "/scheduling-candidates?";
    }
    try {
      const v = await ctx.api(path + params, { ownerId: ctx.user.id, signal: runtime.life.signal });
      if (!runtime.life.active() || token !== readToken) return;
      slots = value.parent ? checkedScheduling(v, value, page, { day, zone }) : checkedSlots(v, value.product, page); q("[data-rv-slots]").innerHTML = slotsHTML(slots, zone, null);
      if (runtime.session.state.error && !runtime.session.state.needsRefresh && !runtime.session.state.pending && !runtime.session.state.busy) { runtime.session.state.error = false; runtime.session.state.message = "현재 가능한 시간을 다시 조회했습니다. 조건을 확인한 뒤 진행해주세요."; }
      runtime.paint();
    } catch (e) { if (runtime.life.active() && token === readToken) { q("[data-rv-slots]").replaceChildren(); selected = null; paintSelection(); runtime.error(e); } }
  }
  root.addEventListener("click", async (event) => {
    const b = event.target.closest("button"); if (!b || !runtime.canAct() || b.disabled) return;
    if (b.hasAttribute("data-rv-day")) { day = b.dataset.rvDay; page = 1; drawMonth(day); await readSlots(); }
    else if (b.hasAttribute("data-rv-month")) { day = shiftMonth(day, Number(b.dataset.rvMonth)); page = 1; drawMonth(day); await readSlots(); }
    else if (b.hasAttribute("data-rv-slot-page")) { page = Number(b.dataset.rvSlotPage); if (page >= 1) await readSlots(); }
    else if (b.hasAttribute("data-rv-slot")) { selected = slots?.slots.find((s) => s.id === b.dataset.rvSlot) || null; if (slots) q("[data-rv-slots]").innerHTML = slotsHTML(slots, zone, selected?.id); paintSelection(); }
    else if (b.hasAttribute("data-rv-policies-page")) {
      const n = Number(b.dataset.rvPoliciesPage), token = ++policyToken; policy = null; paintSelection();
      try { const v = await ctx.api("/api/coaching/products/" + value.product.id + "/reservation-policies?page=" + n, { ownerId: ctx.user.id, signal: runtime.life.signal }); if (!runtime.life.active() || token !== policyToken) return; value = { ...value, policies: checkedPolicies(v, value.product, n) }; drawPolicies(); runtime.paint(); }
      catch (e) { if (runtime.life.active() && token === policyToken) runtime.error(e); }
    }
  }, { signal: runtime.life.signal });
  root.addEventListener("change", async (event) => {
    if (!runtime.canAct()) return;
    if (event.target.matches("[data-rv-zone]")) { zone = event.target.value; page = 1; drawMonth(); await readSlots(); }
    if (event.target.matches("[data-rv-policy-choice]")) { policy = value.policies.policies.find((p) => p.id === event.target.value) || null; q("[data-rv-policy-rules]").innerHTML = policy ? `<details><summary>선택한 정책 조건 확인</summary>${rulesHTML(policy.payload)}</details>` : ""; paintSelection(); }
  }, { signal: runtime.life.signal });
  root.addEventListener("keydown", async (event) => {
    const dayButton = event.target.closest("[data-rv-day]"); if (!dayButton || !runtime.canAct()) return;
    const step = { ArrowLeft: -1, ArrowRight: 1, ArrowUp: -7, ArrowDown: 7 }[event.key];
    if (step) { event.preventDefault(); day = nextDay(dayButton.dataset.rvDay, step); page = 1; drawMonth(day); await readSlots(); }
  }, { signal: runtime.life.signal });
  root.addEventListener("submit", async (event) => {
    const form = event.target; if (!form.matches("[data-rv-pick]")) return; event.preventDefault();
    if (!runtime.canAct() || !form.reportValidity() || !selected || !policy) return;
    try {
      const parent = value.parent?.reservation, fields = new FormData(form);
      const body = { ...selectionBody(value.product, selected), productId: value.product.id, policyId: policy.id, reason: value.mode === "change" ? String(fields.get("reason")) : "회원이 시간과 조건을 검토하고 임시 확보 요청" };
      if (value.mode === "change") {
        const s = parent.sessions.find((s) => s.ordinal === value.ordinal);
        await runtime.session.send("/api/coaching/reservations/" + parent.id + "/reschedule", { ...body, version: parent.version, sessionOrdinal: s.ordinal, sessionVersion: s.version }, { entityType: "reservation", entityId: parent.id, productId: parent.productId, memberId: parent.memberId, command: "reservation.reschedule:" + parent.id });
      } else {
        if (parent) Object.assign(body, { reservationId: parent.id, sessionOrdinal: value.ordinal, reservationVersion: parent.version });
        await runtime.session.send("/api/coaching/holds", body, { entityType: "hold", productId: value.product.id, memberId: ctx.user.id, command: "hold.create" });
      }
    } catch (e) { runtime.error(e); }
  }, { signal: runtime.life.signal });
  drawPolicies(); drawMonth(); void readSlots();
  return runtime;
}
