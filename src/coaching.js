import { esc, money } from "./ui.js";
import { authGate, head, empty, opsNav, date } from "./platform-ui.js";
import { studioBase, productPattern, coachPattern, isCoach, checkedDetail, checkedProduct, checkedSlots, slotTimeLabel, rootOf, openRevision, selectionBody, coachingError } from "./coaching-model.js";
import { createCoachingSession } from "./coaching-session.js";
import { readCoachingForm, weeklyRow, offRow } from "./coaching-forms.js";
import { catalogHTML, productHTML, editorHTML, editorBody, generateHTML, payloadSummary, badge, pageLinks } from "./coaching-view.js";

const publicRoot = "/coaching/products", privateRoot = "/studio/coaching";
const unavailable = (e) => ({ html: `<section class="container p-page">${head("COACHING", "현재 내용을 다시 확인해주세요.", coachingError(e))}<a class="btn btn-outline" href="#/coaching/products">공개 상품으로</a></section>` });
export async function renderCoaching(path, params, ctx) {
  if (path !== publicRoot && !path.startsWith(publicRoot + "/") && path !== privateRoot && !path.startsWith(privateRoot + "/")) return null;
  const priv = path.startsWith(privateRoot);
  if (priv && !ctx.user) return authGate(ctx, "코치 또는 관리자 계정으로 열어주세요.");
  if (priv && !isCoach(ctx.user)) return unavailable({ status: 403 });
  const ownerId = ctx.user?.id, api = (url) => ctx.api(url, priv ? { ownerId } : {});
  try {
    if (path === publicRoot) {
      const query = new URLSearchParams(); for (const k of ["q", "coachId", "page"]) if (params.has(k)) query.set(k, params.get(k));
      const data = await api("/api/coaching/products?" + query); data.products.forEach((p) => checkedProduct(p));
      return { html: catalogHTML(data, query), bind() { const form = document.querySelector("[data-co-search]"); form.onsubmit = (e) => { e.preventDefault(); if (form.reportValidity()) ctx.navigate(publicRoot + "?" + new URLSearchParams(new FormData(form))); }; } };
    }
    if (!priv) {
      const id = path.slice(publicRoot.length + 1); if (!productPattern.test(id)) return unavailable({ status: 404 });
      const p = checkedProduct((await api("/api/coaching/products/" + id)).product, id);
      return { html: productHTML(p), bind() { mountCoachingSelection(document.querySelector("[data-co-public]"), p, ctx); } };
    }
    if (path === privateRoot) return await studioList(params, ctx);
    const match = path.match(/^\/studio\/coaching\/(products|calendars)\/([^/]+)$/); if (!match) return unavailable({ status: 404 });
    const kind = match[1] === "products" ? "product" : "calendar", create = kind === "product" && match[2] === "new", id = create ? params.get("coach") : match[2];
    if (!(kind === "calendar" || create ? coachPattern : productPattern).test(id || "")) return unavailable({ status: 404 });
    const base = studioBase + (create ? "/calendars/" + id : "/" + match[1] + "/" + id);
    let value = checkedDetail(await api(base), create ? "calendar" : kind, id);
    if (create) value = { product: null, context: value.context, revisions: [], events: [] };
    return { html: editorHTML(value, kind, create, ctx), bind() { mountCoachingEditor(document.querySelector("[data-co-editor]"), value, { kind, create, base, id }, ctx); } };
  } catch (error) { return unavailable(error); }
}
async function studioList(params, ctx) {
  const query = new URLSearchParams(); for (const k of ["coachId", "page"]) if (params.has(k)) query.set(k, params.get(k));
  const [data, profiles] = await Promise.all([ctx.api(studioBase + "/products?" + query, { ownerId: ctx.user.id }), ctx.api("/api/studio/profiles", { ownerId: ctx.user.id })]);
  const docs = await Promise.all(data.products.map((p) => ctx.api(studioBase + "/products/" + p.id, { ownerId: ctx.user.id })));
  return { html: `<section class="container p-page co-page">${head("COACHING STUDIO", "코칭 상품과 일정", "제공 범위, 가격, 수행 조건과 가능 시간을 독립된 변경안으로 관리하세요.")}${opsNav(ctx, "coaching")}<section class="p-panel"><h2>코치별 작성 공간</h2><div class="co-coach-grid">${profiles.profiles.map((p) => `<article><strong>${esc(p.publishedPayload?.name || p.id)}</strong><span class="p-muted">${p.publiclyAvailable ? "공개 소개 있음" : "소개 공개 필요"}</span><div><a class="btn btn-outline btn-sm" href="#/studio/coaching/products/new?coach=${esc(p.id)}">상품 만들기</a><a class="btn btn-ghost btn-sm" href="#/studio/coaching/calendars/${esc(p.id)}">가용 일정</a></div></article>`).join("") || empty("담당 코치 소개가 없습니다.", "관리자가 먼저 실제 담당자와 공개 소개를 연결해야 합니다.")}</div></section><div class="co-product-grid">${docs.map((v) => { const p = v.product, rev = openRevision(v) || v.revisions[0]; return `<article class="co-product-card"><div class="co-card-top">${badge(p.state)}${rev ? badge(rev.status) : ""}</div><h2>${esc(rev?.payload?.title || "상품")}</h2><p>${esc(p.coachId)} · 공개 ${p.publicVersion}판</p><p>${rev ? `${money(rev.payload.price)} · ${rev.payload.sessionCount}회 × ${rev.payload.durationMinutes}분` : ""}</p><a class="btn btn-outline" href="#/studio/coaching/products/${esc(p.id)}">편집·검수 열기</a></article>`; }).join("") || empty("첫 상품을 설계해보세요.", "코치를 선택해 초안을 작성하면 검수 후 공개할 수 있습니다.")}</div>${pageLinks(privateRoot, query, data)}</section>` };
}
function scopeLife(root, ctx, privatePage, deny) {
  const route = location.hash, ownerId = ctx.user?.id, abort = new AbortController();
  let disposed = false, observer;
  const active = () => !disposed && root.isConnected && location.hash === route && ctx.user?.id === ownerId && (!privatePage || isCoach(ctx.user));
  function dispose() { if (disposed) return; disposed = true; abort.abort(); observer?.disconnect(); root.replaceChildren(); deny?.(); }
  window.addEventListener("hashchange", dispose, { signal: abort.signal, once: true });
  observer = new MutationObserver(() => { if (!root.isConnected) dispose(); }); observer.observe(root.ownerDocument.body, { childList: true, subtree: true });
  return { active, dispose, signal: abort.signal };
}
const safeStorage = () => { try { return window.sessionStorage; } catch { return null; } };
export function mountCoachingEditor(root, initial, options, ctx) {
  const { kind, create, base, id } = options, ownerId = ctx.user.id, admin = ctx.user.role === "admin";
  const area = root.querySelector("[data-co-editor-body]"), feedback = root.querySelector("[data-co-feedback]"), recovery = root.querySelector("[data-co-recovery]");
  const life = scopeLife(root, ctx, true, () => session?.dispose());
  let calendar = null, calendarRead = 0, painted = initial, historyPage = 0, historyBusy = false, wasBusy = false;
  const commandAPI = async (path, opts) => {
    const value = await ctx.api(path, opts);
    return create && path === base && !opts.method ? { product: null, context: value.context, revisions: [], events: [] } : value;
  };
  const session = createCoachingSession({ api: commandAPI, ownerId, scope: kind + ":" + id + (create ? ":new" : ""), initial, active: life.active, storage: safeStorage(),
    denied(error) { area.replaceChildren(); recovery.replaceChildren(); root.querySelector("[data-co-generate]")?.replaceChildren(); feedback.textContent = coachingError(error); root.querySelector("[data-co-refresh]").disabled = true; },
    changed(s) {
      if (!life.active() || !s.value) return;
      if (create && s.value.product?.id) { ctx.navigate(privateRoot + "/products/" + s.value.product.id); return; }
      if (painted !== s.value) { area.innerHTML = editorBody(s.value, kind, create, admin); painted = s.value; historyPage = 0; void readCalendar(); }
      feedback.textContent = s.message; feedback.classList.toggle("is-error", s.error);
      if (wasBusy && !s.busy) feedback.focus?.({ preventScroll: true });
      wasBusy = s.busy;
      recovery.innerHTML = s.pending ? `<section class="co-recovery"><strong>저장 결과 확인이 필요합니다.</strong><p>이 계정의 원 요청 키: ${esc(s.pending.requestKey)}</p><button data-co-lookup class="btn btn-outline btn-sm" ${s.busy ? "disabled" : ""}>원 요청 결과 조회</button>${s.notFound ? `${session.canRetry() ? '<button data-co-retry class="btn btn-outline btn-sm">같은 원 요청 직접 재전송</button>' : ""}<button data-co-release class="btn btn-ghost btn-sm">미기록 요청 정리</button>` : ""}</section>` : "";
      root.querySelectorAll("form button, form input, form select, form textarea").forEach((e) => { e.disabled = Boolean(e.closest("fieldset[disabled]") || s.busy || s.pending || s.needsRefresh); });
      root.querySelector("[data-co-refresh]").disabled = Boolean(s.busy || s.pending);
      if (s.receipt && kind === "product") {
        const result = root.querySelector("[data-co-generation-result]");
        if (result && s.receipt.command === "product.generate") generationResult(result, s.receipt.result);
      }
    } });
  async function readCalendar() {
    if (kind !== "product" || create || !life.active()) return;
    const target = root.querySelector("[data-co-generate]"), token = ++calendarRead;
    calendar = null; target.textContent = "현재 공개 일정 기준을 조회하고 있습니다…";
    try { const v = await ctx.api(studioBase + "/calendars/" + initial.context.coachId, { ownerId }); if (!life.active() || token !== calendarRead) return; calendar = checkedDetail(v, "calendar", initial.context.coachId); target.innerHTML = generateHTML(session.state.value, calendar); const result = target.querySelector("[data-co-generation-result]"); if (result && session.state.receipt?.command === "product.generate") generationResult(result, session.state.receipt.result); sessionPaint(); }
    catch (e) { if (life.active() && token === calendarRead) { if (privateDenied(e)) return; target.textContent = coachingError(e); } }
  }
  function sessionPaint() {
    root.querySelectorAll("form button, form input, form select, form textarea").forEach((e) => { e.disabled = Boolean(e.closest("fieldset[disabled]") || session.state.busy || session.state.pending || session.state.needsRefresh); });
  }
  function privateDenied(error) {
    if (!error?.sessionChanged && ![401, 403, 404].includes(error?.status)) return false;
    session.dispose(); area.replaceChildren(); recovery.replaceChildren(); root.querySelector("[data-co-generate]")?.replaceChildren(); feedback.textContent = coachingError(error); root.querySelector("[data-co-refresh]").disabled = true; return true;
  }
  root.addEventListener("submit", async (event) => {
    const form = event.target; event.preventDefault(); if (!life.active() || session.state.busy || session.state.pending || session.state.needsRefresh || !form.reportValidity()) return;
    const value = session.state.value, r = rootOf(value), rev = openRevision(value), fields = Object.fromEntries(new FormData(form));
    let path, method = "POST", body = { version: r?.version || 0, reason: fields.reason };
    try {
      if (form.hasAttribute("data-co-edit")) {
        body.payload = readCoachingForm(form, kind); body.profileVersion = value.context.profileVersion;
        if (create) { path = studioBase + "/products"; delete body.version; body.coachId = value.context.coachId; }
        else if (rev) { path = base + "/revisions/" + rev.id; method = "PATCH"; body.revisionVersion = rev.version; }
        else path = base + "/revisions";
      } else if (form.hasAttribute("data-co-command")) {
        const action = form.dataset.coCommand;
        if (["pause", "resume"].includes(action)) { path = base + "/state"; body.state = action === "pause" ? "paused" : "active"; }
        else { if (!rev) return; path = base + "/revisions/" + rev.id; body.revisionVersion = rev.version; body.action = action; if (action === "approve") body.approvalReference = fields.approvalReference; if (["approve", "reject"].includes(action)) body.reviewNote = fields.reviewNote; }
      } else if (form.hasAttribute("data-co-generate-form")) {
        if (!calendar?.calendar) return; path = base + "/generate"; body = { ...body, publicVersion: r.publicVersion, calendarVersion: calendar.calendar.publicVersion, fromDate: fields.fromDate, toDate: fields.toDate };
      } else return;
      await session.send(path, method, body);
    } catch (e) { if (life.active()) feedback.textContent = coachingError(e); }
  }, { signal: life.signal });
  root.addEventListener("click", async (event) => {
    const b = event.target.closest("button"); if (!b || b.disabled || !life.active()) return;
    if (b.hasAttribute("data-co-refresh")) return session.refresh(base);
    if (b.hasAttribute("data-co-lookup")) return session.lookup();
    if (b.hasAttribute("data-co-retry")) return session.retry();
    if (b.hasAttribute("data-co-release")) return session.release();
    if (session.state.busy || session.state.pending || session.state.needsRefresh) return;
    if (b.hasAttribute("data-add-week")) { const rows = area.querySelector("[data-week-rows]"); if (rows.children.length < 28) rows.insertAdjacentHTML("beforeend", weeklyRow()); }
    if (b.hasAttribute("data-add-off")) { const rows = area.querySelector("[data-off-rows]"); if (rows.children.length < 100) rows.insertAdjacentHTML("beforeend", offRow()); }
    if (b.hasAttribute("data-remove-row")) b.closest("[data-week-row],[data-off-row]").remove();
    if (b.hasAttribute("data-co-history") && !create && !historyBusy) {
      historyBusy = true; b.disabled = true;
      try { const data = await ctx.api(base + "/revisions?page=" + (historyPage + 1), { ownerId }); if (!life.active()) return; historyPage = data.page; const list = area.querySelector("[data-co-history-list]"); list.insertAdjacentHTML("beforeend", data.revisions.map((r) => `<details class="co-history"><summary>${badge(r.status)} · ${date(r.updatedAt)}</summary>${payloadSummary(r.payload, kind)}<p>${esc(r.reason)}</p><p>${esc(r.reviewNote)}</p></details>`).join("")); b.hidden = data.page * data.pageSize >= data.total; }
      catch (e) { if (life.active() && !privateDenied(e)) feedback.textContent = coachingError(e); } finally { historyBusy = false; if (b.isConnected) b.disabled = false; }
    }
  }, { signal: life.signal });
  // Paint a restored pending key without submitting anything.
  feedback.textContent = session.state.message;
  if (session.state.pending) recovery.innerHTML = '<button data-co-lookup class="btn btn-outline">원 요청 결과 조회</button>';
  sessionPaint(); root.querySelector("[data-co-refresh]").disabled = Boolean(session.state.pending); void readCalendar();
  return session;
}
function generationResult(target, result) {
  target.textContent = `신규 ${result.createdCount}개 · 기존 ${result.existingCount}개 · DST 중복/누락 제외 ${result.omittedLocalTimes?.length || 0}건. 후보 생성 결과이며 예약은 발생하지 않았습니다.`;
}

