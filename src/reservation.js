import { esc } from "./ui.js";
import { authGate, head } from "./platform-ui.js";
import { productPattern, isCoach, checkedProduct } from "./coaching-model.js";
import { ids, checkedDetail, checkedPolicies, checkedList, checkedPolicy, apiPath, userError } from "./reservation-model.js";
import { pageHTML, listingHTML, holdHTML, reservationHTML, calendarHTML, policyHTML, policiesHTML, when } from "./reservation-view.js";
import { loadReservationCalendar, mountReservationCalendar } from "./reservation-calendar.js";
import { mountReservationDetail } from "./reservation-detail.js";
import { mountReservationRuntime } from "./reservation-runtime.js";

const unavailable = (e) => ({ html: `<section class="container p-page rv-page">${head("RESERVATIONS", "현재 예약 내용을 확인해주세요.", userError(e))}<div class="rv-recovery-actions"><a class="btn btn-outline" href="#/reservations">내 예약으로</a><a class="btn btn-ghost" href="#/coaching/products">코칭 상품 보기</a></div></section>` });
const getRoot = () => document.querySelector("[data-rv-root]");
export async function renderReservations(path, params, ctx) {
  if (!(path === "/reservations" || path.startsWith("/reservations/") || path.startsWith("/coaching/reserve/") || path === "/studio/reservations" || path.startsWith("/manage/reservations/"))) return null;
  if (!ctx.user) return authGate(ctx, "로그인하고 나의 코칭 일정을 확인하세요.");
  const admin = ctx.user.role === "admin", desk = path.startsWith("/studio/") || path.startsWith("/manage/");
  if (desk && (!isCoach(ctx.user) || path.startsWith("/manage/") && !admin)) return unavailable({ status: 403 });
  const read = (url) => ctx.api(url, { ownerId: ctx.user.id });
  try {
    if (["/reservations", "/reservations/holds", "/studio/reservations", "/manage/reservations/holds"].includes(path)) {
      const kind = path.endsWith("/holds") ? "hold" : "reservation", query = new URLSearchParams();
      for (const k of ["state", "paymentState", "page"]) if (params.has(k) && params.get(k)) query.set(k, params.get(k));
      const page = Number(query.get("page") || 1); if (!Number.isInteger(page) || page < 1 || page > 10000) throw { status: 400, message: "페이지를 확인해주세요." };
      const endpoint = path === "/studio/reservations" ? "/api/studio/coaching/reservations" : path === "/manage/reservations/holds" ? "/api/manage/coaching/holds" : "/api/coaching/" + (kind === "hold" ? "holds" : "reservations");
      const queue = desk ? admin ? "admin" : "assigned" : "own";
      const load = async () => checkedList(await read(endpoint + "?" + query), kind, page, queue), data = await load();
      return { html: pageHTML(desk ? kind === "hold" ? "임시 확보 관리" : "예약 처리 데스크" : kind === "hold" ? "임시 확보 내역" : "나의 코칭 일정", desk ? "현재 담당 범위의 요청과 진행 상태를 확인하고 필요한 회차를 처리하세요." : "다음 세션부터 지난 진행 기록까지, 나의 코칭을 한곳에서 이어가세요.", listingHTML(data, kind, path, query, desk && admin), ctx, desk), bind() {
        const root = getRoot(); const rt = mountReservationRuntime(root, ctx, { initial: data, validate: (v) => checkedList(v, kind, page, queue), refresh: load, onValue(v) { rt.area.innerHTML = listingHTML(v, kind, path, query, desk && admin); } });
        root.addEventListener("submit", (e) => { const form = e.target; if (!form.matches("[data-rv-filter]")) return; e.preventDefault(); if (!rt.canAct()) return; const q = new URLSearchParams(); for (const [k, v] of new FormData(form)) if (v) q.set(k, v); ctx.navigate(path + "?" + q); }, { signal: rt.life.signal });
      } };
    }
    let match = path.match(/^\/reservations\/(holds\/)?([^/]+)$/);
    if (match) {
      const kind = match[1] ? "hold" : "reservation", id = match[2]; if (!ids[kind].test(id)) return unavailable({ status: 404 });
      const value = checkedDetail(await read(apiPath(kind, id)), kind, { id });
      return { html: pageHTML(kind === "hold" ? "시간 확보 · 예약 요청" : "예약 진행 상세", kind === "hold" ? "확보한 시간과 제공 조건을 확인한 뒤 코치에게 요청하세요." : "약속한 조건을 유지하며 회차별 일정과 진행 내용을 확인하세요.", kind === "hold" ? holdHTML(value) : reservationHTML(value), ctx), bind() { mountReservationDetail(getRoot(), value, kind, ctx); } };
    }
    match = path.match(/^\/coaching\/reserve\/([^/]+)$/);
    let initial;
    if (match) { if (!productPattern.test(match[1])) return unavailable({ status: 404 }); initial = await loadReservationCalendar(ctx, match[1]); }
    else {
      match = path.match(/^\/reservations\/([^/]+)\/schedule\/([1-9][0-9]?)$/);
      if (match && ids.reservation.test(match[1])) {
        const r = checkedDetail(await read(apiPath("reservation", match[1])), "reservation", { id: match[1] });
        const mode = params.get("mode") || null; if (mode && mode !== "change") throw { status: 400, message: "일정 선택 방식을 확인해주세요." };
        initial = await loadReservationCalendar(ctx, r.reservation.productId, match[1], Number(match[2]), mode);
      }
    }
    if (initial) return { html: pageHTML(initial.mode === "change" ? "코칭 일정 변경" : "코칭 예약 달력", "조회할 날짜와 표시 시간대를 선택하고, 가능한 시간의 조건을 확인하세요.", calendarHTML(initial.product, initial.parent, initial.ordinal, initial.mode), ctx), bind() { mountReservationCalendar(getRoot(), initial, ctx); } };
    match = path.match(/^\/manage\/reservations\/policies\/([^/]+)$/);
    if (match && admin && productPattern.test(match[1])) {
      const pid = match[1], load = async () => { const product = checkedProduct((await read("/api/coaching/products/" + pid)).product, pid); return { product, policies: checkedPolicies(await read("/api/coaching/products/" + pid + "/reservation-policies"), product) }; }, initial = await load();
      return { html: pageHTML("예약 기술 정책 검토", "현재 공개 상품의 조건과 단계별 기한을 명시적으로 확인하고 불변 정책을 남기세요.", policyHTML(initial.product, initial.policies), ctx, true), bind() { mountReservationPolicies(getRoot(), initial, load, ctx); } };
    }
    return unavailable({ status: 404 });
  } catch (e) { return unavailable(e); }
}
export function mountReservationPolicies(root, initial, load, ctx) {
  let value = initial, pageToken = 0;
  const rt = mountReservationRuntime(root, ctx, { initial, validate: (v) => v, refresh: load, onValue(v) {
    ++pageToken;
    if (v.policy) { checkedDetail(v, "policy", { productId: value.product.id }); void rt.session.refresh(load); return; }
    value = v; rt.area.innerHTML = policyHTML(v.product, v.policies); drawPages();
  } });
  rt.life.signal.addEventListener("abort", () => { value = null; ++pageToken; }, { once: true });
  function drawPages() {
    const v = value.policies, n = v.page;
    root.querySelector("[data-rv-policy-pages]").innerHTML = v.total > 20 ? `<nav class="p-pagination" aria-label="정책 페이지"><button class="btn btn-outline btn-sm" type="button" data-rv-policy-page="${n - 1}" ${n === 1 ? "disabled" : ""}>이전 정책</button><span>${n} / ${Math.ceil(v.total / 20)}</span><button class="btn btn-outline btn-sm" type="button" data-rv-policy-page="${n + 1}" ${n * 20 >= v.total ? "disabled" : ""}>다음 정책</button></nav>` : "";
  }
  root.addEventListener("click", async (e) => {
    const b = e.target.closest("button"); if (!b || b.disabled || !rt.canAct()) return;
    try {
      if (b.hasAttribute("data-rv-policy-page")) {
        const n = Number(b.dataset.rvPolicyPage), token = ++pageToken; if (n < 1) return;
        const v = checkedPolicies(await ctx.api("/api/coaching/products/" + value.product.id + "/reservation-policies?page=" + n, { ownerId: ctx.user.id, signal: rt.life.signal }), value.product, n);
        if (!rt.life.active() || token !== pageToken) return; value = { ...value, policies: v }; root.querySelector("[data-rv-policy-list]").innerHTML = policiesHTML(v); root.querySelector("[data-rv-policy-raw]").replaceChildren(); drawPages();
      } else if (b.hasAttribute("data-rv-policy-raw")) {
        const id = b.dataset.rvPolicyRaw, token = ++pageToken; if (!ids.policy.test(id)) return;
        const v = checkedDetail(await ctx.api("/api/manage/coaching/reservation-policies/" + id, { ownerId: ctx.user.id, signal: rt.life.signal }), "policy", { id, productId: value.product.id });
        if (!rt.life.active() || token !== pageToken) return; root.querySelector("[data-rv-policy-raw]").innerHTML = `<div class="rv-notice"><h3>관리자 검수 참조</h3><p>${esc(v.policy.approvalReference)}</p><small>${esc(when(v.policy.createdAt))}</small></div>`;
      }
    } catch (err) { if (rt.life.active()) rt.error(err); }
  }, { signal: rt.life.signal });
  root.addEventListener("submit", async (e) => {
    const form = e.target; if (!form.matches("[data-rv-policy]")) return; e.preventDefault(); if (!rt.canAct() || !form.reportValidity()) return;
    try {
      const f = Object.fromEntries(new FormData(form)), payload = {};
      for (const k of ["holdSeconds", "coachSeconds", "paymentSeconds", "rescheduleBeforeHours", "cancelBeforeHours"]) payload[k] = Number(f[k]);
      for (const k of ["noShowConsumes", "refundOnCancellation", "rescheduleRequiresCoach"]) { if (!["true", "false"].includes(f[k])) throw new Error("각 정책을 명시적으로 선택해주세요."); payload[k] = f[k] === "true"; }
      Object.assign(payload, { validityAnchor: "confirmed_at", revisionRule: "revalidate_until_confirmation" }); checkedPolicy(payload);
      const pid = value.product.id;
      await rt.session.send("/api/manage/coaching/products/" + pid + "/reservation-policies", { productVersion: value.product.version, payload, approvalReference: f.approvalReference, reason: f.reason }, { entityType: "policy", productId: pid, command: "policy.publish" });
      if (rt.canAct() && rt.session.state.value?.policy) await rt.session.refresh(load);
    } catch (err) { rt.error(err); }
  }, { signal: rt.life.signal });
  drawPages(); return rt;
}
