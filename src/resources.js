import { esc } from "./ui.js";
import {
  head,
  empty,
  authGate,
  bindForm,
  date,
  tag,
  pager,
  opsNav,
} from "./platform-ui.js";
export const resourceStates = {
  pending: "검수 전",
  approved: "연결 가능",
  rejected: "검수 반려",
  withdrawn: "제공 철회",
};
export const resourceSize = (n) =>
  n >= 1048576 ? (n / 1048576).toFixed(1) + " MB" : Math.ceil(n / 1024) + " KB";
const mimeFor = (name) =>
  ({ pdf: "application/pdf", txt: "text/plain", json: "application/json" })[
    name.split(".").pop().toLowerCase()
  ];
export async function renderResources(path, params, ctx) {
  const match = path.match(/^\/studio\/course\/([a-z0-9-]+)\/resources$/);
  if (!match) return null;
  if (!ctx.user) return authGate(ctx, "담당 강의의 학습 자료를 관리하세요.");
  if (ctx.user.role !== "admin" && !ctx.user.roles?.includes("coach"))
    return {
      html: `<section class="container p-page">${empty("자료 관리 권한이 필요합니다.", "담당 코치와 관리자만 관리할 수 있습니다.")}</section>`,
    };
  const cid = match[1],
    admin = ctx.user.role === "admin";
  const [courseData, data] = await Promise.all([
    ctx.api(`/api/studio/courses/${cid}`),
    ctx.api(`/api/studio/courses/${cid}/resources?${params}`),
  ]);
  const selected = params.get("resource");
  let exact = selected
    ? data.resources.find((r) => r.id === selected) || null
    : null;
  if (selected && !exact)
    exact = (await ctx.api(`/api/studio/courses/${cid}/resources/${selected}`))
      .resource;
  const rows = [
    ...(exact && !data.resources.some((r) => r.id === exact.id) ? [exact] : []),
    ...data.resources,
  ];
  return {
    title: courseData.course.title + " · 학습 자료",
    html: `<section class="container p-page" id="resource-library"><a class="back-link" href="#/studio/course/${esc(cid)}">← 강의 스튜디오</a>${head("LEARNING RESOURCE LIBRARY", courseData.course.title, "업로드한 원본을 보존하고, 검수한 자료만 공개 차시에 연결합니다.")}${opsNav(ctx, "courses")}<div class="p-form-actions"><a class="btn btn-outline btn-sm" href="#/studio/course/${esc(cid)}/curriculum">커리큘럼 구성 ↗</a></div><div class="p-resource-library"><section class="p-panel"><h2>새 학습 자료</h2><p class="p-muted">PDF·UTF-8 TXT·JSON, 파일별 10MB. 문서나 레인지 노트를 비공개로 업로드합니다. 기존 파일의 교체는 새 자료로 등록합니다.</p><form id="resource-upload" data-no-session-draft><label class="field">학습 자료 파일<input name="file" type="file" accept=".pdf,.txt,.json,application/pdf,text/plain,application/json" required/></label><p class="form-error" data-error role="alert"></p><button class="btn btn-primary" type="submit">검수 전 자료로 업로드</button><p class="p-muted" id="resource-upload-status" role="status"></p></form><div class="p-notice amber"><strong>공개 전 운영 검수</strong><p>파일 형식 확인과 실제 내용·권리·보안 검수는 별개입니다. 관리자가 파일을 내려받아 검토하고 근거를 등록해야 공개 차시에 연결할 수 있습니다.</p></div></section><section><form id="resource-filter" class="p-filter-bar"><label>자료 상태<select name="status"><option value="">전체 상태</option>${Object.entries(
      resourceStates,
    )
      .map(
        ([s, label]) =>
          `<option value="${s}" ${params.get("status") === s ? "selected" : ""}>${label}</option>`,
      )
      .join(
        "",
      )}</select></label><button class="btn btn-outline btn-sm" type="submit">조회</button><small class="p-muted">${data.total}개 자료</small></form>${rows.map((r) => `<article class="p-panel p-resource-card" data-resource-card="${esc(r.id)}"><div class="p-inline-heading"><h2>${esc(r.filename)}</h2>${tag(resourceStates[r.status], r.status === "approved" ? "mint" : "amber")}</div><p class="p-muted">${resourceSize(r.size)} · ${esc(r.mimeType)} · ${date(r.createdAt)} · v${r.version}</p><p class="p-policy-hash">SHA-256 ${esc(r.sha256)}</p>${r.reviewNote ? `<p class="p-notice">${esc(r.reviewNote)}</p>` : ""}${admin && r.approvalReference ? `<p class="p-policy-hash">검수 근거: ${esc(r.approvalReference)}</p>` : ""}<div class="p-form-actions"><a class="btn btn-outline btn-sm" href="/api/studio/courses/${esc(cid)}/resources/${esc(r.id)}/download">원본 내려받아 확인</a>${admin && r.status === "pending" ? `<button class="btn btn-primary btn-sm" data-resource-action="approve" data-resource-id="${esc(r.id)}">자료 검수 승인</button><button class="btn btn-outline btn-sm" data-resource-action="reject" data-resource-id="${esc(r.id)}">자료 반려</button>` : admin && r.status === "approved" ? `<button class="btn btn-outline btn-sm" data-resource-action="withdraw" data-resource-id="${esc(r.id)}">새 다운로드 중지</button>` : ""}</div>${r.status === "approved" ? '<p class="form-note">차시 편집 또는 공개 차시 변경안에서 선택해 연결합니다. 업로드/승인만으로 고객에게 공개하지 않습니다.</p>' : r.status === "withdrawn" ? '<p class="form-note">기존 차시/이력의 참조를 유지하고 새 다운로드를 차단합니다. 다시 제공할 자료는 새로 업로드·검수해주세요.</p>' : ""}</article>`).join("") || empty("등록된 학습 자료가 없습니다.", "파일을 업로드하면 검수 전 상태로 보관합니다.")}${pager(`/studio/course/${cid}/resources`, params, data.page, data.total)}</section></div></section>`,
    bind() {
      bindForm("#resource-filter", (d) =>
        ctx.navigate(
          `/studio/course/${cid}/resources?${new URLSearchParams(d)}`,
        ),
      );
      bindForm("#resource-upload", async (_, form) => {
        const file = form.querySelector("[type=file]").files[0];
        if (!file) throw new Error("학습 자료 파일을 선택해주세요.");
        const mime = mimeFor(file.name);
        if (!mime || !file.size || file.size > data.limits.maxBytes)
          throw new Error("PDF·TXT·JSON 파일을 10MB 이내로 선택해주세요.");
        document.querySelector("#resource-upload-status").textContent =
          "비공개 자료를 업로드하고 있습니다.";
        const r = await ctx.api(`/api/studio/courses/${cid}/resources`, {
          method: "POST",
          rawBody: file,
          headers: {
            "Content-Type": mime,
            "X-File-Name": encodeURIComponent(file.name),
          },
        });
        ctx.toast(
          "검수 전 자료로 업로드했습니다. 공개 차시 연결 전 검수를 요청해주세요.",
        );
        ctx.navigate(
          `/studio/course/${cid}/resources?resource=${r.resource.id}`,
        );
        ctx.reload();
      });
      document.querySelectorAll("[data-resource-action]").forEach(
        (b) =>
          (b.onclick = () => {
            const r = rows.find((r) => r.id === b.dataset.resourceId),
              action = b.dataset.resourceAction,
              label = {
                approve: "자료 검수 승인",
                reject: "자료 반려",
                withdraw: "새 다운로드 중지",
              }[action];
            ctx.modal(
              `<h2>${label}</h2><p>${esc(r.filename)} · ${resourceSize(r.size)}</p>${action === "approve" ? "<p>원본 내용·권리와 제공 적합성을 확인한 근거를 입력합니다. 형식 검사만으로 실제 자료 검수를 대신하지 않습니다.</p>" : action === "withdraw" ? "<p>현재 연결과 과거 이력은 보존하고, 새 다운로드와 새 차시 연결을 중지합니다.</p>" : ""}<form id="resource-review" data-no-session-draft>${action === "approve" ? '<label class="field">자료 검수 근거<textarea name="approvalReference" required minlength="5" maxlength="2000" rows="2"></textarea></label>' : ""}<label class="field">처리 사유<textarea name="reason" required minlength="5" maxlength="2000" rows="3"></textarea></label><p class="form-error" data-error role="alert"></p><button class="btn btn-primary" type="submit">${label}</button></form>`,
              (dialog) =>
                bindForm(dialog.querySelector("form"), async (d) => {
                  await ctx.api(
                    `/api/studio/courses/${cid}/resources/${r.id}`,
                    {
                      method: "POST",
                      body: { version: r.version, action, ...d },
                    },
                  );
                  dialog.close();
                  ctx.reload();
                }),
            );
          }),
      );
    },
  };
}

