/** Owned, explicit inbox intents. No requests, DOM, storage or delivery consent. */
export const noticeCategories = {
  learning: "학습·강의",
  conversation: "커뮤니티",
  transaction: "주문·수강권",
  operation: "운영 안내",
  marketing: "마케팅",
  legacy: "이전 알림",
};
export const noticeStates = { all: "전체", unread: "미읽음", read: "읽음" };
const id = (s) => typeof s === "string" && /^[A-Za-z0-9_-]{1,180}$/.test(s);
const key = (s) => typeof s === "string" && /^[A-Za-z0-9_-]{12,120}$/.test(s);
const count = (n) => Number.isSafeInteger(n) && n >= 0;
const plain = (o) => !!o && Object.getPrototypeOf(o) === Object.prototype;
const exact = (o, keys) =>
  plain(o) &&
  Object.keys(o).length === keys.length &&
  keys.every((k) => Object.hasOwn(o, k));
const canonical = (x) =>
  JSON.stringify(x, (_, v) =>
    plain(v)
      ? Object.fromEntries(
          Object.entries(v).sort(([a], [b]) => a.localeCompare(b)),
        )
      : v,
  );
const same = (a, b) => canonical(a) === canonical(b);
const copy = (o) => JSON.parse(JSON.stringify(o));
const fail = (message) => {
  throw new Error(message);
};

export function inboxQuery(params = new URLSearchParams()) {
  const allowed = ["category", "state", "groupBy", "groupId", "page", "limit"];
  for (const name of params.keys())
    if (!allowed.includes(name) || params.getAll(name).length !== 1)
      throw Object.assign(new Error("알림 목록 조건을 다시 확인해주세요."), {
        status: 400,
      });
  const q = {
    category: params.get("category") || null,
    state: params.get("state") || "all",
    groupBy: params.get("groupBy") || "none",
    groupId: params.get("groupId") || null,
    page: Number(params.get("page") || 1),
    limit: Number(params.get("limit") || 20),
  };
  if (
    (q.category !== null && !Object.hasOwn(noticeCategories, q.category)) ||
    !Object.hasOwn(noticeStates, q.state) ||
    !["none", "reference"].includes(q.groupBy) ||
    (q.groupId !== null && !id(q.groupId)) ||
    !Number.isInteger(q.page) ||
    q.page < 1 ||
    q.page > 10000 ||
    ![20, 50, 100].includes(q.limit)
  )
    throw Object.assign(
      new Error("알림 유형·읽음·묶음·페이지를 확인해주세요."),
      { status: 400 },
    );
  return Object.freeze(q);
}
export const queryKey = (q) => canonical(q);
export const noticeFilters = (q) => ({
  category: q.category,
  state: q.state,
  groupId: q.groupId,
});
export function inboxHref(q, changes = {}) {
  const n = { ...q, ...changes },
    p = new URLSearchParams();
  if (n.category) p.set("category", n.category);
  if (n.state !== "all") p.set("state", n.state);
  if (n.groupBy !== "none") p.set("groupBy", n.groupBy);
  if (n.groupId) p.set("groupId", n.groupId);
  if (n.page !== 1) p.set("page", n.page);
  if (n.limit !== 20) p.set("limit", n.limit);
  return "#/inbox" + (p.size ? "?" + p : "");
}
export const inboxEndpoint = (q) =>
  "/api/notifications?" +
  new URLSearchParams(Object.entries(q).filter(([, v]) => v !== null));
export const filterLabel = (q) =>
  `${q.category ? noticeCategories[q.category] : "모든 유형"} · ${noticeStates[q.state]}${q.groupId ? " · 선택한 묶음" : ""}`;

export function validateListing(data, q, ownerId) {
  if (
    !plain(data) ||
    !Array.isArray(data.notifications) ||
    !count(data.unreadCount) ||
    !count(data.filteredUnreadCount) ||
    !count(data.total) ||
    data.filteredUnreadCount > data.total ||
    data.unreadCount < data.filteredUnreadCount ||
    data.page !== q.page ||
    data.pageSize !== q.limit ||
    data.groupBy !== q.groupBy ||
    !same(data.filter, noticeFilters(q)) ||
    typeof data.readScope !== "string" ||
    data.readScope.length > 3000
  )
    fail("알림 목록의 현재 조건을 확인하지 못했습니다. 다시 불러와주세요.");
  if (
    data.notifications.some((n) => !id(n.id) || n.userId !== ownerId) ||
    (q.groupBy === "reference" &&
      (!count(data.groupTotal) ||
        !Array.isArray(data.groups) ||
        data.groups.some(
          (g) =>
            !id(g.id) ||
            !count(g.notificationCount) ||
            !count(g.unreadCount) ||
            g.unreadCount > g.notificationCount ||
            g.latest?.userId !== ownerId ||
            !id(g.latest?.id),
        )))
  )
    fail("본인의 알림 목록을 다시 확인해주세요.");
  return data;
}

