import { esc, money } from "./ui.js";
import { toSeoulInput, fromSeoulInput } from "./access.js";
import {
  head,
  empty,
  authGate,
  bindForm,
  textBody,
  date,
  tag,
  opsNav,
  pager,
} from "./platform-ui.js";
import { policyKinds, policyTiming, consentPayload } from "./policy-model.js";

const modeLabel = (mode) => (mode === "test" ? "테스트 환경" : "상용 환경");
const versionLabel = (p) =>
  p.current === false && p.timing === "effective"
    ? "이전 버전 · 유효기간 내"
    : policyTiming[p.timing] || p.timing;
const timeLabel = (p) =>
  `${p.effectiveAt ? date(p.effectiveAt) : "발행 시 시행"} → ${p.expiresAt ? date(p.expiresAt) : "만료 지정 없음"}`;
const priceAnchor = (p) =>
  p?.priceVersionId
    ? `<p class="p-notice">연결 가격 ${p.priceAmount === 0 ? "무료 · 0원" : money(p.priceAmount)} · 이 문서에 고정된 가격 버전</p>`
    : "";
export function policyBlockers(bundle) {
  return bundle.ready
    ? `<div class="p-notice">필수 문서의 적용 버전을 확인했습니다.</div>`
    : `<div class="p-notice amber"><strong>제공 조건 확인이 필요합니다.</strong><ul>${bundle.blockers.map((b) => `<li>${esc(b.title)} · ${esc(b.message)}</li>`).join("")}</ul></div>`;
}
function documentCard(p, { consent = false, body = false } = {}) {
  return `<article class="p-policy-document"><div class="p-inline-heading"><h3>${esc(p.title)}</h3>${tag(versionLabel(p), p.timing === "effective" ? "mint" : "amber")}</div><p class="p-muted">${esc(policyKinds[p.kind])} · ${modeLabel(p.mode)} · 문서 ${p.edition}판${p.courseVersion ? ` · 상품 v${p.courseVersion}` : ""}</p><p>${esc(p.summary)}</p><small class="p-muted">${esc(timeLabel(p))}</small>${priceAnchor(p)}${body ? `<div class="p-policy-body">${textBody(p.body)}</div>` : `<a class="text-link" href="#/policies/${esc(p.id)}" target="_blank" rel="noopener">원문 새 탭에서 읽기 ↗</a>`}${consent ? `<label class="p-policy-consent"><input type="checkbox" name="consent_${esc(p.id)}" value="${esc(p.id)}" data-policy-choice/><span>${esc(policyKinds[p.kind])} ${p.edition}판 원문을 확인하고 동의합니다.</span></label>` : ""}</article>`;
}
export function consentChecklist(bundle) {
  return `<div class="p-policy-checklist"><h2>구매 조건 확인</h2><p class="p-muted">${modeLabel(bundle.mode)} · 각 문서를 확인한 후 직접 체크해주세요. 문서나 상품이 바뀌면 다시 확인합니다.</p>${policyBlockers(bundle)}${bundle.documents.map((p) => documentCard(p, { consent: bundle.ready })).join("")}</div>`;
}
export const checkedPolicies = (root) =>
  [...root.querySelectorAll("[data-policy-choice]:checked")].map(
    (input) => input.value,
  );

