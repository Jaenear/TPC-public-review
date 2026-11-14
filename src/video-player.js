import {
  watchPercent,
  watchTime,
  watchCheckpointLabel,
  WatchSession,
  canLeaveCheckpoint,
} from "./video-model.js";
import {
  captionTrackMarkup,
  captionControls,
  bindVideoCaptions,
} from "./video-captions.js";
import { connectLessonContext } from "./lesson-context.js";

export function lessonVideo(lesson, course, ctx, { canEnroll = false } = {}) {
  if (!lesson.videoAssetId) return "";
  const e = ctx.esc,
    w = lesson.watchProgress || {};
  const pct = w.positionPercent;
  return `<section class="v-player" id="video-learning" aria-label="차시 영상과 나의 시청 위치"><div class="v-heading"><span>${lesson.videoSample ? "TPC SAMPLE CLASS" : "TPC VIDEO CLASS"}</span><span>${lesson.videoSample ? "자체 제작 학습용 샘플" : "차시 영상"} · ${watchTime(w.durationSeconds)}</span></div><video id="lesson-video" controls controlslist="nodownload" preload="metadata" playsinline aria-label="${e(lesson.title)} 영상" src="/api/media/${e(lesson.videoAssetId)}" ${lesson.videoSample ? 'poster="/assets/preflop-sample.svg"' : ""}>${captionTrackMarkup(lesson.captions || [], course, lesson, e)}</video><div class="v-transport"><button type="button" class="btn btn-outline btn-sm" id="video-play-toggle">재생</button><button type="button" class="btn btn-quiet btn-sm" data-watch-skip="-10" aria-label="영상 10초 뒤로">↶ 10초 뒤로</button><button type="button" class="btn btn-quiet btn-sm" data-watch-skip="10" aria-label="영상 10초 앞으로">10초 앞으로 ↷</button><button type="button" class="btn btn-quiet btn-sm" id="video-reload">영상 다시 불러오기</button></div><p class="v-media-status" id="video-media-status" role="status">버튼에 초점을 두고 Enter 또는 Space로 재생·이동·자막을 조작할 수 있습니다.</p>${captionControls(lesson, ctx)}<div class="v-position"><div><span>현재 재생 위치</span><strong><span id="watch-time">${watchTime(w.seconds || 0)}</span><small> / ${watchTime(w.durationSeconds)}</small></strong></div><strong id="watch-percent">${pct === null || pct === undefined ? "—" : pct + "%"}</strong></div><progress class="v-meter" id="watch-meter" max="100" ${pct === null || pct === undefined ? "" : `value="${pct}"`} aria-label="현재 재생 지점 비율"></progress><div class="v-saved-row"><span>최대 도달 <strong id="watch-furthest">${w.furthestPercent === null || w.furthestPercent === undefined ? "—" : w.furthestPercent + "%"}</strong></span><span id="watch-saved">${e(watchCheckpointLabel(w))}</span></div><p class="v-feedback" id="watch-feedback" role="status">${w.canSave ? (w.updatedAt ? `${watchTime(w.seconds)}에서 이어볼 수 있습니다. 재생 버튼을 눌러주세요.` : "재생 위치를 회원별로 저장합니다.") : !ctx.user ? "로그인하고 무료 강의를 등록하면 이어보기 위치를 저장합니다." : canEnroll ? "무료 강의 등록 후 나의 이어보기 위치를 저장할 수 있습니다." : "현재 수강권을 확인하면 이어보기 저장을 이용할 수 있습니다."}</p><div class="v-actions">${!ctx.user ? '<button class="btn btn-outline btn-sm" id="watch-login">로그인하고 이어보기</button>' : canEnroll && !w.canSave ? '<button class="btn btn-primary btn-sm" id="watch-enroll">무료 강의 등록 · 이어보기 저장</button>' : ""}<button class="btn btn-outline btn-sm" id="watch-retry" hidden>같은 저장 요청 다시 확인</button><button class="btn btn-outline btn-sm" id="watch-refresh" hidden>최신 수업·시청 위치 확인</button></div>${w.resetReason ? `<p class="v-note">${w.resetReason === "video_changed" ? "새 영상 원본입니다. 이 원본의 저장 위치부터 시작합니다." : w.resetReason === "checkpoint_out_of_range" ? "이전 위치가 현재 영상 길이와 달라 처음부터 시작합니다." : "기존 위치의 영상 원본을 확인할 수 없어 처음부터 시작합니다."}</p>` : ""}<details class="v-explainer"><summary>시청 지점과 학습 완료는 어떻게 다른가요?</summary><p>비율은 전체 영상 길이에 대한 재생 지점입니다. 최대 도달에는 앞으로 건너뛴 지점도 포함됩니다. 연속으로 시청한 비율이나 수료 여부를 뜻하지 않으며, 학습 완료 표시는 별도로 저장합니다.</p>${lesson.videoSample ? "<p>이 영상은 한국어 음성과 학습 슬라이드로 제작한 TPC 샘플입니다. 실제 코치의 정식 강의 콘텐츠는 추가될 예정입니다.</p>" : ""}</details></section>`;
}

