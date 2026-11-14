import { esc } from "./ui.js";
import { attachmentId } from "./qa-model.js";

export const fileStatus = (status) =>
  ({
    pending: "검사 대기",
    approved: "검사 통과",
    rejected: "검사 거절",
    blocked: "이용 중단",
    deleted: "삭제됨",
  })[status] || "이용 불가";
const sizeLabel = (size) =>
  size >= 1048576
    ? `${(size / 1048576).toFixed(1)} MB`
    : `${Math.ceil((size || 0) / 1024)} KB`;
export function fileLinks(items = []) {
  if (!items.length) return "";
  return `<ul class="qa-file-links">${items.map((item) => `<li><span>${esc(item.filename || "현재 이용할 수 없는 자료")}</span>${item.available ? `<small>${esc(sizeLabel(item.size))}</small><button type="button" class="btn-text" data-qa-file="${esc(item.id)}" data-file-action="view">내용 보기</button><button type="button" class="btn-text" data-qa-file="${esc(item.id)}" data-file-action="download">다운로드</button>` : "<small>이용 불가</small>"}</li>`).join("")}</ul>`;
}
export function bindFileLinks(root, items, ctx, onFailure = () => {}) {
  const ownerId = ctx.user.id,
    route = location.hash;
  let generation = 0;
  const active = () =>
    root.isConnected &&
    !root.hidden &&
    root.dataset.qaUnavailable !== "true" &&
    location.hash === route;
  root.addEventListener("click", async (event) => {
    const button = event.target.closest?.("[data-qa-file]");
    if (!button || !root.contains(button)) return;
    const item = items.find((a) => a.id === button.dataset.qaFile);
    if (
      !item?.available ||
      !attachmentId(item.id) ||
      item.href !== `/api/qa/attachments/${item.id}/file`
    )
      return;
    const mine = ++generation;
    const preview = root.querySelector("[data-file-preview]");
    if (preview) preview.replaceChildren();
    button.disabled = true;
    try {
      const blob = await ctx.api(item.href, { ownerId, responseType: "blob" });
      if (!active() || mine !== generation) return;
      if (!(blob instanceof Blob) || blob.size !== item.size)
        throw new Error(
          "자료 크기가 일치하지 않습니다. 목록을 다시 확인해주세요.",
        );
      if (button.dataset.fileAction === "view") {
        const text = await blob.text();
        if (!active() || mine !== generation) return;
        const box =
          preview || root.appendChild(document.createElement("section"));
        box.dataset.filePreview = "";
        box.className = "qa-file-preview";
        const title = document.createElement("h3");
        title.textContent = item.filename;
        const pre = document.createElement("pre");
        pre.textContent = text.slice(0, 20000);
        const close = document.createElement("button");
        close.type = "button";
        close.className = "btn-text";
        close.textContent = "자료 미리보기 닫기";
        close.onclick = () => {
          generation++;
          box.replaceChildren();
          button.focus();
        };
        box.replaceChildren(title, pre, close);
        box.hidden = false;
        if (text.length > 20000) {
          const note = document.createElement("p");
          note.textContent =
            "미리보기는 처음 20,000자입니다. 전체 내용은 다운로드로 확인하세요.";
          box.append(note);
        }
      } else {
        const url = URL.createObjectURL(
          new Blob([blob], { type: "application/octet-stream" }),
        );
        const a = document.createElement("a");
        a.href = url;
        a.download = String(item.filename || "학습 자료").replace(
          /[\\/\x00-\x1f]/g,
          "_",
        );
        a.click();
        setTimeout(() => URL.revokeObjectURL(url), 1000);
      }
    } catch (error) {
      if (!active()) return;
      const denied =
        [401, 403, 404].includes(error.status) || error.sessionChanged;
      // An older transient failure must not erase a newer successful view.
      // Known loss of access invalidates every in-flight view of this scope.
      if (!denied && mine !== generation) return;
      if (denied) document.title = "강의 Q/A | TPC";
      generation++;
      root
        .querySelectorAll("[data-file-preview]")
        .forEach((node) => node.replaceChildren());
      root
        .querySelectorAll("[data-qa-file]")
        .forEach((node) => (node.disabled = Boolean(denied)));
      await onFailure(error);
    } finally {
      if (active() && mine === generation) button.disabled = false;
    }
  });
  return {
    invalidate() {
      generation++;
      root
        .querySelectorAll("[data-file-preview]")
        .forEach((node) => node.replaceChildren());
    },
  };
}

