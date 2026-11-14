import { isFreeCourse, priceLabel } from "./price-model.js";
import { coachCard, coachesPage, coachPage } from "./coaches.js";
import {
  api,
  esc,
  icon,
  money,
  toast,
  modal,
  courseArt,
  observeApiSession,
} from "./ui.js";
import {
  SessionDrafts,
  shouldRecoverSession,
  captureDraftForms,
  restoreDraftForms,
  draftText,
  recoveryCopy,
} from "./session-recovery.js";
import { renderMemberPage } from "./member.js";
import { renderPlatformPage } from "./platform.js";
import { renderHome } from "./home.js";
import { renderStudyPage, clearWorkspaceDrafts } from "./study.js";
import { renderHeader, renderFooter } from "./layout.js";
import { renderIdentity } from "./identity.js";
import { IdentityLinkMemory, sameIdentityContext } from "./identity-state.js";

const identityLinks = new IdentityLinkMemory();
const captureIdentityRoute = () => identityLinks.accept(location.hash, (hash) => history.replaceState(null, "", hash));
captureIdentityRoute();

const state = { user: null, catalog: { courses: [], coaches: [] } };
let renderVersion = 0;
let afterLogin = null;
let sessionEpoch = 0;
let renderedRoute = null;
let recoveryAnchor = null;
let manualDraftReceipts = null;
let authFlow = 0;
let activePageCleanup = null;
const sessionDrafts = new SessionDrafts();
const app = document.querySelector("#app");
const href = (path) => `#${path}`;
export function navigate(path) {
  if (location.hash === href(path)) render();
  else location.hash = path;
}
async function refreshCatalog() {
  state.catalog = await api("/api/catalog");
  return state.catalog;
}
async function refreshSession() {
  const result = await api("/api/session");
  if (state.user && !result.user) {
    handleExpiredSession({
      status: 401,
      path: "/api/session",
      context: sessionIdentity(),
    });
    return null;
  }
  setUser(result.user);
  return state.user;
}
async function refreshIdentitySession(expected) {
  if (!sameIdentityContext(expected, sessionIdentity())) return { applied: false };
  const { user } = await api("/api/session");
  if (!sameIdentityContext(expected, sessionIdentity())) return { applied: false };
  setUser(user);
  clearRecovery();
  const path = (location.hash.slice(1) || "/").split("?")[0];
  const top = app.querySelector(".site-header"), bottom = app.querySelector(".site-footer");
  if (top) top.outerHTML = header(path);
  if (bottom) bottom.outerHTML = footer();
  bindShell();
  return { applied: true, user };
}
function setUser(user) {
  identityLinks.actorChanged(state.user?.id, user?.id);
  if (state.user?.id !== user?.id) manualDraftReceipts = null;
  if (renderedRoute !== null && state.user?.id !== user?.id)
    clearWorkspaceDrafts();
  if (JSON.stringify(state.user) !== JSON.stringify(user)) sessionEpoch++;
  state.user = user;
}
const sessionIdentity = () => ({
  ownerId: state.user?.id || null,
  epoch: sessionEpoch,
});
observeApiSession(sessionIdentity, handleExpiredSession);
function handleExpiredSession(failure) {
  if (!shouldRecoverSession(failure, sessionIdentity())) return;
  recoveryAnchor = { ownerId: state.user.id, route: location.hash || "#/" };
  rememberCurrentDrafts();
  setUser(null);
  afterLogin = null;
  const path = (location.hash.slice(1) || "/").split("?")[0];
  const existingHeader = app.querySelector(".site-header");
  if (existingHeader) existingHeader.outerHTML = header(path);
  const existingFooter = app.querySelector(".site-footer");
  if (existingFooter) existingFooter.outerHTML = footer();
  bindShell();
  showRecoveryNotice();
  openAuth();
}
function rememberCurrentDrafts() {
  if (!recoveryAnchor || recoveryAnchor.route !== location.hash) return;
  const snapshots =
    renderedRoute === location.hash ? captureDraftForms(document) : [];
  if (
    manualDraftReceipts?.ownerId === recoveryAnchor.ownerId &&
    manualDraftReceipts.route === recoveryAnchor.route
  )
    snapshots.push(...manualDraftReceipts.snapshots);
  sessionDrafts.capture(
    recoveryAnchor.ownerId,
    recoveryAnchor.route,
    snapshots,
  );
}
function clearRecovery() {
  sessionDrafts.clear();
  recoveryAnchor = null;
  manualDraftReceipts = null;
  document.querySelector("#session-recovery-notice")?.remove();
}
function showRecoveryNotice() {
  document.querySelector("#session-recovery-notice")?.remove();
  if (!recoveryAnchor || recoveryAnchor.route !== location.hash) return;
  const main = document.querySelector("#main-content");
  if (!main) return;
  const notice = document.createElement("aside");
  notice.id = "session-recovery-notice";
  notice.className = "session-recovery-notice container";
  notice.setAttribute("role", "status");
  notice.innerHTML = `<div><strong>다시 로그인하고 이어가세요.</strong><p>${esc(recoveryCopy(sessionDrafts.peek(location.hash)).notice)}</p></div><button class="btn btn-outline btn-sm" type="button">다시 로그인</button>`;
  notice.querySelector("button").onclick = () => {
    afterLogin = null;
    openAuth();
  };
  main.prepend(notice);
}
function showRecoveredDrafts(packet) {
  if (packet.reason === "different-account") {
    toast(
      "현재 계정의 화면으로 연결했습니다. 이전 계정의 작성 내용은 지웠습니다.",
    );
    return;
  }
  if (packet.reason !== "same-account" || !packet.snapshots.length) return;
  const { restored, manual, discarded } = restoreDraftForms(
    app,
    packet.snapshots,
  );
  manualDraftReceipts = manual.length
    ? {
        ownerId: state.user.id,
        route: location.hash,
        snapshots: manual.map((snapshot) => ({
          ...snapshot,
          auto: false,
          id: snapshot.id.startsWith("receipt:")
            ? snapshot.id
            : "receipt:" + snapshot.id,
        })),
      }
    : null;
  const main = document.querySelector("#main-content");
  if (!main) return;
  const notice = document.createElement("section");
  notice.className = "session-draft-result container";
  notice.setAttribute("aria-label", "작성 내용 복구 결과");
  notice.innerHTML = `<p role="status">${restored.length ? "작성 내용을 복원했습니다. 내용을 확인한 뒤 직접 저장해주세요." : manual.length ? "현재 화면에 자동으로 옮길 수 없는 작성 내용이 있습니다." : "현재 권한에서 복원할 수 없는 작성 내용은 지웠습니다."}${discarded.length ? " 접근할 수 없는 초안은 표시하지 않습니다." : ""}${packet.snapshots.some((s) => s.omittedFiles) && !discarded.length ? " 파일은 다시 선택해주세요." : ""}</p>${manual.map((snapshot, index) => `<details><summary>이전에 작성한 내용 ${index + 1} 확인</summary><p>접근할 수 있는 항목이나 내용이 바뀌어 자동 복원을 멈췄습니다. 현재 화면과 비교한 뒤 필요한 부분을 직접 옮겨주세요.</p><textarea readonly rows="8" aria-label="보관한 작성 내용 ${index + 1}">${esc(draftText(snapshot))}</textarea></details>`).join("")}`;
  main.prepend(notice);
}
function requireAuth(callback) {
  if (state.user) return true;
  afterLogin = recoveryAnchor ? null : callback;
  openAuth();
  return false;
}

