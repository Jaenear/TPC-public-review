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
import {
  publicationStates,
  checkStates,
  sameChecks,
  normalizedChecks,
  readyChecks,
  reviewDiff,
} from "./publication-model.js";
const link = (cid, rid = "", extras = {}) =>
  `/studio/course/${cid}/publication?${new URLSearchParams({ ...extras, ...(rid ? { review: rid } : {}) })}`;
const list = (items) =>
  `<ul>${items.map((i) => `<li>${esc(i)}</li>`).join("")}</ul>`;
function snapshotView(s) {
  return `<div class="p-review-snapshot"><h3>${esc(s.course.title)}</h3><p>${esc(s.course.description)}</p><p class="p-muted">${esc(s.course.category)} · ${esc(s.course.level)} · ${s.course.pilot ? "파일럿" : "정식 강의"} · 상품 v${s.course.version}</p>${s.sections
    .map(
      (section) =>
        `<section><h4>${esc(section.title)}</h4>${s.lessons
          .filter((l) => l.sectionId === section.id)
          .map(
            (l) =>
              `<details><summary>${esc(l.title)} ${tag(l.status === "published" ? (l.free ? "무료 미리보기" : "수강 보호 차시") : "비공개 차시")}</summary><p class="p-muted">차시 v${l.version} · ${esc(l.id)} · ${esc(l.duration || "시간 미정")}</p>${l.content.map((b) => `<h5>${esc(b.heading)}</h5><p>${esc(b.text)}</p>`).join("")}<p>${esc(l.takeaway || "핵심 정리 미입력")}</p>${l.quiz ? `<h5>${esc(l.quiz.question)}</h5>${list(l.quiz.options.map((o, i) => `${i === l.quiz.answer ? "✓ " : ""}${o}`))}<p>${esc(l.quiz.explanation)}</p>` : ""}${l.videoAssetId ? `<p>영상: ${esc(s.videos[l.videoAssetId]?.filename || l.videoAssetId)}</p>` : ""}${l.resources.map((r) => `<p>자료: ${esc(r.label)} · ${esc(s.resources[r.assetId]?.filename || r.assetId)} · ${esc(s.resources[r.assetId]?.status || "확인 필요")}</p>`).join("")}</details>`,
          )
          .join("")}</section>`,
    )
    .join("")}</div>`;
}
export async function renderPublications(path, params, ctx) {
  const match = path.match(/^\/studio\/course\/([a-z0-9-]+)\/publication$/);
  if (path !== "/studio/publications" && !match) return null;
  if (!ctx.user)
    return authGate(ctx, "강의의 발행 준비와 품질 검수를 확인하세요.");
  if (ctx.user.role !== "admin" && !ctx.user.roles?.includes("coach"))
    return {
      html: `<section class="container p-page">${empty("강의 검수 권한이 필요합니다.", "담당 코치와 관리자만 이용할 수 있습니다.")}</section>`,
    };
  if (!match) return queue(params, ctx);
  const cid = match[1],
    admin = ctx.user.role === "admin";
  const data = await ctx.api(
    `/api/studio/courses/${cid}/publication?page=${params.get("page") || 1}`,
  );
  const selected = params.get("review") || data.reviews[0]?.id;
  const detail = selected
    ? await ctx.api(
        `/api/studio/courses/${cid}/publication/reviews/${selected}?${new URLSearchParams({ commentPage: params.get("commentPage") || 1, eventPage: params.get("eventPage") || 1 })}`,
      )
    : null;
  const current = detail?.current || data.current,
    r = detail?.review;
  const authorMode = r?.status === "draft" && r.authorId === ctx.user.id,
    reviewerMode = r?.status === "submitted" && admin;
  const editable = authorMode || reviewerMode;
  const mode = reviewerMode ? "reviewer" : "author";
  const savedNote = (reviewerMode ? r?.reviewNote : r?.authorNote) || "";
  const checks = r?.[mode + "Checks"] || {};
  const choices = [
    ...(r && !data.reviews.some((v) => v.id === r.id) ? [r] : []),
    ...data.reviews,
  ];
  const diff = r ? reviewDiff(r.snapshot, current.snapshot) : [];
  return {
    title: current.snapshot.course.title + " · 발행 검수",
    html: `<section class="container p-page" id="publication-page"><a class="back-link" href="#/studio/course/${cid}">← 강의 스튜디오</a>${head("COURSE QUALITY REVIEW", current.snapshot.course.title, "공개할 내용과 확인 근거를 같은 버전에 묶습니다.", current.snapshot.course.status !== "published" ? '<button class="btn btn-primary" id="new-publication">새 품질 점검 만들기 +</button>' : "")}${opsNav(ctx, "publications")}<div class="p-form-actions"><a class="btn btn-outline btn-sm" href="#/studio/course/${cid}/curriculum">커리큘럼 구성 ↗</a><a class="btn btn-outline btn-sm" href="#/studio/course/${cid}/resources">학습 자료 ↗</a><a class="btn btn-outline btn-sm" href="#/studio/course/${cid}/pricing">판매 준비 ↗</a></div><div class="p-curriculum-overview"><article><span>현재 상품</span><strong>v${current.courseVersion}</strong></article><article><span>공개 예정 차시</span><strong>${current.counts.published}<small>초안 ${current.counts.draft}개 제외</small></strong></article><article><span>미리보기 / 보호</span><strong>${current.counts.preview} / ${current.counts.protected}</strong></article><article><span>연결 자료 / 영상</span><strong>${current.counts.resources} / ${current.counts.videos}</strong></article></div><div class="p-notice ${current.ready ? "" : "amber"}"><strong>${current.ready ? "현재 기술 제공 조건 확인" : "현재 보완이 필요한 제공 조건"}</strong>${current.ready ? "<p>공개 차시와 연결 상태를 확인했습니다. 작성자와 관리자의 항목별 검토가 필요합니다.</p>" : list(current.blockers)}<p>체크리스트는 확인한 내용의 기록입니다. 실제 콘텐츠/권리 인수와 판매 가격·문서·상점 준비는 각각 관리합니다.</p></div><div class="p-price-version-bar"><label class="field">확인할 품질 검수<select id="publication-version">${choices.map((p) => `<option value="${p.id}" ${p.id === r?.id ? "selected" : ""}>${esc(publicationStates[p.status])} · 상품 v${p.baseVersion} · ${date(p.createdAt)}</option>`).join("")}</select></label>${r ? tag(publicationStates[r.status], r.status === "published" ? "mint" : "") : ""}</div>${pager(`/studio/course/${cid}/publication`, params, data.page, data.total)}${r ? `${r.stale ? `<p class="p-notice amber">검수 대상이 바뀌었습니다. ${r.status === "draft" ? "현재 내용과 비교해 확인 표시를 초기화한 뒤 다시 점검해주세요." : "이 제출 원문으로 발행할 수 없습니다. 수정 요청/회수 후 새 검수를 제출해주세요."}</p>` : ""}${r.decisionReason || r.reviewNote ? `<div class="p-panel"><h2>검수 결과와 보완 의견</h2><p>${esc(r.decisionReason || r.reviewNote)}</p>${r.decisionReason && r.reviewNote ? `<p>${esc(r.reviewNote)}</p>` : ""}</div>` : ""}<div class="p-review-layout"><section class="p-panel"><div class="p-inline-heading"><h2>${authorMode ? "작성자 자체 점검" : reviewerMode ? "관리자 항목별 검수" : "당시 품질 확인 기록"}</h2><small>${esc(r.checklistVersion)} · v${r.version}</small></div><p class="p-muted">제출 기준 상품 v${r.baseVersion} · ${esc(r.basisHash.slice(0, 16))}${r.publishedVersion ? ` · 발행 상품 v${r.publishedVersion}` : ""}</p><form id="quality-checks" ${editable ? "data-session-draft" : "data-no-session-draft"} data-recovery-version="${r.id}:${r.version}:${current.hash}">${data.checklist.map((item) => `<article class="p-quality-item"><h3>${esc(item.title)}</h3><p>${esc(item.description)}</p><div class="p-quality-evidence"><small>작성자 · ${esc(checkStates[r.authorChecks[item.id].state])}</small><p>${esc(r.authorChecks[item.id].note || "아직 확인 근거가 없습니다.")}</p>${!authorMode ? `<small>관리자 · ${esc(checkStates[r.reviewerChecks[item.id].state])}</small><p>${esc(r.reviewerChecks[item.id].note || "관리자 확인 전입니다.")}</p>` : ""}</div>${editable ? `${authorMode ? `<label class="p-checkbox"><input type="checkbox" name="${item.id}-confirmed" ${checks[item.id]?.state === "confirmed" ? "checked" : ""}/> ${esc(item.title)} 자체 점검 확인</label>` : `<label class="field">${esc(item.title)} 검수 판단<select name="${item.id}-state">${["pending", "pass", "changes"].map((state) => `<option value="${state}" ${checks[item.id]?.state === state ? "selected" : ""}>${checkStates[state]}</option>`).join("")}</select></label>`}<label class="field">${esc(item.title)} ${authorMode ? "작성자" : "관리자"} 확인 근거<textarea name="${item.id}-note" rows="3" maxlength="2000" placeholder="확인한 차시/자료와 검토 내용을 남겨주세요.">${esc(checks[item.id]?.note || "")}</textarea></label>` : ""}</article>`).join("")}<label class="field">${authorMode ? "제출 전 설명" : reviewerMode ? "검수 메모" : "작성자 설명"}<textarea name="note" rows="3" maxlength="4000" ${editable ? "" : "disabled"}>${esc(editable ? savedNote : r.authorNote)}</textarea></label>${editable ? '<p id="quality-dirty" class="p-muted">저장된 점검입니다.</p><p class="form-error" data-error role="alert"></p><button class="btn btn-primary" type="submit">점검·근거 저장</button>' : ""}</form><div class="p-form-actions">${authorMode ? '<button class="btn btn-primary" data-publication-action="submit">관리자 검수 요청</button><button class="btn btn-outline" id="publication-rebase">현재 내용과 기준 비교</button>' : ""}${reviewerMode ? '<button class="btn btn-primary" data-publication-action="publish">검수 완료·강의 발행</button><button class="btn btn-outline" data-publication-action="reject">수정 요청 보내기</button>' : ""}${["draft", "submitted"].includes(r.status) && (admin || r.authorId === ctx.user.id) ? '<button class="btn btn-quiet" data-publication-action="withdraw">검수 요청 회수</button>' : ""}</div>${r.previousId ? `<a class="text-link" href="#${link(cid, r.previousId)}">이전 검수 원문과 보완 요청 ↗</a>` : ""}</section><aside><details class="p-panel" open><summary>검수 대상 원문 · 상품 v${r.baseVersion}</summary>${snapshotView(r.snapshot)}</details><details class="p-panel"><summary>현재 내용과 변경 비교 · ${diff.length}개</summary>${diff.length ? list(diff) : "<p>제출 기준과 현재 내용이 같습니다.</p>"}${snapshotView(current.snapshot)}</details></aside></div><section class="p-panel"><h2>항목별 보완 대화</h2><p class="p-muted">의견은 원문을 보존합니다. 새로운 보완은 새 의견으로 이어갑니다.</p>${detail.comments.map((c) => `<article class="p-review-comment"><small>${c.author_id === ctx.user.id ? "나의 의견" : c.author_id === r.authorId ? "작성자" : "검수 담당"} · ${date(c.created_at)} · ${esc(data.checklist.find((i) => i.id === c.check_id)?.title || "전체 검수")}</small><p>${esc(c.body)}</p></article>`).join("") || "<p>아직 검수 의견이 없습니다.</p>"}${pager(`/studio/course/${cid}/publication`, new URLSearchParams({ review: r.id }), detail.commentPage, detail.commentTotal, 20, "commentPage")}<form id="review-comment" data-session-draft data-recovery-version="comment:${r.id}"><label class="field">의견을 연결할 항목<select name="checkId"><option value="">전체 검수</option>${data.checklist.map((i) => `<option value="${i.id}">${esc(i.title)}</option>`).join("")}</select></label><label class="field">보완 의견<textarea name="body" required minlength="5" maxlength="4000" rows="3"></textarea></label><p class="form-error" data-error role="alert"></p><button class="btn btn-outline" type="submit">의견 남기기</button></form></section><details class="p-panel"><summary>검수 진행 이력 · ${detail.eventTotal}건</summary>${detail.events.map((e) => `<article class="p-review-comment"><small>${date(e.created_at)} · ${esc({ created: "점검 생성", save: "점검 저장", submit: "검수 요청", reject: "수정 요청", publish: "강의 발행", withdraw: "요청 회수", rebase: "기준 재비교" }[e.action] || e.action)}</small><p>${esc(e.reason)}</p></article>`).join("")}${pager(`/studio/course/${cid}/publication`, new URLSearchParams({ review: r.id }), detail.eventPage, detail.eventTotal, 20, "eventPage")}</details>` : empty("품질 점검을 시작하세요.", "자체 점검→관리자 검수→보완/발행을 같은 강의에서 이어갑니다.")}</section>`,
    bind() {
      const root = document.querySelector("#publication-page"),
        form = root.querySelector("#quality-checks");
      const read = () =>
        Object.fromEntries(
          data.checklist.map(({ id }) => [
            id,
            {
              state: authorMode
                ? form.elements[id + "-confirmed"].checked
                  ? "confirmed"
                  : "pending"
                : form.elements[id + "-state"].value,
              note: form.elements[id + "-note"].value,
            },
          ]),
        );
      const dirty = () =>
        editable &&
        (!sameChecks(data.checklist, read(), checks) ||
          form.elements.note.value !== savedNote);
      const savedFirst = () => {
        if (dirty()) throw new Error("작성한 점검과 근거를 먼저 저장해주세요.");
      };
      if (editable) {
        const changed = () => {
          root.querySelector("#quality-dirty").textContent = dirty()
            ? "저장하지 않은 점검 변경이 있습니다."
            : "저장된 점검입니다.";
        };
        form.addEventListener("input", changed);
        form.addEventListener("tpc:draft-restored", changed);
      }
      root.addEventListener("click", (e) => {
        const a = e.target.closest("a");
        if (a && a.target !== "_blank") {
          try {
            savedFirst();
          } catch (error) {
            e.preventDefault();
            ctx.toast(error.message, "error");
          }
        }
      });
      const select = root.querySelector("#publication-version");
      select.onchange = () => {
        try {
          savedFirst();
          ctx.navigate(link(cid, select.value));
        } catch (e) {
          select.value = r?.id || "";
          ctx.toast(e.message, "error");
        }
      };
      const create = root.querySelector("#new-publication");
      if (create)
        create.onclick = async () => {
          try {
            savedFirst();
            const result = await ctx.api(
              `/api/studio/courses/${cid}/publication/reviews`,
              { method: "POST", body: {} },
            );
            ctx.navigate(link(cid, result.review.id));
            ctx.reload();
          } catch (e) {
            ctx.toast(e.message, "error");
          }
        };
      if (editable)
        bindForm(form, async (d) => {
          await ctx.api(
            `/api/studio/courses/${cid}/publication/reviews/${r.id}`,
            {
              method: "PATCH",
              body: { version: r.version, checks: read(), note: d.note },
            },
          );
          ctx.toast("점검과 확인 근거를 저장했습니다.");
          ctx.reload();
        });
      root.querySelectorAll("[data-publication-action]").forEach((button) => {
        button.onclick = () => {
          try {
            savedFirst();
            const action = button.dataset.publicationAction;
            if (action === "submit" && !readyChecks(data.checklist, checks))
              throw new Error("모든 자체 점검과 확인 근거를 저장해주세요.");
            if (
              action === "publish" &&
              !readyChecks(data.checklist, checks, true)
            )
              throw new Error(
                "모든 항목의 관리자 통과와 확인 근거를 저장해주세요.",
              );
            const title = {
              submit: "관리자 검수 요청",
              publish: "검수 완료·강의 발행",
              reject: "수정 요청 보내기",
              withdraw: "검수 요청 회수",
            }[action];
            ctx.modal(
              `<h2>${title}</h2><p>${action === "publish" ? "확인한 제출 원문과 현재 제공 상태를 다시 검사한 뒤 카탈로그에 발행합니다. 판매 가격·문서 준비는 별도로 확인하세요." : "저장한 원문과 체크 기록을 보존하고 이번 처리를 남깁니다."}</p><form data-no-session-draft><label class="field">처리 사유<textarea name="reason" required minlength="5" maxlength="2000" rows="3"></textarea></label><p class="form-error" data-error role="alert"></p><button type="submit" class="btn btn-primary">${title}</button></form>`,
              (dialog) =>
                bindForm(dialog.querySelector("form"), async (d) => {
                  await ctx.api(
                    `/api/studio/courses/${cid}/publication/reviews/${r.id}`,
                    {
                      method: "POST",
                      body: { version: r.version, action, reason: d.reason },
                    },
                  );
                  dialog.close();
                  await ctx.refreshCatalog();
                  ctx.toast("검수 처리를 기록했습니다.");
                  ctx.reload();
                }),
            );
          } catch (e) {
            ctx.toast(e.message, "error");
          }
        };
      });
      const rebase = root.querySelector("#publication-rebase");
      if (rebase)
        rebase.onclick = async () => {
          try {
            const fresh = (
              await ctx.api(
                `/api/studio/courses/${cid}/publication/reviews/${r.id}`,
              )
            ).current;
            const local = read(),
              note = form.elements.note.value;
            ctx.modal(
              `<h2>현재 내용과 기준 비교</h2><p>상품 v${r.baseVersion} → v${fresh.courseVersion}. 작성한 근거 메모는 보존하고, 모든 확인 표시를 초기화합니다. 새 기준에서 각 항목을 다시 확인해주세요.</p>${list(reviewDiff(r.snapshot, fresh.snapshot))}<details><summary>비교할 현재 내용</summary>${snapshotView(fresh.snapshot)}</details><form data-no-session-draft><label class="field">기준 갱신 사유<textarea name="reason" required minlength="5" maxlength="2000" rows="3"></textarea></label><p class="form-error" data-error role="alert"></p><button type="submit" class="btn btn-primary">현재 기준으로 다시 점검</button></form>`,
              (dialog) =>
                bindForm(dialog.querySelector("form"), async (d) => {
                  await ctx.api(
                    `/api/studio/courses/${cid}/publication/reviews/${r.id}`,
                    {
                      method: "POST",
                      body: {
                        version: r.version,
                        action: "rebase",
                        courseVersion: fresh.courseVersion,
                        basisHash: fresh.hash,
                        checks: local,
                        note,
                        reason: d.reason,
                      },
                    },
                  );
                  dialog.close();
                  ctx.toast("새 기준에 맞춰 확인 표시를 초기화했습니다.");
                  ctx.reload();
                }),
            );
          } catch (e) {
            ctx.toast(e.message, "error");
          }
        };
      const comment = root.querySelector("#review-comment");
      if (comment)
        bindForm(comment, async (d) => {
          savedFirst();
          await ctx.api(
            `/api/studio/courses/${cid}/publication/reviews/${r.id}/comments`,
            { method: "POST", body: d },
          );
          ctx.toast("보완 의견을 남겼습니다.");
          ctx.reload();
        });
    },
  };
}
async function queue(params, ctx) {
  const state = params.get("status") || "submitted",
    data = await ctx.api(
      `/api/studio/course-reviews?${new URLSearchParams({ page: params.get("page") || 1, status: state })}`,
    );
  return {
    html: `<section class="container p-page">${head("PUBLICATION DESK", "강의 발행 검수", "제출 원문과 품질 근거를 확인하고, 보완부터 발행까지 이어갑니다.")}${opsNav(ctx, "publications")}<form id="publication-filter" class="p-filters"><label class="field">품질 검수 상태<select name="status"><option value="all" ${state === "all" ? "selected" : ""}>전체 상태</option>${Object.entries(
      publicationStates,
    )
      .map(
        ([k, v]) =>
          `<option value="${k}" ${k === state ? "selected" : ""}>${v}</option>`,
      )
      .join(
        "",
      )}</select></label><button type="submit" class="btn btn-outline">조회</button></form><div class="p-discussion-list">${data.reviews.map((r) => `<a class="p-panel" href="#${link(r.courseId, r.id)}"><div>${tag(publicationStates[r.status], r.status === "published" ? "mint" : "")}${r.stale ? tag("대상 변경·재점검 필요") : ""}</div><h2>${esc(r.courseTitle)}</h2><p>제출 기준 상품 v${r.baseVersion} · ${date(r.createdAt)}</p><p>${esc(r.decisionReason || r.reviewNote || r.authorNote || "항목별 품질 점검과 확인 근거")}</p></a>`).join("") || empty("이 상태의 검수 요청이 없습니다.", "강의 스튜디오에서 품질 점검을 만들고 요청하세요.")}</div>${pager("/studio/publications", params, data.page, data.total)}</section>`,
    bind() {
      bindForm("#publication-filter", async (d) =>
        ctx.navigate("/studio/publications?" + new URLSearchParams(d)),
      );
    },
  };
}
