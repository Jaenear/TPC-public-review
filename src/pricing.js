import { esc, money } from "./ui.js";
import { toSeoulInput } from "./access.js";
import {
  head,
  empty,
  authGate,
  bindForm,
  date,
  tag,
  opsNav,
  pager,
} from "./platform-ui.js";
import {
  priceStates,
  priceTiming,
  pricePayload,
  priceChanged,
} from "./price-model.js";
const modeLabel = (m) => (m === "live" ? "상용 환경" : "테스트 환경");
const amountLabel = (a) =>
  a == null ? "미정" : a === 0 ? "무료 · 0원" : money(a);
const href = (id, mode, price = "", extra = {}) =>
  `/studio/course/${id}/pricing?${new URLSearchParams({ mode, ...(price ? { price } : {}), ...extra })}`;
const period = (p) =>
  `${p.effectiveAt ? date(p.effectiveAt) : "발행 시 바로 시행"} → ${p.expiresAt ? date(p.expiresAt) : "가격 종료 지정 없음"}`;
const summary = (p) =>
  `<h3>${esc(p.payload.label)}</h3><strong class="p-price-amount">${amountLabel(p.payload.amount)}</strong><p>${esc(period(p))}</p><p class="p-muted">${modeLabel(p.mode)} · 가격 ${p.edition}판 · 기준 상품 v${p.courseVersion}</p>`;
