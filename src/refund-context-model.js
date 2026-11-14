const states = [
  "created",
  "pending",
  "confirming",
  "paid",
  "refunding",
  "refunded",
  "abandoned",
];
const grantStates = ["active", "scheduled", "expired", "revoked"];
const hash = (v) => typeof v === "string" && /^[a-f0-9]{64}$/.test(v);
const date = (v) => typeof v === "string" && Number.isFinite(Date.parse(v));
const count = (v) => Number.isSafeInteger(v) && v >= 0;
const text = (v, max = 12000) => typeof v === "string" && v.length <= max;
const position = (v) =>
  typeof v === "number" && Number.isFinite(v) && v >= 0 && v <= 86400;

export function checkedRefundContext(
  value,
  orderId,
  viewerId,
  operator = false,
) {
  const fail = () => {
    throw Object.assign(
      new Error(
        "환불 검토 근거의 연결을 확인하지 못했습니다. 다시 조회해주세요.",
      ),
      { invalidRefundContext: true },
    );
  };
  if (
    !value ||
    value.schemaVersion !== 1 ||
    value.audience !== (operator ? "operator" : "member") ||
    value.viewerId !== viewerId ||
    !hash(value.contextHash) ||
    !date(value.observedAt)
  )
    fail();
  const {
    order: o,
    policy: p,
    learning: l,
    access: a,
    inquiry: q,
    paymentEvidence: e,
    review: r,
  } = value;
  if (
    !o ||
    o.id !== orderId ||
    !text(o.courseId, 120) ||
    !o.courseId ||
    !text(o.title, 500) ||
    !o.title ||
    !count(o.amount) ||
    o.amount < 1 ||
    o.currency !== "KRW" ||
    !["test", "live"].includes(o.mode) ||
    !states.includes(o.status) ||
    !count(o.version) ||
    o.version < 1 ||
    !date(o.createdAt) ||
    !date(o.updatedAt) ||
    !(o.paidAt === null || date(o.paidAt))
  )
    fail();
  if (
    !p ||
    !["recorded", "unrecorded", "inconsistent"].includes(p.status) ||
    !Array.isArray(p.documents)
  )
    fail();
  if (p.status === "recorded") {
    if (
      !hash(p.fingerprint) ||
      !date(p.sealedAt) ||
      p.documents.length !== 2 ||
      new Set(p.documents.map((d) => d?.kind)).size !== 2 ||
      p.documents.some(
        (d) =>
          !d ||
          !["refund", "course_terms"].includes(d.kind) ||
          !text(d.id, 120) ||
          !d.id ||
          !count(d.edition) ||
          d.edition < 1 ||
          !text(d.title, 500) ||
          !text(d.summary, 1000) ||
          !text(d.body) ||
          !hash(d.contentHash) ||
          !date(d.acceptedAt) ||
          !date(d.effectiveAt) ||
          !(d.expiresAt === null || date(d.expiresAt)),
      )
    )
      fail();
  } else if (
    p.documents.length ||
    p.sealedAt !== null ||
    p.fingerprint !== null
  )
    fail();
  if (
    q !== null &&
    (!q ||
      !["requested", "approved", "rejected"].includes(q.status) ||
      !text(q.reason, 2000) ||
      !text(q.resolution, 2000) ||
      !date(q.createdAt) ||
      !date(q.updatedAt))
  )
    fail();
  if (
    !l ||
    l.scope !== "member_course_lifetime" ||
    l.orderAttribution !== "unavailable" ||
    ![
      l.completedLessons,
      l.quizAttempts,
      l.totalVideoRecords,
      l.currentVideoSources,
      l.legacyResumeRecords,
    ].every(count) ||
    l.currentVideoSources > l.totalVideoRecords ||
    !(l.latestRecordedAt === null || date(l.latestRecordedAt)) ||
    l.videoLimit !== 30 ||
    !Array.isArray(l.videoRecords) ||
    l.videoRecords.length > l.videoLimit ||
    l.videoRecords.length > l.totalVideoRecords ||
    l.hasMoreVideos !== l.totalVideoRecords > l.videoRecords.length
  )
    fail();
  if (
    l.videoRecords.some(
      (v) =>
        !v ||
        !text(v.lessonId, 120) ||
        !v.lessonId ||
        !text(v.title, 500) ||
        !["current", "previous"].includes(v.source) ||
        !position(v.resumeSeconds) ||
        !position(v.furthestSeconds) ||
        v.resumeSeconds > v.furthestSeconds ||
        !date(v.updatedAt) ||
        (v.durationSeconds === null
          ? v.furthestPercent !== null
          : !position(v.durationSeconds) ||
            v.durationSeconds === 0 ||
            v.furthestSeconds > v.durationSeconds + 1 ||
            typeof v.furthestPercent !== "number" ||
            !Number.isFinite(v.furthestPercent) ||
            Math.abs(
              v.furthestPercent -
                (Math.min(v.furthestSeconds, v.durationSeconds) /
                  v.durationSeconds) *
                  100,
            ) > 0.011),
    )
  )
    fail();
  const validGrants = (g) =>
    g &&
    count(g.total) &&
    grantStates.every((s) => count(g[s])) &&
    grantStates.reduce((sum, s) => sum + g[s], 0) === g.total;
  if (
    !a ||
    a.revokeScope !== "payment_order_only" ||
    !validGrants(a.orderGrants) ||
    !validGrants(a.otherGrants)
  )
    fail();
  const expectedAccess =
    grantStates.find((s) => a.orderGrants[s] + a.otherGrants[s] > 0) || "none";
  if (
    a.status !== expectedAccess ||
    a.canAccess !== (expectedAccess === "active")
  )
    fail();
  if (
    !e ||
    ![
      e.verifiedApprovalAt,
      e.verifiedCancellationAt,
      e.unverifiedCancellationAt,
    ].every((v) => v === null || date(v))
  )
    fail();
  const expectedReview =
    o.status === "refunding"
      ? "cancellation_pending"
      : o.status === "refunded"
        ? "cancellation_verified"
        : o.status !== "paid"
          ? "payment_not_completed"
          : p.status !== "recorded"
            ? "records_need_review"
            : "operator_review_required";
  const checklist = [
    ["original_terms", p.status],
    ["inquiry", q?.status || "none"],
    ["payment_result", o.status],
    ["learning_scope", l.latestRecordedAt ? "lifetime_records" : "no_records"],
    [
      "access_scope",
      a.orderGrants.total ? "order_grants_recorded" : "order_grants_missing",
    ],
    ["commercial_decision", "operator_review_required"],
  ];
  if (
    !r ||
    r.state !== expectedReview ||
    r.automaticEligibility !== null ||
    !Array.isArray(r.checklist) ||
    r.checklist.length !== checklist.length ||
    r.checklist.some(
      (s, i) => !s || s.id !== checklist[i][0] || s.status !== checklist[i][1],
    )
  )
    fail();
  return value;
}

export function refundReadError(error) {
  if (error.sessionChanged || [401, 403].includes(error.status))
    return "로그인 계정 또는 이용 권한이 바뀌었습니다. 현재 계정에서 주문을 다시 열어주세요.";
  if (error.status === 404)
    return "현재 계정에서 조회할 수 있는 주문을 찾을 수 없습니다.";
  if (error.invalidRefundContext)
    return "연결된 기록을 확인하지 못했습니다. 다시 조회하거나 운영자에게 문의해주세요.";
  if (error.status === 429)
    return "조회 요청이 많습니다. 잠시 후 다시 확인해주세요.";
  return "현재 검토 근거를 불러오지 못했습니다. 연결 상태를 확인한 뒤 다시 조회해주세요.";
}
