<script>
	import { onMount } from 'svelte';

	// ═══ STATE (Svelte 5 Runes) ═══
	let rawData = $state({ forums_groups: {}, markets: {}, telegram_links: {}, meta: {} });
	let currentTab = $state('all_sectors'); // 'all_sectors', 'forums_groups', 'markets', 'telegram_links'
	let currentFilter = $state('all');     // 'all', 'has-online', 'all-offline'
	let currentSort = $state('name-asc');
	let currentSourceFilter = $state('all');
	let searchQuery = $state('');

	let scraperState = $state({
		last_scrape: null,
		next_scrape: null,
		is_running: false,
		interval_minutes: 1440,
		last_error: null,
		scrape_count: 0
	});

	let recentLogs = $state([]);
	let expandedKeys = $state({});
	let scanStatuses = $state({}); // { url: 'queued' | 'processing' }

	// Modals & Menu Popups
	let showSettingsDropdown = $state(false);
	let showScreenshotModal = $state(false);
	let modalImgSrc = $state('');
	let modalCaptionText = $state('');
	let isLightTheme = $state(true);
	let isLogsCollapsed = $state(false);
	let isScanningAll = $state(false);
	let cacheBuster = $state(Date.now());
	let screenshotState = $state({ running: false, queue_size: 0 });

	/** @type {HTMLDivElement | null} */
	let logsTerminal = $state(null);

	// ═══ DERIVED VALUES (Svelte 5 Runes) ═══
	// Map dictionary key into entity object properties to fix "undefined key" table rendering bugs
	let allForumsGroups = $derived(
		Object.entries(rawData.forums_groups || {}).map(([key, val]) => ({ ...val, key }))
	);
	let allMarkets = $derived(
		Object.entries(rawData.markets || {}).map(([key, val]) => ({ ...val, key }))
	);
	let allTelegramLinks = $derived(
		Object.entries(rawData.telegram_links || {}).map(([key, val]) => ({ ...val, key }))
	);

	// Top Counters
	let countForumsGroups = $derived(allForumsGroups.length);
	let countMarkets = $derived(allMarkets.length);
	let countTelegram = $derived(allTelegramLinks.length);

	let countUrls = $derived(
		allForumsGroups.reduce((acc, e) => acc + (e.urls?.length || 0), 0) +
		allMarkets.reduce((acc, e) => acc + (e.urls?.length || 0), 0) +
		allTelegramLinks.reduce((acc, e) => acc + (e.urls?.length || 0), 0)
	);

	let countOnline = $derived(
		allForumsGroups.reduce((acc, e) => acc + (e.online_count || 0), 0) +
		allMarkets.reduce((acc, e) => acc + (e.online_count || 0), 0) +
		allTelegramLinks.reduce((acc, e) => acc + (e.online_count || 0), 0)
	);

	let countOffline = $derived(
		allForumsGroups.reduce((acc, e) => acc + (e.offline_count || 0), 0) +
		allMarkets.reduce((acc, e) => acc + (e.offline_count || 0), 0) +
		allTelegramLinks.reduce((acc, e) => acc + (e.offline_count || 0), 0)
	);

	// Dynamic checklist of discovered feed sources
	let discoveredSources = $derived(
		Array.from(new Set(
			[...allForumsGroups, ...allMarkets, ...allTelegramLinks].flatMap(e => e.sources || [])
		)).sort()
	);

	// Dynamic tab counts (filtered based on search/status/source options)
	let tabAllCount = $derived(
		allForumsGroups.length + allMarkets.length
	);

	// Main Filtered Entity List
	let filteredEntities = $derived.by(() => {
		let list = [];
		if (currentTab === 'all_sectors') {
			list = [...allForumsGroups, ...allMarkets];
		} else if (currentTab === 'forums_groups') {
			list = allForumsGroups;
		} else if (currentTab === 'markets') {
			list = allMarkets;
		} else if (currentTab === 'telegram_links') {
			list = allTelegramLinks;
		}

		// Ensure list elements are valid and safe to sort
		list = list.filter(e => e && e.name);

		// Apply Status Filter
		if (currentFilter === 'has-online') {
			list = list.filter(e => e.online_count > 0);
		} else if (currentFilter === 'all-offline') {
			list = list.filter(e => e.online_count === 0 && e.offline_count > 0);
		}

		// Apply Feed Source Filter
		if (currentSourceFilter !== 'all') {
			list = list.filter(e => e.sources && e.sources.includes(currentSourceFilter));
		}

		// Apply Search Query
		if (searchQuery.trim()) {
			const q = searchQuery.toLowerCase().trim();
			list = list.filter(e => {
				const matchesName = (e.name || '').toLowerCase().includes(q);
				const matchesUrl = e.urls && e.urls.some(u => u.url && u.url.toLowerCase().includes(q));
				const matchesSource = e.sources && e.sources.some(s => s.toLowerCase().includes(q));
				return matchesName || matchesUrl || matchesSource;
			});
		}

		// Apply Sorting
		if (currentSort === 'name-asc') {
			list.sort((a, b) => (a.name || '').localeCompare(b.name || ''));
		} else if (currentSort === 'name-desc') {
			list.sort((a, b) => (b.name || '').localeCompare(a.name || ''));
		} else if (currentSort === 'urls-desc') {
			list.sort((a, b) => (b.urls?.length || 0) - (a.urls?.length || 0));
		} else if (currentSort === 'online-desc') {
			list.sort((a, b) => (b.online_count || 0) - (a.online_count || 0));
		}

		return list;
	});

	// Total link match counters
	let totalLinkMatches = $derived(
		filteredEntities.reduce((acc, e) => acc + (e.urls?.length || 0), 0)
	);

	// ═══ API CALLS ═══
	async function loadData() {
		try {
			const res = await fetch('/api/data');
			if (res.ok) {
				rawData = await res.json();
				cacheBuster = Date.now();
			}
		} catch (err) {
			console.error('Failed to load API data:', err);
		}
	}

	async function loadScraperStatus() {
		try {
			const res = await fetch('/api/scraper/status');
			if (res.ok) {
				scraperState = await res.json();
			}
		} catch (err) {
			console.error('Failed to load scraper status:', err);
		}
	}

	async function fetchLogs() {
		try {
			const res = await fetch('/api/scraper/logs');
			if (res.ok) {
				const data = await res.json();
				recentLogs = data.map(item => `${item.time} [${item.level}] ${item.message}`);
			}
		} catch (err) {
			console.error('Failed to fetch logs:', err);
		}
	}

	async function triggerManualScrape() {
		try {
			await fetch('/api/scraper/run', { method: 'POST' });
			await loadScraperStatus();
			await fetchLogs();
		} catch (err) {
			console.error('Failed to trigger scrape:', err);
		}
	}

	async function resetScanData() {
		if (!confirm('Are you sure you want to reset all scanned statuses and delete cached screenshots?')) return;
		try {
			const res = await fetch('/api/scan/reset', { method: 'POST' });
			const data = await res.json();
			if (data.status === 'success') {
				await loadData();
				await loadScreenshotStatus();
			}
		} catch (err) {
			console.error('Failed to reset scan data:', err);
		}
	}

	async function updateConfigInterval(event) {
		const val = parseInt(event.target.value);
		try {
			const res = await fetch('/api/config', {
				method: 'POST',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify({ interval_minutes: val })
			});
			if (res.ok) {
				const json = await res.json();
				scraperState.interval_minutes = json.interval_minutes;
				showSettingsDropdown = false;
			}
		} catch (err) {
			console.error('Failed to update interval:', err);
		}
	}

	async function triggerScan(entityKey, url) {
		scanStatuses = { ...scanStatuses, [url]: 'queued' };
		try {
			const resp = await fetch('/api/screenshot/check', {
				method: 'POST',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify({ entity_key: entityKey, url: url })
			});
			if (resp.ok) {
				const res = await resp.json();
				if (res.status === 'queued') {
					scanStatuses = { ...scanStatuses, [url]: 'processing' };
					
					let checkCount = 0;
					const checkInterval = setInterval(async () => {
						checkCount++;
						await loadData();
						
						// Fetch matching item to see if screenshot has populated
						const item = [...allForumsGroups, ...allMarkets, ...allTelegramLinks].find(x => x.key === entityKey);
						if (item) {
							const u = item.urls.find(link => link.url === url);
							if (u && (u.screenshot || checkCount >= 12)) {
								clearInterval(checkInterval);
								const copy = { ...scanStatuses };
								delete copy[url];
								scanStatuses = copy;
							}
						}
					}, 2500);
				} else {
					const copy = { ...scanStatuses };
					delete copy[url];
					scanStatuses = copy;
				}
			} else {
				const copy = { ...scanStatuses };
				delete copy[url];
				scanStatuses = copy;
			}
		} catch (err) {
			console.error('Error triggering link scan:', err);
			const copy = { ...scanStatuses };
			delete copy[url];
		}
	}

	async function scanAllOnline() {
		try {
			isScanningAll = true;
			const res = await fetch('/api/screenshot/scan_all_online', { method: 'POST' });
			if (res.ok) {
				const data = await res.json();
				recentLogs = [...recentLogs, `[INFO] 📸 Queued ${data.count} online Onion URLs for screenshot checks.`];
			}
			isScanningAll = false;
		} catch (err) {
			console.error('Failed to trigger scan all online:', err);
			isScanningAll = false;
		}
	}

	async function updateAnalystNotes(entityKey, url, analystWorking, analystNotes) {
		try {
			const res = await fetch('/api/url/analyst_update', {
				method: 'POST',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify({
					entity_key: entityKey,
					url: url,
					analyst_working: analystWorking,
					analyst_notes: analystNotes
				})
			});
			if (res.ok) {
				// Silently reload data to synchronize local states
				await loadData();
			}
		} catch (err) {
			console.error('Failed to save analyst annotations:', err);
		}
	}

	function getLastScannedDate(ent) {
		const visits = (ent.urls || [])
			.map(u => u.last_visit)
			.filter(v => v && v !== 'N/A' && v.trim() !== '');
		if (visits.length === 0) {
			return 'Not scanned yet';
		}
		// Sort lexicographically descending so the latest date is first
		visits.sort((a, b) => b.localeCompare(a));
		return visits[0];
	}

	async function updateLocationStatus(entityKey, url, newStatus) {
		try {
			const res = await fetch('/api/url/status_update', {
				method: 'POST',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify({
					entity_key: entityKey,
					url: url,
					status: newStatus
				})
			});
			if (res.ok) {
				// Instantly reload database data to synchronize local states and active link counts
				await loadData();
				// Also append manual override details into live logs window
				recentLogs = [...recentLogs, `[SUCCESS] 💾 Analyst manually set ${url} to ${newStatus}.`].slice(-80);
			}
		} catch (err) {
			console.error('Failed to override status:', err);
		}
	}

	async function loadScreenshotStatus() {
		try {
			const res = await fetch('/api/screenshot/status');
			if (res.ok) {
				screenshotState = await res.json();
			}
		} catch (err) {
			console.error('Failed to load screenshot status:', err);
		}
	}

	async function pauseScanning() {
		try {
			const res = await fetch('/api/screenshot/pause', { method: 'POST' });
			if (res.ok) {
				screenshotState = await res.json();
				recentLogs = [...recentLogs, '[WARNING] 🛑 Screenshot scanning paused by analyst.'].slice(-80);
			}
		} catch (err) {
			console.error('Failed to pause scanning:', err);
		}
	}

	async function resumeScanning() {
		try {
			const res = await fetch('/api/screenshot/resume', { method: 'POST' });
			if (res.ok) {
				screenshotState = await res.json();
				recentLogs = [...recentLogs, '[SUCCESS] ▶️ Screenshot scanning resumed.'].slice(-80);
			}
		} catch (err) {
			console.error('Failed to resume scanning:', err);
		}
	}

	// ═══ LOCAL EVENTS ═══
	function toggleRow(key) {
		expandedKeys[key] = !expandedKeys[key];
	}

	function openScreenshot(img, caption) {
		modalImgSrc = `/static/screenshots/${img}`;
		modalCaptionText = caption;
		showScreenshotModal = true;
	}

	function toggleTheme() {
		isLightTheme = !isLightTheme;
		if (isLightTheme) {
			document.body.classList.add('light-theme');
			localStorage.setItem('theme', 'light');
		} else {
			document.body.classList.remove('light-theme');
			localStorage.setItem('theme', 'dark');
		}
	}

	function copyToClipboard(text, event) {
		navigator.clipboard.writeText(text).then(() => {
			const originalText = event.target.textContent;
			event.target.textContent = '✅';
			setTimeout(() => {
				event.target.textContent = originalText;
			}, 1200);
		}).catch(err => {
			console.error('Could not copy URL:', err);
		});
	}

	function getTabCount(tabName) {
		if (tabName === 'all_sectors') {
			return allForumsGroups.length + allMarkets.length + allTelegramLinks.length;
		} else if (tabName === 'forums_groups') {
			return allForumsGroups.length;
		} else if (tabName === 'markets') {
			return allMarkets.length;
		} else if (tabName === 'telegram_links') {
			return allTelegramLinks.length;
		}
		return 0;
	}

	function getStatusClass(ent) {
		if (ent.total_urls === 0) return 'offline';
		if (ent.online_count > 0 && ent.offline_count > 0) return 'mixed';
		if (ent.online_count > 0) return 'online';
		return 'offline';
	}

	function getStatusLabel(ent) {
		if (ent.total_urls === 0) return 'Offline';
		if (ent.online_count > 0 && ent.offline_count > 0) return 'Mixed';
		if (ent.online_count > 0) return 'Online';
		return 'Offline';
	}

	function clearSearch() {
		searchQuery = '';
	}

	// ═══ ONMOUNT POLLING ═══
	onMount(() => {
		loadData();
		loadScraperStatus();
		loadScreenshotStatus();
		fetchLogs();

		// Set default theme to Light UI
		const savedTheme = localStorage.getItem('theme');
		if (savedTheme === 'dark') {
			isLightTheme = false;
			document.body.classList.remove('light-theme');
		} else {
			isLightTheme = true;
			document.body.classList.add('light-theme');
		}

		// Polling intervals
		const logsInterval = setInterval(fetchLogs, 2500);
		const statusInterval = setInterval(loadScraperStatus, 5000);
		const screenshotInterval = setInterval(loadScreenshotStatus, 2500);

		return () => {
			clearInterval(logsInterval);
			clearInterval(statusInterval);
			clearInterval(screenshotInterval);
		};
	});

	// Auto scroll logs console
	$effect(() => {
		if (recentLogs && logsTerminal) {
			logsTerminal.scrollTop = logsTerminal.scrollHeight;
		}
	});
