import { esc } from "./ui.js";
import { head, authGate, date, tag } from "./platform-ui.js";
import {
  NoticePreferenceIntent,
  noticeCategories,
  editablePreference,
  supportedPreference,
  maxNoticePreferenceVersion,
  validatePreferences,
} from "./notification-model.js";
import {
  noticeAPI,
  operationAPI,
  noticeNav,
  denyNotice,
  bindNoticeRecovery,
  watchNoticeLeaving,
} from "./notification-ui.js";

const keyOf = (p) => p.category + ":" + p.channel;
const descriptions = {
  learning: "질문 답변, 강의와 학습 기록에 연결된 소식",
  conversation: "내 게시글·댓글에 이어지는 대화와 수정 소식",
  transaction: "주문·수강권과 거래 처리에 관한 소식",
  operation: "운영 처리, 접수 결과와 내 계정에 관한 안내",
  marketing: "선택적 홍보 소식 · 별도 동의와 전달 준비 필요",
  legacy: "이전에 받은 알림의 보관 기록",
};
function originLabel(p) {
  const origin =
    p.version === 0
      ? "서비스 기본 설정 · 아직 직접 저장하지 않음"
      : `내가 저장한 설정 · ${date(p.updatedAt)}`;
  return (
    origin +
    (p.version === maxNoticePreferenceVersion
      ? " · 현재 수신 상태는 유지되며 추가 변경은 이용할 수 없습니다."
      : "")
  );
}
function unavailableReason(p) {
  if (p.channel === "email")
    return "이메일 발송은 준비 중입니다. 저장된 선호가 있어도 현재 발송하거나 마케팅에 동의한 것으로 처리하지 않습니다.";
  if (p.category === "legacy")
    return "이전 알림은 보관 기록입니다. 읽음 상태는 알림함에서 관리하며 이 설정으로 기존 기록을 숨기지 않습니다.";
  if (p.category === "marketing")
    return "마케팅 동의와 발송을 준비 중입니다. 현재 이 화면에서는 수신 동의를 받지 않습니다.";
  return "현재 전달을 준비 중인 유형입니다.";
}
function preferenceCard(p, policy) {
  const label = noticeCategories[p.category],
    enabled = p.enabled;
  const editable = editablePreference(p, policy);
  const content = `<div class="ni-setting-heading"><div><h2>${esc(label)}</h2><p>${esc(descriptions[p.category])}</p></div>${tag(p.channel === "site" ? "사이트" : "이메일", editable ? "mint" : "")}</div><label class="ni-switch"><span>${esc(label)} ${p.channel === "site" ? "사이트" : "이메일"} 알림 받기</span><input type="checkbox" name="enabled" ${enabled ? "checked" : ""} ${editable ? "" : "disabled"}/><span class="ni-switch-track" aria-hidden="true"></span></label><p class="ni-origin" data-pref-origin>${esc(originLabel(p))}</p>`;
  if (!supportedPreference(p, policy))
    return `<article class="p-panel ni-setting ni-setting-unavailable">${content}<p class="ni-channel-note">${esc(unavailableReason(p))}</p></article>`;
  return `<form class="p-panel ni-setting" id="notice-preference-${p.category}-${p.channel}" data-preference-form data-preference-key="${esc(keyOf(p))}" data-session-draft data-recovery-private data-recovery-version="${esc(keyOf(p))}">${content}<p class="ni-feedback" role="status" tabindex="-1" data-pref-feedback>새로 받을 사이트 알림에 적용합니다. 기존 알림과 읽음 기록은 유지됩니다.</p><div class="ni-actions"><button class="btn btn-outline btn-sm" type="submit" data-pref-preview ${editable ? "" : "disabled"}>이 설정 저장 검토</button><button class="btn-text" type="button" data-pref-refresh>현재 설정 확인</button><button class="btn btn-outline btn-sm" type="button" data-pref-retry hidden>같은 설정 결과 다시 확인</button></div><section class="ni-confirm" data-pref-confirm hidden></section><section class="ni-conflict" data-pref-conflict hidden></section></form>`;
}
export async function renderNotificationSettings(ctx) {
  if (!ctx.user) return authGate(ctx, "로그인하고 내 수신 설정을 확인하세요.");
  const data = validatePreferences(
    await noticeAPI(ctx, "/api/me/notification-preferences"),
  );
  return {
    html: `<section class="container p-page ni-workspace" data-notification-settings>${head("YOUR NOTIFICATIONS", "내게 필요한 소식만.", "사이트에서 받을 새 알림을 유형별로 선택하세요.")}${noticeNav(true)}<aside class="ni-policy p-panel"><h2>수신 설정의 적용 범위</h2><p>이 설정은 새 사이트 알림의 수신 선호입니다. 기존 알림·읽음 상태, 구매 권한과 학습 기록을 바꾸지 않습니다.</p><p>${data.deliveryPolicy.requiredTypesApproved ? "현재 운영 기준을 반영한 설정입니다." : "필수 안내 유형의 운영 기준은 아직 확정 전입니다. 아래 기본값은 서비스의 제안이며 내 선택과 구분합니다."}</p><p>이메일과 마케팅 전달은 준비 중이며, 사이트 설정을 바꿔도 이메일·마케팅 수신 동의가 만들어지지 않습니다.</p></aside><div class="ni-settings-grid">${data.preferences
      .filter((p) => p.channel === "site")
      .map((p) => preferenceCard(p, data.deliveryPolicy))
      .join(
        "",
      )}</div><details class="ni-email-settings p-panel"><summary>이메일 알림 · 발송 준비 중</summary><p>현재 저장된 선호를 확인할 수 있으며 발송은 진행하지 않습니다.</p><div class="ni-settings-grid">${data.preferences
      .filter((p) => p.channel === "email")
      .map((p) => preferenceCard(p, data.deliveryPolicy))
      .join("")}</div></details></section>`,
    bind() {
      bindNotificationSettings(
        document.querySelector("[data-notification-settings]"),
        data,
        ctx,
      );
    },
  };
}

