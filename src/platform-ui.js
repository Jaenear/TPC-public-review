import { esc, icon } from "./ui.js";
export const textBody = (value) =>
  esc(value || "")
    .split(/\n\s*\n/)
    .map((part) => `<p>${part.replaceAll("\n", "<br>")}</p>`)
    .join("");
export const date = (value) =>
  value
    ? new Date(value).toLocaleString("ko-KR", {
        timeZone: "Asia/Seoul",
        dateStyle: "medium",
        timeStyle: "short",
      })
    : "";
export const tag = (label, kind = "") =>
  `<span class="p-tag ${kind}">${esc(label)}</span>`;
export const statusLabel = (value) =>
  ({
    draft: "초안",
    review: "검수 요청",
    submitted: "검수 대기",
    approved: "반영 완료",
    rejected: "반려",
    cancelled: "작성 중단",
    published: "발행 중",
    archived: "보관",
    open: "답변 대기",
    answered: "답변 등록",
    closed: "질문 닫힘",
    active: "활성",
    suspended: "정지",
    created: "주문 생성",
    abandoned: "승인 요청 종료",
    pending: "결과 확인 필요",
    confirming: "승인 확인 중",
    paid: "결제 완료",
    refunding: "취소 확인 중",
    refunded: "전액 취소",
    resolved: "처리 완료",
    hidden: "숨김",
    deleted: "작성자 삭제",
  })[value] || value;
export function head(kicker, title, description, action = "") {
  return `<div class="p-page-head"><div><p class="eyebrow">${esc(kicker)}</p><h1>${esc(title)}</h1><p>${esc(description)}</p></div>${action}</div>`;
}
export function empty(title, description, action = "") {
  return `<div class="empty-state"><h2>${esc(title)}</h2><p>${esc(description)}</p>${action}</div>`;
}
export function authGate(ctx, title = "로그인하고 이어가세요.") {
  return {
    html: `<section class="container p-page">${head("TPC CAMP", title, "수강, 질문, 커뮤니티와 신청 내역이 내 계정에 연결됩니다.")}<div class="m-auth-gate">${icon("user", 35)}<h2>캠프에 입장해주세요.</h2><p>계정을 만들고 무료 강의부터 시작할 수 있습니다.</p><button class="btn btn-primary" id="platform-login">로그인 / 회원가입 ${icon("arrow", 17)}</button></div></section>`,
    bind() {
      document.querySelector("#platform-login").onclick = () =>
        ctx.requireAuth();
    },
  };
}
export function bindForm(selector, callback) {
  const form =
    typeof selector === "string" ? document.querySelector(selector) : selector;
  if (!form) return;
  form.onsubmit = async (event) => {
    event.preventDefault();
    if (!form.reportValidity()) return;
    const button = form.querySelector("[type=submit]"),
      error = form.querySelector("[data-error]");
    if (error) error.textContent = "";
    const wasDisabled = button.disabled;
    button.disabled = true;
    try {
      await callback(Object.fromEntries(new FormData(form)), form);
    } catch (e) {
      if (error) error.textContent = e.message;
      else throw e;
    } finally {
      button.disabled = wasDisabled;
    }
  };
}
export function pager(path, params, page, total, size = 20, pageKey = "page") {
  const pages = Math.ceil(total / size);
  if (pages <= 1) return "";
  const link = (n) => {
    const q = new URLSearchParams(params);
    q.set(pageKey, n);
    return `#${path}?${q}`;
  };
  return `<nav class="p-pagination" aria-label="페이지">${page > 1 ? `<a href="${esc(link(page - 1))}" class="btn btn-outline btn-sm">← 이전</a>` : ""}<span>${page} / ${pages}</span>${page < pages ? `<a href="${esc(link(page + 1))}" class="btn btn-outline btn-sm">다음 →</a>` : ""}</nav>`;
}
export function opsNav(ctx, selected) {
  const admin = ctx.user?.role === "admin",
    coach = admin || ctx.user?.roles?.includes("coach"),
    mod = admin || ctx.user?.roles?.includes("moderator");
  const items = [
    ...(coach
      ? [
          ["/studio", "courses", "강의 스튜디오"],
          ["/studio/publications", "publications", "강의 발행 검수"],
          ["/studio/coaching", "coaching", "코칭 상품·일정"],
          ["/studio/reservations", "reservations", "예약 처리 데스크"],
          ["/studio/hand-reviews", "hand-reviews", "핸드 리뷰 작업함"],
          ["/studio/revisions", "revisions", "변경안 검수"],
          ["/qa?desk=1", "qa", "질문 데스크"],
          ["/studio/contracts", "contracts", "파트너 조건"],
          ...(!admin
            ? [["/studio/profiles", "profiles", "내 코치 프로필"]]
            : []),
        ]
      : []),
    ...(mod
      ? [
          ["/manage?tab=reports", "reports", "커뮤니티 신고"],
          ["/manage/moderation/appeals", "moderation", "이의 검토·조치 이력"],
        ]
      : []),
    ...(admin
      ? [
          ["/manage?tab=users", "users", "사용자"],
          ["/manage?tab=purchases", "purchases", "주문·취소"],
          ["/manage/reconciliation", "reconciliation", "결제 대사"],
          ["/manage?tab=policies", "policies", "정책·권리"],
          ["/manage?tab=profiles", "profiles", "코치 프로필·검수"],
          ["/manage?tab=audit", "audit", "운영 이력"],
          ["/admin", "legacy", "코칭·리뷰 접수"],
        ]
      : []),
  ];
  return `<nav class="p-ops-nav" aria-label="관리 메뉴">${items.map(([path, key, label]) => `<a class="${key === selected ? "active" : ""}" href="#${path}">${label}</a>`).join("")}</nav>`;
}