function scopePacket(token, ownerId, q) {
  if (
    typeof token !== "string" ||
    token.length > 3000 ||
    !/^v1\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]{43}$/.test(token)
  )
    fail("읽음 범위를 다시 확인해주세요.");
  let packet;
  try {
    const part = token.split(".")[1].replaceAll("-", "+").replaceAll("_", "/");
    packet = JSON.parse(
      new TextDecoder().decode(
        Uint8Array.from(
          atob(part + "=".repeat((4 - (part.length % 4)) % 4)),
          (c) => c.charCodeAt(0),
        ),
      ),
    );
  } catch {
    fail("읽음 범위를 다시 확인해주세요.");
  }
  if (
    !exact(packet, ["version", "ownerId", "filters", "upperId", "issuedAt"]) ||
    packet.version !== 1 ||
    packet.ownerId !== ownerId ||
    !same(packet.filters, noticeFilters(q)) ||
    !count(packet.issuedAt) ||
    !(packet.upperId === null || id(packet.upperId))
  )
    fail("현재 계정과 목록의 읽음 범위를 다시 확인해주세요.");
  // Client correlation only; signature/time/ownership authority stays on server.
  return packet;
}

export class NoticeReadIntent {
  constructor({ ownerId, query, makeKey = () => crypto.randomUUID() }) {
    if (!id(ownerId)) fail("알림 계정을 다시 확인해주세요.");
    this.ownerId = ownerId;
    this.query = query;
    this.makeKey = makeKey;
    this.state = "idle";
    this.pending = null;
    this.meta = null;
    this.receipt = null;
  }
  prepare(listing, single = null) {
    if (!["idle", "saved"].includes(this.state))
      fail("앞선 읽음 요청의 결과를 먼저 확인해주세요.");
    const requestKey = this.makeKey();
    if (!key(requestKey)) fail("읽음 요청을 준비하지 못했습니다.");
    if (single) {
      if (!id(single.id) || single.userId !== this.ownerId || single.readAt)
        fail("본인의 미읽음 알림을 확인해주세요.");
      this.pending = Object.freeze({ id: single.id, requestKey });
      this.meta = {
        count: 1,
        scope: { singleId: single.id },
        label: "선택한 알림 1건",
      };
    } else {
      validateListing(listing, this.query, this.ownerId);
      if (!listing.filteredUnreadCount)
        fail("현재 조건에서 읽음 표시할 알림이 없습니다.");
      const scope = scopePacket(listing.readScope, this.ownerId, this.query);
      this.pending = Object.freeze({
        all: true,
        readScope: listing.readScope,
        requestKey,
      });
      this.meta = {
        count: listing.filteredUnreadCount,
        scope,
        label: filterLabel(this.query),
      };
    }
    this.state = "preview";
    this.receipt = null;
    return this.pending;
  }
  sending() {
    if (!this.pending || !["preview", "unknown"].includes(this.state))
      fail("읽음 처리할 범위를 먼저 확인해주세요.");
    this.state = "saving";
    return this.pending;
  }
  uncertain() {
    if (this.pending) this.state = "unknown";
  }
  conflict() {
    this.state = "conflict";
  }
  reset() {
    if (["saving", "unknown"].includes(this.state))
      fail("같은 읽음 요청의 저장 결과를 먼저 확인해주세요.");
    this.pending = null;
    this.meta = null;
    this.state = "idle";
  }
  accept(result) {
    const r = result?.receipt;
    if (
      !this.pending ||
      r?.operation !== "read" ||
      !id(r.id) ||
      r.requestKey !== this.pending.requestKey ||
      !count(r.changedCount) ||
      r.changedCount > this.meta.count ||
      typeof r.readAt !== "string" ||
      !same(r.scope, this.meta.scope)
    )
      fail(
        "읽음 저장 결과가 요청과 다릅니다. 같은 요청으로 다시 확인해주세요.",
      );
    this.receipt = Object.freeze(copy(r));
    this.pending = null;
    this.state = "saved";
    return r;
  }
  export() {
    if (!this.pending || !["saving", "unknown"].includes(this.state))
      return null;
    return {
      type: "notice-read-v1",
      ownerId: this.ownerId,
      queryKey: queryKey(this.query),
      pending: copy(this.pending),
      count: this.meta.count,
    };
  }
  restore(packet) {
    if (
      !exact(packet, ["type", "ownerId", "queryKey", "pending", "count"]) ||
      packet.type !== "notice-read-v1" ||
      packet.ownerId !== this.ownerId ||
      packet.queryKey !== queryKey(this.query) ||
      !count(packet.count) ||
      !packet.count ||
      !key(packet.pending?.requestKey)
    )
      return false;
    let scope;
    if (packet.pending.all === true) {
      if (!exact(packet.pending, ["all", "readScope", "requestKey"]))
        return false;
      try {
        scope = scopePacket(packet.pending.readScope, this.ownerId, this.query);
      } catch {
        return false;
      }
    } else {
      if (
        !exact(packet.pending, ["id", "requestKey"]) ||
        !id(packet.pending.id) ||
        packet.count !== 1
      )
        return false;
      scope = { singleId: packet.pending.id };
    }
    this.pending = Object.freeze(copy(packet.pending));
    this.meta = {
      count: packet.count,
      scope,
      label: packet.pending.all ? filterLabel(this.query) : "선택한 알림 1건",
    };
    this.state = "unknown";
    return true;
  }
}