const header = (path) => renderHeader(path, state.user);
const footer = () => renderFooter(state.user);
function courseCard(course) {
  const coach = state.catalog.coaches.find((c) => c.id === course.coachId);
  return `<a class="course-card" href="#/course/${esc(course.id)}">${courseArt(course)}<div class="course-card-body"><div class="course-badges"><span class="pill">${esc(course.category)}</span><span class="course-level">${esc(course.level)}</span>${isFreeCourse(course) ? '<span class="free-label">FREE</span>' : ""}</div><h3>${esc(course.title)}</h3><p>${esc(course.subtitle)}</p><div class="course-card-meta"><span>${esc(course.instructorName || coach?.name || "TPC 에디토리얼")}</span><span>${icon("clock", 13)} ${esc(course.duration)}</span></div><div class="course-card-bottom"><span class="course-price ${isFreeCourse(course) ? "price-free" : ""}">${priceLabel(course)} ${course.price && course.pilot ? "<small>예시 가격</small>" : ""}</span><span>${course.lessons.length}개 레슨 ${icon("external", 16)}</span></div></div></a>`;
}
function library(params) {
  const q = params.get("q") || "",
    category = params.get("category") || "전체",
    level = params.get("level") || "전체",
    price = params.get("price") || "전체";
  const filtered = state.catalog.courses.filter(
    (c) =>
      (category === "전체" || c.category === category) &&
      (level === "전체" || c.level === level) &&
      (price === "전체" ||
        (price === "무료" ? isFreeCourse(c) : c.price > 0)) &&
      `${c.title} ${c.subtitle} ${c.category}`
        .toLowerCase()
        .includes(q.toLowerCase()),
  );
  const categories = [
    "전체",
    ...new Set(state.catalog.courses.map((c) => c.category)),
  ];
  return {
    html: `<section class="container library-page"><div class="page-head"><p class="eyebrow">LEARN AT YOUR PACE</p><h1>강의 라이브러리<span class="heading-dot">.</span></h1><p>지금 나의 플레이에 필요한 생각을 만나보세요.</p></div><div class="library-toolbar"><div class="category-tabs" role="group" aria-label="강의 주제">${categories.map((c) => `<button class="category-tab ${category === c ? "selected" : ""}" data-category="${esc(c)}" aria-pressed="${category === c}">${esc(c)}</button>`).join("")}</div><form class="search-box" id="search-form" role="search">${icon("search", 18)}<input name="q" aria-label="강의 검색" placeholder="어떤 주제를 찾고 있나요?" value="${esc(q)}"/><button type="submit" aria-label="검색">${icon("arrow", 18)}</button></form></div><div class="library-subtoolbar"><span>전체 <b>${filtered.length}</b>개 강의</span><div><label>난이도 <select id="level-filter" aria-label="난이도 필터">${["전체", "입문", "중급", "심화"].map((s) => `<option ${s === level ? "selected" : ""}>${s}</option>`).join("")}</select></label><label>수강 구분 <select id="price-filter" aria-label="무료 여부 필터">${["전체", "무료", "심화"].map((s) => `<option ${s === price ? "selected" : ""}>${s}</option>`).join("")}</select></label></div></div>${filtered.length ? `<div class="course-grid">${filtered.map(courseCard).join("")}</div>` : `<div class="empty-state">${icon("search", 32)}<h2>검색한 강의가 없습니다.</h2><p>다른 키워드나 주제로 찾아보세요.</p><a class="btn btn-outline" href="#/library">필터 초기화</a></div>`}<div class="library-bottom-note">${icon("info", 19)}<p>파일럿 강의는 예시 가격으로 구분됩니다. 강의별 수강 옵션과 결제 서비스 연결 상태는 상세 화면에서 확인하세요.</p></div></section>`,
    bind() {
      const update = (key, value) => {
        const next = new URLSearchParams(params);
        next.set(key, value);
        navigate(`/library?${next}`);
      };
      document
        .querySelectorAll("[data-category]")
        .forEach(
          (b) => (b.onclick = () => update("category", b.dataset.category)),
        );
      document.querySelector("#search-form").onsubmit = (e) => {
        e.preventDefault();
        update("q", new FormData(e.target).get("q"));
      };
      document.querySelector("#level-filter").onchange = (e) =>
        update("level", e.target.value);
      document.querySelector("#price-filter").onchange = (e) =>
        update("price", e.target.value);
    },
  };
}
async function coursePage(id) {
  const course = state.catalog.courses.find((c) => c.id === id);
  if (!course) return notFound();
  const { start } = await api(`/api/courses/${id}/start`);
  const coach = state.catalog.coaches.find((c) => c.id === course.coachId);
  return {
    html: `<section class="container detail-page"><a class="back-link" href="#/library">← 강의 라이브러리</a><div class="course-detail-grid"><div class="course-detail-main"><div class="course-badges"><span class="pill">${esc(course.category)}</span><span class="pill">${esc(course.level)}</span>${course.pilot ? '<span class="free-label">PILOT CURRICULUM</span>' : '<span class="free-label">TPC ACADEMY</span>'}</div><h1>${esc(course.title)}</h1><p class="course-detail-subtitle">${esc(course.subtitle)}</p><div class="course-detail-author"><span class="avatar-small">${esc(coach?.initials || "TP")}</span>${course.instructorName ? `<span>${esc(course.instructorName)}</span>` : `<a href="#/coach/${esc(course.coachId)}">${esc(coach?.name || "TPC 에디토리얼")}</a>`}<span>·</span><span>${icon("book", 16)} ${esc(course.format)} · ${course.lessons.length}개 차시</span><span>${icon("clock", 16)} ${esc(course.duration)}</span></div><div class="detail-description"><p class="eyebrow">ABOUT THIS COURSE</p><h2>플레이의 기준을 만드는 시간.</h2><p>${esc(course.description)}</p></div><div class="curriculum"><div class="curriculum-title"><h2>이렇게 배웁니다</h2><span>${course.lessons.length}개 레슨</span></div>${course.lessons.map((l, i) => `<a class="curriculum-row" href="#/learn/${esc(course.id)}/${esc(l.id)}"><span class="lesson-index">${String(i + 1).padStart(2, "0")}</span><span><strong>${esc(l.title)}</strong><small>${l.hasVideo ? "영상 차시" : "텍스트 차시"} · ${esc(l.duration)}</small></span><span class="lesson-access">${l.free ? "<span>무료 공개</span>" : icon("lock", 16)} ${icon("chevron", 17)}</span></a>`).join("")}</div><div class="p-course-qa-link"><div><h2>이 강의의 질문과 답변</h2><p>수강 후 차시별 질문을 남기고 담당 코치의 답변을 확인하세요.</p></div><a class="btn btn-outline btn-sm" href="#/qa?courseId=${esc(id)}">강의 Q/A ${icon("arrow", 16)}</a></div>${course.pilot ? `<div class="detail-pilot-note">${icon("info", 20)}<div><strong>파일럿 커리큘럼입니다.</strong><p>기본 콘텐츠는 텍스트 레슨과 확인 퀴즈입니다. 영상이 등록된 차시는 영상으로 표시되며, 확정되지 않은 가격과 코치 프로필은 예시로 구분합니다.</p></div></div>` : ""}</div><aside class="course-enroll-card">${courseArt(course)}<div class="enroll-card-body"><span class="eyebrow">${isFreeCourse(course) ? "START WITH THE BASICS" : "YOUR NEXT CHAPTER"}</span><div class="detail-price">${priceLabel(course)} ${isFreeCourse(course) ? "<span>누구나 시작할 수 있어요</span>" : course.price == null || course.pricing?.purchaseReady === false ? "<span>제공 조건 확인 후 안내</span>" : course.pilot ? "<span>예시 가격 · 파일럿</span>" : "<span>수강 상품 금액</span>"}</div><button id="enroll-button" class="btn btn-primary btn-block">${esc(start.primary.label)} ${icon("arrow", 18)}</button><p class="enroll-note">${esc(start.message)}</p><a class="text-link" href="#/start/${esc(id)}">수강 시작 안내 ↗</a><ul class="enroll-benefits"><li>${icon("check", 16)} 구조화된 한국어 커리큘럼</li><li>${icon("check", 16)} 레슨별 핵심 정리와 확인 퀴즈</li><li>${icon("check", 16)} 마이 캠프에서 학습 진도 관리</li></ul></div></aside></div></section>`,
    bind() {
      document.querySelector("#enroll-button").onclick = () => {
        navigate(
          ["register", "login"].includes(start.primary.kind)
            ? `/start/${id}`
            : start.primary.href.slice(1),
        );
      };
    },
  };
}

