import { esc } from "./ui.js";
import { head, empty, authGate, textBody, date, tag } from "./platform-ui.js";
import {
  QAEditIntent,
  targetOf,
  editPath,
  diffLines,
  refs,
} from "./qa-model.js";
import { fileLinks, bindFileLinks, attachmentManager } from "./qa-files.js";

const baseAPI = (kind, id) =>
  `/api/qa/${kind === "question" ? "questions" : "answers"}/${id}`;
const compare = (before, after) => {
  const lines = diffLines(before, after);
  const side = (label, rows, kind) =>
    `<section><h3>${esc(label)}</h3><div class="qa-diff-text">${rows.map((row) => `<div class="${row.changed ? "qa-line-" + kind : ""}">${esc(row.text) || "&nbsp;"}</div>`).join("")}</div></section>`;
  return `<div class="qa-compare">${side("수정 전", lines.before, "removed")}${side("수정 후", lines.after, "added")}</div>`;
};
const back = (qid) =>
  `<a class="back-link" href="#/qa/${esc(qid)}">← 질문과 답변</a>`;
const snapshotText = (item) =>
  (item?.title ? item.title + "\n\n" : "") + (item?.body || "");

export async function renderQAWorkspace(path, params, ctx) {
  const match = path.match(
    /^\/qa\/([a-z0-9_]+)(?:\/answers\/([a-z0-9_]+))?\/(edit|history)$/,
  );
  if (!match) return null;
  if (!ctx.user) return authGate(ctx, "로그인하고 질문의 맥락을 이어가세요.");
  const [, qid, aid, page] = match,
    kind = aid ? "answer" : "question";
  const q = (
    await ctx.api(`/api/qa/questions/${qid}`, { ownerId: ctx.user.id })
  ).question;
  const target = targetOf(q, aid);
  if (!target[page === "edit" ? "canEdit" : "canViewHistory"])
    return {
      html: `<section class="container p-page">${back(qid)}${empty(page === "edit" ? "현재 수정할 수 없는 내용입니다." : "현재 열람할 수 없는 이력입니다.", "작성자·담당 역할과 질문 상태를 확인해주세요.")}</section>`,
    };
  return page === "edit"
    ? editWorkspace(q, target, kind, ctx)
    : historyWorkspace(q, target, kind, ctx);
}

