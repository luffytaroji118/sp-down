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

let loadedTracks = [];

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
        const data = await api('/api/playlist', { url });
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
        const data = await api('/api/download', {
            url: urlInput.value.trim(),
            format: formatSelect.value,
        });
        downloadBtn.textContent = 'Downloading...';
        playlistInfo.classList.add('hidden');
        progressSection.classList.remove('hidden');
        pollStatus(data.job_id);
    } catch (e) {
        showError(e.message);
        downloadBtn.disabled = false;
        downloadBtn.textContent = 'Download All';
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
            }
        } catch (e) {
            clearInterval(pollTimer);
            showError('Lost connection to server');
            resetDownloadBtn();
        }
    }, 1000);
}

function updateProgress(data) {
    const pct = data.total > 0 ? (data.completed / data.total) * 100 : 0;
    progressBar.style.width = `${pct}%`;
    progressText.textContent = `${data.completed + data.failed} / ${data.total}`;

    if (data.current_title) {
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

    if (data.current_index > 0) {
        const row = $(`track-${data.current_index - 1}`);
        if (row) {
            const iconEl = row.querySelector('.status-icon');
            if (iconEl && data.track_status[data.current_index - 1] === null) {
                iconEl.className = 'status-icon status-downloading';
                iconEl.innerHTML = '&#8635;';
            }
        }
    }
}

function showDownloadReady(jobId, data) {
    progressSection.classList.add('hidden');
    downloadReady.classList.remove('hidden');
    downloadLink.href = `/api/file/${jobId}`;
    summaryText.textContent = `${data.completed} songs downloaded${data.failed > 0 ? `, ${data.failed} failed` : ''}`;
}

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
