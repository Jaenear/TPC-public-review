import { esc } from "./ui.js";
import { head, empty, authGate, textBody, date, tag } from "./platform-ui.js";
import {
  CommentIntent,
  commentAPI,
  commentPath,
  postPath,
} from "./community-model.js";

const back = (pid, cid) =>
  `<a class="back-link" href="${postPath(pid, cid)}">← 대화로 돌아가기</a>`;
const ownAPI = (ctx, path, options = {}) =>
  ctx.api(path, { ...options, ownerId: ctx.user.id });
const unavailable = () =>
  empty(
    "현재 이 내용을 표시할 수 없습니다.",
    "댓글이나 게시글의 상태·이용 권한을 확인해주세요.",
  );

export function redactCommunityView(root, error) {
  if (!root?.isConnected) return;
  const scope =
    root.closest(
      "[data-community-workspace],[data-community-post],[data-community-block]",
    ) || root;
  scope.dataset.unavailable = "true";
  const expired = error.status === 401;
  const title = expired
    ? "다시 로그인하고 대화를 확인해주세요."
    : "현재 이 대화를 표시할 수 없습니다.";
  scope.innerHTML = empty(
    title,
    expired
      ? "같은 계정으로 로그인한 뒤 현재 권한과 작성 내용을 다시 확인합니다."
      : "게시글·댓글 상태나 계정이 변경되었습니다. 현재 화면에서 다시 확인해주세요.",
    '<a class="btn btn-outline" href="#/community">커뮤니티로 돌아가기</a>',
  );
  document.title = `${title} | TPC`;
  if (!expired) {
    scope.setAttribute("tabindex", "-1");
    scope.focus();
  }
}

export function commentFormHTML(intent, { available = true } = {}) {
  const edit = intent.kind === "edit",
    reply = intent.kind === "reply";
  const formId = `conversation-${intent.kind}-${intent.postId}-${intent.targetId || "root"}`;
  return `<div class="cm-editor" data-community-editor><form id="${esc(formId)}" data-session-draft data-recovery-private data-recovery-version="${esc(`${intent.kind}:${intent.postId}:${intent.targetId || "root"}`)}"><label class="field">${edit ? "수정할 댓글" : reply ? "답글 작성" : "댓글 작성"}<textarea name="body" aria-label="${edit ? "수정할 댓글" : reply ? "답글 작성" : "댓글 작성"}" rows="${edit ? 9 : 4}" required minlength="2" maxlength="5000" placeholder="학습의 맥락을 더하는 이야기를 남겨주세요.">${esc(intent.draft.body)}</textarea></label>${edit ? '<label class="field">수정 이유<textarea name="reason" aria-label="수정 이유" rows="2" required minlength="3" maxlength="2000" placeholder="어떤 내용을 보완했나요?"></textarea></label><p class="form-note">수정본과 이유는 본인의 수정 이력에 남습니다.</p>' : ""}<p class="cm-feedback" data-comment-feedback role="status">${available ? "등록 전 최신 대화 상태를 다시 확인합니다." : "현재 이 대화에 저장할 수 없습니다. 작성한 내용은 이 화면에서 확인할 수 있습니다."}</p><div class="cm-form-actions"><button class="btn btn-primary btn-sm" type="submit" data-comment-preview ${available ? "" : "disabled"}>${edit ? "수정 내용 확인" : reply ? "답글 확인" : "댓글 확인"}</button><button class="btn btn-outline btn-sm" type="button" data-comment-refresh>최신 상태 확인</button><button class="btn btn-outline btn-sm" type="button" data-comment-retry hidden>같은 저장 결과 다시 확인</button><button class="btn-text" type="button" data-comment-clear>작성 취소</button></div><section class="cm-preview p-panel" data-comment-preview-box hidden></section><section class="cm-conflict p-panel" data-comment-conflict hidden></section></form></div>`;
}

