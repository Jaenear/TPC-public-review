import { esc } from "./ui.js";
import { head, empty, authGate, date, tag } from "./platform-ui.js";
import { redactCommunityView } from "./community-workspace.js";
import {
  CommunityBlockIntent,
  postPath,
  validConversationId,
} from "./community-model.js";

const endpoint = (target) =>
  "/api/me/community-blocks" + (target ? "/" + target : "");
export async function renderCommunityBlocks(path, params, ctx) {
  const match = path.match(/^\/community\/blocks(?:\/([A-Za-z0-9_-]+))?$/);
  if (!match) return null;
  if (!ctx.user) return authGate(ctx, "로그인하고 내 대화 설정을 확인하세요.");
  const target = match[1],
    request = (path, options = {}) =>
      ctx.api(path, { ...options, ownerId: ctx.user.id });
  const backId = params.get("back"),
    backHref = validConversationId(backId) ? postPath(backId) : "#/community";
  if (!target) {
    const data = await request(
      endpoint() +
        "?" +
        new URLSearchParams({ page: params.get("page") || "1" }),
    );
    const pageHref = (page) => "#/community/blocks?page=" + page;
    return {
      html: `<section class="container p-page cm-workspace"><a class="back-link" href="#/community">← 커뮤니티</a>${head("YOUR CONVERSATIONS", "대화의 경계를 정하세요.", "내가 차단한 회원만 이 목록에 표시합니다.")}<p class="p-notice">대화 차단은 두 회원 사이의 새 댓글·답글과 로그인 후 댓글 표시에 적용됩니다. 공개 게시글 자체는 계속 볼 수 있습니다.</p><div class="cm-block-list">${data.blocks.map((b) => `<article class="p-panel"><div><h2>${esc(b.targetName)}</h2><p class="p-muted">${date(b.updatedAt)} 설정</p></div>${tag("대화 차단 중")}<a class="btn btn-outline btn-sm" href="#/community/blocks/${esc(b.targetId)}">설정 확인</a></article>`).join("") || empty("차단한 회원이 없습니다.", "댓글의 대화 차단 메뉴에서 원하는 대상을 선택할 수 있습니다.")}</div><nav class="cm-form-actions" aria-label="대화 차단 목록 페이지">${data.page > 1 ? `<a class="btn btn-outline" href="${pageHref(data.page - 1)}">이전</a>` : ""}<span>${data.page} / ${Math.max(1, Math.ceil(data.total / data.pageSize))}</span>${data.page * data.pageSize < data.total ? `<a class="btn btn-outline" href="${pageHref(data.page + 1)}">다음</a>` : ""}</nav></section>`,
    };
  }
  const initial = (await request(endpoint(target))).block;
  const intent = new CommunityBlockIntent({
    ownerId: ctx.user.id,
    block: initial,
  });
  return {
    html: `<section class="container p-page cm-workspace" data-community-block><a class="back-link" href="${backHref}">← ${validConversationId(backId) ? "게시글" : "커뮤니티"}</a>${head("CONVERSATION BOUNDARY", "내 대화 설정.", "상대방에게 차단 설정 알림을 보내지 않습니다.")}<form class="p-panel cm-block-setting" id="community-block-${esc(target)}" data-session-draft data-recovery-private data-recovery-version="${esc(target)}"><div class="cm-section-head"><h2>${esc(initial.targetName)}</h2><span data-block-state>${tag(initial.blocked ? "대화 차단 중" : "차단하지 않음", initial.blocked ? "amber" : "mint")}</span></div><p>차단하면 서로의 새 댓글·답글·댓글 편집 참여가 제한되며, 로그인 후 댓글과 해당 댓글의 알림 내용이 가려집니다. 공개 게시글 자체의 읽기와 운영자의 신고 검수 권한은 유지됩니다.</p><p class="form-note">기존 알림의 읽음 여부를 지우거나 차단 중 생기지 않은 알림을 나중에 소급 전달하지 않습니다.</p><p class="cm-feedback" data-block-feedback role="status">현재 설정을 확인한 뒤 직접 변경해주세요.</p><div class="cm-form-actions"><button class="btn btn-primary btn-sm" type="submit" data-block-preview>${initial.blocked ? "차단 해제 검토" : "대화 차단 검토"}</button><button type="button" class="btn btn-outline btn-sm" data-block-refresh>최신 설정 확인</button><button type="button" class="btn btn-outline btn-sm" data-block-retry hidden>같은 설정 저장 결과 다시 확인</button></div><section class="cm-preview" data-block-confirm-box hidden></section><a class="text-link" href="#/community/blocks">내 차단 목록 보기 ↗</a></form></section>`,
    bind() {
      bindCommunityBlock(
        document.querySelector("[data-community-block]"),
        intent,
        { ctx, request },
      );
    },
  };
}

