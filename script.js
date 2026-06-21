const body = document.body;
const menuToggle = document.querySelector(".menu-toggle");
const nav = document.querySelector(".site-nav");
const diagnosisForm = document.querySelector(".diagnosis-form");
const formNote = document.querySelector(".form-note");

const revealSelectors = [
  ".hero-art",
  ".hero-ribbon",
  ".trust-band > div",
  ".section-kicker",
  ".section-heading-row",
  ".proof-card",
  ".coach-card",
  ".flow-visual",
  ".library-equation",
  ".step-card",
  ".suite-board",
  ".suite-card",
  ".course-filter-bar span",
  ".lecture-group",
  ".program-card",
  ".membership-card",
  ".diagnosis-copy",
  ".diagnosis-form",
  ".resource-list a",
  ".review-visual",
  ".review-section > div:not(.review-visual)",
  ".community-section",
  ".faq-list details",
  ".final-cta",
];

function trackEvent(eventName, detail = {}) {
  if (!eventName) return;
  console.info(`[TPC event] ${eventName}`, detail);
}

let scrollTicking = false;
let pointerTicking = false;
let pointerEvent = null;

function updateScrollState() {
  const scrollY = window.scrollY || 0;
  const scrollRatio = Math.min(scrollY / 700, 1).toFixed(3);

  body.classList.toggle("has-scrolled", scrollY > 12);
  document.documentElement.style.setProperty("--scroll-ratio", scrollRatio);
  scrollTicking = false;
}

function queueScrollUpdate() {
  if (scrollTicking) return;
  scrollTicking = true;
  window.requestAnimationFrame(updateScrollState);
}

function updatePointerState() {
  if (!pointerEvent) return;
  const pointerX = pointerEvent.clientX / Math.max(window.innerWidth, 1) - 0.5;
  const pointerY = pointerEvent.clientY / Math.max(window.innerHeight, 1) - 0.5;

  document.documentElement.style.setProperty("--pointer-x", pointerX.toFixed(3));
  document.documentElement.style.setProperty("--pointer-y", pointerY.toFixed(3));
  pointerTicking = false;
}

function queuePointerUpdate(event) {
  pointerEvent = event;
  if (pointerTicking) return;
  pointerTicking = true;
  window.requestAnimationFrame(updatePointerState);
}

function setupRevealAnimation() {
  const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const revealTargets = [...document.querySelectorAll(revealSelectors.join(","))];

  revealTargets.forEach((element, index) => {
    element.dataset.reveal = "";
    element.style.setProperty("--reveal-delay", `${Math.min(index % 5, 4) * 60}ms`);
  });

  body.classList.add("reveal-ready");

  if (reduceMotion || !("IntersectionObserver" in window)) {
    revealTargets.forEach((element) => element.classList.add("is-visible"));
    return;
  }

  const observer = new IntersectionObserver(
    (entries) => {
      entries.forEach((entry) => {
        if (!entry.isIntersecting) return;
        entry.target.classList.add("is-visible");
        observer.unobserve(entry.target);
      });
    },
    {
      rootMargin: "0px 0px -6% 0px",
      threshold: 0.1,
    },
  );

  revealTargets.forEach((element) => observer.observe(element));
}

// Initialize Spotlight effects on interactive cards
function setupSpotlightEffects() {
  const cardSelectors = [
    ".proof-card",
    ".coach-card",
    ".program-card",
    ".suite-card",
    ".membership-card",
    ".step-card"
  ];
  
  const cards = document.querySelectorAll(cardSelectors.join(","));
  
  cards.forEach(card => {
    card.classList.add("spotlight-card");
    
    // Assign specific gold spotlight effect for paid/premium cards
    if (
      card.classList.contains("paid-card") || 
      card.classList.contains("coach-card-solver") || 
      card.classList.contains("membership-card-featured")
    ) {
      card.classList.add("spotlight-card-gold");
    }
    
    // Add local coordinate tracker
    card.addEventListener("pointermove", (e) => {
      const rect = card.getBoundingClientRect();
      const x = e.clientX - rect.left;
      const y = e.clientY - rect.top;
      
      card.style.setProperty("--pointer-x", x.toFixed(1));
      card.style.setProperty("--pointer-y", y.toFixed(1));
    });
  });
}

// Mobile Menu toggles
menuToggle?.addEventListener("click", () => {
  const isOpen = nav?.classList.toggle("is-open");
  body.classList.toggle("menu-open", Boolean(isOpen));
  menuToggle.setAttribute("aria-expanded", String(Boolean(isOpen)));
});

nav?.addEventListener("click", (event) => {
  if (event.target instanceof HTMLAnchorElement) {
    nav.classList.remove("is-open");
    body.classList.remove("menu-open");
    menuToggle?.setAttribute("aria-expanded", "false");
  }
});

// Event tracking
document.querySelectorAll("[data-event], [data-track]").forEach((element) => {
  element.addEventListener("click", () => {
    trackEvent(element.getAttribute("data-event") || element.getAttribute("data-track"), {
      label: element.textContent?.trim().replace(/\s+/g, " "),
    });
  });
});

// Diagnostic form submission handler
diagnosisForm?.addEventListener("submit", (event) => {
  event.preventDefault();
  const data = new FormData(diagnosisForm);
  trackEvent("diagnosis_submit", Object.fromEntries(data.entries()));
  formNote.textContent = "진단 요청이 준비되었습니다. 실제 서비스 연결 전까지 외부로 전송되지 않습니다.";
  diagnosisForm.querySelector("button[type='submit']").textContent = "진단 요청 준비 완료";
});

// Event Listeners
window.addEventListener("scroll", queueScrollUpdate, { passive: true });
window.addEventListener("resize", queueScrollUpdate, { passive: true });
window.addEventListener("pointermove", queuePointerUpdate, { passive: true });

// Init
updateScrollState();
setupRevealAnimation();
setupSpotlightEffects();
