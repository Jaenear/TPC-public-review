/** Member routes. Rendering is kept separate from the shared router and API client. */
import { hasCourseAccess, accessNotice, grantHistory } from "./access.js";
import { isFreeCourse } from "./price-model.js";
import { learnerResourceList } from "./resources.js";
import { lessonVideo, bindLessonVideo } from "./video-player.js";
import { watchTime, lessonDurationLabel } from "./video-model.js";
import {
  lessonChecks,
  lessonCompletion,
  bindLessonLearning,
} from "./lesson-learning.js";
export async function renderMemberPage(path, params, ctx) {
  if (path.startsWith("/learn/")) return renderLesson(path, ctx);
  if (path === "/coaching") return renderCoaching(params, ctx);
  if (path === "/review/legacy") return renderReview(ctx);
  if (path === "/my") return renderMy(params, ctx);
  if (path === "/admin") return renderAdmin(params, ctx);
  if (path === "/solver") return renderSolver(ctx);
  return null;
}

const $ = (selector) => document.querySelector(selector);
const money = (value) => Number(value || 0).toLocaleString("ko-KR") + "원";
const courseHref = (id) => `#/course/${encodeURIComponent(id)}`;
const lessonHref = (courseId, lessonId) =>
  `#/learn/${encodeURIComponent(courseId)}/${encodeURIComponent(lessonId)}`;
const getCourse = (ctx, id) =>
  ctx.catalog.courses.find((item) => item.id === id);
const getCoach = (ctx, id) =>
  ctx.catalog.coaches.find((item) => item.id === id);
const textBlocks = (value, esc) =>
  esc(value || "")
    .split(/\n\s*\n/)
    .map((part) => `<p>${part.replaceAll("\n", "<br>")}</p>`)
    .join("");
const shortId = (value) =>
  String(value || "")
    .slice(-8)
    .toUpperCase();

function dateLabel(value) {
  if (!value) return "";
  const date = new Date(value);
  return Number.isNaN(date.valueOf())
    ? String(value)
    : date.toLocaleDateString("ko-KR", {
        timeZone: "Asia/Seoul",
        year: "numeric",
        month: "long",
        day: "numeric",
      });
}

function koreaDate(offset = 0) {
  const now = new Date();
  now.setUTCDate(now.getUTCDate() + offset);
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Seoul",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).formatToParts(now);
  const part = (key) => parts.find((item) => item.type === key).value;
  return `${part("year")}-${part("month")}-${part("day")}`;
}

function statusTag(status, esc) {
  const kind = ["승인", "완료", "답변 완료", "확정"].includes(status)
    ? "success"
    : status === "취소"
      ? "muted"
      : "pending";
  return `<span class="m-status m-status-${kind}">${esc(status || "접수")}</span>`;
}

function head(ctx, eyebrow, title, description) {
  return `<div class="page-head m-page-head"><p class="eyebrow">${ctx.esc(eyebrow)}</p><h1>${ctx.esc(title)}</h1><p>${ctx.esc(description)}</p></div>`;
}

function gate(ctx, title, description) {
  return {
    html: `<section class="container m-page">${head(ctx, "YOUR NEXT CHAPTER", title, description)}<div class="m-auth-gate"><div class="m-gate-mark">♧</div><h2>나만의 학습을 이어가세요</h2><p>로그인하면 학습 기록과 신청 내역을<br>한곳에서 확인할 수 있습니다.</p><button class="btn btn-primary" id="member-login">로그인하고 시작하기 <span aria-hidden="true">→</span></button><a class="btn btn-quiet" href="#/library">강의 먼저 둘러보기</a></div></section>`,
    bind() {
      $("#member-login")?.addEventListener("click", () => ctx.requireAuth());
    },
  };
}

function errorNote(form, message) {
  const node = form.querySelector("[data-form-error]");
  if (node) {
    node.textContent = message;
    node.hidden = false;
    node.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }
}

function bindForm(selector, ctx, submit) {
  const form = $(selector);
  if (!form) return;
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!form.reportValidity()) return;
    const button = form.querySelector('button[type="submit"]');
    const original = button.innerHTML;
    const error = form.querySelector("[data-form-error]");
    if (error) {
      error.hidden = true;
      error.textContent = "";
    }
    button.disabled = true;
    button.textContent = "저장하는 중…";
    try {
      await submit(Object.fromEntries(new FormData(form)), form);
    } catch (err) {
      errorNote(
        form,
        err.message || "요청을 저장하지 못했습니다. 다시 시도해 주세요.",
      );
    } finally {
      button.disabled = false;
      button.innerHTML = original;
    }
  });
}

