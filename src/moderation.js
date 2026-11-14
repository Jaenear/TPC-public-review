import { esc, icon } from "./ui.js";
import {
  head,
  empty,
  authGate,
  textBody,
  date,
  tag,
  pager,
  opsNav,
} from "./platform-ui.js";
import {
  ModerationIntent,
  actionLabel,
  appealLabel,
  availableActions,
  needsReview,
  same,
  sameReviewBasis,
  moderationAPI,
  moderationPath,
} from "./moderation-model.js";

const link = (staff, entity, id = "") =>
  esc(`#${moderationPath(staff, entity, id)}`);
const badge = (status) =>
  tag(
    appealLabel(status),
    status === "accepted"
      ? "mint"
      : ["submitted", "reviewing"].includes(status)
        ? "amber"
        : "",
  );
const policy = (p, title) =>
  `<details class="mod-rule"><summary>${esc(title)} · ${p ? esc(p.title) + " / " + esc(p.edition) : "판본 미등록"}</summary>${p ? `<p class="form-note">${esc(p.mode === "test" ? "검수용 판본" : "조치에 연결된 규칙 판본")} · ${esc(p.edition)}</p><div class="p-user-body">${textBody(p.body)}</div>` : '<p class="form-note">연결된 운영 규칙 판본이 없습니다. 조치 사유와 당시 상태를 확인해주세요.</p>'}</details>`;
const stateText = (s) =>
  ({
    published: "공개",
    hidden: "운영 숨김",
    deleted: "삭제",
    submitted: "접수",
  })[s] ||
  s ||
  "—";
const fields = [
  ["version", "원문 버전"],
  ["status", "공개 상태"],
  ["locked", "댓글 잠금"],
  ["pinned", "상단 고정"],
  ["ownerDeleted", "작성자 삭제 의도"],
  ["postStatus", "상위 글 상태"],
  ["postLocked", "상위 댓글 잠금"],
  ["contentHash", "내용 식별값"],
  ["contextHash", "상위 내용 식별값"],
];
const value = (v) =>
  typeof v === "boolean"
    ? v
      ? "예"
      : "아니오"
    : typeof v === "string" && /^[a-f0-9]{64}$/.test(v)
      ? `<code title="${esc(v)}">${esc(v.slice(0, 12))}…</code>`
      : esc(stateText(v));
