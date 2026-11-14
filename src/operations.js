import { bindPriceSnapshots } from "./pricing.js";
import { esc, icon, money } from "./ui.js";
import { bindContractSnapshots } from "./contracts.js";
import { bindOrderConditions } from "./policies.js";
import { bindRefundContexts } from "./refund-context.js";
import {
  accessNotice,
  accessLabel,
  sourceLabel,
  grantPeriod,
  toSeoulInput,
  fromSeoulInput,
} from "./access.js";
import {
  head,
  empty,
  authGate,
  bindForm,
  textBody,
  date,
  tag,
  statusLabel,
  pager,
  opsNav,
} from "./platform-ui.js";

export async function renderOperations(path, params, ctx) {
  if (path !== "/manage") return null;
  if (!ctx.user) return authGate(ctx, "운영 계정으로 접속해주세요.");
  const admin = ctx.user.role === "admin",
    moderator = admin || ctx.user.roles?.includes("moderator");
  if (!moderator)
    return {
      html: `<section class="container p-page">${empty("운영 권한이 필요합니다.", "코치는 강의 스튜디오와 질문 데스크에서 담당 강의를 관리할 수 있습니다.", `<a class="btn btn-primary" href="#/studio">강의 스튜디오</a>`)}</section>`,
    };
  const tab = params.get("tab") || (admin ? "users" : "reports");
  if (tab === "users" && admin) return users(params, ctx);
  if (tab === "audit" && admin) return audit(params, ctx);
  if (tab === "purchases" && admin) return purchases(ctx);
  return reports(ctx);
}

