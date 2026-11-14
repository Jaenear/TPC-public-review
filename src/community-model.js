/** Explicit conversation intents; no storage, automatic writes or HTML. */
const idPattern = /^[A-Za-z0-9_-]{1,150}$/;
const keyPattern = /^[A-Za-z0-9_-]{12,120}$/;
export const validConversationId = (id) => idPattern.test(id || "");
export const commentAPI = (id) => `/api/community/comments/${id}`;
export const postPath = (id, comment = null) =>
  `#/community/post/${id}${comment ? `?comment=${encodeURIComponent(comment)}` : ""}`;
export const commentPath = (pid, cid, action) =>
  `#/community/post/${pid}/comments/${cid}/${action}`;
export const replyPath = (pid, cid) => `#/community/post/${pid}/reply/${cid}`;

export function conversationGroups(comments = []) {
  const roots = new Map(),
    children = new Map();
  for (const item of comments) {
    if (!validConversationId(item.id)) continue;
    if (!item.parentId) roots.set(item.id, item);
    else {
      if (!validConversationId(item.parentId)) continue;
      if (!children.has(item.parentId)) children.set(item.parentId, []);
      children.get(item.parentId).push(item);
    }
  }
  for (const id of children.keys())
    if (!roots.has(id)) roots.set(id, { id, tombstone: true, parentId: null });
  return [...roots.values()].map((root) => ({
    root,
    replies: children.get(root.id) || [],
  }));
}

export function notificationCommentHref(notice) {
  const match = /^#\/community\/post\/([A-Za-z0-9_-]+)$/.exec(
    notice?.href || "",
  );
  return match &&
    notice.referenceAvailable &&
    notice.reference?.kind === "comment" &&
    validConversationId(notice.reference.id)
    ? postPath(match[1], notice.reference.id)
    : /^#\/(?!\/)[A-Za-z0-9_/?=&%.-]*$/.test(notice?.href || "")
      ? notice.href
      : "#/inbox";
}

const version = (n) => Number.isSafeInteger(n) && n > 0;
const text = (s, min, max) =>
  typeof s === "string" &&
  s.trim().length >= min &&
  s.trim().length <= max &&
  !s.includes("\0");

