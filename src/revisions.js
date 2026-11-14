import { esc, icon } from "./ui.js";
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
import { renderLessonEditor } from "./lesson-editor.js";

const actionLabel = {
  created: "초안 생성",
  saved: "변경안 저장",
  submitted: "검수 요청",
  withdrawn: "검수 요청 회수",
  rebased: "기준 버전 갱신",
  approved: "검수 승인·반영",
  rejected: "반려",
  cancelled: "작성 중단",
};
const sourceLabel = {
  baseline: "기존 차시 기준선",
  direct: "운영자 직접 저장",
  revision: "변경안 승인 반영",
};
const revisionHref = (id) => `#/studio/revisions/${esc(id)}`;
const historyHref = (course, lesson) =>
  `#/studio/course/${esc(course)}/lesson/${esc(lesson)}/history`;

export async function renderRevisions(path, params, ctx) {
  const history = path.match(
    /^\/studio\/course\/([a-z0-9-]+)\/lesson\/([a-z0-9-]+)\/history$/,
  );
  if (
    path !== "/studio/revisions" &&
    !path.startsWith("/studio/revisions/") &&
    !history
  )
    return null;
  if (!ctx.user) return authGate(ctx, "강의 변경안과 발행 이력을 확인하세요.");
  if (ctx.user.role !== "admin" && !ctx.user.roles?.includes("coach"))
    return {
      html: `<section class="container p-page">${empty("코치 또는 관리자 계정이 필요합니다.", "담당 강의의 변경안과 발행 이력만 확인할 수 있습니다.")}</section>`,
    };
  if (history) return historyPage(history[1], history[2], ctx);
  if (path === "/studio/revisions") return queue(params, ctx);
  const match = path.match(/^\/studio\/revisions\/([a-z0-9_]+)$/);
  return match ? detail(match[1], ctx) : null;
}

async function queue(params, ctx) {
  const query = new URLSearchParams(params);
  if (!query.has("status")) query.set("status", "active");
  const data = await ctx.api(`/api/studio/revisions?${query}`);
  return {
    html: `<section class="container p-page">${head("CONTENT REVIEW", "수업을 멈추지 않는 콘텐츠 개선.", "공개 차시를 유지하며 변경안을 작성하고, 검수 결과와 반영 버전을 기록합니다.")} ${opsNav(ctx, "revisions")}<form id="revision-filter" class="p-filter-bar"><label>변경안 상태<select name="status">${[
      ["active", "진행 중 전체"],
      ["draft", "초안"],
      ["submitted", "검수 대기"],
      ["approved", "반영 완료"],
      ["rejected", "반려"],
      ["cancelled", "작성 중단"],
      ["all", "전체 이력"],
    ]
      .map(
        ([value, label]) =>
          `<option value="${value}" ${query.get("status") === value ? "selected" : ""}>${label}</option>`,
      )
      .join(
        "",
      )}</select></label><button class="btn btn-outline btn-sm" type="submit">조회</button><span class="p-muted">${data.total}개 변경안</span></form><div class="p-revision-list">${data.revisions.map((item) => `<a class="p-panel" href="${revisionHref(item.id)}"><div class="p-inline-heading"><div>${tag(statusLabel(item.status), item.status === "approved" ? "mint" : "")} ${item.stale && ["draft", "submitted"].includes(item.status) ? tag("기준 차시 변경됨", "amber") : ""}<h2>${esc(item.lessonTitle)}</h2></div>${icon("chevron", 18)}</div><p>${esc(item.courseTitle)}</p><p class="p-muted">${esc(item.author || "이전 계정")} · 기준 v${item.baseVersion} · 변경안 v${item.version} · ${date(item.updatedAt)}</p>${item.reason ? `<p class="p-revision-summary">${esc(item.reason)}</p>` : ""}</a>`).join("") || empty("표시할 변경안이 없습니다.", "발행된 강의의 차시 편집 화면에서 변경안을 만들 수 있습니다.", `<a class="btn btn-outline" href="#/studio">강의 스튜디오</a>`)}${pager("/studio/revisions", query, data.page, data.total)}</div></section>`,
    bind() {
      bindForm("#revision-filter", (data) => {
        const next = new URLSearchParams(params);
        next.set("status", data.status);
        next.delete("page");
        ctx.navigate(`/studio/revisions?${next}`);
      });
    },
  };
}