async function users(params, ctx) {
  const data = await ctx.api(`/api/manage/users?${params}`);
  const catalogue = (await ctx.api("/api/studio/courses")).courses;
  return {
    html: `<section class="container p-page">${head("CAMP OPERATIONS", "사용자와 학습 권한 관리.", "역할 변경·계정 정지·수강권 조정에는 사유와 운영 이력을 남깁니다.")} ${opsNav(ctx, "users")}<form id="user-search" class="p-filter-bar"><label>이름·이메일<input name="q" value="${esc(params.get("q") || "")}" placeholder="사용자 검색"/></label><button class="btn btn-outline btn-sm" type="submit">검색</button><span class="p-muted">${data.total}명</span></form><div class="p-user-list">${
      data.users
        .map(
          (user) =>
            `<article class="p-panel"><div><h2>${esc(user.name)} ${user.id === ctx.user.id ? tag("현재 계정") : ""}</h2><p>${esc(user.email)}</p><span>${date(user.createdAt)} 가입 · 수강 ${user.enrollmentCount}개</span></div><div>${tag(statusLabel(user.status), user.status === "active" ? "mint" : "amber")}${tag(user.role === "admin" ? "관리자" : "수강생")}${user.roles
              .filter((r) => r !== "admin")
              .map((r) => tag(r === "coach" ? "코치" : "커뮤니티 운영자"))
              .join(
                "",
              )}</div><button class="btn btn-outline btn-sm" data-user-detail="${esc(user.id)}">상세·수강권 관리</button></article>`,
        )
        .join("") ||
      empty("검색 결과가 없습니다.", "다른 이름이나 이메일로 찾아보세요.")
    }</div>${pager("/manage", params, data.page, data.total)}</section>`,
    bind() {
      bindForm("#user-search", (data) =>
        ctx.navigate(`/manage?tab=users&q=${encodeURIComponent(data.q)}`),
      );
      document.querySelectorAll("[data-user-detail]").forEach(
        (button) =>
          (button.onclick = async () => {
            try {
              const result = await ctx.api(
                `/api/manage/users/${button.dataset.userDetail}`,
              );
              userDialog(result, catalogue, ctx);
            } catch (e) {
              ctx.toast(e.message, "error");
            }
          }),
      );
    },
  };
}
function userDialog(result, courses, ctx) {
  const user = result.user;
  const requestId = crypto.randomUUID();
  ctx.modal(
    `<p class="eyebrow">USER OPERATIONS</p><h2>${esc(user.name)}</h2><p class="dialog-description">${esc(user.email)}</p><h3>계정 역할·상태</h3>${user.id === ctx.user.id ? '<p class="p-notice">현재 로그인한 본인의 권한·상태는 변경할 수 없습니다.</p>' : `<form id="user-settings"><div class="field-row"><label class="field">기본 권한<select name="role"><option value="student" ${user.role === "student" ? "selected" : ""}>수강생</option><option value="admin" ${user.role === "admin" ? "selected" : ""}>관리자</option></select></label><label class="field">이용 상태<select name="status"><option value="active" ${user.status === "active" ? "selected" : ""}>활성</option><option value="suspended" ${user.status === "suspended" ? "selected" : ""}>이용 정지</option></select></label></div><label class="p-checkbox"><input type="checkbox" name="coach" ${user.roles.includes("coach") ? "checked" : ""}/> 코치 · 담당 강의와 질문 관리</label><label class="p-checkbox"><input type="checkbox" name="moderator" ${user.roles.includes("moderator") ? "checked" : ""}/> 커뮤니티 운영자 · 신고와 게시물 운영</label><label class="field">변경 사유<input name="reason" minlength="5" maxlength="2000" required/></label><p class="form-note">저장 시 이 사용자의 기존 세션을 회수하며, 다시 로그인해야 합니다.</p><p class="form-error" data-error role="alert"></p><button class="btn btn-primary btn-sm" type="submit">계정 설정 저장</button></form>`}${accessManagement(result, courses)}`,
    (dialog) => {
      bindForm(dialog.querySelector("#user-settings"), async (data) => {
        await ctx.api(`/api/manage/users/${user.id}`, {
          method: "PATCH",
          body: {
            role: data.role,
            status: data.status,
            roles: [
              ...(data.coach === "on" ? ["coach"] : []),
              ...(data.moderator === "on" ? ["moderator"] : []),
            ],
            reason: data.reason,
            version: user.version,
          },
        });
        dialog.close();
        ctx.toast("계정 상태와 역할을 변경했습니다.");
        ctx.reload();
      });
      const accessForm = dialog.querySelector("#user-access");
      const syncIssue = () => {
        const revoke = accessForm.elements.status.value === "revoked";
        accessForm.querySelector("[data-issue-dates]").hidden = revoke;
        accessForm
          .querySelectorAll("[data-issue-dates] input")
          .forEach((input) => (input.disabled = revoke));
        accessForm.querySelector("[data-revoke-confirm]").hidden = !revoke;
        accessForm.elements.allConfirmed.required = revoke;
        accessForm.elements.allConfirmed.disabled = !revoke;
      };
      accessForm.elements.status.addEventListener("change", syncIssue);
      syncIssue();
      bindForm(accessForm, async (data) => {
        if (
          data.status === "revoked" &&
          !result.access.some((entry) => entry.courseId === data.courseId)
        )
          throw new Error(
            "등록된 수강 이력이 없습니다. 대상 강의를 확인해주세요.",
          );
        await ctx.api("/api/manage/access", {
          method: "POST",
          body: {
            ...data,
            userId: user.id,
            requestId: requestId,
            ...(data.status === "active"
              ? {
                  startsAt: fromSeoulInput(data.startsAt),
                  expiresAt: fromSeoulInput(data.expiresAt),
                }
              : {
                  accessVersion: result.access.find(
                    (entry) => entry.courseId === data.courseId,
                  )?.version,
                }),
          },
        });
        dialog.close();
        ctx.toast("수강권을 변경하고 이력을 남겼습니다.");
        ctx.reload();
      });
      dialog.querySelectorAll("[data-grant-form]").forEach((form) => {
        const sync = () => {
          const period = form.elements.action.value === "period";
          const dates = form.querySelector("[data-period-fields]");
          if (dates) {
            dates.hidden = !period;
            dates
              .querySelectorAll("input")
              .forEach((input) => (input.disabled = !period));
          }
        };
        form.elements.action.addEventListener("change", sync);
        sync();
        bindForm(form, async (data) => {
          await ctx.api(`/api/manage/access/${form.dataset.grantForm}`, {
            method: "PATCH",
            body: {
              action: data.action,
              version: Number(form.dataset.grantVersion),
              reason: data.reason,
              ...(data.action === "period"
                ? {
                    startsAt: fromSeoulInput(data.startsAt),
                    expiresAt: fromSeoulInput(data.expiresAt),
                  }
                : {}),
            },
          });
          dialog.close();
          ctx.toast("해당 출처의 수강권을 변경했습니다.");
          ctx.reload();
        });
      });
    },
  );
}
function periodFields(start = "", end = "", marker = "data-period-fields") {
  return `<div ${marker}><div class="field-row"><label class="field">시작 · 한국 시간<input type="datetime-local" step="1" name="startsAt" value="${esc(toSeoulInput(start))}"/></label><label class="field">종료 · 한국 시간<input type="datetime-local" step="1" name="expiresAt" value="${esc(toSeoulInput(end))}"/></label></div><p class="form-note">시작 시각부터, 종료 시각 직전까지 유효합니다. 종료를 비우면 기한이 없습니다. 입력 시간대는 UTC+09:00입니다.</p></div>`;
}