export function mountCoachingSelection(root, product, ctx) {
  const form = root.querySelector("[data-co-slot-filter]"), list = root.querySelector("[data-co-slots]"), feedback = root.querySelector("[data-co-selection]");
  const life = scopeLife(root, ctx, false), ownerId = ctx.user?.id;
  root.querySelector("[data-co-scroll]")?.addEventListener("click", () => { root.querySelector("#co-times").scrollIntoView({ behavior: "smooth", block: "start" }); form.querySelector("select").focus({ preventScroll: true }); }, { signal: life.signal });
  let sequence = 0, slots = [], selected = null, busy = false, page = 1, total = 0, submittedQuery = null, checking = false;
  const reset = () => { selected = null; feedback.replaceChildren(); checking = false; };
  function invalidate(error) {
    slots = []; selected = null;
    list.innerHTML = '<a class="btn btn-outline" href="#/coaching/products">상품 조건 다시 확인</a>';
    feedback.textContent = coachingError(error);
  }
  function paintSlots() {
    list.innerHTML = slots.length ? `<fieldset class="co-slot-options"><legend>후보 시간을 선택하세요</legend>${slots.map((s) => `<label class="co-slot"><input type="radio" name="co-slot" value="${esc(s.id)}"><span><strong>${esc(slotTimeLabel(s))}</strong><small>${esc(s.displayTimeZone)}<br>코치 기준 ${esc(s.originalStartsAt)} → ${esc(s.originalEndsAt)} · ${esc(s.originalTimeZone)}</small></span></label>`).join("")}</fieldset><div class="co-pagination"><button type="button" data-co-prev class="btn btn-ghost btn-sm" ${page <= 1 ? "disabled" : ""}>이전 후보</button><span>${page}페이지 · ${total}개</span><button type="button" data-co-next class="btn btn-ghost btn-sm" ${page * 50 >= total ? "disabled" : ""}>다음 후보</button></div><button type="button" data-co-check class="btn btn-primary" disabled>선택한 시간 현재 조건 확인</button>` : '<p class="co-empty">조회 기간에 공개된 후보 시간이 없습니다. 다른 기간을 조회하거나 코치 소개에서 상담 문의를 이용해주세요.</p>';
  }
  async function load(query, nextPage = 1) {
    if (!life.active()) return; const token = ++sequence; reset(); slots = []; submittedQuery = query; page = nextPage; busy = true; list.textContent = "현재 공개 후보를 조회하고 있습니다…";
    try {
      const data = await ctx.api("/api/coaching/slots?" + new URLSearchParams({ ...query, productId: product.id, page: nextPage, pageSize: 50 }));
      if (!life.active() || token !== sequence) return;
      checkedSlots(data, product, nextPage);
      slots = data.slots; total = data.total; paintSlots();
    } catch (e) { if (life.active() && token === sequence) { if ([401, 403, 404, 409].includes(e?.status) || e?.sessionChanged) invalidate(e); else { list.replaceChildren(); feedback.textContent = coachingError(e); } } }
    finally { if (token === sequence) busy = false; }
  }
  form.addEventListener("submit", (e) => { e.preventDefault(); if (form.reportValidity()) void load(Object.fromEntries(new FormData(form))); }, { signal: life.signal });
  form.addEventListener("input", () => { ++sequence; reset(); slots = []; list.replaceChildren(); busy = false; }, { signal: life.signal });
  list.addEventListener("change", (e) => { if (e.target.name !== "co-slot" || !life.active()) return; ++sequence; reset(); selected = slots.find((s) => s.id === e.target.value) || null; list.querySelector("[data-co-check]").disabled = !selected; }, { signal: life.signal });
  list.addEventListener("click", async (e) => {
    const b = e.target.closest("button"); if (!b || b.disabled || busy || !life.active()) return;
    if (b.hasAttribute("data-co-prev")) return load(submittedQuery, page - 1);
    if (b.hasAttribute("data-co-next")) return load(submittedQuery, page + 1);
    if (!b.hasAttribute("data-co-check") || !selected || checking) return;
    if (!ownerId) { ctx.requireAuth(); return; }
    const token = ++sequence, candidate = selected; checking = true; b.disabled = true; feedback.textContent = "현재 상품·일정 조건을 다시 확인하고 있습니다…";
    try {
      const body = selectionBody(product, candidate), result = await ctx.api("/api/coaching/products/" + product.id + "/selection", { method: "POST", ownerId, body });
      if (!life.active() || token !== sequence) return;
      const s = result.selection;
      if (!s || s.kind !== "candidate_validation" || s.reserved !== false || s.paymentStarted !== false || s.revalidationRequired !== true || s.slot?.id !== candidate.id || s.product?.id !== product.id || s.productVersion !== product.version || s.calendarVersion !== product.availability.calendarVersion || s.slot.startsAt !== candidate.startsAt || s.slot.endsAt !== candidate.endsAt) throw new Error("현재 선택 확인 결과가 다릅니다. 다시 조회해주세요.");
      checkedProduct(s.product, product.id); selectionBody(s.product, s.slot);
      feedback.innerHTML = `<div class="co-selection-receipt"><span class="eyebrow">CURRENT CONDITIONS CHECKED</span><h3>현재 조건을 확인했습니다</h3><p>${esc(s.product.title)} · ${money(s.product.price)}</p><strong>${esc(candidate.displayStartsAt)} → ${esc(candidate.displayEndsAt)}</strong><p>${esc(candidate.displayTimeZone)} · 확인 ${date(s.checkedAt)}</p><p>예약과 결제가 발생하지 않았습니다. 이후 예약 기능에서 조건을 다시 확인해야 합니다.</p><a class="btn btn-outline btn-sm" href="#/coaching?coach=${esc(product.coachId)}">코치에게 상담 문의</a></div>`;
      feedback.focus?.({ preventScroll: true });
    } catch (e) { if (life.active() && token === sequence) { if ([401, 403, 404, 409].includes(e?.status) || e?.sessionChanged) invalidate(e); else feedback.textContent = coachingError(e); } }
    finally { if (life.active() && token === sequence) { checking = false; if (b.isConnected) b.disabled = false; } }
  }, { signal: life.signal });
  void load(Object.fromEntries(new FormData(form)));
  return { dispose: life.dispose, load };
}