async function renderLesson(path, ctx) {
  const { esc, api } = ctx;
  const [, , rawCourseId, rawLessonId] = path.split("/");
  const courseId = decodeURIComponent(rawCourseId || "");
  const lessonId = decodeURIComponent(rawLessonId || "");
  const course = getCourse(ctx, courseId);
  const meta = course?.lessons.find((item) => item.id === lessonId);
  if (!course || !meta)
    return {
      html: `<section class="container m-page"><div class="empty-state"><h1>레슨을 찾을 수 없습니다.</h1><a class="btn btn-primary" href="#/library">강의 라이브러리</a></div></section>`,
      bind() {},
    };
  let lesson;
  let me = { enrollments: [] };
  try {
    if (ctx.user) me = await api("/api/me");
    ({ lesson } = await api(
      `/api/courses/${encodeURIComponent(courseId)}/lessons/${encodeURIComponent(lessonId)}`,
    ));
  } catch (err) {
    return {
      html: `<section class="container m-page">${head(ctx, "TPC CLASSROOM", course.title, "활성 수강권이 있는 계정으로 학습할 수 있습니다.")}<div class="m-auth-gate"><span class="m-gate-mark">♧</span><h2>${esc(err.message)}</h2>${accessNotice(me.enrollments.find((entry) => entry.courseId === courseId))}<p>${me.enrollments.some((entry) => entry.courseId === courseId) ? "마이 캠프에서 수강 기간과 권한 상태를 확인해주세요." : "강의 상세에서 커리큘럼을 살펴보고 수강을 신청하세요."}</p><a class="btn btn-primary" href="${courseHref(courseId)}">강의 상세 보기</a>${!ctx.user ? '<button class="btn btn-outline" id="lesson-login">로그인</button>' : ""}</div></section>`,
      bind() {
        $("#lesson-login")?.addEventListener("click", () => ctx.requireAuth());
      },
    };
  }
  const enrollment = me.enrollments.find(
    (item) => item.courseId === courseId && hasCourseAccess(item),
  );
  const history = me.enrollments.find((item) => item.courseId === courseId);
  const completed = (history?.completedLessons || []).filter((id) =>
    course.lessons.some((lesson) => lesson.id === id),
  );
  const lessonIndex = course.lessons.findIndex((item) => item.id === lessonId);
  const next = course.lessons[lessonIndex + 1];
  const prev = course.lessons[lessonIndex - 1];
  const percent = course.lessons.length
    ? Math.round((completed.length / course.lessons.length) * 100)
    : 0;
  return {
    html: `<section class="container m-page m-learning-page"><a class="m-back" href="${courseHref(courseId)}">← ${esc(course.title)}</a><div class="m-learning-layout"><aside class="m-curriculum"><p class="eyebrow">YOUR LEARNING PATH</p><h2>${esc(course.title)}</h2><div class="m-progress-label"><span>나의 학습 진도</span><strong>${percent}%</strong></div><div class="m-progress"><span style="width:${percent}%"></span></div><nav aria-label="레슨 목록">${course.lessons.map((item, index) => `<a class="m-lesson-link ${item.id === lessonId ? "is-current" : ""}" href="${lessonHref(courseId, item.id)}" data-lesson-id="${esc(item.id)}" ${item.id === lessonId ? 'aria-current="page"' : ""}><span class="m-lesson-number ${completed.includes(item.id) ? "is-done" : ""}">${completed.includes(item.id) ? "✓" : String(index + 1).padStart(2, "0")}</span><span>${esc(item.title)}<small>${esc(item.id === lessonId ? lessonDurationLabel(lesson, item.duration) : item.duration)}${item.free ? " · 무료 공개" : ""}</small></span></a>`).join("")}</nav><a class="m-curriculum-back" href="#/my">내 학습으로 돌아가기 ↗</a></aside><article class="m-reader"><header class="m-reader-head"><div class="m-reader-meta"><span class="pill">${lesson.videoAssetId ? "영상·텍스트 차시" : "텍스트 차시"}</span><span>${String(lessonIndex + 1).padStart(2, "0")} / ${String(course.lessons.length).padStart(2, "0")} · ${esc(lessonDurationLabel(lesson, meta.duration))}</span></div><h1>${esc(lesson.title)}</h1><p>개념을 이해하고, 내 플레이의 언어로 정리해 보세요.</p></header>${lessonVideo(lesson, course, ctx, { canEnroll: course.pilot && isFreeCourse(course) && !history })}${accessNotice(history)}<div class="m-lesson-body">${(lesson.content || []).map((section, index) => `<section><div class="m-section-number">${String(index + 1).padStart(2, "0")}</div><h2>${esc(section.heading)}</h2>${textBlocks(section.text, esc)}</section>`).join("")}</div>${lesson.takeaway ? `<aside class="m-takeaway"><span>TAKE AWAY</span><p>${esc(lesson.takeaway)}</p></aside>` : ""}${learnerResourceList(courseId, lesson, Boolean(enrollment) || ctx.user?.role === "admin" || (ctx.user?.roles?.includes("coach") && course.instructorUserId === ctx.user.id))}${lessonChecks(lesson, course, ctx)}<section class="p-lesson-support"><div><h2>이 차시에 질문이 있나요?</h2><p>이해한 내용과 막힌 지점을 코치에게 남겨주세요.</p></div><div><a class="btn btn-primary btn-sm" href="#/qa/new?courseId=${esc(courseId)}&lessonId=${esc(lessonId)}">차시 질문하기</a><a class="btn btn-outline btn-sm" href="#/qa?courseId=${esc(courseId)}&lessonId=${esc(lessonId)}">질문·답변 보기</a></div></section><footer class="m-reader-footer"><div>${lessonCompletion(lesson, course, ctx)}</div><div class="m-lesson-pager">${prev ? `<a class="btn btn-quiet btn-sm" href="${lessonHref(courseId, prev.id)}">← 이전 레슨</a>` : ""}${next ? `<a class="btn btn-outline btn-sm" href="${lessonHref(courseId, next.id)}">다음 레슨 →</a>` : `<a class="btn btn-outline btn-sm" href="#/my">내 학습 보기 →</a>`}</div></footer></article></div></section>`,
    bind() {
      bindLessonVideo(lesson, course, ctx);
      bindLessonLearning(lesson, course, ctx);
    },
  };
}

