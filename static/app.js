const $ = (id) => document.getElementById(id);

const urlInput = $('playlist-url');
const loadBtn = $('load-btn');
const inputError = $('input-error');
const playlistInfo = $('playlist-info');
const playlistName = $('playlist-name');
const playlistNameLabel = $('playlist-name-label');
const playlistCover = $('playlist-cover');
const trackCount = $('track-count');
const trackList = $('track-list');
const formatSelect = $('format-select');
const downloadBtn = $('download-btn');
const progressSection = $('progress-section');
const progressBar = $('progress-bar');
const progressText = $('progress-text');
const currentTrack = $('current-track');
const currentTrackText = $('current-track-text');
const progressDetail = $('progress-detail');
const elapsedTime = $('elapsed-time');
const downloadSpeed = $('download-speed');
const downloadReady = $('download-ready');
const downloadLink = $('download-link');
const summaryText = $('summary-text');
const modeSelect = $('mode-select');
const individualReady = $('individual-ready');
const individualFileList = $('individual-file-list');
const individualSummaryText = $('individual-summary-text');
const downloadAllIndividual = $('download-all-individual');
const spinner = $('loading-spinner');
const stopBtn = $('stop-btn');
const stoppedSection = $('stopped-section');
const stoppedSummaryText = $('stopped-summary-text');
const backBtn = $('back-btn');
const searchInfo = $('search-info');
const searchQueryText = $('search-query-text');
const searchCount = $('search-count');
const searchList = $('search-list');
const searchFormatSelect = $('search-format-select');
const searchQueryLabel = $('search-query-label');
const searchToggle = $('search-toggle');
const searchBody = $('search-body');
const individualBackBtn = $('individual-back-btn');
const cartCard = $('cart-card');
const cartList = $('cart-list');
const cartCount = $('cart-count');
const cartSubtitle = $('cart-subtitle');
const cartFormatSelect = $('cart-format-select');
const cartModeSelect = $('cart-mode-select');
const cartDownloadBtn = $('cart-download-btn');
const cartClearBtn = $('cart-clear-btn');
const cartToggle = $('cart-toggle');
const cartBody = $('cart-body');
const playlistToggle = $('playlist-toggle');
const playlistBody = $('playlist-body');

let loadedTracks = [];
let currentJobId = null;
let currentSearchResults = [];
let lastInputMode = 'playlist';
let pollTimer = null;
let elapsedTimer = null;
let downloadStartTime = null;
let cart = loadCart();
let searchSeq = 0;
let liveSearchTimer = null;

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

function isSpotifyUrl(s) {
    return /spotify\.com|spotify:/.test(s);
}

function setBtnLabel(btn, label) {
    if (btn.firstElementChild) btn.firstElementChild.textContent = label;
    else btn.textContent = label;
}

function hideResultCards() {
    playlistInfo.classList.add('hidden');
    searchInfo.classList.add('hidden');
}

loadBtn.addEventListener('click', async () => {
    const url = urlInput.value.trim();
    if (!url) {
        showError('Paste a Spotify link or type a song name');
        return;
    }
    clearError();
    if (isSpotifyUrl(url)) {
        loadPlaylist(url);
    } else {
        loadSearchResults(url);
    }
});

