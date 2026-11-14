export const profileStates = {
  draft: "작성 중",
  submitted: "검수 대기",
  approved: "공개본",
  rejected: "수정 요청",
  cancelled: "작성 중단",
};
const lines = (value) =>
  String(value || "")
    .split(/\r?\n/)
    .map((x) => x.trim())
    .filter(Boolean);
export function readProfileFields(values, levels) {
  const payload = {
    name: values.name.trim(),
    role: values.role.trim(),
    bio: values.bio.trim(),
    initials: values.initials.trim(),
    accent: values.accent,
    specialty: String(values.specialty || "")
      .split(/[,，]/)
      .map((x) => x.trim())
      .filter(Boolean),
    levels: [...levels],
    teachingApproach: values.teachingApproach.trim(),
    claims: lines(values.claims),
    pilot: values.pilot === true,
    acceptsInquiry: values.acceptsInquiry === true,
    samples: [],
  };
  for (let i = 0; i < 3; i++) {
    const kind = values[`sampleKind${i}`];
    if (!kind) continue;
    const title = String(values[`sampleTitle${i}`] || "").trim();
    if (kind === "lesson")
      payload.samples.push({
        kind,
        title,
        courseId: values[`sampleCourse${i}`] || "",
        lessonId: values[`sampleLesson${i}`] || "",
      });
    else if (kind === "video")
      payload.samples.push({
        kind,
        title,
        url: String(values[`sampleUrl${i}`] || "").trim(),
      });
    else throw new Error("샘플 종류를 확인해주세요.");
  }
  return payload;
}
function stable(value) {
  if (Array.isArray(value)) return value.map(stable);
  if (value && typeof value === "object")
    return Object.fromEntries(
      Object.keys(value)
        .sort()
        .map((key) => [key, stable(value[key])]),
    );
  return value;
}
export function hasProfileChanges(current, saved) {
  return JSON.stringify(stable(current)) !== JSON.stringify(stable(saved));
}
export function safeSampleHref(sample) {
  if (
    sample.kind === "lesson" &&
    /^[a-z0-9-]+$/.test(sample.courseId) &&
    /^[a-z0-9-]+$/.test(sample.lessonId)
  )
    return `#/learn/${sample.courseId}/${sample.lessonId}`;
  if (sample.kind === "video") {
    try {
      const u = new URL(sample.url);
      if (
        u.origin === "https://www.youtube.com" &&
        !u.username &&
        !u.password &&
        u.pathname === "/watch" &&
        /^[A-Za-z0-9_-]{11}$/.test(u.searchParams.get("v") || "")
      )
        return u.href;
    } catch {}
  }
  return null;
}