async function renderCoaching(params, ctx) {
  if (!ctx.user)
    return gate(
      ctx,
      "혼자 풀리지 않는 질문, 코치와 함께.",
      "목표와 현재 플레이를 알려주시면 나에게 맞는 코칭을 준비할 수 있습니다.",
    );
  const { esc } = ctx;
  const fresh = await ctx.api("/api/catalog");
  ctx = {
    ...ctx,
    catalog: {
      ...fresh,
      coaches: fresh.coaches.filter((p) => p.acceptsInquiry),
    },
  };
  const requested = params.get("coach");
  const chosen = requested ? getCoach(ctx, requested) : ctx.catalog.coaches[0];
  if (!chosen)
    return {
      html: `<section class="container m-page"><div class="empty-state"><h2>${requested ? "선택한 코치는 현재 신규 접수를 받지 않습니다." : "현재 코칭 접수가 가능한 프로필이 없습니다."}</h2><p>코치의 최신 공개 소개와 접수 가능 여부를 확인해주세요.</p><a class="btn btn-outline" href="#/coaches">코치진 확인</a></div></section>`,
    };
  const contextVersion = ctx.catalog.coaches
    .map((p) => p.id + ":" + p.publicVersion)
    .join("|");
  return {
    html: `<section class="container m-page">${head(ctx, "ONE-ON-ONE COACHING", "다음 플레이를 바꾸는 대화.", "현재의 고민과 학습 목표를 남겨 주세요. 희망 일정을 확인한 뒤 코칭을 조율합니다.")}<div class="m-form-layout"><form id="coaching-form" data-session-draft data-recovery-version="${esc(contextVersion)}" class="m-form-panel"><div class="m-form-section-title"><span>01</span><h2>어떤 코칭을 원하시나요?</h2></div><div class="field"><label for="coach-select">코치 선택</label><select id="coach-select" name="coachId" required>${ctx.catalog.coaches.map((coach) => `<option value="${esc(coach.id)}" ${coach.id === chosen?.id ? "selected" : ""}>${esc(coach.name)} · ${esc(coach.role)}</option>`).join("")}</select></div><div class="field"><label for="coaching-type">코칭 방식</label><select id="coaching-type" name="type"><option>1:1 코칭</option><option>핸드 리뷰 코칭</option></select></div><div class="m-form-section-title"><span>02</span><h2>편한 시간을 알려주세요.</h2></div><div class="field-row"><div class="field"><label for="preferred-date">희망 날짜 <span class="m-required">*</span></label><input type="date" id="preferred-date" name="preferredDate" min="${koreaDate(1)}" max="${koreaDate(90)}" required></div><div class="field"><label for="preferred-time">희망 시간대 <span class="m-required">*</span></label><select id="preferred-time" name="preferredTime" required><option value="">시간대 선택</option><option>오전</option><option>오후</option><option>저녁</option></select></div></div><p class="form-note">모든 일정은 한국 시간(KST) 기준이며, 신청 시 바로 확정되지 않습니다.</p><div class="m-form-section-title"><span>03</span><h2>무엇을 함께 고민할까요?</h2></div><div class="field"><label for="coaching-goal">플레이 경험과 학습 목표 <span class="m-required">*</span></label><textarea id="coaching-goal" name="goal" rows="6" minlength="10" maxlength="2000" placeholder="예: 라이브 캐시를 주 2회 플레이합니다. BTN에서 오픈한 뒤 멀티웨이 팟에서 이어지는 판단을 개선하고 싶어요." required></textarea><small>주로 플레이하는 게임, 어려운 상황, 코칭에서 얻고 싶은 내용을 알려주세요. (10~2,000자)</small></div><label class="m-consent"><input type="checkbox" required><span>신청 내용이 코칭 일정 조율에 사용되며, 실제 코칭과 비용은 별도 협의 후 확정되는 점을 확인했습니다.</span></label><p class="m-form-error" data-form-error role="alert" hidden></p><button class="btn btn-primary m-submit" type="submit">코칭 신청하기 <span aria-hidden="true">↗</span></button></form><aside class="m-form-aside"><div id="selected-coach">${coachSummary(chosen, ctx)}</div><div class="m-info-card"><p class="eyebrow">WHAT HAPPENS NEXT</p><ol class="m-step-list"><li><span>1</span><div><strong>신청 접수</strong><p>학습 목표와 희망 일정을 저장합니다.</p></div></li><li><span>2</span><div><strong>내용 확인 · 일정 조율</strong><p>운영자가 코치와 함께 진행 가능 여부를 확인합니다.</p></div></li><li><span>3</span><div><strong>코칭 확정</strong><p>방식, 일정, 비용을 협의한 뒤 확정합니다.</p></div></li></ol></div><p class="m-aside-note">선택한 코치의 공개 소개와 접수 범위를 확인해주세요.<br>신청 단계에서는 결제가 발생하지 않습니다.</p></aside></div></section>`,
    bind() {
      $("#coach-select")?.addEventListener("change", (event) => {
        $("#selected-coach").innerHTML = coachSummary(
          getCoach(ctx, event.target.value),
          ctx,
        );
      });
      bindForm("#coaching-form", ctx, async (data) => {
        await ctx.api("/api/bookings", {
          method: "POST",
          body: {
            ...data,
            coachVersion: getCoach(ctx, data.coachId)?.publicVersion,
          },
        });
        ctx.toast(
          "코칭 신청이 접수되었습니다. 내 신청에서 진행 상황을 확인하세요.",
        );
        ctx.navigate("/my?tab=bookings");
      });
    },
  };
}

function coachSummary(coach, ctx) {
  if (!coach) return "";
  const { esc } = ctx;
  return `<div class="m-coach-summary"><div class="m-coach-monogram">${esc(coach.initials || "TPC")}</div><span class="pill">${esc(coach.status || "파일럿 프로필")}</span><h2>${esc(coach.name)}</h2><p>${esc(coach.role)}</p><div class="m-specialties">${(coach.specialty || []).map((item) => `<span>${esc(item)}</span>`).join("")}</div><p class="m-coach-bio">${esc(coach.bio)}</p><div class="m-coach-price"><span>진행 방식 · 비용</span><strong>신청 후 개별 협의</strong></div></div>`;
}

