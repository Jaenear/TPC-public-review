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
  statusLabel,
} from "./platform-ui.js";
import {
  contractStates,
  productionModels,
  basisLabels,
  contractTextFields,
  bindingStates,
  bpsToPercent,
  contractPayload,
  contractChanged,
} from "./contract-model.js";
const modeLabel = (m) => (m === "live" ? "상용 환경" : "테스트 환경");
const note = (t) => `<p class="p-notice amber">${esc(t)}</p>`;
const period = (s, e) =>
  `${s ? date(s) : "시행 미정"} → ${e ? date(e) : "종료 지정 없음"}`;
const link = (cid, vid = "", extra = {}) =>
  `/studio/contracts/${cid}?${new URLSearchParams({ ...(vid ? { version: vid } : {}), ...extra })}`;
const termsSummary = (p) =>
  `<div class="p-contract-summary"><h3>${esc(p.title)}</h3><p>${esc(productionModels[p.productionModel])} · ${esc(basisLabels[p.revenueBasis])} · KRW</p><dl class="p-contract-rates"><div><dt>코치 배분</dt><dd>${p.coachShareBps == null ? "미정" : esc(bpsToPercent(p.coachShareBps)) + "%"}</dd></div><div><dt>회사 배분</dt><dd>${p.companyShareBps == null ? "미정" : esc(bpsToPercent(p.companyShareBps)) + "%"}</dd></div></dl><p class="p-muted">${esc(period(p.effectiveAt, p.expiresAt))}</p>${Object.entries(
    contractTextFields,
  )
    .map(
      ([k, title]) =>
        `<details><summary>${esc(title)}</summary><div class="p-policy-body">${p[k] ? textBody(p[k]) : '<p class="p-muted">아직 작성하지 않았습니다.</p>'}</div></details>`,
    )
    .join("")}</div>`;

