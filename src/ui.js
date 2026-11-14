/** Small shared UI primitives. No build step or runtime dependencies. */
import { retryAfterSeconds } from "./api-retry.js";
export const esc = (value = "") =>
  String(value ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );

const paths = {
  bell: '<path d="M18 8a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9M9 21h6"/>',

  target:
    '<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="5"/><circle cx="12" cy="12" r="1"/>',
  calculator:
    '<rect x="5" y="2" width="14" height="20" rx="2"/><path d="M8 6h8M8 10h2m4 0h2M8 14h2m4 0h2M8 18h2m4 0h2"/>',
  chart: '<path d="M3 3v18h18M7 14l4-4 4 2 6-7"/>',

  arrow: '<path d="M5 12h14m-6-6 6 6-6 6"/>',
  external: '<path d="M7 17 17 7M7 7h10v10"/>',
  play: '<path d="m9 5 11 7-11 7z"/>',
  check: '<path d="m5 12 4 4L19 6"/>',
  clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
  book: '<path d="M12 6c-3-3-7-3-10-1v15c3-2 7-2 10 0 3-2 7-2 10 0V5c-3-2-7-2-10 1zm0 0v14"/>',
  search: '<circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 5 5"/>',
  close: '<path d="m6 6 12 12M6 18 18 6"/>',
  menu: '<path d="M4 6h16M4 12h16M4 18h16"/>',
  user: '<circle cx="12" cy="8" r="4"/><path d="M4 21v-2a8 8 0 0 1 16 0v2"/>',
  users:
    '<circle cx="9" cy="8" r="3"/><path d="M3 21v-2a6 6 0 0 1 12 0v2M16 5a3 3 0 0 1 0 6m2 4a5 5 0 0 1 3 4v2"/>',
  lock: '<rect x="5" y="10" width="14" height="11" rx="2"/><path d="M8 10V7a4 4 0 0 1 8 0v3m-4 5v2"/>',
  message:
    '<path d="M21 11.5a8.5 8.5 0 0 1-8.5 8.5H3l1.8-5A8.5 8.5 0 1 1 21 11.5Z"/>',
  layers: '<path d="m12 3 10 5-10 5L2 8zm-9 9 9 5 9-5M3 17l9 5 9-5"/>',
  calendar:
    '<rect x="3" y="5" width="18" height="16" rx="2"/><path d="M7 3v4m10-4v4M3 11h18"/>',
  chevron: '<path d="m9 5 7 7-7 7"/>',
  download: '<path d="M12 3v12m-5-5 5 5 5-5M4 16v5h16v-5"/>',
  info: '<circle cx="12" cy="12" r="9"/><path d="M12 11v6m0-10v1"/>',
};
export const icon = (name, size = 20) =>
  `<svg width="${Number(size) || 20}" height="${Number(size) || 20}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${paths[name] || paths.arrow}</svg>`;
export const money = (n) =>
  n ? `₩${Number(n).toLocaleString("ko-KR")}` : "무료";

let sessionIdentity = () => null;
let sessionFailure = () => {};
export function observeApiSession(identity, failure) {
  sessionIdentity = identity || (() => null);
  sessionFailure = failure || (() => {});
}
export async function api(path, options = {}) {
  const context = sessionIdentity();
  const sessionRequest =
    path === "/api/session" || path.startsWith("/api/auth/");
  const { rawBody, ownerId, responseType, errorBody, ...requestOptions } =
    options;
  if (ownerId !== undefined && ownerId !== context?.ownerId) {
    const error = new Error(
      "로그인 계정이 변경됐습니다. 현재 계정에서 다시 확인해주세요.",
    );
    error.status = 409;
    error.sessionChanged = true;
    throw error;
  }
  const headers = { ...options.headers };
  if (context?.ownerId && !sessionRequest)
    headers["X-TPC-User"] = context.ownerId;
  if (options.body !== undefined) headers["Content-Type"] = "application/json";
  let response;
  try {
    response = await fetch(path, {
      ...requestOptions,
      headers,
      credentials: "same-origin",
      body:
        rawBody !== undefined
          ? rawBody
          : options.body === undefined
            ? undefined
            : JSON.stringify(options.body),
    });
  } catch {
    throw new Error(
      "서버에 연결하지 못했습니다. 연결 상태를 확인하고 다시 시도해주세요.",
    );
  }
  let result;
  try {
    result =
      response.ok && responseType === "blob"
        ? await response.blob()
        : await response.json();
  } catch {
    throw new Error("서버 응답을 읽을 수 없습니다. 잠시 후 다시 시도해주세요.");
  }
  const current = sessionIdentity();
  if (
    !sessionRequest &&
    context?.ownerId &&
    (context.ownerId !== current?.ownerId || context.epoch !== current.epoch)
  ) {
    const error = new Error(
      "로그인 상태가 변경되었습니다. 현재 화면에서 결과를 다시 확인해주세요.",
    );
    error.status = 409;
    error.sessionChanged = true;
    throw error;
  }
  if (!response.ok) {
    const error = new Error(result.error || "요청을 처리하지 못했습니다.");
    error.status = response.status;
    if (response.status === 429) error.retryAfterSeconds = retryAfterSeconds(response.headers.get("Retry-After"));
    if (errorBody === true) error.details = result;
    if (response.status === 401 && !sessionRequest)
      sessionFailure({ status: response.status, path, context });
    throw error;
  }
  return result;
}

