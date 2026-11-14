export const captionStates = {
  pending: "검수 대기",
  approved: "제공 중",
  rejected: "보완 필요",
  withdrawn: "철회됨",
  superseded: "교체됨",
  cancelled: "요청 취소",
};
export async function captionUploadIntent(
  file,
  values,
  video,
  key = () => crypto.randomUUID(),
) {
  if (
    !file ||
    !/\.vtt$/i.test(file.name) ||
    /[/\\]/.test(file.name) ||
    file.size < 8 ||
    file.size > 1024 * 1024
  )
    throw new Error("1MB 이내의 .vtt 원본 파일을 선택해주세요.");
  const bytes = await file.arrayBuffer();
  if (bytes.byteLength !== file.size)
    throw new Error("파일을 끝까지 읽지 못했습니다. 다시 선택해주세요.");
  const sha256 = [
    ...new Uint8Array(await crypto.subtle.digest("SHA-256", bytes)),
  ]
    .map((n) => n.toString(16).padStart(2, "0"))
    .join("");
  const metadata = Object.freeze({
    filename: file.name,
    language: String(values.language).trim(),
    label: String(values.label).trim(),
    kind: values.kind,
    requestKey: key(),
    sha256,
    mediaSha256: video.sha256,
  });
  return Object.freeze({
    metadata,
    body: new Blob([bytes], { type: "text/vtt" }),
  });
}
export function captionReviewIntent(
  caption,
  active,
  action,
  values,
  key = () => crypto.randomUUID(),
) {
  if (
    !["approve", "reject", "withdraw", "cancel"].includes(action) ||
    !Number.isInteger(caption.version)
  )
    throw new Error("현재 자막과 검수 상태를 확인해주세요.");
  const body = {
    action,
    version: caption.version,
    reason: String(values.reason || "").trim(),
    requestKey: key(),
  };
  if (action === "approve")
    Object.assign(body, {
      confirmed: values.confirmed === true,
      approvalReference: String(values.approvalReference || "").trim(),
      activeCaptionId: active?.id || null,
      activeVersion: active?.version || null,
    });
  return Object.freeze(body);
}