function comparison(source, item = null) {
  const columns = [
    ["조치 전", source.before],
    ["조치 직후", source.after],
    ...(item ? [["검토한 기준", item.reviewTarget]] : []),
    ["지금 상태", item?.currentTarget || source.currentTarget],
  ];
  const rows = fields.filter(([key]) =>
    columns.some(([, target]) => target && key in target),
  );
  return `<div class="mod-comparison" tabindex="0" role="region" aria-label="조치 당시와 현재 상태 비교"><table><thead><tr><th scope="col">확인 항목</th>${columns.map(([title]) => `<th scope="col">${esc(title)}</th>`).join("")}</tr></thead><tbody>${rows.map(([key, title]) => `<tr><th scope="row">${esc(title)}</th>${columns.map(([, target], i) => `<td class="${i === columns.length - 1 && item && !same(target?.[key], item.reviewTarget?.[key]) ? "mod-changed" : ""}">${target && key in target ? value(target[key]) : "—"}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`;
}
const pendingRules = `<p class="mod-policy-note">이의 기한과 재접수 기준은 운영 검토 중입니다. 현재 접수한 이의의 검토 상태를 이곳에서 확인할 수 있습니다.</p>`;
const nav = (staff, entity, ctx) =>
  `${staff ? opsNav(ctx, "moderation") : ""}<nav class="mod-tabs" aria-label="커뮤니티 운영 기록"><a href="${link(staff, "actions")}" ${entity === "actions" ? 'aria-current="page"' : ""}>${staff ? "조치 이력" : "내 조치 안내"}</a><a href="${link(staff, "appeals")}" ${entity === "appeals" ? 'aria-current="page"' : ""}>${staff ? "이의 검토" : "내 이의 신청"}</a><a href="#/community">커뮤니티</a></nav>`;

export async function renderModeration(path, params, ctx) {
  const match = path.match(
    /^\/(community|manage)\/moderation(?:\/(actions|appeals)(?:\/([a-z0-9_]+))?)?$/,
  );
  if (!match) return null;
  if (!ctx.user)
    return authGate(ctx, "로그인하고 본인의 조치와 이의 상태를 확인하세요.");
  const staff = match[1] === "manage",
    entity = match[2] || (staff ? "appeals" : "actions"),
    id = match[3];
  const api = moderationAPI(staff),
    q = new URLSearchParams();
  if (params.has("page")) q.set("page", params.get("page"));
  if (!id && entity === "appeals" && params.get("status"))
    q.set("status", params.get("status"));
  const data = await ctx.api(
    `${api}/${entity}${id ? "/" + id : ""}${q.size ? "?" + q : ""}`,
    { ownerId: ctx.user.id },
  );
  if (!id) return listPage(data, staff, entity, params, ctx);
  const item = data[entity === "actions" ? "action" : "appeal"],
    kind = entity === "actions" ? "action" : "appeal";
  const reviewers =
    staff && kind === "appeal" && item.canAssign
      ? (
          await ctx.api(`${api}/actions/${item.actionId}/reviewers`, {
            ownerId: ctx.user.id,
          })
        ).reviewers
      : [];
  return detailPage(item, kind, staff, reviewers, ctx);
}

function listPage(data, staff, entity, params, ctx) {
  const isAppeal = entity === "appeals",
    rows = data[entity],
    current = params.get("status") || "";
  return {
    html: `<section class="container p-page mod-page">${head(staff ? "COMMUNITY OPERATIONS" : "MY COMMUNITY", isAppeal ? (staff ? "이유를 확인하고, 함께 바로잡기." : "내 이의 신청.") : staff ? "조치의 맥락을 확인하세요." : "내 글에 대한 운영 안내.", staff ? "조치 당시의 근거와 지금 상태를 비교하고, 별도의 검토자가 결정합니다." : "조치 사유와 적용 기준을 읽고, 필요한 경우 본인 조치의 재검토를 요청하세요.")}${nav(staff, entity, ctx)}<div class="mod-list-head"><div><span class="eyebrow">${isAppeal ? "APPEALS" : "ACTION HISTORY"}</span><h2>${isAppeal ? "접수·검토 현황" : "조치 기록"} <small>${data.total}건</small></h2></div>${
      isAppeal
        ? `<form data-mod-filter><label class="field">검토 상태<select name="status">${[
            ["", "전체 상태"],
            ["submitted", "미배정"],
            ["reviewing", "검토 중"],
            ["accepted", "인용 완료"],
            ["rejected", "기각 완료"],
            ["withdrawn", "철회 완료"],
          ]
            .map(
              ([v, title]) =>
                `<option value="${v}" ${v === current ? "selected" : ""}>${title}</option>`,
            )
            .join(
              "",
            )}</select></label><button class="btn btn-outline btn-sm" type="submit">조회</button></form>`
        : ""
    }</div>${isAppeal ? '<p class="form-note">기한 경과 분류는 접수·처리 기한이 확정된 뒤 제공됩니다.</p>' : ""}<div class="mod-list">${
      rows
        .map((item) => {
          const source = isAppeal ? item.action : item;
          return `<article class="p-panel mod-card"><div class="mod-card-heading"><div><p class="eyebrow">${source.kind === "comment" ? "COMMENT" : "POST"} · ${esc(date(isAppeal ? item.updatedAt : source.createdAt))}</p><h3><a href="${link(staff, entity, item.id)}">${esc(actionLabel(source.action))}${isAppeal ? "에 대한 이의" : ""} ${icon("arrow", 17)}</a></h3></div>${isAppeal ? badge(item.status) : tag(item.canAppeal ? "이의 접수 가능" : item.appeal ? "이의 " + appealLabel(item.appeal.status) : "조치 기록", item.canAppeal ? "amber" : "")}</div><div class="mod-card-reason"><span>${isAppeal ? "접수 사유" : "조치 사유"}</span><p>${esc((isAppeal ? item.submissionReason : source.reason).slice(0, 260))}</p></div><div class="mod-card-footer"><span>${isAppeal ? `${item.canDecide ? "내 검토 · " : ""}${item.needsReview ? "최신 근거 추가 검토 필요" : ""}` : source.policy ? "적용 기준 · " + esc(source.policy.edition) : "적용 규칙 판본 미등록"}</span><a class="text-link" href="${link(staff, entity, item.id)}">상세 확인 ↗</a></div></article>`;
        })
        .join("") ||
      empty(
        isAppeal ? "접수된 이의가 없습니다." : "확인할 조치 기록이 없습니다.",
        isAppeal
          ? "이의가 접수되면 현재 상태와 검토 이력이 표시됩니다."
          : "게시글이나 댓글에 대한 운영 조치가 생기면 본인 안내에 연결됩니다.",
      )
    }</div>${pager(moderationPath(staff, entity), params, data.page, data.total)}${pendingRules}</section>`,
    bind() {
      const form = document.querySelector("[data-mod-filter]");
      if (form)
        form.onsubmit = (event) => {
          event.preventDefault();
          const query = new URLSearchParams();
          if (form.elements.status.value)
            query.set("status", form.elements.status.value);
          ctx.navigate(
            `${moderationPath(staff, entity)}${query.size ? "?" + query : ""}`,
          );
        };
    },
  };
}

function detailPage(item, kind, staff, reviewers, ctx) {
  const intent = new ModerationIntent({
    ownerId: ctx.user.id,
    item,
    kind,
    staff,
  });
  const config = { kind, staff, reviewers, intent, ctx };
  return {
    html: `<section class="container p-page mod-page"><a class="back-link" href="${link(staff, kind === "action" ? "actions" : "appeals")}">← ${kind === "action" ? "조치 기록" : "이의 목록"}</a>${head(staff ? "COMMUNITY REVIEW" : "MY COMMUNITY", kind === "action" ? "조치의 이유와 다음 절차." : "이의 검토를 이어가는 곳.", staff ? "현재 원문과 검토 근거, 결정 이력을 확인한 뒤 처리해주세요." : "처리 사유와 검토 상태를 한곳에서 확인합니다.")}${staff ? opsNav(ctx, "moderation") : ""}<div data-mod-workspace>${workspaceHTML(item, config)}</div></section>`,
    bind() {
      bindModerationWorkspace(
        document.querySelector("[data-mod-workspace]"),
        item,
        config,
      );
    },
  };
}

function history(item, staff) {
  if (!item.decisions) return "";
  return `<section class="p-panel mod-history"><div class="mod-section-head"><h2>접수·검토 이력</h2><span>${item.decisionTotal}개 기록</span></div><ol>${item.decisions.map((entry) => `<li><div><strong>${esc(actionLabel(entry.action))}</strong><time>${esc(date(entry.createdAt))}</time></div><p>${esc(entry.reason)}</p><small>${entry.actorRole === "member" ? "접수 회원" : entry.actorRole === "admin" ? "관리자" : "운영자"}${staff && entry.actor ? " · " + esc(entry.actor.name) : ""} · 검토 버전 ${entry.resultVersion}${entry.action === "conflict" ? " · 조치 변경 없이 추가 검토 요청" : ""}</small>${entry.resolutionActionId && staff ? `<a class="text-link" href="${link(true, "actions", entry.resolutionActionId)}">연결된 복원 조치 ↗</a>` : ""}</li>`).join("")}</ol>${pager(moderationPath(staff, "appeals", item.id), new URLSearchParams(), item.decisionPage, item.decisionTotal, item.decisionPageSize)}</section>`;
}

function workspaceHTML(item, config, message = "") {
  const { kind, staff, reviewers, intent } = config,
    source = kind === "action" ? item : item.action,
    isAppeal = kind === "appeal";
  const current = intent.latest || item,
    changed = isAppeal && needsReview(current),
    options = availableActions(item, kind, staff);
  const frozen = ["preview", "saving", "unknown", "conflict", "saved"].includes(
    intent.state,
  );
  if (intent.pending && !options.includes(intent.draft.action))
    options.push(intent.draft.action);
  const reviewer = staff
    ? item.reviewer || reviewers.find((p) => p.id === item.reviewerId)
    : null;
  return `<div class="mod-summary p-panel"><div><p class="eyebrow">${source.kind === "comment" ? "댓글 조치" : "게시글 조치"}</p><h2>${esc(actionLabel(source.action))}</h2><p>${esc(date(source.createdAt))} · ${source.actorRole === "admin" ? "관리자" : "운영자"}${staff && source.actor ? " · " + esc(source.actor.name) : ""} 조치</p></div><div>${isAppeal ? badge(current.status) : tag(source.canAppeal ? "이의 접수 가능" : "조치 안내", "amber")}${isAppeal ? `<span class="mod-owner">${reviewer ? "검토자 · " + esc(reviewer.name) + (reviewer.eligible === false ? " · 현재 검토 권한 없음" : "") : item.reviewerId ? "검토자 배정됨" : "검토자 미배정"}</span>` : ""}<a class="text-link" href="#/community/post/${esc(source.postId)}">연결된 게시글 확인 ↗</a></div></div><div class="mod-detail-layout"><div class="mod-main"><section class="p-panel"><h2>조치 사유</h2><div class="p-user-body">${textBody(source.reason)}</div>${policy(source.policy, "조치 당시 적용 기준")}${isAppeal ? `<div class="mod-submission"><h3>이의를 접수한 이유</h3><div class="p-user-body">${textBody(item.submissionReason)}</div></div>` : ""}${!staff ? '<p class="form-note">신고자 정보는 안내에 포함하지 않습니다.</p>' : ""}</section>${isAppeal && changed ? `<section class="mod-attention" role="status"><h2>최신 근거를 다시 확인해주세요.</h2><p>검토한 상태와 지금의 원문·적용 기준이 다릅니다. 최신 근거를 확인하기 전에는 인용할 수 없습니다.</p>${current.currentTarget?.ownerDeleted || current.currentTarget?.status === "deleted" ? "<p>작성자 삭제를 운영 복원으로 되돌리지 않습니다.</p>" : ""}</section>` : ""}${staff && current.currentContent ? `<section class="p-panel"><div class="mod-section-head"><h2>현재 원문</h2><span>버전 ${current.currentTarget.version}</span></div>${current.currentContent.title ? `<h3>${esc(current.currentContent.title)}</h3>` : ""}<div class="p-user-body">${textBody(current.currentContent.body)}</div><p class="form-note">현재 열람할 수 있는 내용입니다. 원문은 결정 직전에 다시 검사됩니다.</p></section>` : ""}<section class="p-panel mod-evidence"><h2>조치와 검토의 근거</h2><p class="form-note">당시의 상태·버전·내용 식별값과 지금 상태를 비교합니다. 과거 본문을 새로 수집한 기록은 아닙니다.</p>${comparison(source, isAppeal ? current : null)}${isAppeal ? policy(current.reviewPolicy, "검토한 적용 기준") : ""}${policy(current.currentPolicy, "현재 적용 기준")}</section>${history(current, staff)}</div><aside class="mod-sidebar"><section class="p-panel mod-form-panel"><div class="mod-section-head"><h2>${isAppeal ? (staff ? "검토 처리" : "내 신청 관리") : "이의 신청"}</h2><button type="button" class="btn-text" data-mod-refresh>최신 상태 확인</button></div>${options.length ? `<form id="mod-${kind}-${esc(item.id)}" data-session-draft data-recovery-private data-recovery-version="${esc(kind + ":" + item.id + ":" + intent.version())}" data-recovery-context="action"><label class="field">처리 항목<select name="action" ${frozen ? "disabled" : ""}>${options.map((action) => `<option value="${action}" ${action === intent.draft.action ? "selected" : ""}>${esc(actionLabel(action))}</option>`).join("")}</select></label>${intent.draft.action === "assign" ? `<label class="field">별도 검토자<select name="reviewerId" required ${frozen ? "disabled" : ""}><option value="">검토자를 선택하세요</option>${reviewers.map((p) => `<option value="${esc(p.id)}" ${intent.draft.reviewerId === p.id ? "selected" : ""}>${esc(p.name)}${p.id === config.ctx.user.id ? " · 나" : ""}</option>`).join("")}</select></label><p class="form-note">최초 조치자·작성자·신고자와 관련 없는 현재 운영자만 배정할 수 있습니다.</p>` : ""}<label class="field">${isAppeal ? "검토·처리 사유" : "이의 신청 사유"}<textarea name="reason" rows="6" minlength="5" maxlength="2000" required ${frozen ? "disabled" : ""} placeholder="검토가 필요한 상황과 이유를 구체적으로 적어주세요.">${esc(intent.draft.reason)}</textarea></label><p class="form-note">개인정보·타인의 비공개 자료를 포함하지 마세요. 현재는 사유 본문으로 접수하며 파일 첨부는 제공하지 않습니다.</p>${["recheck", "accept", "reject", "withdraw"].includes(intent.draft.action) ? `<label class="mod-check"><input type="checkbox" required data-mod-ack ${intent.pending ? "checked" : ""} ${frozen ? "disabled" : ""}>${intent.draft.action === "recheck" ? "현재 원문과 적용 기준을 확인했습니다." : intent.draft.action === "accept" ? "제한 해제와 현재 원문 상태를 확인했습니다." : intent.draft.action === "withdraw" ? "접수한 이의를 철회합니다." : "검토 근거와 기각 사유를 확인했습니다."}</label>` : ""}<button type="submit" class="btn btn-primary" ${frozen ? "disabled" : ""} data-mod-preview>처리 내용 확인 ${icon("arrow", 16)}</button></form>` : `<p class="mod-readonly">${isAppeal ? (["accepted", "rejected", "withdrawn"].includes(item.status) ? "검토가 끝난 신청입니다. 아래 이력에서 처리 이유를 확인해주세요." : staff ? "읽기 전용입니다. 배정된 별도 검토자만 최종 결정할 수 있습니다." : "현재 접수 상태를 확인해주세요.") : item.appeal ? `이미 접수한 이의가 있습니다. <a class="text-link" href="${link(false, "appeals", item.appeal.id)}">내 이의 상태 확인 ↗</a>` : staff ? "작성자에게 안내되는 조치 기록입니다. 본인 조치는 내 조치 안내에서 확인할 수 있습니다." : "현재 이 조치에 새 이의를 접수할 수 없습니다."}</p>`}<p data-mod-feedback role="status" class="mod-feedback">${esc(message)}</p>${intent.state === "preview" ? `<section class="mod-preview" aria-label="처리 내용 최종 확인"><h3>${esc(actionLabel(intent.draft.action))} 확인</h3><div class="p-user-body">${textBody(intent.pending.reason)}</div>${intent.draft.action === "assign" ? `<p>검토자 · ${esc(reviewers.find((r) => r.id === intent.pending.reviewerId)?.name || "선택한 운영자")}</p>` : ""}<p class="form-note">${intent.draft.action === "recheck" ? "화면에서 확인한 최신 원문·적용 기준을 검토 기록으로 남깁니다. 인용은 별도 처리입니다." : "사유와 처리 항목을 확인한 후 저장해주세요."}</p><div class="mod-buttons"><button type="button" class="btn btn-primary" data-mod-save>확인한 내용 저장</button><button type="button" class="btn btn-outline" data-mod-edit>다시 작성</button></div></section>` : ""}${intent.state === "unknown" ? '<div class="mod-attention"><p>저장 결과를 확인하지 못했습니다. 같은 요청으로 결과를 확인해주세요.</p><button type="button" class="btn btn-outline" data-mod-retry>같은 요청 결과 확인</button></div>' : ""}${intent.state === "conflict" ? `<div class="mod-attention" role="status"><h3>최신 상태와 비교가 필요합니다.</h3><p>작성한 사유를 유지했습니다.${intent.receipt ? " 원문을 변경하지 않은 추가 검토 기록이 저장됐습니다." : " 이 화면의 요청은 다시 적용하지 않았습니다."}</p>${intent.latest ? `<p>지금 상태 · ${esc(appealLabel(intent.latest.status) || "조치 안내")} / 버전 ${intent.latest.version || intent.latest.resultVersion}</p><button type="button" class="btn btn-outline" data-mod-rebase>최신 상태 확인 후 계속 작성</button>` : "<p>최신 상태를 조회한 후 계속해주세요.</p>"}</div>` : ""}${pendingRules}</section></aside></div>`;
}

export function bindModerationWorkspace(root, initial, config) {
  const { intent, kind, staff, ctx } = config,
    route = location.hash;
  let item = initial,
    busy = false;
  const active = () =>
    root.isConnected &&
    !root.hidden &&
    root.dataset.modUnavailable !== "true" &&
    location.hash === route;
  const endpoint = `${moderationAPI(staff)}/${kind === "action" ? "actions" : "appeals"}/${item.id}`;
  const say = (text) => {
    if (active()) root.querySelector("[data-mod-feedback]").textContent = text;
  };
  const read = () => {
    const form = root.querySelector("form");
    if (!form) return;
    intent.draft = {
      action: form.elements.action.value,
      reason: form.elements.reason.value,
      reviewerId: form.elements.reviewerId?.value || "",
    };
  };
  function fail(error) {
    if (error.status === 401 && !error.sessionChanged) {
      root.hidden = true;
      return;
    }
    if ([403, 404].includes(error.status) || error.sessionChanged) {
      intent.blocked();
      root.dataset.modUnavailable = "true";
      root.innerHTML = empty(
        "현재 권한으로 이 기록을 확인할 수 없습니다.",
        "계정·운영 역할 또는 대상 상태가 변경되었습니다.",
        `<a class="btn btn-outline" href="${link(staff, kind === "action" ? "actions" : "appeals")}">목록에서 다시 확인</a>`,
      );
      return;
    }
    say(error.message || "최신 상태를 확인하지 못했습니다.");
  }
  async function latest() {
    const data = await ctx.api(endpoint, { ownerId: ctx.user.id });
    if (!active()) return null;
    const fresh = data[kind === "action" ? "action" : "appeal"];
    if (!fresh || fresh.id !== intent.targetId)
      throw new Error("현재 대상의 상태를 확인하지 못했습니다.");
    return fresh;
  }
  function draw(message = "") {
    if (!active()) return;
    root.innerHTML = workspaceHTML(item, config, message);
    bindForm();
  }
  function bindForm() {
    const form = root.querySelector("form");
    if (!form) return;
    form.oninput = () => {
      if (intent.state === "editing") read();
    };
    form.elements.action.onchange = () => {
      read();
      draw();
      root.querySelector("[name=reviewerId],[name=reason]")?.focus();
    };
    form.onsubmit = (event) => {
      event.preventDefault();
      if (busy || !form.reportValidity()) return;
      try {
        read();
        intent.prepare();
        draw();
        root.querySelector("[data-mod-save]")?.focus();
      } catch (error) {
        say(error.message);
      }
    };
    form.addEventListener("tpc:capture-draft", (event) => {
      for (const node of [...form.elements].filter((n) =>
        ["action", "reason", "reviewerId"].includes(n.name),
      ))
        if (!event.detail.entries.some((e) => e.key === node.name))
          event.detail.entries.push({
            key: node.name,
            index: 0,
            type: node.type,
            value: node.value,
            dirty: node.value !== node.defaultValue,
            label: node.name === "reason" ? "작성 사유" : "처리 항목",
          });
      const pending = intent.export();
      if (pending)
        event.detail.entries.push({
          key: "moderationIntent",
          type: "text",
          custom: true,
          value: JSON.stringify(pending),
          dirty: true,
          label: "저장 결과 확인",
          displayValue: "로그인 후 같은 요청 결과를 직접 확인해주세요.",
        });
    });
    form.addEventListener("tpc:draft-restored", (event) => {
      read();
      for (const entry of event.detail.entries.filter(
        (e) => e.custom && e.key === "moderationIntent",
      ))
        try {
          intent.restore(JSON.parse(entry.value));
        } catch {}
      draw(
        intent.state === "unknown"
          ? "이전 저장 결과를 같은 요청으로 직접 확인해주세요."
          : "작성한 사유를 확인한 뒤 직접 저장해주세요.",
      );
      if (intent.state === "unknown")
        root.querySelector("[data-mod-retry]")?.focus();
    });
  }
  async function save() {
    if (busy) return;
    let body;
    try {
      body = intent.sending();
    } catch (error) {
      return say(error.message);
    }
    busy = true;
    draw("저장 결과를 확인하고 있습니다.");
    root.querySelectorAll("button").forEach((b) => (b.disabled = true));
    try {
      const result = await ctx.api(
        kind === "action" ? endpoint + "/appeals" : endpoint,
        {
          method: kind === "action" ? "POST" : "PATCH",
          ownerId: ctx.user.id,
          errorBody: true,
          body,
        },
      );
      if (!active()) return;
      intent.accept(result);
      ctx.toast("처리 결과를 확인했습니다. 현재 상태를 표시합니다.");
      const destination = moderationPath(
        staff,
        "appeals",
        result.currentAppeal.id,
      );
      if (location.hash === "#" + destination) ctx.reload();
      else ctx.navigate(destination);
    } catch (error) {
      if (!active()) return;
      if ([401, 403, 404].includes(error.status) || error.sessionChanged)
        return fail(error);
      if (error.status === 409) {
        const committed = error.details?.conflict && error.details?.receipt;
        if (committed) {
          try {
            intent.accept(error.details, true);
          } catch {
            intent.uncertain();
            draw(
              "충돌 기록의 결과를 확인하지 못했습니다. 같은 요청으로 확인해주세요.",
            );
            return;
          }
        } else {
          intent.pending = null;
          intent.state = "conflict";
        }
        try {
          const fresh = await latest();
          if (!fresh) return;
          if (committed) intent.latest = fresh;
          else intent.conflict(fresh);
        } catch (next) {
          fail(next);
        }
        draw("상태가 바뀌었습니다. 작성한 사유와 최신 근거를 비교해주세요.");
        root.querySelector("[data-mod-rebase]")?.focus();
      } else if ([400, 429].includes(error.status)) {
        intent.pending = null;
        intent.state = "editing";
        draw(error.message);
      } else {
        intent.uncertain();
        draw(
          "저장 결과가 불확실합니다. 새 요청을 만들지 않고 같은 결과를 확인해주세요.",
        );
        root.querySelector("[data-mod-retry]")?.focus();
      }
    } finally {
      busy = false;
    }
  }
  root.addEventListener("click", async (event) => {
    const button = event.target.closest("button");
    if (!button || busy || !active()) return;
    if (
      button.hasAttribute("data-mod-save") ||
      button.hasAttribute("data-mod-retry")
    )
      return save();
    if (button.hasAttribute("data-mod-edit")) {
      intent.change();
      draw();
      root.querySelector("[name=reason]")?.focus();
    }
    if (
      button.hasAttribute("data-mod-refresh") ||
      button.hasAttribute("data-mod-rebase")
    ) {
      busy = true;
      button.disabled = true;
      const rebase = button.hasAttribute("data-mod-rebase");
      if (intent.state === "editing") read();
      try {
        const fresh = await latest();
        if (!fresh) return;
        if (staff && kind === "appeal" && fresh.canAssign) {
          const result = await ctx.api(
            `${moderationAPI(true)}/actions/${fresh.actionId}/reviewers`,
            { ownerId: ctx.user.id },
          );
          if (!active()) return;
          config.reviewers = result.reviewers;
        }
        if (intent.state === "unknown") {
          intent.latest = fresh;
          draw(
            "최신 상태를 조회했습니다. 보관한 같은 요청 결과를 직접 확인해주세요.",
          );
          root.querySelector("[data-mod-retry]")?.focus();
        } else if (rebase) {
          intent.conflict(fresh);
          intent.rebase();
          item = fresh;
          if (!config.reviewers.some((p) => p.id === intent.draft.reviewerId))
            intent.draft.reviewerId = "";
          draw(
            "최신 상태를 확인했습니다. 사유를 검토하고 다시 처리 내용을 확인해주세요.",
          );
          root.querySelector("[name=reason]")?.focus();
        } else if (
          intent.state !== "conflict" &&
          ((!availableActions(item, kind, staff).length && !intent.pending) ||
            (sameReviewBasis(item, fresh) &&
              (intent.draft.action !== "assign" ||
                !intent.draft.reviewerId ||
                config.reviewers.some(
                  (p) => p.id === intent.draft.reviewerId,
                ))))
        ) {
          item = fresh;
          intent.item = fresh;
          intent.latest = null;
          draw("최신 상태를 확인했습니다.");
          root
            .querySelector(
              intent.state === "preview"
                ? "[data-mod-save]"
                : "[data-mod-refresh]",
            )
            ?.focus();
        } else {
          intent.conflict(fresh);
          draw(
            "최신 상태를 조회했습니다. 작성한 사유를 유지하며 계속 여부를 직접 확인합니다.",
          );
          root.querySelector("[data-mod-rebase]")?.focus();
        }
      } catch (error) {
        fail(error);
      } finally {
        busy = false;
        if (active()) button.disabled = false;
      }
    }
  });
  bindForm();
}