function accessManagement(result, courses) {
  return `<h3 class="p-field-spaced">학습 이력과 수강권</h3><p class="form-note">수강 이력은 유지하고 출처별 수강권을 독립적으로 관리합니다. 만료·회수 후에도 진도와 질문은 삭제되지 않습니다.</p><div class="p-access-list">${result.access.map((entry) => `<section class="p-access-course"><h4>${esc(entry.title)}</h4>${accessNotice(entry)}<div class="p-grant-actions">${entry.grants.map((grant) => `<details class="p-grant-detail"><summary><span>${esc(sourceLabel(grant.source))}<small>${esc(grantPeriod(grant))}</small></span>${tag(accessLabel(grant.status), grant.status === "active" ? "mint" : "amber")}</summary><div class="p-grant-detail-body">${grant.reason ? `<p>발급 사유: ${esc(grant.reason)}</p>` : ""}${grant.revocationReason ? `<p>회수 사유: ${esc(grant.revocationReason)}</p>` : ""}<p class="p-muted">수강권 ${esc(grant.id)} · 버전 ${grant.version}</p>${grant.status === "revoked" ? '<p class="form-note">회수된 기록은 유지됩니다. 다시 제공하려면 아래에서 새 수강권을 발급해주세요.</p>' : `<form data-grant-form="${esc(grant.id)}" data-grant-version="${grant.version}"><label class="field">해당 출처 처리<select name="action">${grant.source === "manual" ? '<option value="period">기간 변경</option>' : ""}<option value="revoke">이 수강권만 회수</option></select></label>${grant.source === "manual" ? periodFields(grant.startsAt, grant.expiresAt) : ""}<label class="field">변경 사유<input name="reason" required minlength="5" maxlength="2000"/></label><p class="form-note">다른 출처의 유효한 수강권은 유지합니다. 결제 취소는 주문 관리에서 별도로 처리합니다.</p><p class="form-error" data-error role="alert"></p><button class="btn btn-outline btn-sm" type="submit">해당 수강권 변경</button></form>`}</div></details>`).join("")}</div></section>`).join("") || '<p class="p-muted">등록된 학습 이력이 없습니다.</p>'}</div><h3 class="p-field-spaced">새 발급 또는 전체 회수</h3><form id="user-access"><label class="field">대상 강의<select name="courseId">${courses
    .filter((course) => ["published", "archived"].includes(course.status))
    .map(
      (course) =>
        `<option value="${esc(course.id)}">${esc(course.title)}</option>`,
    )
    .join(
      "",
    )}</select></label><label class="field">처리 방식<select name="status"><option value="active">새 운영자 수강권 발급</option><option value="revoked">해당 강의의 모든 출처 회수</option></select></label>${periodFields("", "", "data-issue-dates")}<p class="form-note">새 발급의 시작을 비우면 지금부터 유효합니다. 기존 발급 기록을 덮어쓰거나 상품 기본 기간을 바꾸지 않습니다.</p><label class="p-checkbox" data-revoke-confirm hidden><input type="checkbox" name="allConfirmed"/> 선택한 강의의 결제·무료·파일럿·운영자 수강권을 모두 회수합니다.</label><label class="field">처리 사유<input name="reason" required minlength="5" maxlength="2000"/></label><p class="form-error" data-error role="alert"></p><button class="btn btn-outline btn-sm" type="submit">수강권 처리 저장</button></form>`;
}