export function bindCommentForm(
  root,
  intent,
  { ctx, load, permitted, current = null, showCurrent = () => {}, onSaved },
) {
  const form = root.querySelector("form"),
    route = location.hash;
  let fresh = current,
    working = false;
  const active = () =>
    root.isConnected &&
    !root.hidden &&
    root.dataset.unavailable !== "true" &&
    location.hash === route;
  const node = (selector) => root.querySelector(selector);
  const feedback = (message) => {
    if (active()) node("[data-comment-feedback]").textContent = message;
  };
  const read = () => {
    intent.draft = {
      body: form.elements.body.value,
      reason: form.elements.reason?.value || "",
    };
  };
  const allowed = () => !!fresh && permitted(fresh);
  function sync() {
    if (!active()) return;
    const frozen =
      working || ["saving", "unknown", "saved"].includes(intent.state);
    form
      .querySelectorAll("[name=body],[name=reason]")
      .forEach((n) => (n.disabled = frozen));
    node("[data-comment-preview]").disabled =
      frozen || intent.state !== "editing" || !allowed();
    node("[data-comment-refresh]").disabled = frozen;
    node("[data-comment-clear]").disabled = frozen;
    node("[data-comment-retry]").hidden = intent.state !== "unknown";
    node("[data-comment-retry]").disabled = working;
    root
      .querySelectorAll(
        "[data-comment-confirm],[data-comment-change],[data-comment-rebase],[data-comment-use-latest]",
      )
      .forEach(
        (n) =>
          (n.disabled =
            frozen ||
            (n.hasAttribute("data-comment-confirm") && !allowed()) ||
            ((n.hasAttribute("data-comment-rebase") ||
              n.hasAttribute("data-comment-use-latest")) &&
              !allowed())),
      );
  }
  function deny(error) {
    if (!active()) return;
    if (error.status === 401) {
      redactCommunityView(root, error);
      cleanup();
      return;
    }
    if (error.sessionChanged || [403, 404].includes(error.status)) {
      root.dataset.unavailable = "true";
      intent.pending = null;
      intent.draft = { body: "", reason: "" };
      redactCommunityView(root, error);
      cleanup();
      return;
    }
    feedback(error.message || "상태를 확인하지 못했습니다. 다시 확인해주세요.");
  }
  async function latest() {
    const result = await load();
    if (!active()) {
      const e = new Error("화면이 변경되었습니다.");
      e.obsolete = true;
      throw e;
    }
    fresh = result;
    showCurrent(result);
    return result;
  }
  function conflict() {
    const t = intent.latest || intent.base;
    const box = node("[data-comment-conflict]");
    box.hidden = false;
    node("[data-comment-preview-box]").hidden = true;
    box.innerHTML = `<h3>${allowed() ? "대화의 내용이 바뀌었습니다." : "지금은 저장할 수 없는 대화입니다."}</h3><p>작성한 초안은 유지했습니다. 최신 내용과 비교한 뒤 직접 다시 검토해주세요.</p><div class="cm-compare"><section><h4>${intent.kind === "reply" ? "현재 원댓글" : "현재 댓글"}</h4><div class="p-user-body">${textBody(t?.body || "새 댓글을 작성할 게시글의 현재 상태를 확인해주세요.")}</div></section><section><h4>내 초안</h4><div class="p-user-body">${textBody(intent.draft.body)}</div></section></div><div class="cm-form-actions"><button type="button" class="btn btn-primary btn-sm" data-comment-rebase ${allowed() ? "" : "disabled"}>${intent.kind === "reply" ? "새 원댓글을 확인하고 내 답글 검토" : "내 초안을 유지하고 다시 검토"}</button>${intent.kind === "edit" ? '<button type="button" class="btn btn-outline btn-sm" data-comment-use-latest>최신 본문 사용</button>' : ""}</div>`;
    box.querySelector("[data-comment-rebase]").onclick = () => {
      if (!allowed()) return;
      intent.rebase();
      box.hidden = true;
      sync();
      form.elements.body.focus();
      feedback("최신 내용을 기준으로 초안을 다시 확인하고 저장해주세요.");
    };
    box
      .querySelector("[data-comment-use-latest]")
      ?.addEventListener("click", () => {
        if (!allowed()) return;
        intent.rebase(true);
        form.elements.body.value = intent.draft.body;
        box.hidden = true;
        sync();
        form.elements.body.focus();
      });
    box.setAttribute("tabindex", "-1");
    box.focus();
    sync();
  }
  function preview() {
    const p = intent.prepare(),
      box = node("[data-comment-preview-box]");
    box.hidden = false;
    box.innerHTML = `<h3>${intent.kind === "edit" ? "수정 내용을 확인해주세요." : "이 내용으로 대화에 남길까요?"}</h3><div class="p-user-body">${textBody(p.body)}</div>${p.reason ? `<p><strong>수정 이유</strong> ${esc(p.reason)}</p>` : ""}<div class="cm-form-actions"><button class="btn btn-primary btn-sm" type="button" data-comment-confirm>${intent.kind === "edit" ? "수정 저장" : intent.kind === "reply" ? "답글 등록" : "댓글 등록"}</button><button class="btn btn-outline btn-sm" type="button" data-comment-change>다시 작성</button></div>`;
    box.querySelector("[data-comment-confirm]").onclick = () => send(false);
    box.querySelector("[data-comment-change]").onclick = () => {
      intent.change();
      box.hidden = true;
      sync();
      form.elements.body.focus();
    };
    box.querySelector("[data-comment-confirm]").focus();
    sync();
  }
  function saved(result) {
    const receipt = result.receipt;
    if (result.currentAvailable === false) intent.acceptUnavailable(result);
    else intent.accept(result);
    node("[data-comment-preview-box]").hidden = true;
    feedback(
      result.currentAvailable === false
        ? "저장 영수증을 확인했습니다. 현재 댓글은 표시할 수 없습니다."
        : "댓글을 저장했습니다.",
    );
    onSaved?.(receipt, result);
    sync();
  }
  async function reconcile() {
    try {
      const result = await ownAPI(
        ctx,
        `/api/me/community-comment-operations/${intent.pending.requestKey}`,
      );
      if (!active()) return true;
      saved(result);
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
      if (!retry) {
        const result = await latest(),
          target = result.target;
        if (
          !permitted(result) ||
          (intent.kind !== "create" &&
            target.contentVersion !== intent.base.contentVersion)
        ) {
          intent.conflict(target);
          conflict();
          return;
        }
      }
      if (!active()) return;
      const body = intent.sending();
      sent = true;
      sync();
      feedback("저장 결과를 확인하고 있습니다…");
      const result = await ownAPI(
        ctx,
        intent.kind === "edit"
          ? commentAPI(intent.targetId)
          : `/api/community/posts/${intent.postId}/comments`,
        { method: intent.kind === "edit" ? "PATCH" : "POST", body },
      );
      if (active()) saved(result);
    } catch (error) {
      if (!active() || error.obsolete) return;
      if (error.status === 409 && !error.sessionChanged) {
        try {
          if (retry && (await reconcile())) return;
          const result = await latest();
          intent.conflict(result.target);
          conflict();
        } catch (failed) {
          if (!failed.obsolete) {
            intent.uncertain();
            deny(failed);
          }
        }
      } else if ([400, 413, 429].includes(error.status)) {
        if (retry) intent.uncertain();
        else intent.rejected();
        deny(error);
      } else if (
        [401, 403, 404].includes(error.status) ||
        error.sessionChanged
      ) {
        intent.uncertain();
        deny(error);
      } else {
        if (sent || retry) intent.uncertain();
        feedback(
          sent || retry
            ? "저장 결과를 확인하지 못했습니다. 같은 요청으로 다시 확인해주세요."
            : error.message,
        );
      }
    } finally {
      working = false;
      sync();
      if (active() && intent.state === "unknown")
        node("[data-comment-retry]").focus();
    }
  }
  form.addEventListener("input", () => {
    read();
    intent.change();
    if (intent.state !== "conflict")
      node("[data-comment-preview-box]").hidden = true;
    sync();
  });
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    if (!active() || working) return;
    try {
      read();
      preview();
    } catch (error) {
      feedback(error.message);
    }
  });
  node("[data-comment-retry]").onclick = () => send(true);
  node("[data-comment-refresh]").onclick = async () => {
    if (working || intent.state === "unknown") return;
    working = true;
    sync();
    try {
      read();
      const result = await latest();
      if (
        intent.state === "conflict" ||
        (intent.kind !== "create" &&
          result.target.contentVersion !== intent.base.contentVersion) ||
        !permitted(result)
      ) {
        intent.conflict(result.target);
        conflict();
      } else
        feedback(
          "최신 대화 상태를 확인했습니다. 작성 내용을 확인한 뒤 직접 저장해주세요.",
        );
    } catch (error) {
      if (!error.obsolete) deny(error);
    } finally {
      working = false;
      sync();
    }
  };
  node("[data-comment-clear]").onclick = () => {
    if (working || intent.state === "unknown") return;
    intent.rejected();
    intent.draft = {
      body: intent.kind === "edit" ? intent.base.body : "",
      reason: "",
    };
    form.elements.body.value = intent.draft.body;
    if (form.elements.reason) form.elements.reason.value = "";
    node("[data-comment-preview-box]").hidden = true;
    node("[data-comment-conflict]").hidden = true;
    feedback("작성을 취소했습니다.");
    sync();
    form.elements.body.focus();
  };
  form.addEventListener("tpc:capture-draft", (event) => {
    for (const [key, value] of Object.entries(intent.draft)) {
      const old = event.detail.entries.find((e) => e.key === key);
      if (old) {
        old.value = value;
        old.dirty = intent.dirty();
      } else if (value)
        event.detail.entries.push({
          key,
          type: "textarea",
          value,
          dirty: true,
          label: key === "body" ? "댓글 초안" : "수정 이유",
        });
    }
    event.detail.entries.push({
      key: "communityDraftVersion",
      type: "text",
      custom: true,
      value: String(intent.base?.contentVersion || 0),
      dirty: intent.dirty(),
      label: "작성 기준",
      displayValue: "최신 대화를 다시 확인해주세요.",
    });
    const packet = intent.export();
    if (packet)
      event.detail.entries.push({
        key: "communityIntent",
        type: "text",
        custom: true,
        value: JSON.stringify(packet),
        dirty: true,
        label: "저장 결과 확인",
        displayValue: "로그인 후 같은 저장 요청의 결과를 직접 확인해주세요.",
      });
  });
  form.addEventListener("tpc:draft-restored", (event) => {
    read();
    let restored = false;
    for (const entry of event.detail.entries.filter(
      (e) => e.custom && e.key === "communityIntent",
    ))
      try {
        restored = intent.restore(JSON.parse(entry.value));
      } catch {}
    const oldVersion = Number(
      event.detail.entries.find((e) => e.key === "communityDraftVersion")
        ?.value,
    );
    if (
      !restored &&
      intent.kind !== "create" &&
      oldVersion &&
      oldVersion !== intent.base.contentVersion
    ) {
      intent.conflict(fresh.target);
      conflict();
    } else
      feedback(
        restored
          ? "이전 저장 결과를 같은 요청으로 직접 확인해주세요."
          : "초안을 복원했습니다. 내용을 확인한 뒤 직접 저장해주세요.",
      );
    sync();
  });
  const leaving = (event) => {
    if (active() && intent.dirty() && intent.state !== "saved") {
      event.preventDefault();
      event.returnValue = "";
    }
  };
  const cleanup = () => {
    if (!active()) {
      window.removeEventListener("beforeunload", leaving);
      window.removeEventListener("hashchange", cleanup);
    }
  };
  window.addEventListener("beforeunload", leaving);
  window.addEventListener("hashchange", cleanup);
  sync();
  return {
    intent,
    active,
    dirty: () => {
      read();
      return intent.dirty();
    },
  };
}

