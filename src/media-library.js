import { esc } from "./ui.js";
import {
  head,
  empty,
  authGate,
  date,
  tag,
  pager,
  opsNav,
  bindForm,
} from "./platform-ui.js";
import { mediaUploadMarkup, bindMediaUploader } from "./media-upload.js";
import { mediaUploadStates } from "./media-upload-model.js";
export async function renderMediaLibrary(path, params, ctx) {
  const match = path.match(/^\/studio\/course\/([a-z0-9-]+)\/media$/);
  if (!match) return null;
  if (!ctx.user)
    return authGate(ctx, "강의 영상 원본과 전송 상태를 관리하세요.");
  if (ctx.user.role !== "admin" && !ctx.user.roles?.includes("coach"))
    return {
      html: `<section class="container p-page">${empty("영상 관리 권한이 필요합니다.", "담당 코치와 관리자만 이용할 수 있습니다.")}</section>`,
    };
  const cid = match[1],
    data = await ctx.api(
      `/api/studio/courses/${cid}/media-uploads?${new URLSearchParams({ page: params.get("page") || 1, status: params.get("status") || "pending" })}`,
    ),
    course = (await ctx.api(`/api/studio/courses/${cid}`)).course;
  const cleanup = data.uploads
    .filter(
      (u) =>
        u.status === "expired" ||
        (u.status === "cancelled" && u.cleanupPending),
    )
    .map((u) => u.id);
  return {
    title: course.title + " · 영상 원본/전송",
    html: `<section class="container p-page" id="media-library"><a class="back-link" href="#/studio/course/${cid}">← 강의 스튜디오</a>${head("PRIVATE MEDIA WORKSPACE", course.title, "확인한 전송 위치부터 복구하고, 저장한 원본을 수업과 연결합니다.")}${opsNav(ctx, "courses")}<div class="p-form-actions"><a class="btn btn-outline" href="#/studio/course/${cid}/publication">품질 점검·발행 검수 ↗</a><a class="btn btn-outline" href="#/studio/course/${cid}/curriculum">커리큘럼 구성 ↗</a></div><div class="p-panel">${mediaUploadMarkup(cid)}</div><section class="p-panel"><h2>보관한 전송</h2><form id="media-list-filter" class="p-filters"><label class="field">업로드 상태<select name="status">${[
      ["pending", "보관 중·만료"],
      ["completed", "원본 저장 완료"],
      ["cancelled", "취소한 전송"],
      ["all", "전체 전송"],
    ]
      .map(
        ([k, v]) =>
          `<option value="${k}" ${k === (params.get("status") || "pending") ? "selected" : ""}>${v}</option>`,
      )
      .join(
        "",
      )}</select></label><button class="btn btn-outline" type="submit">조회</button></form>${data.uploads.map((u) => `<article class="p-media-upload-record"><header><strong>${esc(u.filename)}</strong>${tag(mediaUploadStates[u.status], u.status === "completed" ? "mint" : "")}</header><p>${(u.offset / 1024 / 1024).toFixed(1)} / ${(u.size / 1024 / 1024).toFixed(1)}MB · 보관 기한 ${date(u.expiresAt)}</p><small>${date(u.createdAt)} · 확인된 전송은 재시작 뒤에도 유지됩니다.</small>${["uploading", "ready"].includes(u.status) ? `<button class="btn btn-outline btn-sm" data-select-upload="${u.id}">이 전송 이어 보내기</button>` : ""}</article>`).join("") || "<p>이 상태의 보관 전송이 없습니다.</p>"}${pager(`/studio/course/${cid}/media`, params, data.page, data.total)}${cleanup.length ? '<button type="button" class="btn btn-outline" id="clean-expired-media">이 목록의 만료·취소 임시 파일 정리</button>' : ""}</section><section class="p-panel"><h2>저장된 영상 원본</h2><p class="p-muted">미완료 조각은 원본 목록에 포함하지 않습니다. 파일을 선택해 차시에 연결한 뒤 저장/검수를 진행하세요.</p><div class="p-media-originals">${course.assets.map((a) => `<article><h3>${esc(a.filename)}</h3><p>${(a.size / 1024 / 1024).toFixed(1)}MB · ${date(a.createdAt)}</p><video controls preload="none" playsinline aria-label="${esc(a.filename)} 원본 확인" src="/api/media/${esc(a.id)}"></video><a class="btn btn-outline btn-sm" href="#/studio/course/${cid}/media/${esc(a.id)}/captions">자막 등록·검수 ↗</a></article>`).join("") || "<p>저장된 원본이 없습니다.</p>"}</div></section></section>`,
    async bind() {
      await bindMediaUploader(
        document.querySelector("[data-media-uploader]"),
        cid,
        ctx,
        () => ctx.reload(),
      );
      bindForm("#media-list-filter", async (d) =>
        ctx.navigate(`/studio/course/${cid}/media?` + new URLSearchParams(d)),
      );
      document.querySelectorAll("[data-select-upload]").forEach(
        (b) =>
          (b.onclick = () => {
            const select = document.querySelector("[data-media-session]");
            select.value = b.dataset.selectUpload;
            select.dispatchEvent(new Event("change"));
          }),
      );
      const clean = document.querySelector("#clean-expired-media");
      if (clean)
        clean.onclick = () =>
          ctx.modal(
            '<h2>만료·취소 임시 전송 정리</h2><p>현재 목록에서 기한이 지났거나 취소한 전송의 임시 조각을 정리하고 기록을 보관합니다.</p><form data-no-session-draft><label class="field">정리 사유<textarea name="reason" required minlength="5" maxlength="2000" rows="3"></textarea></label><p class="form-error" data-error role="alert"></p><button type="submit" class="btn btn-primary">임시 파일 정리 요청</button></form>',
            (dialog) =>
              bindForm(dialog.querySelector("form"), async (d) => {
                const result = await ctx.api(
                  `/api/studio/courses/${cid}/media-uploads/cleanup`,
                  {
                    method: "POST",
                    body: { uploadIds: cleanup, reason: d.reason },
                  },
                );
                dialog.close();
                ctx.toast(
                  result.uploads.some((u) => u.cleanupPending)
                    ? "일부 임시 파일은 추가 정리가 필요합니다."
                    : "임시 전송 파일을 정리했습니다.",
                );
                ctx.reload();
              }),
          );
    },
  };
}