export async function renderPolicies(path, params, ctx) {
  if (path === "/policies/consents") return consentHistory(params, ctx);
  if (path === "/policies") return publicPolicies(params, ctx);
  if (path.startsWith("/policies/")) return publicDocument(path.slice(10), ctx);
  if (
    (path === "/manage" && params.get("tab") === "policies") ||
    path.startsWith("/manage/policies/")
  ) {
    if (!ctx.user) return authGate(ctx, "정책 관리 계정으로 접속해주세요.");
    if (ctx.user.role !== "admin")
      return {
        html: `<section class="container p-page">${empty("관리자 권한이 필요합니다.", "정책 발행과 권리 자료는 관리자가 관리합니다.")}</section>`,
      };
    if (path.startsWith("/manage/policies/"))
      return editor(path.slice(17), params, ctx);
    return management(params, ctx);
  }
  return null;
}
async function publicPolicies(params, ctx) {
  const mode = params.get("mode") === "test" ? "test" : "live";
  const [current, account] = await Promise.all([
    ctx.api(`/api/policies/current?mode=${mode}`),
    ctx.api(`/api/policies/current?mode=${mode}&context=account`),
  ]);
  const bundle = account.policies;
  return {
    html: `<section class="container p-page p-thread">${head("POLICIES & YOUR RECORDS", "이용 조건을 투명하게.", "적용 버전과 과거 동의 기록을 확인할 수 있습니다.")}<nav class="p-tabs"><a class="${mode === "live" ? "active" : ""}" href="#/policies">상용 환경</a><a class="${mode === "test" ? "active" : ""}" href="#/policies?mode=test">테스트 환경</a><a href="#/policies/consents">내 동의 기록</a></nav>${mode === "test" ? `<div class="p-notice amber">테스트 환경의 문서는 상용 제공 조건과 구분됩니다.</div>` : ""}${policyBlockers(current.policies)}<div class="p-panel">${current.policies.documents.map((p) => documentCard(p)).join("") || `<p class="p-muted">아직 발행된 문서가 없습니다.</p>`}</div>${ctx.user && bundle.ready ? `<form id="account-consent" class="p-panel"><h2>내 계정에 동의 기록 남기기</h2><p class="p-muted">구매 동의는 주문마다 별도로 확인합니다. 이전 동의는 아래 항목을 자동 선택하지 않습니다.</p>${bundle.documents.map((p) => documentCard(p, { consent: true })).join("")}<p class="form-error" data-error role="alert"></p><button type="submit" class="btn btn-primary">선택한 조건에 동의 기록</button></form>` : ""}</section>`,
    bind() {
      bindForm("#account-consent", async (_, form) => {
        await ctx.api("/api/consents", {
          method: "POST",
          body: {
            mode,
            ...consentPayload(bundle, checkedPolicies(form), "account", mode),
          },
        });
        ctx.toast("확인한 버전과 동의 시각을 기록했습니다.");
        ctx.navigate("/policies/consents");
      });
    },
  };
}
async function publicDocument(id, ctx) {
  const p = (await ctx.api(`/api/policies/${encodeURIComponent(id)}`)).policy;
  return {
    html: `<section class="container p-page p-thread"><a class="back-link" href="#/policies${p.mode === "test" ? "?mode=test" : ""}">← 정책 안내</a>${head(modeLabel(p.mode), p.title, `${policyKinds[p.kind]} · ${p.edition}판`)}<div class="p-panel">${documentCard(p, { body: true })}<details class="p-policy-meta"><summary>버전 확인 정보</summary><p>문서 ID: ${esc(p.id)}</p><p>발행: ${date(p.publishedAt)}</p><p class="p-policy-hash">원문 SHA-256: ${esc(p.contentHash)}</p>${p.withdrawnAt ? `<p>철회: ${date(p.withdrawnAt)} · ${esc(p.withdrawalReason)}</p>` : ""}</details></div></section>`,
  };
}
async function consentHistory(params, ctx) {
  if (!ctx.user) return authGate(ctx, "동의한 계정으로 기록을 확인해주세요.");
  const data = await ctx.api(`/api/consents?${params}`);
  return {
    html: `<section class="container p-page p-thread">${head("YOUR CONSENT HISTORY", "내가 확인한 조건의 기록.", "동의 당시 문서 버전과 원문을 보존합니다.", `<a class="btn btn-outline" href="#/policies">현재 정책</a>`)}<div class="p-panel">${data.consents.map((s) => `<article class="p-policy-document"><h3><a href="#/policies/${esc(s.policyId)}">${esc(s.title)}</a></h3><p>${modeLabel(s.mode)} · ${s.edition}판 · ${s.context === "purchase" ? "구매 동의" : "계정 동의"}</p><p class="p-muted">동의 시각 ${date(s.acceptedAt)}</p>${s.orderId ? `<p class="p-policy-hash">주문 ${esc(s.orderId)}</p>` : ""}</article>`).join("") || empty("동의 기록이 없습니다.", "이전 가입이나 구매에 없던 동의를 소급해서 만들지 않습니다.")}</div>${pager("/policies/consents", params, data.page, data.total)}</section>`,
  };
}