export async function renderCommunityWorkspace(path, params, ctx) {
  const reply = path.match(
    /^\/community\/post\/([A-Za-z0-9_-]+)\/reply\/([A-Za-z0-9_-]+)$/,
  );
  const comment = path.match(
    /^\/community\/post\/([A-Za-z0-9_-]+)\/comments\/([A-Za-z0-9_-]+)\/(edit|history)$/,
  );
  if (!reply && !comment) return null;
  if (!ctx.user) return authGate(ctx, "로그인하고 대화를 이어가세요.");
  const [, pid, cid] = reply || comment,
    kind = reply ? "reply" : "edit";
  const load = async () => {
    const post = (await ownAPI(ctx, `/api/community/posts/${pid}`)).post;
    const target = (await ownAPI(ctx, commentAPI(cid))).comment;
    if (
      target.postId !== pid ||
      (kind === "edit" && target.userId !== ctx.user.id) ||
      (kind === "reply" && target.parentId)
    ) {
      const e = new Error("현재 대화 대상을 확인할 수 없습니다.");
      e.status = 404;
      throw e;
    }
    return { post, target };
  };
  const current = await load();
  if (comment?.[3] === "history") return commentHistory(current, params, ctx);
  const { post, target } = current;
  if (!Number.isSafeInteger(target.contentVersion))
    return {
      html: `<section class="container p-page">${back(pid, cid)}${empty("댓글의 최신 내용을 확인해주세요.", "현재 본문 상태를 확인할 수 없어 저장을 멈췄습니다.")}</section>`,
    };
  const permitted = (item) =>
    !!item.post.canComment &&
    !!item.target[kind === "reply" ? "canReply" : "canEdit"];
  const intent = new CommentIntent({
    ownerId: ctx.user.id,
    postId: pid,
    kind,
    target,
  });
  return {
    html: `<section class="container p-page cm-workspace" data-community-workspace>${back(pid, cid)}${head("CONTINUE THE CONVERSATION", kind === "reply" ? "맥락을 더하는 답글." : "생각을 더 분명하게.", kind === "reply" ? "원댓글의 맥락을 확인하고 이야기를 이어가세요." : "수정 이유와 현재 내용을 비교한 뒤 저장합니다.")}<div class="cm-context">${tag(kind === "reply" ? "원댓글에 답글" : "내 댓글 편집", "mint")}<span>${esc(post.title)}</span></div><div class="cm-workspace-grid"><div class="p-panel">${commentFormHTML(intent, { available: permitted(current) })}<a class="text-link" href="${postPath(pid, cid)}">취소하고 대화로 돌아가기 ↗</a></div><aside class="p-panel cm-original" data-community-original><p class="eyebrow">${kind === "reply" ? "REPLY TO" : "CURRENT COMMENT"}</p><h2>${kind === "reply" ? "답글을 남길 원댓글" : "현재 댓글"}</h2><p data-current-author>${esc(target.author)}</p><div class="p-user-body" data-current-body>${textBody(target.body)}</div><p class="form-note" data-current-version>본문 버전 ${target.contentVersion}</p>${kind === "edit" ? `<a class="text-link" href="${commentPath(pid, cid, "history")}">내 수정 이력 ↗</a>` : ""}</aside></div></section>`,
    bind() {
      const workspace = document.querySelector("[data-community-workspace]");
      bindCommentForm(
        workspace.querySelector("[data-community-editor]"),
        intent,
        {
          ctx,
          load,
          permitted,
          current,
          showCurrent(item) {
            workspace.querySelector("[data-current-body]").innerHTML = textBody(
              item.target.body,
            );
            workspace.querySelector("[data-current-version]").textContent =
              "본문 버전 " + item.target.contentVersion;
          },
          onSaved(receipt, result) {
            ctx.toast(
              result.currentAvailable === false
                ? "저장 영수증을 확인했습니다. 현재 댓글은 표시할 수 없습니다."
                : kind === "reply"
                  ? "답글을 등록했습니다."
                  : "댓글을 수정했습니다.",
            );
            ctx.navigate(postPath(pid, receipt.commentId).slice(1));
          },
        },
      );
    },
  };
}

