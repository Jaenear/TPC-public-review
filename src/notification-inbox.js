import { esc, icon } from "./ui.js";
import { head, empty, authGate, date, tag } from "./platform-ui.js";
import { notificationCommentHref } from "./community-model.js";
import {
  NoticeReadIntent,
  inboxQuery,
  inboxHref,
  inboxEndpoint,
  queryKey,
  filterLabel,
  noticeCategories,
  noticeStates,
  validateListing,
} from "./notification-model.js";
import {
  noticeAPI,
  operationAPI,
  noticeNav,
  denyNotice,
  bindNoticeRecovery,
  watchNoticeLeaving,
} from "./notification-ui.js";

export function noticeCard(notice, { group = null, query } = {}) {
  const available = notice.referenceAvailable === true;
  const title = available ? notice.title : "현재 확인할 수 없는 알림입니다";
  const body = available
    ? notice.body
    : "원문 상태나 이용 권한이 바뀌었습니다. 이전 요약과 이동을 표시하지 않습니다.";
  return `<article class="ni-card ${notice.readAt ? "" : "ni-unread"}" data-notice-id="${esc(notice.id)}"><div class="ni-card-icon" aria-hidden="true">${icon(notice.category === "conversation" ? "message" : notice.category === "learning" ? "book" : "bell", 20)}</div><div class="ni-card-body"><div class="ni-card-meta">${tag(noticeCategories[notice.category] || "이전 알림")}<span>${date(notice.createdAt)}</span>${!available ? tag("이용 불가") : ""}</div><h2>${available ? `<a href="${esc(notificationCommentHref(notice))}">${esc(title)}</a>` : esc(title)}</h2><p>${esc(body)}</p>${group ? `<div class="ni-group-meta"><span>현재 조건의 알림 ${group.notificationCount}건 · 미읽음 ${group.unreadCount}건</span><a class="text-link" href="${esc(inboxHref(query, { groupId: group.id, groupBy: "none", page: 1 }))}">묶음의 알림 보기 ↗</a></div>` : ""}</div><div class="ni-card-actions">${notice.readAt ? tag("읽음") : `<button class="btn-text" type="button" data-read-notice="${esc(notice.id)}">${group ? "이 알림 읽음" : "읽음 표시"}</button>`}</div></article>`;
}
export function inboxListHTML(data, q) {
  const rows =
    q.groupBy === "reference"
      ? data.groups.map((g) => noticeCard(g.latest, { group: g, query: q }))
      : data.notifications.map((n) => noticeCard(n));
  const total = q.groupBy === "reference" ? data.groupTotal : data.total;
  const pages = Math.max(1, Math.ceil(total / q.limit));
  const outside = q.page > pages;
  const emptyTitle = outside
    ? "목록이 바뀌어 이 페이지에는 알림이 없습니다."
    : q.state === "unread"
      ? "읽지 않은 알림이 없습니다."
      : "현재 조건의 알림이 없습니다.";
  const pageLabel = outside
    ? `요청한 ${q.page}페이지 · 현재 ${pages}페이지까지`
    : `${q.page} / ${pages}${q.groupBy === "reference" ? " 묶음 페이지" : " 페이지"}`;
  return `<div class="ni-cards">${rows.join("") || empty(emptyTitle, outside ? "첫 페이지에서 현재 조건에 맞는 알림을 확인하세요." : "유형·읽음 조건을 바꾸거나 최신 알림을 확인해보세요.")}</div><nav class="ni-pagination" aria-label="알림 페이지">${q.page > 1 ? `<a class="btn btn-outline btn-sm" href="${esc(inboxHref(q, { page: q.page - 1 }))}">← 이전</a>` : ""}<span>${pageLabel}</span>${q.page < pages ? `<a class="btn btn-outline btn-sm" href="${esc(inboxHref(q, { page: q.page + 1 }))}">다음 →</a>` : ""}${outside ? `<a class="text-link" href="${esc(inboxHref(q, { page: 1 }))}">첫 페이지로</a>` : ""}</nav>`;
}
export function inboxStatsHTML(data) {
  return `<div><span>내 전체 미읽음</span><strong>${data.unreadCount}</strong></div><div><span>현재 조건의 미읽음</span><strong>${data.filteredUnreadCount}</strong></div><div><span>현재 조건의 전체 알림</span><strong>${data.total}</strong></div>`;
}
export async function renderNotificationInbox(params, ctx) {
  if (!ctx.user) return authGate(ctx, "로그인하고 내 알림을 확인하세요.");
  const q = inboxQuery(params);
  const data = validateListing(
    await noticeAPI(ctx, inboxEndpoint(q)),
    q,
    ctx.user.id,
  );
  return {
    html: `<section class="container p-page ni-workspace" data-notification-inbox>${head("YOUR INBOX", "배움의 다음 소식.", "학습과 대화, 주문과 운영의 소식을 내 기준으로 확인하세요.")}${noticeNav()}<div class="ni-stats" data-notice-stats>${inboxStatsHTML(data)}</div><form id="inbox-filter" class="ni-filter"><label>알림 유형<select name="category" aria-label="알림 유형"><option value="">모든 유형</option>${Object.entries(
      noticeCategories,
    )
      .map(
        ([k, label]) =>
          `<option value="${k}" ${q.category === k ? "selected" : ""}>${esc(label)}</option>`,
      )
      .join(
        "",
      )}</select></label><label>읽음 상태<select name="state" aria-label="읽음 상태">${Object.entries(
      noticeStates,
    )
      .map(
        ([k, label]) =>
          `<option value="${k}" ${q.state === k ? "selected" : ""}>${label}</option>`,
      )
      .join(
        "",
      )}</select></label><label>보기<select name="groupBy" aria-label="알림 보기"><option value="none" ${q.groupBy === "none" ? "selected" : ""}>알림별 보기</option><option value="reference" ${q.groupBy === "reference" ? "selected" : ""}>대화·항목별 묶음</option></select></label><label>페이지 크기<select name="limit" aria-label="페이지 크기">${[20, 50, 100].map((n) => `<option value="${n}" ${q.limit === n ? "selected" : ""}>${n}개</option>`).join("")}</select></label><button class="btn btn-outline btn-sm" type="submit">조건 적용</button></form>${q.groupId ? `<p class="ni-scope-note">선택한 묶음의 알림을 보고 있습니다. <a class="text-link" href="${esc(inboxHref(q, { groupId: null, groupBy: "reference", page: 1 }))}">묶음 목록으로 ↗</a></p>` : ""}<form id="notification-read" data-session-draft data-recovery-private data-recovery-version="${esc(queryKey(q))}"><div class="ni-toolbar"><div><strong data-notice-filter-label>${esc(filterLabel(q))}</strong><p>일괄 읽음은 현재 조건의 모든 페이지에서, 이 목록을 확인한 시점까지 받은 알림에 적용됩니다.</p></div><div class="ni-actions"><button class="btn btn-outline btn-sm" id="notice-refresh" type="button">최신 알림 확인</button><button class="btn btn-primary btn-sm" id="read-all" type="submit" ${data.filteredUnreadCount ? "" : "disabled"}>조건에 맞는 알림 읽음</button><button class="btn btn-outline btn-sm" type="button" data-notice-retry hidden>같은 읽음 결과 다시 확인</button></div></div><p class="ni-feedback" role="status" tabindex="-1" data-notice-feedback>현재 조건에서 미읽음 ${data.filteredUnreadCount}건을 확인했습니다.</p><section class="p-panel ni-confirm" data-notice-preview hidden></section></form><div data-notice-list>${inboxListHTML(data, q)}</div></section>`,
    bind() {
      bindNotificationInbox(
        document.querySelector("[data-notification-inbox]"),
        data,
        q,
        ctx,
      );
    },
  };
}

