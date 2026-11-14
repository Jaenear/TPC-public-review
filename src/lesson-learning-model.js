/** Account-bound explicit actions. Quiz, completion and video facts stay separate. */
export class LessonIntent {
  constructor({
    ownerId,
    courseId,
    lessonId,
    learning,
    key = () => crypto.randomUUID(),
  }) {
    Object.assign(this, { ownerId, courseId, lessonId, key });
    this.learning = { ...learning, completion: { ...learning?.completion } };
  }
  prepare(kind, value) {
    const state = this.learning;
    if (
      !this.ownerId ||
      !state.canRecord ||
      !Number.isInteger(state.lessonVersion)
    )
      throw new Error("현재 계정과 수강권을 확인해주세요.");
    const body = { requestKey: this.key(), lessonVersion: state.lessonVersion };
    if (kind === "quiz") {
      if (!Number.isInteger(value) || value < 0 || value > 5 || !state.quizHash)
        throw new Error("답안과 현재 퀴즈를 확인해주세요.");
      Object.assign(body, { answer: value, quizHash: state.quizHash });
    } else if (kind === "completion" && typeof value === "boolean") {
      Object.assign(body, {
        courseId: this.courseId,
        lessonId: this.lessonId,
        completed: value,
        completionVersion: state.completion.version,
      });
    } else throw new Error("학습 요청을 확인해주세요.");
    return Object.freeze(body);
  }
  isSnapshot(response) {
    const next = response?.learning;
    return Boolean(
      next &&
        next.courseId === this.courseId &&
        next.lessonId === this.lessonId &&
        Number.isInteger(next.lessonVersion) &&
        next.lessonVersion > 0 &&
        Number.isInteger(next.completion?.version) &&
        next.completion.version >= 0,
    );
  }
  accept(response) {
    const next = response?.learning;
    if (
      !this.isSnapshot(response) ||
      next.lessonVersion < this.learning.lessonVersion ||
      (Number.isInteger(next.quizAttemptCount) &&
        Number.isInteger(this.learning.quizAttemptCount) &&
        next.quizAttemptCount < this.learning.quizAttemptCount) ||
      next.completion.version < (this.learning.completion?.version || 0) ||
      (Number.isInteger(next.completion.eventCount) &&
        Number.isInteger(this.learning.completion?.eventCount) &&
        next.completion.eventCount < this.learning.completion.eventCount)
    )
      return false;
    this.learning = { ...next, completion: { ...next.completion } };
    return true;
  }
  confirms(kind, body, response) {
    const state = response?.learning;
    const receipt = kind === "quiz" ? response?.attempt : response?.receipt;
    if (
      !body ||
      !state ||
      state.courseId !== this.courseId ||
      state.lessonId !== this.lessonId ||
      state.canRecord !== true ||
      !Number.isInteger(state.lessonVersion) ||
      !Number.isInteger(state.completion?.version) ||
      state.completion.version < 0 ||
      !receipt ||
      typeof receipt.id !== "string" ||
      !receipt.id ||
      receipt.requestKey !== body.requestKey ||
      receipt.lessonVersion !== body.lessonVersion ||
      state.lessonVersion < receipt.lessonVersion
    )
      return false;
    if (kind === "quiz")
      return (
        receipt.quizHash === body.quizHash &&
        receipt.answer === body.answer &&
        typeof receipt.correct === "boolean" &&
        Number.isInteger(state.quizAttemptCount) &&
        state.quizAttemptCount > 0
      );
    return (
      kind === "completion" &&
      receipt.completed === body.completed &&
      typeof receipt.changed === "boolean" &&
      Number.isInteger(receipt.resultVersion) &&
      receipt.resultVersion ===
        body.completionVersion + Number(receipt.changed) &&
      state.completion.version >= receipt.resultVersion
    );
  }
}
export function previewAnswer(quiz, answer) {
  if (!Number.isInteger(answer) || answer < 0 || answer >= quiz.options.length)
    throw new Error("답안을 선택해주세요.");
  const expected = Number.isInteger(quiz.answer)
    ? quiz.answer
    : quiz.options.indexOf(quiz.answer);
  return {
    correct: answer === expected,
    explanation: quiz.explanation || "",
    saved: false,
  };
}
export function completionSource(completion = {}) {
  if (!completion.completed) return "아직 완료 표시를 하지 않았습니다.";
  return completion.source === "recorded"
    ? `수업 v${completion.lessonVersion}에서 완료했습니다.`
    : "이전에 완료한 기록입니다. 당시 수업 판본은 확인되지 않습니다.";
}
/** Disabled quiz controls still need an account/version-bound answer draft. */
export function quizDraftEntries(quiz, selected) {
  if (
    !Number.isInteger(selected) ||
    selected < 0 ||
    selected >= quiz.options.length
  )
    return [];
  return quiz.options.map((label, index) => ({
    key: "answer",
    index,
    type: "radio",
    value: String(index),
    checked: index === selected,
    dirty: index === selected,
    label,
    custom: false,
  }));
}