function lessonPreview(lesson, course = {}) {
  const section = course.sections?.find((item) => item.id === lesson.sectionId);
  const asset = course.assets?.find((item) => item.id === lesson.videoAssetId);
  return `<div class="p-version-content"><h3>${esc(lesson.title)}</h3><p class="p-muted">${esc(lesson.duration || "시간 미입력")} · ${lesson.free ? "무료 미리보기" : "수강권 차시"} · ${statusLabel(lesson.status)}</p><dl class="p-version-meta"><div><dt>섹션</dt><dd>${esc(section?.title || "이전 섹션")}</dd></div><div><dt>차시 순서</dt><dd>${Number(lesson.sortOrder) + 1}번째</dd></div><div><dt>연결 영상</dt><dd>${lesson.videoAssetId ? esc(asset?.filename || "보호된 영상") : "없음"}</dd></div></dl>${lesson.videoAssetId ? '<p class="p-notice">이 버전에는 보호된 영상이 연결되어 있습니다.</p>' : ""}${(lesson.content || []).map((block) => `<section><h4>${esc(block.heading)}</h4><div class="p-user-body">${textBody(block.text)}</div></section>`).join("")}${lesson.resources?.length ? `<section><h4>연결 학습 자료</h4><ul>${lesson.resources.map((r) => `<li>${esc(r.label)} · ${esc(course.learningResources?.find((a) => a.id === r.assetId)?.filename || r.assetId)}</li>`).join("")}</ul></section>` : ""}${lesson.takeaway ? `<section><h4>핵심 정리</h4><p>${esc(lesson.takeaway)}</p></section>` : ""}${lesson.quiz ? `<section class="p-version-quiz"><h4>확인 퀴즈</h4><p>${esc(lesson.quiz.question)}</p><ol>${lesson.quiz.options.map((option, index) => `<li>${esc(option)}${index === lesson.quiz.answer ? tag("정답", "mint") : ""}</li>`).join("")}</ol><p class="p-muted">${esc(lesson.quiz.explanation)}</p></section>` : ""}</div>`;
}

function comparison(revision, course) {
  const historical = ["approved", "rejected", "cancelled"].includes(
    revision.status,
  );
  const before = historical ? revision.baseLesson : revision.currentLesson;
  const labels = {
    title: "차시명",
    duration: "소요 시간",
    sectionId: "섹션",
    sortOrder: "차시 순서",
    status: "공개 상태",
    free: "미리보기 범위",
    videoAssetId: "영상",
    resources: "학습 자료",
    content: "학습 본문",
    takeaway: "핵심 정리",
    quiz: "확인 퀴즈",
  };
  const fields = Object.keys(labels).filter(
    (key) =>
      JSON.stringify(before[key]) !==
      JSON.stringify(revision.proposedLesson[key]),
  );
  const title =
    revision.status === "approved"
      ? "반영 전후 내용 비교"
      : historical
        ? "저장된 요청 내용 비교"
        : "저장된 변경 내용 비교";
  const target =
    revision.status === "approved"
      ? `반영된 차시 v${revision.appliedLessonVersion}`
      : `변경안 v${revision.version}`;
  return `<section class="p-panel p-revision-comparison"><div class="p-inline-heading"><h2>${title}</h2><span class="p-muted">${historical ? "작성 기준" : "현재 공개"} v${before.version} → ${target}</span></div><p class="p-muted">${historical ? "해당 변경안의 기준 차시와 마지막 저장 내용을 표시합니다." : "비교에는 마지막으로 저장한 변경안이 표시됩니다."}</p>${revision.status === "approved" && revision.currentLesson.version !== revision.appliedLessonVersion ? `<p class="p-notice">현재 공개 차시는 v${revision.currentLesson.version}입니다. 이 기록은 v${revision.appliedLessonVersion}에 반영한 내용입니다.</p>` : ""}<div class="p-tag-row">${fields.map((key) => tag(labels[key], "mint")).join("") || tag("비교 기준과 동일")}</div><div class="p-version-columns"><article><p class="eyebrow">${historical ? "BASE VERSION" : "CURRENT LIVE"} · v${before.version}</p>${lessonPreview(before, course)}</article><article><p class="eyebrow">${revision.status === "approved" ? "APPROVED CONTENT" : "PROPOSED CHANGE"}</p>${lessonPreview(revision.proposedLesson, course)}</article></div>${revision.stale && !historical ? `<details class="p-base-version"><summary>처음 작성한 기준 v${revision.baseVersion} 보기</summary>${lessonPreview(revision.baseLesson, course)}</details>` : ""}</section>`;
}