async function loadPlaylist(url) {
    lastInputMode = 'playlist';
    loadBtn.disabled = true;
    setBtnLabel(loadBtn, 'Loading...');
    spinner.classList.remove('hidden');
    hideResultCards();

    try {
        const data = await api('/api/playlist', {
            url,
            limit: null,
        });
        loadedTracks = data.tracks;
        playlistName.textContent = data.name;
        playlistNameLabel.textContent = 'Select a format and delivery method below';
        trackCount.textContent = `${data.total} tracks`;

        if (data.cover_url) {
            playlistCover.src = data.cover_url;
            playlistCover.classList.remove('hidden');
        } else {
            playlistCover.classList.add('hidden');
        }

        trackList.innerHTML = data.tracks.map(t => `
            <div class="track-row playlist-row" id="track-${t.index - 1}" data-index="${t.index - 1}">
                <span class="num">${t.index}</span>
                ${t.cover_url ? `<img class="track-thumb" src="${t.cover_url}" alt="" loading="lazy">` : '<span class="track-thumb-placeholder"></span>'}
                <div class="info">
                    <div class="title">${escapeHtml(t.title)}</div>
                    <div class="artists">${escapeHtml(t.artists)}</div>
                </div>
                <span class="duration">${formatDuration(t.duration_ms)}</span>
                <button class="btn btn-ghost btn-sm playlist-cart-btn" data-index="${t.index - 1}" title="Add to cart"><span class="btn-text">Add to cart</span><span class="btn-symbol">+</span></button>
                <button class="btn btn-green btn-sm playlist-download-btn" data-index="${t.index - 1}" title="Download"><span class="btn-text">Download</span><span class="btn-symbol">&darr;</span></button>
                <span class="status-wrap">${statusIcon(null)}</span>
            </div>
        `).join('');

        playlistInfo.classList.remove('hidden');
        expand(playlistToggle, playlistBody);
    } catch (e) {
        showError(e.message);
    } finally {
        loadBtn.disabled = false;
        setBtnLabel(loadBtn, 'Search');
        spinner.classList.add('hidden');
    }
}

async function loadSearchResults(query, live = false) {
    const mySeq = ++searchSeq;
    lastInputMode = 'search';
    if (!live) { loadBtn.disabled = true; setBtnLabel(loadBtn, 'Searching...'); }
    else setBtnLabel(loadBtn, 'Search');
    spinner.classList.remove('hidden');
    hideResultCards();

    try {
        const data = await api('/api/search', { query });
        if (mySeq !== searchSeq) return;
        currentSearchResults = data.results;
        searchQueryText.textContent = `"${query}"`;
        searchCount.textContent = `${data.total} results`;

        if (!data.results.length) {
            searchList.innerHTML = '<div class="search-empty">No results found. Try another name.</div>';
        } else {
            searchList.innerHTML = data.results.map((r, i) => `
                <div class="track-row search-row" data-index="${i}">
                    <span class="num">${i + 1}</span>
                    <div class="info">
                        <div class="title">${escapeHtml(r.title)}</div>
                        <div class="artists">${escapeHtml(r.artists)}</div>
                    </div>
                    <span class="duration">${formatDuration(r.duration_ms)}</span>
                    <button class="btn btn-ghost btn-sm search-cart-btn" data-index="${i}" title="Add to cart"><span class="btn-text">Add to cart</span><span class="btn-symbol">+</span></button>
                    <button class="btn btn-green btn-sm search-download-btn" data-index="${i}" title="Download"><span class="btn-text">Download</span><span class="btn-symbol">&darr;</span></button>
                </div>
            `).join('');
        }
        searchInfo.classList.remove('hidden');
        expand(searchToggle, searchBody);
    } catch (e) {
        if (mySeq !== searchSeq) return;
        showError(e.message);
    } finally {
        if (mySeq === searchSeq) {
            loadBtn.disabled = false;
            setBtnLabel(loadBtn, 'Search');
            spinner.classList.add('hidden');
        }
    }
}