async function editWorkspace(q, target, kind, ctx) {
  const data = await ctx.api(`/api/qa/questions/${q.id}/attachments`, {
    ownerId: ctx.user.id,
  });
  const intent = new QAEditIntent({
    ownerId: ctx.user.id,
    questionId: q.id,
    kind,
    target,
  });
  const id = `qa-edit-${kind}-${target.id}`;
  return {
    html: `<section class="container p-page qa-page" data-qa-editor>${back(q.id)}${head("REFINE YOUR QUESTION", kind === "question" ? "질문을 더 분명하게." : "답변에 근거를 더하세요.", "수정 이유와 전후 내용을 확인한 뒤 저장합니다. 공식 답변과 해결 표시는 유지됩니다.")}<div class="qa-context">${tag(q.visibility === "private" ? "비공개 질문" : "수강생 공개")}${kind === "answer" && target.official ? tag("공식 답변", "mint") : ""}<span>${esc(q.title)}</span><a class="text-link" href="${editPath(q.id, kind === "answer" ? target.id : null, "history")}">수정 이력 ↗</a></div><form id="${esc(id)}" class="qa-editor-layout" data-session-draft data-recovery-private data-recovery-version="${esc(kind + ":" + target.id + ":" + target.contentVersion)}"><div class="qa-editor-main p-panel"><div class="qa-section-head"><h2>수정할 내용</h2><span data-base-version>본문 버전 ${target.contentVersion}</span></div>${kind === "question" ? `<label class="field">질문 제목<input name="title" required minlength="3" maxlength="150" value="${esc(target.title)}"></label>` : ""}<label class="field">${kind === "question" ? "질문 내용" : "답변 내용"}<textarea name="body" rows="12" required minlength="${kind === "question" ? 10 : 2}" maxlength="10000">${esc(target.body)}</textarea></label><label class="field">수정 이유<textarea name="reason" rows="3" required minlength="5" maxlength="2000" placeholder="어떤 전제나 근거를 보완했나요?"></textarea></label><p class="form-note">수정 전 내용과 이유는 현재 열람 권한이 있는 회원에게 기록으로 남습니다.</p><div data-qa-manager class="qa-manager"></div><p class="form-error" data-qa-feedback role="status"></p><div class="qa-save-bar"><button type="submit" class="btn btn-primary" data-qa-preview>수정 내용 미리보기</button><button type="button" class="btn btn-outline" data-qa-retry hidden>같은 수정 저장 결과 다시 확인</button></div><section data-qa-preview-box class="qa-preview" hidden></section><section data-qa-conflict class="qa-conflict" hidden></section></div><aside class="qa-original p-panel" data-qa-original><p class="eyebrow">CURRENT VERSION</p><h2>현재 공개된 원문</h2><div data-original-text>${kind === "question" ? `<h3>${esc(target.title)}</h3>` : ""}<div class="p-user-body">${textBody(target.body)}</div></div><p class="form-note">저장 전에는 이 원문이 바뀌지 않습니다.</p><div data-current-files>${fileLinks(target.contentAttachments)}</div><div data-file-preview></div></aside></form></section>`,
    bind() {
      const root = document.querySelector("[data-qa-editor]"),
        form = root.querySelector("form"),
        route = location.hash;
      const active = () =>
        root.isConnected &&
        !root.hidden &&
        root.dataset.qaUnavailable !== "true" &&
        location.hash === route;
      const feedback = (message) => {
        if (active())
          root.querySelector("[data-qa-feedback]").textContent = message;
      };
      const controls = () => {
        if (!active()) return;
        const frozen = ["saving", "unknown", "blocked", "saved"].includes(
          intent.state,
        );
        form
          .querySelectorAll(
            "[name=title],[name=body],[name=reason],[data-reference],[data-qa-preview]",
          )
          .forEach((node) => (node.disabled = frozen));
        root.querySelector("[data-qa-retry]").hidden =
          intent.state !== "unknown";
        root.querySelector("[data-qa-retry]").disabled =
          intent.state === "saving";
        root.querySelector("[data-base-version]").textContent =
          "본문 버전 " + intent.base.version;
        form.dataset.recoveryVersion = `${kind}:${target.id}:${intent.base.version}`;
        manager?.sync();
      };
      const read = () => {
        intent.draft = {
          title: form.elements.title?.value || "",
          body: form.elements.body.value,
          reason: form.elements.reason.value,
        };
      };
      const originalItems = [...(target.contentAttachments || [])];
      function fail(error) {
        if (!active()) return;
        if (error.status === 401) {
          root.hidden = true;
          return;
        }
        if ([403, 404].includes(error.status) || error.sessionChanged) {
          root.dataset.qaUnavailable = "true";
          intent.state = "blocked";
          intent.pending = null;
          intent.draft = { title: "", body: "", reason: "" };
          root.innerHTML = `${empty("현재 권한에서 내용을 표시할 수 없습니다.", "질문·수강권 또는 담당 역할이 변경되었습니다. 최신 상태에서 다시 확인해주세요.", `<a class="btn btn-outline" href="#/qa/${esc(q.id)}">질문 다시 확인</a>`)}`;
          return;
        }
        feedback(
          error.message ||
            "최신 상태를 확인하지 못했습니다. 직접 다시 확인해주세요.",
        );
      }
      async function latest() {
        const fresh = (
          await ctx.api(`/api/qa/questions/${q.id}`, { ownerId: ctx.user.id })
        ).question;
        if (!active()) throw new Error("화면이 변경되었습니다.");
        const t = targetOf(fresh, kind === "answer" ? target.id : null);
        if (!t.canEdit) {
          const error = new Error("현재 수정 권한이 없습니다.");
          error.status = 403;
          throw error;
        }
        return t;
      }
      async function refreshFiles() {
        viewer.invalidate();
        const fresh = await latest();
        const next = await ctx.api(`/api/qa/questions/${q.id}/attachments`, {
          ownerId: ctx.user.id,
        });
        if (active()) updateOriginal(fresh);
        if (
          active() &&
          fresh.contentVersion !== intent.base.version &&
          intent.state !== "saved"
        ) {
          intent.conflict(fresh);
          showConflict();
        }
        return next;
      }
      const manager = attachmentManager(root, q.id, data, ctx, {
        locked: () =>
          ["saving", "unknown", "blocked", "saved"].includes(intent.state),
        selected: () => intent.selected,
        baseRefs: () => intent.base.refs,
        onSelection(id, checked) {
          if (checked) intent.selected.add(id);
          else intent.selected.delete(id);
          intent.change();
          root.querySelector("[data-qa-preview-box]").hidden = true;
        },
        refresh: refreshFiles,
        fail,
      });
      const viewer = bindFileLinks(root, originalItems, ctx, async (error) => {
        if (error.status === 404) {
          try {
            manager.update(await refreshFiles());
            feedback("자료 상태가 변경되었습니다. 최신 목록을 확인해주세요.");
          } catch (next) {
            fail(next);
          }
        } else fail(error);
      });
      function updateOriginal(fresh) {
        originalItems.splice(
          0,
          originalItems.length,
          ...(fresh.contentAttachments || []),
        );
        root.querySelector("[data-original-text]").innerHTML = textBody(
          snapshotText(fresh),
        );
        root.querySelector("[data-current-files]").innerHTML = fileLinks(
          fresh.contentAttachments,
        );
      }
      function showConflict() {
        viewer.invalidate();
        updateOriginal(intent.latest);
        const box = root.querySelector("[data-qa-conflict]");
        box.hidden = false;
        box.innerHTML = `<p class="eyebrow">CONTENT CHANGED</p><h2>다른 수정이 먼저 저장됐습니다.</h2><p>작성한 초안을 유지했습니다. 최신 원문과 비교하고 다시 적용할지 결정해주세요.</p><div class="qa-compare"><section><h3>나의 수정 초안</h3><div class="p-user-body">${textBody(snapshotText(intent.draft))}</div></section><section><h3>최신 서버 원문 · 버전 ${intent.latest.contentVersion}</h3><div class="p-user-body">${textBody(snapshotText(intent.latest))}</div></section></div><button type="button" class="btn btn-outline" data-qa-rebase>최신 원문을 기준으로 다시 편집</button>`;
        root.querySelector("[data-qa-preview-box]").hidden = true;
        controls();
        box.scrollIntoView({ block: "nearest", behavior: "smooth" });
      }
      form.addEventListener("input", (event) => {
        if (["body", "title", "reason"].includes(event.target.name)) {
          read();
          intent.change();
          root.querySelector("[data-qa-preview-box]").hidden = true;
        }
      });
      form.onsubmit = (event) => {
        event.preventDefault();
        if (!form.reportValidity()) return;
        if (manager.busy())
          return feedback("자료 작업의 결과를 먼저 확인해주세요.");
        try {
          read();
          const request = intent.prepare();
          const box = root.querySelector("[data-qa-preview-box]");
          box.hidden = false;
          box.innerHTML = `<p class="eyebrow">REVIEW YOUR CHANGES</p><h2>저장 전, 한 번 더 확인하세요.</h2>${compare(snapshotText(intent.base), snapshotText(request))}<div class="qa-reason"><strong>수정 이유</strong><p>${esc(request.reason)}</p><small>자료 추가 ${request.attachments.add.length}개 · 분리 ${request.attachments.detach.length}개</small></div><button type="button" class="btn btn-primary" data-qa-save>확인한 내용 저장</button>`;
          feedback("");
          box.scrollIntoView({ block: "nearest", behavior: "smooth" });
        } catch (error) {
          feedback(error.message);
        }
      };
      async function save() {
        if (manager.busy())
          return feedback("자료 작업의 결과를 먼저 확인해주세요.");
        let request;
        try {
          request = intent.sending();
        } catch (error) {
          return feedback(error.message);
        }
        controls();
        root
          .querySelectorAll("[data-qa-save],[data-qa-rebase]")
          .forEach((node) => (node.disabled = true));
        try {
          const result = await ctx.api(baseAPI(kind, target.id), {
            method: "PATCH",
            ownerId: ctx.user.id,
            body: request,
          });
          if (!active()) return;
          intent.accept(result);
          ctx.toast("수정 내용을 저장했습니다. 최신 질문 화면을 확인합니다.");
          ctx.navigate(`/qa/${q.id}`);
        } catch (error) {
          if (!active()) return;
          if (error.status === 409 && !error.sessionChanged) {
            try {
              intent.conflict(await latest());
              showConflict();
            } catch (next) {
              intent.state = "conflict";
              feedback(
                "최신 원문을 확인하지 못했습니다. 작성 내용은 유지합니다.",
              );
              fail(next);
            }
          } else if (
            [401, 403, 404].includes(error.status) ||
            error.sessionChanged
          )
            fail(error);
          else if ([400, 429].includes(error.status)) {
            intent.pending = null;
            intent.state = "editing";
            feedback(error.message);
          } else {
            intent.uncertain();
            feedback(
              "저장 결과를 확인하지 못했습니다. 같은 수정 요청으로 결과를 직접 다시 확인해주세요.",
            );
          }
        } finally {
          if (active() && intent.state !== "blocked") controls();
        }
      }
      root.addEventListener("click", async (event) => {
        const b = event.target.closest("button");
        if (!b) return;
        if (b.hasAttribute("data-qa-save") || b.hasAttribute("data-qa-retry"))
          return save();
        if (b.hasAttribute("data-qa-rebase")) {
          try {
            const current = await latest();
            intent.conflict(current);
            const list = await ctx.api(
              `/api/qa/questions/${q.id}/attachments`,
              { ownerId: ctx.user.id },
            );
            if (!active()) return;
            intent.rebase(list.attachments);
            viewer.invalidate();
            updateOriginal(current);
            form.elements.body.value = intent.draft.body;
            if (form.elements.title)
              form.elements.title.value = intent.draft.title;
            root.querySelector("[data-qa-conflict]").hidden = true;
            root.querySelector("[data-original-text]").innerHTML = textBody(
              snapshotText(intent.base),
            );
            manager.update(list);
            feedback(
              "최신 원문 기준으로 초안을 준비했습니다. 미리보기에서 다시 확인하고 저장해주세요.",
            );
            controls();
          } catch (error) {
            fail(error);
          }
        }
      });
      form.addEventListener("tpc:capture-draft", (event) => {
        for (const control of [...form.elements].filter(
          (c) =>
            ["title", "body", "reason"].includes(c.name) || c.dataset.reference,
        )) {
          if (!event.detail.entries.some((e) => e.key === control.name))
            event.detail.entries.push({
              key: control.name,
              index: 0,
              type: control.type,
              value: control.value,
              checked: control.checked,
              dirty:
                control.type === "checkbox"
                  ? control.checked !== control.defaultChecked
                  : control.value !== control.defaultValue,
              label:
                control.name === "body"
                  ? "본문"
                  : control.name === "title"
                    ? "제목"
                    : control.name === "reason"
                      ? "수정 이유"
                      : "본문 연결 자료",
            });
        }
        for (const [key, value, label] of [
          ["qaIntent", intent.export(), "저장 결과 확인"],
          ["qaFileIntent", manager.snapshot(), "자료 작업 확인"],
        ])
          if (value)
            event.detail.entries.push({
              key,
              type: "text",
              custom: true,
              value: JSON.stringify(value),
              dirty: true,
              label,
              displayValue: "같은 요청의 결과는 로그인 후 직접 확인해주세요.",
            });
      });
      form.addEventListener("tpc:draft-restored", (event) => {
        read();
        form.querySelectorAll("[data-reference]").forEach((node) => {
          if (node.checked) intent.selected.add(node.value);
          else intent.selected.delete(node.value);
        });
        for (const entry of event.detail.entries.filter((e) => e.custom)) {
          try {
            const value = JSON.parse(entry.value);
            if (entry.key === "qaIntent") intent.restore(value);
            if (entry.key === "qaFileIntent") manager.restore(value);
          } catch {}
        }
        controls();
        if (intent.state === "unknown")
          feedback("이전 저장 결과를 같은 수정 요청으로 직접 확인해주세요.");
      });
      const leaving = (event) => {
        if (active() && intent.state !== "saved" && intent.dirty()) {
          event.preventDefault();
          event.returnValue = "";
        }
      };
      window.addEventListener("beforeunload", leaving);
      const cleanup = () => {
        if (!root.isConnected || location.hash !== route) {
          window.removeEventListener("beforeunload", leaving);
          window.removeEventListener("hashchange", cleanup);
        }
      };
      window.addEventListener("hashchange", cleanup);
      controls();
    },
  };
}

