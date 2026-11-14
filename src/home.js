import { icon, esc } from "./ui.js";
import { studyPreview } from "./study.js";

/** Presentation layer; every CTA routes to a working learning flow. */
export function renderHome({
  catalog,
  courseCard,
  coachCard,
  faqRows,
  faqItem,
}) {
  const courses = catalog.courses,
    featured = [
      courses.find((c) => c.id === "preflop-foundations"),
      courses.find((c) => c.id === "live-cash"),
      courses.find((c) => c.id === "mtt-icm"),
    ].filter(Boolean);
  return `<div class="home-frame">
 <section class="hero container">
  <div class="hero-copy"><span class="hero-overline"><i></i> TWO PAIR POKER CAMP</span><h1>모든 선택에,<br><span>분명한 기준을.</span></h1><p class="hero-description">흩어진 지식을, 나의 플레이로.<br>강의를 배우고, 코치에게 묻고, 함께 성장하는 포커 캠프.</p><div class="hero-actions"><a class="btn btn-primary" href="#/library">강의 둘러보기 ${icon("arrow", 18)}</a><a class="hero-secondary" href="#/community">커뮤니티 둘러보기 ${icon("chevron", 17)}</a></div><div class="hero-assurance"><span>${icon("check", 14)} 한국어로 깊이 있게</span><span>${icon("check", 14)} 나만의 속도로</span></div></div>
  <div class="hero-product"><div class="hero-product-glow"></div>${academyPreview(catalog)}<span class="hero-product-caption"><span class="live-dot"></span> LEARN. ASK. GROW TOGETHER.</span></div>
 </section>
 <section class="container home-features" aria-label="TPC 학습 방식">
  <a href="#/library" class="home-feature"><span class="feature-icon">${icon("layers", 26)}</span><h2>Courses <span>체계적으로 배우고</span></h2><p>주제별 커리큘럼과 차시별 학습으로<br>플레이의 기준을 만드세요.</p><span class="feature-arrow">${icon("external", 20)}</span></a>
  <a href="#/qa" class="home-feature"><span class="feature-icon">${icon("target", 26)}</span><h2>Q/A <span>깊이 있게 묻고</span></h2><p>강의에서 막힌 지점을 질문하고,<br>담당 코치의 답변으로 이해를 넓히세요.</p><span class="feature-arrow">${icon("external", 20)}</span></a>
  <a href="#/community" class="home-feature"><span class="feature-icon">${icon("chart", 26)}</span><h2>Community <span>함께 성장하다</span></h2><p>학습 기록과 핸드의 관점을 나누고,<br>함께 공부할 동료를 만나보세요.</p><span class="feature-arrow">${icon("external", 20)}</span></a>
 </section>
 <section class="section container home-study" id="Study">
  <div class="centered-heading"><span class="section-pill">STUDY</span><h2><span>한 스팟을,</span> 깊이 이해하는 시간.</h2><p>핸드를 선택하고, 생각을 정리하고, 나만의 기준으로.<br>막연한 감각이 구체적인 질문이 되는 학습을 시작하세요.</p></div>
  <div class="study-feature-grid">
   <article class="product-feature-card"><div class="product-feature-visual">${studyPreview()}</div><div class="product-feature-copy"><span class="eyebrow">YOUR RANGE. YOUR REASON.</span><h3>외우는 레인지에서,<br>생각하는 레인지로.</h3><p>169개 핸드 클래스를 직접 편집하고, 상황과 가정을 함께 기록하세요. 조합 수와 팟오즈도 확인할 수 있습니다.</p><a class="text-link" href="#/study">레인지 노트 만들기 ${icon("arrow", 17)}</a></div></article>
   <article class="product-feature-card"><div class="learning-preview"><div class="preview-sidebar"><span class="preview-logo">TPC</span><span class="active">${icon("book", 13)} 내 학습</span><span>${icon("layers", 13)} 스터디</span><span>${icon("message", 13)} 핸드 리뷰</span></div><div class="preview-curriculum"><span class="preview-kicker">YOUR LEARNING PATH</span><h4>프리플랍의 기준을 세우다</h4><div class="preview-progress"><i></i></div><div><span class="preview-check">✓</span><span>포지션이 만드는 차이<small>LESSON 01</small></span></div><div><span class="preview-current">02</span><span>시작 핸드를 고르는 질문<small>텍스트 레슨 · 확인 퀴즈</small></span>${icon("play", 12)}</div><div><span>03</span><span>표보다 먼저 읽어야 할 전제<small>LESSON 03</small></span></div><a href="#/library">나의 다음 레슨 찾기 ${icon("arrow", 14)}</a></div></div><div class="product-feature-copy"><span class="eyebrow">A CLEAR PATH FORWARD</span><h3>정보를 쌓기보다,<br>실력으로 이어지도록.</h3><p>프리플랍부터 MTT·멘탈까지. 한국어 레슨과 확인 퀴즈로 이해하고, 학습 기록에서 다음 단계를 이어가세요.</p><a class="text-link" href="#/library">강의 라이브러리 ${icon("arrow", 17)}</a></div></article>
  </div>
 </section>
 <section class="container home-practice">
  <div class="centered-heading"><span class="section-pill">PRACTICE</span><h2>배운 것을, <span>나의 판단으로.</span></h2><p>한 번에 한 개념씩. 짧은 퀴즈와 해설로 이해를 점검하세요.</p></div>
  <div class="practice-banner"><div class="practice-banner-copy"><span class="eyebrow">SMALL STEPS. REAL UNDERSTANDING.</span><h3>좋은 판단도<br>연습이 필요하니까.</h3><p>공개 레슨의 핵심 개념으로 구성한 5문제.<br>정답을 맞히고, 이유를 이해하고,<br>다음 학습으로 연결하세요.</p><a class="btn btn-primary" href="#/practice">트레이닝 시작 ${icon("arrow", 17)}</a></div><div class="quiz-preview" aria-label="개념 퀴즈 미리보기"><div><span class="pill">QUICK CHECK</span><span>02 / 05</span></div><h4>콜하기 전,<br>무엇을 먼저 확인해야 할까요?</h4><span class="quiz-preview-choice"><i>A</i> 직전 핸드의 결과</span><span class="quiz-preview-choice is-correct"><i>B</i> 팟 크기와 필요한 콜 금액 ${icon("check", 17)}</span><span class="quiz-preview-choice"><i>C</i> 오늘의 승패</span><div class="quiz-preview-feedback">${icon("book", 16)} 계산할 수 있는 기준부터 정리해보세요.</div></div></div>
 </section>
 <section class="section container home-library"><div class="centered-heading"><span class="section-pill">LEARNING LIBRARY</span><h2>다음 레벨을 위한 <span>다음 강의.</span></h2><p>기초를 단단하게. 생각은 더 깊게.<br>지금 나에게 필요한 주제를 만나보세요.</p></div><div class="course-grid">${featured.map(courseCard).join("")}</div><div class="section-bottom"><a class="btn btn-outline" href="#/library">전체 강의 둘러보기 ${icon("arrow", 17)}</a><p class="catalog-note">텍스트 기반 파일럿 콘텐츠 · 심화 강의 예시 가격 · 신청 시 결제 없음</p></div></section>
 <section class="container home-review" id="Review"><div class="centered-heading"><span class="section-pill">REVIEW</span><h2>결과보다, <span>판단의 과정을.</span></h2><p>그때의 선택을 돌아보면, 다음 선택의 기준이 보입니다.</p></div><div class="review-banner"><div class="review-notebook" aria-hidden="true"><div class="review-notebook-header"><span>${icon("layers", 15)} HAND REVIEW</span><span>YOUR SPOT / 001</span></div><div class="review-spot"><span>BTN</span><div class="review-cards"><i>A<small>♠</small></i><i>K<small>♥</small></i></div><strong>100<small>BB</small></strong></div><div class="review-question"><span>MY QUESTION</span><p>그때, 다른 선택이 있었을까?</p><div></div><div></div></div></div><div class="review-banner-copy"><span class="eyebrow">A DIFFERENT PERSPECTIVE</span><h3>혼자 풀리지 않던 핸드,<br>함께 살펴보세요.</h3><p>포지션과 스택, 스트리트별 액션과 고민까지.<br>상황을 기록하고 코치에게 리뷰를 요청하세요.<br>마이 캠프에서 진행 상태와 답변을 확인할 수 있습니다.</p><a class="btn btn-primary" href="#/review">핸드 리뷰 신청 ${icon("arrow", 17)}</a><a class="text-link" href="#/coaches">코치진 알아보기 ${icon("chevron", 16)}</a></div></div></section>
 <section class="section container home-coaches"><div class="section-heading"><div><span class="section-pill">COACHING</span><h2>다른 관점, 같은 성장의 방향.</h2></div><a class="text-link" href="#/coaches">코치진 살펴보기 ${icon("arrow", 18)}</a></div><div class="coach-grid">${catalog.coaches.map(coachCard).join("")}</div><p class="catalog-note">파일럿 소개는 예시로 표시합니다. 공개 프로필에서 전문 분야와 현재 접수 가능 여부를 확인하세요.</p></section>
 <section class="section container faq-preview"><div><span class="section-pill">FAQ</span><h2>캠프가 처음이신가요?</h2><a class="text-link" href="#/faq">모든 질문 보기 ${icon("arrow", 17)}</a></div><div class="faq-list">${faqRows().slice(0, 3).map(faqItem).join("")}</div></section>
 <section class="container final-banner"><span class="section-pill">YOUR NEXT MOVE</span><h2>좋은 질문이,<br><span>좋은 플레이를 만듭니다.</span></h2><a class="btn btn-primary" href="#/library">나의 다음 강의 찾기 ${icon("arrow", 18)}</a><p>한 핸드씩, 나만의 속도로. Two Pair Poker Camp.</p></section>
 </div>`;
}