async function audit(params, ctx) {
  const data = await ctx.api(`/api/manage/audit?${params}`);
  return {
    html: `<section class="container p-page">${head("AUDIT TRAIL", "중요한 변경은, 근거와 함께.", "발행·권한·커뮤니티 운영·결제 처리의 기록을 확인합니다.")} ${opsNav(ctx, "audit")}<div class="p-audit-list">${
      data.events
        .map(
          (event) =>
            `<details class="p-panel"><summary><div>${tag(event.action)}<strong>${esc(event.actor || "시스템 / 삭제된 계정")}</strong></div><span>${date(event.createdAt)}</span></summary><dl><div><dt>대상</dt><dd>${esc(event.targetType)} · ${esc(event.targetId)}</dd></div>${Object.entries(
              event.details,
            )
              .map(
                ([key, value]) =>
                  `<div><dt>${esc(key)}</dt><dd>${value !== null && typeof value === "object" ? `<pre class="p-audit-value">${esc(JSON.stringify(value, null, 2))}</pre>` : esc(String(value))}</dd></div>`,
              )
              .join("")}</dl></details>`,
        )
        .join("") ||
      empty(
        "운영 이력이 없습니다.",
        "강의·계정·결제에 중요한 변경이 생기면 기록됩니다.",
      )
    }</div>${pager("/manage", params, data.page, data.total)}</section>`,
  };
}
async function reports(ctx) {
  const data = await ctx.api("/api/manage/reports");
  return {
    html: `<section class="container p-page">${head("COMMUNITY OPERATIONS", "신뢰를 지키는 운영.", "신고 내용을 읽고, 처리 사유와 함께 결과를 남깁니다.")} ${opsNav(ctx, "reports")}<div class="p-report-list">${data.reports.map((report) => `<article class="p-panel"><div class="p-inline-heading"><h2><a href="#/community/post/${esc(report.postId)}">${esc(report.title)}</a></h2>${tag(report.status === "open" ? "검토 대기" : "처리 완료", report.status === "open" ? "amber" : "mint")}</div><p class="p-muted">신고자 ${esc(report.reporter)} · ${date(report.createdAt)}</p><div class="p-notice"><strong>신고 내용</strong><p>${esc(report.reason)}</p></div><details><summary>게시글 원문 확인</summary><div class="p-user-body">${textBody(report.body)}</div></details>${report.status === "open" ? `<form data-report-form="${esc(report.id)}">${report.moderation?.sourceChanged ? '<p class="p-notice">신고 후 게시글 내용이 변경되었습니다. 현재 원문을 확인하고 처리해주세요.</p><label class="p-checkbox"><input type="checkbox" required>변경된 원문을 확인했습니다.</label>' : ""}<div class="field-row"><label class="field">처리<select name="action"><option value="dismiss">조치 없이 종결</option><option value="hide">게시글 숨김 후 종결</option></select></label><label class="field">처리 사유<input name="reason" required minlength="5" maxlength="2000"/></label></div><p class="form-error" data-error role="alert"></p><button class="btn btn-primary btn-sm" type="submit">처리 결과 저장</button></form>` : `<p class="p-notice">처리 답변: ${esc(report.resolution)}</p>`}</article>`).join("") || empty("접수된 신고가 없습니다.", "신고가 들어오면 내용과 처리 상태가 이곳에 표시됩니다.")}</div></section>`,
    bind() {
      document.querySelectorAll("[data-report-form]").forEach((form) => {
        const report = data.reports.find(
          (r) => r.id === form.dataset.reportForm,
        );
        bindForm(form, async (data) => {
          await ctx.api(`/api/manage/reports/${form.dataset.reportForm}`, {
            method: "PATCH",
            body: {
              ...data,
              ...(report.moderation
                ? {
                    expectedTargetVersion: report.moderation.target.version,
                    expectedContentHash: report.moderation.target.contentHash,
                    policyId: report.moderation.policyId,
                  }
                : {}),
            },
          });
          ctx.toast("신고 처리 결과를 저장했습니다.");
          ctx.reload();
        });
      });
    },
  };
}
async function purchases(ctx) {
  const data = await ctx.api("/api/manage/purchases");
  return {
    html: `<section class="container p-page">${head("COMMERCE OPERATIONS", "주문 확인부터 전액 취소까지.", "결제사 처리 결과를 검증한 뒤 수강권과 주문 상태에 반영합니다.")} ${opsNav(ctx, "purchases")}<div class="p-purchase-list">${data.orders.map((order) => `<article class="p-panel"><div class="p-inline-heading"><h2>${esc(order.title)}</h2>${tag(statusLabel(order.status), order.status === "paid" ? "mint" : "")}</div><p class="p-muted">${esc(order.buyer)} · ${esc(order.buyerEmail)} · ${date(order.createdAt)}</p><p class="p-muted">${esc(order.id)}${order.mode === "test" ? " · 테스트 결제" : ""}</p><div class="p-purchase-total">${money(order.amount)}</div>${order.refundRequest ? `<div class="p-notice"><strong>사용자 취소 문의 · ${{ requested: "대기", approved: "취소 완료", rejected: "답변 완료" }[order.refundRequest.status]}</strong><p>${esc(order.refundRequest.reason)}</p>${order.refundRequest.resolution ? `<p>${esc(order.refundRequest.resolution)}</p>` : ""}</div>` : ""}<div class="p-form-actions"><button class="btn btn-outline btn-sm" data-order-conditions="${esc(order.id)}">구매 당시 조건</button><button class="btn btn-outline btn-sm" data-order-contract="${esc(order.id)}">적용 계약 기록</button><button class="btn btn-outline btn-sm" data-order-price="${esc(order.id)}">당시 가격</button><button class="btn btn-outline btn-sm" data-refund-context="${esc(order.id)}">환불 검토 근거</button></div><a class="btn btn-primary btn-sm" href="#/refund/${esc(order.id)}?desk=1">환불 검토·처리 이력 →</a> <a class="btn btn-outline btn-sm" href="#/manage/reconciliation/order/${esc(order.id)}">결제 대사·조회 기록 →</a></article>`).join("") || empty("결제 주문이 없습니다.", "파일럿 신청은 기존 코칭·리뷰 접수 관리의 수강 신청 탭에서 확인합니다.", `<a class="btn btn-outline" href="#/admin?tab=orders">파일럿 수강 신청</a>`)}</div></section>`,
    bind() {
      bindOrderConditions(ctx, document, true);
      bindContractSnapshots(ctx);
      bindPriceSnapshots(ctx);
      bindRefundContexts(ctx, document, true);
    },
  };
}
