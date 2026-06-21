const root = document.documentElement;
const body = document.body;
const slides = [...document.querySelectorAll(".slide")];
const navButtons = [...document.querySelectorAll("[data-target]")];
const prevButtons = [...document.querySelectorAll("[data-prev]")];
const nextButtons = [...document.querySelectorAll("[data-next]")];
const status = document.querySelector(".control-status");
const railIndex = document.querySelector(".rail-index");
const railTotal = document.querySelector(".rail-total");
const desktopQuery = window.matchMedia("(min-width: 861px)");

let currentSlide = 0;
let wheelLocked = false;

function formatNumber(value) {
  return String(value).padStart(2, "0");
}

function clampSlide(index) {
  return Math.max(0, Math.min(slides.length - 1, index));
}

function updateSlide(index, options = {}) {
  currentSlide = clampSlide(index);
  root.style.setProperty("--slide-index", currentSlide);
  root.style.setProperty("--slide-count", slides.length);
  root.style.setProperty("--slide-progress", `${((currentSlide + 1) / slides.length) * 100}%`);

  status.textContent = `${formatNumber(currentSlide + 1)} / ${formatNumber(slides.length)}`;
  railIndex.textContent = formatNumber(currentSlide + 1);
  railTotal.textContent = formatNumber(slides.length);

  prevButtons.forEach((button) => {
    button.disabled = currentSlide === 0;
  });

  nextButtons.forEach((button) => {
    button.disabled = currentSlide === slides.length - 1;
  });

  navButtons.forEach((button) => {
    const target = Number(button.dataset.target);
    const nextTarget = navButtons
      .map((item) => Number(item.dataset.target))
      .filter((value) => value > target)
      .sort((a, b) => a - b)[0] ?? slides.length;

    button.classList.toggle("is-active", currentSlide >= target && currentSlide < nextTarget);
  });

  if (options.focus && desktopQuery.matches) {
    slides[currentSlide].setAttribute("tabindex", "-1");
    slides[currentSlide].focus({ preventScroll: true });
  }
}

function goToTarget(event) {
  const target = Number(event.currentTarget.dataset.target);
  updateSlide(target, { focus: true });
}

function setupRevealAnimation() {
  const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const targets = [
    ...document.querySelectorAll(
      ".section-kicker, .section-heading-row, .proof-card, .text-panel, .flow-visual, .stage-row, .model-card, .share-table, .coach-card, .value-list article, .option-card, .market-stat, .trend-panel, .source-strip, .channel-card, .gap-graphic, .market-note, .summary-card, .closing-statement, .contact-card, .question-accordion details, .question-note, .mvp-grid > div, .roadmap li, .final-note",
    ),
  ];

  targets.forEach((element, index) => {
    element.dataset.reveal = "";
    element.style.setProperty("--reveal-delay", `${Math.min(index % 5, 4) * 60}ms`);
  });

  body.classList.add("reveal-ready");

  if (reduceMotion || !("IntersectionObserver" in window)) {
    targets.forEach((element) => element.classList.add("is-visible"));
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
      root: desktopQuery.matches ? document.querySelector(".deck") : null,
      threshold: 0.12,
    },
  );

  targets.forEach((element) => observer.observe(element));
}

function setupSpotlightEffects() {
  const cards = document.querySelectorAll(
    ".proof-card, .model-card, .text-panel, .option-card, .market-stat, .channel-card, .summary-card, .mvp-grid > div, .value-list article",
  );

  cards.forEach((card) => {
    card.addEventListener("pointermove", (event) => {
      const rect = card.getBoundingClientRect();
      card.style.setProperty("--pointer-x", (event.clientX - rect.left).toFixed(1));
      card.style.setProperty("--pointer-y", (event.clientY - rect.top).toFixed(1));
    });
  });
}

function handleWheel(event) {
  if (!desktopQuery.matches) return;
  if (Math.abs(event.deltaY) < 18) return;
  event.preventDefault();
  if (wheelLocked) return;

  wheelLocked = true;
  updateSlide(currentSlide + Math.sign(event.deltaY), { focus: true });

  window.setTimeout(() => {
    wheelLocked = false;
  }, 760);
}

function handleKeydown(event) {
  if (!desktopQuery.matches) return;

  if (event.key === "ArrowRight" || event.key === "PageDown" || event.key === " ") {
    event.preventDefault();
    updateSlide(currentSlide + 1, { focus: true });
  }

  if (event.key === "ArrowLeft" || event.key === "PageUp") {
    event.preventDefault();
    updateSlide(currentSlide - 1, { focus: true });
  }

  if (event.key === "Home") {
    event.preventDefault();
    updateSlide(0, { focus: true });
  }

  if (event.key === "End") {
    event.preventDefault();
    updateSlide(slides.length - 1, { focus: true });
  }
}

navButtons.forEach((button) => button.addEventListener("click", goToTarget));
prevButtons.forEach((button) => button.addEventListener("click", () => updateSlide(currentSlide - 1, { focus: true })));
nextButtons.forEach((button) => button.addEventListener("click", () => updateSlide(currentSlide + 1, { focus: true })));
window.addEventListener("wheel", handleWheel, { passive: false });
window.addEventListener("keydown", handleKeydown);
desktopQuery.addEventListener("change", () => updateSlide(currentSlide));

setupRevealAnimation();
setupSpotlightEffects();
updateSlide(0);
