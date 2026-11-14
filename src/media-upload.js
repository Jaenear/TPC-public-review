import { esc } from "./ui.js";
import { bindForm } from "./platform-ui.js";
import {
  transferMedia,
  mediaUploadStates,
  validateVideoFile,
} from "./media-upload-model.js";
export function mediaUploadMarkup(courseId) {
  return `<section class="p-media-upload" data-media-uploader><h2>영상 전송과 복구</h2><p>MP4 최대100MB. 중단한 전송은 같은 파일을 다시 선택해 이어 보냅니다. 임시 전송은24시간·계정별5개까지 보관합니다.</p><label class="field">이어 보낼 전송<select data-media-session><option value="">새 업로드</option></select></label><label class="field">새 MP4 파일<input type="file" data-media-file accept="video/mp4,.mp4"/></label><progress data-media-progress aria-label="영상 전송 진행" max="100" value="0"></progress><p class="p-muted" data-media-state role="status" aria-live="polite">파일을 선택하거나 보관한 전송을 확인하세요.</p><div class="p-form-actions"><button class="btn btn-outline btn-sm" type="button" data-media-refresh>진행 상태 확인</button><button class="btn btn-primary btn-sm" type="button" data-media-start>전송 시작·이어 보내기</button><button class="btn btn-outline btn-sm" type="button" data-media-pause disabled>일시정지</button><button class="btn btn-quiet btn-sm" type="button" data-media-cancel>이 전송 취소</button></div><p class="form-note">화면을 떠나면 전송을 멈춥니다. 서버가 확인한 조각은 보관하며, 파일은 이 브라우저의 저장소에 보관하지 않습니다. 원본 저장 뒤 차시에 연결하고 저장/검수를 진행해주세요.</p><a class="text-link" href="#/studio/course/${esc(courseId)}/media" target="_blank" rel="noopener">영상 원본·전송 관리 ↗</a></section>`;
}
export async function bindMediaUploader(
  root,
  courseId,
  ctx,
  onComplete = () => {},
) {
  if (!root) return;
  const select = root.querySelector("[data-media-session]"),
    file = root.querySelector("[data-media-file]"),
    progress = root.querySelector("[data-media-progress]"),
    status = root.querySelector("[data-media-state]");
  let current = null,
    controller = null,
    requestKey = crypto.randomUUID(),
    fingerprint = "";
  const connected = () => root.isConnected;
  const busy = (value) => {
    for (const key of ["session", "file", "refresh", "start", "cancel"])
      root.querySelector(`[data-media-${key}]`).disabled = value;
    root.querySelector("[data-media-pause]").disabled = !value;
  };
  function draw(upload, phase = "checking") {
    if (!connected()) return;
    if (upload) {
      current = upload;
      progress.value = upload.size ? (100 * upload.offset) / upload.size : 0;
      if (![...select.options].some((o) => o.value === upload.id)) {
        const option = document.createElement("option");
        option.value = upload.id;
        select.append(option);
      }
      const option = [...select.options].find((o) => o.value === upload.id);
      option.textContent = `${upload.filename} · ${mediaUploadStates[upload.status] || upload.status} · ${Math.floor((upload.offset * 100) / upload.size)}%`;
      select.value = upload.id;
    }
    const label = {
      hashing: "같은 원본 파일인지 확인하고 있습니다…",
      checking: "서버가 확인한 진행 상태",
      uploading: "확인한 위치까지 전송했습니다.",
      repairing: "누락된 전송 조각을 복구했습니다.",
      finalizing: "전체 원본을 확인하고 저장합니다…",
      completed: "원본 저장 완료. 차시에 연결하고 저장/검수를 진행해주세요.",
    }[phase];
    status.textContent = upload
      ? `${label || "전송 상태"} · ${mediaUploadStates[upload.status] || upload.status} · ${(upload.offset / 1024 / 1024).toFixed(1)} / ${(upload.size / 1024 / 1024).toFixed(1)}MB${upload.missingChunks?.length ? ` · 복구할 조각 ${upload.missingChunks.length}개` : ""}`
      : label || "파일을 선택해주세요.";
  }
  async function refresh() {
    const selected = select.value;
    const data = await ctx.api(
      `/api/studio/courses/${courseId}/media-uploads?status=pending`,
    );
    if (!connected()) return;
    select.innerHTML =
      '<option value="">새 업로드</option>' +
      data.uploads
        .map(
          (u) =>
            `<option value="${esc(u.id)}">${esc(u.filename)} · ${esc(mediaUploadStates[u.status])} · ${Math.floor((u.offset * 100) / u.size)}%</option>`,
        )
        .join("");
    if (selected) {
      select.value = selected;
      if (select.value !== selected) {
        const option = document.createElement("option");
        option.value = selected;
        option.textContent = current?.filename || "선택한 전송";
        select.append(option);
        select.value = selected;
      }
      draw(
        (
          await ctx.api(
            `/api/studio/courses/${courseId}/media-uploads/${selected}`,
          )
        ).upload,
      );
    }
  }
  select.onchange = async () => {
    current = null;
    requestKey = crypto.randomUUID();
    fingerprint = "";
    progress.value = 0;
    if (select.value) {
      try {
        draw(
          (
            await ctx.api(
              `/api/studio/courses/${courseId}/media-uploads/${select.value}`,
            )
          ).upload,
        );
      } catch (e) {
        status.textContent = e.message;
      }
    } else status.textContent = "새 원본 파일을 선택하고 전송을 시작하세요.";
  };
  file.onchange = () => {
    if (!file.files[0]) return;
    try {
      validateVideoFile(file.files[0]);
      if (!select.value) {
        const next = [
          file.files[0].name,
          file.files[0].size,
          file.files[0].lastModified,
        ].join(":");
        if (fingerprint !== next) {
          requestKey = crypto.randomUUID();
          fingerprint = next;
        }
      }
      status.textContent =
        "파일을 선택했습니다. 전송 시작/이어 보내기를 눌러주세요.";
    } catch (e) {
      status.textContent = e.message;
    }
  };
  root.querySelector("[data-media-refresh]").onclick = () =>
    refresh().catch((e) => {
      if (connected()) status.textContent = e.message;
    });
  root.querySelector("[data-media-pause]").onclick = () => controller?.abort();
  root.querySelector("[data-media-start]").onclick = async () => {
    controller = new AbortController();
    const signal = controller.signal;
    const stop = () => controller?.abort();
    window.addEventListener("hashchange", stop, { once: true });
    busy(true);
    try {
      const upload = await transferMedia({
        api: ctx.api,
        courseId,
        file: file.files[0],
        uploadId: select.value || null,
        requestKey,
        signal,
        onState: ({ phase, upload }) => draw(upload, phase),
      });
      if (connected()) {
        draw(upload, "completed");
        await onComplete(upload.asset);
      }
    } catch (e) {
      if (connected())
        status.textContent = signal.aborted
          ? "일시정지했습니다. 진행 상태를 확인한 뒤 같은 파일을 이어 보내세요."
          : e.status === 401
            ? "로그인 후 같은 파일을 다시 선택하고 진행 상태를 확인해주세요."
            : e.message;
    } finally {
      window.removeEventListener("hashchange", stop);
      if (connected()) busy(false);
      controller = null;
    }
  };
  root.querySelector("[data-media-cancel]").onclick = () => {
    if (!select.value) {
      status.textContent = "취소할 보관 전송을 선택해주세요.";
      return;
    }
    const uid = select.value;
    ctx.modal(
      '<h2>이 전송 취소</h2><p>선택한 미완료 전송의 임시 조각을 정리하고 기록을 보관합니다.</p><form data-no-session-draft><label class="field">취소 사유<textarea name="reason" required minlength="5" maxlength="2000" rows="3"></textarea></label><p class="form-error" data-error role="alert"></p><button class="btn btn-primary" type="submit">전송 취소·임시 파일 정리</button></form>',
      (dialog) =>
        bindForm(dialog.querySelector("form"), async (d) => {
          const latest = (
            await ctx.api(
              `/api/studio/courses/${courseId}/media-uploads/${uid}`,
            )
          ).upload;
          const result = await ctx.api(
            `/api/studio/courses/${courseId}/media-uploads/${uid}`,
            {
              method: "POST",
              body: {
                action: "cancel",
                version: latest.version,
                reason: d.reason,
              },
            },
          );
          dialog.close();
          draw(result.upload);
          status.textContent = result.upload.cleanupPending
            ? "전송을 취소했습니다. 영상 관리에서 남은 임시 파일 정리를 다시 요청해주세요."
            : "전송을 취소하고 임시 파일을 정리했습니다.";
        }),
    );
  };
  await refresh().catch((e) => {
    if (connected()) status.textContent = e.message;
  });
}
