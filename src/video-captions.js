const endpoint = (course, lesson) =>
  `/api/courses/${encodeURIComponent(course.id)}/lessons/${encodeURIComponent(lesson.id)}/captions`;
export function captionTrackMarkup(captions, course, lesson, e) {
  return captions
    .map(
      (c) =>
        `<track data-caption-id="${e(c.id)}" kind="${e(c.kind)}" srclang="${e(c.language)}" label="${e(c.label)}" src="${endpoint(course, lesson)}/${encodeURIComponent(c.id)}">`,
    )
    .join("");
}
export function captionControls(lesson, ctx) {
  const captions = lesson.captions || [],
    e = ctx.esc;
  return `<div class="v-caption-controls"><label>자막 언어<select id="video-caption-select" ${captions.length ? "" : "disabled"}><option value="">사용 안 함</option>${captions.map((c) => `<option value="${e(c.id)}">${e(c.label)} · ${e(c.language)}</option>`).join("")}</select></label><button type="button" class="btn btn-quiet btn-sm" id="video-caption-toggle" aria-pressed="false" ${captions.length ? "" : "disabled"}>자막 켜기</button><button type="button" class="btn btn-quiet btn-sm" id="video-caption-refresh">승인 자막 새로 확인</button><p class="v-note" id="video-caption-status" role="status">${captions.length ? "승인된 자막을 선택하거나 영상의 자막 메뉴를 이용하세요." : "이 영상에 제공 중인 자막이 없습니다. 본문과 질문을 함께 이용하세요."}</p></div>`;
}
export function bindVideoCaptions(
  root,
  player,
  lesson,
  course,
  ctx,
  { signal, onPermissionError, onLessonChanged },
) {
  const select = root.querySelector("#video-caption-select"),
    toggle = root.querySelector("#video-caption-toggle"),
    refresh = root.querySelector("#video-caption-refresh"),
    status = root.querySelector("#video-caption-status"),
    route = location.hash,
    ownerId = ctx.user?.id || null;
  const live = () => root.isConnected && location.hash === route;
  const tracks = () => [...player.querySelectorAll("track[data-caption-id]")];
  function sync() {
    if (!live()) return;
    const showing = tracks().find((node) => node.track.mode === "showing");
    select.value = showing?.dataset.captionId || "";
    toggle.textContent = showing ? "자막 끄기" : "자막 켜기";
    toggle.setAttribute("aria-pressed", String(Boolean(showing)));
  }
  function bindTracks() {
    tracks().forEach((node) => {
      node.addEventListener(
        "load",
        () => {
          if (live() && node.isConnected && node.track.mode === "showing")
            status.textContent = `${node.label} 자막을 표시합니다.`;
        },
        { signal },
      );
      node.addEventListener(
        "error",
        () => {
          if (live() && node.isConnected) {
            status.textContent =
              "자막을 불러오지 못했습니다. 승인 자막을 새로 확인하거나 본문·질문을 이용해주세요.";
          }
        },
        { signal },
      );
    });
  }
  function choose(id) {
    tracks().forEach((node) => {
      node.track.mode = node.dataset.captionId === id ? "showing" : "disabled";
    });
    sync();
    const node = tracks().find((node) => node.dataset.captionId === id);
    status.textContent = node
      ? node.readyState === 3
        ? "자막을 불러오지 못했습니다. 승인 자막을 새로 확인해주세요."
        : node.readyState === 2
          ? `${node.label} 자막을 표시합니다.`
          : "선택한 자막을 불러옵니다…"
      : "자막 표시를 껐습니다.";
  }
  select.addEventListener("change", () => choose(select.value), { signal });
  toggle.addEventListener(
    "click",
    () =>
      choose(
        toggle.getAttribute("aria-pressed") === "true"
          ? ""
          : select.value || tracks()[0]?.dataset.captionId || "",
      ),
    { signal },
  );
  player.textTracks.addEventListener("change", sync, { signal });
  bindTracks();
  refresh.addEventListener(
    "click",
    async () => {
      if (refresh.disabled) return;
      refresh.disabled = true;
      status.textContent = "현재 승인 자막과 수강 상태를 확인합니다…";
      try {
        const data = await ctx.api(endpoint(course, lesson), { ownerId });
        if (!live()) return;
        if (
          data.lessonVersion !== lesson.version ||
          data.videoAssetId !== lesson.videoAssetId
        ) {
          status.textContent =
            "영상/수업이 변경됐습니다. 최신 수업을 확인해주세요.";
          onLessonChanged();
          return;
        }
        const selected = select.value;
        tracks().forEach((node) => {
          node.track.mode = "disabled";
          node.remove();
        });
        select.innerHTML = '<option value="">사용 안 함</option>';
        for (const caption of data.captions) {
          const option = document.createElement("option");
          option.value = caption.id;
          option.textContent = `${caption.label} · ${caption.language}`;
          select.append(option);
          const track = document.createElement("track");
          track.dataset.captionId = caption.id;
          track.kind = caption.kind;
          track.srclang = caption.language;
          track.label = caption.label;
          track.src =
            endpoint(course, lesson) + "/" + encodeURIComponent(caption.id);
          player.append(track);
        }
        bindTracks();
        select.disabled = toggle.disabled = !data.captions.length;
        const same = data.captions.some((c) => c.id === selected);
        choose(same ? selected : "");
        if (!same)
          status.textContent = data.captions.length
            ? "승인 자막 목록을 갱신했습니다. 제공할 자막을 선택해주세요."
            : "현재 제공 중인 승인 자막이 없습니다. 본문과 질문을 이용하세요.";
      } catch (error) {
        if (!live()) return;
        status.textContent = error.message;
        if ([401, 403, 404, 409].includes(error.status)) {
          tracks().forEach((node) => {
            node.track.mode = "disabled";
            node.remove();
          });
          select.value = "";
          select.disabled = toggle.disabled = true;
          onPermissionError(error);
        }
      } finally {
        if (live()) refresh.disabled = false;
      }
    },
    { signal },
  );
}