function renderReview(ctx) {
  if (!ctx.user)
    return gate(
      ctx,
      "한 핸드에서, 다음 선택의 기준을.",
      "상황과 판단의 이유를 기록하세요. 코치가 검토할 수 있는 구조화된 핸드 노트를 만듭니다.",
    );
  return {
    html: `<section class="container m-page">${head(ctx, "HAND REVIEW", "기억에 남는 핸드가 있나요?", "결과보다 중요한 건 판단의 과정입니다. 당시의 상황과 고민을 차근차근 기록해 주세요.")}<div class="m-form-layout"><form id="review-form" data-session-draft data-recovery-version="new" class="m-form-panel"><div class="m-form-section-title"><span>01</span><h2>테이블 상황</h2></div><div class="field"><label for="review-title">핸드 제목 <span class="m-required">*</span></label><input id="review-title" name="title" minlength="2" maxlength="100" placeholder="예: BTN 오픈 후 턴에서 만난 체크레이즈" required></div><div class="field-row"><div class="field"><label for="game-type">게임 유형</label><select id="game-type" name="gameType"><option>라이브 캐시</option><option>토너먼트</option><option>온라인 캐시</option></select></div><div class="field"><label for="review-position">내 포지션</label><select id="review-position" name="position"><option>UTG</option><option>HJ</option><option>CO</option><option selected>BTN</option><option>SB</option><option>BB</option></select></div></div><div class="field"><label for="review-stack">유효 스택 (BB) <span class="m-required">*</span></label><div class="m-input-unit"><input id="review-stack" type="number" name="stack" min="0.1" max="20000" step="any" placeholder="100" required><span>BB</span></div><small>나와 상대가 이번 핸드에서 실제로 사용할 수 있는 스택 중 더 작은 값입니다.</small></div><div class="m-form-section-title"><span>02</span><h2>카드와 액션</h2></div><div class="field-row"><div class="field"><label for="hero-hand">내 홀카드 <span class="m-required">*</span></label><input id="hero-hand" name="heroHand" placeholder="As Kh" maxlength="12" autocomplete="off" required></div><div class="field"><label for="review-board">보드 (선택)</label><input id="review-board" name="board" placeholder="Qs Jh 2d" maxlength="30" autocomplete="off"></div></div><div class="m-card-notation"><span>♠ s</span><span class="m-red">♥ h</span><span class="m-red">♦ d</span><span>♣ c</span><small>A · K · Q · J · T · 9…2 &nbsp; / &nbsp; 예: As = A♠</small></div><div class="field"><label for="review-actions">스트리트별 액션 <span class="m-required">*</span></label><textarea id="review-actions" name="actions" rows="7" minlength="10" maxlength="10000" placeholder="프리플랍: BTN에서 2.5BB 오픈, BB 콜.&#10;플랍 (5.5BB): BB 체크, 히어로 2BB 베팅, BB 콜.&#10;턴: BB 체크, 히어로 6BB 베팅, BB 20BB 레이즈.&#10;리버: …" required></textarea><small>팟 크기, 베팅 사이즈, 상대의 액션을 가능한 한 구체적으로 적어주세요.</small></div><div class="m-form-section-title"><span>03</span><h2>내 판단과 질문</h2></div><div class="field"><label for="review-question">가장 궁금한 지점 <span class="m-required">*</span></label><textarea id="review-question" name="question" rows="4" minlength="5" maxlength="3000" placeholder="어떤 선택 사이에서 고민했나요? 상대를 어떻게 생각했고, 어떤 이유로 액션을 결정했나요?" required></textarea></div><label class="m-consent"><input type="checkbox" required><span>제출한 핸드가 코치 검토를 위해 저장되는 데 동의합니다. 다른 사람의 이름이나 연락처는 포함하지 않았습니다.</span></label><p class="m-form-error" data-form-error role="alert" hidden></p><button class="btn btn-primary m-submit" type="submit">핸드 리뷰 요청하기 <span aria-hidden="true">↗</span></button></form><aside class="m-form-aside"><div class="m-review-visual" aria-hidden="true"><span class="eyebrow">EVERY HAND TELLS A STORY</span><div class="m-playing-cards"><span>A<small>♠</small></span><span>K<small>♥</small></span></div><p>좋은 질문이<br>좋은 플레이를 만듭니다.</p></div><div class="m-info-card"><h3>이렇게 기록하면 좋아요.</h3><ul class="m-tip-list"><li>결과보다 의사결정에 집중하세요.</li><li>각 스트리트의 팟과 사이즈를 남겨주세요.</li><li>상대 성향이나 테이블 맥락을 더해주세요.</li><li>고민한 선택과 이유를 함께 적어주세요.</li></ul></div><p class="m-aside-note">접수 후 내 핸드 리뷰에서 상태와 답변을 확인할 수 있습니다. 코치가 작성한 답변만 표시하며, 자동 분석은 제공하지 않습니다.</p></aside></div></section>`,
    bind() {
      bindForm("#review-form", ctx, async (data) => {
        const parseCards = (value) => {
          const clean = value.trim();
          if (!clean) return [];
          return clean
            .split(/[\s,]+/)
            .map((card) => card.replace(/^10/, "T").toUpperCase());
        };
        const hero = parseCards(data.heroHand);
        const board = parseCards(data.board);
        if (
          hero.length !== 2 ||
          ![0, 3, 4, 5].includes(board.length) ||
          [...hero, ...board].some((card) => !/^[2-9TJQKA][SHDC]$/.test(card))
        )
          throw new Error(
            "홀카드 2장과 보드 0·3·4·5장을 카드 표기법에 맞게 입력해 주세요. 예: As Kh / Qs Jh 2d",
          );
        if (new Set([...hero, ...board]).size !== hero.length + board.length)
          throw new Error(
            "같은 카드를 두 번 사용할 수 없습니다. 홀카드와 보드를 확인해 주세요.",
          );
        data.heroHand = hero
          .map((card) => card[0] + card[1].toLowerCase())
          .join(" ");
        data.board = board
          .map((card) => card[0] + card[1].toLowerCase())
          .join(" ");
        data.stack = Number(data.stack);
        await ctx.api("/api/reviews", { method: "POST", body: data });
        ctx.toast("핸드 리뷰가 접수되었습니다.");
        ctx.navigate("/my?tab=reviews");
      });
    },
  };
}