async function management(params, ctx) {
  const mode = params.get("mode") === "test" ? "test" : "live";
  const courses = (await ctx.api("/api/studio/courses")).courses;
  const courseId = params.get("courseId") || "";
  const query = new URLSearchParams({ mode, page: params.get("page") || "1" });
  if (params.get("status")) query.set("status", params.get("status"));
  const [data, readiness] = await Promise.all([
    ctx.api(`/api/manage/policies?${query}`),
    ctx.api(
      `/api/manage/policies/readiness?mode=${mode}&context=${courseId ? "checkout" : "public"}&courseId=${encodeURIComponent(courseId)}`,
    ),
  ]);
  const bundle = readiness.policies;
  return {
    html: `<section class="container p-page">${head("VERSIONED CONDITIONS", "정책과 권리, 적용 범위를 명확하게.", "발행된 원문을 보존하고 새 판매의 준비 상태를 확인합니다.", `<a class="btn btn-primary" href="#/manage/policies/new?mode=${mode}">새 버전 초안</a>`)}${opsNav(ctx, "policies")}<form id="policy-filter" class="p-filter-bar"><label>환경<select name="mode"><option value="live" ${mode === "live" ? "selected" : ""}>상용 환경</option><option value="test" ${mode === "test" ? "selected" : ""}>테스트 환경</option></select></label><label>문서 상태<select name="status"><option value="">전체 상태</option>${["draft", "published", "withdrawn"].map((x) => `<option value="${x}" ${params.get("status") === x ? "selected" : ""}>${{ draft: "초안", published: "발행", withdrawn: "철회" }[x]}</option>`).join("")}</select></label><label>준비 상태를 확인할 상품<select name="courseId"><option value="">공통 정책</option>${courses
      .filter((c) => c.status === "published")
      .map(
        (c) =>
          `<option value="${esc(c.id)}" ${c.id === courseId ? "selected" : ""}>${esc(c.title)}</option>`,
      )
      .join(
        "",
      )}</select></label><button type="submit" class="btn btn-outline btn-sm">조회</button></form><div class="p-policy-management"><section class="p-panel"><div class="p-inline-heading"><h2>버전 목록</h2><span class="p-muted">${data.total}건</span></div>${data.policies.map((p) => `<article class="p-policy-document"><div class="p-inline-heading"><h3><a href="#/manage/policies/${esc(p.id)}">${esc(p.title)}</a></h3>${tag(versionLabel(p), p.timing === "effective" ? "mint" : "amber")}</div><p class="p-muted">${esc(policyKinds[p.kind])} · ${modeLabel(p.mode)} · ${p.edition}판${p.courseId ? ` · ${esc(p.courseId)} / 상품 v${p.courseVersion}` : " · 전체 서비스"}</p><small>${esc(timeLabel(p))}</small><a class="text-link" href="#/manage/policies/${esc(p.id)}">${p.status === "draft" ? "초안 편집·발행" : "원문·변경 이력 확인"} →</a></article>`).join("") || empty("등록된 버전이 없습니다.", "승인받은 문서와 근거를 준비해 새 초안으로 등록하세요.")}${pager("/manage", params, data.page, data.total)}</section><aside class="p-panel p-policy-readiness"><p class="eyebrow">${modeLabel(mode)}</p><h2>${courseId ? "신규 구매 준비" : "공통 정책 준비"}</h2>${policyBlockers(bundle)}${[...bundle.documents, ...(bundle.rights ? [bundle.rights] : [])].map((p) => `<p><a href="#/manage/policies/${esc(p.id)}">${esc(policyKinds[p.kind])} · ${p.edition}판</a><br><small class="p-muted">${esc(policyTiming[p.timing])}</small></p>`).join("")}<p class="form-note">문서 준비 상태입니다. 실제 판매 승인·PG 상점·수강권·배포 검수는 별도로 확인합니다.</p></aside></div></section>`,
    bind() {
      bindForm("#policy-filter", async (data) =>
        ctx.navigate(
          "/manage?" + new URLSearchParams({ tab: "policies", ...data }),
        ),
      );
    },
  };
}

