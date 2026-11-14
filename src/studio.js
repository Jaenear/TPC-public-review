import { renderLessonEditor } from "./lesson-editor.js";
import { esc, icon, money } from "./ui.js";
import { priceLabel } from "./price-model.js";
import {
  head,
  empty,
  authGate,
  bindForm,
  date,
  tag,
  statusLabel,
  opsNav,
} from "./platform-ui.js";

export async function renderStudio(path, params, ctx) {
  if (path !== "/studio" && !path.startsWith("/studio/course/")) return null;
  if (!ctx.user) return authGate(ctx, "코치 스튜디오에 입장해주세요.");
  if (ctx.user.role !== "admin" && !ctx.user.roles?.includes("coach"))
    return {
      html: `<section class="container p-page">${empty("코치 또는 관리자 계정이 필요합니다.", "운영자가 코치 역할과 담당 강의를 지정하면 이용할 수 있습니다.")}</section>`,
    };
  if (path === "/studio") return studioList(ctx);
  const match = path.match(
    /^\/studio\/course\/([a-z0-9-]+)(?:\/lesson\/([a-z0-9-]+|new))?$/,
  );
  if (!match) return null;
  const course = (await ctx.api(`/api/studio/courses/${match[1]}`)).course;
  return match[2]
    ? renderLessonEditor(course, match[2], ctx)
    : courseEditor(course, ctx);
}
function metadataFields(course = {}, admin = false, users = []) {
  return `<label class="field">강의명<input name="title" required minlength="2" maxlength="100" value="${esc(course.title || "")}"/></label><label class="field">한 줄 소개<input name="subtitle" maxlength="200" value="${esc(course.subtitle || "")}"/></label><div class="field-row"><label class="field">주제<select name="category">${["프리플랍", "라이브 캐시", "토너먼트", "멘탈", "기타"].map((v) => `<option ${v === course.category ? "selected" : ""}>${v}</option>`).join("")}</select></label><label class="field">난이도<select name="level">${["입문", "중급", "심화"].map((v) => `<option ${v === course.level ? "selected" : ""}>${v}</option>`).join("")}</select></label></div><label class="field">${course.pilot === false ? "기획 제안 금액 (판매 가격은 별도 검수)" : "파일럿 예시 가격 (원, 무료는 0)"}<input name="price" type="number" min="0" max="10000000" step="1" required value="${course.price || 0}"/></label><label class="field">강의 소개<textarea name="description" rows="6" minlength="10" maxlength="10000" required>${esc(course.description || "")}</textarea></label>${
    admin
      ? `<label class="field">담당 코치<select name="instructorUserId"><option value="">미지정</option>${course.instructorUserId && !users.some((u) => u.id === course.instructorUserId) ? `<option value="${esc(course.instructorUserId)}" selected>현재 담당자 · ${esc(course.instructorUserId)}</option>` : ""}${users
          .filter((u) => u.role === "admin" || u.roles?.includes("coach"))
          .map(
            (u) =>
              `<option value="${esc(u.id)}" ${u.id === course.instructorUserId ? "selected" : ""}>${esc(u.name)} · ${esc(u.email)}</option>`,
          )
          .join(
            "",
          )}</select></label><label class="p-checkbox"><input type="checkbox" name="pilot" ${course.pilot !== false ? "checked" : ""}/> 파일럿 강의 (예시 가격·실제 판매 전 검수)</label>`
      : ""
  }`;
}
async function studioList(ctx) {
  const data = await ctx.api("/api/studio/courses");
  return {
    html: `<section class="container p-page">${head("TPC STUDIO", "강의를 만들고, 배움을 연결하세요.", "초안 작성, 커리큘럼 구성, 미디어와 발행 상태를 관리합니다.", `<button class="btn btn-primary" id="new-course">새 강의 만들기 ${icon("external", 17)}</button>`)}${opsNav(ctx, "courses")}<div class="p-course-management">${data.courses.map((course) => `<a class="p-panel" href="#/studio/course/${esc(course.id)}"><div>${tag(statusLabel(course.status), course.status === "published" ? "mint" : "")} ${course.pilot ? tag("파일럿") : ""}</div><h2>${esc(course.title)}</h2><p>${esc(course.subtitle)}</p><div class="p-management-meta"><span>${course.lessons.length}개 차시 · ${priceLabel({ ...course, price: course.pricing?.amount ?? null })}</span><span>v${course.version} ${icon("external", 16)}</span></div></a>`).join("") || empty("첫 강의를 만들어보세요.", "초안을 작성하고 커리큘럼을 완성한 뒤 발행을 요청합니다.")}</div></section>`,
    bind() {
      document.querySelector("#new-course").onclick = () =>
        ctx.modal(
          `<p class="eyebrow">NEW COURSE</p><h2>새 강의 초안</h2><form id="new-course-form">${metadataFields()}<p class="form-error" data-error role="alert"></p><button class="btn btn-primary btn-block" type="submit">초안 생성</button></form>`,
          (dialog) =>
            bindForm(dialog.querySelector("form"), async (data) => {
              const result = await ctx.api("/api/studio/courses", {
                method: "POST",
                body: { ...data, price: Number(data.price), status: "draft" },
              });
              dialog.close();
              ctx.navigate(`/studio/course/${result.course.id}`);
            }),
        );
    },
  };
}
async function courseEditor(course, ctx) {
  const admin = ctx.user.role === "admin",
    users = [];
  if (admin) {
    let page = 1,
      result;
    do {
      result = await ctx.api(`/api/studio/instructors?page=${page++}`);
      users.push(...result.users);
    } while (users.length < result.total);
  }
  const locked = ["published", "archived"].includes(course.status) && !admin;
  return {
    html: `<section class="container p-page"><a class="back-link" href="#/studio">← 강의 스튜디오</a>${head("COURSE STUDIO", course.title, "발행 상태와 차시 ID는 학습·주문·질문 기록에 연결됩니다.")} ${opsNav(ctx, "courses")}<div class="p-form-actions"><a class="btn btn-primary" href="#/studio/course/${esc(course.id)}/publication">품질 점검·발행 검수 ↗</a><a class="btn btn-outline" href="#/studio/course/${esc(course.id)}/curriculum">커리큘럼 일괄 구성 ↗</a><a class="btn btn-outline" href="#/studio/course/${esc(course.id)}/media">영상 원본·전송 관리 ↗</a><a class="btn btn-outline" href="#/studio/course/${esc(course.id)}/resources">학습 자료 ↗</a><a class="btn btn-outline" href="#/studio/course/${esc(course.id)}/pricing">판매 가격·준비 관리 ↗</a></div>${locked ? '<p class="p-notice">발행된 강의입니다. 내용 변경은 관리자에게 검수를 요청해주세요. 담당 강의의 Q/A는 질문 데스크에서 계속 관리할 수 있습니다.</p>' : ""}<div class="p-studio-layout"><form class="p-panel" id="course-metadata" data-session-draft data-recovery-version="${esc(course.version)}"><fieldset class="p-editor-fields" ${locked ? "disabled" : ""}><h2>강의 정보</h2>${metadataFields(course, admin, users)}<label class="field">상태<select name="status">${(admin && course.status === "published" ? ["published", "archived"] : [course.status]).map((v) => `<option value="${v}" ${v === course.status ? "selected" : ""}>${statusLabel(v)}</option>`).join("")}</select></label><p class="form-note">정식 판매 가격은 가격 검수 화면에서 별도로 발행합니다. 여기의 제안 금액은 실제 판매 가격을 바꾸지 않습니다. 최초 발행·보관 후 재발행은 품질 점검 화면에서 관리자 검수를 거칩니다. 보관한 강의의 기존 수강권은 유지됩니다. 변경은 버전 충돌을 검사합니다.</p><p class="form-error" data-error role="alert"></p><button class="btn btn-primary btn-block" type="submit">강의 정보 저장 · v${course.version}</button></fieldset></form><div><section class="p-panel p-curriculum-editor"><div class="p-inline-heading"><h2>커리큘럼</h2><button class="btn btn-outline btn-sm" id="new-section" ${locked ? "disabled" : ""}>섹션 추가</button></div>${course.sections
      .map(
        (section) =>
          `<div class="p-section-block"><h3>${esc(section.title)}</h3>${
            course.lessons
              .filter((l) => l.sectionId === section.id)
              .map(
                (lesson, i) =>
                  `<a href="#/studio/course/${esc(course.id)}/lesson/${esc(lesson.id)}"><span>${String(i + 1).padStart(2, "0")}</span><div><strong>${esc(lesson.title)}</strong><small>${lesson.videoAssetId ? "영상 + 텍스트" : "텍스트"} · ${lesson.duration || "시간 미입력"}</small></div>${tag(statusLabel(lesson.status))}${lesson.free ? tag("미리보기") : ""}${icon("chevron", 16)}</a>`,
              )
              .join("") || '<p class="p-muted">아직 차시가 없습니다.</p>'
          }</div>`,
      )
      .join(
        "",
      )}${locked ? "" : `<a class="btn btn-primary btn-block" href="#/studio/course/${esc(course.id)}/lesson/new">차시 추가 ${icon("external", 17)}</a>`}</section><section class="p-panel p-studio-info"><h2>수강생과 이어지는 운영</h2><p>등록한 강의는 검수 후 라이브러리에 노출됩니다. 유료 차시와 영상은 활성 수강권으로 보호됩니다.</p><a class="text-link" href="#/qa?desk=1&courseId=${esc(course.id)}">이 강의 질문 데스크 ${icon("arrow", 16)}</a>${course.status === "published" ? `<a class="text-link" href="#/course/${esc(course.id)}">공개 상세 보기 ${icon("external", 16)}</a>` : ""}</section></div></div></section>`,
    bind() {
      bindForm("#course-metadata", async (data) => {
        await ctx.api(`/api/studio/courses/${course.id}`, {
          method: "PATCH",
          body: {
            ...data,
            price: Number(data.price),
            pilot: admin ? data.pilot === "on" : true,
            version: course.version,
          },
        });
        await ctx.refreshCatalog();
        ctx.toast("강의 정보를 저장했습니다.");
        ctx.reload();
      });
      document.querySelector("#new-section").onclick = () =>
        ctx.modal(
          '<h2>새 커리큘럼 섹션</h2><form id="section-form"><label class="field">섹션 제목<input name="title" required minlength="2" maxlength="120"/></label><p class="form-error" data-error role="alert"></p><button class="btn btn-primary" type="submit">섹션 추가</button></form>',
          (dialog) =>
            bindForm(dialog.querySelector("form"), async (data) => {
              await ctx.api(`/api/studio/courses/${course.id}/sections`, {
                method: "POST",
                body: { ...data, version: course.version },
              });
              dialog.close();
              ctx.reload();
            }),
        );
    },
  };
}
