/** Video positions are separate from attendance, quizzes and completion. */
export function watchPercent(seconds, duration) {
  return Number.isFinite(seconds) && Number.isFinite(duration) && duration > 0
    ? Math.round(Math.min(100, Math.max(0, (seconds / duration) * 100)) * 10) /
        10
    : null;
}

export function watchTime(seconds) {
  if (!Number.isFinite(seconds) || seconds < 0) return "—";
  const value = Math.floor(seconds);
  const minutes = Math.floor(value / 60),
    rest = String(value % 60).padStart(2, "0");
  return minutes >= 60
    ? `${Math.floor(minutes / 60)}:${String(minutes % 60).padStart(2, "0")}:${rest}`
    : `${minutes}:${rest}`;
}

/** A checkpoint is confirmed server state, independent of the live playhead. */
export function watchCheckpointLabel(progress) {
  if (!progress?.updatedAt) return "아직 저장된 위치가 없습니다.";
  const percent = watchPercent(progress.seconds, progress.durationSeconds);
  return `저장한 위치 ${watchTime(progress.seconds)} · ${percent === null ? "비율 미확인" : percent + "%"}`;
}

export class WatchSession {
  constructor({
    ownerId,
    courseId,
    lessonId,
    assetId,
    lessonVersion,
    progress,
    key = () => crypto.randomUUID(),
  }) {
    Object.assign(this, {
      ownerId,
      courseId,
      lessonId,
      assetId,
      lessonVersion,
      key,
    });
    this.progress = { ...progress };
    this.streamId = key();
    this.sequence = 0;
  }
  prepare(seconds) {
    if (
      !this.ownerId ||
      !this.assetId ||
      !Number.isFinite(seconds) ||
      seconds < 0 ||
      seconds > 86400
    )
      throw new Error("현재 계정과 영상의 재생 위치를 확인해주세요.");
    return Object.freeze({
      courseId: this.courseId,
      lessonId: this.lessonId,
      videoAssetId: this.assetId,
      lessonVersion: this.lessonVersion,
      seconds: Math.round(seconds * 1000) / 1000,
      watchVersion: this.progress.version || 0,
      requestKey: this.key(),
      streamId: this.streamId,
      sequence: ++this.sequence,
    });
  }
  accept(body, response) {
    const next = response?.watchProgress;
    if (
      body.streamId !== this.streamId ||
      !next ||
      next.assetId !== this.assetId ||
      !Number.isInteger(next.version) ||
      next.version < (this.progress.version || 0)
    )
      return false;
    this.progress = { ...next };
    return true;
  }
}
/** Unknown checkpoints require explicit same-request confirmation before recovery. */
export function canLeaveCheckpoint({
  uncertain,
  blocked,
  canSave,
  interacted,
}) {
  return !uncertain && !blocked && canSave && interacted;
}

/** The current original's server-read length takes precedence over an editorial label. */
export function lessonDurationLabel(lesson, fallback = lesson.duration || "") {
  const seconds =
    lesson.videoDurationSeconds ?? lesson.watchProgress?.durationSeconds;
  return lesson.videoAssetId && Number.isFinite(seconds) && seconds > 0
    ? `${lesson.videoSample ? "샘플 영상" : "영상"} ${watchTime(seconds)}`
    : fallback;
}
