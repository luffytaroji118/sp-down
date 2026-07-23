const $ = (id) => document.getElementById(id);

const urlInput = $('playlist-url');
const loadBtn = $('load-btn');
const inputError = $('input-error');
const playlistInfo = $('playlist-info');
const playlistName = $('playlist-name');
const trackCount = $('track-count');
const trackList = $('track-list');
const formatSelect = $('format-select');
const downloadBtn = $('download-btn');
const progressSection = $('progress-section');
const progressBar = $('progress-bar');
const progressText = $('progress-text');
const currentTrack = $('current-track');
const downloadReady = $('download-ready');
const downloadLink = $('download-link');
const summaryText = $('summary-text');
const spinner = $('loading-spinner');
const stopBtn = $('stop-btn');
const stoppedSection = $('stopped-section');
const stoppedSummaryText = $('stopped-summary-text');
const backBtn = $('back-btn');

let loadedTracks = [];
let currentJobId = null;

async function api(path, body) {
    const resp = await fetch(path, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: body ? JSON.stringify(body) : undefined,
    });
    if (!resp.ok) {
        const err = await resp.json().catch(() => ({ detail: 'Request failed' }));
        throw new Error(err.detail || 'Request failed');
    }
    return resp.json();
}

function showError(msg) {
    inputError.textContent = msg;
}

function clearError() {
    inputError.textContent = '';
}

function formatDuration(ms) {
    const s = Math.floor(ms / 1000);
    return `${Math.floor(s / 60)}:${(s % 60).toString().padStart(2, '0')}`;
}

function statusIcon(status) {
    if (status === 'done') return '<span class="status-icon status-done">&#10003;</span>';
    if (status === 'downloading') return '<span class="status-icon status-downloading">&#8635;</span>';
    if (status === 'failed') return '<span class="status-icon status-failed">&#10007;</span>';
    return '<span class="status-icon status-pending">&#8226;</span>';
}

loadBtn.addEventListener('click', async () => {
    const url = urlInput.value.trim();
    if (!url) {
        showError('Please paste a Spotify playlist URL');
        return;
    }
    clearError();
    loadBtn.disabled = true;
    loadBtn.textContent = 'Loading...';
    spinner.classList.remove('hidden');
    playlistInfo.classList.add('hidden');

    try {
        const limitVal = document.getElementById('limit-input').value;
        const data = await api('/api/playlist', {
            url,
            limit: limitVal ? parseInt(limitVal) : null,
        });
        loadedTracks = data.tracks;
        playlistName.textContent = data.name;
        trackCount.textContent = `${data.total} tracks`;

        trackList.innerHTML = data.tracks.map(t => `
            <div class="track-row" id="track-${t.index - 1}">
                <span class="num">${t.index}</span>
                <div class="info">
                    <div class="title">${escapeHtml(t.title)}</div>
                    <div class="artists">${escapeHtml(t.artists)}</div>
                </div>
                <span class="duration">${formatDuration(t.duration_ms)}</span>
                ${statusIcon(null)}
            </div>
        `).join('');

        playlistInfo.classList.remove('hidden');
    } catch (e) {
        showError(e.message);
    } finally {
        loadBtn.disabled = false;
        loadBtn.textContent = 'Load';
        spinner.classList.add('hidden');
    }
});

downloadBtn.addEventListener('click', async () => {
    if (loadedTracks.length === 0) return;
    clearError();
    downloadBtn.disabled = true;
    downloadBtn.textContent = 'Starting...';

    try {
        const limitVal = document.getElementById('limit-input').value;
        const data = await api('/api/download', {
            url: urlInput.value.trim(),
            format: formatSelect.value,
            limit: limitVal ? parseInt(limitVal) : null,
        });
        currentJobId = data.job_id;
        downloadBtn.textContent = 'Download All';
        downloadBtn.disabled = false;
        playlistInfo.classList.add('hidden');
        progressSection.classList.remove('hidden');
        stopBtn.disabled = false;
        pollStatus(data.job_id);
    } catch (e) {
        showError(e.message);
        downloadBtn.disabled = false;
        downloadBtn.textContent = 'Download All';
    }
});

stopBtn.addEventListener('click', async () => {
    if (!currentJobId) return;
    stopBtn.disabled = true;
    stopBtn.textContent = 'Stopping...';
    try {
        await api(`/api/stop/${currentJobId}`);
    } catch (e) {
        stopBtn.disabled = false;
        stopBtn.textContent = 'Stop';
    }
});

let pollTimer = null;

function pollStatus(jobId) {
    if (pollTimer) clearInterval(pollTimer);
    pollTimer = setInterval(async () => {
        try {
            const resp = await fetch(`/api/status/${jobId}`);
            const data = await resp.json();
            updateProgress(data);
            if (data.status === 'done') {
                clearInterval(pollTimer);
                showDownloadReady(jobId, data);
            } else if (data.status === 'error') {
                clearInterval(pollTimer);
                showError(data.error || 'Download failed');
                resetDownloadBtn();
                progressSection.classList.add('hidden');
            } else if (data.status === 'stopped') {
                clearInterval(pollTimer);
                showStopped(data);
            }
        } catch (e) {
            clearInterval(pollTimer);
            showError('Lost connection to server');
            resetDownloadBtn();
        }
    }, 1000);
}

function updateProgress(data) {
    const done = data.completed + data.failed;
    const pct = data.total > 0 ? (done / data.total) * 100 : 0;
    progressBar.style.width = `${pct}%`;
    progressText.textContent = `${done} / ${data.total}`;

    if (data.current_downloading && data.current_downloading.length > 0) {
        currentTrack.textContent = `Downloading: ${data.current_downloading.join(', ')}`;
    } else if (data.current_title) {
        currentTrack.textContent = `Now downloading: ${data.current_title}`;
    }

    data.track_status.forEach((status, i) => {
        const row = $(`track-${i}`);
        if (row && status) {
            const iconEl = row.querySelector('.status-icon');
            if (iconEl) {
                iconEl.className = `status-icon status-${status}`;
                iconEl.innerHTML = status === 'done' ? '&#10003;'
                    : status === 'downloading' ? '&#8635;'
                    : status === 'failed' ? '&#10007;'
                    : '&#8226;';
            }
        }
    });
}

function showDownloadReady(jobId, data) {
    progressSection.classList.add('hidden');
    downloadReady.classList.remove('hidden');
    downloadLink.href = `/api/file/${jobId}`;
    summaryText.textContent = `${data.completed} songs downloaded${data.failed > 0 ? `, ${data.failed} failed` : ''}`;
}

function showStopped(data) {
    progressSection.classList.add('hidden');
    stoppedSection.classList.remove('hidden');
    stoppedSummaryText.textContent = `${data.completed} songs downloaded, ${data.failed} failed before stopping.`;
    stopBtn.textContent = 'Stop';
    stopBtn.disabled = false;
}

backBtn.addEventListener('click', () => {
    stoppedSection.classList.add('hidden');
    playlistInfo.classList.remove('hidden');
});

function resetDownloadBtn() {
    downloadBtn.disabled = false;
    downloadBtn.textContent = 'Download All';
}

function escapeHtml(str) {
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
}

urlInput.addEventListener('keypress', (e) => {
    if (e.key === 'Enter') loadBtn.click();
});

urlInput.addEventListener('input', clearError);
