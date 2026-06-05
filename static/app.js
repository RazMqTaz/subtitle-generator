// Frontend for the subtitle generator demo.
//
// Submit kicks off a job on the server. The job runs as a subprocess; we poll
// its status every two seconds until it completes or fails, then surface the
// per-file download links. When the user closes or refreshes the page, we
// fire a best-effort cleanup request via sendBeacon so their files don't
// linger on the server.

const POLL_INTERVAL_MS = 2000;

const $ = (id) => document.getElementById(id);

const form = $("job-form");
const submitBtn = $("submit-btn");
const fileInput = $("files");
const dropZone = $("drop-zone");
const fileList = $("file-list");
const statusSection = $("status-section");
const statusText = $("status-text");
const statusDot = $("status-indicator");
const eventLog = $("event-log");
const resultsSection = $("results-section");
const resultsList = $("results-list");
const resetBtn = $("reset-btn");

const keyFields = {
    soniox_api_key: $("soniox-key"),
    tmdb_read_access_token: $("tmdb-key"),
    anthropic_api_key: $("anthropic-key"),
};

const STATUS_STYLES = {
    queued:    { text: "Queued",    dot: "bg-slate-400" },
    running:   { text: "Running",   dot: "bg-amber-400 animate-pulse" },
    completed: { text: "Completed", dot: "bg-emerald-500" },
    failed:    { text: "Failed",    dot: "bg-rose-500" },
};

let pollHandle = null;
let activeJobId = null;
let renderedEventCount = 0;


// File picker + drag and drop --------------------------------------------

function formatBytes(bytes) {
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 ** 2) return `${(bytes / 1024).toFixed(1)} KB`;
    if (bytes < 1024 ** 3) return `${(bytes / 1024 ** 2).toFixed(1)} MB`;
    return `${(bytes / 1024 ** 3).toFixed(2)} GB`;
}

function renderFileList(files) {
    fileList.innerHTML = "";
    for (const file of files) {
        const li = document.createElement("li");
        li.className = "flex items-center justify-between bg-slate-50 rounded-md px-3 py-2";
        const name = document.createElement("span");
        name.className = "truncate pr-3";
        name.textContent = file.name;
        const size = document.createElement("span");
        size.className = "text-slate-400 text-xs flex-shrink-0";
        size.textContent = formatBytes(file.size);
        li.append(name, size);
        fileList.appendChild(li);
    }
}

for (const evt of ["dragenter", "dragover"]) {
    dropZone.addEventListener(evt, (e) => {
        e.preventDefault();
        dropZone.classList.add("border-indigo-500", "bg-indigo-50/50");
    });
}
for (const evt of ["dragleave", "drop"]) {
    dropZone.addEventListener(evt, (e) => {
        e.preventDefault();
        dropZone.classList.remove("border-indigo-500", "bg-indigo-50/50");
    });
}
dropZone.addEventListener("drop", (e) => {
    fileInput.files = e.dataTransfer.files;
    renderFileList(fileInput.files);
});
fileInput.addEventListener("change", () => renderFileList(fileInput.files));


// Submit + polling --------------------------------------------------------

form.addEventListener("submit", async (e) => {
    e.preventDefault();
    if (!fileInput.files.length) {
        alert("Please choose at least one video file.");
        return;
    }

    const data = new FormData();
    for (const [field, input] of Object.entries(keyFields)) {
        data.append(field, input.value);
    }
    for (const file of fileInput.files) {
        data.append("files", file);
    }

    setSubmitState({ disabled: true, label: "Generating subtitles…" });

    try {
        const res = await fetch("/api/jobs", { method: "POST", body: data });
        if (!res.ok) throw new Error(`Upload failed (${res.status})`);
        const body = await res.json();
        activeJobId = body.job_id;
        showStatus();
        startPolling();
    } catch (err) {
        alert(err.message || "Upload failed");
        setSubmitState({ disabled: false, label: "Generate subtitles" });
    }
});