function eventTimeline(revision) {
  return `<section class="p-panel"><h2>변경안 진행 이력</h2><div class="p-revision-events">${revision.events.map((event) => `<article><span>${tag(actionLabel[event.action] || event.action)}</span><div><strong>${esc(event.actor)}</strong><span class="p-muted">변경안 v${event.version} · ${date(event.createdAt)}</span><p>${esc(event.note)}</p></div></article>`).join("")}</div></section>`;
}

async function detail(id, ctx) {
  const revision = (await ctx.api(`/api/studio/revisions/${id}`)).revision;
  const course = (await ctx.api(`/api/studio/courses/${revision.courseId}`))
    .course;
  const editor = revision.canEdit
    ? renderLessonEditor(course, revision.lessonId, ctx, {
        revision,
        embedded: true,
      })
    : null;
  const terminal = ["approved", "rejected", "cancelled"].includes(
    revision.status,
  );
  return {
    html: `<section class="container p-page p-revision-detail"><a class="back-link" href="#/studio/revisions">← 차시 변경안</a>${head("LESSON CHANGE REQUEST", revision.currentLesson.title, "변경안은 검수 승인 후 공개 차시에 반영됩니다. 기존 차시 ID와 수강·질문 기록을 유지합니다.")} ${opsNav(ctx, "revisions")}<section class="p-panel"><div class="p-inline-heading"><div class="p-tag-row">${tag(statusLabel(revision.status), revision.status === "approved" ? "mint" : "")} ${tag(`기준 차시 v${revision.baseVersion}`)} ${tag(`변경안 v${revision.version}`)}</div><a class="text-link" href="${historyHref(revision.courseId, revision.lessonId)}">발행 이력 ${icon("external", 16)}</a></div><p class="p-muted">${esc(revision.courseTitle)} · 작성 ${esc(revision.author || "이전 계정")} · ${date(revision.updatedAt)}</p>${revision.reason ? `<div class="p-notice"><strong>변경 사유</strong><p>${esc(revision.reason)}</p></div>` : ""}${revision.reviewNote ? `<div class="p-notice ${revision.status === "rejected" ? "amber" : ""}"><strong>${revision.status === "approved" ? `검수 승인 · 차시 v${revision.appliedLessonVersion} 반영` : "검수 의견"}</strong><p>${esc(revision.reviewNote)}</p><p class="p-muted">${esc(revision.reviewer || "운영자")} · ${date(revision.reviewedAt)}</p></div>` : ""}${revision.stale && !terminal ? `<div class="p-notice amber"><strong>공개 차시가 기준 버전 이후 변경되었습니다.</strong><p>현재 내용과 작성안을 다시 비교해야 합니다. 검수 중이면 요청을 회수한 뒤 기준 버전을 갱신해주세요.</p></div>` : ""}<div class="p-form-actions">${revision.canWithdraw ? '<button class="btn btn-outline btn-sm" id="withdraw-revision">검수 요청 회수</button>' : ""}${revision.canEdit && revision.stale ? '<button class="btn btn-outline btn-sm" id="rebase-revision">최신 차시와 다시 비교</button>' : ""}${revision.canCancel ? '<button class="btn-text" id="cancel-revision">변경안 작성 중단</button>' : ""}<a class="btn btn-outline btn-sm" href="#/studio/course/${esc(revision.courseId)}/lesson/${esc(revision.lessonId)}">차시 편집 화면</a></div></section>${comparison(revision, course)}${editor ? `<div class="p-section-intro"><p class="eyebrow">EDIT YOUR PROPOSAL</p><h2>공개 수업을 유지하며 개선하세요.</h2><p>저장은 변경안에만 반영됩니다. 수정이 끝나면 사유를 적고 검수를 요청하세요.</p></div>${editor.html}` : ""}${revision.canReview ? `<section class="p-panel p-revision-review"><h2>관리자 검수</h2><p class="p-muted">본문, 영상, 퀴즈와 공개 범위를 비교한 후 결과를 남겨주세요.</p><form id="revision-review" data-session-draft data-recovery-version="${esc(`${revision.version}:${revision.currentLesson.version}`)}"><label class="field">검수 의견<textarea name="note" rows="4" minlength="5" maxlength="2000" required placeholder="확인한 내용과 반영 또는 반려 사유를 적어주세요."></textarea></label><p class="form-error" data-error role="alert"></p><div class="p-form-actions"><button class="btn btn-primary" type="submit" id="approve-revision" ${revision.stale ? "disabled" : ""}>승인하고 공개 차시에 반영</button><button class="btn btn-outline" type="button" id="reject-revision">반려하고 의견 전달</button></div></form></section>` : ""}${eventTimeline(revision)}</section>`,
    bind() {
      editor?.bind();
      const perform = async (action, button) => {
        button.disabled = true;
        try {
          await ctx.api(`/api/studio/revisions/${id}`, {
            method: "PATCH",
            body: { action, version: revision.version },
          });
          ctx.reload();
        } catch (error) {
          ctx.toast(error.message, "error");
          button.disabled = false;
        }
      };
      document
        .querySelector("#withdraw-revision")
        ?.addEventListener("click", (event) =>
          perform("withdraw", event.target),
        );
      document
        .querySelector("#cancel-revision")
        ?.addEventListener("click", (event) => perform("cancel", event.target));
      document
        .querySelector("#rebase-revision")
        ?.addEventListener("click", (event) => perform("rebase", event.target));
      const review = document.querySelector("#revision-review");
      if (review) {
        document.querySelector("#approve-revision").onclick = () => {
          review.dataset.action = "approve";
        };
        document.querySelector("#reject-revision").onclick = () => {
          review.dataset.action = "reject";
          review.requestSubmit();
        };
        bindForm(review, async (data) => {
          const reject = document.querySelector("#reject-revision");
          reject.disabled = true;
          try {
            await ctx.api(`/api/studio/revisions/${id}/review`, {
              method: "POST",
              body: {
                action: review.dataset.action || "approve",
                version: revision.version,
                note: data.note,
              },
            });
            await ctx.refreshCatalog();
            ctx.toast(
              review.dataset.action === "reject"
                ? "검수 의견과 반려 결과를 남겼습니다."
                : "변경안을 승인하고 새 차시 버전을 반영했습니다.",
            );
            ctx.reload();
          } finally {
            reject.disabled = false;
          }
        });
      }
    },
  };
}