async function renderMy(params, ctx) {
  if (!ctx.user)
    return gate(
      ctx,
      "나의 플레이가 쌓이는 곳.",
      "학습 기록부터 코칭 신청, 핸드 리뷰까지 나의 성장 과정을 확인하세요.",
    );
  const me = await ctx.api("/api/me");
  const { esc } = ctx;
  const tab = ["learning", "bookings", "reviews", "orders"].includes(
    params.get("tab"),
  )
    ? params.get("tab")
    : "learning";
  const enrollments = me.enrollments || [];
  const activeCount = enrollments.filter(hasCourseAccess).length;
  const bookings = me.bookings || [];
  const reviews = me.reviews || [];
  const orders = me.orders || [];
  const completedCount = enrollments.reduce(
    (total, entry) => total + (entry.completedLessons || []).length,
    0,
  );
  const pendingCount = [...bookings, ...reviews].filter((item) =>
    ["접수", "검토 중", "보완 요청"].includes(item.status),
  ).length;
  let content;
  if (tab === "learning") {
    content = enrollments.length
      ? `<div class="m-enrollment-grid">${enrollments.map((entry) => enrollmentCard(entry, ctx)).join("")}</div>`
      : emptyState(
          ctx,
          "아직 시작한 강의가 없어요.",
          "무료 레슨으로 첫 학습을 시작해 보세요.",
          "강의 둘러보기",
          "#/library",
        );
  } else if (tab === "bookings") {
    content = bookings.length
      ? `<div class="m-request-list">${bookings.map((item) => bookingCard(item, ctx)).join("")}</div>`
      : emptyState(
          ctx,
          "코칭 신청 내역이 없어요.",
          "내 플레이의 고민을 코치와 함께 풀어보세요.",
          "코칭 알아보기",
          "#/coaches",
        );
  } else if (tab === "reviews") {
    content = reviews.length
      ? `<div class="m-request-list">${reviews.map((item) => reviewCard(item, ctx)).join("")}</div>`
      : emptyState(
          ctx,
          "첫 핸드를 기록해 보세요.",
          "기억에 남는 한 핸드가 다음 학습의 시작이 됩니다.",
          "핸드 리뷰 요청",
          "#/review",
        );
  } else {
    content = orders.length
      ? `<div class="m-request-list">${orders.map((item) => orderCard(item, ctx)).join("")}</div><p class="m-list-note">파일럿 수강 신청입니다. 신청 단계에서 결제가 발생하지 않으며, 운영자가 확인 후 수강을 승인합니다.</p>`
      : emptyState(
          ctx,
          "수강 신청 내역이 없어요.",
          "관심 있는 심화 강의의 커리큘럼을 살펴보세요.",
          "강의 둘러보기",
          "#/library",
        );
  }
  const tabs = [
    ["learning", "내 학습", enrollments.length],
    ["bookings", "코칭 신청", bookings.length],
    ["reviews", "핸드 리뷰", reviews.length],
    ["orders", "수강 신청", orders.length],
  ];
  return {
    html: `<section class="container m-page">${head(ctx, "MY CAMP", `${ctx.user.name}님의 다음 한 수.`, "꾸준히 쌓인 작은 이해가, 테이블 위의 자신감이 됩니다.")}<div class="m-my-stats"><div><span>수강 가능한 강의</span><strong>${activeCount}<small>개</small></strong></div><div><span>완료한 레슨</span><strong>${completedCount}<small>개</small></strong></div><div><span>확인 중인 신청</span><strong>${pendingCount}<small>건</small></strong></div><a href="#/review"><span>오늘의 플레이를 남겨볼까요?</span><strong>핸드 리뷰 요청 <span>↗</span></strong></a></div><div class="my-study-shortcut"><div><strong>나의 스터디 기록</strong><p>레인지 노트와 트레이닝 결과를 한곳에서 이어보세요.</p></div><a class="btn btn-outline btn-sm" href="#/notes">스터디 기록 보기 ↗</a></div><div class="p-my-links"><a href="#/account">계정 설정 ${ctx.icon("user", 16)}</a><a href="#/reviews">내 핸드 리뷰 ${ctx.icon("book", 16)}</a><a href="#/reservations">내 코칭 일정 ${ctx.icon("calendar", 16)}</a><a href="#/qa">내 강의 질문 ${ctx.icon("message", 16)}</a><a href="#/purchases">주문·취소 문의 ${ctx.icon("book", 16)}</a><a href="#/inbox">알림 ${ctx.icon("bell", 16)}</a><a href="#/community/moderation/actions">내 조치·이의 ${ctx.icon("info", 16)}</a></div><nav class="m-tabs" aria-label="마이페이지 메뉴">${tabs.map(([key, label, count]) => `<a href="#/my?tab=${key}" class="${tab === key ? "is-active" : ""}" ${tab === key ? 'aria-current="page"' : ""}>${label}<span>${count}</span></a>`).join("")}</nav>${content}</section>`,
    bind() {
      bindBookingProfiles(ctx);
      document.querySelectorAll("[data-cancel-kind]").forEach((button) =>
        button.addEventListener("click", async () => {
          const { cancelKind, cancelId } = button.dataset;
          ctx.modal(
            `<div class="m-confirm"><p class="eyebrow">REQUEST CANCELLATION</p><h2>이 신청을 취소할까요?</h2><p>취소한 신청은 다시 접수할 수 있습니다.<br>기존 기록은 내 신청에 남습니다.</p><div class="m-confirm-actions"><button class="btn btn-outline" data-modal-close>돌아가기</button><button class="btn btn-primary" id="confirm-cancel">신청 취소</button></div><p class="m-form-error" id="cancel-error" role="alert" hidden></p></div>`,
            (dialog) => {
              dialog
                .querySelector("[data-modal-close]")
                .addEventListener("click", () => dialog.close());
              $("#confirm-cancel")?.addEventListener("click", async (event) => {
                event.target.disabled = true;
                try {
                  await ctx.api(
                    `/api/${cancelKind}/${encodeURIComponent(cancelId)}`,
                    { method: "PATCH", body: { status: "취소" } },
                  );
                  dialog.close();
                  ctx.toast("신청을 취소했습니다.");
                  ctx.reload();
                } catch (err) {
                  const note = $("#cancel-error");
                  note.hidden = false;
                  note.textContent = err.message;
                  event.target.disabled = false;
                }
              });
            },
          );
        }),
      );
    },
  };
}

function emptyState(ctx, title, description, label, href) {
  return `<div class="empty-state m-empty"><div class="m-empty-mark" aria-hidden="true">♧</div><h2>${ctx.esc(title)}</h2><p>${ctx.esc(description)}</p><a class="btn btn-primary" href="${href}">${ctx.esc(label)} →</a></div>`;
}