export function toast(message, type = "success") {
  const region = document.querySelector("#toast-region");
  const node = document.createElement("div");
  node.className = `toast ${type === "error" ? "toast-error" : ""}`;
  node.textContent = message;
  region.append(node);
  setTimeout(() => node.remove(), 5000);
}

let previousFocus;
export function modal(html, onMount) {
  const dialog = document.querySelector("#app-dialog");
  if (!dialog.open) previousFocus = document.activeElement;
  dialog.setAttribute("aria-label", "TPC 안내");
  dialog.dataset.busy = "false";
  dialog.dataset.sessionAuth = "false";
  dialog.innerHTML = `<button class="dialog-close" aria-label="닫기" type="button">${icon("close")}</button>${html}`;
  dialog.querySelector(".dialog-close").onclick = () => {
    if (dialog.dataset.busy !== "true") dialog.close();
  };
  dialog.oncancel = (event) => {
    if (dialog.dataset.busy === "true") event.preventDefault();
  };
  dialog.onclick = (event) => {
    if (
      dialog.dataset.busy !== "true" &&
      event.target === dialog &&
      (event.offsetX < 0 ||
        event.offsetX > dialog.clientWidth ||
        event.offsetY < 0 ||
        event.offsetY > dialog.clientHeight)
    )
      dialog.close();
  };
  dialog.onclose = () => {
    document.body.classList.remove("dialog-open");
    if (previousFocus?.isConnected) previousFocus.focus();
  };
  if (!dialog.open) dialog.showModal();
  document.body.classList.add("dialog-open");
  onMount?.(dialog);
  return dialog;
}

/** Code-native artwork distinguishes sample curriculum from unavailable videos. */
export function courseArt(course, small = false) {
  const art = course.art;
  let inside;
  if (art === "range") {
    const ranks = ["A", "K", "Q", "J", "T", "9", "8"];
    inside = `<div class="art-grid">${ranks.flatMap((r, i) => ranks.map((s, j) => `<span class="${i <= j ? "range-open" : i < j + 3 ? "range-mixed" : ""}">${i === j ? r + s : i < j ? r + s + "s" : s + r + "o"}</span>`)).join("")}</div><div class="art-range-label">POSITION IS POWER<span>BTN / PREFLOP</span></div>`;
  } else if (art === "tournament") {
    inside = `<div class="art-tournament-scene"><span class="tourney-orbit orbit-one"></span><span class="tourney-orbit orbit-two"></span><div class="chip-stack"><i></i><i></i><i></i><i></i><i></i></div><span class="art-tourney-title">THE NEXT<br><b>DECISION.</b></span><span class="art-tourney-bb">15<span>BB</span></span></div>`;
  } else if (art === "mental") {
    inside = `<div class="art-mental-scene"><span class="mental-ring r1"></span><span class="mental-ring r2"></span><span class="mental-ring r3"></span><span class="mental-center">FOCUS<br><b>OVER<br>NOISE.</b></span><span class="mental-coordinate">MINDSET / ROUTINE</span></div>`;
  } else {
    inside = `<div class="art-table-scene"><div class="mini-felt"></div><div class="art-playing-card">A<span>♠</span></div><div class="art-playing-card second">K<span>♣</span></div><span class="table-annotation">READ THE SPOT.<br><b>NOT JUST THE CARDS.</b></span><span class="table-position">BTN</span></div>`;
  }
  return `<div class="course-art art-${esc(art)} accent-${esc(course.accent)} ${small ? "art-small" : ""}" aria-hidden="true"><div class="art-topline"><span>TPC / STUDY SERIES</span><span>♢ ♢</span></div>${inside}<span class="art-bottomline">TWO PAIR POKER CAMP</span></div>`;
}
