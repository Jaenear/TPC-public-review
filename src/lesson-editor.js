import { esc, icon } from "./ui.js";
import { head, empty, bindForm, statusLabel } from "./platform-ui.js";
import { lessonResourceEditor, readResourceChoices } from "./resources.js";
import { mediaUploadMarkup, bindMediaUploader } from "./media-upload.js";

function blockFields(block = {}) {
  return `<div class="p-content-block"><label class="field">문단 제목<input data-heading required maxlength="160" value="${esc(block.heading || "")}"/></label><label class="field">본문<textarea data-text rows="7" required maxlength="20000">${esc(block.text || "")}</textarea></label><button class="btn-text" type="button" data-remove-block>이 문단 제거</button></div>`;
}
export function renderLessonEditor(course, id, ctx, options = {}) {
  const revision = options.revision;
  const lesson = revision
    ? {
        ...revision.proposedLesson,
        id: revision.lessonId,
        version: revision.baseVersion,
      }
    : id === "new"
      ? null
      : course.lessons.find((l) => l.id === id);
  if (id !== "new" && !lesson)
    return {
      html: empty("차시를 찾을 수 없습니다.", "강의 스튜디오로 돌아가주세요."),
    };
  const quiz = lesson?.quiz;
  const locked =
    !revision &&
    ["published", "archived"].includes(course.status) &&
    ctx.user.role !== "admin";
  return {
    html: `${options.embedded ? "" : `<section class="container p-page p-lesson-editor"><a class="back-link" href="#/studio/course/${esc(course.id)}">← ${esc(course.title)}</a>${head("LESSON EDITOR", lesson ? "차시를 다듬는 시간." : "새로운 배움을 구성하세요.", "공개 여부, 본문과 확인 퀴즈, 보호된 영상을 한 차시로 묶습니다.")}${locked ? '<p class="p-notice">발행된 차시입니다. 변경안을 작성하면 현재 공개 수업을 유지하며 검수를 요청할 수 있습니다.</p>' : ""}${lesson ? `<div class="p-editor-tools"><a class="btn btn-outline btn-sm" href="#/studio/course/${esc(course.id)}/lesson/${esc(lesson.id)}/history">발행 이력 보기</a>${["published", "archived"].includes(course.status) && lesson.status === "published" ? '<button class="btn btn-primary btn-sm" type="button" id="start-lesson-revision">차시 변경안 만들기</button>' : ""}</div>` : ""}`}<form id="lesson-form" class="p-panel" data-session-draft data-recovery-version="${esc(`${course.version}:${revision ? `revision-${revision.version}-${revision.baseVersion}` : lesson?.version || "new"}`)}"><fieldset class="p-editor-fields" ${locked ? "disabled" : ""}>${revision ? `<label class="field p-revision-reason">변경 사유 (검수 요청 시 필수)<textarea name="revisionReason" rows="3" minlength="5" maxlength="2000">${esc(revision.reason)}</textarea></label>` : ""}<div class="field-row"><label class="field">차시명<input name="title" required minlength="2" maxlength="120" value="${esc(lesson?.title || "")}"/></label><label class="field">소요 시간<input name="duration" maxlength="40" placeholder="예: 15분" value="${esc(lesson?.duration || "")}"/></label></div><div class="field-row"><label class="field">섹션<select name="sectionId">${course.sections.map((s) => `<option value="${esc(s.id)}" ${lesson?.sectionId === s.id ? "selected" : ""}>${esc(s.title)}</option>`).join("")}</select></label><label class="field">정렬 순서 (0부터)<input name="sortOrder" type="number" min="0" max="10000" step="1" value="${lesson?.sortOrder ?? course.lessons.length}" required/></label></div><div class="field-row"><label class="field">차시 상태<select name="status">${["draft", "published", "archived"].map((s) => `<option value="${s}" ${lesson?.status === s ? "selected" : ""}>${statusLabel(s)}</option>`).join("")}</select></label><label class="p-checkbox"><input name="free" type="checkbox" ${lesson?.free ? "checked" : ""}/> 구매 전 무료 미리보기</label></div><h2>학습 본문</h2><div id="lesson-blocks">${(lesson?.content || [{ heading: "", text: "" }]).map(blockFields).join("")}</div><button class="btn btn-outline btn-sm" id="add-lesson-block" type="button">문단 추가</button><label class="field p-field-spaced">핵심 정리<textarea name="takeaway" rows="3" maxlength="2000">${esc(lesson?.takeaway || "")}</textarea></label><section class="p-media-editor"><h2>보호된 강의 영상</h2><p>MP4, 최대 100MB. 비공개 저장소에 업로드되며 재생할 때마다 차시 공개 상태와 수강권을 확인합니다.</p><label class="field">이 강의의 영상<select name="videoAssetId" id="lesson-video-asset"><option value="">영상 없음 · 텍스트 차시</option>${(course.assets || []).map((a) => `<option value="${esc(a.id)}" ${lesson?.videoAssetId === a.id ? "selected" : ""}>${esc(a.filename)} · ${(a.size / 1024 / 1024).toFixed(1)}MB</option>`).join("")}</select></label>${mediaUploadMarkup(course.id)}</section>${lessonResourceEditor(course, lesson, locked)}<section class="p-quiz-editor"><label class="p-checkbox"><input type="checkbox" name="hasQuiz" ${quiz ? "checked" : ""}/> 확인 퀴즈 포함</label><label class="field">질문<input name="quizQuestion" maxlength="500" value="${esc(quiz?.question || "")}"/></label><label class="field">선택지 (한 줄에 하나, 2~6개)<textarea name="quizOptions" rows="5">${esc((quiz?.options || []).join("\n"))}</textarea></label><label class="field">정답 번호<select name="quizAnswer">${[0, 1, 2, 3, 4, 5].map((i) => `<option value="${i}" ${i === (quiz?.answer ?? 0) ? "selected" : ""}>${i + 1}번</option>`).join("")}</select></label><label class="field">해설<textarea name="quizExplanation" rows="4" maxlength="2000">${esc(quiz?.explanation || "")}</textarea></label></section><p class="form-error" data-error role="alert"></p><div class="p-form-actions"><a href="#/studio/course/${esc(course.id)}" class="btn btn-outline">강의로 돌아가기</a>${revision ? `<button class="btn btn-outline" type="submit" id="save-revision">변경안 저장</button><button class="btn btn-primary" type="button" id="submit-revision">검수 요청 ${icon("arrow", 17)}</button>` : `<button class="btn btn-primary" type="submit">차시 저장 ${icon("check", 17)}</button>`}</div></fieldset></form>${options.embedded ? "" : "</section>"}`,
    bind() {
      document
        .querySelector("#start-lesson-revision")
        ?.addEventListener("click", async (event) => {
          event.target.disabled = true;
          try {
            const result = await ctx.api(
              `/api/studio/courses/${course.id}/lessons/${lesson.id}/revisions`,
              { method: "POST", body: {} },
            );
            ctx.navigate(`/studio/revisions/${result.revision.id}`);
          } catch (error) {
            ctx.toast(error.message, "error");
            event.target.disabled = false;
          }
        });
      if (revision) {
        const form = document.querySelector("#lesson-form");
        document.querySelector("#save-revision").onclick = () => {
          form.dataset.intent = "save";
        };
        document.querySelector("#submit-revision").onclick = () => {
          form.dataset.intent = "submit";
          form.requestSubmit();
        };
      }
      const blocks = document.querySelector("#lesson-blocks");
      function bindRemoval() {
        blocks
          .querySelectorAll("[data-remove-block]")
          .forEach(
            (b) => (b.onclick = () => b.closest(".p-content-block").remove()),
          );
      }
      bindRemoval();
      const lessonForm = document.querySelector("#lesson-form");
      const originalBlocks = lesson?.content || [{ heading: "", text: "" }];
      lessonForm.addEventListener("tpc:capture-draft", (event) => {
        if (locked) return;
        const content = [...blocks.querySelectorAll(".p-content-block")].map(
          (block) => ({
            heading: block.querySelector("[data-heading]").value,
            text: block.querySelector("[data-text]").value,
          }),
        );
        event.detail.entries.push({
          key: "lessonBlocks",
          index: 0,
          type: "text",
          custom: true,
          value: JSON.stringify(content),
          label: "학습 본문",
          displayValue: content
            .map((block) => `${block.heading}\n${block.text}`)
            .join("\n\n"),
          dirty: JSON.stringify(content) !== JSON.stringify(originalBlocks),
        });
      });
      lessonForm.addEventListener("tpc:draft-restored", (event) => {
        const entry = event.detail.entries.find(
          (entry) => entry.key === "lessonBlocks" && entry.custom,
        );
        if (!entry) return;
        try {
          const content = JSON.parse(entry.value);
          if (
            !Array.isArray(content) ||
            content.length > 30 ||
            content.some(
              (block) =>
                typeof block.heading !== "string" ||
                typeof block.text !== "string",
            )
          )
            return;
          blocks.innerHTML = content.map(blockFields).join("");
          bindRemoval();
        } catch {}
      });
      document.querySelector("#add-lesson-block").onclick = () => {
        if (blocks.children.length >= 30) {
          ctx.toast("문단은 최대 30개입니다.", "error");
          return;
        }
        blocks.insertAdjacentHTML("beforeend", blockFields());
        bindRemoval();
      };
      bindMediaUploader(
        document.querySelector("[data-media-uploader]"),
        course.id,
        ctx,
        (asset) => {
          const select = document.querySelector("#lesson-video-asset");
          let option = [...select.options].find(
            (item) => item.value === asset.id,
          );
          if (!option) {
            option = document.createElement("option");
            option.value = asset.id;
            option.textContent = asset.filename;
            select.append(option);
          }
          option.selected = true;
          select.dispatchEvent(new Event("change", { bubbles: true }));
        },
      ).catch((error) => ctx.toast(error.message, "error"));
      bindForm("#lesson-form", async (data, form) => {
        const content = [...blocks.querySelectorAll(".p-content-block")].map(
          (b) => ({
            heading: b.querySelector("[data-heading]").value,
            text: b.querySelector("[data-text]").value,
          }),
        );
        const quiz =
          data.hasQuiz === "on"
            ? {
                question: data.quizQuestion,
                options: data.quizOptions
                  .split("\n")
                  .map((v) => v.trim())
                  .filter(Boolean),
                answer: Number(data.quizAnswer),
                explanation: data.quizExplanation,
              }
            : null;
        const payload = {
          title: data.title,
          duration: data.duration,
          sectionId: data.sectionId,
          sortOrder: Number(data.sortOrder),
          status: data.status,
          free: data.free === "on",
          videoAssetId: data.videoAssetId,
          resources: readResourceChoices(form),
          content,
          takeaway: data.takeaway,
          quiz,
          version: lesson?.version,
        };
        if (revision) {
          const submit = document.querySelector("#submit-revision");
          submit.disabled = true;
          try {
            const action =
              document.querySelector("#lesson-form").dataset.intent || "save";
            await ctx.api(`/api/studio/revisions/${revision.id}`, {
              method: "PATCH",
              body: {
                action,
                version: revision.version,
                reason: data.revisionReason,
                lesson: payload,
              },
            });
            ctx.toast(
              action === "submit"
                ? "변경안 검수를 요청했습니다."
                : "변경안을 저장했습니다.",
            );
            ctx.reload();
          } finally {
            submit.disabled = false;
          }
        } else {
          await ctx.api(
            `/api/studio/courses/${course.id}/lessons${lesson ? `/${lesson.id}` : ""}`,
            { method: lesson ? "PATCH" : "POST", body: payload },
          );
          await ctx.refreshCatalog();
          ctx.toast("차시를 저장했습니다.");
          ctx.navigate(`/studio/course/${course.id}`);
        }
      });
    },
  };
}
