import { esc, icon } from "./ui.js";
import { head, empty, date, textBody } from "./platform-ui.js";
import { safeSampleHref } from "./profile-model.js";

export function coachCard(coach) {
  return `<article class="coach-card"><a href="#/coach/${esc(coach.id)}" class="coach-card-top"><div class="coach-portrait accent-${esc(coach.accent)}"><div class="portrait-lines"></div><span class="coach-monogram">${esc(coach.initials)}</span><span class="portrait-label">TPC / COACHING TEAM</span><span class="portrait-suit">${coach.id === "live-coach" ? "♠" : coach.id === "mtt-coach" ? "♦" : "♣"}</span></div><div class="coach-card-body"><div class="coach-status"><i></i>${esc(coach.status || "파일럿 프로필")}</div><h3>${esc(coach.name)}</h3><p>${esc(coach.role)}</p><div class="tag-list">${coach.specialty.map((x) => `<span>${esc(x)}</span>`).join("")}</div>${coach.verifiedFacts?.length ? `<p class="p-profile-fact-count">${icon("check", 14)} 근거가 연결된 확인 사항 ${coach.verifiedFacts.length}건</p>` : ""}</div></a><a class="coach-card-link" href="#/coach/${esc(coach.id)}">프로필 살펴보기 ${icon("external", 18)}</a></article>`;
}
export async function coachesPage(params, ctx) {
  const { coaches } = await ctx.api("/api/catalog");
  const q = params.get("q") || "",
    topic = params.get("topic") || "",
    level = params.get("level") || "";
  const topics = [...new Set(coaches.flatMap((p) => p.specialty))];
  const filtered = coaches.filter(
    (p) =>
      (!q ||
        [p.name, p.role, p.bio, ...p.specialty]
          .join(" ")
          .toLowerCase()
          .includes(q.toLowerCase())) &&
      (!topic || p.specialty.includes(topic)) &&
      (!level || p.levels?.includes(level)),
  );
  return {
    html: `<section class="container coaches-page p-page">${head("A SHARED DIRECTION", "다른 관점이, 더 좋은 판단을 만듭니다.", "전문 분야와 교육 방식, 실제 공개 강의를 따라 나에게 맞는 코치를 찾아보세요.", '<a class="btn btn-outline" href="#/coaching/products">코칭 상품·가능 시간</a>')}<div class="coach-pilot-banner">${icon("info", 21)}<p>파일럿 예시와 실제 공개 소개를 구분합니다. 코치가 소개한 경험과 운영자가 근거로 확인한 사실은 각각 표시됩니다.</p></div><form id="coach-search" class="p-filter-bar"><label>이름·전문 분야<input name="q" value="${esc(q)}" placeholder="찾고 싶은 코치나 주제"/></label><label>분야<select name="topic"><option value="">전체 분야</option>${topics.map((t) => `<option ${t === topic ? "selected" : ""}>${esc(t)}</option>`).join("")}</select></label><label>난이도<select name="level"><option value="">전체 난이도</option>${["입문", "중급", "심화"].map((v) => `<option ${v === level ? "selected" : ""}>${v}</option>`).join("")}</select></label><button class="btn btn-outline btn-sm" type="submit">코치 찾기</button><span class="p-muted">${filtered.length}명</span></form><div class="coach-grid">${filtered.map(coachCard).join("") || empty("조건에 맞는 코치가 없습니다.", "분야나 난이도를 바꿔 다시 찾아보세요.")}</div></section>`,
    bind() {
      document.querySelector("#coach-search").onsubmit = (event) => {
        event.preventDefault();
        ctx.navigate(
          "/coaches?" +
            new URLSearchParams(
              Object.fromEntries(new FormData(event.currentTarget)),
            ),
        );
      };
    },
  };
}
export async function coachPage(id, ctx) {
  const [data, catalog] = await Promise.all([
    ctx.api(`/api/coaches/${encodeURIComponent(id)}`),
    ctx.api("/api/catalog"),
  ]);
  const p = data.coach,
    courses = catalog.courses.filter((c) => p.courseIds.includes(c.id));
  return {
    html: `<section class="container coach-profile p-page"><a class="back-link" href="#/coaches">← 코치진</a><div class="coach-profile-grid"><div class="profile-art">${coachCard(p)}<p>${p.pilot ? "전문 분야별 파일럿 예시 프로필입니다." : `공개 소개 ${p.publicVersion}판 · ${date(p.publishedAt)}`}</p></div><div class="coach-profile-content"><span class="eyebrow">MEET YOUR PERSPECTIVE</span><h1>${esc(p.name)}</h1><p class="profile-role">${esc(p.role)}</p><div class="tag-list">${p.specialty.map((s) => `<span>${esc(s)}</span>`).join("")}</div><div class="profile-bio">${textBody(p.bio)}</div><a class="btn btn-outline" href="#/coaching/products?coachId=${esc(id)}">코칭 상품·가능 시간</a><div class="profile-facts"><div><span>대상 난이도</span><strong>${p.levels.map(esc).join(" · ")}</strong></div><div><span>코칭 접수</span><strong>${p.acceptsInquiry ? "상담 문의 가능" : "새 상담 접수 중지"}</strong></div><div><span>상품</span><strong>공개 강의 ${courses.length}개</strong></div></div>${p.acceptsInquiry ? `<a class="btn btn-primary" href="#/coaching?coach=${esc(id)}">코칭 상담 문의 ${icon("arrow", 18)}</a><p class="form-note">문의 접수 단계입니다. 일정·방식·비용은 별도 협의하며 결제가 발생하지 않습니다.</p>` : `<p class="p-notice amber">현재 새 상담 문의를 받고 있지 않습니다.</p>`}</div></div><div class="p-profile-sections"><section class="p-panel"><p class="eyebrow">TEACHING APPROACH</p><h2>함께 배우는 방식</h2>${textBody(p.teachingApproach)}</section>${p.claims.length ? `<section class="p-panel"><h2>코치가 소개하는 경험</h2><p class="p-muted">아래 내용은 코치의 소개입니다. 운영자가 확인한 사항은 별도로 표시합니다.</p><ul>${p.claims.map((x) => `<li>${esc(x)}</li>`).join("")}</ul></section>` : ""}<section class="p-panel"><h2>근거로 확인한 사항</h2>${p.verifiedFacts.length ? p.verifiedFacts.map((f) => `<article class="p-profile-fact"><h3>${icon("check", 16)} ${esc(f.label)}</h3><p>${esc(f.publicNote)}</p><small>확인 ${date(f.checkedAt)}${f.expiresAt ? ` · 적용 기한 ${date(f.expiresAt)}` : ""}</small></article>`).join("") : `<p class="p-muted">현재 이 공개판에 연결된 확인 사실이 없습니다. 소개 검수와 경험·성과의 사실 확인은 구분합니다.</p>`}</section>${
      p.samples.length
        ? `<section class="p-panel"><h2>교육 스타일 살펴보기</h2><div class="p-profile-samples">${p.samples
            .map((s) => {
              const href = safeSampleHref(s);
              return href
                ? `<a class="p-profile-sample" href="${esc(href)}" ${s.kind === "video" ? 'target="_blank" rel="noopener noreferrer"' : ""}><span>${s.kind === "video" ? "YouTube 외부 샘플" : "무료 공개 차시"}</span><strong>${esc(s.title)}</strong>${icon("external", 17)}</a>`
                : "";
            })
            .join("")}</div></section>`
        : ""
    }</div><div class="section-heading"><div><p class="eyebrow">RELATED CURRICULUM</p><h2>이 코치의 공개 강의</h2></div></div><div class="course-grid">${courses.map(ctx.courseCard).join("") || empty("공개 강의를 준비하고 있습니다.", "강의가 발행되면 여기에서 커리큘럼을 확인할 수 있습니다.")}</div></section>`,
  };
}