async function editor(id, params, ctx) {
  const source = id !== "new" ? id : params.get("from");
  const previous = source
    ? (await ctx.api(`/api/policies/${encodeURIComponent(source)}`)).policy
    : null;
  const p =
    id === "new" && previous
      ? {
          ...previous,
          id: null,
          status: "draft",
          effectiveAt: null,
          expiresAt: null,
        }
      : previous;
  const existing = id !== "new",
    editable = !existing || p.status === "draft";
  const courses = (await ctx.api("/api/studio/courses")).courses;
  const mode = p?.mode || (params.get("mode") === "test" ? "test" : "live");
  return {
    html: `<section class="container p-page p-thread"><a class="back-link" href="#/manage?tab=policies&mode=${mode}">← 정책·권리 관리</a>${head("POLICY EDITION", existing ? `${p.edition}판 · ${versionLabel(p)}` : "새 버전 초안", "원문·시행 기간·대상 상품을 확인한 후 별도로 발행합니다.")}${existing && p.status !== "draft" ? `<div class="p-notice">발행된 원문과 구매 당시 참조는 보존됩니다. 변경할 내용은 새 초안에서 작성하세요.</div><a class="btn btn-outline" href="#/manage/policies/new?from=${esc(p.id)}">이 원문에서 새 버전 작성</a>` : ""}<form id="policy-editor" class="p-panel"><div class="p-form-grid"><label class="field">문서 종류<select name="kind" ${existing ? "disabled" : ""}>${Object.entries(
      policyKinds,
    )
      .map(
        ([k, label]) =>
          `<option value="${k}" ${p?.kind === k ? "selected" : ""}>${label}</option>`,
      )
      .join(
        "",
      )}</select></label><label class="field">적용 환경<select name="mode" ${existing ? "disabled" : ""}><option value="live" ${mode === "live" ? "selected" : ""}>상용 환경</option><option value="test" ${mode === "test" ? "selected" : ""}>테스트 환경</option></select></label><label class="field">대상 강의<select name="courseId" ${existing ? "disabled" : ""}><option value="">전역 정책 · 대상 없음</option>${courses.map((c) => `<option value="${esc(c.id)}" ${(p?.courseId || params.get("courseId")) === c.id ? "selected" : ""}>${esc(c.title)} · v${c.version}</option>`).join("")}</select></label></div><p class="form-note">상품 제공 조건·권리 증빙에는 강의를 지정합니다. 나머지 세 문서는 전역 정책입니다. 시간은 한국 시간 기준입니다.</p><div id="policy-price-picker">${priceAnchor(p)}</div><label class="field">제목<input name="title" required minlength="3" maxlength="120" value="${esc(p?.title || "")}" ${editable ? "" : "readonly"}/></label><label class="field">요약<textarea name="summary" maxlength="1000" rows="2" ${editable ? "" : "readonly"}>${esc(p?.summary || "")}</textarea></label><label class="field">원문<textarea name="body" required minlength="20" maxlength="12000" rows="15" ${editable ? "" : "readonly"}>${esc(p?.body || "")}</textarea></label><div class="p-form-grid"><label class="field">시행 시각 · 비우면 발행 즉시<input type="datetime-local" step="1" name="effectiveAt" value="${toSeoulInput(p?.effectiveAt)}" ${editable ? "" : "readonly"}/></label><label class="field">만료 시각 · 선택<input type="datetime-local" step="1" name="expiresAt" value="${toSeoulInput(p?.expiresAt)}" ${editable ? "" : "readonly"}/></label></div>${existing ? `<input type="hidden" name="version" value="${p.version}"/>` : ""}<p class="form-error" data-error role="alert"></p>${editable ? `<button type="submit" class="btn btn-primary">초안 저장</button>` : `<p class="p-policy-hash">원문 SHA-256: ${esc(p.contentHash)}</p>${p.approvalReference ? `<p class="p-policy-hash">승인 근거: ${esc(p.approvalReference)}</p>` : ""}`}</form>${existing && p.status !== "withdrawn" ? `<form id="policy-status" class="p-panel"><h2>${p.status === "draft" ? "검토 후 발행" : "새 구매 적용 철회"}</h2><p class="p-muted">${p.status === "draft" ? "저장된 원문을 고정합니다. 상용 문서는 승인 근거가 필요합니다. 미저장 수정은 먼저 저장해주세요." : "새 구매의 적용을 중지합니다. 이미 동의한 원문과 진행 중 결제의 결과 확인은 보존합니다."}</p><input type="hidden" name="version" value="${p.version}"/>${p.status === "draft" ? `<label class="field">승인 근거 · ${mode === "live" ? "필수" : "선택"}<textarea name="approvalReference" ${mode === "live" ? "required minlength=5" : ""} maxlength="2000" rows="2"></textarea></label>` : ""}<label class="field">${p.status === "draft" ? "발행" : "철회"} 사유<textarea name="reason" required minlength="5" maxlength="2000" rows="2"></textarea></label><p class="form-error" data-error role="alert"></p><button type="submit" class="btn ${p.status === "draft" ? "btn-primary" : "btn-danger"}">${p.status === "draft" ? "저장된 버전 발행" : "적용 철회"}</button></form>` : ""}</section>`,
    bind() {
      const validatePrice = bindPolicyPricePicker(
        document.querySelector("#policy-editor"),
        p,
        courses,
        ctx,
        editable,
      );
      if (editable)
        bindForm("#policy-editor", async (data) => {
          validatePrice();
          const payload = {
            ...data,
            effectiveAt: fromSeoulInput(data.effectiveAt),
            expiresAt: fromSeoulInput(data.expiresAt),
          };
          if (existing) payload.version = p.version;
          const result = await ctx.api(
            existing ? `/api/manage/policies/${p.id}` : "/api/manage/policies",
            { method: existing ? "PATCH" : "POST", body: payload },
          );
          ctx.toast("초안을 저장했습니다. 발행 전 원문과 범위를 확인해주세요.");
          ctx.navigate(`/manage/policies/${result.policy.id}`);
          if (existing) ctx.reload();
        });
      bindForm("#policy-status", async (data) => {
        const editForm = document.querySelector("#policy-editor");
        if (
          p.status === "draft" &&
          ((editForm.elements.priceVersionId?.value || "") !==
            (p.priceVersionId || "") ||
            ["title", "summary", "body"].some(
              (key) => editForm.elements[key].value !== (p[key] || ""),
            ) ||
            fromSeoulInput(editForm.elements.effectiveAt.value) !==
              p.effectiveAt ||
            fromSeoulInput(editForm.elements.expiresAt.value) !== p.expiresAt)
        )
          throw new Error("수정한 원문이나 기간을 먼저 초안 저장해주세요.");
        await ctx.api(`/api/manage/policies/${p.id}/status`, {
          method: "POST",
          body: {
            ...data,
            version: p.version,
            action: p.status === "draft" ? "publish" : "withdraw",
          },
        });
        ctx.toast(
          p.status === "draft"
            ? "정책 버전을 발행했습니다."
            : "새 구매 적용을 철회했습니다.",
        );
        ctx.reload();
      });
    },
  };
}

