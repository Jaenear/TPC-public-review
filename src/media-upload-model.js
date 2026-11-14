export const mediaUploadStates = {
  uploading: "전송 보관 중",
  ready: "원본 확인 대기",
  completed: "원본 저장 완료",
  cancelled: "전송 취소",
  expired: "보관 기한 만료",
};
export const MAX_MEDIA_BYTES = 100 * 1024 * 1024;
export async function sha256(blob) {
  const value = await crypto.subtle.digest("SHA-256", await blob.arrayBuffer());
  return [...new Uint8Array(value)]
    .map((v) => v.toString(16).padStart(2, "0"))
    .join("");
}
export function validateVideoFile(file) {
  if (
    !file ||
    typeof file.name !== "string" ||
    !file.name.toLowerCase().endsWith(".mp4") ||
    file.size < 32 ||
    file.size > MAX_MEDIA_BYTES
  )
    throw new Error("32바이트~100MB의 MP4 파일을 선택해주세요.");
}
function paused(signal) {
  if (signal?.aborted)
    throw new Error(
      "전송을 일시정지했습니다. 서버가 확인한 위치부터 이어 보낼 수 있습니다.",
    );
}
function validProgress(upload) {
  if (
    !Number.isInteger(upload.offset) ||
    !Number.isInteger(upload.size) ||
    upload.offset < 0 ||
    upload.offset > upload.size ||
    !Number.isInteger(upload.chunkSize) ||
    upload.chunkSize < 1 ||
    upload.chunkSize > 1024 * 1024
  )
    throw new Error(
      "서버 전송 위치를 확인할 수 없습니다. 진행 상태를 다시 조회해주세요.",
    );
}
export async function transferMedia({
  api,
  courseId,
  file,
  uploadId,
  requestKey,
  onState = () => {},
  signal,
}) {
  const base = `/api/studio/courses/${courseId}/media-uploads`;
  let upload;
  if (uploadId) {
    upload = (await api(`${base}/${uploadId}`)).upload;
    validProgress(upload);
    onState({ phase: "checking", upload });
  }
  if (upload?.status === "cancelled" || upload?.status === "expired")
    throw new Error(
      "계속할 수 없는 전송입니다. 임시 전송을 정리하고 새 업로드를 시작해주세요.",
    );
  if (upload?.status === "completed") {
    paused(signal);
    return (
      await api(`${base}/${upload.id}`, {
        method: "POST",
        body: { action: "complete", version: upload.version },
        signal,
      })
    ).upload;
  }
  let hash;
  if (file) {
    validateVideoFile(file);
    onState({ phase: "hashing", upload, filename: file.name });
    hash = await sha256(file);
    paused(signal);
    if (
      upload &&
      (upload.size !== file.size ||
        upload.sha256 !== hash ||
        upload.filename !== file.name)
    )
      throw new Error(
        "이 전송을 시작한 것과 같은 이름·내용의 파일을 선택해주세요.",
      );
  } else if (
    !upload ||
    upload.status !== "ready" ||
    upload.missingChunks?.length
  )
    throw new Error("이어 보낼 같은 MP4 파일을 다시 선택해주세요.");
  if (!upload) {
    paused(signal);
    upload = (
      await api(base, {
        method: "POST",
        body: {
          filename: file.name,
          size: file.size,
          sha256: hash,
          requestKey,
        },
        signal,
      })
    ).upload;
    validProgress(upload);
    onState({ phase: "checking", upload });
    upload = (await api(`${base}/${upload.id}`)).upload;
  }
  if (!["uploading", "ready"].includes(upload.status))
    throw new Error(
      "서버 전송 상태가 바뀌었습니다. 진행 상태를 다시 조회해주세요.",
    );
  async function send(offset, repair = false) {
    paused(signal);
    const piece = file.slice(
      offset,
      Math.min(offset + upload.chunkSize, file.size),
    );
    const expected = offset + piece.size;
    const digest = await sha256(piece);
    paused(signal);
    const next = (
      await api(`${base}/${upload.id}/chunks`, {
        method: "PATCH",
        rawBody: piece,
        headers: {
          "Content-Type": "application/octet-stream",
          "Upload-Offset": String(offset),
          "X-Chunk-SHA256": digest,
        },
        signal,
      })
    ).upload;
    validProgress(next);
    if (
      next.offset < expected ||
      next.id !== upload.id ||
      next.size !== upload.size ||
      next.sha256 !== upload.sha256
    )
      throw new Error(
        "서버 확인 위치가 일치하지 않습니다. 전송 상태를 다시 조회해주세요.",
      );
    upload = next;
    onState({ phase: repair ? "repairing" : "uploading", upload });
  }
  for (const offset of upload.missingChunks || []) {
    if (
      !Number.isInteger(offset) ||
      offset < 0 ||
      offset >= upload.offset ||
      offset % upload.chunkSize !== 0
    )
      throw new Error("복구할 전송 위치를 확인할 수 없습니다.");
    await send(offset, true);
  }
  while (upload.offset < upload.size) await send(upload.offset);
  paused(signal);
  upload = (await api(`${base}/${upload.id}`)).upload;
  validProgress(upload);
  if (upload.missingChunks?.length || upload.offset !== upload.size)
    throw new Error(
      "복구할 조각이 있습니다. 진행 상태를 확인하고 다시 이어 보내주세요.",
    );
  onState({ phase: "finalizing", upload });
  paused(signal);
  upload = (
    await api(`${base}/${upload.id}`, {
      method: "POST",
      body: { action: "complete", version: upload.version },
      signal,
    })
  ).upload;
  if (upload.status !== "completed" || !upload.asset?.id)
    throw new Error("원본 저장 결과를 확인해주세요.");
  onState({ phase: "completed", upload });
  return upload;
}
