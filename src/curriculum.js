import { esc, icon } from "./ui.js";
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
import {
  curriculumStates,
  copyLayout,
  sameLayout,
  validLayout,
  moveSection,
  moveLessons,
  rebaseLayout,
  batchRows,
  layoutChanges,
} from "./curriculum-model.js";
const link = (id, change = "", extra = {}) =>
  `/studio/course/${id}/curriculum?${new URLSearchParams({ ...extra, ...(change ? { change } : {}) })}`;
function readOnly(layout, lessons) {
  const index = new Map(lessons.map((l) => [l.id, l]));
  return `<div class="p-layout-summary">${layout.sections.map((s, i) => `<section><h3><small>${String(i + 1).padStart(2, "0")}</small>${esc(s.title)}</h3><ol>${s.lessons.map((id) => `<li>${esc(index.get(id)?.title || id)} ${index.get(id)?.status === "draft" ? tag("초안") : ""}</li>`).join("") || '<li class="p-muted">아직 차시가 없습니다.</li>'}</ol></section>`).join("")}</div>`;
}
function editorMarkup(layout, lessons, selected) {
  const index = new Map(lessons.map((l) => [l.id, l]));
  return layout.sections
    .map(
      (s, si) =>
        `<section class="p-layout-section" data-layout-section="${esc(s.id)}"><header><span class="p-layout-number">${String(si + 1).padStart(2, "0")}</span><label class="field">섹션 이름<input data-section-title value="${esc(s.title)}" minlength="2" maxlength="120" required/></label><div><button type="button" class="btn btn-outline btn-sm" data-section-up="${esc(s.id)}" aria-label="${esc(s.title)} 섹션 위로" ${si === 0 ? "disabled" : ""}>↑</button><button type="button" class="btn btn-outline btn-sm" data-section-down="${esc(s.id)}" aria-label="${esc(s.title)} 섹션 아래로" ${si === layout.sections.length - 1 ? "disabled" : ""}>↓</button></div></header><div>${s.lessons.map((id, i) => `<article class="p-layout-lesson" data-layout-lesson="${esc(id)}"><label><input type="checkbox" data-select-lesson="${esc(id)}" aria-label="${esc(index.get(id)?.title || id)} 이동 선택" ${selected.has(id) ? "checked" : ""}/></label><span class="p-layout-lesson-title"><strong>${esc(index.get(id)?.title || id)}</strong><small>${esc(index.get(id)?.duration || "시간 미정")} · ${esc({ draft: "비공개 초안", published: "공개 차시", archived: "보관 차시" }[index.get(id)?.status] || "")} · ID 유지</small></span><div class="p-layout-lesson-buttons"><button type="button" class="btn btn-quiet btn-sm" data-lesson-up="${esc(id)}" aria-label="${esc(index.get(id)?.title || id)} 위로" ${i === 0 ? "disabled" : ""}>↑</button><button type="button" class="btn btn-quiet btn-sm" data-lesson-down="${esc(id)}" aria-label="${esc(index.get(id)?.title || id)} 아래로" ${i === s.lessons.length - 1 ? "disabled" : ""}>↓</button><a class="text-link" href="#/studio/course/${esc(index.get(id)?.courseId || "")}/lesson/${esc(id)}" data-edit-lesson="${esc(id)}">차시 편집 ↗</a></div></article>`).join("") || '<p class="p-muted p-layout-empty">차시를 선택해 이 섹션으로 옮길 수 있습니다.</p>'}</div></section>`,
    )
    .join("");
}
export async function renderCurriculum(path, params, ctx) {
  const match = path.match(/^\/studio\/course\/([a-z0-9-]+)\/curriculum$/);
  if (!match) return null;
  if (!ctx.user) return authGate(ctx, "강의의 커리큘럼을 구성하세요.");
  if (ctx.user.role !== "admin" && !ctx.user.roles?.includes("coach"))
    return {
      html: `<section class="container p-page">${empty("구성 관리 권한이 필요합니다.", "담당 코치와 관리자만 이용할 수 있습니다.")}</section>`,
    };
  const cid = match[1],
    admin = ctx.user.role === "admin";
  const data = await ctx.api(
    `/api/studio/courses/${cid}/curriculum?page=${params.get("page") || 1}`,
  );
  const now = data.current;
  let working =
    data.changes.find((p) => p.id === params.get("change")) || data.changes[0];
  if (
    params.get("change") &&
    !data.changes.some((p) => p.id === params.get("change"))
  )
    working = (
      await ctx.api(
        `/api/studio/courses/${cid}/curriculum/changes/${params.get("change")}`,
      )
    ).change;
  const editable =
    working &&
    ["draft", "rejected"].includes(working.status) &&
    (admin || working.authorId === ctx.user.id);
  const author = working && (admin || working.authorId === ctx.user.id),
    selected = new Set();
  const lessons = now.lessons.map((l) => ({ ...l, courseId: cid }));
  let layout = working ? copyLayout(working.payload) : copyLayout(now.layout);
  const choices = [
    ...(working && !data.changes.some((p) => p.id === working.id)
      ? [working]
      : []),
    ...data.changes,
  ];
  return {
    title: now.courseTitle + " · 커리큘럼 구성",
    html: `<section class="container p-page" id="curriculum-page"><a class="back-link" href="#/studio/course/${esc(cid)}">← 강의 스튜디오</a>${head("CURRICULUM WORKSPACE", now.courseTitle, "차시 ID와 학습 기록을 유지하며 수업의 순서와 구성을 다듬습니다.", '<button class="btn btn-primary" id="new-curriculum">구성 변경안 만들기 +</button>')}${opsNav(ctx, "courses")}<div class="p-form-actions"><a class="btn btn-outline btn-sm" href="#/studio/course/${esc(cid)}/resources">학습 자료 라이브러리 ↗</a><a class="text-link" target="_blank" rel="noopener" href="#/course/${esc(cid)}">고객 강의 화면 ↗</a></div><div class="p-curriculum-overview"><article><span>현재 상품 기준</span><strong>v${now.courseVersion}</strong></article><article><span>섹션</span><strong>${now.layout.sections.length}</strong></article><article><span>전체 차시</span><strong>${now.lessons.length}<small>공개·초안·보관 포함</small></strong></article><article><span>반영 방식</span><strong>${["published", "archived"].includes(now.status) ? "관리자 승인" : "초안에 직접 적용"}</strong></article></div><p class="p-notice">공개 강의는 변경안을 저장·요청한 뒤 관리자가 승인합니다. 승인 전에는 기존 수업이 유지됩니다. 반영 후 상품 버전이 바뀌므로 정식 상품의 가격·제공 문서도 다시 확인해주세요.</p>
${now.status !== "archived" && (!["published"].includes(now.status) || admin) ? `<details class="p-panel"><summary>차시 초안을 한 번에 만들기 · 최대 50개</summary><form id="lesson-batch" data-session-draft data-recovery-version="batch:${now.courseVersion}"><p class="p-muted">제목을 한 줄에 하나씩 입력합니다. 선택한 섹션에 비공개 초안으로만 생성됩니다. 본문·자료·퀴즈는 각 차시에서 작성합니다.</p><label class="field">초안 생성 섹션<select name="sectionId">${now.layout.sections.map((s) => `<option value="${esc(s.id)}">${esc(s.title)}</option>`).join("")}</select></label><label class="field">차시 제목 목록<textarea name="titles" rows="7" required placeholder="포지션과 범위의 이해&#10;팟 크기를 읽는 기준"></textarea></label><p class="form-note">소요 시간도 입력하려면 제목과 시간을 탭으로 구분합니다. 예: 제목 [탭] 15분</p><p class="form-error" data-error role="alert"></p><button class="btn btn-primary" type="submit">비공개 차시 초안 묶음 생성</button></form></details>` : ""}
<div class="p-price-version-bar"><label class="field">확인할 구성 변경안<select id="curriculum-version">${choices.map((p) => `<option value="${p.id}" ${p.id === working?.id ? "selected" : ""}>${esc(curriculumStates[p.status])} · 상품 v${p.baseVersion} 기준 · ${date(p.createdAt)}</option>`).join("")}</select></label>${working ? tag(curriculumStates[working.status], working.status === "applied" ? "mint" : "") : ""}</div>${pager(`/studio/course/${cid}/curriculum`, params, data.page, data.total)}
${working ? `<div class="p-curriculum-workspace"><details class="p-panel p-current-layout"><summary>현재 구성 · 상품 v${now.courseVersion}</summary>${readOnly(now.layout, lessons)}</details><section class="p-panel"><div class="p-inline-heading"><h2>구성 변경안</h2><small class="p-muted">v${working.version} · 상품 v${working.baseVersion} 기준</small></div>${working.stale ? '<p class="p-notice amber">상품 또는 차시가 변경됐습니다. 현재 구성을 비교한 뒤 기준을 갱신해주세요.</p>' : ""}${working.reviewNote ? `<p class="p-notice">${esc(working.reviewNote)}</p>` : ""}${editable ? `<form id="curriculum-editor" data-session-draft data-recovery-version="${working.id}:${working.version}:${now.courseVersion}"><div class="p-layout-toolbar"><span id="layout-selected">선택 0개</span><label class="field">선택 차시를 옮길 섹션<select id="layout-target">${layout.sections.map((s) => `<option value="${esc(s.id)}">${esc(s.title)}</option>`).join("")}</select></label><button type="button" class="btn btn-outline btn-sm" id="move-selected-lessons">선택 차시 이동</button></div><div id="layout-editor">${editorMarkup(layout, lessons, selected)}</div><label class="field">구성 변경 이유<textarea name="reason" rows="3" maxlength="2000">${esc(working.reason)}</textarea></label><p id="layout-dirty" class="p-muted">저장된 구성입니다.</p><p class="form-error" data-error role="alert"></p><button class="btn btn-primary" type="submit">구성 변경안 저장</button></form>` : readOnly(working.payload, working.base.lessons)}<div class="p-form-actions">${editable ? `<button class="btn btn-outline" data-curriculum-action="submit">구성 검수 요청</button>${["draft", "review"].includes(now.status) ? '<button class="btn btn-primary" data-curriculum-action="apply">초안 강의에 적용</button>' : ""}<button class="btn btn-outline" id="rebase-curriculum">현재 구성과 기준 비교</button>` : working.status === "submitted" ? `${admin ? '<button class="btn btn-primary" data-curriculum-action="approve">구성 승인·반영</button><button class="btn btn-outline" data-curriculum-action="reject">구성 수정 요청</button>' : ""}${author ? '<button class="btn btn-outline" data-curriculum-action="withdraw">검수 요청 회수</button>' : ""}` : ""}${author && ["draft", "submitted", "rejected"].includes(working.status) ? '<button class="btn btn-quiet" data-curriculum-action="cancel">변경안 중단</button>' : ""}</div>${working.appliedVersion ? `<p class="p-notice">상품 v${working.appliedVersion}에 반영했습니다. 과거 변경안과 차시 이력은 보존됩니다.</p>` : ""}</section></div>` : empty("구성 변경안이 없습니다.", "현재 섹션과 차시 순서를 복사해 변경안을 만들어보세요.")}</section>`,
    bind() {
      const root = document.querySelector("#curriculum-page"),
        form = root.querySelector("#curriculum-editor");
      const read = () =>
        form
          ? {
              sections: [...root.querySelectorAll("[data-layout-section]")].map(
                (s) => ({
                  id: s.dataset.layoutSection,
                  title: s.querySelector("[data-section-title]").value.trim(),
                  lessons: [...s.querySelectorAll("[data-layout-lesson]")].map(
                    (l) => l.dataset.layoutLesson,
                  ),
                }),
              ),
            }
          : layout;
      const dirty = () =>
        form &&
        (!sameLayout(read(), working.payload) ||
          form.elements.reason.value !== (working.reason || ""));
      const savedFirst = () => {
        if (dirty())
          throw new Error("작성한 구성과 변경 이유를 먼저 저장해주세요.");
      };
      function changed() {
        if (form)
          root.querySelector("#layout-dirty").textContent = dirty()
            ? "저장하지 않은 구성 변경이 있습니다."
            : "저장된 구성입니다.";
      }
      function draw(next) {
        layout = next;
        root.querySelector("#layout-editor").innerHTML = editorMarkup(
          layout,
          lessons,
          selected,
        );
        const target = root.querySelector("#layout-target"),
          previous = target.value;
        target.innerHTML = layout.sections
          .map((s) => `<option value="${esc(s.id)}">${esc(s.title)}</option>`)
          .join("");
        if (layout.sections.some((s) => s.id === previous))
          target.value = previous;
        fixLinks();
        changed();
      }
      function fixLinks() {
        root
          .querySelectorAll("[data-edit-lesson]")
          .forEach(
            (a) =>
              (a.href = `#/studio/course/${cid}/lesson/${a.dataset.editLesson}`),
          );
      }
      fixLinks();
      root.querySelector("#new-curriculum").onclick = async () => {
        try {
          savedFirst();
          const r = await ctx.api(
            `/api/studio/courses/${cid}/curriculum/changes`,
            { method: "POST", body: {} },
          );
          ctx.navigate(link(cid, r.change.id));
          ctx.reload();
        } catch (e) {
          ctx.toast(e.message, "error");
        }
      };
      root.querySelector("#curriculum-version").onchange = (e) => {
        try {
          savedFirst();
          ctx.navigate(link(cid, e.target.value));
        } catch (err) {
          e.target.value = working.id;
          ctx.toast(err.message, "error");
        }
      };
      root.addEventListener("click", (e) => {
        const a = e.target.closest("a");
        if (a && a.target !== "_blank") {
          try {
            savedFirst();
          } catch (err) {
            e.preventDefault();
            ctx.toast(err.message, "error");
          }
        }
      });
      const batchKey = crypto.randomUUID();
      bindForm("#lesson-batch", async (d) => {
        savedFirst();
        const r = await ctx.api(`/api/studio/courses/${cid}/lessons/batch`, {
          method: "POST",
          body: {
            version: now.courseVersion,
            requestKey: batchKey,
            sectionId: d.sectionId,
            lessons: batchRows(d.titles),
          },
        });
        ctx.toast(`${r.lessonIds.length}개 비공개 차시 초안을 만들었습니다.`);
        ctx.reload();
      });
      if (form) {
        form.addEventListener("input", changed);
        form.addEventListener("tpc:capture-draft", (e) => {
          const value = read();
          e.detail.entries.push({
            key: "curriculumLayout",
            index: 0,
            type: "text",
            custom: true,
            value: JSON.stringify(value),
            label: "커리큘럼 구성",
            displayValue: value.sections
              .map((s) => s.title + " · " + s.lessons.length + "개 차시")
              .join("\n"),
            dirty: !sameLayout(value, working.payload),
          });
        });
        form.addEventListener("tpc:draft-restored", (e) => {
          const entry = e.detail.entries.find(
            (x) => x.key === "curriculumLayout" && x.custom,
          );
          if (entry) {
            try {
              const next = JSON.parse(entry.value);
              if (!validLayout(next, now.layout))
                throw new Error("현재 구성과 항목이 다릅니다.");
              draw(next);
            } catch {
              ctx.toast("복원한 구성의 항목을 다시 비교해주세요.", "error");
            }
          }
          changed();
        });
        bindForm(form, async (d) => {
          const payload = read();
          if (!validLayout(payload, now.layout))
            throw new Error("모든 섹션과 차시를 한 번씩 포함해주세요.");
          await ctx.api(
            `/api/studio/courses/${cid}/curriculum/changes/${working.id}`,
            {
              method: "PATCH",
              body: { version: working.version, payload, reason: d.reason },
            },
          );
          ctx.toast("구성 변경안을 저장했습니다.");
          ctx.reload();
        });
        form.addEventListener("change", (e) => {
          if (e.target.dataset.selectLesson) {
            e.target.checked
              ? selected.add(e.target.dataset.selectLesson)
              : selected.delete(e.target.dataset.selectLesson);
            root.querySelector("#layout-selected").textContent =
              `선택 ${selected.size}개`;
          }
        });
        root.querySelector("#move-selected-lessons").onclick = () => {
          try {
            if (!selected.size)
              throw new Error("이동할 차시를 먼저 선택해주세요.");
            draw(
              moveLessons(
                read(),
                [...selected],
                root.querySelector("#layout-target").value,
              ),
            );
          } catch (e) {
            ctx.toast(e.message, "error");
          }
        };
        form.addEventListener("click", (e) => {
          const b = e.target.closest("button");
          if (!b) return;
          let next = read();
          if (b.dataset.sectionUp || b.dataset.sectionDown) {
            draw(
              moveSection(
                next,
                b.dataset.sectionUp || b.dataset.sectionDown,
                b.dataset.sectionUp ? -1 : 1,
              ),
            );
          } else if (b.dataset.lessonUp || b.dataset.lessonDown) {
            const id = b.dataset.lessonUp || b.dataset.lessonDown,
              s = next.sections.find((s) => s.lessons.includes(id)),
              i = s.lessons.indexOf(id);
            draw(
              moveLessons(next, [id], s.id, i + (b.dataset.lessonUp ? -1 : 1)),
            );
          }
        });
      }
      root.querySelectorAll("[data-curriculum-action]").forEach(
        (b) =>
          (b.onclick = () => {
            try {
              savedFirst();
              actionDialog(b.dataset.curriculumAction);
            } catch (e) {
              ctx.toast(e.message, "error");
            }
          }),
      );
      function actionDialog(action) {
        const label = {
          submit: "구성 검수 요청",
          apply: "초안 강의에 적용",
          approve: "구성 승인·반영",
          reject: "구성 수정 요청",
          withdraw: "검수 요청 회수",
          cancel: "변경안 중단",
        }[action];
        ctx.modal(
          `<h2>${label}</h2>${["apply", "approve"].includes(action) ? "<p>저장된 섹션·차시 순서를 한 번에 반영합니다. 기존 차시 ID·진도·질문은 유지합니다. 정식 상품의 가격과 제공 문서도 다시 확인해주세요.</p>" : ""}<form id="curriculum-action" data-no-session-draft><label class="field">처리 사유<textarea name="reason" required minlength="5" maxlength="2000" rows="3"></textarea></label><p class="form-error" data-error role="alert"></p><button class="btn btn-primary" type="submit">${label}</button></form>`,
          (dialog) =>
            bindForm(dialog.querySelector("form"), async (d) => {
              await ctx.api(
                `/api/studio/courses/${cid}/curriculum/changes/${working.id}`,
                {
                  method: "POST",
                  body: { version: working.version, action, reason: d.reason },
                },
              );
              dialog.close();
              await ctx.refreshCatalog();
              ctx.reload();
            }),
        );
      }
      root
        .querySelector("#rebase-curriculum")
        ?.addEventListener("click", async () => {
          const button = root.querySelector("#rebase-curriculum");
          button.disabled = true;
          try {
            const fresh = (
              await ctx.api(
                `/api/studio/courses/${cid}/curriculum/changes/${working.id}`,
              )
            ).current;
            const merged = rebaseLayout(read(), fresh.layout);
            ctx.modal(
              `<h2>현재 구성과 기준 비교</h2><p>작성 기준 상품 v${working.baseVersion} → 현재 상품 v${fresh.courseVersion}. 현재 추가된 항목을 포함하고, 차시 ID를 유지합니다.</p><div class="p-curriculum-compare"><section><h3>현재 공개/작성 구성</h3>${readOnly(fresh.layout, fresh.lessons)}</section><section><h3>기존 순서에 현재 항목을 추가한 안</h3>${readOnly(merged, fresh.lessons)}</section></div><form id="curriculum-rebase" data-no-session-draft><label class="field">새 기준으로 사용할 구성<select name="choice"><option value="merged">내 변경 순서를 유지하고 새 항목 포함</option><option value="current">현재 구성으로 다시 시작</option></select></label><label class="field">기준 갱신 사유<textarea name="reason" required minlength="5" maxlength="2000"></textarea></label><p class="form-error" data-error role="alert"></p><button class="btn btn-primary" type="submit">비교한 구성으로 기준 갱신</button></form>`,
              (dialog) =>
                bindForm(dialog.querySelector("form"), async (d) => {
                  const r = await ctx.api(
                    `/api/studio/courses/${cid}/curriculum/changes/${working.id}`,
                    {
                      method: "POST",
                      body: {
                        action: "rebase",
                        version: working.version,
                        courseVersion: fresh.courseVersion,
                        payload: d.choice === "current" ? fresh.layout : merged,
                        reason: d.reason,
                      },
                    },
                  );
                  dialog.close();
                  ctx.toast(
                    "비교한 구성으로 기준을 갱신했습니다. 내용을 확인하고 검수를 요청해주세요.",
                  );
                  ctx.reload();
                }),
            );
          } catch (error) {
            ctx.toast(error.message, "error");
          } finally {
            button.disabled = false;
          }
        });
    },
  };
}