export function bindNotificationSettings(root, initial, ctx) {
  const route = location.hash,
    rows = new Map();
  let snapshot = initial,
    working = false;
  const active = () =>
    root.isConnected &&
    root.dataset.unavailable !== "true" &&
    location.hash === route;
  for (const form of root.querySelectorAll("[data-preference-form]")) {
    const p = initial.preferences.find(
      (p) => keyOf(p) === form.dataset.preferenceKey,
    );
    rows.set(keyOf(p), {
      form,
      intent: new NoticePreferenceIntent({
        ownerId: ctx.user.id,
        preference: p,
      }),
    });
  }
  const anyPending = () =>
    [...rows.values()].some(({ intent }) => !!intent.pending);
  const anyUnknown = () =>
    [...rows.values()].some(({ intent }) =>
      ["unknown", "saving"].includes(intent.state),
    );
  const cleanup = watchNoticeLeaving(
    active,
    () =>
      [...rows.values()].some(({ intent }) => intent.dirty() || intent.pending),
    root,
  );
  const node = (row, selector) => row.form.querySelector(selector);
  const status = (row, message, focus = false) => {
    if (active()) {
      const n = node(row, "[data-pref-feedback]");
      n.textContent = message;
      if (focus) n.focus();
    }
  };
  function sync() {
    if (!active()) return;
    const frozen = working || anyPending();
    for (const row of rows.values()) {
      const i = row.intent;
      row.form.elements.enabled.disabled =
        frozen ||
        i.state === "conflict" ||
        !editablePreference(i.base, snapshot.deliveryPolicy);
      node(row, "[data-pref-preview]").disabled =
        frozen ||
        i.state !== "editing" ||
        !editablePreference(i.base, snapshot.deliveryPolicy) ||
        (!i.dirty() && i.base.version > 0);
      node(row, "[data-pref-refresh]").disabled = frozen;
      node(row, "[data-pref-retry]").hidden = i.state !== "unknown";
      node(row, "[data-pref-retry]").disabled = working;
      row.form
        .querySelectorAll("[data-pref-confirm-save],[data-pref-cancel]")
        .forEach((n) => (n.disabled = working || anyUnknown()));
      row.form
        .querySelectorAll("[data-pref-rebase],[data-pref-use-latest]")
        .forEach(
          (n) =>
            (n.disabled =
              working ||
              anyPending() ||
              (n.hasAttribute("data-pref-rebase") &&
                !editablePreference(
                  i.latest || i.base,
                  snapshot.deliveryPolicy,
                ))),
        );
      node(row, "[data-pref-origin]").textContent = originLabel(i.base);
    }
    root
      .querySelectorAll("a")
      .forEach((a) => a.setAttribute("aria-disabled", String(frozen)));
  }
  function failed(row, error) {
    if (!active()) return;
    if (denyNotice(root, error)) cleanup();
    else status(row, error.message || "수신 설정을 다시 확인해주세요.", true);
  }
  function comparison(row) {
    const i = row.intent,
      current = i.latest;
    const box = node(row, "[data-pref-conflict]");
    node(row, "[data-pref-confirm]").hidden = true;
    box.hidden = false;
    box.innerHTML = `<h3>다른 곳에서 설정이 바뀌었습니다.</h3><div class="ni-compare"><p><span>현재 설정</span><strong>${current.enabled ? "알림 받기" : "알림 받지 않기"}</strong></p><p><span>내 선택</span><strong>${i.draft ? "알림 받기" : "알림 받지 않기"}</strong></p></div><p>현재 상태를 확인한 뒤 직접 다시 선택합니다. 자동으로 덮어쓰지 않습니다.</p><div class="ni-actions"><button type="button" class="btn btn-primary btn-sm" data-pref-rebase>내 선택 유지하고 다시 검토</button><button type="button" class="btn btn-outline btn-sm" data-pref-use-latest>현재 설정 사용</button></div>`;
    const choose = (latest) => {
      i.rebase(latest);
      row.form.elements.enabled.checked = i.draft;
      row.form.elements.enabled.defaultChecked = i.base.enabled;
      box.hidden = true;
      sync();
      row.form.elements.enabled.focus();
      status(
        row,
        editablePreference(i.base, snapshot.deliveryPolicy)
          ? "선택을 확인한 뒤 직접 저장해주세요."
          : "현재 수신 상태를 확인했습니다. 이 설정의 추가 변경은 이용할 수 없습니다.",
      );
    };
    box.querySelector("[data-pref-rebase]").onclick = () => choose(false);
    box.querySelector("[data-pref-use-latest]").onclick = () => choose(true);
    box.setAttribute("tabindex", "-1");
    box.focus();
    sync();
  }
  function install(data, except = null) {
    snapshot = validatePreferences(data);
    for (const [key, row] of rows) {
      if (key === except) continue;
      const current = snapshot.preferences.find((p) => keyOf(p) === key);
      row.intent.refresh(current);
      row.form.elements.enabled.checked = row.intent.draft;
      row.form.elements.enabled.defaultChecked = row.intent.base.enabled;
      if (row.intent.state === "conflict") comparison(row);
    }
  }
  function saved(row, result) {
    // Validate the complete snapshot before consuming the fixed request. A valid
    // receipt alone cannot make an incomplete/inconsistent current view safe.
    const fresh = validatePreferences(result);
    const head = fresh.preferences.find(
      (p) => keyOf(p) === keyOf(row.intent.base),
    );
    if (
      !head ||
      head.version !== result.currentPreference?.version ||
      head.enabled !== result.currentPreference?.enabled
    )
      throw new Error(
        "저장 결과와 현재 설정 목록이 다릅니다. 같은 요청으로 다시 확인해주세요.",
      );
    const i = row.intent,
      current = i.accept(result),
      receipt = i.receipt;
    install(fresh, keyOf(current));
    row.form.elements.enabled.checked = i.draft;
    row.form.elements.enabled.defaultChecked = i.base.enabled;
    node(row, "[data-pref-confirm]").hidden = true;
    node(row, "[data-pref-conflict]").hidden = true;
    status(
      row,
      receipt.version !== current.version
        ? "원래 저장 결과와 다른 곳에서 변경된 현재 설정을 함께 확인했습니다. 표시된 상태가 최신 설정입니다."
        : `수신 설정을 저장했습니다. 현재 ${current.enabled ? "알림 받기" : "알림 받지 않기"}입니다.`,
      true,
    );
    sync();
  }
  async function reconcile(row) {
    try {
      const result = await noticeAPI(
        ctx,
        operationAPI(row.intent.pending.requestKey),
      );
      if (!active()) return true;
      saved(row, result);
      return true;
    } catch (error) {
      if (error.status === 404) return false;
      throw error;
    }
  }
  async function send(row, retry) {
    if (!active() || working) return;
    working = true;
    sync();
    let sent = false;
    const i = row.intent;
    try {
      if (retry && (await reconcile(row))) return;
      if (!active()) return;
      if (!retry) {
        const fresh = validatePreferences(
          await noticeAPI(ctx, "/api/me/notification-preferences"),
        );
        if (!active()) return;
        snapshot = fresh;
        const current = fresh.preferences.find(
          (p) => keyOf(p) === keyOf(i.base),
        );
        if (
          current.version !== i.base.version ||
          current.enabled !== i.base.enabled ||
          !editablePreference(current, fresh.deliveryPolicy)
        ) {
          i.conflict(current);
          comparison(row);
          return;
        }
      }
      if (!active()) return;
      const body = i.sending();
      sent = true;
      sync();
      status(row, "설정 저장 결과를 확인하고 있습니다…");
      const result = await noticeAPI(ctx, "/api/me/notification-preferences", {
        method: "PATCH",
        body,
      });
      if (active()) saved(row, result);
    } catch (error) {
      if (!active()) return;
      if (error.status === 409 && !error.sessionChanged) {
        try {
          if (await reconcile(row)) return;
          const fresh = validatePreferences(
            await noticeAPI(ctx, "/api/me/notification-preferences"),
          );
          if (!active()) return;
          snapshot = fresh;
          i.conflict(fresh.preferences.find((p) => keyOf(p) === keyOf(i.base)));
          comparison(row);
        } catch (failedError) {
          i.uncertain();
          failed(row, failedError);
        }
      } else {
        if (sent || retry) i.uncertain();
        if (!denyNotice(root, error))
          status(
            row,
            sent || retry
              ? "저장 결과를 확인하지 못했습니다. 같은 설정 요청으로 다시 확인해주세요."
              : error.message,
            true,
          );
        else cleanup();
      }
    } finally {
      working = false;
      sync();
      if (active() && i.state === "unknown")
        node(row, "[data-pref-retry]").focus();
    }
  }
  for (const row of rows.values()) {
    const i = row.intent;
    row.form.elements.enabled.addEventListener("change", () => {
      try {
        i.edit(row.form.elements.enabled.checked);
        node(row, "[data-pref-confirm]").hidden = true;
        sync();
      } catch (error) {
        failed(row, error);
      }
    });
    row.form.onsubmit = (event) => {
      event.preventDefault();
      if (!active() || working || anyPending()) return;
      try {
        i.prepare();
        const box = node(row, "[data-pref-confirm]");
        box.hidden = false;
        box.innerHTML = `<h3>${esc(noticeCategories[i.base.category])} 알림 설정을 저장할까요?</h3><p>앞으로 새로 받을 사이트 알림: <strong>${i.draft ? "받기" : "받지 않기"}</strong></p><p>기존 알림·읽음 기록·수강 권한은 바뀌지 않습니다.</p><div class="ni-actions"><button type="button" class="btn btn-primary btn-sm" data-pref-confirm-save>설정 저장</button><button type="button" class="btn btn-outline btn-sm" data-pref-cancel>다시 선택</button></div>`;
        box.querySelector("[data-pref-confirm-save]").onclick = () =>
          send(row, false);
        box.querySelector("[data-pref-cancel]").onclick = () => {
          i.change();
          box.hidden = true;
          sync();
          row.form.elements.enabled.focus();
        };
        sync();
        box.querySelector("[data-pref-confirm-save]").focus();
      } catch (error) {
        failed(row, error);
      }
    };
    node(row, "[data-pref-retry]").onclick = () => send(row, true);
    node(row, "[data-pref-refresh]").onclick = async () => {
      if (!active() || working || anyPending()) return;
      working = true;
      sync();
      try {
        const fresh = validatePreferences(
          await noticeAPI(ctx, "/api/me/notification-preferences"),
        );
        if (active()) {
          install(fresh);
          if (i.state !== "conflict")
            status(
              row,
              editablePreference(i.base, snapshot.deliveryPolicy)
                ? "현재 설정을 확인했습니다. 내 선택을 검토한 뒤 직접 저장해주세요."
                : "현재 수신 상태를 확인했습니다. 이 설정의 추가 변경은 이용할 수 없습니다.",
              true,
            );
        }
      } catch (error) {
        failed(row, error);
      } finally {
        working = false;
        sync();
      }
    };
    bindNoticeRecovery(row.form, i, (accepted) => {
      if (!accepted) return;
      row.form.elements.enabled.checked = i.draft;
      if (i.state === "conflict") comparison(row);
      else
        status(
          row,
          i.state === "unknown"
            ? "이전 설정 저장 결과를 같은 요청으로 직접 확인해주세요."
            : "이전에 선택한 설정을 복원했습니다. 확인한 뒤 직접 저장해주세요.",
          true,
        );
      sync();
    });
  }
  root.addEventListener("click", (event) => {
    if (event.target.closest("a") && (working || anyPending())) {
      event.preventDefault();
      const row = [...rows.values()].find((r) => r.intent.pending);
      if (row)
        status(
          row,
          "현재 설정 요청을 취소하거나 저장 결과를 먼저 확인해주세요.",
          true,
        );
    }
  });
  sync();
  return { rows, active };
}
