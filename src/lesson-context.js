/** Scoped UI invalidation. Server authorization remains authoritative. */
export function connectLessonContext(root, scope, onUnavailable, options = {}) {
  const route = location.hash;
  const identity = Object.freeze({
    ownerId: scope.ownerId,
    courseId: scope.courseId,
    lessonId: scope.lessonId,
  });
  const active = () => root?.isConnected && location.hash === route;
  root?.addEventListener(
    "tpc:lesson-unavailable",
    (event) => {
      if (
        !active() ||
        !event.detail ||
        !Object.keys(identity).every(
          (key) => event.detail[key] === identity[key],
        ) ||
        typeof event.detail.message !== "string"
      )
        return;
      onUnavailable(event.detail.message);
    },
    options,
  );
  return (message) => {
    if (active())
      root.dispatchEvent(
        new CustomEvent("tpc:lesson-unavailable", {
          detail: Object.freeze({
            ...identity,
            message: String(message).slice(0, 2000),
          }),
        }),
      );
  };
}