function faqRows() {
  return [
    [
      "처음 시작해도 괜찮을까요?",
      "네. 무료 프리플랍 기초와 핸드 리뷰 입문 레슨부터 시작해보세요. 강의 라이브러리에서 입문 난이도와 무료 필터로 찾아볼 수 있습니다.",
    ],
    [
      "현재 어떤 서비스를 이용할 수 있나요?",
      "강의 학습과 진도 기록, 강의·차시 Q/A, 커뮤니티와 알림을 이용할 수 있습니다. 코칭 상담·핸드 리뷰·레인지 노트도 내 계정에 연결됩니다. 강의의 제공 형식은 각 커리큘럼에서 확인하세요.",
    ],
    [
      "수강 신청을 하면 바로 결제되나요?",
      "파일럿 수강 문의에는 결제가 발생하지 않습니다. 결제 서비스가 연결된 상품은 결제 화면에 테스트 또는 실제 결제 환경과 금액을 표시하며, 결제사 인증과 서버 승인 확인 뒤 수강권이 발급됩니다. 결제 기능이 연결되지 않은 상품에는 준비 중 안내가 표시됩니다.",
    ],
    [
      "코칭 신청과 예약 확정은 어떻게 다른가요?",
      "신청은 희망 코치, 날짜, 시간대를 접수하는 단계입니다. 운영자가 일정과 담당 코치를 확인해 확정 안내를 남기면 마이 캠프에서 볼 수 있습니다. 접수 단계의 신청은 직접 취소할 수 있습니다.",
    ],
    [
      "핸드 리뷰는 어떻게 작성하나요?",
      "홀카드와 보드는 As Kh 또는 Qs Jh 2d처럼 입력하고, 포지션과 유효 스택, 스트리트별 액션, 궁금한 점을 적어주세요. 제출한 핸드와 실제 답변은 마이 캠프에 저장됩니다.",
    ],
    [
      "영상 강의와 솔버는 언제 제공되나요?",
      "기본 파일럿은 텍스트 레슨입니다. 영상이 등록된 차시에는 영상 표시가 있으며, 무료 미리보기 또는 활성 수강권으로 재생합니다. 솔버는 추후 별도로 연결하며 현재 분석 결과를 제공하지 않습니다.",
    ],
    [
      "포커 게임이나 베팅을 제공하나요?",
      "TPC는 포커의 개념과 의사결정을 배우는 교육 서비스입니다. 강의, 핸드 분석과 코칭을 중심으로 운영됩니다.",
    ],
    [
      "신청 내역은 어디에서 확인하나요?",
      "마이 캠프에서 학습·코칭·리뷰를, 내 주문에서 결제와 취소 문의를 확인합니다. 강의 Q/A와 알림에서도 질문·답변 진행 상황을 확인할 수 있습니다.",
    ],
  ];
}
function faqItem([q, a]) {
  return `<details><summary>${esc(q)}<span>+</span></summary><p>${esc(a)}</p></details>`;
}
function faqPage() {
  return `<section class="container prose-page"><div class="page-head"><p class="eyebrow">GOOD QUESTIONS, CLEAR ANSWERS</p><h1>자주 묻는 질문<span class="heading-dot">.</span></h1><p>캠프를 시작하기 전에 궁금한 점을 확인하세요.</p></div><div class="faq-list">${faqRows().map(faqItem).join("")}</div><div class="help-note"><h2>시작할 준비가 되셨나요?</h2><a class="btn btn-primary" href="#/library">무료 강의 둘러보기 ${icon("arrow")}</a></div></section>`;
}
function aboutPage() {
  return `<section class="container prose-page"><div class="page-head"><p class="eyebrow">ABOUT THE CAMP</p><h1>좋은 플레이의 시작,<br>투페어 포커캠프.</h1><p>학습과 복기를 연결하는 한국어 포커 교육 플랫폼입니다.</p></div><article class="prose-card"><h2>배움에서 실전의 생각으로</h2><p>TPC는 구조화된 커리큘럼, 핸드 리뷰, 코칭 연결을 통해 플레이어가 자신의 판단을 돌아볼 수 있는 학습 환경을 만듭니다.</p><h2>파일럿 서비스 안내</h2><p>현재 강의는 텍스트 기반 파일럿 콘텐츠이며, 파일럿 코치 소개는 예시로 구분하며, 공개 소개와 샘플은 검수를 거칩니다. 코치가 소개한 경험과 운영자가 확인한 사실은 별도로 표시합니다. 심화 강의의 예시 가격으로 결제를 청구하지 않으며, 수강 신청은 운영 확인 후 승인됩니다.</p><h2>계정과 신청 정보</h2><p>이름, 이메일, 학습 진도와 직접 제출한 신청 내용은 계정별로 저장됩니다. 코칭과 핸드 리뷰를 처리하는 운영자는 해당 신청 내용과 연락 이메일을 확인할 수 있습니다. 제출란에는 학습에 필요한 정보만 작성해주세요.</p><h2>코치 파트너십</h2><p>커리큘럼 설계, 콘텐츠 제작과 유통을 함께하는 파트너를 준비하고 있습니다. 구체적인 협업 방식과 수익분배는 개별 협의하며, 기획안과 제안서에서 방향을 확인할 수 있습니다.</p><a class="text-link" href="/proposal/">사업기획안과 코치 제안서 ${icon("external", 18)}</a></article></section>`;
}
function notFound() {
  return `<section class="container empty-state not-found"><p class="eyebrow">404 / OFF THE TABLE</p><h1>찾으시는 페이지가 없습니다.</h1><p>주소를 확인하거나 캠프 홈으로 돌아가세요.</p><a href="#/" class="btn btn-primary">TPC 홈으로 ${icon("arrow")}</a></section>`;
}

