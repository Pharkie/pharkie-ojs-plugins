/**
 * QA Splits — review interface
 *
 * Vanilla JS app for the 3-pane QA review screen.
 * Uses PDF.js for PDF rendering, fetches data from the plugin API.
 *
 * Keyboard shortcuts (when not in text input):
 *   Left/Right arrow  — previous/next article
 *   A                 — approve
 *   R                 — reject (opens comment box)
 *   ?                 — show shortcut help
 */

(function () {
    'use strict';

    const API = window.QA_CONFIG.apiBase;
    const PLUGIN_URL = window.QA_CONFIG.pluginUrl;

    // State
    let articles = [];          // Full unfiltered list
    let workingSet = [];        // Current filtered subset (indices into articles[])
    let setIndex = -1;          // Position within workingSet
    let setFilter = null;       // { type: 'author'|'issue'|'status'|'search', query: string } or null
    let currentIndex = -1;      // Index into articles[] (derived from workingSet[setIndex])
    let lastSeenId = parseInt(localStorage.getItem('qa-last-seen'), 10) || null;
    let pdfDoc = null;
    let loadGeneration = 0;
    let scrollHandler = null;
    let drawerOpen = false;

    // Prefetch cache
    const PREFETCH_AHEAD = 5;
    const prefetchCache = new Map();

    // DOM refs
    const els = {};
    const elIds = [
        'qa-title', 'qa-status', 'qa-authors', 'qa-section', 'qa-issue',
        'qa-pages', 'qa-progress', 'pdf-page-info', 'pdf-container',
        'html-content', 'endmatter-items',
        'btn-approve', 'btn-reject', 'reject-comment', 'btn-submit-reject',
        'btn-last-seen', 'btn-prev', 'btn-next', 'btn-random', 'btn-problem',
    ];

    function init() {
        elIds.forEach(id => els[id] = document.getElementById(id));

        // No labels — metadata is self-evident from context

        if (window.pdfjsLib) {
            pdfjsLib.GlobalWorkerOptions.workerSrc = PLUGIN_URL + '/js/pdf.worker.min.js';
        }

        bindEvents();
        bindDrawerEvents();
        loadArticles();
    }

    function bindEvents() {
        els['btn-approve'].addEventListener('click', () => submitReview('approved'));
        els['btn-reject'].addEventListener('click', showRejectInput);
        els['btn-submit-reject'].addEventListener('click', () => {
            const comment = els['reject-comment'].value.trim();
            if (!comment) {
                toast('Comment required for rejection', 'error');
                els['reject-comment'].focus();
                return;
            }
            submitReview('needs_fix', comment);
        });

        els['btn-prev'].addEventListener('click', () => navigate(-1));
        els['btn-next'].addEventListener('click', () => navigate(1));
        els['btn-last-seen'].addEventListener('click', goToLastSeen);
        els['btn-random'].addEventListener('click', goToRandom);
        els['btn-problem'].addEventListener('click', goToProblem);
        // Dashboard opened via "View stats" link inside progress, not container click

        document.addEventListener('keydown', (e) => {
            if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;

            switch (e.key) {
                case 'ArrowLeft':  e.preventDefault(); navigate(-1); break;
                case 'ArrowRight': e.preventDefault(); navigate(1); break;
                case 'a': case 'A': e.preventDefault(); submitReview('approved'); break;
                case 'r': case 'R': e.preventDefault(); showRejectInput(); break;
                case '?': e.preventDefault(); showHelp(); break;
            }
        });

        els['reject-comment'].addEventListener('keydown', (e) => {
            if (e.key === 'Escape') hideRejectInput();
            // Ctrl+Enter or Cmd+Enter to submit (plain Enter adds newline)
            if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
                e.preventDefault();
                const comment = els['reject-comment'].value.trim();
                if (!comment) {
                    toast('Comment required', 'error');
                    return;
                }
                submitReview('needs_fix', comment);
            }
        });
    }

    // ── Data loading ──

    async function loadArticles() {
        try {
            const res = await fetch(API + '/articles', { credentials: 'same-origin' });
            const data = await res.json();
            articles = data.articles || [];

            updateProgress(data.counts);

            if (data.warnings && data.warnings.length > 0) {
                console.warn('QA Splits — unimported JATS files:', data.warnings);
            }

            if (articles.length === 0) {
                els['qa-title'].textContent = 'No articles found';
                return;
            }

            // Initialize working set (full list by default)
            workingSet = articles.map((_, i) => i);

            // Check URL for set filter params
            const urlParams = new URL(window.location).searchParams;
            const urlSet = urlParams.get('set');
            const urlQ = urlParams.get('q');
            const urlPos = parseInt(urlParams.get('pos'), 10);
            const urlId = parseInt(urlParams.get('id'), 10);

            if (urlSet && urlQ) {
                // Apply filter from URL
                applyFilter(urlSet, urlQ);
                if (urlPos && urlPos > 0 && urlPos <= workingSet.length) {
                    loadArticleFromSet(urlPos - 1);
                } else {
                    loadArticleFromSet(0);
                }
            } else {
                // Single article or default
                let startIndex = 0;
                if (urlId) {
                    const idx = articles.findIndex(a => a.submission_id === urlId);
                    if (idx >= 0) startIndex = idx;
                } else if (lastSeenId) {
                    const idx = articles.findIndex(a => a.submission_id === lastSeenId);
                    if (idx >= 0) startIndex = idx;
                }
                setIndex = startIndex;
                loadArticle(startIndex);
            }

            // Pre-resolve random/problem targets so buttons are instant
            prefetchRandomTarget();
            prefetchProblemTarget();
        } catch (err) {
            els['qa-title'].textContent = 'Error loading articles';
            console.error('Failed to load articles:', err);
        }
    }

    async function loadArticle(index) {
        if (index < 0 || index >= articles.length) return;

        // Increment generation to invalidate any in-flight requests
        const gen = ++loadGeneration;
        currentIndex = index;
        const article = articles[index];

        localStorage.setItem('qa-last-seen', article.submission_id);
        lastSeenId = article.submission_id;

        // Track position in working set
        const si = workingSet.indexOf(index);
        if (si >= 0) setIndex = si;
        updateUrl();
        updateDrawerTab();
        if (setFilter) updateSetPosition();
        if (drawerOpen) renderDrawerList();

        // Compact title: 37.1 #14 (2026) Title [section]
        const sectionTag = article.section ? ' [' + article.section.toLowerCase() + ']' : '';
        els['qa-title'].textContent = article.volume + '.' + article.number
            + ' #' + article.seq + ' (' + article.year + ') '
            + article.title + sectionTag;
        els['qa-authors'].textContent = article.authors.length ? 'by ' + article.authors.join(', ') : '';
        els['qa-section'].textContent = '';
        els['qa-issue'].textContent = '';
        els['qa-pages'].textContent = 'ID ' + article.submission_id;

        updateStatusBadge(article.status, article.reviewer, article.reviewed_at, article.comment);
        hideRejectInput();

        // Check hash validity per-article (deferred from list to avoid O(n) file reads)
        if (article.status === 'approved' || article.status === 'needs_fix') {
            checkHashValidity(article, gen);
        }

        els['btn-prev'].disabled = (setIndex <= 0);
        els['btn-next'].disabled = (setIndex >= workingSet.length - 1);

        // Load content in parallel, checking generation before rendering
        loadPdf(article.submission_id, gen);
        loadHtml(article.submission_id, gen);
        loadClassification(article.submission_id, gen);

        // Prefetch nearby articles in background
        prefetchNearby(index);
    }

    /**
     * Prefetch PDF, HTML and classification for articles near the current index.
     * Fetches are fire-and-forget — results cached for instant display on navigation.
     */
    function prefetchNearby(index) {
        for (let offset = 1; offset <= PREFETCH_AHEAD; offset++) {
            for (const i of [index + offset, index - offset]) {
                if (i >= 0 && i < articles.length) {
                    const id = articles[i].submission_id;
                    if (!prefetchCache.has(id)) {
                        prefetchCache.set(id, { pdf: null, html: null, classification: null });
                        // Fire and forget — don't await
                        fetch(API + '/articles/' + id + '/pdf', { credentials: 'same-origin' })
                            .then(r => r.ok ? r.blob() : null)
                            .then(blob => { if (prefetchCache.has(id)) prefetchCache.get(id).pdf = blob; })
                            .catch(() => {});
                        fetch(API + '/articles/' + id + '/html', { credentials: 'same-origin' })
                            .then(r => r.ok ? r.text() : null)
                            .then(html => { if (prefetchCache.has(id)) prefetchCache.get(id).html = html; })
                            .catch(() => {});
                        fetch(API + '/articles/' + id + '/classification', { credentials: 'same-origin' })
                            .then(r => r.ok ? r.json() : null)
                            .then(data => { if (prefetchCache.has(id)) prefetchCache.get(id).classification = data; })
                            .catch(() => {});
                    }
                }
            }
        }

        // Evict entries far from current position to limit memory
        for (const [id] of prefetchCache) {
            const artIndex = articles.findIndex(a => a.submission_id === id);
            if (artIndex >= 0 && Math.abs(artIndex - index) > PREFETCH_AHEAD + 2) {
                prefetchCache.delete(id);
            }
        }
    }

    // ── PDF rendering ──

    async function loadPdf(submissionId, gen) {
        // Clean up previous PDF document to prevent memory leak
        if (pdfDoc) {
            pdfDoc.destroy();
            pdfDoc = null;
        }

        // Remove old scroll handler
        if (scrollHandler) {
            els['pdf-container'].removeEventListener('scroll', scrollHandler);
            scrollHandler = null;
        }

        els['pdf-container'].innerHTML = '<div class="qa-loading">Loading PDF...</div>';
        els['pdf-page-info'].textContent = 'Loading...';

        try {
            // Check prefetch cache first
            const cached = prefetchCache.get(submissionId);
            let doc;
            if (cached && cached.pdf) {
                const arrayBuffer = await cached.pdf.arrayBuffer();
                doc = await pdfjsLib.getDocument({ data: arrayBuffer }).promise;
            } else {
                const url = API + '/articles/' + submissionId + '/pdf';
                doc = await pdfjsLib.getDocument({ url, withCredentials: true }).promise;
            }

            // Stale check: if user navigated away, discard
            if (gen !== loadGeneration) {
                doc.destroy();
                return;
            }

            pdfDoc = doc;
            els['pdf-container'].innerHTML = '';
            const totalPages = pdfDoc.numPages;
            els['pdf-page-info'].textContent = totalPages + ' page' + (totalPages !== 1 ? 's' : '');

            for (let i = 1; i <= totalPages; i++) {
                if (gen !== loadGeneration) return;  // Abort if navigated away
                await renderPage(i);
            }

            // Attach scroll handler (stored for cleanup)
            scrollHandler = updatePageIndicator;
            els['pdf-container'].addEventListener('scroll', scrollHandler);
        } catch (err) {
            if (gen !== loadGeneration) return;
            els['pdf-container'].innerHTML = '<div class="qa-loading">PDF not available</div>';
            console.error('PDF load error:', err);
        }
    }

    async function renderPage(pageNum) {
        const page = await pdfDoc.getPage(pageNum);
        const containerWidth = els['pdf-container'].clientWidth - 16;
        const viewport = page.getViewport({ scale: 1 });
        const scale = containerWidth / viewport.width;
        const scaledViewport = page.getViewport({ scale });

        // Wrapper holds canvas + text layer in position
        const wrapper = document.createElement('div');
        wrapper.className = 'qa-pdf-page';
        wrapper.dataset.page = pageNum;
        wrapper.style.width = scaledViewport.width + 'px';
        wrapper.style.height = scaledViewport.height + 'px';

        const canvas = document.createElement('canvas');
        canvas.width = scaledViewport.width;
        canvas.height = scaledViewport.height;
        wrapper.appendChild(canvas);

        // Text layer for selectable/copyable text
        const textLayerDiv = document.createElement('div');
        textLayerDiv.className = 'qa-pdf-text-layer';
        wrapper.appendChild(textLayerDiv);

        els['pdf-container'].appendChild(wrapper);

        const ctx = canvas.getContext('2d');
        await page.render({ canvasContext: ctx, viewport: scaledViewport }).promise;

        // Render text layer
        const textContent = await page.getTextContent();
        pdfjsLib.renderTextLayer({
            textContentSource: textContent,
            container: textLayerDiv,
            viewport: scaledViewport,
        });
    }

    function updatePageIndicator() {
        const container = els['pdf-container'];
        const pages = container.querySelectorAll('.qa-pdf-page');
        const scrollTop = container.scrollTop;
        let currentPage = 1;

        pages.forEach((page) => {
            if (page.offsetTop <= scrollTop + 50) {
                currentPage = parseInt(page.dataset.page, 10);
            }
        });

        const total = pdfDoc ? pdfDoc.numPages : 0;
        els['pdf-page-info'].textContent = 'Page ' + currentPage + ' of ' + total;
    }

    // ── HTML + classification loading ──

    async function loadHtml(submissionId, gen) {
        els['html-content'].innerHTML = '<div class="qa-loading">Loading HTML...</div>';

        try {
            let html;
            const cached = prefetchCache.get(submissionId);
            if (cached && cached.html) {
                html = cached.html;
            } else {
                const res = await fetch(API + '/articles/' + submissionId + '/html', { credentials: 'same-origin' });
                if (gen !== loadGeneration) return;
                if (!res.ok) {
                    els['html-content'].innerHTML = '<div class="qa-loading">HTML galley not available</div>';
                    return;
                }
                html = await res.text();
            }
            if (gen !== loadGeneration) return;

            // Strip any script tags for safety (server CSP also blocks scripts)
            const sanitized = html.replace(/<script[\s\S]*?<\/script>/gi, '');
            els['html-content'].innerHTML = sanitized;
        } catch (err) {
            if (gen !== loadGeneration) return;
            els['html-content'].innerHTML = '<div class="qa-loading">Error loading HTML</div>';
        }
    }

    async function checkHashValidity(article, gen) {
        try {
            const res = await fetch(API + '/articles/' + article.submission_id, { credentials: 'same-origin' });
            if (gen !== loadGeneration) return;
            const data = await res.json();
            if (gen !== loadGeneration) return;

            if (data.current_hash && data.reviews && data.reviews.length > 0) {
                const latestHash = data.reviews[0].content_hash;
                if (latestHash && latestHash !== data.current_hash) {
                    article.status = 'invalidated';
                    updateStatusBadge('invalidated', article.reviewer, article.reviewed_at, article.comment);
                    toast('Content changed since last review', 'error');
                    recalculateProgress();
                }
            }
        } catch (err) {
            // Non-critical — hash check failure doesn't block review
        }
    }

    async function loadClassification(submissionId, gen) {
        els['endmatter-items'].innerHTML = '';

        try {
            let data;
            const cached = prefetchCache.get(submissionId);
            if (cached && cached.classification) {
                data = cached.classification;
            } else {
                const res = await fetch(API + '/articles/' + submissionId + '/classification', { credentials: 'same-origin' });
                if (gen !== loadGeneration) return;
                data = await res.json();
            }
            if (gen !== loadGeneration) return;

            renderClassification(data);
        } catch (err) {
            if (gen !== loadGeneration) return;
            els['endmatter-items'].innerHTML = '<div class="qa-endmatter-empty">Classification not available</div>';
        }
    }

    function renderClassification(data) {
        const container = els['endmatter-items'];
        container.innerHTML = '';

        if (data.error) {
            container.innerHTML = '<div class="qa-endmatter-empty">JATS parse error: ' + escapeHtml(data.error) + '</div>';
            return;
        }

        const sections = [
            { key: 'references', label: 'Reference', pillClass: 'qa-pill-reference' },
            { key: 'notes',      label: 'Note',      pillClass: 'qa-pill-note' },
            { key: 'bios',       label: 'Bio',        pillClass: 'qa-pill-bio' },
            { key: 'provenance', label: 'Provenance', pillClass: 'qa-pill-provenance' },
        ];

        let totalItems = 0;

        sections.forEach(sec => {
            const items = data[sec.key] || [];
            totalItems += items.length;

            items.forEach(item => {
                const li = document.createElement('div');
                li.className = 'qa-endmatter-item';
                li.setAttribute('role', 'listitem');

                const pill = document.createElement('span');
                pill.className = 'qa-pill ' + sec.pillClass;
                pill.textContent = sec.label;

                const text = document.createElement('span');
                text.className = 'qa-endmatter-text';
                text.textContent = item.text;  // textContent = safe from XSS

                li.appendChild(pill);
                li.appendChild(text);
                container.appendChild(li);
            });
        });

        if (totalItems === 0) {
            // Hide the entire section — nothing to show
            const section = document.getElementById('endmatter-section');
            if (section) section.style.display = 'none';
            return;
        }
        // Show section if it was previously hidden
        const section = document.getElementById('endmatter-section');
        if (section) section.style.display = '';
    }

    // ── Review actions ──

    function showRejectInput() {
        document.getElementById('qa-row-reject').style.display = '';
        els['btn-approve'].closest('.qa-btn-wrap').style.display = 'none';
        els['btn-reject'].closest('.qa-btn-wrap').style.display = 'none';
        // Prepopulate with existing rejection comment if any
        const article = currentIndex >= 0 ? articles[currentIndex] : null;
        if (article && article.comment && !els['reject-comment'].value) {
            els['reject-comment'].value = article.comment;
        }
        els['reject-comment'].focus();
    }

    function hideRejectInput() {
        document.getElementById('qa-row-reject').style.display = 'none';
        els['btn-approve'].closest('.qa-btn-wrap').style.display = '';
        els['btn-reject'].closest('.qa-btn-wrap').style.display = '';
        els['reject-comment'].value = '';
    }

    async function submitReview(decision, comment) {
        if (currentIndex < 0) return;

        // Prevent double-submit
        els['btn-approve'].disabled = true;
        els['btn-reject'].disabled = true;

        const article = articles[currentIndex];

        try {
            const res = await fetch(API + '/reviews', {
                method: 'POST',
                credentials: 'same-origin',
                headers: {
                    'Content-Type': 'application/json',
                    'X-Csrf-Token': window.QA_CONFIG.csrfToken,
                },
                body: JSON.stringify({
                    submissionId: article.submission_id,
                    decision: decision,
                    comment: comment || '',
                }),
            });

            const data = await res.json();

            if (!res.ok) {
                toast(data.error || 'Review failed', 'error');
                return;
            }

            article.status = decision;
            article.reviewer = 'you';
            article.reviewed_at = new Date().toISOString();
            article.comment = comment || null;

            hideRejectInput();
            recalculateProgress();

            // Auto-advance to next article in working set
            if (setIndex < workingSet.length - 1) {
                loadArticleFromSet(setIndex + 1);
            } else {
                // Last in set — just update the badge
                updateStatusBadge(decision, 'you', article.reviewed_at, article.comment);
            }
        } catch (err) {
            toast('Review failed — network error', 'error');
            console.error('Review submission error:', err);
        } finally {
            els['btn-approve'].disabled = false;
            els['btn-reject'].disabled = false;
        }
    }

    // ── Navigation ──

    function navigate(delta) {
        const newSi = setIndex + delta;
        if (newSi >= 0 && newSi < workingSet.length) {
            loadArticleFromSet(newSi);
        }
    }

    function goToLastSeen() {
        if (!lastSeenId) return;
        const idx = articles.findIndex(a => a.submission_id === lastSeenId);
        if (idx >= 0 && idx !== currentIndex) {
            loadArticle(idx);
        }
    }

    // Pre-fetched random/problem targets — resolved ahead of time
    let nextRandomIdx = null;
    let nextProblemIdx = null;

    function prefetchRandomTarget() {
        fetch(API + '/nav/random-unreviewed', { credentials: 'same-origin' })
            .then(r => r.json())
            .then(data => {
                if (data.submission_id) {
                    const idx = articles.findIndex(a => a.submission_id === data.submission_id);
                    nextRandomIdx = idx >= 0 ? idx : null;
                    // Also prefetch that article's content
                    if (nextRandomIdx !== null) prefetchNearby(nextRandomIdx);
                } else {
                    nextRandomIdx = null;
                }
            })
            .catch(() => { nextRandomIdx = null; });
    }

    function prefetchProblemTarget() {
        fetch(API + '/nav/problem-case', { credentials: 'same-origin' })
            .then(r => r.json())
            .then(data => {
                if (data.submission_id) {
                    const idx = articles.findIndex(a => a.submission_id === data.submission_id);
                    nextProblemIdx = idx >= 0 ? idx : null;
                    if (nextProblemIdx !== null) prefetchNearby(nextProblemIdx);
                } else {
                    nextProblemIdx = null;
                }
            })
            .catch(() => { nextProblemIdx = null; });
    }

    async function goToRandom() {
        if (nextRandomIdx !== null) {
            loadArticle(nextRandomIdx);
            nextRandomIdx = null;
            // Fetch the next random target in background
            prefetchRandomTarget();
            return;
        }
        // Fallback: fetch synchronously if prefetch wasn't ready
        try {
            const res = await fetch(API + '/nav/random-unreviewed', { credentials: 'same-origin' });
            const data = await res.json();
            if (data.submission_id) {
                const idx = articles.findIndex(a => a.submission_id === data.submission_id);
                if (idx >= 0) loadArticle(idx);
            } else {
                toast('All articles reviewed', 'success');
            }
        } catch (err) {
            toast('Error finding random article', 'error');
        }
        prefetchRandomTarget();
    }

    async function goToProblem() {
        if (nextProblemIdx !== null) {
            loadArticle(nextProblemIdx);
            nextProblemIdx = null;
            prefetchProblemTarget();
            return;
        }
        try {
            const res = await fetch(API + '/nav/problem-case', { credentials: 'same-origin' });
            const data = await res.json();
            if (data.submission_id) {
                const idx = articles.findIndex(a => a.submission_id === data.submission_id);
                if (idx >= 0) {
                    loadArticle(idx);
                    toast('Problem: ' + data.reason, 'error');
                }
            } else {
                toast('No problem cases found', 'success');
            }
        } catch (err) {
            toast('Error finding problem case', 'error');
        }
        prefetchProblemTarget();
    }

    // ── UI helpers ──

    function updateStatusBadge(status, reviewer, reviewedAt, comment) {
        const badge = els['qa-status'];
        badge.className = 'qa-badge qa-badge-' + status;
        badge.setAttribute('role', 'status');

        const statusLabels = { approved: 'Approved', needs_fix: 'Fix Requested', invalidated: 'Invalidated', unreviewed: 'Unreviewed' };
        let label = statusLabels[status] || status;
        if (reviewer && reviewedAt) {
            const d = new Date(reviewedAt);
            const months = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
            const date = String(d.getDate()).padStart(2,'0') + months[d.getMonth()] + String(d.getFullYear()).slice(2);
            label += ' ' + date;
        }
        // Show comment inline if needs_fix
        if (comment && status === 'needs_fix') {
            label += ' ⓘ';
            badge.style.cursor = 'pointer';
            badge.onclick = () => toast(comment, 'error');
        } else {
            badge.style.cursor = '';
            badge.onclick = null;
        }
        badge.textContent = label;
        badge.title = comment || '';
    }

    function updateProgress(counts) {
        if (!counts) return;
        const remaining = (counts.unreviewed || 0) + (counts.invalidated || 0);
        // Each entry: [text, filterStatus or null]
        const parts = [];
        if (counts.approved) parts.push([counts.approved + ' approved', 'approved']);
        if (counts.needs_fix) parts.push([counts.needs_fix + ' needs fix', 'needs_fix']);
        parts.push([counts.total + ' total', null]);
        if (remaining > 0) parts.push([remaining + ' remaining', 'unreviewed']);

        els['qa-progress'].innerHTML = '';
        parts.forEach(([text, filter], i) => {
            if (i > 0) els['qa-progress'].appendChild(document.createTextNode(' / '));
            const span = document.createElement('span');
            span.textContent = text;
            if (filter) {
                span.className = 'qa-progress-link';
                span.addEventListener('click', (e) => {
                    e.stopPropagation();
                    applyFilter('status', filter);
                    if (!drawerOpen) toggleDrawer();
                });
            }
            els['qa-progress'].appendChild(span);
        });

        // "View stats" link at the end
        els['qa-progress'].appendChild(document.createTextNode(' / '));
        const statsLink = document.createElement('span');
        statsLink.textContent = 'View stats';
        statsLink.className = 'qa-progress-link';
        statsLink.addEventListener('click', (e) => { e.stopPropagation(); showDashboard(); });
        els['qa-progress'].appendChild(statsLink);
    }

    function showFilteredList(status) {
        const existing = document.querySelector('.qa-dashboard-overlay');
        if (existing) existing.remove();

        const filterLabels = { approved: 'Approved', rejected: 'Needs Fix', unreviewed: 'Remaining' };
        const label = filterLabels[status] || status;
        const filtered = articles.filter(a => {
            if (status === 'unreviewed') return a.status === 'unreviewed' || a.status === 'invalidated';
            return a.status === status;
        });

        const overlay = document.createElement('div');
        overlay.className = 'qa-dashboard-overlay';
        overlay.setAttribute('role', 'dialog');
        overlay.setAttribute('aria-modal', 'true');
        overlay.addEventListener('click', (e) => { if (e.target === overlay) overlay.remove(); });
        document.addEventListener('keydown', function dismiss(e) {
            if (e.key === 'Escape') { overlay.remove(); document.removeEventListener('keydown', dismiss); }
        });

        let html = '<div class="qa-dashboard">'
            + '<div class="qa-dash-header">'
            + '<h2>' + label + ' Articles (' + filtered.length + ')</h2>'
            + '<button class="qa-dash-close" id="qa-dash-close">&times;</button>'
            + '</div>'
            + '<div class="qa-filtered-list">';

        if (filtered.length === 0) {
            html += '<p class="qa-filtered-empty">None</p>';
        } else {
            filtered.forEach(a => {
                const comment = (a.comment && status === 'needs_fix') ? '<span class="qa-filtered-comment">' + escapeHtml(a.comment) + '</span>' : '';
                html += '<div class="qa-filtered-item" data-sid="' + a.submission_id + '">'
                    + '<span class="qa-filtered-title">'
                    + a.volume + '.' + a.number + ' #' + a.seq + ' ' + escapeHtml(a.title)
                    + '</span>'
                    + comment
                    + '</div>';
            });
        }

        html += '</div></div>';
        overlay.innerHTML = html;
        document.body.appendChild(overlay);

        // Close button
        const closeBtn = overlay.querySelector('#qa-dash-close');
        if (closeBtn) closeBtn.addEventListener('click', () => overlay.remove());

        // Click article to navigate
        overlay.querySelectorAll('.qa-filtered-item').forEach(item => {
            item.addEventListener('click', () => {
                const sid = parseInt(item.dataset.sid, 10);
                const idx = articles.findIndex(a => a.submission_id === sid);
                if (idx >= 0) {
                    overlay.remove();
                    loadArticle(idx);
                }
            });
        });
    }

    function recalculateProgress() {
        const counts = { total: articles.length, approved: 0, rejected: 0, invalidated: 0, unreviewed: 0 };
        articles.forEach(a => {
            if (a.status === 'approved') counts.approved++;
            else if (a.status === 'needs_fix') counts.needs_fix++;
            else if (a.status === 'invalidated') counts.invalidated++;
            else counts.unreviewed++;
        });
        updateProgress(counts);
    }

    function showFeedback(decision) {
        // Show inline feedback under the relevant button
        const feedbackEl = decision === 'approved'
            ? document.getElementById('feedback-approve')
            : document.getElementById('feedback-reject');

        if (!feedbackEl) return;

        feedbackEl.textContent = decision === 'error' ? 'Error' : 'Done';
        feedbackEl.classList.remove('qa-feedback-show');
        // Force reflow to restart animation
        void feedbackEl.offsetWidth;
        feedbackEl.classList.add('qa-feedback-show');
    }

    function toast(message, type) {
        const div = document.createElement('div');
        div.className = 'qa-toast qa-toast-' + type;
        div.setAttribute('role', 'alert');
        div.textContent = message;
        document.body.appendChild(div);
        setTimeout(() => div.remove(), 2500);
    }

    function escapeHtml(str) {
        const div = document.createElement('div');
        div.textContent = str;
        return div.innerHTML;
    }

    function showHelp() {
        const existing = document.querySelector('.qa-help-overlay');
        if (existing) { existing.remove(); return; }

        const overlay = document.createElement('div');
        overlay.className = 'qa-help-overlay';
        overlay.setAttribute('role', 'dialog');
        overlay.setAttribute('aria-modal', 'true');
        overlay.setAttribute('aria-label', 'Keyboard shortcuts');
        overlay.innerHTML = '<div class="qa-help-box">'
            + '<h3>Keyboard Shortcuts</h3>'
            + '<table>'
            + '<tr><td><kbd>&larr;</kbd> / <kbd>&rarr;</kbd></td><td>Previous / Next article</td></tr>'
            + '<tr><td><kbd>A</kbd></td><td>Approve article</td></tr>'
            + '<tr><td><kbd>R</kbd></td><td>Reject (opens comment box)</td></tr>'
            + '<tr><td><kbd>Esc</kbd></td><td>Close reject box / this help</td></tr>'
            + '<tr><td><kbd>?</kbd></td><td>Toggle this help</td></tr>'
            + '</table>'
            + '<p>Press any key to close</p>'
            + '</div>';

        overlay.addEventListener('click', () => overlay.remove());
        document.addEventListener('keydown', function dismiss() {
            overlay.remove();
            document.removeEventListener('keydown', dismiss);
        }, { once: true });

        document.body.appendChild(overlay);
    }

    // ── Stats dashboard overlay ──

    async function showDashboard() {
        const existing = document.querySelector('.qa-dashboard-overlay');
        if (existing) { existing.remove(); return; }

        const overlay = document.createElement('div');
        overlay.className = 'qa-dashboard-overlay';
        overlay.setAttribute('role', 'dialog');
        overlay.setAttribute('aria-modal', 'true');
        overlay.setAttribute('aria-label', 'QA Progress Dashboard');
        overlay.innerHTML = '<div class="qa-dashboard"><div class="qa-loading">Loading stats...</div></div>';

        overlay.addEventListener('click', (e) => {
            if (e.target === overlay) overlay.remove();
        });
        document.addEventListener('keydown', function dismiss(e) {
            if (e.key === 'Escape') { overlay.remove(); document.removeEventListener('keydown', dismiss); }
        });

        document.body.appendChild(overlay);

        try {
            const res = await fetch(API + '/stats', { credentials: 'same-origin' });
            const data = await res.json();
            renderDashboard(overlay.querySelector('.qa-dashboard'), data);
        } catch (err) {
            overlay.querySelector('.qa-dashboard').innerHTML = '<p>Failed to load stats</p>';
        }
    }

    function renderDashboard(container, data) {
        const o = data.overall;
        const pctApproved = o.total ? Math.round((o.approved / o.total) * 100) : 0;
        const pctRejected = o.total ? Math.round((o.needs_fix / o.total) * 100) : 0;
        const pctUnreviewed = o.total ? Math.round((o.unreviewed / o.total) * 100) : 0;

        // Reviewer depth
        const rd = data.by_reviewer_count || [];

        let html = '<div class="qa-dash-header">'
            + '<h2>QA Progress</h2>'
            + '<button class="qa-dash-close" id="qa-dash-close">&times;</button>'
            + '</div>';

        // Overall donut
        html += '<div class="qa-dash-overview">';
        html += '<div class="qa-dash-donut-wrap">';
        html += renderDonut(pctApproved, pctRejected, pctUnreviewed, o.total);
        html += '</div>';
        html += '<div class="qa-dash-numbers">';
        html += '<div class="qa-dash-stat qa-dash-stat-approved"><span class="qa-dash-num">' + o.approved + '</span><span class="qa-dash-label">Approved</span></div>';
        html += '<div class="qa-dash-stat qa-dash-stat-needs_fix"><span class="qa-dash-num">' + o.needs_fix + '</span><span class="qa-dash-label">Needs Fix</span></div>';
        html += '<div class="qa-dash-stat qa-dash-stat-unreviewed"><span class="qa-dash-num">' + o.unreviewed + '</span><span class="qa-dash-label">Unreviewed</span></div>';
        html += '</div>';
        html += '</div>';

        // Reviewer depth
        html += '<div class="qa-dash-section"><h3>Review Depth</h3><div class="qa-dash-bars">';
        rd.forEach(r => {
            const pct = o.total ? Math.round((r.count / o.total) * 100) : 0;
            html += '<div class="qa-dash-bar-row">'
                + '<span class="qa-dash-bar-label">' + r.label + '</span>'
                + '<div class="qa-dash-bar-track"><div class="qa-dash-bar-fill" style="width:' + pct + '%"></div></div>'
                + '<span class="qa-dash-bar-value">' + r.count + '</span>'
                + '</div>';
        });
        html += '</div></div>';

        // Section breakdown
        html += '<div class="qa-dash-section"><h3>By Section</h3><table class="qa-dash-table">';
        html += '<tr><th>Section</th><th>Total</th><th>Approved</th><th>Needs Fix</th><th>Unreviewed</th><th>Progress</th></tr>';

        const sections = Object.entries(data.by_section || {}).sort((a, b) => b[1].total - a[1].total);
        sections.forEach(([name, s]) => {
            const pct = s.total ? Math.round((s.approved / s.total) * 100) : 0;
            html += '<tr>'
                + '<td>' + escapeHtml(name) + '</td>'
                + '<td>' + s.total + '</td>'
                + '<td>' + s.approved + '</td>'
                + '<td>' + s.needs_fix + '</td>'
                + '<td>' + s.unreviewed + '</td>'
                + '<td><div class="qa-dash-mini-bar"><div class="qa-dash-mini-fill" style="width:' + pct + '%"></div><span>' + pct + '%</span></div></td>'
                + '</tr>';
        });
        html += '</table></div>';

        container.innerHTML = html;

        // Close button
        const closeBtn = container.querySelector('#qa-dash-close');
        if (closeBtn) closeBtn.addEventListener('click', () => container.closest('.qa-dashboard-overlay').remove());

        // Animate donut segments in
        requestAnimationFrame(() => {
            container.querySelectorAll('.qa-dash-donut-seg').forEach(seg => {
                seg.style.strokeDashoffset = seg.dataset.target;
            });
            container.querySelectorAll('.qa-dash-bar-fill').forEach(bar => {
                bar.style.transition = 'width 0.6s cubic-bezier(0.16, 1, 0.3, 1)';
            });
        });
    }

    function renderDonut(pctApproved, pctRejected, pctUnreviewed, total) {
        // SVG donut chart
        const r = 70, cx = 90, cy = 90, circ = 2 * Math.PI * r;
        const segApproved = (pctApproved / 100) * circ;
        const segRejected = (pctRejected / 100) * circ;
        const segUnreviewed = (pctUnreviewed / 100) * circ;

        const offApproved = 0;
        const offRejected = segApproved;
        const offUnreviewed = segApproved + segRejected;

        return '<svg class="qa-dash-donut" viewBox="0 0 180 180">'
            + '<circle cx="' + cx + '" cy="' + cy + '" r="' + r + '" fill="none" stroke="var(--divider)" stroke-width="18"/>'
            + '<circle class="qa-dash-donut-seg" cx="' + cx + '" cy="' + cy + '" r="' + r + '" fill="none" '
            + 'stroke="var(--color-approve)" stroke-width="18" '
            + 'stroke-dasharray="' + segApproved + ' ' + circ + '" '
            + 'stroke-dashoffset="0" '
            + 'data-target="0" '
            + 'transform="rotate(-90 ' + cx + ' ' + cy + ')"/>'
            + '<circle class="qa-dash-donut-seg" cx="' + cx + '" cy="' + cy + '" r="' + r + '" fill="none" '
            + 'stroke="var(--color-fix)" stroke-width="18" '
            + 'stroke-dasharray="' + segRejected + ' ' + circ + '" '
            + 'stroke-dashoffset="-' + offRejected + '" '
            + 'data-target="-' + offRejected + '" '
            + 'transform="rotate(-90 ' + cx + ' ' + cy + ')"/>'
            + '<text x="' + cx + '" y="' + cy + '" text-anchor="middle" dy="0.35em" '
            + 'font-family="var(--font-ui)" font-size="28" font-weight="700" fill="var(--text-primary)">'
            + pctApproved + '%</text>'
            + '<text x="' + cx + '" y="' + (cy + 18) + '" text-anchor="middle" '
            + 'font-family="var(--font-ui)" font-size="11" fill="var(--text-muted)">'
            + total + ' articles</text>'
            + '</svg>';
    }

    // ── Drawer: collapsible article list + filtering ──

    let drawerPinned = false;

    function bindDrawerEvents() {
        document.getElementById('qa-drawer-tab').addEventListener('click', toggleDrawer);
        document.getElementById('qa-drawer-close').addEventListener('click', () => { if (!drawerPinned) toggleDrawer(); });
        document.getElementById('qa-drawer-pin').addEventListener('click', togglePin);
        document.getElementById('qa-drawer-search').addEventListener('input', refilterDrawer);
        document.getElementById('qa-drawer-issue').addEventListener('change', refilterDrawer);
        document.getElementById('qa-drawer-status').addEventListener('change', refilterDrawer);
        document.getElementById('qa-drawer-section').addEventListener('change', refilterDrawer);
    }

    function toggleDrawer() {
        drawerOpen = !drawerOpen;
        const drawer = document.getElementById('qa-drawer');
        const tab = document.getElementById('qa-drawer-tab');
        if (drawerOpen) {
            drawer.style.display = '';
            tab.style.display = 'none';
            populateDropdowns();
            renderDrawerList();
            document.getElementById('qa-drawer-search').focus();
        } else {
            if (drawerPinned) togglePin();
            drawer.style.display = 'none';
            tab.style.display = '';
        }
    }

    function togglePin() {
        drawerPinned = !drawerPinned;
        const layout = document.querySelector('.qa-layout');
        const pinBtn = document.getElementById('qa-drawer-pin');
        const closeBtn = document.getElementById('qa-drawer-close');
        if (drawerPinned) {
            layout.classList.add('qa-drawer-pinned');
            pinBtn.classList.add('pinned');
            closeBtn.style.display = 'none';
        } else {
            layout.classList.remove('qa-drawer-pinned');
            pinBtn.classList.remove('pinned');
            closeBtn.style.display = '';
        }
    }

    function populateDropdowns() {
        const issueSelect = document.getElementById('qa-drawer-issue');
        const statusSelect = document.getElementById('qa-drawer-status');
        const sectionSelect = document.getElementById('qa-drawer-section');

        // Issues — all unique vol.num sorted desc
        const issues = {};
        articles.forEach(a => {
            const key = a.volume + '.' + a.number;
            issues[key] = (issues[key] || 0) + 1;
        });
        const sortedIssues = Object.entries(issues).sort((a, b) => {
            const [av, an] = a[0].split('.').map(Number);
            const [bv, bn] = b[0].split('.').map(Number);
            return bv - av || bn - an;
        });
        issueSelect.innerHTML = '<option value="">All issues</option>'
            + sortedIssues.map(([k, c]) => '<option value="' + k + '">Issue ' + k + ' (' + c + ')</option>').join('');

        // Statuses
        statusSelect.innerHTML = '<option value="">All statuses</option>'
            + '<option value="approved">Approved</option>'
            + '<option value="needs_fix">Needs Fix</option>'
            + '<option value="unreviewed">Unreviewed</option>';

        // Sections
        const sections = {};
        articles.forEach(a => { if (a.section) sections[a.section] = (sections[a.section] || 0) + 1; });
        sectionSelect.innerHTML = '<option value="">All sections</option>'
            + Object.entries(sections).sort((a, b) => b[1] - a[1])
                .map(([k, c]) => '<option value="' + k + '">' + k + ' (' + c + ')</option>').join('');

        // Restore selections if filter is active
        if (setFilter) {
            if (setFilter.type === 'issue') issueSelect.value = setFilter.query;
            if (setFilter.type === 'status') statusSelect.value = setFilter.query;
            if (setFilter.type === 'section') sectionSelect.value = setFilter.query;
        }
    }

    function refilterDrawer() {
        const search = document.getElementById('qa-drawer-search').value.trim();
        const issue = document.getElementById('qa-drawer-issue').value;
        const status = document.getElementById('qa-drawer-status').value;
        const section = document.getElementById('qa-drawer-section').value;

        // Build combined filter
        workingSet = [];
        const q = search.toLowerCase();
        articles.forEach((a, i) => {
            if (issue && (a.volume + '.' + a.number) !== issue) return;
            if (status && a.status !== status) return;
            if (section && a.section !== section) return;
            if (q && !a.title.toLowerCase().includes(q)
                && !a.authors.some(auth => auth.toLowerCase().includes(q))
                && !(a.section || '').toLowerCase().includes(q)) return;
            workingSet.push(i);
        });

        // Track active filter for URL/display
        if (issue) setFilter = { type: 'issue', query: issue };
        else if (status) setFilter = { type: 'status', query: status };
        else if (section) setFilter = { type: 'section', query: section };
        else if (q) setFilter = { type: 'search', query: search };
        else setFilter = null;

        setIndex = workingSet.indexOf(currentIndex);
        if (setIndex < 0 && workingSet.length > 0) {
            setIndex = 0;
            loadArticleFromSet(0);
        }

        updateSetPosition();
        updateDrawerTab();
        renderDrawerList();
        updateUrl();
    }

    function applyFilter(type, query) {
        // Set the appropriate dropdown and clear others
        const issueEl = document.getElementById('qa-drawer-issue');
        const statusEl = document.getElementById('qa-drawer-status');
        const sectionEl = document.getElementById('qa-drawer-section');
        const searchEl = document.getElementById('qa-drawer-search');

        if (issueEl) issueEl.value = type === 'issue' ? query : '';
        if (statusEl) statusEl.value = type === 'status' ? query : '';
        if (sectionEl) sectionEl.value = type === 'section' ? query : '';
        if (searchEl) searchEl.value = type === 'search' ? query : '';

        // Delegate to refilterDrawer which reads from dropdowns
        refilterDrawer();
    }

    function loadArticleFromSet(si) {
        if (si < 0 || si >= workingSet.length) return;
        setIndex = si;
        loadArticle(workingSet[si]);
    }

    function renderDrawerList() {
        const list = document.getElementById('qa-drawer-list');
        const filters = document.getElementById('qa-drawer-filters');

        // Quick filter buttons
        const counts = { rejected: 0, unreviewed: 0 };
        const issues = {};
        articles.forEach(a => {
            if (a.status === 'needs_fix') counts.needs_fix++;
            if (a.status === 'unreviewed') counts.unreviewed++;
            const key = a.volume + '.' + a.number;
            issues[key] = (issues[key] || 0) + 1;
        });

        let filtersHtml = '';
        if (counts.needs_fix) filtersHtml += '<button class="qa-drawer-filter-btn' + (setFilter && setFilter.query === 'needs_fix' ? ' active' : '') + '" data-type="status" data-q="needs_fix">Needs Fix (' + counts.needs_fix + ')</button>';
        if (counts.unreviewed) filtersHtml += '<button class="qa-drawer-filter-btn' + (setFilter && setFilter.query === 'unreviewed' ? ' active' : '') + '" data-type="status" data-q="unreviewed">Unreviewed (' + counts.unreviewed + ')</button>';

        // Top 5 issues by article count
        const sortedIssues = Object.entries(issues).sort((a, b) => b[1] - a[1]).slice(0, 5);
        sortedIssues.forEach(([key, count]) => {
            filtersHtml += '<button class="qa-drawer-filter-btn' + (setFilter && setFilter.query === key ? ' active' : '') + '" data-type="issue" data-q="' + key + '">Issue ' + key + ' (' + count + ')</button>';
        });

        filters.innerHTML = filtersHtml;
        filters.querySelectorAll('.qa-drawer-filter-btn').forEach(btn => {
            btn.addEventListener('click', () => {
                const type = btn.dataset.type;
                const q = btn.dataset.q;
                // Toggle off if same filter clicked
                if (setFilter && setFilter.type === type && setFilter.query === q) {
                    applyFilter(null, null);
                    document.getElementById('qa-drawer-search').value = '';
                } else {
                    applyFilter(type, q);
                    document.getElementById('qa-drawer-search').value = '';
                }
            });
        });

        // Article list
        let html = '';
        workingSet.forEach((artIdx, si) => {
            const a = articles[artIdx];
            const active = si === setIndex ? ' active' : '';
            const icon = a.status === 'approved' ? '✓' : a.status === 'needs_fix' ? '✗' : '·';
            html += '<div class="qa-drawer-item' + active + '" data-si="' + si + '">'
                + '<span class="qa-drawer-item-status">' + icon + '</span>'
                + '<span class="qa-drawer-item-title">' + a.volume + '.' + a.number + ' #' + a.seq + ' ' + escapeHtml(a.title) + '</span>'
                + '</div>';
        });
        list.innerHTML = html || '<div style="padding:20px;text-align:center;color:rgba(255,255,255,0.35)">No articles match</div>';

        list.querySelectorAll('.qa-drawer-item').forEach(item => {
            item.addEventListener('click', () => {
                loadArticleFromSet(parseInt(item.dataset.si, 10));
                renderDrawerList(); // Re-render to update active state
            });
        });

        // Scroll active item into view
        const activeEl = list.querySelector('.qa-drawer-item.active');
        if (activeEl) activeEl.scrollIntoView({ block: 'nearest' });

        // Footer
        const footer = document.getElementById('qa-drawer-footer');
        let footerText = (setIndex + 1) + ' / ' + workingSet.length;
        if (setFilter) {
            footerText += ' <button class="qa-drawer-clear">Clear filter</button>';
        }
        footer.innerHTML = footerText;
        const clearBtn = footer.querySelector('.qa-drawer-clear');
        if (clearBtn) clearBtn.addEventListener('click', () => {
            applyFilter(null, null);
            document.getElementById('qa-drawer-search').value = '';
        });
    }

    function updateDrawerTab() {
        const tab = document.getElementById('qa-drawer-tab-text');
        if (setFilter) {
            tab.textContent = (setIndex + 1) + '/' + workingSet.length;
        } else {
            tab.textContent = (setIndex + 1) + '/' + articles.length;
        }
    }

    function updateSetPosition() {
        // Update progress counter to show set position when filtered
        if (setFilter && workingSet.length > 0) {
            const label = setFilter.type === 'search' ? '"' + setFilter.query + '"'
                : setFilter.type === 'author' ? 'by ' + setFilter.query
                : setFilter.type === 'issue' ? 'Issue ' + setFilter.query
                : setFilter.query;
            els['qa-progress'].innerHTML = '';
            const posText = document.createTextNode((setIndex + 1) + ' / ' + workingSet.length + ' ' + label + ' ');
            els['qa-progress'].appendChild(posText);
            const exitLink = document.createElement('span');
            exitLink.textContent = '× Clear filter';
            exitLink.className = 'qa-progress-link';
            exitLink.addEventListener('click', (e) => {
                e.stopPropagation();
                applyFilter(null, null);
                document.getElementById('qa-drawer-search').value = '';
            });
            els['qa-progress'].appendChild(exitLink);
        }
    }

    function updateUrl() {
        const url = new URL(window.location);
        if (setFilter) {
            url.searchParams.set('set', setFilter.type);
            url.searchParams.set('q', setFilter.query);
            url.searchParams.set('pos', String(setIndex + 1));
            url.searchParams.delete('id');
        } else {
            url.searchParams.delete('set');
            url.searchParams.delete('q');
            url.searchParams.delete('pos');
            if (currentIndex >= 0) {
                url.searchParams.set('id', articles[currentIndex].submission_id);
            }
        }
        history.replaceState(null, '', url);
    }

    // Boot
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