function academyPreview(catalog) {
  const course =
    catalog.courses.find((c) => c.id === "preflop-foundations") ||
    catalog.courses[0];
  if (!course) return "";
  return `<div class="academy-preview"><div class="academy-preview-top"><span>TPC <i>ACADEMY</i></span><span>${icon("book", 14)} LEARNING SPACE</span></div><div class="academy-preview-body"><div class="academy-preview-kicker">YOUR NEXT CHAPTER</div><h2>${esc(course.title)}</h2><p>하나의 강의에서, 다음 질문까지.</p><div class="academy-preview-lessons">${course.lessons
    .slice(0, 3)
    .map(
      (lesson, i) =>
        `<a href="#/learn/${esc(course.id)}/${esc(lesson.id)}"><span>${String(i + 1).padStart(2, "0")}</span><div><strong>${esc(lesson.title)}</strong><small>${esc(lesson.duration)} · ${lesson.free ? "무료 공개" : "수강권 차시"}</small></div>${icon(lesson.free ? "play" : "lock", 16)}</a>`,
    )
    .join(
      "",
    )}</div><div class="academy-preview-links"><a href="#/qa">${icon("message", 17)} 강의 Q/A ${icon("arrow", 14)}</a><a href="#/community">${icon("users", 17)} 커뮤니티 ${icon("arrow", 14)}</a></div></div><a class="academy-preview-bottom" href="#/library"><span>나에게 필요한 다음 강의</span>${icon("arrow", 18)}</a></div>`;
}
