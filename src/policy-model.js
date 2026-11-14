export const policyKinds = {
  terms: "이용 조건",
  privacy: "개인정보 안내",
  refund: "환불 안내",
  course_terms: "상품 제공 조건",
  content_rights: "콘텐츠 권리 증빙",
};
export const policyTiming = {
  draft: "초안",
  effective: "유효기간 내",
  scheduled: "시행 예정",
  expired: "기간 만료",
  withdrawn: "철회",
};

// Explicit current choices only. Old consent and restored drafts are never input.
export function consentPayload(
  bundle,
  accepted,
  context,
  mode,
  courseId = null,
) {
  if (
    !bundle?.ready ||
    !bundle.fingerprint ||
    bundle.context !== context ||
    bundle.mode !== mode ||
    (bundle.courseId || null) !== courseId
  )
    throw new Error("최신 정책과 제공 조건을 다시 확인해주세요.");
  const ids = bundle.requiredPolicyIds;
  const publicIds = (bundle.documents || [])
    .filter((d) => d.kind !== "content_rights" && d.timing === "effective")
    .map((d) => d.id);
  if (
    !Array.isArray(ids) ||
    !Array.isArray(accepted) ||
    !ids.length ||
    new Set(ids).size !== ids.length ||
    publicIds.length !== ids.length ||
    ids.some((id) => !publicIds.includes(id)) ||
    accepted.length !== ids.length ||
    new Set(accepted).size !== ids.length ||
    ids.some((id) => !accepted.includes(id))
  )
    throw new Error("원문을 읽고 필수 항목을 각각 체크해주세요.");
  return { policyDigest: bundle.fingerprint, acceptedPolicyIds: [...accepted] };
}
