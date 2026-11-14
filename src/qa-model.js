/** Explicit edits, immutable retries and user-approved three-way rebasing. */
export const attachmentId = (id) =>
  /^qaattachment_[a-f0-9]{32}$/.test(id || "");
export const refs = (target) =>
  (target.contentAttachments || []).map((a) => a.id).filter(attachmentId);
export const sorted = (values) => [...new Set(values)].sort();
export function targetOf(question, answerId = null) {
  const target = answerId
    ? question.answers.find((a) => a.id === answerId)
    : question;
  if (!target) throw new Error("현재 질문의 답변을 찾을 수 없습니다.");
  return target;
}
export function editPath(qid, aid = null, page = "edit") {
  return `#/qa/${qid}${aid ? `/answers/${aid}` : ""}/${page}`;
}
export function diffLines(before = "", after = "") {
  const left = String(before).split("\n"),
    right = String(after).split("\n");
  // Bound the comparison: a large paragraph still gets an exact side-by-side
  // view, without quadratic work proportional to an arbitrary line count.
  if (left.length * right.length > 40000)
    return {
      before: left.map((text) => ({ text, changed: before !== after })),
      after: right.map((text) => ({ text, changed: before !== after })),
    };
  const table = Array.from(
    { length: left.length + 1 },
    () => new Uint16Array(right.length + 1),
  );
  for (let i = left.length - 1; i >= 0; i--)
    for (let j = right.length - 1; j >= 0; j--)
      table[i][j] =
        left[i] === right[j]
          ? table[i + 1][j + 1] + 1
          : Math.max(table[i + 1][j], table[i][j + 1]);
  const l = [],
    r = [];
  let i = 0,
    j = 0;
  while (i < left.length || j < right.length) {
    if (i < left.length && j < right.length && left[i] === right[j]) {
      l.push({ text: left[i++], changed: false });
      r.push({ text: right[j++], changed: false });
    } else if (
      j >= right.length ||
      (i < left.length && table[i + 1][j] >= table[i][j + 1])
    )
      l.push({ text: left[i++], changed: true });
    else r.push({ text: right[j++], changed: true });
  }
  return { before: l, after: r };
}