searchList.addEventListener('click', async (e) => {
    const cartBtn = e.target.closest('.search-cart-btn');
    if (cartBtn) {
        const idx = parseInt(cartBtn.dataset.index, 10);
        const result = currentSearchResults[idx];
        if (result) {
            addToCart(result);
            const orig = cartBtn.firstElementChild.textContent;
            setBtnLabel(cartBtn, 'Added');
            cartBtn.disabled = true;
            setTimeout(() => { setBtnLabel(cartBtn, orig); cartBtn.disabled = false; }, 900);
        }
        return;
    }
    const btn = e.target.closest('.search-download-btn');
    if (!btn) return;
    const idx = parseInt(btn.dataset.index, 10);
    const result = currentSearchResults[idx];
    if (!result) return;
    clearError();
    btn.disabled = true;
    setBtnLabel(btn, 'Starting...');

    try {
        const data = await api('/api/download_track', {
            video_url: result.video_url,
            title: result.title,
            artists: result.artists,
            format: searchFormatSelect.value,
        });
        currentJobId = data.job_id;
        setBtnLabel(btn, 'Download');
        btn.disabled = false;
        searchInfo.classList.add('hidden');
        progressSection.classList.remove('hidden');
        stopBtn.disabled = false;
        scrollToEl(progressSection);
        pollStatus(data.job_id);
    } catch (err) {
        showError(err.message);
        setBtnLabel(btn, 'Download');
        btn.disabled = false;
    }
});

trackList.addEventListener('click', async (e) => {
    const cartBtn = e.target.closest('.playlist-cart-btn');
    if (cartBtn) {
        const idx = parseInt(cartBtn.dataset.index, 10);
        const t = loadedTracks[idx];
        if (!t) return;
        addToCart({ title: t.title, artists: t.artists, video_url: '', duration_ms: t.duration_ms });
        const orig = cartBtn.querySelector('.btn-text') ? cartBtn.firstElementChild.textContent : '';
        setBtnLabel(cartBtn, 'Added');
        cartBtn.disabled = true;
        setTimeout(() => { if (orig) setBtnLabel(cartBtn, orig); cartBtn.disabled = false; }, 900);
        return;
    }
    const dlBtn = e.target.closest('.playlist-download-btn');
    if (!dlBtn) return;
    const idx = parseInt(dlBtn.dataset.index, 10);
    const t = loadedTracks[idx];
    if (!t) return;
    clearError();
    dlBtn.disabled = true;
    setBtnLabel(dlBtn, 'Starting...');

    try {
        const data = await api('/api/download_track_by_name', {
            title: t.title,
            artists: t.artists,
            format: formatSelect.value,
        });
        currentJobId = data.job_id;
        setBtnLabel(dlBtn, 'Download');
        dlBtn.disabled = false;
        playlistInfo.classList.add('hidden');
        progressSection.classList.remove('hidden');
        stopBtn.disabled = false;
        scrollToEl(progressSection);
        pollStatus(data.job_id);
    } catch (err) {
        showError(err.message);
        setBtnLabel(dlBtn, 'Download');
        dlBtn.disabled = false;
    }
});

downloadBtn.addEventListener('click', async () => {
    if (loadedTracks.length === 0) return;
    clearError();
    downloadBtn.disabled = true;
    setBtnLabel(downloadBtn, 'Preparing...');

    try {
        const data = await api('/api/download', {
            url: urlInput.value.trim(),
            format: formatSelect.value,
            mode: modeSelect.value,
            limit: null,
        });
        currentJobId = data.job_id;
        setBtnLabel(downloadBtn, 'Start download');
        downloadBtn.disabled = false;
        playlistInfo.classList.add('hidden');
        progressSection.classList.remove('hidden');
        stopBtn.disabled = false;
        scrollToEl(progressSection);
        pollStatus(data.job_id);
    } catch (e) {
        showError(e.message);
        downloadBtn.disabled = false;
        setBtnLabel(downloadBtn, 'Start download');
    }
});

stopBtn.addEventListener('click', async () => {
    if (!currentJobId) return;
    stopBtn.disabled = true;
    setBtnLabel(stopBtn, 'Stopping...');
    try {
        await api(`/api/stop/${currentJobId}`);
    } catch (e) {
        stopBtn.disabled = false;
        setBtnLabel(stopBtn, 'Stop');
    }
});

function startElapsedTimer() {
    downloadStartTime = Date.now();
    elapsedTime.textContent = '0:00';
    if (elapsedTimer) clearInterval(elapsedTimer);
    elapsedTimer = setInterval(() => {
        if (!downloadStartTime) return;
        const secs = Math.floor((Date.now() - downloadStartTime) / 1000);
        const m = Math.floor(secs / 60);
        const s = secs % 60;
        elapsedTime.textContent = `${m}:${s.toString().padStart(2, '0')}`;
    }, 1000);
}

