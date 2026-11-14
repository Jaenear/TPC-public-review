import {
  captionStates,
  captionUploadIntent,
  captionReviewIntent,
} from "./caption-model.js";
import {
  head,
  empty,
  authGate,
  date,
  tag,
  pager,
  opsNav,
} from "./platform-ui.js";
import { watchTime } from "./video-model.js";
const base = (cid) => `/api/studio/courses/${encodeURIComponent(cid)}`;
const listPath = (cid, aid) =>
  `/studio/course/${encodeURIComponent(cid)}/media/${encodeURIComponent(aid)}/captions`;
const exactPath = (cid, id) =>
  `/studio/course/${encodeURIComponent(cid)}/caption/${encodeURIComponent(id)}`;
const actions = {
  approve: "자막 승인",
  reject: "보완 요청",
  withdraw: "승인 자막 철회",
  cancel: "검수 요청 취소",
};
export async function renderCaptionPage(path, params, ctx) {
  const list = path.match(
      /^\/studio\/course\/([a-z0-9-]+)\/media\/([a-z0-9_]+)\/captions$/,
    ),
    exact = path.match(
      /^\/studio\/course\/([a-z0-9-]+)\/caption\/(caption_[a-z0-9]+)$/,
    );
  if (!list && !exact) return null;
  if (!ctx.user)
    return authGate(ctx, "담당 영상의 자막을 등록하고 검수 상태를 확인하세요.");
  if (ctx.user.role !== "admin" && !ctx.user.roles?.includes("coach"))
    return {
      html: `<section class="container p-page">${empty("자막 관리 권한이 필요합니다.", "담당 코치와 관리자만 이용할 수 있습니다.")}</section>`,
    };
  return list
    ? listing(list[1], list[2], params, ctx)
    : detail(exact[1], exact[2], ctx);
}
async function listing(cid, aid, params, ctx) {
  const data = await ctx.api(
      `${base(cid)}/media/${encodeURIComponent(aid)}/captions?${new URLSearchParams({ page: params.get("page") || 1 })}`,
    ),
    e = ctx.esc;
  return {
    title: data.courseTitle + " · 자막 관리",
    html: `<section class="container p-page cap-workspace" id="caption-workspace"><a class="back-link" href="#/studio/course/${cid}/media">← 영상 원본·전송 관리</a>${head("VIDEO CAPTION WORKSPACE", data.courseTitle, "영상 원본에 맞는 자막을 등록하고, 확인한 판본만 제공합니다.")}${opsNav(ctx, "courses")}<div class="cap-layout"><section class="p-panel"><p class="eyebrow">CURRENT ORIGINAL</p><h2>${e(data.video.filename)}</h2><p class="p-muted">영상 길이 ${watchTime(data.video.durationSeconds)} · ${e(data.profile)}</p><details><summary>파일 규격·전송 안내</summary><p class="p-muted">UTF-8 WebVTT, 파일당 1MB·10,000개 구간 이내입니다. NOTE·일반 자막 태그와 위치 설정을 지원하며 STYLE/REGION 블록은 받지 않습니다. 원본 길이를 확인할 수 없는 영상은 승인 전에 확인이 필요합니다.</p><p class="p-muted">전송 결과가 불확실하면 같은 요청으로 확인합니다. 새로고침한 경우 먼저 목록을 확인하고 파일을 다시 선택해주세요.</p></details><form id="caption-upload-form" data-no-session-draft><fieldset><label class="field">WebVTT 원본 파일<input type="file" name="file" accept=".vtt,text/vtt" required></label><div class="cap-fields"><label class="field">자막 언어<input name="language" value="ko" maxlength="63" required placeholder="ko, en, ko-KR"></label><label class="field">표시 이름<input name="label" value="한국어" minlength="2" maxlength="80" required></label></div><label class="field">자막 종류<select name="kind"><option value="captions">대사·소리 설명 자막</option><option value="subtitles">대사·번역 자막</option></select></label><button class="btn btn-primary" type="submit">자막 등록·검수 요청</button></fieldset><p class="cap-feedback" role="status" id="caption-upload-status"></p><button type="button" class="btn btn-outline" id="caption-upload-retry" hidden>같은 자막 등록 다시 확인</button><button type="button" class="btn btn-outline" id="caption-upload-refresh" hidden>현재 자막 목록 다시 확인</button></form></section><section class="p-panel"><h2>원본별 자막 판본</h2><p class="p-muted">등록한 파일은 보존하며, 승인/교체/철회는 이력으로 남깁니다.</p>${data.captions.map((c) => `<article class="cap-record"><div class="cap-record-head"><h3>${e(c.label)}</h3>${tag(captionStates[c.status], c.status === "approved" ? "mint" : "")}</div><p>${e(c.language)} · ${e(c.filename)} · ${c.cueCount}개 구간 / ${watchTime(c.endSeconds)}</p><p class="p-muted">${date(c.createdAt)} · v${c.version}</p><a class="btn btn-outline btn-sm" href="#${exactPath(cid, c.id)}">자막 상세·검수 이력 ↗</a></article>`).join("") || '<p class="p-muted">등록된 자막이 없습니다.</p>'}${pager(listPath(cid, aid), params, data.page, data.total)}</section></div></section>`,
    bind() {
      const root = document.querySelector("#caption-workspace"),
        form = root.querySelector("#caption-upload-form"),
        status = root.querySelector("#caption-upload-status"),
        retry = root.querySelector("#caption-upload-retry"),
        refresh = root.querySelector("#caption-upload-refresh"),
        ownerId = ctx.user.id;
      let intent = null,
        busy = false,
        uncertain = false,
        blocked = false;
      const draw = () => {
        form.querySelector("fieldset").disabled = busy || uncertain || blocked;
        retry.hidden = !uncertain;
        retry.disabled = busy;
      };
      async function upload() {
        if (busy || blocked) return;
        const values = intent ? null : Object.fromEntries(new FormData(form));
        const chosenFile = intent ? null : form.elements.file.files[0];
        busy = true;
        draw();
        try {
          if (!intent) {
            status.textContent = "원본 파일 확인 중…";
            intent = await captionUploadIntent(chosenFile, values, data.video);
          }
          status.textContent = "자막을 전송하고 검수 요청을 확인합니다…";
          const result = await ctx.api(
            `${base(cid)}/media/${encodeURIComponent(aid)}/captions`,
            {
              method: "POST",
              ownerId,
              rawBody: intent.body,
              headers: {
                "Content-Type": "text/vtt",
                "X-Caption-Metadata": encodeURIComponent(
                  JSON.stringify(intent.metadata),
                ),
              },
            },
          );
          if (!root.isConnected) return;
          ctx.toast(
            result.replayed
              ? "같은 자막 등록 기록을 확인했습니다."
              : "자막을 검수 요청했습니다.",
          );
          ctx.navigate(exactPath(cid, result.caption.id));
        } catch (error) {
          if (!root.isConnected) return;
          status.textContent = error.message;
          if (!intent) {
            uncertain = false;
          } else if (
            [400, 401, 403, 404, 409, 413, 415].includes(error.status)
          ) {
            blocked = true;
            uncertain = false;
            refresh.hidden = false;
          } else {
            uncertain = true;
            status.textContent +=
              " 같은 파일·요청으로 결과를 다시 확인할 수 있습니다.";
          }
        } finally {
          busy = false;
          if (root.isConnected) draw();
        }
      }
      form.addEventListener("submit", (event) => {
        event.preventDefault();
        if (form.reportValidity()) upload();
      });
      retry.addEventListener("click", upload);
      refresh.addEventListener("click", () => ctx.reload());
    },
  };
}
async function detail(cid, rid, ctx) {
  const data = await ctx.api(`${base(cid)}/captions/${rid}`),
    c = data.caption,
    e = ctx.esc;
  const available = [];
  if (c.status === "pending") {
    if (data.canReview) available.push("approve", "reject");
    if (data.canCancel) available.push("cancel");
  } else if (c.status === "approved" && data.canReview)
    available.push("withdraw");
  return {
    title: c.label + " · 자막 검수",
    html: `<section class="container p-page cap-workspace" id="caption-detail"><a class="back-link" href="#${listPath(cid, c.mediaAssetId)}">← 이 영상의 자막 목록</a>${head("CAPTION REVIEW", c.label, "등록한 원문과 영상의 타이밍을 확인하고 검수 근거를 남깁니다.")}${opsNav(ctx, "courses")}<div class="p-form-actions">${tag(captionStates[c.status], c.status === "approved" ? "mint" : "")}<span class="pill">v${c.version} · ${e(c.language)}</span>${available.map((action) => `<button type="button" class="btn ${action === "approve" ? "btn-primary" : "btn-outline"}" data-caption-review="${action}">${actions[action]}</button>`).join("")}</div><div class="cap-layout"><section class="p-panel"><h2>담당자 원본 미리보기</h2><p class="p-muted">검수 전/과거 자막은 담당자 미리보기에서만 열 수 있습니다. 학습 화면에는 현재 승인본만 제공합니다.</p><video class="cap-preview" controls preload="metadata" playsinline aria-label="자막 원본 검수 영상" src="/api/media/${e(c.mediaAssetId)}"><track kind="${e(c.kind)}" srclang="${e(c.language)}" label="${e(c.label)}" src="${base(cid)}/captions/${rid}/file" default></video><p class="cap-feedback" role="status" id="caption-preview-status">${e(c.filename)} · ${c.cueCount}개 구간 · 마지막 ${watchTime(c.endSeconds)}</p><details><summary>등록 원본·첫 구간 확인</summary><p class="p-muted">자막 원본 ${e(c.sha256.slice(0, 16))} · 영상 원본 ${e(c.mediaSha256.slice(0, 16))}</p><a class="text-link" href="${base(cid)}/captions/${rid}/file" target="_blank" rel="noopener">등록 WebVTT 원문 열기 ↗</a><ol class="cap-cues">${data.previewCues.map((q) => `<li><small>${watchTime(q.start)}–${watchTime(q.end)}</small><pre>${e(q.text)}</pre></li>`).join("")}</ol></details></section><section class="p-panel"><h2>검수와 변경 이력</h2>${data.activeCaption && data.activeCaption.id !== rid ? `<p class="p-notice">현재 같은 언어·종류의 승인본: ${e(data.activeCaption.label)} · v${data.activeCaption.version}. 이 자막을 승인하면 해당 승인본을 함께 교체합니다.</p>` : ""}<p class="p-muted">${e(c.reviewNote || "아직 검수 결과가 없습니다.")}</p>${c.approvalReference ? `<details><summary>승인 확인 근거</summary><p>${e(c.approvalReference)}</p></details>` : ""}${data.events.map((event) => `<article class="cap-event"><strong>${e({ submitted: "검수 요청", approved: "승인", sample_approved: "자체 샘플 등록", superseded: "새 판본으로 교체", withdrawn: "철회", rejected: "보완 요청", cancelled: "요청 취소" }[event.action] || event.action)}</strong><small>${date(event.created_at)} · v${event.result_version}</small><p>${e(event.reason)}</p></article>`).join("")}</section></div></section>`,
    bind() {
      const root = document.querySelector("#caption-detail");
      root.querySelector("track").addEventListener("error", () => {
        root.querySelector("#caption-preview-status").textContent =
          "미리보기 자막을 불러오지 못했습니다. 원본과 현재 담당 권한을 확인해주세요.";
      });
      root
        .querySelectorAll("[data-caption-review]")
        .forEach((button) =>
          button.addEventListener("click", () =>
            reviewDialog(button.dataset.captionReview, cid, data, ctx),
          ),
        );
    },
  };
}
function reviewDialog(action, cid, data, ctx) {
  const c = data.caption,
    e = ctx.esc;
  ctx.modal(
    `<h2>${actions[action]}</h2><p class="dialog-description">${action === "approve" ? (data.activeCaption ? "현재 승인본 교체와 새 자막 승인을 함께 처리합니다." : "확인한 자막 판본을 학습 화면에 제공합니다.") : "원문과 학습 기록은 보존하고 제공/검수 상태를 바꿉니다."}</p><form id="caption-review-form" data-no-session-draft><fieldset>${action === "approve" ? '<label class="field">승인 확인 근거<textarea name="approvalReference" minlength="5" maxlength="2000" required rows="3"></textarea></label><label class="cap-confirm"><input type="checkbox" name="confirmed" required>영상 원본·자막 내용과 타이밍을 확인했습니다.</label>' : ""}<label class="field">처리 사유<textarea name="reason" minlength="5" maxlength="2000" required rows="3"></textarea></label><button class="btn btn-primary" type="submit">${actions[action]} 확인</button></fieldset><p class="cap-feedback" role="status"></p><button class="btn btn-outline" type="button" data-caption-retry hidden>같은 검수 요청 다시 확인</button><button class="btn btn-outline" type="button" data-caption-refresh hidden>최신 검수 상태 다시 확인</button></form>`,
    (dialog) => {
      const form = dialog.querySelector("form"),
        status = form.querySelector("[role=status]"),
        retry = form.querySelector("[data-caption-retry]"),
        refresh = form.querySelector("[data-caption-refresh]"),
        ownerId = ctx.user.id;
      let body = null,
        busy = false,
        uncertain = false,
        blocked = false;
      function draw() {
        form.querySelector("fieldset").disabled = busy || uncertain || blocked;
        retry.hidden = !uncertain;
        retry.disabled = busy;
        dialog.dataset.busy = String(busy);
        dialog.querySelector(".dialog-close").disabled = busy;
      }
      async function send() {
        if (busy || blocked) return;
        const values = Object.fromEntries(new FormData(form));
        body ||= captionReviewIntent(c, data.activeCaption, action, {
          ...values,
          confirmed: form.elements.confirmed?.checked,
        });
        busy = true;
        draw();
        status.textContent = "검수 결과를 확인합니다…";
        try {
          const result = await ctx.api(`${base(cid)}/captions/${c.id}/review`, {
            method: "POST",
            ownerId,
            body,
          });
          if (!dialog.open) return;
          dialog.close();
          ctx.toast(
            result.replayed
              ? "같은 요청의 처리와 현재 자막 상태를 확인했습니다."
              : "자막 검수 상태를 저장했습니다.",
          );
          ctx.reload();
        } catch (error) {
          if (!dialog.open) return;
          status.textContent = error.message;
          if ([400, 401, 403, 404, 409].includes(error.status)) {
            blocked = true;
            uncertain = false;
            refresh.hidden = false;
          } else {
            uncertain = true;
            status.textContent +=
              " 같은 요청으로 결과를 다시 확인할 수 있습니다.";
          }
        } finally {
          busy = false;
          if (dialog.open) draw();
        }
      }
      form.addEventListener("submit", (event) => {
        event.preventDefault();
        if (form.reportValidity()) send();
      });
      retry.addEventListener("click", send);
      refresh.addEventListener("click", () => {
        dialog.close();
        ctx.reload();
      });
    },
  );
}
