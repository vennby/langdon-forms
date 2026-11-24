function watch(area, token) {
  let last = "";
  let kind = "type";
  let inputType = "";
  let queue = [];
  let chain = Promise.resolve();
  let stopped = false;
  let sinceCheckpoint = 0;
  let lastCursor = "";
  let suppressCursorUntil = 0;
  const t0 = performance.now();

  function now() {
    return Math.max(0, Math.round(performance.now() - t0));
  }
  function cpIndex(str, utf16) {
    const end = Math.max(0, Math.min(utf16 | 0, str.length));
    let i = 0;
    let cp = 0;
    while (i < end) {
      const code = str.charCodeAt(i);
      if (code >= 0xD800 && code <= 0xDBFF && i + 1 < end) {
        const next = str.charCodeAt(i + 1);
        if (next >= 0xDC00 && next <= 0xDFFF) { i += 2; cp += 1; continue; }
      }
      i += 1;
      cp += 1;
    }
    return cp;
  }
  function diffText(before, after) {
    const a = Array.from(before);
    const b = Array.from(after);
    let i = 0;
    const n = Math.min(a.length, b.length);
    while (i < n && a[i] === b[i]) i += 1;
    let j = 0;
    while (j < a.length - i && j < b.length - i && a[a.length - 1 - j] === b[b.length - 1 - j]) j += 1;
    return { pos: i, deleted: a.slice(i, a.length - j).join(""), inserted: b.slice(i, b.length - j).join("") };
  }
  function emit(event) { queue.push(event); if (queue.length >= 25) flush(); }
  function flush(keepalive) {
    if (!queue.length) return chain;
    const batch = queue.splice(0, queue.length);
    const body = JSON.stringify({ token, events: batch });
    if (keepalive && navigator.sendBeacon) {
      navigator.sendBeacon("/api/events", new Blob([body], { type: "application/json" }));
      return chain;
    }
    chain = chain.then(async () => {
      const response = await fetch("/api/events", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body,
        signal: AbortSignal.timeout(8000)
      });
      if (!response.ok) throw new Error(await response.text());
    }).catch(() => { queue = batch.concat(queue); });
    return chain;
  }
  area.addEventListener("beforeinput", (event) => {
    inputType = event.inputType || "";
    if (inputType === "insertFromPaste" || inputType === "insertFromDrop") kind = "paste";
    else if (inputType === "historyUndo" || inputType === "historyRedo") kind = "history";
    else kind = "type";
  });
  area.addEventListener("paste", () => { kind = "paste"; });
  area.addEventListener("input", () => {
    const next = area.value;
    const change = diffText(last, next);
    const t = now();
    let mode = kind;
    if (mode === "type" && Array.from(change.inserted).length >= 80 && inputType !== "insertCompositionText" && inputType !== "insertFromComposition") {
      mode = "paste";
    }
    if (change.deleted) emit({ t, op: mode === "history" ? "history_delete" : "delete", pos: change.pos, text: change.deleted });
    if (change.inserted) {
      let op = "insert";
      if (mode === "paste") op = "paste";
      else if (mode === "history") op = "history_insert";
      emit({ t, op, pos: change.pos, text: change.inserted });
    }
    if (change.deleted || change.inserted) {
      last = next;
      sinceCheckpoint += 1;
      if (sinceCheckpoint >= 200) {
        sinceCheckpoint = 0;
        emit({ t: now(), op: "checkpoint", text: next });
      }
    }
    kind = "type";
    inputType = "";
    suppressCursorUntil = performance.now() + 120;
  });
  area.addEventListener("focus", () => { emit({ t: now(), op: "focus" }); });
  area.addEventListener("blur", () => { emit({ t: now(), op: "blur" }); flush(); });
  document.addEventListener("selectionchange", () => {
    if (document.activeElement !== area || performance.now() < suppressCursorUntil) return;
    const backward = area.selectionDirection === "backward";
    const start = cpIndex(area.value, area.selectionStart);
    const end = cpIndex(area.value, area.selectionEnd);
    const pos = backward ? start : end;
    const anchor = backward ? end : start;
    const key = pos + ":" + anchor;
    if (key === lastCursor) return;
    lastCursor = key;
    emit({ t: now(), op: "cursor", pos, anchor });
  });
  window.addEventListener("pagehide", () => flush(true));
  const timer = setInterval(() => { if (!stopped) flush(); }, 400);
  return {
    flush,
    stop() { stopped = true; clearInterval(timer); }
  };
}