export function bindCommunityBlock(root, intent, { ctx, request }) {
  const route = location.hash,
    form = root.querySelector("form");
  let working = false;
  const active = () =>
    root.isConnected &&
    !root.hidden &&
    root.dataset.unavailable !== "true" &&
    location.hash === route;
  const node = (selector) => root.querySelector(selector);
  const feedback = (message) => {
    if (active()) node("[data-block-feedback]").textContent = message;
  };
  function sync() {
    if (!active()) return;
    const frozen = working || ["saving", "unknown"].includes(intent.state);
    node("[data-block-preview]").disabled =
      frozen || intent.state !== "editing";
    node("[data-block-preview]").textContent = intent.base.blocked
      ? "차단 해제 검토"
      : "대화 차단 검토";
    node("[data-block-refresh]").disabled = frozen;
    node("[data-block-retry]").hidden = intent.state !== "unknown";
    node("[data-block-retry]").disabled = working;
    root
      .querySelectorAll("[data-block-confirm],[data-block-cancel]")
      .forEach((n) => (n.disabled = frozen));
    node("[data-block-state]").innerHTML = tag(
      intent.base.blocked ? "대화 차단 중" : "차단하지 않음",
      intent.base.blocked ? "amber" : "mint",
    );
  }
  function fail(error) {
    if (!active()) return;
    if (error.status === 401) {
      redactCommunityView(root, error);
      return;
    }
    if (error.sessionChanged || [403, 404].includes(error.status)) {
      root.dataset.unavailable = "true";
      redactCommunityView(root, error);
      return;
    }
    feedback(error.message || "설정 저장 결과를 확인하지 못했습니다.");
  }
  async function refresh() {
    const result = await request(endpoint(intent.targetId));
    if (!active()) return;
    intent.refresh(result.block);
    node("[data-block-confirm-box]").hidden = true;
    feedback(
      "현재 설정을 불러왔습니다. 변경할 내용이 있으면 다시 선택해주세요.",
    );
    sync();
  }
  async function send() {
    if (!active() || working) return;
    working = true;
    sync();
    const wasUnknown = intent.state === "unknown";
    try {
      const body = intent.sending();
      sync();
      const result = await request(endpoint(intent.targetId), {
        method: "PATCH",
        body,
      });
      if (!active()) return;
      const current = intent.accept(result);
      node("[data-block-confirm-box]").hidden = true;
      feedback(
        result.receipt.version !== current.version
          ? "이전 저장 영수증과 다른 탭에서 변경된 현재 설정을 함께 확인했습니다. 표시된 상태가 최신 설정입니다."
          : current.blocked
            ? "대화 차단 설정을 저장했습니다."
            : "대화 차단을 해제했습니다.",
      );
      ctx.toast("내 대화 설정을 확인했습니다.");
    } catch (error) {
      if (!active()) return;
      if (error.status === 409 && !error.sessionChanged && !wasUnknown) {
        intent.state = "conflict";
        try {
          await refresh();
          feedback(
            "다른 곳에서 설정이 바뀌었습니다. 최신 상태를 확인한 뒤 직접 다시 선택해주세요.",
          );
        } catch (failed) {
          fail(failed);
        }
      } else {
        intent.uncertain();
        fail(error);
      }
    } finally {
      working = false;
      sync();
    }
  }
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    if (!active() || working || intent.state !== "editing") return;
    const pending = intent.prepare(!intent.base.blocked),
      box = node("[data-block-confirm-box]");
    box.hidden = false;
    box.innerHTML = `<h3>${pending.blocked ? "이 회원과의 대화를 차단할까요?" : "대화 차단을 해제할까요?"}</h3><p>${pending.blocked ? "댓글 표시와 새 대화 참여에 위 설명이 적용됩니다." : "현재 상태에서 허용된 댓글과 새 대화 참여를 다시 확인할 수 있습니다."}</p><div class="cm-form-actions"><button type="button" class="btn btn-primary btn-sm" data-block-confirm>${pending.blocked ? "대화 차단 저장" : "차단 해제 저장"}</button><button type="button" class="btn btn-outline btn-sm" data-block-cancel>취소</button></div>`;
    box.querySelector("[data-block-confirm]").onclick = send;
    box.querySelector("[data-block-cancel]").onclick = () => {
      intent.pending = null;
      intent.state = "editing";
      box.hidden = true;
      sync();
      node("[data-block-preview]").focus();
    };
    box.querySelector("[data-block-confirm]").focus();
    sync();
  });
  node("[data-block-retry]").onclick = send;
  node("[data-block-refresh]").onclick = async () => {
    if (working || intent.state === "unknown") return;
    working = true;
    sync();
    try {
      await refresh();
    } catch (error) {
      fail(error);
    } finally {
      working = false;
      sync();
    }
  };
  form.addEventListener("tpc:capture-draft", (event) => {
    const value = intent.export();
    if (value)
      event.detail.entries.push({
        key: "communityBlockIntent",
        type: "text",
        custom: true,
        value: JSON.stringify(value),
        dirty: true,
        label: "내 대화 설정 저장 결과",
        displayValue: "로그인 후 같은 설정 요청의 결과를 직접 확인해주세요.",
      });
  });
  form.addEventListener("tpc:draft-restored", (event) => {
    for (const entry of event.detail.entries.filter(
      (e) => e.key === "communityBlockIntent",
    ))
      try {
        if (intent.restore(JSON.parse(entry.value)))
          feedback("이전 설정 저장 결과를 같은 요청으로 직접 확인해주세요.");
      } catch {}
    sync();
  });
  sync();
  return { intent, active };
}