export class QAEditIntent {
  constructor({
    ownerId,
    questionId,
    kind,
    target,
    key = () => crypto.randomUUID(),
  }) {
    Object.assign(this, { ownerId, questionId, kind, key });
    this.targetId = target.id;
    this.setBase(target);
    this.draft = { title: target.title || "", body: target.body, reason: "" };
    this.selected = new Set(this.base.refs);
    this.pending = null;
    this.state = "editing";
  }
  setBase(target) {
    this.base = {
      version: target.contentVersion,
      title: target.title || "",
      body: target.body,
      refs: refs(target),
    };
  }
  dirty() {
    return (
      this.draft.body !== this.base.body ||
      (this.kind === "question" && this.draft.title !== this.base.title) ||
      JSON.stringify(sorted(this.selected)) !==
        JSON.stringify(sorted(this.base.refs)) ||
      Boolean(this.draft.reason.trim())
    );
  }
  prepare() {
    if (
      ["blocked", "unknown", "saving", "conflict", "saved"].includes(this.state)
    )
      throw new Error("현재 저장 상태를 먼저 확인해주세요.");
    const body = this.draft.body.trim(),
      reason = this.draft.reason.trim(),
      title = this.draft.title.trim();
    if (
      body.length < (this.kind === "question" ? 10 : 2) ||
      body.length > 10000 ||
      reason.length < 5 ||
      reason.length > 2000 ||
      (this.kind === "question" && (title.length < 3 || title.length > 150))
    )
      throw new Error("제목·본문과 5자 이상의 수정 이유를 확인해주세요.");
    const add = sorted(
      [...this.selected].filter((id) => !this.base.refs.includes(id)),
    );
    const detach = sorted(
      this.base.refs.filter((id) => !this.selected.has(id)),
    );
    if (
      body === this.base.body &&
      (this.kind !== "question" || title === this.base.title) &&
      !add.length &&
      !detach.length
    )
      throw new Error("변경한 내용을 확인해주세요.");
    this.pending = Object.freeze({
      requestKey: this.key(),
      expectedVersion: this.base.version,
      body,
      reason,
      ...(this.kind === "question" ? { title } : {}),
      attachments: Object.freeze({
        add: Object.freeze(add),
        detach: Object.freeze(detach),
      }),
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
      throw new Error("미리보기 또는 저장 재확인이 필요합니다.");
    this.state = "saving";
    return this.pending;
  }
  uncertain() {
    this.state = "unknown";
  }
  accepts(result) {
    const receipt = result?.edit,
      body = this.pending;
    return Boolean(
      body &&
        receipt?.id === this.targetId &&
        receipt.questionId === this.questionId &&
        receipt.requestKey === body.requestKey &&
        receipt.contentVersion === body.expectedVersion + 1 &&
        typeof receipt.revisionId === "string" &&
        Number.isInteger(result.currentContentVersion) &&
        result.currentContentVersion >= receipt.contentVersion,
    );
  }
  accept(result) {
    if (!this.accepts(result))
      throw new Error(
        "저장 결과가 요청과 일치하지 않습니다. 같은 요청으로 다시 확인해주세요.",
      );
    this.state = "saved";
    this.pending = null;
  }
  conflict(target) {
    if (target.id !== this.targetId || !Number.isInteger(target.contentVersion))
      throw new Error("현재 원문을 확인할 수 없습니다.");
    this.latest = target;
    this.pending = null;
    this.state = "conflict";
  }
  rebase(candidates = []) {
    if (this.state !== "conflict" || !this.latest)
      throw new Error("최신 원문을 먼저 확인해주세요.");
    const old = this.base,
      latest = this.latest;
    const additions = [...this.selected].filter((id) => !old.refs.includes(id));
    const removals = old.refs.filter((id) => !this.selected.has(id));
    const available = new Set([
      ...refs(latest),
      ...candidates.filter((a) => a.canReference).map((a) => a.id),
    ]);
    if (this.draft.body === old.body) this.draft.body = latest.body;
    if (this.draft.title === old.title) this.draft.title = latest.title || "";
    this.setBase(latest);
    this.selected = new Set([
      ...this.base.refs.filter((id) => !removals.includes(id)),
      ...additions.filter((id) => available.has(id)),
    ]);
    this.latest = null;
    this.state = "editing";
  }
  export() {
    return this.pending && ["saving", "unknown"].includes(this.state)
      ? {
          ownerId: this.ownerId,
          questionId: this.questionId,
          kind: this.kind,
          targetId: this.targetId,
          version: this.base.version,
          pending: this.pending,
        }
      : null;
  }
  restore(packet) {
    if (
      !packet ||
      packet.ownerId !== this.ownerId ||
      packet.questionId !== this.questionId ||
      packet.kind !== this.kind ||
      packet.targetId !== this.targetId ||
      packet.version !== this.base.version ||
      packet.pending?.expectedVersion !== this.base.version ||
      !/^[A-Za-z0-9_-]{12,120}$/.test(packet.pending?.requestKey || "")
    )
      return false;
    const p = packet.pending;
    if (
      typeof p.body !== "string" ||
      typeof p.reason !== "string" ||
      !Array.isArray(p.attachments?.add) ||
      !Array.isArray(p.attachments?.detach) ||
      [...p.attachments.add, ...p.attachments.detach].some(
        (id) => !attachmentId(id),
      )
    )
      return false;
    if (
      p.body !== this.draft.body.trim() ||
      p.reason !== this.draft.reason.trim() ||
      (this.kind === "question" && p.title !== this.draft.title.trim())
    )
      return false;
    this.pending = Object.freeze({
      ...p,
      attachments: Object.freeze({
        add: Object.freeze([...p.attachments.add]),
        detach: Object.freeze([...p.attachments.detach]),
      }),
    });
    this.selected = new Set([
      ...this.base.refs.filter((id) => !p.attachments.detach.includes(id)),
      ...p.attachments.add,
    ]);
    this.state = "unknown";
    return true;
  }
}