export async function renderContracts(path, params, ctx) {
  if (
    !["/studio/contracts", "/manage"].includes(path) &&
    !path.startsWith("/studio/contracts/")
  )
    return null;
  if (path === "/manage" && params.get("tab") !== "contracts") return null;
  if (!ctx.user) return authGate(ctx, "파트너 조건을 내 계정에서 확인하세요.");
  if (ctx.user.role !== "admin" && !ctx.user.roles?.includes("coach"))
    return {
      html: `<section class="container p-page">${empty("계약 기록에 접근할 권한이 없습니다.", "파트너 본인과 관리자만 조회할 수 있습니다.")}</section>`,
    };
  if (path.startsWith("/studio/contracts/"))
    return editor(path.slice(18), params, ctx);
  return listing(params, ctx);
}
async function listing(params, ctx) {
  const data = await ctx.api("/api/studio/contracts?" + params);
  const admin = ctx.user.role === "admin";
  return {
    html: `<section class="container p-page">${head("PARTNER TERMS", "만드는 과정부터, 합의한 조건까지.", "제작 기여·권리·배분과 상품별 적용 기간을 같은 버전으로 확인합니다.", admin ? '<button class="btn btn-primary" id="new-partner-contract">계약 묶음 만들기 +</button>' : "")}${opsNav(ctx, "contracts")}${note("제안서의 수치는 협의안입니다. 실제 합의한 자료와 배분·권리·정산 기준을 확인한 버전만 상품에 적용합니다.")}<form id="contract-search" class="p-search-row"><label class="sr-only" for="contract-q">계약 검색</label><input id="contract-q" name="q" value="${esc(params.get("q") || "")}" placeholder="계약명 또는 파트너명"/><select name="mode" aria-label="계약 환경"><option value="">전체 환경</option><option value="test" ${params.get("mode") === "test" ? "selected" : ""}>테스트 환경</option><option value="live" ${params.get("mode") === "live" ? "selected" : ""}>상용 환경</option></select><button class="btn btn-outline" type="submit">검색</button></form><div class="p-contract-list">${data.contracts.map((c) => `<a class="p-panel" href="#${link(c.id)}"><div class="p-inline-heading"><h2>${esc(c.title)}</h2>${tag(modeLabel(c.mode), c.mode === "test" ? "amber" : "")}</div><p>${esc(c.partnerName)} · ${c.latestEdition ? `조건 ${c.latestEdition}판` : "아직 작성한 조건이 없습니다."}</p><span class="text-link">조건·상품 적용 확인 ↗</span></a>`).join("") || empty("등록된 계약 기록이 없습니다.", "합의할 제작 기여와 권리 조건부터 차근차근 준비하세요.")}</div>${pager("/studio/contracts", params, data.page, data.total)}</section>`,
    bind() {
      bindForm("#contract-search", (d) =>
        ctx.navigate("/studio/contracts?" + new URLSearchParams(d)),
      );
      document
        .querySelector("#new-partner-contract")
        ?.addEventListener("click", () => newContract(ctx));
    },
  };
}
async function newContract(ctx) {
  try {
    const initial = await ctx.api("/api/manage/contract-partners");
    ctx.modal(
      `<p class="eyebrow">PARTNER RECORD</p><h2>계약 묶음 만들기</h2><p>담당 계정과 환경은 생성 후 유지됩니다. 다른 파트너나 환경에는 새 묶음을 만듭니다.</p><form id="new-contract" data-no-session-draft><label class="field">계약명<input name="title" required minlength="2" maxlength="150"/></label><label class="field">파트너 검색<input id="partner-query" placeholder="이름 또는 이메일"/></label><button class="btn btn-outline btn-sm" type="button" id="find-contract-partner">파트너 찾기</button><label class="field">담당 파트너<select name="partnerUserId" required><option value="">계정 선택</option>${initial.users.map((u) => `<option value="${esc(u.id)}">${esc(u.name)} · ${esc(u.email)}</option>`).join("")}</select></label><div class="p-form-actions"><button type="button" class="btn btn-quiet btn-sm" id="partner-previous" disabled>이전 계정</button><span id="partner-page">1쪽 · ${initial.total}명</span><button type="button" class="btn btn-quiet btn-sm" id="partner-next" ${initial.total <= 20 ? "disabled" : ""}>다음 계정</button></div><label class="field">환경<select name="mode"><option value="test">테스트 환경</option><option value="live">상용 환경</option></select></label><label class="field">생성 사유<textarea name="reason" required minlength="5" maxlength="1000"></textarea></label><p class="form-error" data-error role="alert"></p><button class="btn btn-primary" type="submit">묶음 만들기</button></form>`,
      (dialog) => {
        const form = dialog.querySelector("form");
        let page = 1;
        async function load(n) {
          try {
            const r = await ctx.api(
              "/api/manage/contract-partners?" +
                new URLSearchParams({
                  q: dialog.querySelector("#partner-query").value,
                  page: n,
                }),
            );
            const select = form.elements.partnerUserId;
            const chosen = select.selectedOptions[0];
            const options = r.users.map((u) => [
              u.id,
              `${u.name} · ${u.email}`,
            ]);
            if (select.value && !options.some(([id]) => id === select.value))
              options.unshift([select.value, chosen.textContent]);
            select.innerHTML =
              '<option value="">계정 선택</option>' +
              options
                .map(
                  ([id, label]) =>
                    `<option value="${esc(id)}" ${id === chosen?.value ? "selected" : ""}>${esc(label)}</option>`,
                )
                .join("");
            page = r.page;
            dialog.querySelector("#partner-page").textContent =
              `${page}쪽 · ${r.total}명`;
            dialog.querySelector("#partner-previous").disabled = page === 1;
            dialog.querySelector("#partner-next").disabled =
              page * 20 >= r.total;
          } catch (e) {
            form.querySelector("[data-error]").textContent = e.message;
          }
        }
        dialog.querySelector("#find-contract-partner").onclick = () => load(1);
        dialog.querySelector("#partner-previous").onclick = () =>
          load(page - 1);
        dialog.querySelector("#partner-next").onclick = () => load(page + 1);
        bindForm(form, async (d) => {
          const r = await ctx.api("/api/manage/contracts", {
            method: "POST",
            body: d,
          });
          dialog.close();
          ctx.navigate(link(r.contractId));
        });
      },
    );
  } catch (e) {
    ctx.toast(e.message, "error");
  }
}
function fields(p) {
  const select = (name, label, choices) =>
    `<label class="field">${esc(label)}<select name="${name}">${Object.entries(
      choices,
    )
      .map(
        ([k, v]) =>
          `<option value="${k}" ${p[name] === k ? "selected" : ""}>${esc(v)}</option>`,
      )
      .join("")}</select></label>`;
  return `<label class="field">조건명<input name="title" value="${esc(p.title)}" required minlength="2" maxlength="150"/></label><div class="p-form-grid">${select("productionModel", "제작 방식", productionModels)}${select("revenueBasis", "배분 기준", basisLabels)}</div><div class="p-form-grid"><label class="field">코치 배분 (%)<input name="coachPercent" inputmode="decimal" value="${esc(bpsToPercent(p.coachShareBps))}" placeholder="미정"/></label><label class="field">회사 배분 (%)<input name="companyPercent" inputmode="decimal" value="${esc(bpsToPercent(p.companyShareBps))}" placeholder="미정"/></label></div><p class="form-note">합계100% · 소수 둘째 자리까지 · 통화 KRW. 확정 전에는 빈 값으로 보관할 수 있습니다.</p><div class="p-form-grid"><label class="field">계약 시행 (한국 시간)<input type="datetime-local" step="1" name="effectiveAt" value="${esc(toSeoulInput(p.effectiveAt))}"/></label><label class="field">계약 종료 (한국 시간·선택)<input type="datetime-local" step="1" name="expiresAt" value="${esc(toSeoulInput(p.expiresAt))}"/></label></div>${Object.entries(
    contractTextFields,
  )
    .map(
      ([k, label]) =>
        `<label class="field">${esc(label)}<textarea name="${k}" maxlength="${k === "body" ? 12000 : 3000}" rows="${k === "body" ? 7 : 3}">${esc(p[k])}</textarea></label>`,
    )
    .join(
      "",
    )}<label class="field">작성 메모<textarea name="reason" maxlength="1000" rows="2"></textarea></label><p class="form-note">조건 원문에는 주민등록번호·은행정보 등 불필요한 개인정보를 포함하지 마세요.</p>`;
}
async function editor(cid, params, ctx) {
  const admin = ctx.user.role === "admin";
  const [data, orders, courses, bindingData] = await Promise.all([
    ctx.api(
      "/api/studio/contracts/" + cid + "?page=" + (params.get("page") || 1),
    ),
    ctx.api(
      "/api/studio/contracts/" +
        cid +
        "/orders?page=" +
        (params.get("ordersPage") || 1),
    ),
    admin ? ctx.api("/api/studio/courses") : Promise.resolve({ courses: [] }),
    ctx.api(
      `/api/studio/contracts/${cid}/bindings?page=${params.get("bindingPage") || 1}`,
    ),
  ]);
  const contract = data.contract;
  const selected = params.get("version");
  let working =
    data.versions.find((r) => r.id === selected) || data.versions[0];
  if (selected && !data.versions.some((r) => r.id === selected))
    working = (
      await ctx.api(`/api/studio/contracts/${cid}/versions/${selected}`)
    ).contractVersion;
  const editable =
    admin && working && ["draft", "rejected"].includes(working.status);
  const available = courses.courses.filter(
    (c) =>
      !c.pilot &&
      c.status !== "archived" &&
      c.instructorUserId === contract.partnerUserId,
  );
  const choices = [
    ...(working && !data.versions.some((v) => v.id === working.id)
      ? [working]
      : []),
    ...data.versions,
  ];
  const previous = data.versions.find(
    (v) => v.id !== working?.id && v.status === "recorded",
  );
  return {
    title: contract.title + " · 파트너 조건",
    html: `<section class="container p-page"><a class="back-link" href="#/studio/contracts">← 파트너 조건 목록</a>${head("PARTNER TERMS", contract.title, `${contract.partnerName} · ${modeLabel(contract.mode)}`, admin ? '<button class="btn btn-outline" id="new-contract-version">새 조건 작성</button>' : "")}${opsNav(ctx, "contracts")}${note("이 화면은 외부에서 합의한 조건과 근거를 기록합니다. 제안 수치를 확정값으로 채우지 않으며, 합의 자료 확인 기록과 전자서명·정산 지급은 구분합니다.")}<div class="p-contract-version-bar"><label class="field">확인할 조건 버전<select id="contract-version-select">${choices.map((v) => `<option value="${esc(v.id)}" ${v.id === working?.id ? "selected" : ""}>${v.edition}판 · ${esc(contractStates[v.status])}</option>`).join("")}</select></label>${working ? tag(contractStates[working.status], working.status === "recorded" ? "mint" : "amber") : ""}</div>${pager("/studio/contracts/" + cid, params, data.page, data.total)}${
      working
        ? `<div class="p-contract-layout"><section class="p-panel"><div class="p-inline-heading"><h2>${working.edition}판 · ${editable ? "조건 작성" : "기록 원문"}</h2><span class="p-muted">v${working.version}</span></div>${working.reviewNote ? `<div class="p-notice">${textBody(working.reviewNote)}</div>` : ""}${editable ? `<form id="contract-editor" data-session-draft data-recovery-version="${esc(working.id + ":" + working.version + ":" + contract.mode)}">${fields(working.payload)}<p class="form-error" data-error role="alert"></p><button type="submit" class="btn btn-primary">조건 초안 저장</button><p class="p-muted" id="contract-dirty">저장한 조건입니다.</p></form>` : termsSummary(working.payload)}<div class="p-form-actions">${admin ? (editable ? '<button class="btn btn-outline" data-contract-action="submit">검토로 넘기기</button><button class="btn btn-quiet" data-contract-action="cancel">작성 중단</button>' : working.status === "submitted" ? '<button class="btn btn-primary" data-contract-action="record">합의 자료 확인 등록</button><button class="btn btn-outline" data-contract-action="reject">수정 요청</button><button class="btn btn-quiet" data-contract-action="withdraw_submission">작성으로 돌리기</button>' : working.status === "recorded" ? '<button class="btn btn-danger" data-contract-action="withdraw">이 조건의 적용 철회</button>' : "") : ""}</div>${working.recordedAt ? `<p class="p-muted">합의 자료 기록 ${esc(date(working.recordedAt))}${admin ? ` · 참조 ${esc(working.agreementReference)}` : ""}</p>` : ""}</section><aside class="p-panel"><h2>조건 버전 비교</h2><label class="field">비교할 이전 버전<select id="compare-contract"><option value="">버전 선택</option>${choices
            .filter((v) => v.id !== working.id)
            .map(
              (v) =>
                `<option value="${esc(v.id)}" ${v.id === previous?.id ? "selected" : ""}>${v.edition}판 · ${esc(contractStates[v.status])}</option>`,
            )
            .join(
              "",
            )}</select></label><div id="contract-comparison">${previous ? termsSummary(previous.payload) : '<p class="p-muted">비교할 버전을 선택하세요. 과거 원문은 수정되지 않습니다.</p>'}</div></aside></div>`
        : empty(
            "아직 조건을 작성하지 않았습니다.",
            "관리자가 새 조건을 작성해 제작 기여·권리·기간을 준비합니다.",
          )
    }
 <section class="p-panel"><h2>상품별 적용 기간 · ${bindingData.total}건</h2><p class="p-muted">같은 상품·환경의 기간은 겹칠 수 없습니다. 적용 종료는 이후 주문에 반영하고 과거 주문의 원문은 유지합니다.</p>${admin && working?.status === "recorded" ? `<form id="bind-product-contract" data-no-session-draft><label class="field">적용할 정식 강의<select name="courseId" required><option value="">강의 선택</option>${available.map((c) => `<option value="${esc(c.id)}">${esc(c.title)} · ${esc(statusLabel(c.status))}</option>`).join("")}</select></label><div class="p-form-grid"><label class="field">적용 시작 (한국 시간)<input name="startsAt" type="datetime-local" step="1" value="${esc(toSeoulInput(working.payload.effectiveAt))}" required/></label><label class="field">적용 종료 (한국 시간·선택)<input name="expiresAt" type="datetime-local" step="1" value="${esc(toSeoulInput(working.payload.expiresAt))}"/></label></div><label class="field">상품 적용 사유<textarea name="reason" required minlength="5" maxlength="1000" rows="2"></textarea></label><p class="form-error" data-error role="alert"></p><button class="btn btn-primary" type="submit">이 버전을 상품에 적용</button></form>` : ""}<div class="p-contract-bindings">${bindingData.bindings.map((b) => `<article><div class="p-inline-heading"><h3>${esc(b.courseTitle)}</h3>${tag(bindingStates[b.state], b.state === "active" ? "mint" : "")}</div><p class="p-muted">조건 ${b.edition}판 · ${esc(modeLabel(b.mode))} · ${b.currentApplication ? "현재 계약 적용" : esc(b.readinessReason)}</p><p>${esc(period(b.startsAt, b.effectiveEnd))}</p><p class="p-muted">${esc(b.reason)}</p>${b.stopsAt ? `<p class="p-muted">적용 종료 기록 ${esc(date(b.stopsAt))}</p>` : ""}${admin && ["active", "scheduled"].includes(b.state) ? `<button class="btn btn-outline btn-sm" data-stop-contract="${esc(b.id)}">적용 종료·예약</button>` : ""}</article>`).join("") || '<p class="p-muted">아직 연결한 상품이 없습니다.</p>'}</div>${bindingData.page > 1 ? `<a class="btn btn-quiet btn-sm" href="#${link(cid, working?.id, { ...Object.fromEntries(params), bindingPage: bindingData.page - 1 })}">이전 적용 기록</a>` : ""}${bindingData.page * 20 < bindingData.total ? `<a class="btn btn-quiet btn-sm" href="#${link(cid, working?.id, { ...Object.fromEntries(params), bindingPage: bindingData.page + 1 })}">다음 적용 기록</a>` : ""}</section>
 <section class="p-panel"><h2>주문 당시 적용 기록 · ${orders.total}건</h2><p class="p-muted">상품 적용 상태가 바뀌어도 주문 당시 조건은 보존됩니다. 정산 금액 계산·지급 결과와 구분합니다.</p>${orders.orders.map((o) => `<article class="p-contract-order"><div><h3>${esc(o.title)}</h3><p>${o.edition}판 · ${esc(date(o.sealedAt))} · ${esc(statusLabel(o.status))}</p></div><strong>${money(o.amount)}</strong><button class="btn btn-outline btn-sm" data-order-contract="${esc(o.orderId)}">당시 적용 조건</button></article>`).join("") || '<p class="p-muted">이 계약에 연결된 주문이 없습니다. 과거 미수집 주문에 현재 조건을 소급하지 않습니다.</p>'}${orders.page > 1 ? `<a class="text-link" href="#${link(cid, working?.id, { ordersPage: orders.page - 1 })}">이전 주문</a>` : ""}${orders.page * 20 < orders.total ? `<a class="text-link" href="#${link(cid, working?.id, { ordersPage: orders.page + 1 })}">다음 주문</a>` : ""}</section>
 <details class="p-panel"><summary>최근 진행 이력 · ${data.events.length}건</summary>${data.events.map((e) => `<article class="p-contract-event"><strong>${esc({ created: "묶음 생성", "draft.created": "조건 작성", save: "초안 저장", submit: "검토 요청", reject: "수정 요청", record: "합의 자료 기록", withdraw: "적용 철회", cancel: "작성 중단", withdraw_submission: "작성으로 전환", "product.bound": "상품 연결", "product.stopped": "상품 적용 종료" }[e.action] || e.action)}</strong><small>${esc(date(e.createdAt))}</small>${textBody(e.reason)}</article>`).join("")}</details></section>`,
    bind() {
      const form = document.querySelector("#contract-editor");
      const dirty = () =>
        form &&
        contractChanged(
          contractPayload(Object.fromEntries(new FormData(form))),
          working.payload,
        );
      function savedFirst() {
        if (dirty())
          throw new Error(
            "작성 중인 조건을 먼저 저장해주세요. 저장한 원문으로 진행합니다.",
          );
      }
      if (form) {
        const refresh = () => {
          try {
            document.querySelector("#contract-dirty").textContent = dirty()
              ? "저장하지 않은 변경이 있습니다."
              : "저장한 조건입니다.";
          } catch {
            document.querySelector("#contract-dirty").textContent =
              "입력값을 확인해주세요.";
          }
        };
        form.addEventListener("input", refresh);
        form.addEventListener("tpc:draft-restored", refresh);
        bindForm(form, async (values) => {
          await ctx.api(`/api/manage/contracts/${cid}/versions/${working.id}`, {
            method: "PATCH",
            body: {
              version: working.version,
              payload: contractPayload(values),
              reason: values.reason,
            },
          });
          ctx.toast("초안을 저장했습니다.");
          ctx.navigate(link(cid, working.id));
          ctx.reload();
        });
      }
      document
        .querySelector("#new-contract-version")
        ?.addEventListener("click", async () => {
          try {
            savedFirst();
            const r = await ctx.api(`/api/manage/contracts/${cid}/versions`, {
              method: "POST",
              body: {},
            });
            ctx.navigate(link(cid, r.contractVersion.id));
            ctx.reload();
          } catch (e) {
            ctx.toast(e.message, "error");
          }
        });
      document.querySelector("#contract-version-select").onchange = (e) => {
        try {
          savedFirst();
          ctx.navigate(link(cid, e.target.value));
        } catch (err) {
          e.target.value = working.id;
          ctx.toast(err.message, "error");
        }
      };
      document
        .querySelector("#compare-contract")
        ?.addEventListener("change", async (e) => {
          try {
            const r = e.target.value
              ? (
                  await ctx.api(
                    `/api/studio/contracts/${cid}/versions/${e.target.value}`,
                  )
                ).contractVersion
              : null;
            document.querySelector("#contract-comparison").innerHTML = r
              ? termsSummary(r.payload)
              : '<p class="p-muted">비교할 버전을 선택하세요.</p>';
          } catch (err) {
            ctx.toast(err.message, "error");
          }
        });
      document.querySelectorAll("[data-contract-action]").forEach(
        (button) =>
          (button.onclick = () => {
            try {
              savedFirst();
              actionDialog(button.dataset.contractAction, working, cid, ctx);
            } catch (e) {
              ctx.toast(e.message, "error");
            }
          }),
      );
      bindForm("#bind-product-contract", async (d) => {
        savedFirst();
        await ctx.api(
          `/api/manage/contracts/${cid}/versions/${working.id}/bindings`,
          {
            method: "POST",
            body: {
              ...d,
              version: working.version,
              courseVersion: available.find((c) => c.id === d.courseId)
                ?.version,
              startsAt: fromSeoulInput(d.startsAt),
              expiresAt: fromSeoulInput(d.expiresAt) || null,
            },
          },
        );
        ctx.toast("상품 적용 기간을 기록했습니다.");
        ctx.reload();
      });
      document.querySelectorAll("[data-stop-contract]").forEach(
        (button) =>
          (button.onclick = () => {
            try {
              savedFirst();
              stopDialog(
                bindingData.bindings.find(
                  (b) => b.id === button.dataset.stopContract,
                ),
                ctx,
              );
            } catch (e) {
              ctx.toast(e.message, "error");
            }
          }),
      );
      bindContractSnapshots(ctx);
    },
  };
}
function actionDialog(action, r, cid, ctx) {
  const title = {
    submit: "검토로 넘기기",
    record: "합의 자료 확인 등록",
    reject: "수정 요청",
    withdraw_submission: "작성으로 돌리기",
    cancel: "작성 중단",
    withdraw: "이 조건의 적용 철회",
  }[action];
  ctx.modal(
    `<h2>${esc(title)}</h2>${action === "record" ? note("외부에서 이미 합의한 자료의 참조를 기록합니다. 미정 조건을 제안서 수치로 대신하지 말고 당사자가 확인한 배분·권리·정산 기준을 입력하세요.") : action === "withdraw" ? note("이 버전의 현재·예정 상품 적용을 종료합니다. 과거 주문 원문과 이미 시작한 결제 결과 복구는 보존됩니다.") : ""}<form id="contract-action" data-no-session-draft>${action === "record" ? '<label class="field">합의 자료 참조<input name="agreementReference" required minlength="5" maxlength="2000"/></label><label class="field">배분·공제 합의 근거 (DEC-01)<input name="revenueReference" required minlength="5" maxlength="2000"/></label><label class="field">권리·사용 범위 합의 근거 (DEC-04)<input name="rightsReference" required minlength="5" maxlength="2000"/></label><label class="field">정산·증빙·지급 절차 합의 근거 (DEC-07)<input name="settlementReference" required minlength="5" maxlength="2000"/></label>' : ""}<label class="field">처리 사유·검토 의견<textarea name="reason" required minlength="5" maxlength="1000" rows="4"></textarea></label><p class="form-error" data-error role="alert"></p><button class="btn ${action === "withdraw" ? "btn-danger" : "btn-primary"}" type="submit">${esc(title)}</button></form>`,
    (dialog) =>
      bindForm(dialog.querySelector("form"), async (d) => {
        await ctx.api(`/api/manage/contracts/${cid}/versions/${r.id}`, {
          method: "POST",
          body: {
            version: r.version,
            action,
            reason: d.reason,
            ...(action === "record"
              ? {
                  agreementReference: d.agreementReference,
                  decisionReferences: {
                    "DEC-01": d.revenueReference,
                    "DEC-04": d.rightsReference,
                    "DEC-07": d.settlementReference,
                  },
                }
              : {}),
          },
        });
        dialog.close();
        ctx.reload();
      }),
  );
}
function stopDialog(b, ctx) {
  ctx.modal(
    `<h2>상품 적용 종료·예약</h2><p>현재 적용: ${esc(period(b.startsAt, b.effectiveEnd))}</p><form id="stop-product-contract" data-no-session-draft><label class="field">종료 시각 (한국 시간·비우면 즉시)<input type="datetime-local" step="1" name="stopsAt"/></label><label class="field">종료 사유<textarea name="reason" required minlength="5" maxlength="1000" rows="3"></textarea></label><p class="form-error" data-error role="alert"></p><button class="btn btn-primary" type="submit">종료 기록</button></form>`,
    (dialog) =>
      bindForm(dialog.querySelector("form"), async (d) => {
        await ctx.api("/api/manage/contract-bindings/" + b.id + "/stop", {
          method: "POST",
          body: {
            version: b.version,
            reason: d.reason,
            stopsAt: fromSeoulInput(d.stopsAt) || null,
          },
        });
        dialog.close();
        ctx.reload();
      }),
  );
}
export function bindContractSnapshots(ctx, root = document) {
  root.querySelectorAll("[data-order-contract]").forEach(
    (button) =>
      (button.onclick = async () => {
        button.disabled = true;
        try {
          const { contractSnapshot: s } = await ctx.api(
            "/api/studio/contract-orders/" +
              encodeURIComponent(button.dataset.orderContract),
          );
          ctx.modal(
            s.recorded
              ? `<p class="eyebrow">ORDER TERMS SNAPSHOT</p><h2>주문 당시 적용 조건</h2><p>${esc(s.payload.title)} · ${money(s.payload.amount)} · ${modeLabel(s.payload.mode)}</p><p class="p-muted">조건 ${s.payload.edition}판 · 기록 ${esc(date(s.sealedAt))}</p>${termsSummary(s.payload.terms)}<p class="p-muted">당시 상품 적용: ${esc(period(s.payload.binding.startsAt, s.payload.binding.effectiveEnd))}</p>`
              : `<h2>이 주문의 계약 조건은 미수집 상태입니다.</h2><p>기존 주문과 학습 기록은 보존됩니다. 현재 계약을 과거 거래에 소급하지 않습니다.</p>`,
          );
        } catch (e) {
          ctx.toast(e.message, "error");
        } finally {
          button.disabled = false;
        }
      }),
  );
}