export function lessonResourceEditor(course, lesson, locked) {
  const refs = new Map((lesson?.resources || []).map((r) => [r.assetId, r]));
  return `<section class="p-lesson-resources"><div class="p-inline-heading"><h2>차시 학습 자료</h2><a class="text-link" target="_blank" rel="noopener" href="#/studio/course/${esc(course.id)}/resources">자료 업로드·검수 ↗</a></div><p class="p-muted">공개 차시에는 검수한 자료만 연결합니다. 공개 레슨의 파일도 현재 수강권이 있어야 내려받을 수 있습니다. 자료를 추가한 뒤 현재 입력을 저장하고 다시 조회해주세요.</p>${(course.learningResources || []).map((r) => `<div class="p-resource-choice" data-resource-choice="${esc(r.id)}"><label class="p-checkbox"><input type="checkbox" name="resource_${esc(r.id)}" value="${esc(r.id)}" ${refs.has(r.id) ? "checked" : ""} ${locked || (!refs.has(r.id) && ["rejected", "withdrawn"].includes(r.status)) ? "disabled" : ""}/> ${esc(r.filename)} · ${resourceSize(r.size)} · ${esc(resourceStates[r.status])}</label><label class="field">수강 화면의 자료 이름<input name="resourceLabel_${esc(r.id)}" value="${esc(refs.get(r.id)?.label || r.filename.slice(0, 120))}" maxlength="120" ${locked ? "disabled" : ""}/></label></div>`).join("") || '<p class="form-note">등록한 자료가 없습니다. 라이브러리에서 업로드·검수한 뒤 연결할 수 있습니다.</p>'}</section>`;
}
export function readResourceChoices(form) {
  return [...form.querySelectorAll("[data-resource-choice]")]
    .filter((row) => row.querySelector("[type=checkbox]").checked)
    .map((row) => ({
      assetId: row.dataset.resourceChoice,
      label: row
        .querySelector("input[type=text],input:not([type])")
        .value.trim(),
    }));
}
export function learnerResourceList(courseId, lesson, canDownload) {
  if (!lesson.resources?.length) return "";
  return `<section class="m-learning-resources"><p class="eyebrow">LESSON RESOURCES</p><h2>이 수업의 학습 자료</h2><p>수강권과 현재 제공 상태를 확인한 뒤 원본 파일을 내려받습니다.</p>${lesson.resources.map((r) => `<article><div><strong>${esc(r.label)}</strong><small>${resourceSize(r.size)} · ${esc(r.mimeType)}</small></div>${r.available && canDownload ? `<a class="btn btn-outline btn-sm" href="/api/courses/${esc(courseId)}/lessons/${esc(lesson.id)}/resources/${esc(r.assetId)}">자료 내려받기 ↓</a>` : `<span>${r.available ? "수강 등록 후 이용" : "현재 제공하지 않는 자료"}</span>`}</article>`).join("")}</section>`;
}
