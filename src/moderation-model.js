/** Explicit, owner-bound moderation requests. A retry never gets a new key. */
export const actionLabel = (value) =>
  ({
    hide: "게시글 숨김",
    restore: "게시글 복원",
    lock: "댓글 잠금",
    unlock: "댓글 잠금 해제",
    pin: "상단 공지 고정",
    unpin: "고정 해제",
    comment_delete: "댓글 운영 삭제",
    comment_restore: "댓글 복원",
    report_dismiss: "신고 종결",
    submit: "이의 접수",
    assign: "검토자 배정",
    recheck: "최신 근거 검토 확인",
    accept: "이의 인용·제한 해제",
    reject: "이의 기각",
    withdraw: "이의 철회",
    submitted: "이의 접수",
    assigned: "검토자 배정",
    rechecked: "최신 근거 검토 확인",
    accepted: "인용",
    rejected: "기각",
    withdrawn: "철회",
    conflict: "근거 변경·추가 검토",
  })[value] || value;
export const appealLabel = (value) =>
  ({
    submitted: "미배정",
    reviewing: "검토 중",
    accepted: "인용 완료",
    rejected: "기각 완료",
    withdrawn: "철회 완료",
  })[value] || value;
export const staffMember = (user) =>
  user?.role === "admin" || user?.roles?.includes("moderator");
export const moderationPath = (staff = false, entity = "actions", id = "") =>
  `${staff ? "/manage" : "/community"}/moderation/${entity}${id ? "/" + id : ""}`;
export const moderationAPI = (staff = false) =>
  `/api/${staff ? "manage" : "community"}/moderation`;
const ordered = (value) =>
  value && typeof value === "object"
    ? Array.isArray(value)
      ? value.map(ordered)
      : Object.fromEntries(
          Object.keys(value)
            .sort()
            .map((k) => [k, ordered(value[k])]),
        )
    : value;
export const canonical = (value) => JSON.stringify(ordered(value));
export const same = (left, right) => canonical(left) === canonical(right);
export const sameReviewBasis = (left, right) => {
  const basis = (item) =>
    Object.fromEntries(
      [
        "id",
        "actionId",
        "appellantId",
        "version",
        "resultVersion",
        "status",
        "reviewerId",
        "canAppeal",
        "canAssign",
        "canDecide",
        "canWithdraw",
        "appeal",
        "needsReview",
        "currentTarget",
        "currentTargetHash",
        "currentPolicy",
        "reviewTarget",
        "reviewPolicy",
      ].map((key) => [key, item?.[key]]),
    );
  return same(basis(left), basis(right));
};
export const needsReview = (item) =>
  ["submitted", "reviewing"].includes(item.status) &&
  Boolean(
    item.needsReview ||
      !same(item.reviewTarget, item.currentTarget) ||
      !same(item.reviewPolicy, item.currentPolicy),
  );
export function availableActions(item, kind, staff = false) {
  if (kind === "action") return !staff && item.canAppeal ? ["submit"] : [];
  if (!["submitted", "reviewing"].includes(item.status)) return [];
  if (!staff) return item.canWithdraw ? ["withdraw"] : [];
  return [
    ...(item.canDecide
      ? needsReview(item)
        ? ["recheck", "reject"]
        : ["accept", "reject", "recheck"]
      : []),
    ...(item.canAssign ? ["assign"] : []),
  ];
}
const copy = (value) => JSON.parse(JSON.stringify(value));
const requestKey = (value) => /^[A-Za-z0-9_-]{12,120}$/.test(value || "");
const decisionId = (value) => /^moddecision_[a-z0-9_]+$/.test(value || "");