export const maxNoticePreferenceVersion = 2147483647;
export function supportedPreference(p, policy = {}) {
  return (
    !!p.deliveryReady &&
    p.channel === "site" &&
    p.category !== "legacy" &&
    (p.category !== "marketing" ||
      (policy.marketingConfigured && policy.marketingConsentGranted))
  );
}
export function editablePreference(p, policy = {}) {
  return (
    validPreference(p) &&
    supportedPreference(p, policy) &&
    p.version < maxNoticePreferenceVersion
  );
}
function validPreference(p) {
  return (
    plain(p) &&
    Object.hasOwn(noticeCategories, p.category) &&
    ["site", "email"].includes(p.channel) &&
    typeof p.enabled === "boolean" &&
    count(p.version) &&
    p.version <= maxNoticePreferenceVersion
  );
}
export function validatePreferences(data) {
  const expected = Object.keys(noticeCategories).flatMap((c) => [
    c + ":site",
    c + ":email",
  ]);
  if (
    !plain(data) ||
    !Array.isArray(data.preferences) ||
    data.preferences.length !== expected.length ||
    new Set(data.preferences.map((p) => p.category + ":" + p.channel)).size !==
      expected.length ||
    data.preferences.some(
      (p) =>
        !validPreference(p) ||
        !expected.includes(p.category + ":" + p.channel) ||
        typeof p.deliveryReady !== "boolean",
    ) ||
    !plain(data.deliveryPolicy) ||
    data.deliveryPolicy.sitePreferenceIsEmailConsent !== false ||
    [
      "emailConfigured",
      "marketingConfigured",
      "marketingConsentGranted",
      "requiredTypesApproved",
    ].some((k) => typeof data.deliveryPolicy[k] !== "boolean")
  )
    fail(
      "알림 설정의 현재 적용 범위를 확인하지 못했습니다. 다시 불러와주세요.",
    );
  return data;
}
export class NoticePreferenceIntent {
  constructor({ ownerId, preference, makeKey = () => crypto.randomUUID() }) {
    if (!id(ownerId) || !validPreference(preference))
      fail("본인 알림 설정을 다시 확인해주세요.");
    this.ownerId = ownerId;
    this.base = { ...preference };
    this.draft = preference.enabled;
    this.makeKey = makeKey;
    this.pending = null;
    this.latest = null;
    this.state = "editing";
  }
  dirty() {
    return this.draft !== this.base.enabled;
  }
  edit(value) {
    if (
      typeof value !== "boolean" ||
      ["saving", "unknown", "conflict"].includes(this.state)
    )
      fail("현재 저장 결과나 최신 설정을 먼저 확인해주세요.");
    this.draft = value;
    this.pending = null;
    this.state = "editing";
  }
  prepare() {
    if (
      this.state !== "editing" ||
      this.base.version >= maxNoticePreferenceVersion ||
      (!this.dirty() && this.base.version > 0)
    )
      fail("변경할 수신 설정을 먼저 확인해주세요.");
    const requestKey = this.makeKey();
    if (!key(requestKey)) fail("설정 요청을 준비하지 못했습니다.");
    this.pending = Object.freeze({
      category: this.base.category,
      channel: this.base.channel,
      enabled: this.draft,
      expectedVersion: this.base.version,
      requestKey,
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
      fail("저장할 수신 설정을 먼저 확인해주세요.");
    this.state = "saving";
    return this.pending;
  }
  uncertain() {
    if (this.pending) this.state = "unknown";
  }
  matches(p) {
    return (
      validPreference(p) &&
      p.category === this.base.category &&
      p.channel === this.base.channel
    );
  }
  refresh(p) {
    if (
      !this.matches(p) ||
      p.version < this.base.version ||
      ["saving", "unknown"].includes(this.state)
    )
      fail("같은 설정 요청의 결과를 먼저 확인해주세요.");
    if (
      this.state === "conflict" ||
      (this.dirty() &&
        (p.version !== this.base.version || p.enabled !== this.base.enabled))
    ) {
      this.latest = { ...p };
      this.pending = null;
      this.state = "conflict";
    } else {
      const dirty = this.dirty();
      this.base = { ...p };
      if (!dirty) this.draft = p.enabled;
      this.pending = null;
      this.state = "editing";
    }
  }
  conflict(p) {
    if (!this.matches(p)) fail("현재 수신 설정을 다시 확인해주세요.");
    this.latest = { ...p };
    this.pending = null;
    this.state = "conflict";
  }
  rebase(useLatest = false) {
    if (this.state !== "conflict" || !this.latest)
      fail("현재 설정을 먼저 확인해주세요.");
    this.base = { ...this.latest };
    if (useLatest) this.draft = this.base.enabled;
    this.latest = null;
    this.pending = null;
    this.state = "editing";
  }
  accept(result) {
    const r = result?.receipt,
      p = result?.currentPreference,
      b = this.pending;
    if (
      !b ||
      r?.operation !== "preference" ||
      !id(r.id) ||
      r.requestKey !== b.requestKey ||
      r.category !== b.category ||
      r.channel !== b.channel ||
      r.enabled !== b.enabled ||
      r.version !== b.expectedVersion + 1 ||
      !this.matches(p) ||
      p.version < r.version
    )
      fail(
        "설정 저장 결과가 요청과 다릅니다. 같은 요청으로 다시 확인해주세요.",
      );
    this.receipt = Object.freeze(copy(r));
    this.base = { ...p };
    this.draft = p.enabled;
    this.pending = null;
    this.latest = null;
    this.state = "saved";
    return p;
  }
  export() {
    if (!this.dirty() && !this.pending) return null;
    return {
      type: "notice-preference-v1",
      ownerId: this.ownerId,
      base: {
        category: this.base.category,
        channel: this.base.channel,
        version: this.base.version,
        enabled: this.base.enabled,
      },
      draft: this.draft,
      pending:
        ["saving", "unknown"].includes(this.state) && this.pending
          ? copy(this.pending)
          : null,
    };
  }
  restore(packet) {
    if (
      !exact(packet, ["type", "ownerId", "base", "draft", "pending"]) ||
      packet.type !== "notice-preference-v1" ||
      packet.ownerId !== this.ownerId ||
      !validPreference(packet.base) ||
      packet.base.category !== this.base.category ||
      packet.base.channel !== this.base.channel ||
      packet.base.version > this.base.version ||
      typeof packet.draft !== "boolean"
    )
      return false;
    if (packet.pending) {
      const p = packet.pending;
      if (
        !exact(p, [
          "category",
          "channel",
          "enabled",
          "expectedVersion",
          "requestKey",
        ]) ||
        p.category !== this.base.category ||
        p.channel !== this.base.channel ||
        p.expectedVersion !== packet.base.version ||
        p.expectedVersion >= maxNoticePreferenceVersion ||
        p.enabled !== packet.draft ||
        !key(p.requestKey)
      )
        return false;
      this.draft = p.enabled;
      this.pending = Object.freeze(copy(p));
      this.state = "unknown";
    } else {
      this.draft = packet.draft;
      if (
        packet.base.version !== this.base.version ||
        packet.base.enabled !== this.base.enabled
      ) {
        this.latest = { ...this.base };
        this.base = { ...this.base, ...packet.base };
        this.state = "conflict";
      } else this.state = "editing";
    }
    return true;
  }
}
