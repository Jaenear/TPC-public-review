export const publicationStates = {
  draft: "점검 작성",
  submitted: "관리자 검수 대기",
  changes_requested: "보완 요청",
  published: "검수 발행 완료",
  withdrawn: "요청 회수",
};
export const checkStates = {
  pending: "미확인",
  confirmed: "작성자 확인",
  pass: "검수 통과",
  changes: "수정 필요",
};
function canonical(value) {
  if (Array.isArray(value)) return value.map(canonical);
  if (value && typeof value === "object")
    return Object.fromEntries(
      Object.keys(value)
        .sort()
        .map((key) => [key, canonical(value[key])]),
    );
  return value;
}
const equal = (a, b) =>
  JSON.stringify(canonical(a)) === JSON.stringify(canonical(b));
export function normalizedChecks(checklist, values = {}) {
  return Object.fromEntries(
    checklist.map(({ id }) => [
      id,
      { state: values[id]?.state || "pending", note: values[id]?.note || "" },
    ]),
  );
}
export function sameChecks(checklist, a, b) {
  return (
    JSON.stringify(normalizedChecks(checklist, a)) ===
    JSON.stringify(normalizedChecks(checklist, b))
  );
}
export function readyChecks(checklist, checks, reviewer = false) {
  const wanted = reviewer ? "pass" : "confirmed";
  return (
    checklist.length > 0 &&
    checklist.every(
      ({ id }) =>
        checks[id]?.state === wanted && checks[id].note.trim().length >= 5,
    )
  );
}
export function reviewDiff(before, after) {
  if (!before || !after) return [];
  const result = [];
  const titles = {
    title: "강의명",
    subtitle: "한 줄 소개",
    description: "강의 소개",
    category: "주제",
    level: "난이도",
    price: "기획 제안 금액",
    duration: "소요 시간",
    pilot: "파일럿 구분",
    instructor_user_id: "담당 코치",
    status: "강의 상태",
    version: "상품 버전",
  };
  for (const key of Object.keys(titles))
    if (before.course[key] !== after.course[key])
      result.push(titles[key] + " 변경");
  if (before.instructorEligible !== after.instructorEligible)
    result.push("담당 코치의 현재 역할/활성 상태 변경");
  if (!equal(before.sections, after.sections))
    result.push("섹션 이름/순서/구성 변경");
  const old = new Map(before.lessons.map((l) => [l.id, l])),
    current = new Map(after.lessons.map((l) => [l.id, l]));
  for (const l of before.lessons) {
    if (!current.has(l.id)) result.push(`차시 제외: ${l.title}`);
    else if (!equal(l, current.get(l.id))) result.push(`차시 변경: ${l.title}`);
  }
  for (const l of after.lessons)
    if (!old.has(l.id)) result.push(`차시 추가: ${l.title}`);
  for (const [key, title] of [
    ["resources", "학습 자료"],
    ["videos", "영상 원본"],
  ])
    if (!equal(before[key], after[key]))
      result.push(title + " 연결/제공 상태 변경");
  return result;
}
