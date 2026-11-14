export const curriculumStates = {
  draft: "구성 초안",
  submitted: "검수 대기",
  rejected: "수정 요청",
  applied: "반영 완료",
  cancelled: "작성 중단",
};
export const copyLayout = (layout) => JSON.parse(JSON.stringify(layout));
const shape = (layout) =>
  layout.sections.map((s) => ({
    id: s.id,
    title: s.title,
    lessons: [...s.lessons],
  }));
export const sameLayout = (a, b) =>
  JSON.stringify(shape(a)) === JSON.stringify(shape(b));
export function validLayout(layout, current) {
  if (
    !layout ||
    !Array.isArray(layout.sections) ||
    !layout.sections.every(
      (s) =>
        s &&
        typeof s.id === "string" &&
        Array.isArray(s.lessons) &&
        s.lessons.every((id) => typeof id === "string"),
    )
  )
    return false;
  const sections = layout.sections.map((s) => s.id),
    ids = layout.sections.flatMap((s) => s.lessons || []);
  const expectedSections = current.sections.map((s) => s.id).sort(),
    expectedIds = current.sections.flatMap((s) => s.lessons).sort();
  return (
    sections.length === new Set(sections).size &&
    ids.length === new Set(ids).size &&
    JSON.stringify([...sections].sort()) === JSON.stringify(expectedSections) &&
    JSON.stringify([...ids].sort()) === JSON.stringify(expectedIds) &&
    layout.sections.every(
      (s) =>
        typeof s.title === "string" &&
        s.title.trim().length >= 2 &&
        s.title.trim().length <= 120,
    )
  );
}
export function moveSection(layout, id, delta) {
  const next = copyLayout(layout),
    i = next.sections.findIndex((s) => s.id === id),
    at = i + delta;
  if (i < 0 || at < 0 || at >= next.sections.length) return next;
  const [s] = next.sections.splice(i, 1);
  next.sections.splice(at, 0, s);
  return next;
}
export function moveLessons(layout, ids, target, at = null) {
  const next = copyLayout(layout),
    destination = next.sections.find((s) => s.id === target);
  const set = new Set(ids),
    ordered = next.sections
      .flatMap((s) => s.lessons)
      .filter((id) => set.has(id));
  if (!destination || ordered.length !== set.size)
    throw new Error("이동할 차시와 대상 섹션을 확인해주세요.");
  for (const s of next.sections)
    s.lessons = s.lessons.filter((id) => !set.has(id));
  const i =
    at == null
      ? destination.lessons.length
      : Math.max(0, Math.min(at, destination.lessons.length));
  destination.lessons.splice(i, 0, ...ordered);
  return next;
}
export function rebaseLayout(proposed, current) {
  const validIds = new Set(current.sections.flatMap((s) => s.lessons)),
    seen = new Set();
  const validSections = new Set(current.sections.map((s) => s.id));
  const next = {
    sections: proposed.sections
      .filter((s) => validSections.has(s.id))
      .map((s) => ({
        id: s.id,
        title: s.title,
        lessons: s.lessons.filter((id) => {
          if (!validIds.has(id) || seen.has(id)) return false;
          seen.add(id);
          return true;
        }),
      })),
  };
  for (const currentSection of current.sections) {
    let s = next.sections.find((s) => s.id === currentSection.id);
    if (!s) {
      s = { id: currentSection.id, title: currentSection.title, lessons: [] };
      next.sections.push(s);
    }
    for (const id of currentSection.lessons)
      if (!seen.has(id)) {
        s.lessons.push(id);
        seen.add(id);
      }
  }
  return next;
}
export function batchRows(value) {
  const rows = String(value)
    .split(/\r?\n/)
    .map((s) => s.trim())
    .filter(Boolean);
  if (!rows.length || rows.length > 50)
    throw new Error("차시 제목을 한 줄에 하나씩, 최대 50개 입력해주세요.");
  const lessons = rows.map((row) => {
    const parts = row.split("\t").map((s) => s.trim());
    if (
      parts.length > 2 ||
      parts[0].length < 2 ||
      parts[0].length > 120 ||
      (parts[1] || "").length > 40
    )
      throw new Error("제목은 2~120자, 시간은 선택하여 탭으로 구분해주세요.");
    return { title: parts[0], duration: parts[1] || "" };
  });
  if (new Set(lessons.map((s) => s.title)).size !== lessons.length)
    throw new Error("같은 묶음의 차시 제목이 중복됩니다.");
  return lessons;
}
export function layoutChanges(before, after) {
  const oldSections = new Map(
    before.sections.map((s, i) => [s.id, { ...s, index: i }]),
  );
  const oldLessons = new Map(
    before.sections.flatMap((s) =>
      s.lessons.map((id, i) => [id, { section: s.id, index: i }]),
    ),
  );
  const result = [];
  for (const [i, s] of after.sections.entries()) {
    const old = oldSections.get(s.id);
    if (!old) result.push({ type: "section-added", id: s.id });
    else if (old.title !== s.title || old.index !== i)
      result.push({ type: "section-changed", id: s.id });
    for (const [at, id] of s.lessons.entries()) {
      const was = oldLessons.get(id);
      if (!was || was.section !== s.id || was.index !== at)
        result.push({ type: was ? "lesson-moved" : "lesson-added", id });
    }
  }
  return result;
}