function videoResumeCard(entry, course, ctx) {
  const { esc } = ctx;
  const items = (entry.videoProgress || []).filter((item) =>
    course.lessons.some((lesson) => lesson.id === item.lessonId),
  );
  if (!items.length) return "";
  const latest = items[0];
  const ratio = (value) =>
    value === null || value === undefined ? "비율 미확인" : value + "%";
  return `<div class="m-video-resume"><small>나의 영상 이어보기</small><strong>${esc(latest.lessonTitle)}</strong><p>마지막 ${watchTime(latest.seconds)} / ${watchTime(latest.durationSeconds)} · 위치 ${esc(ratio(latest.positionPercent))} · 최대 도달 ${esc(ratio(latest.furthestPercent))}</p><a href="${hasCourseAccess(entry) ? lessonHref(course.id, latest.lessonId) : `#/start/${esc(course.id)}`}">${hasCourseAccess(entry) ? "이 영상 이어보기" : "현재 수강권 확인"} →</a>${
    items.length > 1
      ? `<details><summary>다른 영상 기록 ${items.length - 1}개</summary>${items
          .slice(1)
          .map(
            (item) =>
              `<p><a href="${hasCourseAccess(entry) ? lessonHref(course.id, item.lessonId) : `#/start/${esc(course.id)}`} ">${esc(item.lessonTitle)} · ${watchTime(item.seconds)} · ${esc(ratio(item.positionPercent))} ↗</a></p>`,
          )
          .join("")}</details>`
      : ""
  }</div>`;
}

function enrollmentCard(entry, ctx) {
  const course = getCourse(ctx, entry.courseId);
  if (!course)
    return `<article class="p-panel"><h2>이전 수강 기록</h2><p>${ctx.esc(entry.courseId)}</p><p class="p-muted">현재 공개되지 않은 강의입니다. 진도 기록은 보존됩니다.</p></article>`;
  const { esc } = ctx;
  const completed = (entry.completedLessons || []).filter((id) =>
    course.lessons.some((lesson) => lesson.id === id),
  );
  const percent = course.lessons.length
    ? Math.round((completed.length / course.lessons.length) * 100)
    : 0;
  const next =
    course.lessons.find((item) => !completed.includes(item.id)) ||
    course.lessons[0];
  return `<article class="m-enrollment">${ctx.courseArt(course, true)}<div class="m-enrollment-content"><div class="m-enrollment-tags"><span class="pill">${esc(course.category)}</span><span>${esc(course.level)}</span></div><h2><a href="${courseHref(course.id)}">${esc(course.title)}</a></h2><p>${esc(course.subtitle || course.description)}</p>${accessNotice(entry)}${grantHistory(entry.access)}${videoResumeCard(entry, course, ctx)}<div class="m-progress-label"><span>${completed.length} / ${course.lessons.length} 레슨 완료</span><strong>${percent}%</strong></div><div class="m-progress"><span style="width:${percent}%"></span></div><a class="btn ${percent === 100 ? "btn-outline" : "btn-primary"} btn-sm" href="${!hasCourseAccess(entry) || !next ? `#/start/${esc(course.id)}` : lessonHref(course.id, next.id)}">${!next ? "공개 차시 준비 확인" : !hasCourseAccess(entry) ? "수강권·강의 확인" : percent === 100 ? "다시 복습하기" : percent > 0 ? "이어서 학습하기" : "첫 레슨 시작하기"} →</a><a class="text-link" href="#/start/${esc(course.id)}">수강 시작 안내 ↗</a></div></article>`;
}

function requestHeader(item, title, category, ctx) {
  return `<div class="m-request-heading"><span class="m-request-category">${ctx.esc(category)}</span><h2>${ctx.esc(title)}</h2><span class="m-request-date">${ctx.esc(dateLabel(item.createdAt))}</span>${statusTag(item.status, ctx.esc)}<span class="m-details-toggle" aria-hidden="true">+</span></div>`;
}

function replyBlock(reply, ctx) {
  return reply
    ? `<div class="m-actual-reply"><p class="eyebrow">TPC RESPONSE</p><h3>운영자 답변</h3>${textBlocks(reply, ctx.esc)}</div>`
    : '<div class="m-waiting-note">신청 내용이 저장되었습니다. 운영자가 확인하면 이곳에서 답변을 볼 수 있습니다.</div>';
}

function bindBookingProfiles(ctx) {
  document.querySelectorAll("[data-booking-profile]").forEach((button) => {
    button.addEventListener("click", async () => {
      button.disabled = true;
      try {
        const { profileSnapshot: snapshot } = await ctx.api(
          `/api/bookings/${encodeURIComponent(button.dataset.bookingProfile)}/profile`,
        );
        if (!button.isConnected) return;
        const { esc } = ctx;
        const profile = snapshot.payload;
        const content = snapshot.recorded
          ? `<p class="eyebrow">접수 당시 소개 · ${esc(snapshot.publicVersion)}판</p><h2>${esc(profile.name)}</h2><p>${esc(profile.role)}</p><p class="form-note">${esc(dateLabel(snapshot.recordedAt))} 접수 기록입니다. 현재 공개 소개나 담당 코치가 달라져도 이 원문은 보존됩니다.</p><h3>자기소개</h3>${textBlocks(profile.bio, esc)}<h3>교육 방식</h3>${textBlocks(profile.teachingApproach, esc)}${profile.claims?.length ? `<h3>당시 자기소개 항목</h3><ul>${profile.claims.map((claim) => `<li>${esc(claim)}</li>`).join("")}</ul>` : ""}${profile.samples?.length ? `<h3>당시 등록된 샘플</h3><ul>${profile.samples.map((sample) => `<li>${esc(sample.title)}</li>`).join("")}</ul><p class="form-note">현재 공개 여부는 코치 소개에서 다시 확인해주세요.</p>` : ""}<p class="p-notice">당시의 확인 사실 표시 이력은 수집되지 않았습니다. 현재의 확인 사실을 과거 신청에 적용하지 않습니다.</p>`
          : `<h2>당시 소개가 기록되지 않은 신청입니다.</h2><p>기존 신청 내용은 보존됩니다. 현재 소개로 과거 내용을 추정하지 않습니다.</p>`;
        ctx.modal(
          `<div class="m-confirm">${content}<button class="btn btn-outline" data-modal-close>닫기</button></div>`,
          (dialog) => {
            dialog
              .querySelector("[data-modal-close]")
              .addEventListener("click", () => dialog.close());
          },
        );
      } catch (error) {
        ctx.toast(error.message, "error");
      } finally {
        button.disabled = false;
      }
    });
  });
}

function bookingCard(item, ctx) {
  const coach = getCoach(ctx, item.coachId);
  const { esc } = ctx;
  return `<details class="m-request-card"><summary>${requestHeader(item, item.assignedCoachName || coach?.name || "코칭 신청", item.type || "1:1 코칭", ctx)}</summary><div class="m-request-content"><dl class="m-detail-grid"><div><dt>희망 일정</dt><dd>${esc(item.preferredDate)} · ${esc(item.preferredTime)}</dd></div><div><dt>신청 번호</dt><dd>${esc(shortId(item.id))}</dd></div></dl><h3>학습 목표</h3><div class="m-user-text">${textBlocks(item.goal, esc)}</div>${item.profileSnapshotRecorded ? `<p class="form-note">접수 당시 ${esc(item.originalCoachName)} · 소개 ${item.originalProfileVersion}판</p>` : `<p class="form-note">이전 신청의 소개 버전은 미수집 상태입니다.</p>`}${item.needsHandoff ? `<div class="p-notice amber">담당 코치 변경을 확인 중입니다. 기존 신청과 내용은 보존됩니다.</div>` : ""}${replyBlock(item.reply, ctx)}<div class="m-request-actions"><p>희망 일정은 운영자 확인 후 확정됩니다.</p><button class="btn btn-outline btn-sm" data-booking-profile="${esc(item.id)}">접수 당시 소개 보기</button>${item.status === "접수" ? `<button class="btn btn-quiet btn-sm" data-cancel-kind="bookings" data-cancel-id="${esc(item.id)}">신청 취소</button>` : ""}</div></div></details>`;
}

function reviewDetails(item, ctx) {
  const { esc } = ctx;
  return `<dl class="m-detail-grid"><div><dt>게임 · 포지션</dt><dd>${esc(item.gameType)} · ${esc(item.position)}</dd></div><div><dt>유효 스택</dt><dd>${esc(item.stack)} BB</dd></div><div><dt>홀카드</dt><dd class="m-card-text">${esc(item.heroHand)}</dd></div><div><dt>보드</dt><dd class="m-card-text">${esc(item.board || "프리플랍")}</dd></div></dl><h3>스트리트별 액션</h3><div class="m-user-text">${textBlocks(item.actions, esc)}</div><h3>가장 궁금한 지점</h3><div class="m-user-text">${textBlocks(item.question, esc)}</div>`;
}

function reviewCard(item, ctx) {
  const managed = Boolean(item.reviewContext?.managed);
  const information = item.reviewContext?.informationRequest;
  const response = information ? `<div class="p-notice amber"><strong>추가 확인이 필요합니다.</strong><div class="m-user-text">${textBlocks(information.reason, ctx.esc)}</div></div>` : replyBlock(item.reply, ctx);
  return `<details class="m-request-card"><summary>${requestHeader(item, item.title, "핸드 리뷰", ctx)}</summary><div class="m-request-content">${reviewDetails(item, ctx)}${response}<div class="m-request-actions"><p>접수 번호 ${ctx.esc(shortId(item.id))}</p><a class="btn btn-outline btn-sm" href="#/reviews/${ctx.esc(item.id)}">원문·보완 보기 ↗</a>${item.status === "접수" && !managed ? `<button class="btn btn-quiet btn-sm" data-cancel-kind="reviews" data-cancel-id="${ctx.esc(item.id)}">신청 취소</button>` : ""}</div></div></details>`;
}

function orderCard(item, ctx) {
  const course = getCourse(ctx, item.courseId);
  return `<article class="m-order-card"><div><p class="eyebrow">COURSE REQUEST · ${ctx.esc(shortId(item.id))}</p><h2><a href="${courseHref(item.courseId)}">${ctx.esc(course?.title || item.courseId)}</a></h2><p>${ctx.esc(dateLabel(item.createdAt))} 신청 · 파일럿 예시 가격 ${money(item.amount ?? item.price ?? course?.price)}</p></div><div class="m-order-state">${statusTag(item.status, ctx.esc)}${item.status === "승인" ? '<a class="btn btn-outline btn-sm" href="#/my?tab=learning">내 학습 보기 →</a>' : ""}</div></article>`;
}

async function renderAdmin(params, ctx) {
  if (!ctx.user)
    return gate(
      ctx,
      "TPC 운영 공간",
      "신청과 학습이 자연스럽게 이어질 수 있도록 확인합니다.",
    );
  if (ctx.user.role !== "admin")
    return {
      html: `<section class="container m-page"><div class="empty-state"><h1>운영자 계정으로 접근할 수 있습니다.</h1><a class="btn btn-primary" href="#/my">내 학습으로 이동</a></div></section>`,
      bind() {},
    };
  const data = await ctx.api("/api/admin");
  const { esc } = ctx;
  const tab = ["orders", "bookings", "reviews"].includes(params.get("tab"))
    ? params.get("tab")
    : "reviews";
  const tabs = [
    ["reviews", "핸드 리뷰"],
    ["bookings", "코칭 신청"],
    ["orders", "수강 신청"],
  ];
  const queue = [...(data[tab] || [])].sort((a, b) =>
    String(b.createdAt).localeCompare(String(a.createdAt)),
  );
  const pending = (items) =>
    (items || []).filter((item) => ["접수", "검토 중"].includes(item.status))
      .length;
  return {
    html: `<section class="container m-page">${head(ctx, "TPC OPERATIONS", "좋은 학습 경험의 시작.", "접수된 신청을 확인하고 진행 상태와 답변을 업데이트하세요.")}<div class="m-admin-stats"><div><span>검토할 핸드 리뷰</span><strong>${pending(data.reviews)}</strong></div><div><span>조율할 코칭 신청</span><strong>${pending(data.bookings)}</strong></div><div><span>대기 중인 수강 신청</span><strong>${pending(data.orders)}</strong></div></div><nav class="m-tabs" aria-label="운영 메뉴">${tabs.map(([key, label]) => `<a href="#/admin?tab=${key}" class="${tab === key ? "is-active" : ""}">${label}<span>${(data[key] || []).length}</span></a>`).join("")}</nav>${queue.length ? `<div class="m-request-list">${queue.map((item) => adminCard(item, tab, ctx)).join("")}</div>` : `<div class="empty-state m-empty"><h2>접수된 신청이 없습니다.</h2><p>새 신청이 들어오면 이곳에서 확인할 수 있습니다.</p></div>`}</section>`,
    bind() {
      document.querySelectorAll("[data-admin-form]").forEach((form) => {
        form.addEventListener("submit", async (event) => {
          event.preventDefault();
          const values = Object.fromEntries(new FormData(form));
          if (
            tab === "bookings" &&
            values.status === "확정" &&
            !values.reply.trim()
          ) {
            errorNote(
              form,
              "예약 확정 전에 실제 합의된 일정을 안내에 작성해 주세요.",
            );
            return;
          }
          if (
            tab === "reviews" &&
            values.status === "답변 완료" &&
            !values.reply.trim()
          ) {
            errorNote(
              form,
              "답변 완료로 변경하려면 실제 검토 답변을 작성해 주세요.",
            );
            return;
          }
          const button = form.querySelector('button[type="submit"]');
          button.disabled = true;
          try {
            await ctx.api(
              `/api/admin/${tab}/${encodeURIComponent(form.dataset.adminForm)}`,
              { method: "PATCH", body: values },
            );
            ctx.toast(
              tab === "orders" && values.status === "승인"
                ? "수강을 승인하고 학습 권한을 열었습니다."
                : "진행 상태를 저장했습니다.",
            );
            ctx.reload();
          } catch (err) {
            errorNote(form, err.message);
            button.disabled = false;
          }
        });
      });
    },
  };
}

function adminCard(item, type, ctx) {
  const { esc } = ctx;
  const person = item.user || {};
  const userName = item.userName || person.name || "수강생";
  const userEmail = item.userEmail || person.email || "";
  const course = getCourse(ctx, item.courseId);
  const coach = getCoach(ctx, item.assignedCoachId || item.coachId);
  const title =
    type === "reviews"
      ? item.title
      : type === "orders"
        ? course?.title || item.courseId
        : `${item.assignedCoachName || coach?.name || "코칭"} · ${item.type}`;
  const statuses =
    type === "orders"
      ? ["승인", "취소"]
      : type === "reviews"
        ? ["접수", "검토 중", "답변 완료", "취소"]
        : ["접수", "확정", "완료", "취소"];
  const id = `admin-${type}-${item.id}`;
  let content;
  if (type === "reviews") content = reviewDetails(item, ctx);
  else if (type === "bookings")
    content = `<dl class="m-detail-grid"><div><dt>희망 일정</dt><dd>${esc(item.preferredDate)} · ${esc(item.preferredTime)}</dd></div><div><dt>코칭 방식</dt><dd>${esc(item.type)}</dd></div></dl><h3>학습 목표</h3><div class="m-user-text">${textBlocks(item.goal, esc)}</div>`;
  else
    content = `<div class="m-user-text"><p>파일럿 예시 가격 ${money(item.amount ?? item.price ?? course?.price)} · 수강 신청을 승인하면 신청자에게 이 강의의 학습 권한이 부여됩니다.</p><p>결제 처리가 연결되어 있지 않습니다. 실제 비용 및 제공 조건은 신청자와 별도로 확인하세요.</p></div>`;
  const updatePanel =
    type === "reviews" && item.reviewContext?.managed
      ? `<div class="p-notice"><p>현재 제출판과 담당 권한으로 보완·답변을 이어갑니다.</p><a class="btn btn-outline btn-sm" href="#/studio/hand-reviews/${esc(item.id)}">핸드 리뷰 작업함 열기 ↗</a></div>`
      : type === "orders" && item.status !== "접수"
      ? `<div class="m-waiting-note">${item.status === "승인" ? "수강 승인이 완료되었습니다. 신청자의 내 학습에 이 강의가 표시됩니다." : "취소 처리된 수강 신청입니다."}</div>`
      : `<form class="m-admin-update" data-admin-form="${esc(item.id)}"><div class="field"><label for="${esc(id)}-status">진행 상태</label><select id="${esc(id)}-status" name="status" required>${type === "orders" ? '<option value="" disabled selected>처리 방식을 선택하세요</option>' : ""}${statuses.map((status) => `<option ${item.status === status ? "selected" : ""}>${status}</option>`).join("")}</select></div>${type !== "orders" ? `<div class="field"><label for="${esc(id)}-reply">${type === "reviews" ? "핸드 리뷰 답변" : "신청자에게 보낼 안내"}</label><textarea id="${esc(id)}-reply" name="reply" rows="6" maxlength="10000" placeholder="입력한 내용은 신청자의 마이페이지에 표시됩니다.">${esc(item.reply || "")}</textarea></div>` : ""}<p class="m-form-error" data-form-error role="alert" hidden></p><button class="btn btn-primary btn-sm" type="submit">변경사항 저장</button></form>`;
  return `<details class="m-request-card m-admin-card"><summary>${requestHeader(item, title, userName, ctx)}</summary><div class="m-request-content"><div class="m-admin-person"><span>${esc(userName)}</span><a href="mailto:${esc(userEmail)}">${esc(userEmail)}</a><small>접수 번호 ${esc(shortId(item.id))}</small></div>${content}${type === "bookings" ? `<div class="p-notice ${item.needsHandoff ? "amber" : ""}"><p>현재 담당: ${esc(item.assignedCoachName || coach?.name || item.coachId)}</p>${item.needsHandoff ? "<p>담당 코치가 비활성 상태입니다. 접수를 인계한 후 진행해주세요.</p>" : ""}<a class="text-link" href="#/studio/profiles/${esc(item.assignedCoachId || item.coachId)}">담당 소개·접수 인계 관리 →</a></div>` : ""}${updatePanel}</div></details>`;
}

async function renderSolver(ctx) {
  const status = await ctx.api("/api/solver/status");
  return {
    html: `<section class="container m-page m-solver-page"><div class="m-solver-grid" aria-hidden="true">${Array.from({ length: 81 }, (_, i) => `<span class="${i % 7 === 0 ? "is-lit" : i % 5 === 0 ? "is-soft" : ""}"></span>`).join("")}</div><div class="m-solver-copy"><span class="pill m-status-pending">${status.available ? "상태 확인" : "COMING LATER"}</span><p class="eyebrow">TPC ANALYSIS LAB</p><h1>더 깊은 이해를 위한<br>다음 도구.</h1><p>핸드 기록부터 분석까지 이어지는 학습 경험을 준비하고 있습니다. 지금은 코치와 함께 판단의 이유를 정리해 보세요.</p><div class="m-solver-status"><span></span>${ctx.esc(status.message || "솔버 연결 준비 중")}</div><div class="m-solver-actions"><a class="btn btn-primary" href="#/review">핸드 리뷰 요청하기 ↗</a><a class="btn btn-outline" href="#/library">기초부터 학습하기</a></div><small>현재 솔버 분석 결과는 제공하지 않습니다.</small></div></section>`,
    bind() {},
  };
}