export function bindNotificationInbox(root, initial, q, ctx) {
  const route = location.hash,
    form = root.querySelector("#notification-read");
  const intent = new NoticeReadIntent({ ownerId: ctx.user.id, query: q });
  let data = initial,
    working = false;
  const active = () =>
    root.isConnected &&
    root.dataset.unavailable !== "true" &&
    location.hash === route;
  const node = (s) => root.querySelector(s);
  const status = (message, focus = false) => {
    if (active()) {
      const n = node("[data-notice-feedback]");
      n.textContent = message;
      if (focus) n.focus();
    }
  };
  const pending = () => !!intent.pending;
  const cleanup = watchNoticeLeaving(active, pending, root);
  function sync() {
    if (!active()) return;
    const frozen = working || pending();
    root
      .querySelectorAll(
        "#inbox-filter input,#inbox-filter select,#inbox-filter button,[data-read-notice]",
      )
      .forEach((el) => (el.disabled = frozen));
    node("#read-all").disabled = frozen || !data.filteredUnreadCount;
    node("#notice-refresh").disabled =
      working || (pending() && intent.state !== "conflict");
    node("[data-notice-retry]").hidden = intent.state !== "unknown";
    node("[data-notice-retry]").disabled = working;
    root
      .querySelectorAll("[data-notice-confirm],[data-notice-cancel]")
      .forEach((el) => (el.disabled = working || intent.state === "unknown"));
    root
      .querySelectorAll("a")
      .forEach((a) => a.setAttribute("aria-disabled", String(frozen)));
  }
  function failed(error) {
    if (!active()) return;
    if (denyNotice(root, error)) cleanup();
    else status(error.message || "알림을 다시 확인해주세요.");
  }
  function bindReadButtons() {
    root.querySelectorAll("[data-read-notice]").forEach(
      (b) =>
        (b.onclick = () => {
          if (!active() || working || pending()) return;
          try {
            const n = data.notifications.find(
              (n) => n.id === b.dataset.readNotice,
            );
            if (!n) throw new Error("현재 목록의 알림을 다시 확인해주세요.");
            intent.prepare(data, n);
            return send(false);
          } catch (error) {
            failed(error);
          }
        }),
    );
  }
  async function load() {
    const fresh = await noticeAPI(ctx, inboxEndpoint(q));
    if (!active()) return false;
    data = validateListing(fresh, q, ctx.user.id);
    node("[data-notice-stats]").innerHTML = inboxStatsHTML(data);
    node("[data-notice-list]").innerHTML = inboxListHTML(data, q);
    bindReadButtons();
    return true;
  }
  async function saved(result) {
    const receipt = intent.accept(result);
    node("[data-notice-preview]").hidden = true;
    node("[data-notice-list]").innerHTML =
      '<p class="p-notice">현재 권한의 알림 목록을 확인하고 있습니다…</p>';
    const message = `읽음 저장 결과를 확인했습니다. 당시 범위에서 ${receipt.changedCount}건을 읽음 표시했습니다.`;
    status(message, true);
    try {
      if (await load())
        status(
          `${message} 현재 조건의 미읽음은 ${data.filteredUnreadCount}건입니다.`,
          true,
        );
    } catch (error) {
      if (active() && !denyNotice(root, error))
        status(
          `${message} 목록 확인은 실패했습니다. 최신 알림을 다시 확인해주세요.`,
          true,
        );
      else cleanup();
    }
  }
  async function reconcile() {
    try {
      const result = await noticeAPI(
        ctx,
        operationAPI(intent.pending.requestKey),
      );
      if (!active()) return true;
      await saved(result);
      return true;
    } catch (error) {
      if (error.status === 404) return false;
      throw error;
    }
  }
  async function send(retry) {
    if (!active() || working) return;
    working = true;
    sync();
    let sent = false;
    try {
      if (retry && (await reconcile())) return;
      if (!active()) return;
      const body = intent.sending();
      sent = true;
      status("읽음 저장 결과를 확인하고 있습니다…");
      sync();
      const result = await noticeAPI(ctx, "/api/notifications", {
        method: "PATCH",
        body,
      });
      if (active()) await saved(result);
    } catch (error) {
      if (!active()) return;
      if (error.status === 409 && !error.sessionChanged) {
        try {
          if (await reconcile()) return;
          intent.conflict();
          node("[data-notice-preview]").hidden = true;
          status(
            "이 읽음 범위를 더 이상 사용할 수 없습니다. 최신 알림을 확인한 뒤 적용 범위를 다시 검토해주세요.",
            true,
          );
        } catch (e) {
          intent.uncertain();
          failed(e);
        }
      } else {
        if (sent || retry) intent.uncertain();
        if (!denyNotice(root, error))
          status(
            "읽음 저장 결과를 확인하지 못했습니다. 같은 요청으로 다시 확인해주세요.",
            true,
          );
        else cleanup();
      }
    } finally {
      working = false;
      sync();
      if (active() && intent.state === "unknown")
        node("[data-notice-retry]").focus();
    }
  }
  form.onsubmit = (event) => {
    event.preventDefault();
    if (!active() || working || pending()) return;
    try {
      intent.prepare(data);
      const box = node("[data-notice-preview]");
      box.hidden = false;
      box.innerHTML = `<h2>이 범위를 읽음 표시할까요?</h2><p><strong>${esc(intent.meta.label)}</strong></p><p>확인 당시의 미읽음 ${intent.meta.count}건까지, 현재 조건에 맞는 모든 페이지에 적용합니다. 이후 받은 알림은 포함하지 않습니다. 다른 곳에서 이미 읽은 알림은 중복 처리하지 않습니다.</p><div class="ni-actions"><button type="button" class="btn btn-primary btn-sm" data-notice-confirm>확인한 범위 읽음 표시</button><button type="button" class="btn btn-outline btn-sm" data-notice-cancel>취소</button></div>`;
      box.querySelector("[data-notice-confirm]").onclick = () => send(false);
      box.querySelector("[data-notice-cancel]").onclick = () => {
        intent.reset();
        box.hidden = true;
        sync();
        node("#read-all").focus();
      };
      sync();
      box.querySelector("[data-notice-confirm]").focus();
    } catch (error) {
      failed(error);
    }
  };
  node("#notice-refresh").onclick = async () => {
    if (!active() || working || (pending() && intent.state !== "conflict"))
      return;
    working = true;
    sync();
    try {
      if (await load()) {
        intent.reset();
        node("[data-notice-preview]").hidden = true;
        status(
          "최신 알림을 확인했습니다. 일괄 읽음은 적용 범위를 새로 검토해주세요.",
          true,
        );
      }
    } catch (error) {
      failed(error);
    } finally {
      working = false;
      sync();
    }
  };
  node("[data-notice-retry]").onclick = () => send(true);
  root.querySelector("#inbox-filter").onsubmit = (event) => {
    event.preventDefault();
    if (!active() || working || pending()) return;
    const f = new FormData(event.target);
    const next = inboxQuery(
      new URLSearchParams({
        category: f.get("category"),
        state: f.get("state"),
        groupBy: f.get("groupBy"),
        limit: f.get("limit"),
      }),
    );
    ctx.navigate(inboxHref(next).slice(1));
  };
  root.addEventListener("click", (event) => {
    if (event.target.closest("a") && (working || pending())) {
      event.preventDefault();
      status(
        "현재 읽음 요청을 취소하거나 저장 결과를 먼저 확인해주세요.",
        true,
      );
    }
  });
  bindNoticeRecovery(form, intent, (restored) => {
    if (restored) {
      status(
        "이전에 요청한 읽음 결과를 같은 요청으로 직접 확인해주세요.",
        true,
      );
      sync();
    }
  });
  bindReadButtons();
  sync();
  return { intent, active };
}