function bindPolicyPricePicker(form, p, courses, ctx, editable) {
  const host = form.querySelector("#policy-price-picker");
  if (!editable) return () => {};
  let generation = 0,
    loading = false,
    failed = false,
    required = false;
  const context = () => ({
    kind: form.elements.kind.value,
    mode: form.elements.mode.value,
    courseId: form.elements.courseId.value,
  });
  async function load() {
    const serial = ++generation,
      scope = context();
    const course = courses.find((c) => c.id === scope.courseId);
    required = scope.kind === "course_terms" && course && !course.pilot;
    loading = false;
    failed = false;
    if (!required) {
      host.replaceChildren();
      return;
    }
    const selected =
      p?.kind === scope.kind &&
      p?.mode === scope.mode &&
      p?.courseId === scope.courseId
        ? p.priceVersionId || ""
        : "";
    loading = true;
    host.innerHTML =
      '<p class="p-muted" role="status">발행 가격을 불러오고 있습니다.</p>';
    let page = 1,
      rows = [],
      total = 0,
      busy = false;
    async function fetchPage() {
      if (busy) return;
      busy = true;
      try {
        const result = await ctx.api(
          `/api/studio/courses/${scope.courseId}/prices?mode=${scope.mode}&status=published&page=${page}`,
        );
        if (serial !== generation || !host.isConnected) return;
        rows.push(...result.prices);
        total = result.total;
        page++;
        if (page === 2 && selected && !rows.some((r) => r.id === selected)) {
          const exact = (
            await ctx.api(
              `/api/studio/courses/${scope.courseId}/prices/${selected}`,
            )
          ).priceVersion;
          if (serial !== generation || !host.isConnected) return;
          rows.unshift(exact);
        }
        const previous =
          host.querySelector("select")?.value || (page === 2 ? selected : "");
        const unique = [...new Map(rows.map((r) => [r.id, r])).values()];
        host.innerHTML = `<label class="field">문서에 연결할 발행 가격<select name="priceVersionId" required><option value="">가격 버전을 직접 선택해주세요</option>${unique.map((r) => `<option value="${esc(r.id)}" ${r.id === previous ? "selected" : ""}>가격 ${r.edition}판 · ${r.payload.amount === 0 ? "무료 · 0원" : money(r.payload.amount)} · 상품 v${r.courseVersion} · ${esc(r.effectiveAt ? date(r.effectiveAt) : "시행 미정")}${r.status !== "published" ? " · 철회됨" : ""}</option>`).join("")}</select></label><p class="form-note">같은 상품·환경의 승인된 가격을 연결합니다. 문서 시행 시점에 적용되는 가격이어야 합니다. 새 가격이 시행되면 그 가격에 맞는 새 문서를 발행합니다.</p>${(page - 1) * result.pageSize < total ? '<button class="btn btn-outline btn-sm" type="button" data-more-prices>이전 가격 더 보기</button>' : ""}${!unique.length ? `<p class="p-notice amber">연결할 발행 가격이 없습니다. <a class="text-link" target="_blank" rel="noopener" href="#/studio/course/${esc(scope.courseId)}/pricing?mode=${scope.mode}">가격 검수 화면 ↗</a></p>` : ""}`;
        host
          .querySelector("[data-more-prices]")
          ?.addEventListener("click", () => fetchPage());
        loading = false;
        failed = false;
      } catch (error) {
        if (serial !== generation || !host.isConnected) return;
        failed = true;
        loading = false;
        host.innerHTML = `<p class="form-error" role="alert">${esc(error.message)}</p><button type="button" class="btn btn-outline btn-sm">가격 목록 다시 확인</button>`;
        host.querySelector("button").onclick = load;
      } finally {
        busy = false;
      }
    }
    await fetchPage();
  }
  for (const key of ["kind", "mode", "courseId"])
    form.elements[key].addEventListener("change", load);
  load();
  return () => {
    if (required && (loading || failed || !form.elements.priceVersionId?.value))
      throw new Error("문서에 연결할 가격 버전을 확인하고 직접 선택해주세요.");
  };
}