export class CommentIntent {
  constructor({
    ownerId,
    postId,
    kind = "create",
    target = null,
    key = () => crypto.randomUUID(),
  }) {
    if (
      !validConversationId(ownerId) ||
      !validConversationId(postId) ||
      !["create", "reply", "edit"].includes(kind) ||
      (kind !== "create" &&
        (!validConversationId(target?.id) || !version(target.contentVersion)))
    )
      throw new Error("대화 대상을 다시 확인해주세요.");
    Object.assign(this, {
      ownerId,
      postId,
      kind,
      targetId: target?.id || null,
      key,
    });
    this.base = target ? { ...target } : null;
    this.draft = { body: kind === "edit" ? target.body : "", reason: "" };
    this.pending = null;
    this.latest = null;
    this.state = "editing";
  }
  dirty() {
    return (
      !!this.draft.reason ||
      this.draft.body !== (this.kind === "edit" ? this.base.body : "")
    );
  }
  prepare() {
    if (this.state !== "editing")
      throw new Error("현재 저장 결과나 변경 내용을 먼저 확인해주세요.");
    if (
      !text(this.draft.body, 2, 5000) ||
      (this.kind === "edit" && !text(this.draft.reason, 3, 2000))
    )
      throw new Error("댓글 내용과 3자 이상의 수정 이유를 확인해주세요.");
    if (this.kind === "edit" && this.draft.body.trim() === this.base.body)
      throw new Error("수정한 내용을 확인해주세요.");
    const requestKey = this.key();
    if (!keyPattern.test(requestKey))
      throw new Error("저장 요청을 준비하지 못했습니다.");
    this.pending = Object.freeze({
      body: this.draft.body.trim(),
      requestKey,
      ...(this.kind === "reply"
        ? { parentId: this.targetId, parentVersion: this.base.contentVersion }
        : {}),
      ...(this.kind === "edit"
        ? {
            expectedVersion: this.base.contentVersion,
            reason: this.draft.reason.trim(),
          }
        : {}),
    });
    this.state = "preview";
    return this.pending;
  }
  change() {
    if (this.state === "preview") {
      this.pending = null;
      this.state = "editing";
    }
  }
  sending() {
    if (!this.pending || !["preview", "unknown"].includes(this.state))
      throw new Error("저장할 내용을 먼저 확인해주세요.");
    this.state = "saving";
    return this.pending;
  }
  uncertain() {
    if (this.pending) this.state = "unknown";
  }
  accepts(result) {
    const r = result?.receipt,
      c = result?.comment,
      p = this.pending;
    return !!(
      p &&
      r &&
      c &&
      r.kind === (this.kind === "edit" ? "edit" : "create") &&
      r.requestKey === p.requestKey &&
      r.postId === this.postId &&
      validConversationId(r.commentId) &&
      r.commentId === c.id &&
      c.postId === this.postId &&
      c.userId === this.ownerId &&
      r.contentVersion === (this.kind === "edit" ? p.expectedVersion + 1 : 1) &&
      validConversationId(r.id) &&
      validConversationId(r.revisionId) &&
      version(c.contentVersion) &&
      c.contentVersion >= r.contentVersion &&
      (this.kind !== "edit" ||
        (r.commentId === this.targetId &&
          r.parentId === this.base.parentId &&
          c.parentId === this.base.parentId)) &&
      (this.kind !== "reply" ||
        (r.parentId === this.targetId &&
          c.parentId === this.targetId &&
          r.parentContentVersion === p.parentVersion)) &&
      (this.kind !== "create" || (r.parentId === null && c.parentId === null))
    );
  }
  accept(result) {
    if (!this.accepts(result))
      throw new Error(
        "저장 영수증이 요청과 다릅니다. 같은 요청으로 결과를 다시 확인해주세요.",
      );
    this.state = "saved";
    this.pending = null;
    return result.comment;
  }
  acceptUnavailable(result) {
    const r = result?.receipt;
    if (
      result?.currentAvailable !== false ||
      result.comment !== null ||
      !r ||
      !this.accepts({
        receipt: r,
        comment: {
          id: r.commentId,
          postId: r.postId,
          userId: this.ownerId,
          parentId: r.parentId,
          contentVersion: r.contentVersion,
        },
      })
    )
      throw new Error("본인의 저장 영수증을 다시 확인해주세요.");
    this.state = "saved";
    this.pending = null;
    return r;
  }
  conflict(target) {
    if (
      this.kind !== "create" &&
      (target?.id !== this.targetId || !version(target.contentVersion))
    )
      throw new Error("현재 댓글을 확인할 수 없습니다.");
    this.latest = target;
    this.pending = null;
    this.state = "conflict";
  }
  rebase(useLatestBody = false) {
    if (this.state !== "conflict" || (this.kind !== "create" && !this.latest))
      throw new Error("최신 내용을 먼저 확인해주세요.");
    if (this.latest) {
      this.base = { ...this.latest };
      if (useLatestBody && this.kind === "edit")
        this.draft.body = this.latest.body;
    }
    this.latest = null;
    this.state = "editing";
  }
  rejected() {
    this.pending = null;
    this.state = "editing";
  }
  export() {
    return this.pending && ["saving", "unknown"].includes(this.state)
      ? {
          ownerId: this.ownerId,
          postId: this.postId,
          kind: this.kind,
          targetId: this.targetId,
          pending: { ...this.pending },
        }
      : null;
  }
  restore(packet) {
    if (
      !packet ||
      packet.ownerId !== this.ownerId ||
      packet.postId !== this.postId ||
      packet.kind !== this.kind ||
      packet.targetId !== this.targetId
    )
      return false;
    const p = packet.pending;
    if (
      !p ||
      !keyPattern.test(p.requestKey || "") ||
      !text(p.body, 2, 5000) ||
      p.body !== this.draft.body.trim()
    )
      return false;
    const allowed =
      this.kind === "edit"
        ? ["body", "requestKey", "expectedVersion", "reason"]
        : this.kind === "reply"
          ? ["body", "requestKey", "parentId", "parentVersion"]
          : ["body", "requestKey"];
    if (
      Object.keys(p).length !== allowed.length ||
      Object.keys(p).some((k) => !allowed.includes(k))
    )
      return false;
    if (
      this.kind === "edit" &&
      (!version(p.expectedVersion) ||
        p.expectedVersion > this.base.contentVersion ||
        !text(p.reason, 3, 2000) ||
        p.reason !== this.draft.reason.trim())
    )
      return false;
    if (
      this.kind === "reply" &&
      (p.parentId !== this.targetId ||
        !version(p.parentVersion) ||
        p.parentVersion > this.base.contentVersion)
    )
      return false;
    this.pending = Object.freeze({ ...p });
    this.state = "unknown";
    return true;
  }
}