function setSubmitState({ disabled, label }) {
    submitBtn.disabled = disabled;
    submitBtn.textContent = label;
}

function showStatus() {
    statusSection.classList.remove("hidden");
    resultsSection.classList.add("hidden");
    eventLog.innerHTML = "";
    renderedEventCount = 0;
}

function appendEvents(events) {
    // Only render events we haven't already rendered. This keeps polling
    // updates flicker-free.
    for (let i = renderedEventCount; i < events.length; i++) {
        const li = document.createElement("li");
        li.className = "flex items-start gap-2";
        const bullet = document.createElement("span");
        bullet.className = "text-slate-400 select-none";
        bullet.textContent = "•";
        const text = document.createElement("span");
        text.textContent = events[i];
        li.append(bullet, text);
        eventLog.appendChild(li);
    }
    renderedEventCount = events.length;
}

function applyJobState(state) {
    const style = STATUS_STYLES[state.status] || STATUS_STYLES.queued;
    statusText.textContent = style.text;
    statusDot.className = `w-3 h-3 rounded-full ${style.dot}`;
    appendEvents(state.events || []);

    if (state.status === "completed" || state.status === "failed") {
        stopPolling();
        showResults(state);
    }
}

function startPolling() {
    pollHandle = setInterval(pollOnce, POLL_INTERVAL_MS);
    pollOnce();
}

function stopPolling() {
    if (pollHandle) {
        clearInterval(pollHandle);
        pollHandle = null;
    }
}

async function pollOnce() {
    if (!activeJobId) return;
    try {
        const res = await fetch(`/api/jobs/${activeJobId}`);
        if (!res.ok) throw new Error(`Polling failed (${res.status})`);
        applyJobState(await res.json());
    } catch (err) {
        console.error(err);
    }
}


// Results -----------------------------------------------------------------

function showResults(state) {
    resultsSection.classList.remove("hidden");
    resultsList.innerHTML = "";

    if (!state.outputs || !state.outputs.length) {
        const empty = document.createElement("li");
        empty.className = "text-slate-500 text-sm";
        empty.textContent =
            "No subtitle files were produced. Check the event log above for failure details.";
        resultsList.appendChild(empty);
        return;
    }

    for (const name of state.outputs) {
        const li = document.createElement("li");
        li.className = "flex items-center justify-between bg-slate-50 rounded-md px-3 py-2";

        const label = document.createElement("span");
        label.className = "truncate pr-3";
        label.textContent = name;

        const link = document.createElement("a");
        link.href = `/api/jobs/${activeJobId}/files/${encodeURIComponent(name)}`;
        link.download = name;
        link.className =
            "text-indigo-600 hover:text-indigo-700 text-sm font-medium flex-shrink-0";
        link.textContent = "Download";

        li.append(label, link);
        resultsList.appendChild(li);
    }
}


// Cleanup -----------------------------------------------------------------

function requestCleanup(jobId) {
    // sendBeacon is the browser API designed to complete during page unload.
    // It only supports POST, hence the /delete suffix on the endpoint.
    if (navigator.sendBeacon) {
        navigator.sendBeacon(`/api/jobs/${jobId}/delete`);
    } else {
        // Fallback for environments without sendBeacon support.
        fetch(`/api/jobs/${jobId}/delete`, { method: "POST", keepalive: true })
            .catch(() => {});
    }
}

window.addEventListener("beforeunload", () => {
    if (activeJobId) {
        requestCleanup(activeJobId);
    }
});

resetBtn.addEventListener("click", () => {
    if (activeJobId) {
        // Use a regular fetch here; we're not unloading.
        fetch(`/api/jobs/${activeJobId}/delete`, { method: "POST" }).catch(() => {});
    }
    stopPolling();
    activeJobId = null;
    renderedEventCount = 0;
    statusSection.classList.add("hidden");
    resultsSection.classList.add("hidden");
    setSubmitState({ disabled: false, label: "Generate subtitles" });
    fileInput.value = "";
    renderFileList([]);
});