function openAuth(mode = "login") {
  rememberCurrentDrafts();
  const recovering = Boolean(recoveryAnchor);
  const flow = ++authFlow;
  const signup = mode === "register";
  modal(
    `<p class="eyebrow">${recovering ? "CONTINUE YOUR WORK" : "WELCOME TO THE CAMP"}</p><h2>${signup ? "나만의 성장을 시작하세요." : recovering ? "다시 로그인하고 이어가세요." : "다시 만나 반갑습니다."}</h2><p class="dialog-description">${recovering ? esc(recoveryCopy(sessionDrafts.peek(location.hash)).description) : "학습 기록부터 핸드 리뷰까지, 한곳에서."}</p><form id="auth-form">${signup ? '<label class="field">이름<input name="name" autocomplete="name" required minlength="2" maxlength="50" placeholder="캠프에서 사용할 이름"/></label>' : ""}<label class="field">이메일<input name="email" type="email" autocomplete="email" required maxlength="254" placeholder="you@example.com"/></label><label class="field">비밀번호<input name="password" type="password" autocomplete="${signup ? "new-password" : "current-password"}" required ${signup ? 'minlength="8"' : ""} maxlength="128" placeholder="${signup ? "8자 이상 입력해주세요" : "비밀번호를 입력해주세요"}"/></label>${signup ? '<p class="form-note">계정을 만들면 이름·이메일과 학습·신청 기록이 저장됩니다. 신청 내용은 담당 운영자가 확인할 수 있습니다.</p>' : ""}<p class="form-error" role="alert"></p><button class="btn btn-primary btn-block" type="submit">${signup ? "계정 만들기" : "로그인"} ${icon("arrow", 18)}</button></form>${signup ? "" : '<p class="auth-recovery-link"><a href="#/account/recover" id="auth-recover">비밀번호를 잊으셨나요?</a></p>'}<p class="auth-switch">${signup ? "이미 함께하고 계신가요?" : "TPC가 처음이신가요?"} <button type="button" id="auth-switch">${signup ? "로그인" : "무료 계정 만들기"}</button></p>`,
    (dialog) => {
      dialog.setAttribute("aria-label", signup ? "회원가입" : "로그인");
      dialog.dataset.sessionAuth = "true";
      dialog.querySelector("#auth-recover")?.addEventListener("click", (event) => {
        if (dialog.dataset.busy === "true") { event.preventDefault(); return; }
        dialog.querySelector("#auth-form")?.reset();
        dialog.close();
      });
      dialog.querySelector("#auth-switch").onclick = () =>
        openAuth(signup ? "login" : "register");
      dialog.querySelector("form").onsubmit = async (e) => {
        e.preventDefault();
        const button = e.target.querySelector("button[type=submit]"),
          error = dialog.querySelector(".form-error");
        error.textContent = "";
        button.disabled = true;
        dialog.dataset.busy = "true";
        dialog.querySelector(".dialog-close").disabled = true;
        dialog.querySelector("#auth-switch").disabled = true;
        button.textContent = signup ? "계정을 만드는 중…" : "로그인 중…";
        try {
          rememberCurrentDrafts();
          const route = location.hash;
          const wasRecovering = Boolean(recoveryAnchor);
          const values = Object.fromEntries(new FormData(e.target));
          await api(`/api/auth/${signup ? "register" : "login"}`, {
            method: "POST",
            body: values,
          });
          if (flow !== authFlow) return;
          setUser(null);
          ++renderVersion;
          app.innerHTML = `${header("/")}<main id="main-content" class="container empty-state"><h1>캠프에 연결하고 있습니다.</h1><p>현재 계정의 접근 권한과 작성 화면을 확인합니다.</p></main>${footer()}`;
          bindShell();
          if (!(await refreshSession()))
            throw new Error(
              "로그인 상태를 확인하지 못했습니다. 다시 연결해주세요.",
            );
          await refreshCatalog();
          const ownerId = state.user?.id;
          await render();
          if (flow !== authFlow || !ownerId || state.user?.id !== ownerId)
            return;
          const packet = sessionDrafts.take(ownerId, location.hash);
          const callback =
            !wasRecovering && route === location.hash ? afterLogin : null;
          afterLogin = null;
          document.querySelector("#toast-region")?.replaceChildren();
          clearRecovery();
          showRecoveredDrafts(packet);
          dialog.dataset.busy = "false";
          dialog.dataset.sessionAuth = "false";
          authFlow++;
          dialog.querySelector("#auth-form")?.reset();
          dialog.close();
          if (packet.reason !== "different-account")
            toast(
              wasRecovering
                ? recoveryCopy(packet).afterLogin
                : `${state.user.name}님, 캠프에 오신 것을 환영합니다.`,
            );
          await callback?.();
        } catch (err) {
          if (flow !== authFlow || !dialog.open) return;
          error.textContent = err.message;
          button.disabled = false;
          button.textContent = signup ? "계정 만들기" : "로그인";
        } finally {
          if (flow === authFlow) {
            dialog.dataset.busy = "false";
            dialog.querySelector(".dialog-close").disabled = false;
            dialog.querySelector("#auth-switch").disabled = false;
            button.disabled = false;
          }
        }
      };
    },
  );
}
function bindShell() {
  document.querySelector(".skip-link").onclick = (event) => {
    event.preventDefault();
    document.querySelector("#main-content")?.focus();
  };
  document.querySelectorAll(".login-button").forEach(
    (b) =>
      (b.onclick = () => {
        afterLogin = null;
        openAuth();
      }),
  );
  document.querySelectorAll(".signup-button").forEach(
    (b) =>
      (b.onclick = () => {
        afterLogin = null;
        openAuth("register");
      }),
  );
  document.querySelectorAll(".logout-button").forEach((button) =>
    button.addEventListener("click", async () => {
      button.disabled = true;
      try {
        await api("/api/auth/logout", { method: "POST" });
        setUser(null);
        clearRecovery();
        await refreshCatalog();
        toast("로그아웃했습니다.");
        navigate("/");
        await render();
      } catch (e) {
        toast(e.message, "error");
      } finally {
        if (button.isConnected) button.disabled = false;
      }
    }),
  );
  const toggle = document.querySelector(".mobile-toggle"),
    nav = document.querySelector("#mobile-nav");
  toggle.onclick = () => {
    const open = toggle.getAttribute("aria-expanded") !== "true";
    toggle.setAttribute("aria-expanded", String(open));
    toggle.setAttribute("aria-label", open ? "메뉴 닫기" : "메뉴 열기");
    nav.hidden = !open;
  };
}
async function render() {
  activePageCleanup?.();
  activePageCleanup = null;
  captureIdentityRoute();
  const version = ++renderVersion;
  const raw = location.hash.slice(1) || "/";
  const [path, query = ""] = raw.split("?"),
    params = new URLSearchParams(query);
  let result;
  try {
    const ctx = {
      ...state,
      api,
      navigate,
      toast,
      modal,
      refreshSession,
      refreshCatalog,
      reload: render,
      esc,
      courseArt,
      courseCard,
      icon,
      requireAuth,
      sessionIdentity,
      refreshIdentitySession,
    };
    if (path === "/")
      result = renderHome({
        catalog: state.catalog,
        courseCard,
        coachCard,
        faqRows,
        faqItem,
      });
    else if (path === "/library") result = library(params);
    else if (path.startsWith("/course/"))
      result = await coursePage(path.slice(8));
    else if (path === "/coaches") result = await coachesPage(params, ctx);
    else if (path.startsWith("/coach/"))
      result = await coachPage(path.slice(7), ctx);
    else if (path === "/faq") result = faqPage();
    else if (path === "/about") result = aboutPage();
    else {
      result =
        (await renderIdentity(path, params, ctx, identityLinks)) ||
        (await renderPlatformPage(path, params, ctx)) ||
        (await renderStudyPage(path, params, ctx)) ||
        (await renderMemberPage(path, params, ctx));
    }
    if (version !== renderVersion) return;
    if (!result) result = notFound();
    const html = typeof result === "string" ? result : result.html;
    app.innerHTML = `${header(path)}<main id="main-content" tabindex="-1">${html}</main>${footer()}`;
    const titles = {
      "/": "투페어 포커캠프",
      "/library": "강의 라이브러리",
      "/study": "레인지 스터디",
      "/practice": "개념 트레이닝",
      "/notes": "내 스터디 기록",
      "/coaches": "코치진",
      "/my": "마이 캠프",
      "/account": "나의 계정",
      "/account/recover": "계정 복구",
      "/account/verify": "이메일 인증",
      "/account/email-change": "이메일 변경",
      "/account/reset": "비밀번호 재설정",
      "/review": "핸드 리뷰",
      "/coaching": "코칭 신청",
      "/admin": "코칭·리뷰 운영",
      "/studio": "강의 스튜디오",
      "/manage": "관리센터",
      "/qa": "강의 Q/A",
      "/community": "커뮤니티",
      "/purchases": "주문 내역",
      "/inbox": "알림",
      "/solver": "솔버",
      "/faq": "자주 묻는 질문",
      "/about": "서비스 안내",
    };
    document.title = `${titles[path] || document.querySelector("h1")?.textContent || "학습"} | TPC`;
    bindShell();
    if (typeof result !== "string") {
      result.bind?.();
      activePageCleanup = result.dispose ? () => result.dispose() : null;
    }
    renderedRoute = location.hash;
    showRecoveryNotice();
  } catch (error) {
    if (version !== renderVersion) return;
    if (error.status === 401) setUser(null);
    const authNeeded = error.status === 401;
    const title = authNeeded
      ? "로그인하고 이어가세요."
      : error.status === 403
        ? "이용 권한을 확인해주세요."
        : error.status === 404
          ? "요청한 내용을 찾을 수 없습니다."
          : "화면을 불러오지 못했습니다.";
    document.title = `${title} | TPC`;
    app.innerHTML = `${header(path)}<main id="main-content" class="container empty-state not-found"><p class="eyebrow">TPC CAMP</p><h1>${title}</h1><p>${esc(error.message)}</p>${authNeeded ? '<button class="btn btn-primary" id="retry-login">로그인</button>' : '<button class="btn btn-primary" id="retry">다시 확인</button>'}<a class="btn btn-outline" href="#/library">강의 라이브러리</a></main>${footer()}`;
    bindShell();
    renderedRoute = location.hash;
    showRecoveryNotice();
    document.querySelector("#retry")?.addEventListener("click", render);
    document
      .querySelector("#retry-login")
      ?.addEventListener("click", () => requireAuth());
  }
}
window.addEventListener("hashchange", async () => {
  captureIdentityRoute();
  clearRecovery();
  afterLogin = null;
  const dialog = document.querySelector("#app-dialog");
  if (
    dialog.open &&
    dialog.dataset.sessionAuth === "true" &&
    dialog.dataset.busy !== "true"
  )
    dialog.close();
  window.scrollTo({ top: 0, behavior: "instant" });
  await render();
  const focusTarget =
    document.querySelector("#main-content [data-route-focus]") ||
    document.querySelector("#main-content");
  focusTarget?.focus({ preventScroll: true });
});
window.addEventListener("pagehide", () => {
  identityLinks.clear();
  activePageCleanup?.();
  activePageCleanup = null;
  document.querySelector("#auth-form")?.reset();
});
window.addEventListener("pageshow", (event) => { if (event.persisted) render(); });
document.querySelector("#app-dialog").addEventListener("close", (event) => {
  const dialog = event.target;
  if (dialog.dataset.sessionAuth !== "true") return;
  dialog.dataset.sessionAuth = "false";
  authFlow++;
  afterLogin = null;
  dialog.querySelector("#auth-form")?.reset();
  showRecoveryNotice();
});
async function init() {
  const paymentParams = new URLSearchParams(location.search);
  if (["success", "fail"].includes(paymentParams.get("paymentResult"))) {
    const route = paymentParams.get("paymentResult");
    paymentParams.delete("paymentResult");
    history.replaceState(null, "", `/#/payment/${route}?${paymentParams}`);
  }
  try {
    const [catalog] = await Promise.all([
      api("/api/catalog"),
      refreshSession(),
    ]);
    state.catalog = catalog;
    await render();
  } catch (e) {
    app.innerHTML = `<main class="container boot-error"><span class="brand-word">TPC</span><h1>캠프에 연결하지 못했습니다.</h1><p>${esc(e.message)}</p><p>서버가 실행 중인지 확인한 후 새로고침해주세요.</p><button class="btn btn-primary" id="boot-retry">다시 연결</button></main>`;
    document.querySelector("#boot-retry").onclick = init;
  }
}
init();