async function commentHistory({ post, target }, params, ctx) {
  if (target.userId !== ctx.user.id || target.canViewHistory === false)
    return {
      html: `<section class="container p-page">${back(post.id, target.id)}${unavailable()}</section>`,
    };
  const query = new URLSearchParams({ page: params.get("page") || "1" });
  if (params.get("through")) query.set("through", params.get("through"));
  const data = await ownAPI(ctx, `${commentAPI(target.id)}/revisions?${query}`);
  const href = (page) =>
    `${commentPath(post.id, target.id, "history")}?${new URLSearchParams({ page: String(page), ...(data.through ? { through: data.through } : {}) })}`;
  return {
    html: `<section class="container p-page cm-history" data-community-history>${back(post.id, target.id)}${head("YOUR COMMENT HISTORY", "생각이 다듬어진 기록.", "현재 열람할 수 있는 본인 댓글의 원문과 수정 이유입니다.")}<p class="p-notice">이력은 본인에게만 표시합니다. 도입 전 수정 사실은 추정하지 않습니다.</p><div class="cm-context">${tag("본문 버전 " + data.contentVersion)}<span>${esc(post.title)}</span></div><div class="cm-revisions">${data.revisions.map((r) => `<article class="p-panel"><div class="cm-revision-head"><h2>버전 ${r.version}</h2>${tag(r.origin === "baseline" ? "당시 관찰 원문" : r.origin === "created" ? "처음 작성" : "수정")}</div><p class="p-muted">${r.origin === "baseline" ? "관찰 시각 " + date(r.observedAt) + " · 과거 작성·수정 시각 미확인" : date(r.eventAt)}</p><div class="p-user-body">${textBody(r.body)}</div>${r.reason ? `<p class="cm-reason"><strong>수정 이유</strong> ${esc(r.reason)}</p>` : ""}</article>`).join("") || empty("아직 기록된 수정본이 없습니다.", "기존 댓글은 현재 원문만 관찰했으며, 실제 수정부터 이력을 기록합니다.")}</div><nav class="cm-form-actions" aria-label="댓글 이력 페이지">${data.page > 1 ? `<a class="btn btn-outline" href="${href(data.page - 1)}">이전</a>` : ""}<span>${data.page} / ${Math.max(1, Math.ceil(data.total / data.pageSize))}</span>${data.page * data.pageSize < data.total ? `<a class="btn btn-outline" href="${href(data.page + 1)}">다음</a>` : ""}</nav></section>`,
  };
}
