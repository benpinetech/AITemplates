// wwwroot/live-listen.js
// v24.12.05+hotfix - Deepgram live routing + transcript + AI → Blazor bridge
// Fixes:
//  - strongCase now supports 8+ digit numeric case ids (e.g., 25-098765 => 25098765)
//  - extractCaseTokens includes 8-digit numeric tokens and spoken 8+ digit collapses
//  - keeps cross-case contamination guard

(function () {
    "use strict";

    var log = function () { console.log.apply(console, ["[live-listen]"].concat([].slice.call(arguments))); };
    var warn = function () { console.warn.apply(console, ["[live-listen]"].concat([].slice.call(arguments))); };

    // ---------- global state ----------
    var hub = null;
    var hubReady = null;
    var hubConfig = null;

    var audioCtx = null;
    var workletNode = null;
    var mediaStream = null;

    var sessionId = null;
    var listening = false;
    var currentEventId = 0;

    var lastSwitchAt = 0;
    var cooldownMs = 3500;
    var lockUntil = 0;
    var suppressUntil = 0;
    var recentVisit = new Map();

    var interimBaseByEvent = new Map();

    // AI handler state
    var aiUpdateHandlerInstalled = false;
    var lastAiSummaryByEvent = new Map();
    var aiDotNetRef = null; // set by liveListen.registerAiHandler

    // ---------- DOM helpers ----------
    function allRows() {
        return Array.prototype.slice.call(document.querySelectorAll("[data-event-id]"));
    }

    function getEventIdFromRow(row) {
        if (!row) return 0;
        var id = parseInt(row.getAttribute("data-event-id") || "", 10);
        return isFinite(id) ? id : 0;
    }

    function closest(el, sel) {
        while (el && el !== document) {
            if (el.matches && el.matches(sel)) return el;
            el = el.parentNode;
        }
        return null;
    }

    // ---------- visuals ----------
    var listenStylesInjected = false;
    function injectListenStyles() {
        if (listenStylesInjected) return;
        var css = ""
            + "[data-event-id].listening { background-color: rgba(255,220,120,.25) !important; }\n"
            + "[data-event-id].listening textarea,\n"
            + "[data-event-id].listening input,\n"
            + "[data-event-id].listening .form-control { background-color:#fff9d6 !important; transition: background-color .15s; }\n"
            + ".badge-live { display:inline-block;margin-left:.5rem;padding:.15rem .4rem;border-radius:.5rem;background:#dc3545;color:#fff;font-size:.75rem;font-weight:600; }\n";
        var s = document.createElement("style");
        s.textContent = css;
        document.head.appendChild(s);
        listenStylesInjected = true;
    }

    function setLiveBadge(eventId, on) {
        var row = document.querySelector('[data-event-id="' + eventId + '"]');
        if (!row) return;
        var title = row.querySelector("h5,.case-title,.card-title,.row-title,.col h5,.col .title") || row;
        var b = row.querySelector(".badge-live");
        if (on) {
            if (!b) {
                b = document.createElement("span");
                b.className = "badge-live";
                b.textContent = "LIVE";
                title.appendChild(b);
            }
        } else if (b && b.parentNode) {
            b.parentNode.removeChild(b);
        }
    }

    function clearAllLiveUI() {
        Array.prototype.slice.call(document.querySelectorAll("[data-event-id].listening"))
            .forEach(function (r) { r.classList.remove("listening", "bg-warning-subtle", "border-warning"); });

        Array.prototype.slice.call(document.querySelectorAll("[data-event-id] .badge-live"))
            .forEach(function (b) { if (b && b.parentNode) b.parentNode.removeChild(b); });
    }

    function ensureRowListeningClass(eventId, on) {
        var row = document.querySelector('[data-event-id="' + eventId + '"]');
        if (!row) return;

        var wrappers = [row];
        var li = row.closest ? row.closest(".list-group-item") : null; if (li) wrappers.push(li);
        var card = (row.closest && row.closest(".card")) || row.querySelector(".card"); if (card) wrappers.push(card);
        var body = (row.closest && row.closest(".card-body")) || row.querySelector(".card-body"); if (body) wrappers.push(body);
        var r = row.closest ? row.closest(".row") : null; if (r) wrappers.push(r);

        var ACTIVE_BG = "bg-warning-subtle";
        var ACTIVE_BORDER = "border-warning";

        for (var i = 0; i < wrappers.length; i++) {
            var el = wrappers[i];
            if (!el) continue;
            if (on) el.classList.add("listening", ACTIVE_BG, ACTIVE_BORDER);
            else el.classList.remove("listening", ACTIVE_BG, ACTIVE_BORDER);
        }

        setLiveBadge(eventId, !!on);

        if (on) {
            try { row.scrollIntoView({ behavior: "smooth", block: "center" }); } catch (_) { }
        }
    }

    // ---------- transcript textarea helpers ----------
    function visibleTextareasInRow(row) {
        var all = Array.prototype.slice.call(row.querySelectorAll("textarea"));
        return all.filter(function (ta) {
            var cs = window.getComputedStyle ? window.getComputedStyle(ta) : null;
            if (!cs) return true;
            if (cs.display === "none" || cs.visibility === "hidden") return false;
            if ((ta.offsetWidth === 0 && ta.offsetHeight === 0) && cs.overflow !== "visible") return false;
            return true;
        });
    }

    function tagTranscriptAreasForRow(row) {
        if (!row) return;
        if (row.querySelector("textarea[data-transcript]")) return;

        var labels = Array.prototype.slice.call(row.querySelectorAll("b,strong,label,div,span,h5"));
        for (var i = 0; i < labels.length; i++) {
            var el = labels[i];
            var txt = (el.textContent || "").replace(/\s+/g, " ").trim().toLowerCase();
            if (!txt || txt.indexOf("transcript") < 0) continue;

            var block = el.closest("div");
            if (block) {
                var t1 = block.querySelector("textarea");
                if (t1) { t1.setAttribute("data-transcript", ""); return; }
            }
            var sib = el.nextElementSibling, guard = 0;
            while (sib && sib !== row && guard++ < 20) {
                if (sib.tagName === "TEXTAREA") { sib.setAttribute("data-transcript", ""); return; }
                var inner = sib.querySelector ? sib.querySelector("textarea") : null;
                if (inner) { inner.setAttribute("data-transcript", ""); return; }
                sib = sib.nextElementSibling;
            }
        }

        var vis = visibleTextareasInRow(row);
        if (vis.length >= 2) vis[1].setAttribute("data-transcript", "");
    }

    function tagTranscriptAreas() { allRows().forEach(tagTranscriptAreasForRow); }

    function openDetailsAndTag(eventId) {
        var row = document.querySelector('[data-event-id="' + eventId + '"]');
        if (!row) return;
        if (row.querySelector("textarea[data-transcript]")) return;

        var toggles = Array.prototype.slice.call(row.querySelectorAll("a,button")).filter(function (b) {
            var t = (b.textContent || "").toLowerCase();
            return t.indexOf("show details") >= 0 || t === "details" || t.indexOf("details") >= 0;
        });
        if (toggles.length) {
            try { toggles[0].click(); } catch (_) { }
        }
        setTimeout(function () { tagTranscriptAreasForRow(row); }, 60);
    }

    function getTranscriptTextarea(eventId) {
        var row = document.querySelector('[data-event-id="' + eventId + '"]');
        if (!row) return null;

        var tagged = row.querySelector("textarea[data-transcript]");
        if (tagged) return tagged;

        var vis = visibleTextareasInRow(row);
        if (vis.length <= 1) { openDetailsAndTag(eventId); return null; }

        tagTranscriptAreasForRow(row);
        var again = row.querySelector("textarea[data-transcript]");
        if (again) return again;

        return vis.length >= 2 ? vis[1] : null;
    }

    function notifyBoundInput(el) {
        try {
            el.dispatchEvent(new Event("input", { bubbles: true }));
            el.dispatchEvent(new Event("change", { bubbles: true }));
        } catch (_) { }
    }

    // ---------- tokenization / scoring ----------
    var DIGITS = {
        "zero": "0", "oh": "0", "o": "0",
        "one": "1", "two": "2", "three": "3", "four": "4", "for": "4", "five": "5",
        "six": "6", "seven": "7", "eight": "8", "nine": "9",
        "ten": "10", "eleven": "11", "twelve": "12", "thirteen": "13", "fourteen": "14",
        "fifteen": "15", "sixteen": "16", "seventeen": "17", "eighteen": "18", "nineteen": "19", "twenty": "20"
    };

    function normalize(s) { return (s || "").toLowerCase().replace(/[^a-z0-9]/g, ""); }

    function collapseSpokenCase(text) {
        var words = (text || "").toLowerCase().split(/\s+/), out = "";
        for (var i = 0; i < words.length; i++) {
            var w = words[i];
            if (w === "dash" || w === "hyphen") continue;
            if (DIGITS[w] != null) { out += DIGITS[w]; continue; }
            if (/^[a-z]$/.test(w)) { out += w; continue; }
            if (/^\d+$/.test(w)) { out += w; continue; }
            if (/^(tc|ti|tl|td)$/i.test(w)) { out += w.toLowerCase(); continue; }
        }
        return out;
    }

    function extractCaseTokens(text) {
        var raw = text || "";
        var compact = normalize(raw);
        var spoken = collapseSpokenCase(raw);
        var toks = [], m;

        // e.g. 25cr1234567
        m = compact.match(/\d{2}[a-z]{1,2}\d{5,7}/);
        if (m) toks.push(m[0]);

        // NEW: include 8-digit runs (covers 25-098765 => 25098765)
        var re8 = /\d{8}/g;
        while ((m = re8.exec(compact)) !== null) toks.push(m[0]);

        // keep existing 5-7 digit runs too
        var re = /\d{5,7}/g;
        while ((m = re.exec(compact)) !== null) toks.push(m[0]);

        // NEW: spoken collapse threshold 8+ (was 9+)
        if (spoken.length >= 8) toks.push(spoken);

        var uniq = [];
        for (var i = 0; i < toks.length; i++) if (uniq.indexOf(toks[i]) < 0) uniq.push(toks[i]);
        return uniq;
    }

    var STOP = { "the": 1, "a": 1, "an": 1, "of": 1, "and": 1, "or": 1, "to": 1, "for": 1, "in": 1, "on": 1, "at": 1, "is": 1, "it": 1, "he": 1, "she": 1, "they": 1, "we": 1, "you": 1, "i": 1, "this": 1, "that": 1 };

    function extractNameTokens(text) {
        var words = (text || "").toLowerCase().match(/[a-z]+/g) || [], kept = [];
        for (var i = 0; i < words.length; i++) {
            var w = words[i];
            if (!STOP[w] && w.length >= 3 && kept.indexOf(w) < 0) kept.push(w);
            if (kept.length >= 4) break;
        }
        return kept;
    }

    function getRowHeaderEl(row) {
        return row.querySelector("h5,.case-title,.card-title,.row-title,.col h5,.col .title") || row.firstElementChild || row;
    }

    function scoreRowAgainstTokens(row, caseTokens, nameTokens) {
        var header = getRowHeaderEl(row);
        var headerText = (header && header.textContent ? header.textContent : (row.textContent || "")).toLowerCase();
        var headerNorm = normalize(headerText);
        var rowTextNorm = normalize(row.textContent || "");

        var score = 0;
        var strongCase = false;

        for (var i = 0; i < caseTokens.length; i++) {
            var tok = caseTokens[i];
            if (!tok) continue;
            var tokLower = tok.toLowerCase();

            // NEW: strong numeric case tokens are 8+ (was 9+)
            if (tok.length >= 8) {
                if (headerNorm.indexOf(tokLower) >= 0) {
                    score += 120;
                    strongCase = true;
                } else if (rowTextNorm.indexOf(tokLower) >= 0) {
                    score += 40;
                }
            } else {
                if (headerNorm.indexOf(tokLower) >= 0) score += 20;
                else if (rowTextNorm.indexOf(tokLower) >= 0) score += 10;
            }
        }

        var nameHits = 0;
        for (var j = 0; j < nameTokens.length; j++) {
            var n = nameTokens[j];
            if (!n) continue;
            if (headerText.indexOf(n) >= 0) { score += 25; nameHits++; }
            else if (rowTextNorm.indexOf(n) >= 0) { score += 8; nameHits++; }
        }
        if (nameHits >= 2) score += 10;

        if (getEventIdFromRow(row) === currentEventId) score += 5;

        return { score: score, strongCase: strongCase };
    }

    // ---------- AI update handler ----------
    function registerAiUpdateHandlerOnHub(h) {
        if (aiUpdateHandlerInstalled) return;
        aiUpdateHandlerInstalled = true;

        h.on("AiUpdate", function (sid, eventId, summary, suggestions) {
            log("AiUpdate for event", eventId, "summary len:", (summary || "").length,
                "suggestions:", suggestions ? suggestions.length : 0);

            if (lastAiSummaryByEvent.get(eventId) === summary) {
                log("AiUpdate: duplicate summary ignored for event", eventId);
            } else {
                lastAiSummaryByEvent.set(eventId, summary);
            }

            var detail = {
                sessionId: sid,
                eventId: eventId,
                summary: summary,
                suggestions: suggestions || []
            };

            try { window.dispatchEvent(new CustomEvent("ai:update", { detail: detail })); }
            catch (err2) { warn("ai:update dispatch error", err2); }

            if (aiDotNetRef && typeof aiDotNetRef.invokeMethodAsync === "function") {
                try { aiDotNetRef.invokeMethodAsync("OnLiveAiUpdate", detail); }
                catch (err3) { warn("OnLiveAiUpdate invoke error", err3); }
            }
        });
    }

    // ---------- SignalR hub ----------
    function connectHub() {
        if (hubReady) return hubReady;

        var cfg = hubConfig || {};
        var url = cfg.hubUrl || "/hubs/live-transcribe";
        var accessToken = cfg.accessToken || "";

        hub = new signalR.HubConnectionBuilder()
            .withUrl(url, { accessTokenFactory: function () { return accessToken; } })
            .withHubProtocol(new signalR.protocols.msgpack.MessagePackHubProtocol())
            .withAutomaticReconnect()
            .build();

        hub.on("Transcript", function (sid, eventId, isFinal, text) {
            var rows = allRows();
            var caseTokens = extractCaseTokens(text);
            var nameTokens = extractNameTokens(text);

            var bestRow = null, bestScore = 0, bestMeta = null;
            for (var i = 0; i < rows.length; i++) {
                var s = scoreRowAgainstTokens(rows[i], caseTokens, nameTokens);
                if (s.score > bestScore) { bestScore = s.score; bestRow = rows[i]; bestMeta = s; }
            }
            var targetId = bestRow ? getEventIdFromRow(bestRow) : 0;

            // NEW: "strong" token threshold is 8+
            var hasStrongToken = caseTokens.some(function (t) { return t && t.length >= 8; });
            var strongCase = bestMeta && bestMeta.strongCase;
            var hasNames = nameTokens.length > 0;

            // Off-docket protection
            var offDocketStrongCase = hasStrongToken && bestScore <= 0;
            if (offDocketStrongCase) {
                log("Transcript: strong case token not found on current docket. Clearing currentEventId and skipping chunk.");
                if (currentEventId) { try { ensureRowListeningClass(currentEventId, false); } catch (_) { } }
                currentEventId = 0;
                return;
            }

            // Routing / switching:
            if (!firstRouteDone()) {
                if (hasStrongToken && strongCase) {
                    maybeSwitchFromText(sid, text, true);
                } else if (!hasStrongToken && hasNames) {
                    // Name-only fallback ONLY for initial lock
                    maybeSwitchFromText(sid, text, true);
                }
            } else {
                // Subsequent routing: case-token based
                if (currentEventId && targetId && targetId !== currentEventId && strongCase) {
                    var now = Date.now();
                    var lastSeen = recentVisit.get(targetId) || 0;
                    var recentBlockMs = 6000;

                    if ((now >= lockUntil || strongCase) &&
                        ((now - lastSeen) >= recentBlockMs || strongCase)) {
                        maybeSwitchFromText(sid, text, false);
                    }
                }
            }

            // Cross-case contamination guard
            if (strongCase && targetId && currentEventId && targetId !== currentEventId) {
                var now2 = Date.now();
                if (now2 < lockUntil || now2 < suppressUntil) {
                    log("Transcript: strong case hit for different event", targetId,
                        "while locked on", currentEventId, "-> skipping write to avoid contamination.");
                    return;
                }
            }

            // Decide where to write
            var writeId = currentEventId;
            if (!writeId) return;

            var ta = getTranscriptTextarea(writeId);
            if (!ta) return;

            if (isFinal) {
                var base = interimBaseByEvent.has(writeId) ? interimBaseByEvent.get(writeId) : (ta.value || "");
                var finalText = /\n$/.test(text) ? text : (text + "\n");
                ta.value = base + finalText;
                interimBaseByEvent.delete(writeId);
            } else {
                if (!interimBaseByEvent.has(writeId)) interimBaseByEvent.set(writeId, (ta.value || ""));
                var b = interimBaseByEvent.get(writeId);
                var sep = (b && !/\s$/.test(b)) ? " " : "";
                ta.value = b + sep + text;
            }

            try {
                ta.style.height = "auto";
                ta.style.height = (ta.scrollHeight + 6) + "px";
            } catch (_) { }
            notifyBoundInput(ta);

            if (isFinal) maybeSwitchFromText(sid, text, false);
        });

        registerAiUpdateHandlerOnHub(hub);

        hubReady = hub.start()
            .then(function () { log("hub connected", url); })
            .catch(function (err) { warn("hub start failed", err); throw err; });

        return hubReady;
    }

    function firstRouteDone() { return lastSwitchAt !== 0; }

    function maybeSwitchFromText(sid, text, force) {
        var rows = allRows();
        var caseTokens = extractCaseTokens(text);
        var nameTokens = extractNameTokens(text);
        if (!caseTokens.length && !nameTokens.length) return;

        var bestRow = null, bestScore = 0, bestMeta = null;
        for (var i = 0; i < rows.length; i++) {
            var s = scoreRowAgainstTokens(rows[i], caseTokens, nameTokens);
            if (s.score > bestScore) { bestScore = s.score; bestRow = rows[i]; bestMeta = s; }
        }
        if (!bestRow) return;
        var targetId = getEventIdFromRow(bestRow);

        var strongCase = bestMeta && bestMeta.strongCase;
        if (!strongCase && !force) return;

        var now = Date.now();
        if (currentEventId !== 0 && now < lockUntil && !force) return;

        var recentBlockMs = 6000;
        if (recentVisit.has(targetId) && (now - recentVisit.get(targetId)) < recentBlockMs && !force) return;
        if (!targetId || targetId === currentEventId) return;

        if (currentEventId && interimBaseByEvent.has(currentEventId)) {
            var oldTa = getTranscriptTextarea(currentEventId);
            if (oldTa) notifyBoundInput(oldTa);
            interimBaseByEvent.delete(currentEventId);
        }

        if (currentEventId) ensureRowListeningClass(currentEventId, false);
        currentEventId = targetId;
        ensureRowListeningClass(currentEventId, true);
        openDetailsAndTag(currentEventId);

        hub.invoke("SwitchEvent", sid, currentEventId).then(function () {
            var t = Date.now();
            lastSwitchAt = t;
            lockUntil = t + cooldownMs;
            recentVisit.set(currentEventId, t);
            log("speech-switch → event", currentEventId);
        }).catch(function (e) { warn("SwitchEvent error", e); });
    }

    // ---------- audio lifecycle ----------
    async function startListen(config) {
        hubConfig = config || hubConfig || {};

        await connectHub();
        injectListenStyles();
        try { tagTranscriptAreas(); } catch (_) { }

        clearAllLiveUI();
        suppressUntil = Date.now() + 1200;

        Array.prototype.slice.call(document.querySelectorAll("[data-event-id][data-active='true']"))
            .forEach(function (r) { r.removeAttribute("data-active"); });

        interimBaseByEvent.clear();
        recentVisit.clear();
        lastSwitchAt = 0;

        currentEventId = 0;
        lockUntil = 0;

        sessionId = await hub.invoke("StartSession", 0);
        log("StartSession ->", sessionId);

        if (!audioCtx) {
            var AC = window.AudioContext || window.webkitAudioContext;
            audioCtx = new AC({ sampleRate: hubConfig.sampleRate || 48000 });
        }

        mediaStream = await navigator.mediaDevices.getUserMedia({
            audio: { channelCount: 1, noiseSuppression: true, echoCancellation: true },
            video: false
        });

        if (!audioCtx.audioWorklet) { warn("AudioWorklet unavailable"); return; }
        if (!window.__liveListenWorkletLoaded) {
            var blob = new Blob([`
class Pcm16Mono extends AudioWorkletProcessor {
  process(inputs) {
    const i = inputs[0];
    if (!i || !i[0]) return true;
    const ch = i[0];
    const out = new Int16Array(ch.length);
    for (let k = 0; k < ch.length; k++) {
      let s = Math.max(-1, Math.min(1, ch[k]));
      out[k] = s < 0 ? s * 0x8000 : s * 0x7FFF;
    }
    this.port.postMessage(out.buffer, [out.buffer]);
    return true;
  }
}
registerProcessor("pcm16mono", Pcm16Mono);
`], { type: "application/javascript" });
            var url = URL.createObjectURL(blob);
            await audioCtx.audioWorklet.addModule(url);
            window.__liveListenWorkletLoaded = true;
        }

        var src = audioCtx.createMediaStreamSource(mediaStream);
        workletNode = new AudioWorkletNode(audioCtx, "pcm16mono", { numberOfInputs: 1, numberOfOutputs: 0 });
        src.connect(workletNode);

        var sawFirst = false;
        workletNode.port.onmessage = function (e) {
            if (!sessionId || !listening) return;
            var bytes = new Uint8Array(e.data);
            if (!sawFirst) { log("first audio frame", bytes.byteLength, "bytes"); sawFirst = true; }
            hub.invoke("PushAudio", sessionId, bytes).catch(function (err) {
                warn("PushAudio error:", err && err.message ? err.message : err);
            });
        };

        listening = true;
        log("listening started");
    }

    async function stopListen() {
        var base = interimBaseByEvent.get(currentEventId);
        if (typeof base !== "undefined") {
            var ta = getTranscriptTextarea(currentEventId);
            if (ta && ta.value && ta.value.length >= base.length) notifyBoundInput(ta);
            interimBaseByEvent.delete(currentEventId);
        }
        listening = false;

        try {
            if (workletNode) {
                try { workletNode.disconnect(); } catch (_) { }
                workletNode.port.onmessage = null;
                workletNode = null;
            }
            if (audioCtx && audioCtx.state !== "closed") {
                try { await audioCtx.close(); } catch (_) { }
            }
        } catch (_) { }
        audioCtx = null;

        try { if (mediaStream) mediaStream.getTracks().forEach(function (t) { t.stop(); }); } catch (_) { }
        mediaStream = null;

        if (hub && sessionId) {
            try { await hub.invoke("StopSession", sessionId); } catch (e) { warn("StopSession error", e); }
        }

        clearAllLiveUI();
        log("listening stopped");
    }

    async function switchEventIfChanged() {
        if (!listening || !hub || !sessionId) return;
        if (Date.now() < suppressUntil) return;

        var row = document.querySelector("[data-event-id][data-active='true']");
        if (!row) return;
        var nextEventId = getEventIdFromRow(row);
        if (!nextEventId || nextEventId === currentEventId) return;

        if (currentEventId && interimBaseByEvent.has(currentEventId)) {
            var oldTa = getTranscriptTextarea(currentEventId);
            if (oldTa) notifyBoundInput(oldTa);
            interimBaseByEvent.delete(currentEventId);
        }

        if (currentEventId) ensureRowListeningClass(currentEventId, false);
        currentEventId = nextEventId;
        ensureRowListeningClass(currentEventId, true);
        openDetailsAndTag(currentEventId);

        try {
            await hub.invoke("SwitchEvent", sessionId, currentEventId);
            var now = Date.now();
            lastSwitchAt = now;
            lockUntil = now + cooldownMs;
            recentVisit.set(currentEventId, now);
            log("switched to event", currentEventId);
        } catch (e) { warn("SwitchEvent error", e); }
    }

    // ---------- public API ----------
    window.liveListen = {
        toggle: function (config) { return listening ? stopListen() : startListen(config); },
        start: function (config) { if (!listening) return startListen(config); },
        stop: function () { if (listening) return stopListen(); },
        switchEvent: function () { return switchEventIfChanged(); },

        registerAiHandler: function (dotNetRef) {
            aiDotNetRef = dotNetRef;
            log("[live-listen] AI handler registered from Blazor.");
        }
    };

    // ---------- wiring ----------
    document.addEventListener("click", function (e) {
        if (closest(e.target, "#btn-listen,#btn-stop,[data-action='listen'],[data-action='stop'],[data-live-control],.listen-toggle,.bulk-update,.toolbar")) return;
        try { tagTranscriptAreas(); } catch (_) { }
        var row = closest(e.target, "[data-event-id]");
        if (!row) return;
        Array.prototype.slice.call(document.querySelectorAll("[data-event-id][data-active='true']"))
            .forEach(function (r) { r.removeAttribute("data-active"); });
        row.setAttribute("data-active", "true");
        if (window.liveListen && typeof window.liveListen.switchEvent === "function") window.liveListen.switchEvent();
    });

    document.addEventListener("change", function (e) {
        if (closest(e.target, "#btn-listen,#btn-stop,[data-live-control],.listen-toggle,.bulk-update,.toolbar")) return;
        try { tagTranscriptAreas(); } catch (_) { }
        var el = e.target;
        if (!el) return;
        if ((el.matches && el.matches("[data-event-id] input[type='checkbox']")) || el.id === "btn-next-case") {
            if (window.liveListen && typeof window.liveListen.switchEvent === "function") window.liveListen.switchEvent();
        }
    });

    try { tagTranscriptAreas(); injectListenStyles(); } catch (_) { }
})();