function stopElapsedTimer() {
    if (elapsedTimer) { clearInterval(elapsedTimer); elapsedTimer = null; }
    downloadStartTime = null;
}

function pollStatus(jobId) {
    if (pollTimer) clearInterval(pollTimer);
    startElapsedTimer();
    pollTimer = setInterval(async () => {
        try {
            const resp = await fetch(`/api/status/${jobId}`);
            const data = await resp.json();
            updateProgress(data);
            if (data.status === 'done') {
                clearInterval(pollTimer);
                stopElapsedTimer();
                if (data.mode === 'individual') {
                    showIndividualReady(jobId, data);
                } else {
                    showDownloadReady(jobId, data);
                }
            } else if (data.status === 'error') {
                clearInterval(pollTimer);
                stopElapsedTimer();
                showError(data.error || 'Download failed');
                resetDownloadBtn();
                progressSection.classList.add('hidden');
                if (lastInputMode === 'cart') {
                    cartCard.classList.remove('hidden');
                    scrollToEl(cartCard);
                }
            } else if (data.status === 'stopped') {
                clearInterval(pollTimer);
                stopElapsedTimer();
                showStopped(data);
            }
        } catch (e) {
            clearInterval(pollTimer);
            stopElapsedTimer();
            showError('Lost connection to server');
            resetDownloadBtn();
        }
    }, 1000);
}

function formatSpeed(bytesPerSec) {
    if (!bytesPerSec || bytesPerSec <= 0) return '';
    if (bytesPerSec >= 1048576) return `${(bytesPerSec / 1048576).toFixed(1)} MB/s`;
    if (bytesPerSec >= 1024) return `${(bytesPerSec / 1024).toFixed(0)} KB/s`;
    return `${bytesPerSec.toFixed(0)} B/s`;
}

