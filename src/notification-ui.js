import { head, empty } from "./platform-ui.js";
export const noticeAPI = (ctx, path, options = {}) =>
  ctx.api(path, { ...options, ownerId: ctx.user.id });
export const operationAPI = (key) => `/api/me/notification-operations/${key}`;
export const noticeNav = (settings = false) =>
  `<nav class="ni-nav" aria-label="알림 메뉴"><a href="#/inbox" ${settings ? "" : 'aria-current="page"'}>알림함</a><a href="#/inbox/preferences" ${settings ? 'aria-current="page"' : ""}>수신 설정</a></nav>`;
export function denyNotice(root, error) {
  if (![401, 403, 404].includes(error.status) && !error.sessionChanged)
    return false;
  root.dataset.unavailable = "true";
  const title =
    error.status === 401
      ? "다시 로그인하고 알림을 확인해주세요."
      : "현재 알림을 표시할 수 없습니다.";
  root.innerHTML =
    head(
      "YOUR INBOX",
      title,
      error.status === 401
        ? "같은 계정으로 로그인한 뒤 현재 설정과 보관한 요청을 다시 확인합니다."
        : "현재 계정과 이용 권한을 다시 확인해주세요.",
    ) +
    empty(
      "이전 화면을 정리했습니다.",
      "현재 계정의 알림함에서 다시 확인할 수 있습니다.",
      '<a class="btn btn-outline" href="#/inbox">알림함 다시 확인</a>',
    );
  document.title = `${title} | TPC`;
  if (error.status !== 401) {
    root.setAttribute("tabindex", "-1");
    root.focus();
  }
  return true;
}
export function bindNoticeRecovery(form, intent, restored) {
  form.addEventListener("tpc:capture-draft", (event) => {
    const packet = intent.export();
    if (packet)
      event.detail.entries.push({
        key: "notificationIntent",
        type: "text",
        custom: true,
        value: JSON.stringify(packet),
        dirty: true,
        label: "알림 설정·저장 결과",
        displayValue: "같은 계정에서 내용을 확인하고 직접 다시 진행해주세요.",
      });
  });
  form.addEventListener("tpc:draft-restored", (event) => {
    let accepted = false;
    for (const entry of event.detail.entries.filter(
      (e) => e.key === "notificationIntent",
    ))
      try {
        accepted = intent.restore(JSON.parse(entry.value));
      } catch {}
    restored(accepted);
  });
}
export function watchNoticeLeaving(active, dirty, root) {
  const leaving = (event) => {
    if (active() && dirty()) {
      event.preventDefault();
      event.returnValue = "";
    }
  };
  let observer;
  const dispose = () => {
    window.removeEventListener("beforeunload", leaving);
    window.removeEventListener("hashchange", cleanup);
    observer?.disconnect();
  };
  const cleanup = () => {
    if (!active()) dispose();
  };
  window.addEventListener("beforeunload", leaving);
  window.addEventListener("hashchange", cleanup);
  if (root && typeof MutationObserver !== "undefined") {
    observer = new MutationObserver(cleanup);
    observer.observe(document.body, { childList: true, subtree: true });
  }
  return dispose;
}