async function historyPage(courseId, lessonId, ctx) {
  const data = await ctx.api(
    `/api/studio/courses/${courseId}/lessons/${lessonId}/history`,
  );
  return {
    html: `<section class="container p-page p-thread"><a class="back-link" href="#/studio/course/${esc(courseId)}/lesson/${esc(lessonId)}">← 차시 편집</a>${head("PUBLICATION HISTORY", data.currentLesson.title, "차시 저장·발행 버전과 검수 근거를 보존합니다. 최근 30개 버전을 확인할 수 있습니다.")}<div class="p-notice">현재 차시 v${data.currentLesson.version} · 이전 버전 열람은 공개 수업을 바꾸지 않습니다.</div><div class="p-publication-list">${data.entries.map((entry) => `<details class="p-panel" data-history-version="${entry.lessonVersion}"><summary><div><strong>v${entry.lessonVersion}</strong>${tag(sourceLabel[entry.source] || entry.source, entry.lessonVersion === data.currentLesson.version ? "mint" : "")}${tag(statusLabel(entry.lessonStatus))}<span>${date(entry.createdAt)}</span></div><span>${icon("chevron", 16)}</span></summary><p class="p-muted">${esc(entry.actor || "초기 콘텐츠 기준선")}</p>${entry.reason ? `<div class="p-notice"><strong>저장·검수 사유</strong><p>${esc(entry.reason)}</p></div>` : ""}${entry.revisionId ? `<a class="text-link" href="${revisionHref(entry.revisionId)}">관련 변경안과 검수 결과 ${icon("external", 16)}</a>` : ""}<div class="p-history-body"><p class="p-muted">버전 내용을 불러오고 있습니다…</p></div></details>`).join("") || empty("발행 이력이 없습니다.", "차시를 저장하면 버전 이력이 남습니다.")}</div></section>`,
    bind() {
      document.querySelectorAll("[data-history-version]").forEach((detail) =>
        detail.addEventListener("toggle", async () => {
          if (!detail.open || detail.dataset.loaded === "yes") return;
          try {
            const result = await ctx.api(
              `/api/studio/courses/${courseId}/lessons/${lessonId}/history/${detail.dataset.historyVersion}`,
            );
            detail.querySelector(".p-history-body").innerHTML = lessonPreview(
              result.entry.snapshot,
            );
            detail.dataset.loaded = "yes";
          } catch (error) {
            detail.querySelector(".p-history-body").textContent = error.message;
          }
        }),
      );
    },
  };
}