</script>

<div class="app-container">
	<!-- ═══ HEADER ═══ -->
	<header class="app-header">
		<div class="brand">
			<div class="brand-icon">🧅</div>
			<div class="brand-text">
				<h1>OnionExplorer</h1>
				<span class="tagline">Dark Web Threat Intelligence</span>
			</div>
		</div>
		<div class="header-right">
			<div class="search-box">
				<span class="search-icon">🔍</span>
				<input
					type="text"
					bind:value={searchQuery}
					placeholder="Search groups, markets, URLs..."
					autocomplete="off"
				/>
			</div>
			<div class="scrape-status">
				<span class="status-dot {scraperState.is_running ? 'online animate-pulse' : 'offline'}"></span>
				<span>{scraperState.is_running ? 'Scraping feeds...' : 'Idle'}</span>
			</div>
			<button
				class="scrape-all-btn"
				onclick={triggerManualScrape}
				disabled={scraperState.is_running}
				title="Run manual scrape across all darkweb sources"
			>
				⚡ Scrape All Sources
			</button>
			<button
				class="scrape-all-btn"
				onclick={scanAllOnline}
				disabled={isScanningAll}
				style="background: linear-gradient(135deg, #10b981 0%, #059669 100%);"
				title="Scan and verify all online onion links while you sleep"
			>
				📸 Scan All Online
			</button>
			{#if screenshotState.running}
				<button
					class="scrape-all-btn stop-scan-btn"
					onclick={pauseScanning}
					title="Stop/Pause the background screenshot worker"
					style="background: linear-gradient(135deg, #ef4444 0%, #dc2626 100%); font-weight: 750;"
				>
					🛑 Stop Scanning ({screenshotState.queue_size})
				</button>
			{:else}
				<button
					class="scrape-all-btn start-scan-btn"
					onclick={resumeScanning}
					title="Start/Resume the background screenshot worker"
					style="background: linear-gradient(135deg, #06b6d4 0%, #0891b2 100%); font-weight: 750;"
				>
					▶️ Start Scanning ({screenshotState.queue_size})
				</button>
			{/if}
			<button class="theme-toggle-btn" onclick={toggleTheme}>
				{isLightTheme ? '🌙 Dark UI' : '☀️ Light UI'}
			</button>
			<div class="settings-menu-container">
				<button class="settings-btn" onclick={() => showSettingsDropdown = !showSettingsDropdown}>
					⚙️ Config
				</button>
				{#if showSettingsDropdown}
					<div class="settings-dropdown">
						<div class="settings-group">
							<label for="intervalSelect">Scrape Every:</label>
							<select id="intervalSelect" value={scraperState.interval_minutes} onchange={updateConfigInterval}>
								<option value="720">12 Hours</option>
								<option value="1440">24 Hours</option>
								<option value="2880">48 Hours</option>
							</select>
						</div>
						<button class="settings-action-btn" onclick={triggerManualScrape} disabled={scraperState.is_running}>
							🔄 Scrape Now
						</button>
						<button class="settings-action-btn" style="background: rgba(239, 68, 68, 0.1); color: #ef4444; border-color: rgba(239, 68, 68, 0.3); margin-top: 6px;" onclick={resetScanData}>
							🧹 Reset Scan Data
						</button>
					</div>
				{/if}
			</div>
		</div>
	</header>

	<!-- ═══ STATS ROW ═══ -->
	<section class="stats-row">
		<div class="stat-card cyan">
			<div class="stat-label">Forums/Groups</div>
			<div class="stat-value">{countForumsGroups}</div>
		</div>
		<div class="stat-card purple">
			<div class="stat-label">Markets</div>
			<div class="stat-value">{countMarkets}</div>
		</div>
		<div class="stat-card orange">
			<div class="stat-label">Telegram</div>
			<div class="stat-value">{countTelegram}</div>
		</div>
		<div class="stat-card yellow">
			<div class="stat-label">Total URLs</div>
			<div class="stat-value">{countUrls}</div>
		</div>
		<div class="stat-card green">
			<div class="stat-label">Online</div>
			<div class="stat-value">{countOnline}</div>
		</div>
		<div class="stat-card red">
			<div class="stat-label">Offline</div>
			<div class="stat-value">{countOffline}</div>
		</div>
	</section>

	<!-- ═══ TABS & FILTERS BAR (HORIZONTAL RESTORED LAYOUT) ═══ -->
	<section class="controls-bar">
		<div class="tab-group">
			{#each ['all_sectors', 'forums_groups', 'markets', 'telegram_links'] as tabName}
				<button
					class="tab {currentTab === tabName ? 'active' : ''}"
					onclick={() => currentTab = tabName}
				>
					<span class="tab-icon">
						{#if tabName === 'all_sectors'}🌐{:else if tabName === 'forums_groups'}👥{:else if tabName === 'markets'}🏪{:else}📢{/if}
					</span>
					{tabName === 'all_sectors' ? 'All Sectors' : tabName === 'forums_groups' ? 'Forums & Groups' : tabName === 'markets' ? 'Markets' : 'Telegram Links'}
					<span class="tab-count">{getTabCount(tabName)}</span>
				</button>
			{/each}
		</div>

		<div class="filter-group">
			<!-- Link Status Filter Chips -->
			<div class="filter-chips">
				<button
					class="chip {currentFilter === 'all' ? 'active' : ''}"
					onclick={() => currentFilter = 'all'}
				>
					All
				</button>
				<button
					class="chip {currentFilter === 'has-online' ? 'active' : ''}"
					onclick={() => currentFilter = 'has-online'}
				>
					Has Online
				</button>
				<button
					class="chip {currentFilter === 'all-offline' ? 'active' : ''}"
					onclick={() => currentFilter = 'all-offline'}
				>
					All Offline
				</button>
			</div>

			<!-- Sort Settings Dropdown -->
			<div class="sort-dropdown">
				<select bind:value={currentSort}>
					<option value="name-asc">Name A→Z</option>
					<option value="name-desc">Name Z→A</option>
					<option value="urls-desc">Most URLs</option>
					<option value="online-desc">Most Online</option>
				</select>
			</div>

			<!-- Source Filter Dropdown -->
			<div class="sort-dropdown">
				<select bind:value={currentSourceFilter}>
					<option value="all">🔍 All Sources</option>
					{#each discoveredSources as src}
						<option value={src}>
							{#if src === 'rlive'}Ransomware.Live{:else if src === 'rlook'}RansomLook{:else if src === 'rfeed'}RansomFeed{:else if src === 'watchguard'}WatchGuard{:else}{src.replace('github:', '')}{/if}
						</option>
					{/each}
				</select>
			</div>

			<!-- Export Buttons -->
			<a
				href="/api/export/csv?sector={currentTab}&status={currentFilter}&source={currentSourceFilter}"
				class="chip export-btn"
			>
				📥 Export CSV
			</a>
			<a
				href="/api/export/markdown?sector={currentTab}&status={currentFilter}&source={currentSourceFilter}"
				class="chip export-btn md-btn"
			>
				📝 Export Markdown
			</a>
			<a
				href="/api/export/html?sector={currentTab}&status={currentFilter}&source={currentSourceFilter}"
				class="chip export-btn html-btn"
				style="background: linear-gradient(135deg, #0284c7 0%, #0369a1 100%); color: #ffffff; border-color: transparent;"
			>
				🖼️ Export HTML (with Screenshots)
			</a>
		</div>
	</section>

	<!-- ═══ SEARCH SUMMARY BANNER ═══ -->
	{#if searchQuery.trim()}
		<div class="search-summary-banner">
			<span class="banner-icon">🔍</span>
			<span class="banner-text">
				Found <strong>{filteredEntities.length}</strong> matching entries and <strong>{totalLinkMatches}</strong> links
			</span>
			<button class="clear-search-btn" onclick={clearSearch}>✕ Clear Search</button>
		</div>
	{/if}

	<!-- ═══ LIVE SCRAPER LOG CONSOLE ═══ -->
	<section class="log-card">
		<div class="log-card-header">
			<div class="log-card-title">
				<span class="pulse-icon {scraperState.is_running ? 'active' : ''}">●</span>
				<span>📟 Live Scraper Log Console</span>
			</div>
			<div class="log-card-actions">
				<button class="log-btn" onclick={() => recentLogs = []}>🗑️ Clear</button>
				<button class="log-btn" onclick={() => isLogsCollapsed = !isLogsCollapsed}>
					{isLogsCollapsed ? '🔼 Expand' : '🔽 Collapse'}
				</button>
			</div>
		</div>
		<div class="log-card-body {isLogsCollapsed ? 'collapsed' : ''}" bind:this={logsTerminal}>
			{#each recentLogs as line}
				{@const isErr = line.includes('[ERROR]')}
				{@const isWarn = line.includes('[WARNING]') || line.includes('⚠️')}
				{@const isSuccess = line.includes('Successfully') || line.includes('finished') || line.includes('✨') || line.includes('📸')}
				<div class="log-line {isErr ? 'error' : isWarn ? 'warning' : isSuccess ? 'success' : 'info'}">
					{line}
				</div>
			{/each}
		</div>
	</section>

	<!-- ═══ UNIFIED THREAT DIRECTORY TABLE ═══ -->
	<section class="table-card">
		<div class="table-container">
			<table class="unified-table">
				<thead>
					<tr>
						<th style="width: 40px;"></th>
						<th>Threat Actor / Entity</th>
						<th>Last Scanned</th>
						<th>Combined Status</th>
						<th>Sources</th>
						<th>Link Count</th>
					</tr>
				</thead>
				<tbody>
					{#each filteredEntities as ent (ent.key)}
						<!-- Threat Actor Accordion Header -->
						<!-- svelte-ignore a11y_click_events_have_key_events -->
						<!-- svelte-ignore a11y_no_noninteractive_element_interactions -->
						<tr
							class="entity-row {expandedKeys[ent.key] ? 'expanded' : ''}"
							onclick={() => toggleRow(ent.key)}
						>
							<td class="arrow-cell">
								<span class="expand-arrow">{expandedKeys[ent.key] ? '▼' : '▶'}</span>
							</td>
							<td class="name-cell">
								<strong>{ent.name}</strong>
							</td>
							<td class="last-scanned-cell" style="font-family: var(--font-mono); font-size: 0.8rem;">
								{getLastScannedDate(ent)}
							</td>
							<td>
								<div class="status-indicator {getStatusClass(ent)}">
									<span class="status-pip {getStatusClass(ent)}"></span>
									{getStatusLabel(ent)}
								</div>
							</td>
							<td>
								<div class="source-tags">
									{#each ent.sources || [] as s}
										<span class="source-tag {s.startsWith('github:') ? 'github' : s}">
											{#if s === 'rlive'}R.live{:else if s === 'rlook'}R.look{:else if s === 'rfeed'}R.feed{:else if s === 'watchguard'}WatchGuard{:else}{s.replace('github:', '')}{/if}
										</span>
									{/each}
								</div>
							</td>
							<td>
								<div class="links-counter-badge">
									<span class="online-count">{ent.online_count || 0}</span> / 
									<span class="total-count">{ent.urls?.length || 0}</span> active
								</div>
							</td>
						</tr>

						<!-- Expanded URLs Subtable Details Sheet -->
						{#if expandedKeys[ent.key]}
							<tr class="details-row visible" id="details-{ent.key}">
								<td colspan="6">
									<div class="details-content">
										<table class="nested-links-table">
											<thead>
												<tr>
													<th>Onion URL / Invite Link</th>
													<th>Status</th>
													<th>Discovered Sources</th>
													<th>Last Checked</th>
													<th style="width: 140px;">Screen Preview & Date</th>
													<th style="width: 220px;">Analyst Verification & Notes</th>
													<th style="width: 100px;">Actions</th>
												</tr>
											</thead>
											<tbody>
												{#each ent.urls as u}
													{@const isOnline = u.status === 'Online' || u.status === 'Up'}
													{@const isTelegram = u.url.includes('t.me') || u.url.includes('telegram.me') || ent.sector === 'telegram_links'}
													<tr>
														<td class="nested-url-cell">
															<span class="url-dot {isOnline ? 'online' : 'offline'}"></span>
															<a href={u.url} target="_blank" class="nested-link">{u.url}</a>
															<button
																class="copy-url-btn"
																onclick={(e) => copyToClipboard(u.url, e)}
																title="Copy URL"
															>
																📋
															</button>
														</td>
														<td>
															{#if isTelegram}
																<span class="status-indicator {isOnline ? 'online' : 'offline'}">
																	<span class="status-pip {isOnline ? 'online' : 'offline'}"></span>
																	{isOnline ? 'Up' : 'Down'}
																</span>
															{:else}
																<select
																	class="status-select-indicator {isOnline ? 'online' : 'offline'}"
																	value={u.status === 'Online' || u.status === 'Up' ? 'Online' : 'Offline'}
																	onchange={(e) => updateLocationStatus(ent.key, u.url, e.currentTarget.value)}
																>
																	<option value="Online">🟢 Up</option>
																	<option value="Offline">🔴 Down</option>
																</select>
															{/if}
														</td>
														<td>
															<div class="source-tags">
																{#each u.sources || [] as s}
																	<span class="source-tag {s.startsWith('github:') ? 'github' : s}">
																		{#if s === 'rlive'}R.live{:else if s === 'rlook'}R.look{:else if s === 'rfeed'}R.feed{:else if s === 'watchguard'}WatchGuard{:else}{s.replace('github:', '')}{/if}
																	</span>
																{/each}
															</div>
														</td>
														<td class="last-visit-cell">{u.last_visit || 'N/A'}</td>
														<td>
															{#if isTelegram}
																<span class="text-muted" style="font-size: 0.75rem; opacity: 0.6;">N/A (Telegram)</span>
															{:else if u.screenshot}
																<!-- svelte-ignore a11y_click_events_have_key_events -->
																<!-- svelte-ignore a11y_no_noninteractive_element_interactions -->
																<div
																	class="screenshot-thumb-container"
																	role="button"
																	tabindex="0"
																	onclick={() => openScreenshot(u.screenshot, `${ent.name}: ${u.url}`)}
																	onkeydown={(e) => { if (e.key === 'Enter' || e.key === ' ') openScreenshot(u.screenshot, `${ent.name}: ${u.url}`); }}
																>
																	<img
																		src="/static/screenshots/{u.screenshot}?t={cacheBuster}"
																		class="screenshot-thumb"
																		alt="Preview"
																	/>
																</div>
																<span class="screenshot-date">Saved: {u.last_visit || 'N/A'}</span>
															{:else}
																<div class="screenshot-thumb-container" style="cursor: default;">
																	<div class="screenshot-placeholder">No Preview</div>
																</div>
															{/if}
														</td>
														<td>
															{#if isTelegram}
																<span class="text-muted" style="font-size: 0.75rem; opacity: 0.6;">N/A (Telegram)</span>
															{:else}
																<div class="analyst-controls">
																	<label class="analyst-checkbox-label">
																		<input
																			type="checkbox"
																			checked={u.analyst_working}
																			onchange={(e) => updateAnalystNotes(ent.key, u.url, e.currentTarget.checked, u.analyst_notes)}
																		/>
																		<span>Verified Working</span>
																	</label>
																	<input
																		type="text"
																		value={u.analyst_notes}
																		placeholder="Notes (e.g. Captcha, DDOS)"
																		class="analyst-notes-input"
																		onchange={(e) => updateAnalystNotes(ent.key, u.url, u.analyst_working, e.currentTarget.value)}
																	/>
																</div>
															{/if}
														</td>
														<td>
															{#if isTelegram}
																<span class="text-muted" style="font-size: 0.75rem; opacity: 0.6;">N/A</span>
															{:else}
																<button
																	class="check-status-btn"
																	onclick={() => triggerScan(ent.key, u.url)}
																	disabled={scanStatuses[u.url] !== undefined}
																>
																	{#if scanStatuses[u.url] === 'queued'}
																		🔄 Queued...
																	{:else if scanStatuses[u.url] === 'processing'}
																		⏳ Processing...
																	{:else}
																		⚡ Scan
																	{/if}
																</button>
															{/if}
														</td>
													</tr>
												{/each}
											</tbody>
										</table>
									</div>
								</td>
							</tr>
						{/if}
					{:else}
						<tr>
							<td colspan="6" class="no-urls-placeholder" style="padding: 40px; text-align: center; color: var(--text-muted);">
								No threat directories or matching entries found.
							</td>
						</tr>
					{/each}
				</tbody>
			</table>
		</div>
	</section>
</div>

<!-- ═══ LIGHTBOX PREVIEW SCREENSHOT MODAL ═══ -->
{#if showScreenshotModal}
	<div
		class="modal"
		style="display: block;"
		role="button"
		tabindex="0"
		onclick={() => showScreenshotModal = false}
		onkeydown={(e) => { if (e.key === 'Escape' || e.key === 'Enter' || e.key === ' ') showScreenshotModal = false; }}
	>
		<span
			class="modal-close"
			role="button"
			tabindex="0"
			onclick={() => showScreenshotModal = false}
			onkeydown={(e) => { if (e.key === 'Enter' || e.key === ' ') showScreenshotModal = false; }}
		>
			&times;
		</span>
		<img
			class="modal-content"
			src={modalImgSrc}
			alt="Full Capture Preview"
			role="presentation"
			onclick={(e) => e.stopPropagation()}
		/>
		<div id="modalCaption">{modalCaptionText}</div>
	</div>
{/if}