export function bindLessonVideo(lesson, course, ctx) {
  const root = document.querySelector("#video-learning"),
    player = root?.querySelector("video");
  if (!player) return;
  const ownerId = ctx.user?.id || null,
    route = location.hash;
  const session = new WatchSession({
    ownerId,
    courseId: course.id,
    lessonId: lesson.id,
    assetId: lesson.videoAssetId,
    lessonVersion: lesson.version,
    progress: lesson.watchProgress || { version: 0 },
  });
  const feedback = root.querySelector("#watch-feedback"),
    retry = root.querySelector("#watch-retry"),
    refresh = root.querySelector("#watch-refresh"),
    playToggle = root.querySelector("#video-play-toggle"),
    mediaStatus = root.querySelector("#video-media-status"),
    reloadVideo = root.querySelector("#video-reload");
  const controller = new AbortController(),
    options = { signal: controller.signal };
  let ready = false,
    restoring = false,
    interacted = false,
    blocked = false,
    busy = false,
    uncertain = false,
    pending = null;
  let reloading = false,
    restorePoint = null;
  let queued = null,
    lastQueued = Date.now(),
    disposed = false;
  const active = () => root.isConnected && location.hash === route && !disposed;
  const mayConfirm = () =>
    Boolean(ownerId && session.progress.canSave && !blocked);
  const canSave = () =>
    Boolean(
      ownerId &&
        session.progress.canSave &&
        !blocked &&
        ready &&
        !restoring &&
        !reloading,
    );
  function show(message, error = false) {
    if (!active()) return;
    feedback.textContent = message;
    feedback.classList.toggle("is-error", error);
  }
  function display() {
    if (!active()) return;
    const duration = session.progress.durationSeconds;
    const seconds =
      ready && Number.isFinite(player.currentTime)
        ? player.currentTime
        : (restorePoint ?? session.progress.seconds ?? 0);
    const pct = watchPercent(seconds, duration);
    root.querySelector("#watch-time").textContent = watchTime(seconds);
    root.querySelector("#watch-percent").textContent =
      pct === null ? "—" : pct + "%";
    const meter = root.querySelector("#watch-meter");
    if (pct === null) meter.removeAttribute("value");
    else meter.value = pct;
    const furthest = watchPercent(
      Math.max(session.progress.furthestSeconds || 0, seconds),
      duration,
    );
    root.querySelector("#watch-furthest").textContent =
      furthest === null ? "—" : furthest + "%";
    root.querySelector("#watch-saved").textContent = watchCheckpointLabel(
      session.progress,
    );
  }
  async function pump() {
    if (
      !active() ||
      busy ||
      uncertain ||
      queued === null ||
      (pending ? !mayConfirm() : !canSave())
    )
      return;
    if (!pending && Math.abs(queued - (session.progress.seconds || 0)) < 0.05)
      return;
    pending ||= session.prepare(queued);
    const body = pending;
    busy = true;
    show("시청 위치 저장 중…");
    try {
      const result = await ctx.api("/api/watch", {
        method: "PUT",
        body,
        ownerId,
        keepalive: true,
      });
      if (!session.accept(body, result))
        throw new Error("현재 영상의 저장 결과를 확인하지 못했습니다.");
      if (pending?.requestKey === body.requestKey) pending = null;
      if (active()) {
        retry.hidden = true;
        display();
        show(
          `${watchTime(session.progress.seconds)} 저장됨 · 같은 영상에서 이어볼 수 있습니다.`,
        );
      }
    } catch (error) {
      if (!active()) return;
      if ([400, 401, 403, 404, 409].includes(error.status)) {
        pending = null;
        blockPlayback(error.message, [401, 403, 404].includes(error.status));
      } else {
        uncertain = true;
        show(
          "저장 결과를 확인하지 못했습니다. 같은 요청으로 다시 확인할 수 있습니다.",
          true,
        );
        retry.hidden = false;
      }
    } finally {
      busy = false;
      if (
        active() &&
        !blocked &&
        !uncertain &&
        pending === null &&
        queued !== null &&
        Math.abs(queued - body.seconds) >= 0.05
      )
        queueMicrotask(pump);
    }
  }
  function save() {
    if (!active() || !canSave() || !interacted) return;
    queued = Math.round(player.currentTime * 1000) / 1000;
    lastQueued = Date.now();
    pump();
  }
  function leave() {
    if (
      !root.isConnected ||
      !canLeaveCheckpoint({
        uncertain,
        blocked,
        canSave: canSave(),
        interacted,
      }) ||
      !Number.isFinite(player.currentTime)
    )
      return;
    // A newer sequence from this player can land before an older in-flight
    // checkpoint. The server rejects that older sequence when it arrives.
    if (
      !pending &&
      Math.abs(player.currentTime - (session.progress.seconds || 0)) < 0.05
    )
      return;
    const body = session.prepare(player.currentTime);
    ctx
      .api("/api/watch", { method: "PUT", body, ownerId, keepalive: true })
      .then((result) => {
        if (active()) {
          session.accept(body, result);
          display();
        }
      })
      .catch(() => {});
  }
  function metadata() {
    ready = true;
    const point = restorePoint ?? session.progress.seconds ?? 0;
    if (
      point > 0 &&
      Number.isFinite(player.duration) &&
      Math.abs(player.currentTime - Math.min(point, player.duration)) > 0.05
    ) {
      restoring = true;
      restorePoint = point;
      player.currentTime = Math.min(point, player.duration);
    } else restorePoint = null;
    display();
  }
  function mediaMessage(message, error = false) {
    if (!active()) return;
    mediaStatus.textContent = message;
    mediaStatus.classList.toggle("is-error", error);
  }
  const informUnavailable = connectLessonContext(
    root.closest(".m-reader"),
    { ownerId, courseId: course.id, lessonId: lesson.id },
    (message) => blockPlayback(message),
    options,
  );
  function blockPlayback(message, notify = false) {
    blocked = true;
    player.pause();
    playToggle.disabled = true;
    root
      .querySelectorAll("[data-watch-skip]")
      .forEach((b) => (b.disabled = true));
    show(message + " 최신 수업과 계정에서 다시 확인해주세요.", true);
    refresh.hidden = false;
    if (notify) informUnavailable(message);
  }
  bindVideoCaptions(root, player, lesson, course, ctx, {
    signal: controller.signal,
    onPermissionError: (error) =>
      blockPlayback(error.message, [401, 403, 404].includes(error.status)),
    onLessonChanged: () =>
      blockPlayback("영상 또는 차시가 변경됐습니다.", true),
  });
  playToggle.addEventListener(
    "click",
    async () => {
      if (blocked || reloading) return;
      if (!player.paused) return player.pause();
      try {
        await player.play();
      } catch {
        mediaMessage(
          "재생을 시작하지 못했습니다. 영상 다시 불러오기를 이용해주세요.",
          true,
        );
        reloadVideo.hidden = false;
      }
    },
    options,
  );
  reloadVideo.addEventListener(
    "click",
    async () => {
      if (reloading || blocked) return;
      player.pause();
      if (busy || pending || uncertain) {
        mediaMessage(
          "시청 위치 저장 결과를 먼저 확인한 뒤 영상을 다시 불러올 수 있습니다.",
          true,
        );
        return;
      }
      reloading = true;
      reloadVideo.disabled = true;
      mediaMessage("현재 계정·영상·저장 위치를 확인합니다…");
      try {
        const data = await ctx.api(
          `/api/courses/${encodeURIComponent(course.id)}/lessons/${encodeURIComponent(lesson.id)}`,
          { ownerId },
        );
        if (!active()) return;
        const current = data.lesson;
        const changedLesson =
          current.version !== lesson.version ||
          current.videoAssetId !== lesson.videoAssetId;
        if (
          changedLesson ||
          (current.watchProgress?.version || 0) !==
            (session.progress.version || 0)
        ) {
          blockPlayback(
            "다른 탭의 저장 위치 또는 영상 판본이 변경됐습니다.",
            changedLesson,
          );
          return;
        }
        restorePoint ??=
          ready && Number.isFinite(player.currentTime)
            ? player.currentTime
            : session.progress.seconds || 0;
        ready = false;
        player.src = `/api/media/${encodeURIComponent(lesson.videoAssetId)}?reload=${encodeURIComponent(crypto.randomUUID())}`;
        player.load();
        mediaMessage(
          "영상을 다시 불러옵니다. 준비되면 재생 버튼을 눌러주세요.",
        );
      } catch (error) {
        if (!active()) return;
        if ([401, 403, 404, 409].includes(error.status))
          blockPlayback(error.message, [401, 403, 404].includes(error.status));
        else
          mediaMessage(error.message + " 다시 불러오기로 확인해주세요.", true);
      } finally {
        reloading = false;
        if (active()) reloadVideo.disabled = false;
      }
    },
    options,
  );
  root.querySelectorAll("[data-watch-skip]").forEach((button) => {
    button.addEventListener(
      "click",
      () => {
        if (!ready || blocked || !Number.isFinite(player.duration)) return;
        interacted = true;
        player.currentTime = Math.min(
          player.duration,
          Math.max(0, player.currentTime + Number(button.dataset.watchSkip)),
        );
      },
      options,
    );
  });
  player.addEventListener("loadedmetadata", metadata, options);
  if (player.readyState >= 1) metadata();
  player.addEventListener(
    "play",
    () => {
      if (blocked) player.pause();
      else {
        interacted = true;
        playToggle.textContent = "일시정지";
      }
    },
    options,
  );
  player.addEventListener(
    "timeupdate",
    () => {
      display();
      if (!player.paused && Date.now() - lastQueued >= 5000) save();
    },
    options,
  );
  player.addEventListener(
    "pause",
    () => {
      playToggle.textContent = "재생";
      save();
    },
    options,
  );
  player.addEventListener("ended", save, options);
  player.addEventListener(
    "seeked",
    () => {
      if (restoring) {
        restoring = false;
        restorePoint = null;
        display();
      } else {
        interacted = true;
        display();
        save();
      }
    },
    options,
  );
  player.addEventListener(
    "error",
    () => {
      ready = false;
      mediaMessage(
        "영상을 불러오지 못했습니다. 다시 불러오거나 본문·질문을 이용해주세요.",
        true,
      );
      reloadVideo.hidden = false;
    },
    options,
  );
  for (const event of ["waiting", "stalled"])
    player.addEventListener(
      event,
      () => {
        mediaMessage(
          "영상 데이터를 기다리고 있습니다. 계속 멈춰 있으면 다시 불러오기를 이용해주세요.",
        );
        reloadVideo.hidden = false;
      },
      options,
    );
  player.addEventListener(
    "canplay",
    () =>
      mediaMessage("영상이 준비됐습니다. 재생·이동·자막 버튼을 이용하세요."),
    options,
  );
  retry.addEventListener(
    "click",
    () => {
      if (!pending || blocked) return;
      uncertain = false;
      queued = Math.round(player.currentTime * 1000) / 1000;
      pump();
    },
    options,
  );
  refresh.addEventListener("click", () => ctx.reload(), options);
  root
    .querySelector("#watch-login")
    ?.addEventListener("click", () => ctx.requireAuth(), options);
  root.querySelector("#watch-enroll")?.addEventListener(
    "click",
    async (event) => {
      if (!ctx.requireAuth()) return;
      event.currentTarget.disabled = true;
      let enrolled = false;
      try {
        await ctx.api("/api/enrollments", {
          method: "POST",
          body: { courseId: course.id },
          ownerId,
        });
        enrolled = true;
        if (interacted && ready && player.currentTime > 0)
          await ctx.api("/api/watch", {
            method: "PUT",
            body: session.prepare(player.currentTime),
            ownerId,
          });
        ctx.toast("무료 강의를 등록했습니다. 이어보기 저장을 시작합니다.");
        ctx.reload();
      } catch (error) {
        show(
          (enrolled ? "무료 등록은 완료됐습니다. " : "") + error.message,
          true,
        );
        refresh.hidden = false;
        event.target.disabled = false;
      }
    },
    options,
  );
  window.addEventListener("hashchange", leave, { ...options, capture: true });
  window.addEventListener("pagehide", leave, options);
  document.addEventListener(
    "visibilitychange",
    () => {
      if (document.hidden) save();
    },
    options,
  );
  const observer = new MutationObserver(() => {
    if (!root.isConnected) {
      disposed = true;
      controller.abort();
      observer.disconnect();
    }
  });
  observer.observe(document.body, { childList: true, subtree: true });
}
