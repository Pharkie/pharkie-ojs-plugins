/**
 * Archive Checker — Alpine.js application (ES module)
 *
 * Replaces vanilla JS with reactive state management.
 * PDF.js rendering stays imperative (it's a canvas library).
 */

import * as pdfjsLib from './pdf.min.mjs';
import { EventBus, PDFFindController, PDFLinkService, PDFViewer } from './pdf_viewer.mjs';
import Alpine from './alpine.esm.min.js';

pdfjsLib.GlobalWorkerOptions.workerSrc = new URL('./pdf.worker.min.mjs', import.meta.url).href;

// pdf.js viewer infrastructure
const _eventBus = new EventBus();
const _linkService = new PDFLinkService({ eventBus: _eventBus });
const _findController = new PDFFindController({ linkService: _linkService, eventBus: _eventBus });

// Full PDFViewer — handles canvas, text layer, find highlighting, resize, everything
let _viewer = null;

// Non-reactive state
const _pdf = { doc: null, loadGen: 0 };

Alpine.data('acApp', () => ({
    // Config
    api: window.AC_CONFIG.apiBase,
    pluginUrl: window.AC_CONFIG.pluginUrl,
    csrfToken: window.AC_CONFIG.csrfToken,
    currentUsername: window.AC_CONFIG.username,

    // Article data
    articles: [],
    counts: null,
    loading: true,

    // Current article
    currentIndex: -1,
    htmlContent: '',
    htmlLoading: true,
    isContentFiltered: false,
    pdfLoading: true,
    pdfPageInfo: 'Loading...',
    classification: null,

    // Working set (filtered subset)
    workingSet: [],
    setIndex: -1,
    setFilter: null,

    // Filters
    searchQuery: '',
    issueFilter: '',
    activeStatuses: new Set(),
    activeSections: new Set(),
    activeReviewers: new Set(),
    showOnlyContentFiltered: false, // when true, show ONLY content-filtered articles

    // Dark mode
    isDarkMode: window.matchMedia('(prefers-color-scheme: dark)').matches,

    // Review UI
    rejectComment: '',
    submitting: false,
    approveLabel: 'Approve',
    reportSaved: false,
    get reportLabel() {
        if (this.reportSaved) return 'Updated \u2713';
        const a = this.article;
        if (a && (a.status === 'needs_fix' || a.status === 'recheck' || a.status === 'deferred')) return 'Update Problem';
        return 'Report Problem';
    },

    // PDF search
    pdfSearchQuery: '',
    pdfSearchOpen: false,
    _findTick: 0,

    // Prefetch
    prefetchCache: new Map(),
    _prefetchController: null,


    // ── Lifecycle ──

    async init() {
        this.bindKeys();

        // Create the PDFViewer — it manages all page rendering, text layers, find
        const darkMode = window.matchMedia('(prefers-color-scheme: dark)');
        const viewerOpts = {
            container: document.getElementById('pdf-container'),
            viewer: document.getElementById('pdf-viewer'),
            eventBus: _eventBus,
            linkService: _linkService,
            findController: _findController,
            annotationMode: 0, // DISABLE
            removePageBorders: true,
        };
        if (darkMode.matches) {
            viewerOpts.pageColors = { background: '#1a1a1e', foreground: '#e8e4de' };
        }
        _viewer = new PDFViewer(viewerOpts);
        _linkService.setViewer(_viewer);

        // Re-render PDF when OS colour scheme changes
        darkMode.addEventListener('change', (e) => {
            this.isDarkMode = e.matches;
            if (_pdf.doc) {
                _viewer.pageColors = e.matches
                    ? { background: '#1a1a1e', foreground: '#e8e4de' }
                    : null;
                // Force re-render by resetting the document
                _viewer.setDocument(_pdf.doc);
                _eventBus.on('pagesloaded', () => {
                    _viewer.currentScaleValue = 'page-width';
                }, { once: true });
            }
        });

        // Trigger Alpine reactivity when find controller updates
        _eventBus.on('updatefindmatchescount', () => { this._findTick++; });
        _eventBus.on('updatefindcontrolstate', () => { this._findTick++; });

        // Update page indicator on scroll
        _eventBus.on('pagechanging', ({ pageNumber }) => {
            this.pdfPageInfo = 'Page ' + pageNumber + ' of ' + (_pdf.doc ? _pdf.doc.numPages : 0);
        });

        // Re-fit PDF to container width on resize
        new ResizeObserver(() => {
            if (_viewer.currentScaleValue === 'page-width') {
                _viewer.currentScaleValue = 'page-width';
            }
        }).observe(document.getElementById('pdf-container'));

        await this.loadArticles();

        // Post-load UI — run via $nextTick to ensure Alpine reactivity
        // (async init() loses proxy context after await)
        this.$nextTick(() => {
            if (!localStorage.getItem('ac-help-seen')) {
                this.showGuide = true;
            } else if (this.shouldIntroduceDrawer()) {
                this.drawerIntro = true;
                this.drawerOpen = true;
                setTimeout(() => this.$nextTick(() => {
                    this.drawerOpen = false;
                    this.drawerIntro = false;
                }), 1500);
            }
        });
    },

    // ── Data loading ──

    async loadArticles() {
        try {
            const res = await fetch(this.api + '/articles', { credentials: 'same-origin' });
            if (!res.ok) throw new Error('Failed to load articles');
            const data = await res.json();
            this.articles = data.articles || [];
            this.counts = data.counts;
            this.loading = false;

            if (this.articles.length === 0) return;

            // Parse URL params BEFORE building working set — avoids
            // refilter() triggering a premature article load that races
            // with the ?id= navigation.
            const params = new URL(window.location).searchParams;
            const urlId = parseInt(params.get('id'), 10);

            if (params.get('issue')) { this.issueFilter = params.get('issue'); }
            if (params.get('status')) { params.get('status').split(',').forEach(s => this.activeStatuses.add(s)); }
            if (params.get('section')) { params.get('section').split(',').forEach(s => this.activeSections.add(s)); }
            if (params.get('reviewer')) { params.get('reviewer').split(',').forEach(s => this.activeReviewers.add(s)); }
            if (params.get('q')) { this.searchQuery = params.get('q'); }

            // Build working set (applies content-filtered exclusion + URL filters).
            // Don't use refilter() — it calls goToSetIndex(0) which would load
            // the wrong article before we handle ?id=.
            this._buildWorkingSet();

            if (params.get('mode') === 'random') {
                await this.goToRandom();
            } else {
                let start = 0;
                if (urlId) {
                    const artIdx = this.articles.findIndex(a => a.submission_id === urlId);
                    const wsIdx = this.workingSet.indexOf(artIdx);
                    if (wsIdx >= 0) {
                        start = wsIdx;
                    }
                    // If not in working set (e.g. just approved), fall through
                    // to start=0 so left pane and content stay in sync.
                } else {
                    const lastSeen = parseInt(localStorage.getItem('ac-last-seen'), 10);
                    if (lastSeen) {
                        const artIdx = this.articles.findIndex(a => a.submission_id === lastSeen);
                        const wsIdx = this.workingSet.indexOf(artIdx);
                        if (wsIdx >= 0) start = wsIdx;
                    }
                }
                this.setIndex = start;
                await this.loadArticle(this.workingSet[start]);
            }

            this.prefetchRandomTarget();
        } catch (err) {
            this.loading = false;
            console.error('Failed to load articles:', err);
        }
    },

    // ── Article display ──

    get article() {
        return this.currentIndex >= 0 ? this.articles[this.currentIndex] : null;
    },

    get titleDisplay() {
        const a = this.article;
        if (!a) return this.loading ? 'Loading...' : 'No articles found';
        const section = a.section ? ' [' + a.section.toLowerCase() + ']' : '';
        return a.volume + '.' + a.number + ' #' + a.seq + ' (' + a.year + ') ' + a.title + section + ' [id: ' + a.submission_id + ']';
    },

    get authorsDisplay() {
        const a = this.article;
        return a && a.authors.length ? 'by ' + a.authors.join(', ') : '';
    },

    get statusLabel() {
        const labels = { approved: 'Approved', needs_fix: 'Reported', recheck: 'Recheck', deferred: 'Deferred', invalidated: 'Invalidated', unreviewed: 'Unchecked' };
        const a = this.article;
        if (!a) return '';
        let label = labels[a.status] || a.status;
        if (a.reviewer) label += ' by ' + a.reviewer;
        if (a.reviewed_at) {
            const d = new Date(a.reviewed_at);
            const months = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
            label += ', ' + String(d.getDate()).padStart(2,'0') + months[d.getMonth()] + String(d.getFullYear()).slice(2);
        }
        return label;
    },

    get statusClass() {
        return this.article ? 'ac-badge ac-badge-' + this.article.status : 'ac-badge';
    },

    get positionDisplay() {
        if (this.workingSet.length === 0) return '';
        return (this.setIndex + 1) + ' / ' + this.workingSet.length;
    },

    get progressDisplay() {
        if (!this.counts) return '';
        const c = this.counts;
        const parts = [];
        if (c.approved) parts.push(c.approved + ' approved');
        if (c.needs_fix) parts.push(c.needs_fix + ' reported');
        if (c.recheck) parts.push(c.recheck + ' recheck');
        if (c.deferred) parts.push(c.deferred + ' deferred');
        const remaining = (c.unreviewed || 0) + (c.invalidated || 0);
        if (remaining > 0) parts.push(remaining + ' unchecked of ' + c.total);
        else parts.push(c.total + ' total');
        return parts.join(' \u00b7 ');
    },

    get progressApprovedPct() {
        if (!this.counts || !this.counts.total) return 0;
        return ((this.counts.approved || 0) / this.counts.total * 100).toFixed(1);
    },

    get progressReportedPct() {
        if (!this.counts || !this.counts.total) return 0;
        return ((this.counts.needs_fix || 0) / this.counts.total * 100).toFixed(1);
    },

    showFixReason() {
        const a = this.article;
        if (a && a.comment && a.status === 'needs_fix') {
            alert(a.comment); // Simple for now — could be a toast
        }
    },

    // ── Navigation ──

    async loadArticle(index) {
        if (index < 0 || index >= this.articles.length) return;
        _pdf.loadGen++;
        this.currentIndex = index;
        const a = this.articles[index];

        // Abort any in-flight prefetch requests so they don't block
        // PHP-FPM workers needed for the current article.
        if (this._prefetchController) {
            this._prefetchController.abort();
            this._prefetchController = null;
        }

        localStorage.setItem('ac-last-seen', a.submission_id);
        const si = this.workingSet.indexOf(index);
        if (si >= 0) this.setIndex = si;
        this.updateUrl();
        // Populate comment from existing review (if any)
        this.rejectComment = a.comment || '';
        this.approveLabel = 'Approve';

        // Load current article, then prefetch nearby once done.
        await Promise.all([
            this.loadPdf(a.submission_id),
            this.loadHtml(a.submission_id),
            this.loadClassification(a.submission_id),
        ]);

        // Scroll right pane and PDF container to top
        document.querySelector('.ac-right')?.scrollTo(0, 0);
        document.getElementById('pdf-container')?.scrollTo(0, 0);
        this.prefetchNearby(this.setIndex);
    },

    goToSetIndex(si) {
        if (si >= 0 && si < this.workingSet.length) {
            this.setIndex = si;
            this.loadArticle(this.workingSet[si]);
        }
    },

    navigate(delta) {
        this.goToSetIndex(this.setIndex + delta);
    },

    diceRolling: false,

    async goToRandom() {
        this.diceRolling = true;
        setTimeout(() => { this.diceRolling = false; }, 1500);
        try {
            const res = await fetch(this.api + '/nav/random-unreviewed', { credentials: 'same-origin' });
            if (!res.ok) throw new Error('Failed to load random articles');
            const data = await res.json();
            const ids = data.submission_ids || [];
            if (ids.length === 0) return;

            const indices = ids
                .map(id => this.articles.findIndex(a => a.submission_id === id))
                .filter(i => i >= 0)
                .filter(i => !this.articles[i].content_filtered); // always exclude content-filtered from random
            if (indices.length === 0) return;

            this.searchQuery = '';
            this.issueFilter = '';
            this.activeStatuses.clear();
            this.activeSections.clear();
            this.activeReviewers.clear();
            this.workingSet = indices;
            this.setFilter = { type: 'random', query: 'random' };
            this.goToSetIndex(0);
        } catch (err) {
            console.error('Random navigation error:', err);
        }
    },

    get canGoPrev() { return this.setIndex > 0; },
    get canGoNext() { return this.setIndex < this.workingSet.length - 1; },

    // ── PDF ──

    async loadPdf(submissionId) {
        const gen = _pdf.loadGen;
        this.clearPdfSearch();
        const searchInput = document.getElementById('pdf-search-input');
        if (searchInput) searchInput.value = '';
        this.pdfLoading = true;
        this.pdfPageInfo = 'Loading...';

        try {
            const cached = this.prefetchCache.get(submissionId);
            let doc;
            if (cached && cached.pdf) {
                doc = await pdfjsLib.getDocument({ data: await cached.pdf.arrayBuffer() }).promise;
            } else {
                doc = await pdfjsLib.getDocument({ url: this.api + '/articles/' + submissionId + '/pdf', withCredentials: true }).promise;
            }
            if (gen !== _pdf.loadGen) { doc.destroy(); return; }

            _pdf.doc = doc;
            _linkService.setDocument(doc);
            _viewer.setDocument(doc);

            // Fit to container width once pages are ready
            await new Promise(resolve => {
                _eventBus.on('pagesloaded', resolve, { once: true });
            });
            _viewer.currentScaleValue = 'page-width';

            // Wait for first page to render at correct scale
            await new Promise(resolve => {
                _eventBus.on('pagerendered', resolve, { once: true });
            });

            this.pdfLoading = false;
            this.pdfPageInfo = doc.numPages + ' page' + (doc.numPages !== 1 ? 's' : '');
        } catch (err) {
            if (gen !== _pdf.loadGen) return;
            this.pdfLoading = false;
            this.pdfPageInfo = 'PDF not available';
        }
    },

    // ── PDF Search ──

    togglePdfSearch() {
        this.pdfSearchOpen = !this.pdfSearchOpen;
        if (this.pdfSearchOpen) {
            this.$nextTick(() => {
                const input = document.getElementById('pdf-search-input');
                if (input) { input.focus(); input.select(); }
            });
        } else {
            this.clearPdfSearch();
            const input = document.getElementById('pdf-search-input');
            if (input) input.value = '';
        }
    },

    pdfSearch(query) {
        this.pdfSearchQuery = query;
        if (!query || query.length < 2) {
            _eventBus.dispatch('findbarclose', {});
            return;
        }
        // Dispatch through pdf.js find controller — highlights rendered by TextHighlighter
        _eventBus.dispatch('find', {
            source: this,
            type: '',
            query,
            caseSensitive: false,
            entireWord: false,
            highlightAll: true,
            findPrevious: false,
            matchDiacritics: false,
        });
    },

    pdfSearchNext() {
        if (!this.pdfSearchQuery) return;
        _eventBus.dispatch('find', {
            source: this,
            type: 'again',
            query: this.pdfSearchQuery,
            caseSensitive: false,
            entireWord: false,
            highlightAll: true,
            findPrevious: false,
            matchDiacritics: false,
        });
    },

    pdfSearchPrev() {
        if (!this.pdfSearchQuery) return;
        _eventBus.dispatch('find', {
            source: this,
            type: 'again',
            query: this.pdfSearchQuery,
            caseSensitive: false,
            entireWord: false,
            highlightAll: true,
            findPrevious: true,
            matchDiacritics: false,
        });
    },

    clearPdfSearch() {
        this.pdfSearchQuery = '';
        _eventBus.dispatch('findbarclose', {});
    },


    get pdfSearchInfo() {
        void this._findTick; // reactive dependency
        if (!this.pdfSearchQuery || this.pdfSearchQuery.length < 2) return '';
        const pageMatches = _findController.pageMatches || [];
        const total = pageMatches.reduce((sum, m) => sum + (m?.length || 0), 0);
        if (total === 0) return 'No matches';
        const selected = _findController.selected;
        let current = 0;
        if (selected && selected.matchIdx !== -1) {
            for (let i = 0; i < selected.pageIdx; i++) {
                current += pageMatches[i]?.length || 0;
            }
            current += selected.matchIdx + 1;
        }
        return (current || 1) + ' / ' + total;
    },

    // ── HTML + Classification ──

    async loadHtml(submissionId) {
        const gen = _pdf.loadGen;
        this.htmlLoading = true;
        this.htmlContent = '';
        try {
            const cached = this.prefetchCache.get(submissionId);
            let html;
            if (cached && cached.html) {
                html = cached.html;
            } else {
                const res = await fetch(this.api + '/articles/' + submissionId + '/html', { credentials: 'same-origin' });
                if (gen !== _pdf.loadGen) return;
                if (!res.ok) { this.htmlLoading = false; return; }
                html = await res.text();
            }
            if (gen !== _pdf.loadGen) return;
            this.htmlContent = html.replace(/<script[\s\S]*?<\/script>/gi, '');
            this.isContentFiltered = html.includes('AUTO-EXTRACTED:') || html.includes('data-content-filtered');
            this.htmlLoading = false;
        } catch (err) {
            if (gen !== _pdf.loadGen) return;
            this.htmlLoading = false;
        }
    },

    async loadClassification(submissionId) {
        const gen = _pdf.loadGen;
        this.classification = null;
        try {
            const cached = this.prefetchCache.get(submissionId);
            let data;
            if (cached && cached.classification) {
                data = cached.classification;
            } else {
                const res = await fetch(this.api + '/articles/' + submissionId + '/classification', { credentials: 'same-origin' });
                if (gen !== _pdf.loadGen) return;
                if (!res.ok) throw new Error('Failed to load classification');
                data = await res.json();
            }
            if (gen !== _pdf.loadGen) return;
            this.classification = data;
        } catch (err) {
            if (gen !== _pdf.loadGen) return;
        }
    },

    get hasClassification() {
        if (!this.classification) return false;
        const c = this.classification;
        return (c.references || []).length > 0;
    },

    get classificationGroups() {
        if (!this.classification) return [];
        const c = this.classification;
        const groups = [];

        // References — full list (not in HTML galley, from citations table)
        const refs = c.references || [];
        if (refs.length > 0) {
            const doiCount = refs.filter(r => r.doi).length;
            const label = doiCount > 0 ? `References (${refs.length}, ${doiCount} DOIs)` : `References (${refs.length})`;
            groups.push({ label, cls: 'ac-pill-reference', count: refs.length, items: refs });
        }

        return groups;
    },

    // ── Reviews ──

    async submitReview(decision) {
        if (this.currentIndex < 0 || this.submitting) return;
        this.submitting = true;
        const a = this.articles[this.currentIndex];
        const comment = decision === 'needs_fix' ? this.rejectComment.trim() : '';

        if (decision === 'needs_fix' && !comment) {
            this.submitting = false;
            return;
        }

        try {
            const res = await fetch(this.api + '/reviews', {
                method: 'POST', credentials: 'same-origin',
                headers: { 'Content-Type': 'application/json', 'X-Csrf-Token': this.csrfToken },
                body: JSON.stringify({ submissionId: a.submission_id, decision, comment }),
            });
            if (!res.ok) { this.submitting = false; return; }
            const data = await res.json();

            a.status = decision;
            a.reviewer = 'you';
            a.reviewed_at = new Date().toISOString();
            a.comment = comment || null;
            this.recalculateCounts();

            if (decision === 'approved') {
                this.approveLabel = 'Approved ✓';
                this.rejectComment = '';

                // Confetti burst from the Approve button
                if (typeof confetti !== 'undefined') {
                    const btn = [...document.querySelectorAll('.ac-btn-approve')].find(b => b.offsetParent !== null);
                    if (btn) {
                        const rect = btn.getBoundingClientRect();
                        const x = (rect.left + rect.width / 2) / window.innerWidth;
                        const y = (rect.top + rect.height / 2) / window.innerHeight;
                        const opts = {
                            origin: { x, y },
                            scalar: 1.4,
                            ticks: 300,
                        };
                        confetti({ ...opts, count: 150, spread: 160, startVelocity: 55, decay: 0.91 });
                        confetti({ ...opts, count: 80, spread: 120, startVelocity: 70, decay: 0.88, drift: -1 });
                        confetti({ ...opts, count: 80, spread: 120, startVelocity: 70, decay: 0.88, drift: 1 });
                    }
                }
            } else {
                // Flash confirmation, stay on article, keep comment visible
                this.reportSaved = true;
                setTimeout(() => { this.reportSaved = false; }, 2000);
                this.rejectComment = comment;
            }
        } catch (err) {
            console.error('Review submission error:', err);
        } finally {
            this.submitting = false;
        }
    },

    approve() { this.submitReview('approved'); },

    submitFix() {
        if (this.rejectComment.trim()) this.submitReview('needs_fix');
    },

    submitDefer() {
        if (this.rejectComment.trim()) this.submitReview('deferred');
    },

    recalculateCounts() {
        const c = { total: this.articles.length, approved: 0, needs_fix: 0, recheck: 0, deferred: 0, unreviewed: 0, invalidated: 0 };
        this.articles.forEach(a => {
            if (c[a.status] !== undefined) c[a.status]++;
            else c.unreviewed++;
        });
        this.counts = c;
    },

    // ── Filtering ──

    // Articles matching all filters EXCEPT issue (so issue dropdown shows counts within current status/section/search context)
    _matchesContentFilter(a) {
        if (this.showOnlyContentFiltered && !a.content_filtered) return false;
        return true;
    },

    _matchesReviewerFilter(a) {
        if (this.activeReviewers.size === 0) return true;
        const isMine = a.reviewer === 'you' || a.reviewer === this.currentUsername;
        const byMe = this.activeReviewers.has('me');
        const byOthers = this.activeReviewers.has('others');
        if (byMe && !byOthers && !isMine) return false;
        if (byOthers && !byMe && (isMine || !a.reviewer)) return false;
        if (byMe && byOthers && !a.reviewer) return false;
        return true;
    },

    _matchesSearch(a) {
        const q = this.searchQuery.toLowerCase();
        if (!q) return true;
        const isNumeric = /^\d+$/.test(q);
        if (isNumeric) return String(a.submission_id) === q;
        return a.title.toLowerCase().includes(q)
            || a.authors.some(auth => auth.toLowerCase().includes(q))
            || (a.section || '').toLowerCase().includes(q)
            || String(a.submission_id) === q;
    },

    // Core filter — applies all active filters, with optional exclusion for pill counts.
    // Each pill group needs counts from "all other filters except mine" to show
    // how many articles would match if that pill were toggled.
    _filterArticles({ skipIssue, skipSections, skipReviewers } = {}) {
        const issue = this.issueFilter;
        return this.articles.filter(a => {
            if (!this._matchesContentFilter(a)) return false;
            if (!skipIssue && issue && (a.volume + '.' + a.number) !== issue) return false;
            if (this.activeStatuses.size > 0 && !this.activeStatuses.has(a.status)) return false;
            if (!skipSections && this.activeSections.size > 0 && !this.activeSections.has(a.section)) return false;
            if (!skipReviewers && !this._matchesReviewerFilter(a)) return false;
            if (!this._matchesSearch(a)) return false;
            return true;
        });
    },

    get allIssues() {
        const base = this._filterArticles({ skipIssue: true });
        const issues = {};
        base.forEach(a => {
            const key = a.volume + '.' + a.number;
            issues[key] = (issues[key] || 0) + 1;
        });
        return Object.entries(issues)
            .sort((a, b) => {
                const [av, an] = a[0].split('.').map(Number);
                const [bv, bn] = b[0].split('.').map(Number);
                return bv - av || bn - an;
            })
            .map(([k, c]) => ({ key: k, count: c }));
    },

    get contentFilteredCount() {
        return this.articles.filter(a => a.content_filtered).length;
    },

    get statusPills() {
        const base = this._filterArticles();
        const counts = { approved: 0, needs_fix: 0, recheck: 0, deferred: 0, unreviewed: 0 };
        base.forEach(a => { if (counts[a.status] !== undefined) counts[a.status]++; });
        return [
            { key: 'approved', label: '\u2713 Approved', count: counts.approved },
            { key: 'needs_fix', label: '\u26A0 Problem', count: counts.needs_fix },
            { key: 'recheck', label: '\u21BB Recheck', count: counts.recheck },
            { key: 'deferred', label: '\u23F8 Deferred', count: counts.deferred },
            { key: 'unreviewed', label: '\u00B7 Unchecked', count: counts.unreviewed },
        ];
    },

    get sectionPills() {
        const base = this._filterArticles({ skipSections: true });
        const counts = {};
        base.forEach(a => { if (a.section) counts[a.section] = (counts[a.section] || 0) + 1; });
        return Object.entries(counts)
            .sort((a, b) => b[1] - a[1])
            .map(([k, c]) => ({ key: k, label: k, count: c }));
    },

    get reviewerPills() {
        const base = this._filterArticles({ skipReviewers: true });
        let byMe = 0, byOthers = 0;
        base.forEach(a => {
            if (!a.reviewer) return;
            if (a.reviewer === 'you' || a.reviewer === this.currentUsername) byMe++;
            else byOthers++;
        });
        return [
            { key: 'me', label: 'By me', count: byMe },
            { key: 'others', label: 'By others', count: byOthers },
        ].filter(p => p.count > 0);
    },

    toggleStatus(key) {
        if (this.activeStatuses.has(key)) this.activeStatuses.delete(key);
        else this.activeStatuses.add(key);
        this.refilter();
    },

    toggleSection(key) {
        if (this.activeSections.has(key)) this.activeSections.delete(key);
        else this.activeSections.add(key);
        this.refilter();
    },

    isStatusActive(key) { return this.activeStatuses.has(key); },
    isSectionActive(key) { return this.activeSections.has(key); },

    toggleReviewer(key) {
        if (this.activeReviewers.has(key)) this.activeReviewers.delete(key);
        else this.activeReviewers.add(key);
        this.refilter();
    },
    isReviewerActive(key) { return this.activeReviewers.has(key); },

    _buildWorkingSet() {
        // Build working set from current filters without triggering navigation.
        // Used during init to avoid premature article loads.
        const filtered = this._filterArticles();
        const indexSet = new Set(filtered.map(a => this.articles.indexOf(a)));
        this.workingSet = [];
        this.articles.forEach((a, i) => { if (indexSet.has(i)) this.workingSet.push(i); });
    },

    refilter() {
        this._buildWorkingSet();

        this.setFilter = this.hasFilters ? { type: 'filters' } : null;
        this.setIndex = this.workingSet.indexOf(this.currentIndex);
        if (this.setIndex < 0 && this.workingSet.length > 0) {
            this.goToSetIndex(0);
        }
        this.updateUrl();
    },

    clearFilters() {
        this.searchQuery = '';
        this.issueFilter = '';
        this.activeStatuses.clear();
        this.activeSections.clear();
        this.activeReviewers.clear();
        this.showOnlyContentFiltered = false;
        this.refilter();
    },

    get hasFilters() {
        return this.searchQuery || this.issueFilter || this.activeStatuses.size > 0 || this.activeSections.size > 0 || this.activeReviewers.size > 0 || this.showOnlyContentFiltered || (this.setFilter && this.setFilter.type === 'random');
    },

    // ── Working set article list ──

    get workingSetArticles() {
        return this.workingSet.map((artIdx, si) => {
            const a = this.articles[artIdx];
            return {
                si,
                artIdx,
                num: si + 1,
                icon: a.status === 'approved' ? '\u2713' : a.status === 'needs_fix' ? '\u26A0' : a.status === 'recheck' ? '\u21BB' : a.status === 'deferred' ? '\u23F8' : '\u00B7',
                statusCls: 'ac-drawer-item-status ac-drawer-item-status-' + a.status,
                title: a.volume + '.' + a.number + ' (' + a.year + ') ' + a.title + ' [id: ' + a.submission_id + ']',
                active: si === this.setIndex,
            };
        });
    },

    selectArticle(si) {
        this.drawerOpen = false;
        this.goToSetIndex(si);
    },

    // ── URL management ──

    updateUrl() {
        const url = new URL(window.location);
        // Clear legacy params
        url.searchParams.delete('set');
        url.searchParams.delete('pos');

        // Serialize all active filters
        const setOrDelete = (key, val) => val ? url.searchParams.set(key, val) : url.searchParams.delete(key);

        setOrDelete('issue', this.issueFilter);
        setOrDelete('status', this.activeStatuses.size > 0 ? [...this.activeStatuses].sort().join(',') : '');
        setOrDelete('section', this.activeSections.size > 0 ? [...this.activeSections].sort().join(',') : '');
        setOrDelete('reviewer', this.activeReviewers.size > 0 ? [...this.activeReviewers].sort().join(',') : '');
        setOrDelete('q', this.searchQuery);

        // Always store current article ID for direct linking
        if (this.currentIndex >= 0) url.searchParams.set('id', this.articles[this.currentIndex].submission_id);
        else url.searchParams.delete('id');

        history.replaceState(null, '', url);
    },

    // ── Keyboard shortcuts ──

    bindKeys() {
        document.addEventListener('keydown', (e) => {
            // Ctrl+F / Cmd+F — open PDF search
            if ((e.ctrlKey || e.metaKey) && e.key === 'f') {
                e.preventDefault();
                if (!this.pdfSearchOpen) this.pdfSearchOpen = true;
                this.$nextTick(() => {
                    const input = document.getElementById('pdf-search-input');
                    if (input) { input.focus(); input.select(); }
                });
                return;
            }

            // PDF search input handlers
            if (e.target.id === 'pdf-search-input') {
                if (e.key === 'Escape') { this.clearPdfSearch(); e.target.value = ''; this.pdfSearchOpen = false; e.target.blur(); }
                if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); this.pdfSearchNext(); }
                if (e.key === 'Enter' && e.shiftKey) { e.preventDefault(); this.pdfSearchPrev(); }
                return;
            }

            if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') {
                if (e.key === 'Escape') e.target.blur();
                if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) { e.preventDefault(); this.submitFix(); }
                return;
            }

            // Dismiss overlays (highest z-index first)
            if (this.showGuide) {
                this.dismissGuide();
                return;
            }
            if (this.showShortcuts) {
                this.showShortcuts = false;
                return;
            }
            if (this.drawerOpen && e.key === 'Escape') {
                this.drawerOpen = false;
                return;
            }

            switch (e.key) {
                case 'ArrowLeft': e.preventDefault(); this.navigate(-1); break;
                case 'ArrowRight': e.preventDefault(); this.navigate(1); break;
                case 'a': case 'A': e.preventDefault(); this.approve(); break;
                case '?': e.preventDefault(); this.showShortcuts = true; break;
            }
        });
    },

    showGuide: false,
    showShortcuts: false,
    drawerOpen: false,
    drawerIntro: false,
    activePane: 'pdf',

    dismissGuide() {
        this.showGuide = false;
        localStorage.setItem('ac-help-seen', '1');
        if (this.shouldIntroduceDrawer()) {
            this.drawerIntro = true;
            this.drawerOpen = true;
            setTimeout(() => this.$nextTick(() => {
                this.drawerOpen = false;
                this.drawerIntro = false;
            }), 1500);
        }
    },

    /** Check if drawer intro should play (tablet/phone only, every load, once per init). */
    shouldIntroduceDrawer() {
        if (window.innerWidth > 1024) return false;
        if (this.drawerIntro) return false; // already in progress
        return true;
    },

    // ── Prefetch ──

    async prefetchNearby(index) {
        this._prefetchController = new AbortController();
        const signal = this._prefetchController.signal;

        // Collect IDs to prefetch (next 1 article only — keep concurrency low)
        const ids = [];
        for (const si of [index + 1, index - 1]) {
            if (si >= 0 && si < this.workingSet.length) {
                const id = this.articles[this.workingSet[si]].submission_id;
                if (!this.prefetchCache.has(id)) ids.push(id);
            }
        }

        // Prefetch sequentially — one article at a time to avoid saturating
        // Apache workers (~10 under Rosetta). Each article = 3 requests.
        for (const id of ids) {
            if (signal.aborted) return;
            this.prefetchCache.set(id, { pdf: null, html: null, classification: null });
            try {
                const [pdfRes, htmlRes, classRes] = await Promise.all([
                    fetch(this.api + '/articles/' + id + '/pdf', { credentials: 'same-origin', signal }),
                    fetch(this.api + '/articles/' + id + '/html', { credentials: 'same-origin', signal }),
                    fetch(this.api + '/articles/' + id + '/classification', { credentials: 'same-origin', signal }),
                ]);
                const entry = this.prefetchCache.get(id);
                if (!entry) continue;
                entry.pdf = pdfRes.ok ? await pdfRes.blob() : null;
                entry.html = htmlRes.ok ? await htmlRes.text() : null;
                entry.classification = classRes.ok ? await classRes.json() : null;
            } catch (err) {
                // Aborted or network error — remove incomplete cache entry
                this.prefetchCache.delete(id);
            }
        }

        // Evict far entries
        for (const [id] of this.prefetchCache) {
            const ai = this.articles.findIndex(a => a.submission_id === id);
            if (ai >= 0 && Math.abs(this.workingSet.indexOf(ai) - index) > 4) this.prefetchCache.delete(id);
        }
    },

    async prefetchRandomTarget() {
        // Pre-warm one random target — just fetch the nav endpoint,
        // don't prefetch article data (would saturate workers)
        try {
            await fetch(this.api + '/nav/random-unreviewed', { credentials: 'same-origin' });
        } catch (err) {}
    },

}));

Alpine.start();