export function attachmentManager(
  root,
  questionId,
  data,
  ctx,
  { selected, baseRefs, onSelection, refresh, fail, locked = () => false },
) {
  const box = root.querySelector("[data-qa-manager]"),
    ownerId = ctx.user.id,
    route = location.hash;
  let current = data,
    pending = null,
    file = null,
    busy = false,
    preparing = false;
  const active = () =>
    root.isConnected &&
    !root.hidden &&
    root.dataset.qaUnavailable !== "true" &&
    location.hash === route;
  const note = (message) => {
    if (active())
      box.querySelector("[data-file-message]").textContent = message;
  };
  function render() {
    // A rebuilt native file input is empty. Keep the transport selection in
    // step with what the member can actually see and explicitly select.
    file = null;
    box.innerHTML = `<div class="qa-section-head"><div><h2>참고 자료</h2><p>검사를 통과한 자료를 선택하고 본문과 함께 저장하세요.</p></div><button type="button" class="btn-text" data-file-refresh>자료 새로 확인</button></div><div class="qa-file-options">${
      current.attachments
        .map(
          (item) =>
            `<article><div><strong>${esc(item.filename || "이용할 수 없는 자료")}</strong><small>${esc(fileStatus(item.status))}${item.size ? " · " + esc(sizeLabel(item.size)) : ""}</small></div>${baseRefs().includes(item.id) || item.canReference ? `<label class="qa-select"><input type="checkbox" name="reference_${esc(item.id)}" value="${esc(item.id)}" data-reference="${esc(item.id)}" ${selected().has(item.id) ? "checked" : ""}>본문에 연결</label>` : ""}<div class="qa-file-ops">${[
              ...[
                item.canInspect ? "inspect" : null,
                item.canCancel ? "cancel" : null,
              ],
            ]
              .filter(Boolean)
              .map(
                (action) =>
                  `<button type="button" class="btn-text" data-file-op="${action}" data-id="${esc(item.id)}">${action === "inspect" ? "검사 요청" : "임시 자료 취소"}</button>`,
              )
              .join("")}</div></article>`,
        )
        .join("") || '<p class="p-muted">등록한 참고 자료가 없습니다.</p>'
    }</div><p class="qa-policy-note">${current.upload.ready ? `허용 형식 ${esc(current.upload.accept.join(", "))} · 파일당 ${esc(sizeLabel(current.upload.maxBytes))}. 업로드한 자료는 검사 뒤 연결할 수 있습니다.` : "현재 자료 업로드를 준비 중입니다. 본문 수정과 기존 자료 확인은 이용할 수 있습니다."}</p><label class="field">참고 파일<input type="file" name="qaFile" accept="${esc(current.upload.accept.join(","))}" ${current.upload.ready ? "" : "disabled"}></label><button class="btn btn-outline btn-sm" type="button" data-file-upload ${current.upload.ready ? "" : "disabled"}>파일 업로드 · 검사 대기</button><p class="form-note" data-file-message role="status"></p><button type="button" class="btn btn-outline btn-sm" data-file-retry hidden>같은 자료 작업 결과 다시 확인</button><div data-file-preview></div>`;
    controls();
  }
  function controls() {
    if (!active()) return;
    box
      .querySelectorAll(
        "[data-file-op],[data-file-upload],[data-file-refresh],input",
      )
      .forEach((node) => {
        node.disabled =
          locked() ||
          busy ||
          preparing ||
          Boolean(pending) ||
          (node.matches("[type=file],[data-file-upload]") &&
            !current.upload.ready);
      });
    const retry = box.querySelector("[data-file-retry]");
    retry.hidden = !pending;
    retry.disabled = busy || preparing || locked();
    if (
      pending?.action === "upload" &&
      !file &&
      !busy &&
      !preparing &&
      !locked() &&
      current.upload.ready
    )
      box.querySelector("[type=file]").disabled = false;
  }
  async function send(retry = false) {
    if (locked() || busy || preparing || (!retry && pending)) return;
    if (!pending) return;
    if (pending.action === "upload") {
      if (file && (file.size < 1 || file.size > 8388608))
        return note("허용 크기의 원본을 선택해주세요.");
      if (!file) {
        note("같은 파일을 다시 선택한 뒤 결과를 확인해주세요.");
        box.querySelector("[type=file]").disabled = false;
        return;
      }
      preparing = true;
      controls();
      let sha;
      try {
        const bytes = await file.arrayBuffer();
        sha = [...new Uint8Array(await crypto.subtle.digest("SHA-256", bytes))]
          .map((n) => n.toString(16).padStart(2, "0"))
          .join("");
      } catch {
        note("원본 파일을 읽지 못했습니다. 같은 파일을 다시 선택해주세요.");
        file = null;
        box.querySelector("[type=file]").value = "";
        return;
      } finally {
        preparing = false;
        controls();
      }
      if (
        file.name !== pending.name ||
        file.size !== pending.size ||
        sha !== pending.sha
      ) {
        note(
          "확인 중인 원본과 다른 파일입니다. 같은 이름과 내용의 파일을 선택해주세요.",
        );
        return;
      }
    }
    if (!active() || locked() || busy) return;
    busy = true;
    controls();
    note("자료 작업을 확인하고 있습니다.");
    try {
      const result = await ctx.api(
        pending.action === "upload"
          ? `/api/qa/questions/${questionId}/attachments`
          : `/api/qa/attachments/${pending.id}/${pending.action}`,
        pending.action === "upload"
          ? {
              method: "POST",
              ownerId,
              rawBody: file,
              headers: {
                "Content-Type": file.name.toLowerCase().endsWith(".json")
                  ? "application/json"
                  : "text/plain",
                "X-File-Name": encodeURIComponent(file.name),
                "X-Request-Key": pending.requestKey,
              },
            }
          : {
              method: "POST",
              ownerId,
              body: {
                requestKey: pending.requestKey,
                expectedVersion: pending.version,
              },
            },
      );
      if (!active()) return;
      if (
        result.attachment?.requestKey !== pending.requestKey ||
        (pending.id && result.attachment.id !== pending.id)
      )
        throw new Error(
          "자료 작업의 저장 결과를 확인할 수 없습니다. 같은 요청으로 다시 확인해주세요.",
        );
      pending = null;
      file = null;
      current = await refresh();
      if (!active()) return;
      render();
      note(
        "자료 상태를 확인했습니다. 본문에 연결할 자료를 선택한 뒤 수정 내용을 저장해주세요.",
      );
    } catch (error) {
      if (!active()) return;
      if ([400, 409, 413, 415, 429].includes(error.status)) {
        pending = null;
        file = null;
        box.querySelector("[type=file]").value = "";
        note(error.message);
      } else if (
        [401, 403, 404].includes(error.status) ||
        error.sessionChanged
      ) {
        file = null;
        fail(error);
      } else
        note(
          "처리 결과를 확인하지 못했습니다. 같은 작업으로 다시 확인해주세요. 파일 업로드라면 같은 원본을 다시 선택하세요.",
        );
    } finally {
      busy = false;
      controls();
    }
  }
  box.addEventListener("change", (event) => {
    if (event.target.type === "file") file = event.target.files?.[0] || null;
    if (event.target.dataset.reference)
      onSelection(event.target.dataset.reference, event.target.checked);
  });
  box.addEventListener("click", async (event) => {
    const button = event.target.closest("button");
    if (!button || !box.contains(button)) return;
    if (locked() || busy || preparing) return;
    if (button.hasAttribute("data-file-retry")) return send(true);
    if (button.hasAttribute("data-file-refresh")) {
      busy = true;
      controls();
      note("자료 상태를 확인하고 있습니다.");
      try {
        current = await refresh();
        if (active()) {
          render();
          note("자료 상태를 확인했습니다. 업로드할 파일은 다시 선택해주세요.");
        }
      } catch (error) {
        fail(error);
      } finally {
        busy = false;
        controls();
      }
      return;
    }
    if (button.hasAttribute("data-file-upload")) {
      if (!file || !current.upload.ready)
        return note("업로드할 파일을 선택해주세요.");
      if (file.size < 1 || file.size > current.upload.maxBytes)
        return note("파일 크기를 확인하고 허용 범위의 원본을 선택해주세요.");
      preparing = true;
      controls();
      let sha;
      try {
        const bytes = await file.arrayBuffer();
        sha = [...new Uint8Array(await crypto.subtle.digest("SHA-256", bytes))]
          .map((n) => n.toString(16).padStart(2, "0"))
          .join("");
      } catch {
        note("파일을 읽지 못했습니다. 원본을 다시 선택해주세요.");
        file = null;
        box.querySelector("[type=file]").value = "";
        return;
      } finally {
        preparing = false;
        controls();
      }
      if (!active() || locked()) return;
      pending = {
        action: "upload",
        requestKey: crypto.randomUUID(),
        name: file.name,
        size: file.size,
        sha,
      };
    } else if (button.dataset.fileOp) {
      const item = current.attachments.find((a) => a.id === button.dataset.id);
      if (!item) return;
      pending = {
        action: button.dataset.fileOp,
        id: item.id,
        version: item.version,
        requestKey: crypto.randomUUID(),
      };
    } else return;
    await send(true);
  });
  render();
  return {
    sync: controls,
    snapshot: () => (pending ? { ownerId, questionId, pending } : null),
    restore(packet) {
      if (
        packet?.ownerId !== ownerId ||
        packet?.questionId !== questionId ||
        !/^[A-Za-z0-9_-]{12,120}$/.test(packet.pending?.requestKey || "") ||
        !["upload", "inspect", "cancel"].includes(packet.pending?.action)
      )
        return;
      pending = packet.pending;
      file = null;
      controls();
      note(
        "이전 자료 작업 결과를 같은 요청으로 확인할 수 있습니다. 업로드는 같은 파일을 다시 선택해주세요.",
      );
    },
    update(data) {
      current = data;
      render();
    },
    busy: () => busy || preparing || Boolean(pending),
    current: () => current,
  };
}