export async function renderPricing(path, params, ctx) {
  const match = path.match(/^\/studio\/course\/([a-z0-9-]+)\/pricing$/);
  if (!match) return null;
  if (!ctx.user)
    return authGate(ctx, "담당 강의의 가격과 판매 준비를 확인하세요.");
  if (ctx.user.role !== "admin" && !ctx.user.roles?.includes("coach"))
    return {
      html: `<section class="container p-page">${empty("가격 관리 권한이 필요합니다.", "담당 코치와 관리자만 이용할 수 있습니다.")}</section>`,
    };
  return editor(match[1], params, ctx);
}
async function editor(id, params, ctx) {
  const mode = params.get("mode") === "live" ? "live" : "test",
    admin = ctx.user.role === "admin";
  const [courseData, data, readyData] = await Promise.all([
    ctx.api(`/api/studio/courses/${id}`),
    ctx.api(
      `/api/studio/courses/${id}/prices?mode=${mode}&page=${params.get("page") || 1}`,
    ),
    ctx.api(`/api/studio/courses/${id}/sale-readiness?mode=${mode}`),
  ]);
  const course = courseData.course,
    ready = readyData.readiness;
  let working =
    data.prices.find((p) => p.id === params.get("price")) || data.prices[0];
  if (
    params.get("price") &&
    !data.prices.some((p) => p.id === params.get("price"))
  )
    working = (
      await ctx.api(`/api/studio/courses/${id}/prices/${params.get("price")}`)
    ).priceVersion;
  const choices = [
    ...(working && !data.prices.some((p) => p.id === working.id)
      ? [working]
      : []),
    ...data.prices,
  ];
  const editable =
    working &&
    ["draft", "rejected"].includes(working.status) &&
    (admin || working.authorId === ctx.user.id);
  const controls = data.saleControl;
  return {
    title: course.title + " · 가격과 판매 준비",
    html: `<section class="container p-page" id="pricing-page"><a class="back-link" href="#/studio/course/${esc(id)}">← 강의 정보</a>${head("PRICE & SALES READINESS", course.title, "가격 검수와 상품 제공 조건, 판매 상태를 함께 확인합니다.", !course.pilot ? '<button class="btn btn-primary" id="new-price">가격 변경안 만들기 +</button>' : "")}${opsNav(ctx, "courses")}<nav class="p-tabs" aria-label="가격 환경"><button class="btn ${mode === "test" ? "btn-primary" : "btn-outline"}" data-price-mode="test">테스트 환경</button><button class="btn ${mode === "live" ? "btn-primary" : "btn-outline"}" data-price-mode="live">상용 환경</button></nav>${course.pilot ? '<p class="p-notice amber">파일럿은 기존 예시 가격을 유지합니다. 정식 강의 전환 후 가격을 검수합니다. 신규 접수 중지는 아래 판매 상태에서 관리할 수 있습니다.</p>' : ""}<div class="p-pricing-stats"><article class="p-panel"><span class="p-muted">현재 적용 가격</span><strong>${data.current.ready ? amountLabel(data.current.amount) : esc(priceTiming[data.current.status])}</strong><small>${data.current.edition ? `가격 ${data.current.edition}판 · ` : ""}${modeLabel(mode)}</small></article><article class="p-panel"><span class="p-muted">신규 판매</span><strong>${controls.state === "paused" ? "중지" : "접수 가능 설정"}</strong><small>기존 수강·주문 기록 보존</small></article><article class="p-panel"><span class="p-muted">전체 제공 조건</span><strong>${ready.ready ? "준비 확인" : "추가 확인 필요"}</strong><small>${ready.ready ? "가격·문서·계약 적용 확인" : `${ready.blockers.length}개 확인 항목`}</small></article></div><section class="p-panel"><div class="p-inline-heading"><h2>상품 제공 준비</h2>${tag(ready.ready ? "준비 확인" : "검토 필요", ready.ready ? "mint" : "amber")}</div>${ready.ready ? "<p>현재 가격과 연결 문서·상품 제공 조건을 확인했습니다. 결제 상점과 실제 자료 인수는 운영 기준에 따라 확인해주세요.</p>" : `<ul class="p-readiness-list">${ready.blockers.map((b) => `<li><strong>${esc(b.title)}</strong><span>${esc(b.message)}</span></li>`).join("")}</ul>`}${admin ? `<div class="p-form-actions"><a class="text-link" target="_blank" rel="noopener" href="#/manage?tab=policies&mode=${mode}&courseId=${esc(id)}">상품 제공 문서·권리 관리 ↗</a><a class="text-link" target="_blank" rel="noopener" href="#/studio/contracts?mode=${mode}">파트너 적용 조건 ↗</a></div><form id="sale-control" data-no-session-draft><h3>신규 판매 ${controls.state === "paused" ? "재개" : "중지"}</h3><p class="p-muted">새 주문과 무료 등록에 반영됩니다. 기존 학습과 이미 시작한 결제의 결과 확인은 보존합니다.</p><label class="field">판매 상태 변경 사유<textarea name="reason" required minlength="5" maxlength="1000" rows="2"></textarea></label><p class="form-error" data-error role="alert"></p><button type="submit" class="btn ${controls.state === "paused" ? "btn-primary" : "btn-outline"}">신규 판매 ${controls.state === "paused" ? "재개" : "중지"}</button></form>` : ""}</section>
 <div class="p-price-version-bar"><label class="field">확인할 가격 버전<select id="price-version">${choices.map((p) => `<option value="${p.id}" ${p.id === working?.id ? "selected" : ""}>${p.edition}판 · ${esc(priceStates[p.status])} · ${esc(amountLabel(p.payload.amount))}</option>`).join("")}</select></label>${working ? tag(priceStates[working.status], working.status === "published" ? "mint" : "") : ""}</div>${pager(`/studio/course/${id}/pricing`, params, data.page, data.total)}${working ? `<div class="p-pricing-layout"><section class="p-panel"><div class="p-inline-heading"><h2>가격 ${working.edition}판</h2><span id="price-row-version" class="p-muted">v${working.version} · 기준 상품 v${working.courseVersion}</span></div>${working.reviewNote ? `<div class="p-notice">${esc(working.reviewNote)}</div>` : ""}${working.courseVersion !== course.version ? '<p class="p-notice amber">상품 정보가 변경됐습니다. 작성 중인 변경안은 기준 상품을 다시 비교한 뒤 저장·제출해주세요.</p>' : ""}${editable ? `<form id="price-editor" data-session-draft data-recovery-version="${working.id}:${working.version}:${course.version}:${mode}"><label class="field">가격 이름<input name="label" required minlength="2" maxlength="100" value="${esc(working.payload.label)}"/></label><label class="field">판매 가격 (원·무료는 0)<input name="amount" type="number" min="0" max="10000000" step="1" value="${working.payload.amount ?? ""}" placeholder="미정"/></label><p class="form-note">미정과 무료를 구분합니다. 가격 검수 시 무료라면 0원을 직접 입력합니다. 통화는 KRW입니다.</p><div class="p-form-grid"><label class="field">가격 시행 (한국 시간·비우면 발행 즉시)<input name="effectiveAt" type="datetime-local" step="1" value="${toSeoulInput(working.payload.effectiveAt)}"/></label><label class="field">가격 종료 (한국 시간·선택)<input name="expiresAt" type="datetime-local" step="1" value="${toSeoulInput(working.payload.expiresAt)}"/></label></div><label class="field">작성 메모<textarea name="reason" maxlength="1000" rows="3">${esc(working.reason)}</textarea></label><p class="form-error" data-error role="alert"></p><button type="submit" class="btn btn-primary">가격 초안 저장</button><p class="p-muted" id="price-dirty">저장한 변경안입니다.</p></form>` : summary(working)}<div class="p-form-actions">${editable ? '<button class="btn btn-outline" data-price-action="submit">가격 검수 요청</button><button class="btn btn-outline" id="rebase-price">기준 상품 다시 비교</button><button class="btn btn-quiet" data-price-action="cancel">작성 중단</button>' : working.status === "submitted" ? `${admin ? '<button class="btn btn-primary" data-price-action="publish">판매 가격 발행</button><button class="btn btn-outline" data-price-action="reject">가격 수정 요청</button>' : ""}${admin || working.authorId === ctx.user.id ? '<button class="btn btn-quiet" data-price-action="withdraw_submission">검수 요청 회수</button>' : ""}` : admin && working.status === "published" ? '<button class="btn btn-danger" data-price-action="withdraw">이 가격 철회</button>' : ""}</div>${admin && working.approvalReference ? `<p class="p-policy-hash">가격 승인 근거: ${esc(working.approvalReference)}</p>` : ""}</section><aside class="p-panel"><h2>현재 상품과 가격 기준</h2><h3>${esc(course.title)}</h3><p>${esc(course.instructorName || "담당자 미지정")} · 상품 v${course.version}</p><p class="p-muted">작성 기준: ${esc(working.courseSnapshot.title)} · 상품 v${working.courseVersion}</p><p>새 가격은 시행 시점부터 적용합니다. 가격 종료·철회 뒤 이전 가격을 자동으로 되살리지 않습니다.</p>${data.current.next ? `<div class="p-notice">다음 시행 ${date(data.current.next.effectiveAt)} · 가격 ${data.current.next.edition}판</div>` : ""}<p class="p-muted">상품 제공 문서에는 해당 가격 버전을 연결합니다. 과거 주문에는 당시 가격·문서·내부 조건이 보존됩니다.</p><a class="text-link" target="_blank" rel="noopener" href="#/course/${esc(id)}">고객 상품 화면 ↗</a></aside></div>` : empty("가격 변경안이 없습니다.", "첫 변경안은 금액 미정으로 시작합니다. 제안 가격을 자동 확정하지 않습니다.")}
 <details class="p-panel"><summary>최근 가격·판매 진행 이력 · ${data.events.length}건</summary>${data.events.map((e) => `<article class="p-price-event"><strong>${esc({ "draft.created": "변경안 생성", save: "초안 저장", submit: "검수 요청", publish: "가격 발행", reject: "수정 요청", withdraw_submission: "요청 회수", withdraw: "가격 철회", cancel: "작성 중단", rebase: "기준 갱신", "sale.pause": "판매 중지", "sale.resume": "판매 재개" }[e.action] || e.action)}</strong><small>${esc(date(e.createdAt))}</small><p>${esc(e.reason)}</p></article>`).join("")}</details></section>`,
    bind() {
      const root = document.querySelector("#pricing-page"),
        form = root.querySelector("#price-editor");
      const dirty = () =>
        form &&
        (form.elements.reason.value !== (working.reason || "") ||
          priceChanged(
            pricePayload(Object.fromEntries(new FormData(form))),
            working.payload,
          ));
      function savedFirst() {
        if (dirty())
          throw new Error(
            "작성한 가격을 먼저 저장해주세요. 저장한 변경안으로 진행합니다.",
          );
      }
      root.addEventListener("click", (e) => {
        const a = e.target.closest("a");
        if (a && a.target !== "_blank" && form) {
          try {
            savedFirst();
          } catch (err) {
            e.preventDefault();
            ctx.toast(err.message, "error");
          }
        }
      });
      if (form) {
        const changed = () => {
          try {
            root.querySelector("#price-dirty").textContent = dirty()
              ? "저장하지 않은 변경이 있습니다."
              : "저장한 변경안입니다.";
          } catch (e) {
            root.querySelector("#price-dirty").textContent = e.message;
          }
        };
        form.addEventListener("input", changed);
        form.addEventListener("tpc:draft-restored", changed);
        bindForm(form, async (d) => {
          await ctx.api(`/api/studio/courses/${id}/prices/${working.id}`, {
            method: "PATCH",
            body: {
              version: working.version,
              payload: pricePayload(d),
              reason: d.reason,
            },
          });
          ctx.toast("가격 초안을 저장했습니다.");
          ctx.navigate(href(id, mode, working.id));
          ctx.reload();
        });
      }
      root.querySelectorAll("[data-price-mode]").forEach(
        (b) =>
          (b.onclick = () => {
            try {
              savedFirst();
              ctx.navigate(href(id, b.dataset.priceMode));
            } catch (e) {
              ctx.toast(e.message, "error");
            }
          }),
      );
      root.querySelector("#price-version").onchange = (e) => {
        try {
          savedFirst();
          ctx.navigate(href(id, mode, e.target.value));
        } catch (err) {
          e.target.value = working.id;
          ctx.toast(err.message, "error");
        }
      };
      root.querySelector("#new-price")?.addEventListener("click", async () => {
        try {
          savedFirst();
          const r = await ctx.api(`/api/studio/courses/${id}/prices`, {
            method: "POST",
            body: { mode },
          });
          ctx.navigate(href(id, mode, r.priceVersion.id));
          ctx.reload();
        } catch (e) {
          ctx.toast(e.message, "error");
        }
      });
      root.querySelectorAll("[data-price-action]").forEach(
        (b) =>
          (b.onclick = () => {
            try {
              savedFirst();
              actionDialog(id, working, b.dataset.priceAction, ctx);
            } catch (e) {
              ctx.toast(e.message, "error");
            }
          }),
      );
      root.querySelector("#rebase-price")?.addEventListener("click", () => {
        ctx.modal(
          `<h2>기준 상품 다시 비교</h2><p>기존 기준: ${esc(working.courseSnapshot.title)} · v${working.courseVersion}</p><p>현재 상품: ${esc(course.title)} · v${course.version}</p><p>작성한 금액은 자동 변경하지 않습니다. 비교 후 직접 저장하고 검수를 요청해주세요.</p><form id="price-rebase" data-no-session-draft><label class="field">기준 갱신 사유<textarea name="reason" required minlength="5" maxlength="1000"></textarea></label><p class="form-error" data-error role="alert"></p><button type="submit" class="btn btn-primary">현재 상품을 기준으로 연결</button></form>`,
          (dialog) =>
            bindForm(dialog.querySelector("form"), async (d) => {
              const r = await ctx.api(
                `/api/studio/courses/${id}/prices/${working.id}`,
                {
                  method: "POST",
                  body: {
                    version: working.version,
                    courseVersion: course.version,
                    action: "rebase",
                    reason: d.reason,
                  },
                },
              );
              working = r.priceVersion;
              root.querySelector("#price-row-version").textContent =
                `v${working.version} · 기준 상품 v${working.courseVersion}`;
              if (form)
                form.dataset.recoveryVersion = `${working.id}:${working.version}:${course.version}:${mode}`;
              dialog.close();
              ctx.toast(
                "기준을 연결했습니다. 작성 내용을 확인하고 직접 저장해주세요.",
              );
            }),
        );
      });
      bindForm("#sale-control", async (d) => {
        savedFirst();
        await ctx.api(`/api/studio/courses/${id}/sale-control`, {
          method: "POST",
          body: {
            mode,
            version: controls.version,
            action: controls.state === "paused" ? "resume" : "pause",
            reason: d.reason,
          },
        });
        ctx.toast("판매 상태를 반영했습니다.");
        ctx.reload();
      });
    },
  };
}
function actionDialog(id, p, action, ctx) {
  const label = {
    submit: "가격 검수 요청",
    publish: "판매 가격 발행",
    reject: "가격 수정 요청",
    withdraw_submission: "검수 요청 회수",
    withdraw: "이 가격 철회",
    cancel: "작성 중단",
  }[action];
  ctx.modal(
    `<h2>${esc(label)}</h2>${action === "publish" ? "<p>승인한 실제 가격의 근거를 입력합니다. 상품 제공 문서에 이 가격을 연결해야 새 주문을 준비할 수 있습니다.</p>" : action === "withdraw" ? "<p>새 주문에 이 가격을 적용하지 않습니다. 이전 가격으로 자동 복귀하지 않으며, 과거 주문과 이미 시작한 결제 결과 확인은 보존합니다.</p>" : ""}<form id="price-action" data-no-session-draft>${action === "publish" ? '<label class="field">가격 승인 근거<input name="approvalReference" required minlength="5" maxlength="2000"/></label>' : ""}<label class="field">처리 사유·의견<textarea name="reason" required minlength="5" maxlength="1000" rows="3"></textarea></label><p class="form-error" data-error role="alert"></p><button class="btn ${action === "withdraw" ? "btn-danger" : "btn-primary"}" type="submit">${esc(label)}</button></form>`,
    (dialog) =>
      bindForm(dialog.querySelector("form"), async (d) => {
        await ctx.api(`/api/studio/courses/${id}/prices/${p.id}`, {
          method: "POST",
          body: { version: p.version, action, ...d },
        });
        dialog.close();
        ctx.reload();
      }),
  );
}
export function bindPriceSnapshots(ctx, root = document) {
  root.querySelectorAll("[data-order-price]").forEach(
    (b) =>
      (b.onclick = async () => {
        b.disabled = true;
        try {
          const { priceSnapshot: s } = await ctx.api(
            `/api/checkout/orders/${b.dataset.orderPrice}/price`,
          );
          ctx.modal(
            s.recorded
              ? `<h2>주문 당시 가격</h2><p>${esc(s.payload.title)}</p><strong class="p-price-amount">${amountLabel(s.payload.amount)}</strong><p>${s.payload.edition ? `가격 ${s.payload.edition}판` : "파일럿 예시 가격"} · ${modeLabel(s.payload.mode)} · 기록 ${date(s.sealedAt)}</p><p>${esc(period(s.payload))}</p>`
              : "<h2>당시 가격 버전은 미수집 상태입니다.</h2><p>기존 주문 금액과 내역은 보존됩니다. 현재 가격을 과거 주문에 소급하지 않습니다.</p>",
          );
        } catch (e) {
          ctx.toast(e.message, "error");
        } finally {
          b.disabled = false;
        }
      }),
  );
}