async function historyWorkspace(q, target, kind, ctx) {
  const initial = await ctx.api(
    baseAPI(kind, target.id) + "/revisions?limit=10",
    { ownerId: ctx.user.id },
  );
  return {
    html: `<section class="container p-page qa-page" data-qa-history>${back(q.id)}${head("THE CONTEXT OF CHANGE", "이유가 남는 수정 이력.", "수정 전후와 당시 이유를 확인합니다. 자료는 현재 열람 권한과 제공 상태를 따릅니다.")}<div class="qa-context"><strong>${esc(kind === "question" ? q.title : "답변 수정 이력")}</strong>${target.canEdit ? `<a class="text-link" href="${editPath(q.id, kind === "answer" ? target.id : null)}">내용 수정 ↗</a>` : ""}<button type="button" class="btn-text" data-history-refresh>최신 이력 새로 보기</button></div><div data-qa-history-rows></div><p data-history-message role="status"></p><button class="btn btn-outline" type="button" data-history-more>이전 수정 더 보기</button><div data-file-preview></div></section>`,
    bind() {
      const root = document.querySelector("[data-qa-history]"),
        route = location.hash,
        rows = root.querySelector("[data-qa-history-rows]");
      const active = () =>
        root.isConnected &&
        !root.hidden &&
        root.dataset.qaUnavailable !== "true" &&
        location.hash === route;
      let packet = initial,
        busy = false;
      const items = [];
      function unavailable(error) {
        if (!active()) return;
        if (error.status === 401) {
          root.hidden = true;
          return;
        }
        if ([403, 404].includes(error.status) || error.sessionChanged) {
          root.dataset.qaUnavailable = "true";
          root.innerHTML = empty(
            "현재 권한에서 이력을 표시할 수 없습니다.",
            "질문과 수강권을 최신 상태에서 확인해주세요.",
            `<a class="btn btn-outline" href="#/qa/${esc(q.id)}">질문 다시 확인</a>`,
          );
        } else
          root.querySelector("[data-history-message]").textContent =
            error.message;
      }
      function append(data, clear = false) {
        if (clear) {
          rows.replaceChildren();
          items.splice(0);
        }
        for (const r of data.revisions) {
          const node = document.createElement("article");
          node.className = "p-panel qa-revision";
          const role =
            { admin: "관리자", coach: "담당 코치", member: "작성 회원" }[
              r.actorRole
            ] ||
            (r.origin === "baseline"
              ? "기존 원문 기준본"
              : r.origin === "edited"
                ? "수정 당시 역할 미수집"
                : "작성 원문");
          node.innerHTML = `<div class="qa-section-head"><div>${tag("본문 버전 " + r.version, r.origin === "edited" ? "mint" : "")}<strong>${esc(role)}</strong></div><time>${esc(date(r.recordedAt))}</time></div>${r.before ? `<p class="qa-revision-reason"><strong>수정 이유</strong> ${esc(r.reason)}</p>${compare(snapshotText(r.before), snapshotText(r.after))}` : `<p class="form-note">${r.origin === "baseline" ? "수집 시점의 기준본입니다. 수집하지 않은 과거 수정자·이유는 표시하지 않습니다." : "처음 작성한 원문입니다."}</p><div class="p-user-body">${textBody(snapshotText(r.after))}</div>`}<div class="qa-history-files"><h3>당시 연결 자료 · 현재 제공 상태</h3>${fileLinks(r.afterAttachments)}</div>`;
          rows.append(node);
          for (const a of r.afterAttachments || [])
            if (!items.some((x) => x.id === a.id)) items.push(a);
        }
        packet = data;
        root.querySelector("[data-history-more]").hidden = !data.hasMore;
        root.querySelector("[data-history-message]").textContent =
          "현재 본문 버전 " + data.contentVersion;
      }
      async function load(clear) {
        if (busy) return;
        busy = true;
        viewer.invalidate();
        root.querySelectorAll("button").forEach((b) => (b.disabled = true));
        try {
          const p = await ctx.api(
            baseAPI(kind, target.id) +
              `/revisions?limit=10${!clear && packet.nextBeforeVersion ? "&beforeVersion=" + packet.nextBeforeVersion : ""}`,
            { ownerId: ctx.user.id },
          );
          if (active()) append(p, clear);
        } catch (error) {
          unavailable(error);
        } finally {
          busy = false;
          if (active())
            root
              .querySelectorAll("button")
              .forEach((b) => (b.disabled = false));
        }
      }
      root.querySelector("[data-history-more]").onclick = () => load(false);
      root.querySelector("[data-history-refresh]").onclick = () => load(true);
      const viewer = bindFileLinks(root, items, ctx, unavailable);
      append(initial);
    },
  };
}

export function contentActions(q, target, kind) {
  const aid = kind === "answer" ? target.id : null;
  return `<div class="qa-content-actions">${target.contentVersion > 1 ? tag("수정됨 · 버전 " + target.contentVersion) : ""}${target.canEdit ? `<a class="btn-text" href="${editPath(q.id, aid)}">${kind === "question" ? "질문 수정" : "답변 수정"}</a>` : ""}${target.canViewHistory ? `<a class="btn-text" href="${editPath(q.id, aid, "history")}">수정 이력</a>` : ""}</div>${fileLinks(target.contentAttachments)}`;
}