export class CommunityBlockIntent {
  constructor({ ownerId, block, key = () => crypto.randomUUID() }) {
    Object.assign(this, {
      ownerId,
      targetId: block.targetId,
      base: { ...block },
      key,
    });
    this.pending = null;
    this.state = "editing";
  }
  prepare(blocked) {
    if (this.state !== "editing" || typeof blocked !== "boolean")
      throw new Error("현재 차단 설정을 먼저 확인해주세요.");
    const requestKey = this.key();
    if (!keyPattern.test(requestKey))
      throw new Error("저장 요청을 준비하지 못했습니다.");
    this.pending = Object.freeze({
      blocked,
      expectedVersion: this.base.version,
      requestKey,
    });
    this.state = "preview";
    return this.pending;
  }
  sending() {
    if (!this.pending || !["preview", "unknown"].includes(this.state))
      throw new Error("차단 요청을 확인해주세요.");
    this.state = "saving";
    return this.pending;
  }
  accept(result) {
    const r = result?.receipt,
      b = result?.currentBlock,
      p = this.pending;
    if (
      !p ||
      r?.targetId !== this.targetId ||
      r.requestKey !== p.requestKey ||
      r.version !== p.expectedVersion + 1 ||
      r.blocked !== p.blocked ||
      b?.targetId !== this.targetId ||
      !Number.isSafeInteger(b.version) ||
      b.version < r.version ||
      typeof b.blocked !== "boolean"
    )
      throw new Error("차단 저장 결과를 같은 요청으로 다시 확인해주세요.");
    this.base = { ...b };
    this.pending = null;
    this.state = "saved";
    return b;
  }
  uncertain() {
    if (this.pending) this.state = "unknown";
  }
  refresh(block) {
    if (this.state === "unknown" || this.state === "saving")
      throw new Error("기존 요청의 결과를 먼저 확인해주세요.");
    if (block.targetId !== this.targetId)
      throw new Error("차단 대상을 다시 확인해주세요.");
    this.base = { ...block };
    this.pending = null;
    this.state = "editing";
  }
  export() {
    return this.pending && ["unknown", "saving"].includes(this.state)
      ? {
          ownerId: this.ownerId,
          targetId: this.targetId,
          pending: { ...this.pending },
        }
      : null;
  }
  restore(packet) {
    const p = packet?.pending;
    if (
      packet?.ownerId !== this.ownerId ||
      packet?.targetId !== this.targetId ||
      !p ||
      Object.keys(p).length !== 3 ||
      Object.keys(p).some(
        (k) => !["blocked", "expectedVersion", "requestKey"].includes(k),
      ) ||
      typeof p.blocked !== "boolean" ||
      !Number.isSafeInteger(p.expectedVersion) ||
      p.expectedVersion < 0 ||
      p.expectedVersion > this.base.version ||
      !keyPattern.test(p.requestKey || "")
    )
      return false;
    this.pending = Object.freeze({ ...p });
    this.state = "unknown";
    return true;
  }
}