function updateProgress(data) {
    const done = data.completed + data.failed;
    const trackProg = data.track_progress || {};

    let completedUnits = done;
    (data.track_status || []).forEach((status, i) => {
        if (status === 'downloading') {
            const p = trackProg[i + 1] || 0;
            completedUnits += p / 100;
        }
    });
    const pct = data.total > 0 ? (completedUnits / data.total) * 100 : 0;
    progressBar.style.width = `${Math.min(pct, 100)}%`;
    progressText.textContent = `${done} / ${data.total}`;

    const dlTracks = data.current_downloading || [];
    if (dlTracks.length > 0 || data.current_title) {
        const label = dlTracks.length > 0 ? `Downloading: ${dlTracks.join(', ')}` : `Now downloading: ${data.current_title}`;
        const firstDlIdx = (data.track_status || []).findIndex(s => s === 'downloading');
        const tp = firstDlIdx >= 0 ? (trackProg[firstDlIdx + 1] || 0) : 0;
        currentTrackText.textContent = label;
        progressDetail.textContent = tp > 0 ? `${Math.round(tp)}%` : '';
    } else {
        currentTrackText.textContent = 'Preparing…';
        progressDetail.textContent = '';
    }

    downloadSpeed.textContent = formatSpeed(data.download_speed || 0);

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

function showIndividualReady(jobId, data) {
    progressSection.classList.add('hidden');
    individualReady.classList.remove('hidden');
    individualSummaryText.textContent = `${data.completed} songs downloaded${data.failed > 0 ? `, ${data.failed} failed` : ''}`;

    const files = (data.files || []).slice().sort((a, b) => a.index - b.index);
    individualFileList.innerHTML = files.map(f => `
        <a class="individual-file-row" href="/api/track_file/${jobId}/${f.index}" download>
            <span class="status-icon status-done">&#10003;</span>
            <span class="individual-file-name">${escapeHtml(f.name)}</span>
            <span class="individual-download-icon">&darr;</span>
        </a>
    `).join('');

    downloadAllIndividual.dataset.jobId = jobId;
    downloadAllIndividual.dataset.fileCount = String(files.length);
    downloadAllIndividual.disabled = false;
    setBtnLabel(downloadAllIndividual, 'Download all tracks');
}

async function downloadAllIndividualSequentially(jobId) {
    const rows = individualFileList.querySelectorAll('a.individual-file-row');
    if (!rows.length) return;
    downloadAllIndividual.disabled = true;
    let done = 0;
    for (const row of rows) {
        const href = row.getAttribute('href');
        try {
            const resp = await fetch(href);
            if (!resp.ok) throw new Error('failed');
            const blob = await resp.blob();
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = row.querySelector('.individual-file-name').textContent;
            document.body.appendChild(a);
            a.click();
            a.remove();
            URL.revokeObjectURL(url);
        } catch (e) {
            row.classList.add('failed-row');
        }
        done++;
        setBtnLabel(downloadAllIndividual, `Downloading ${done}/${rows.length}`);
        await new Promise(r => setTimeout(r, 600));
    }
    setBtnLabel(downloadAllIndividual, 'Download all tracks');
    downloadAllIndividual.disabled = false;
}

downloadAllIndividual.addEventListener('click', () => {
    const jobId = downloadAllIndividual.dataset.jobId;
    if (jobId) downloadAllIndividualSequentially(jobId);
});

function showStopped(data) {
    progressSection.classList.add('hidden');
    stoppedSection.classList.remove('hidden');
    stoppedSummaryText.textContent = `${data.completed} songs downloaded, ${data.failed} failed before stopping.`;
    setBtnLabel(stopBtn, 'Stop');
    stopBtn.disabled = false;
}

function backToPrevious() {
    individualReady.classList.add('hidden');
    stoppedSection.classList.add('hidden');
    if (lastInputMode === 'search') {
        searchInfo.classList.remove('hidden');
    } else if (lastInputMode === 'cart') {
        cartCard.classList.remove('hidden');
        collapseCart();
        scrollToEl(cartCard);
    } else {
        playlistInfo.classList.remove('hidden');
    }
}

backBtn.addEventListener('click', backToPrevious);
individualBackBtn.addEventListener('click', backToPrevious);

function resetDownloadBtn() {
    downloadBtn.disabled = false;
    setBtnLabel(downloadBtn, 'Start download');
}

function escapeHtml(str) {
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
}

urlInput.addEventListener('keypress', (e) => {
    if (e.key === 'Enter') loadBtn.click();
});

urlInput.addEventListener('input', () => {
    clearError();
    if (liveSearchTimer) { clearTimeout(liveSearchTimer); liveSearchTimer = null; }
    const v = urlInput.value.trim();
    if (!v) {
        searchSeq++;
        hideResultCards();
        spinner.classList.add('hidden');
        return;
    }
    if (isSpotifyUrl(v)) {
        searchSeq++;
        hideResultCards();
        spinner.classList.add('hidden');
        return;
    }
    if (v.length < 2) return;
    liveSearchTimer = setTimeout(() => {
        liveSearchTimer = null;
        loadSearchResults(v, true);
    }, 350);
});

function scrollToEl(el) {
    el.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

// ---- Cart ----
const CART_KEY = 'sounddrop_cart';

function loadCart() {
    try {
        const raw = localStorage.getItem(CART_KEY);
        const parsed = raw ? JSON.parse(raw) : [];
        return Array.isArray(parsed) ? parsed : [];
    } catch { return []; }
}

function saveCart() {
    try { localStorage.setItem(CART_KEY, JSON.stringify(cart)); } catch {}
}

function cartKey(r) {
    return (r.video_url || '').split('v=').pop() || r.video_url;
}

function expandCart() {
    cartBody.classList.remove('hidden');
    cartToggle.setAttribute('aria-expanded', 'true');
}

function collapseCart() {
    cartBody.classList.add('hidden');
    cartToggle.setAttribute('aria-expanded', 'false');
}

function toggleCart() {
    if (cartBody.classList.contains('hidden')) expandCart();
    else collapseCart();
}

cartToggle.addEventListener('click', toggleCart);

function expand(el, body) { body.classList.remove('hidden'); el.setAttribute('aria-expanded', 'true'); }
function collapse(el, body) { body.classList.add('hidden'); el.setAttribute('aria-expanded', 'false'); }

searchToggle.addEventListener('click', () => {
    if (searchBody.classList.contains('hidden')) expand(searchToggle, searchBody);
    else collapse(searchToggle, searchBody);
});
playlistToggle.addEventListener('click', () => {
    if (playlistBody.classList.contains('hidden')) expand(playlistToggle, playlistBody);
    else collapse(playlistToggle, playlistBody);
});

function addToCart(result) {
    if (cart.some(c => cartKey(c) === cartKey(result))) {
        inputError.textContent = 'Already in cart';
        setTimeout(clearError, 1200);
        return;
    }
    cart.push({
        title: result.title,
        artists: result.artists,
        video_url: result.video_url,
        duration_ms: result.duration_ms || 0,
    });
    saveCart();
    renderCart();
    expandCart();
    scrollToEl(cartCard);
}

function removeFromCart(pos) {
    cart.splice(pos, 1);
    saveCart();
    renderCart();
}

function clearCart() {
    cart = [];
    saveCart();
    renderCart();
    collapseCart();
}

function renderCart() {
    cartCount.textContent = String(cart.length);
    cartDownloadBtn.disabled = cart.length === 0;
    cartClearBtn.disabled = cart.length === 0;
    cartSubtitle.textContent = cart.length === 0
        ? 'Search above and add tracks here to download together'
        : `${cart.length} ${cart.length === 1 ? 'track' : 'tracks'} ready to download`;

    if (cart.length === 0) {
        cartList.innerHTML = '<div class="search-empty">Your cart is empty. Search above and tap “Add to cart”.</div>';
        return;
    }
    cartList.innerHTML = cart.map((c, i) => `
        <div class="track-row cart-row">
            <span class="num">${i + 1}</span>
            <div class="info">
                <div class="title">${escapeHtml(c.title)}</div>
                <div class="artists">${escapeHtml(c.artists)}</div>
            </div>
            <span class="duration">${formatDuration(c.duration_ms)}</span>
            <button class="btn btn-ghost btn-sm cart-remove-btn" data-index="${i}" title="Remove"><span class="btn-text">Remove</span><span class="btn-symbol">&times;</span></button>
        </div>
    `).join('');
}

cartList.addEventListener('click', (e) => {
    const btn = e.target.closest('.cart-remove-btn');
    if (!btn) return;
    removeFromCart(parseInt(btn.dataset.index, 10));
});

cartClearBtn.addEventListener('click', () => {
    if (cart.length && !confirm(`Remove all ${cart.length} tracks from the cart?`)) return;
    clearCart();
});

cartDownloadBtn.addEventListener('click', async () => {
    if (cart.length === 0) return;
    clearError();
    cartDownloadBtn.disabled = true;
    setBtnLabel(cartDownloadBtn, 'Preparing...');

    try {
        const data = await api('/api/download_cart', {
            tracks: cart,
            format: cartFormatSelect.value,
            mode: cartModeSelect.value,
            name: 'Cart',
        });
        currentJobId = data.job_id;
        lastInputMode = 'cart';
        setBtnLabel(cartDownloadBtn, 'Download cart');
        cartDownloadBtn.disabled = false;
        cartCard.classList.add('hidden');
        progressSection.classList.remove('hidden');
        stopBtn.disabled = false;
        scrollToEl(progressSection);
        pollStatus(data.job_id);
    } catch (e) {
        showError(e.message);
        setBtnLabel(cartDownloadBtn, 'Download cart');
        cartDownloadBtn.disabled = false;
    }
});

renderCart();
if (cart.length) expandCart(); else collapseCart();