export class ModerationIntent {
  constructor({
    ownerId,
    item,
    kind,
    staff = false,
    key = () => crypto.randomUUID(),
  }) {
    Object.assign(this, { ownerId, kind, staff, key });
    this.item = copy(item);
    this.targetId = item.id;
    this.state = "editing";
    this.draft = {
      action: availableActions(item, kind, staff)[0] || "",
      reason: "",
      reviewerId: "",
    };
    this.pending = null;
    this.latest = null;
    this.receipt = null;
  }
  version() {
    return this.kind === "action" ? this.item.resultVersion : this.item.version;
  }
  prepare() {
    if (this.state !== "editing")
      throw new Error("현재 요청 결과 또는 최신 상태를 먼저 확인해주세요.");
    const { action, reviewerId } = this.draft,
      reason = this.draft.reason.trim();
    if (!availableActions(this.item, this.kind, this.staff).includes(action))
      throw new Error("현재 가능한 처리 항목을 다시 확인해주세요.");
    if (reason.length < 5 || reason.length > 2000)
      throw new Error("사유를 5~2000자로 입력해주세요.");
    if (action === "assign" && !reviewerId)
      throw new Error(
        "최초 조치자·작성자·신고자와 분리된 검토자를 선택해주세요.",
      );
    const data = {
      expectedVersion: this.version(),
      reason,
      requestKey: this.key(),
      ...(action !== "submit" ? { action } : {}),
    };
    if (!requestKey(data.requestKey))
      throw new Error(
        "요청 식별자를 만들지 못했습니다. 화면을 다시 열어주세요.",
      );
    if (action === "assign") data.reviewerId = reviewerId;
    if (action === "recheck")
      Object.assign(data, {
        expectedTargetVersion: this.item.currentTarget.version,
        expectedContentHash: this.item.currentTarget.contentHash,
        expectedTargetHash: this.item.currentTargetHash,
        policyId: this.item.currentPolicy?.id ?? null,
      });
    this.pending = Object.freeze(data);
    this.state = "preview";
    return this.pending;
  }
  change() {
    if (this.state !== "preview") return;
    this.pending = null;
    this.state = "editing";
  }
  sending() {
    if (!this.pending || !["preview", "unknown"].includes(this.state))
      throw new Error("확인할 저장 요청이 없습니다.");
    this.state = "saving";
    return this.pending;
  }
  uncertain() {
    if (this.pending) this.state = "unknown";
  }
  accepts(result, conflict = false) {
    const r = result?.receipt,
      current = result?.currentAppeal,
      p = this.pending;
    if (
      !r ||
      !current ||
      !p ||
      !Number.isInteger(r.version) ||
      !Number.isInteger(current.version) ||
      r.requestKey !== p.requestKey ||
      !decisionId(r.id) ||
      r.appealId !== current.id ||
      current.version < r.version
    )
      return false;
    if (this.kind === "action")
      return (
        !conflict &&
        r.version === 1 &&
        r.status === "submitted" &&
        current.actionId === this.targetId &&
        current.appellantId === this.ownerId
      );
    const status = {
      assign: "reviewing",
      recheck: "reviewing",
      accept: conflict ? "reviewing" : "accepted",
      reject: "rejected",
      withdraw: "withdrawn",
    }[p.action];
    return (
      current.id === this.targetId &&
      current.actionId === this.item.actionId &&
      current.appellantId === this.item.appellantId &&
      r.version === p.expectedVersion + 1 &&
      r.status === status &&
      (!conflict || (p.action === "accept" && result.conflict === true))
    );
  }
  accept(result, conflict = false) {
    if (!this.accepts(result, conflict))
      throw new Error(
        "저장 영수증을 확인하지 못했습니다. 같은 요청으로 결과를 다시 확인해주세요.",
      );
    this.receipt = copy(result.receipt);
    this.pending = null;
    this.latest = copy(result.currentAppeal);
    this.state = conflict ? "conflict" : "saved";
  }
  conflict(latest) {
    if (!latest || latest.id !== this.targetId)
      throw new Error("현재 대상의 상태를 확인해주세요.");
    this.latest = copy(latest);
    this.pending = null;
    this.state = "conflict";
  }
  rebase(latest = this.latest) {
    if (this.state !== "conflict" || !latest || latest.id !== this.targetId)
      throw new Error("최신 상태를 먼저 조회해주세요.");
    this.item = copy(latest);
    const options = availableActions(latest, this.kind, this.staff);
    if (!options.includes(this.draft.action))
      this.draft.action = options[0] || "";
    this.latest = null;
    this.receipt = null;
    this.state = "editing";
  }
  blocked() {
    this.state = "blocked";
    this.pending = null;
    this.latest = null;
    this.item = null;
    this.draft = { action: "", reason: "", reviewerId: "" };
  }
  export() {
    return this.pending && ["saving", "unknown"].includes(this.state)
      ? {
          ownerId: this.ownerId,
          targetId: this.targetId,
          kind: this.kind,
          staff: this.staff,
          pending: this.pending,
        }
      : null;
  }
  restore(packet) {
    const p = packet?.pending;
    if (
      packet?.ownerId !== this.ownerId ||
      packet?.targetId !== this.targetId ||
      packet?.kind !== this.kind ||
      packet?.staff !== this.staff ||
      !p ||
      p.expectedVersion !== this.version() ||
      !requestKey(p.requestKey) ||
      p.reason !== this.draft.reason.trim()
    )
      return false;
    const permitted = [
      "requestKey",
      "expectedVersion",
      "reason",
      ...(this.kind === "appeal" ? ["action"] : []),
      ...(p.action === "assign" ? ["reviewerId"] : []),
      ...(p.action === "recheck"
        ? [
            "expectedTargetVersion",
            "expectedContentHash",
            "expectedTargetHash",
            "policyId",
          ]
        : []),
    ];
    const action = this.kind === "action" ? "submit" : p.action;
    if (
      Object.keys(p).some((k) => !permitted.includes(k)) ||
      !availableActions(this.item, this.kind, this.staff).includes(action) ||
      p.reason.length < 5 ||
      p.reason.length > 2000 ||
      action !== this.draft.action ||
      (action === "assign" && p.reviewerId !== this.draft.reviewerId)
    )
      return false;
    this.pending = Object.freeze(copy(p));
    this.state = "unknown";
    return true;
  }
}