export function bindOrderConditions(ctx, root = document, operator = false) {
  root.querySelectorAll("[data-order-conditions]").forEach((button) => {
    button.onclick = async () => {
      button.disabled = true;
      try {
        const endpoint = operator
          ? "/api/manage/purchases"
          : "/api/checkout/orders";
        const { conditions: c } = await ctx.api(
          `${endpoint}/${encodeURIComponent(button.dataset.orderConditions)}/conditions`,
        );
        ctx.modal(
          `<p class="eyebrow">PURCHASE CONDITIONS</p><h2>구매 당시 제공 조건</h2><p class="p-policy-hash">주문 ${esc(c.orderId)}</p>${c.status === "recorded" ? `<p>${esc(c.course.title)} · ${money(c.course.amount)} · 상품 v${c.course.version}</p><p class="p-muted">기록 시각 ${date(c.sealedAt)}</p>${c.documents.map((p) => `<details class="p-policy-receipt"><summary>${esc(policyKinds[p.kind])} · ${p.edition}판${p.kind === "content_rights" ? " · 운영자 전용" : ""}</summary>${documentCard(p, { body: true })}<p class="p-muted">${c.consents.find((s) => s.policyId === p.id) ? `동의 시각 ${date(c.consents.find((s) => s.policyId === p.id).acceptedAt)}` : "운영 증빙 · 고객 동의 대상 아님"}</p></details>`).join("")}` : `<div class="p-notice amber">이전 주문에 문서 버전과 동의 기록이 없습니다. 과거 동의를 소급해서 만들지 않습니다.</div>`}${c.paymentCheck?.classification === "legacy_inflight" ? `<p class="form-note">이관 전 시작한 결제의 결과 확인 기록입니다. 새로운 정책 동의를 뜻하지 않습니다.</p>` : ""}`,
          () => {},
        );
      } catch (error) {
        ctx.toast(error.message, "error");
      } finally {
        button.disabled = false;
      }
    };
  });
}
