/**
 * Enhanced Audit Trail Functions
 * Advanced filtering, analytics, and export capabilities
 */

// Global variables for enhanced audit trail
window.auditTrailEnhanced = {
    currentQuery: {
        page: 1,
        page_size: 50,
        sort_by: "updated_at",
        sort_order: "desc"
    },
    totalPages: 0,
    totalCount: 0,
    analytics: null,
    isLoading: false
};

/**
 * Initialize enhanced audit trail functionality
 */
function initializeEnhancedAuditTrail() {
    console.log('Initializing enhanced audit trail...');
    
    // Setup event listeners for new controls
    setupEnhancedFilters();
    setupPaginationControls();
    setupExportControls();
    setupAnalyticsRefresh();
    
    // Load initial data
    loadAuditTrailData();
    loadAuditAnalytics();
}

/**
 * Setup enhanced filter controls
 */
function setupEnhancedFilters() {
    // Advanced filters toggle
    const advancedToggle = document.getElementById('advanced-filters-toggle');
    if (advancedToggle) {
        advancedToggle.addEventListener('click', toggleAdvancedFilters);
    }
    
    // Duration filters
    const minDurationInput = document.getElementById('audit-min-duration');
    const maxDurationInput = document.getElementById('audit-max-duration');
    
    if (minDurationInput) {
        minDurationInput.addEventListener('change', validateDurationFilter);
    }
    if (maxDurationInput) {
        maxDurationInput.addEventListener('change', validateDurationFilter);
    }
    
    // Error only checkbox
    const errorOnlyCheckbox = document.getElementById('audit-error-only');
    if (errorOnlyCheckbox) {
        errorOnlyCheckbox.addEventListener('change', applyEnhancedFilters);
    }
    
    // Sort controls
    const sortBySelect = document.getElementById('audit-sort-by');
    const sortOrderSelect = document.getElementById('audit-sort-order');
    
    if (sortBySelect) {
        sortBySelect.addEventListener('change', applyEnhancedFilters);
    }
    if (sortOrderSelect) {
        sortOrderSelect.addEventListener('change', applyEnhancedFilters);
    }
    
    // Real-time filter button
    const applyFiltersBtn = document.getElementById('apply-enhanced-filters-btn');
    if (applyFiltersBtn) {
        applyFiltersBtn.addEventListener('click', applyEnhancedFilters);
    }
}

/**
 * Setup pagination controls
 */
function setupPaginationControls() {
    const prevBtn = document.getElementById('audit-prev-page');
    const nextBtn = document.getElementById('audit-next-page');
    const pageInput = document.getElementById('audit-current-page');
    const pageSizeSelect = document.getElementById('audit-page-size');
    
    if (prevBtn) {
        prevBtn.addEventListener('click', () => navigateToPage('prev'));
    }
    if (nextBtn) {
        nextBtn.addEventListener('click', () => navigateToPage('next'));
    }
    if (pageInput) {
        pageInput.addEventListener('change', (e) => navigateToPage(parseInt(e.target.value)));
    }
    if (pageSizeSelect) {
        pageSizeSelect.addEventListener('change', changePageSize);
    }
}

/**
 * Setup export controls
 */
function setupExportControls() {
    const exportJsonBtn = document.getElementById('export-audit-json');
    const exportCsvBtn = document.getElementById('export-audit-csv');
    
    if (exportJsonBtn) {
        exportJsonBtn.addEventListener('click', () => exportAuditData('json'));
    }
    if (exportCsvBtn) {
        exportCsvBtn.addEventListener('click', () => exportAuditData('csv'));
    }
}

/**
 * Setup analytics refresh
 */
function setupAnalyticsRefresh() {
    const refreshBtn = document.getElementById('refresh-analytics-btn');
    if (refreshBtn) {
        refreshBtn.addEventListener('click', loadAuditAnalytics);
    }
    
    // Auto-refresh analytics every 5 minutes
    setInterval(loadAuditAnalytics, 5 * 60 * 1000);
}

/**
 * Toggle advanced filters panel
 */
function toggleAdvancedFilters() {
    const panel = document.getElementById('advanced-filters-panel');
    const toggle = document.getElementById('advanced-filters-toggle');
    
    if (panel && toggle) {
        const isVisible = !panel.classList.contains('hidden');
        
        if (isVisible) {
            panel.classList.add('hidden');
            toggle.textContent = 'Show Advanced Filters';
        } else {
            panel.classList.remove('hidden');
            toggle.textContent = 'Hide Advanced Filters';
        }
    }
}

/**
 * Validate duration filter inputs
 */
function validateDurationFilter() {
    const minInput = document.getElementById('audit-min-duration');
    const maxInput = document.getElementById('audit-max-duration');
    
    if (minInput && maxInput) {
        const min = parseFloat(minInput.value);
        const max = parseFloat(maxInput.value);
        
        if (min && max && min >= max) {
            showErrorMessage('Minimum duration must be less than maximum duration');
            maxInput.value = '';
        }
    }
}

/**
 * Apply enhanced filters and reload data
 */
async function applyEnhancedFilters() {
    if (window.auditTrailEnhanced.isLoading) {
        return;
    }
    
    // Build query from form inputs
    const query = buildQueryFromFilters();
    
    // Reset to first page when applying new filters
    query.page = 1;
    window.auditTrailEnhanced.currentQuery = query;
    
    await loadAuditTrailData();
}

/**
 * Build query object from filter form inputs
 */
function buildQueryFromFilters() {
    const query = {
        page: window.auditTrailEnhanced.currentQuery.page,
        page_size: window.auditTrailEnhanced.currentQuery.page_size,
        sort_by: window.auditTrailEnhanced.currentQuery.sort_by,
        sort_order: window.auditTrailEnhanced.currentQuery.sort_order,
        include_events: true
    };
    
    // Basic filters
    const ticker = document.getElementById('audit-search-ticker')?.value?.trim();
    const status = document.getElementById('audit-status-filter')?.value;
    const location = document.getElementById('audit-location-filter')?.value;
    const startDate = document.getElementById('audit-filter-start')?.value;
    const endDate = document.getElementById('audit-filter-end')?.value;
    
    if (ticker) query.ticker = ticker;
    if (status && status !== 'all') query.status = status;
    if (location && location !== 'all') query.location = location;
    if (startDate) query.start_time = startDate;
    if (endDate) query.end_time = endDate;
    
    // Advanced filters
    const errorOnly = document.getElementById('audit-error-only')?.checked;
    const minDuration = document.getElementById('audit-min-duration')?.value;
    const maxDuration = document.getElementById('audit-max-duration')?.value;
    const sortBy = document.getElementById('audit-sort-by')?.value;
    const sortOrder = document.getElementById('audit-sort-order')?.value;
    
    if (errorOnly) query.error_only = true;
    if (minDuration) query.min_duration = parseFloat(minDuration);
    if (maxDuration) query.max_duration = parseFloat(maxDuration);
    if (sortBy) query.sort_by = sortBy;
    if (sortOrder) query.sort_order = sortOrder;
    
    return query;
}

/**
 * Load audit trail data with current query
 */
async function loadAuditTrailData() {
    try {
        window.auditTrailEnhanced.isLoading = true;
        showLoadingIndicator(true);
        
        const response = await fetch('/admin/audit/query', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify(window.auditTrailEnhanced.currentQuery)
        });
        
        if (!response.ok) {
            throw new Error(`HTTP ${response.status}: ${response.statusText}`);
        }
        
        const data = await response.json();
        
        // Update global state
        window.auditTrailEnhanced.totalPages = data.total_pages;
        window.auditTrailEnhanced.totalCount = data.total_count;
        
        // Update UI
        updateAuditTrailDisplay(data);
        updatePaginationControls(data);
        updateFilterSummary(data);
        
    } catch (error) {
        console.error('Error loading audit trail data:', error);
        showErrorMessage(`Failed to load audit data: ${error.message}`);
    } finally {
        window.auditTrailEnhanced.isLoading = false;
        showLoadingIndicator(false);
    }
}

/**
 * Update audit trail display with new data
 */
function updateAuditTrailDisplay(data) {
    const container = document.getElementById('audit-log-container');
    const emptyMessage = document.getElementById('audit-log-empty-message');
    
    if (!container) return;
    
    // Clear existing entries
    container.innerHTML = '';
    
    if (data.entries && data.entries.length > 0) {
        // Hide empty message
        if (emptyMessage) {
            emptyMessage.style.display = 'none';
        }
        
        // Add new entries
        data.entries.forEach(entry => {
            const entryElement = createEnhancedAuditEntry(entry);
            container.appendChild(entryElement);
        });
        
        // Update statistics
        updateAuditStatistics();
        
    } else {
        // Show empty message
        if (emptyMessage) {
            emptyMessage.style.display = 'block';
            emptyMessage.textContent = 'No audit entries match the current filters.';
        }
    }
}

/**
 * Create enhanced audit entry element
 */
function createEnhancedAuditEntry(entry) {
    const entryDiv = document.createElement('div');
    entryDiv.className = `audit-entry bg-slate-800 rounded-lg p-3 border border-slate-700 mb-2`;
    entryDiv.setAttribute('data-signal-id', entry.signal_id);
    entryDiv.setAttribute('data-entry', JSON.stringify(entry));
    
    // Enhanced entry content with performance metrics
    const performanceMetrics = entry.performance_metrics || {};
    const tags = entry.tags || [];
    
    entryDiv.innerHTML = `
        <div class="flex justify-between items-start mb-2">
            <div class="flex items-center space-x-2">
                <span class="font-semibold text-teal-400">${entry.ticker}</span>
                <span class="text-xs text-slate-400">${entry.signal_id.substring(0, 8)}...</span>
                ${tags.map(tag => `<span class="bg-blue-600 text-white text-xs px-1 py-0.5 rounded">${tag}</span>`).join('')}
            </div>
            <div class="text-right text-xs text-slate-400">
                <div>${formatTimestamp(entry.timestamp)}</div>
                <div class="mt-1">
                    <span class="mr-2">⏱️ ${performanceMetrics.total_duration ? performanceMetrics.total_duration.toFixed(2) + 's' : 'N/A'}</span>
                    <span class="mr-2">🔄 ${entry.events_count || 0} events</span>
                    ${entry.error_count > 0 ? `<span class="text-red-400">⚠️ ${entry.error_count}</span>` : ''}
                </div>
            </div>
        </div>
        <div class="flex justify-between items-center mb-2">
            <span class="status-badge ${getStatusColorClass(entry.status)}">${entry.status_display || entry.status}</span>
            <span class="text-xs text-slate-400">📍 ${entry.location}</span>
        </div>
        <div class="text-sm text-slate-300 mb-2">${entry.details || 'No details available'}</div>
        <div class="flex justify-between items-center text-xs">
            <div class="flex space-x-4">
                ${performanceMetrics.processing_efficiency ? 
                    `<span>Efficiency: ${(performanceMetrics.processing_efficiency * 100).toFixed(1)}%</span>` : ''}
                ${entry.retry_count > 0 ? `<span class="text-yellow-400">Retries: ${entry.retry_count}</span>` : ''}
            </div>
            <div class="flex space-x-2">
                <button onclick="viewSignalDetails('${entry.signal_id}')" class="text-blue-400 hover:text-blue-300">View Details</button>
                <button onclick="addSignalTag('${entry.signal_id}')" class="text-green-400 hover:text-green-300">Add Tag</button>
            </div>
        </div>
    `;
    
    return entryDiv;
}

/**
 * Navigate to specific page
 */
async function navigateToPage(direction) {
    let newPage;
    
    if (direction === 'prev') {
        newPage = Math.max(1, window.auditTrailEnhanced.currentQuery.page - 1);
    } else if (direction === 'next') {
        newPage = Math.min(window.auditTrailEnhanced.totalPages, window.auditTrailEnhanced.currentQuery.page + 1);
    } else if (typeof direction === 'number') {
        newPage = Math.max(1, Math.min(window.auditTrailEnhanced.totalPages, direction));
    } else {
        return;
    }
    
    if (newPage === window.auditTrailEnhanced.currentQuery.page) {
        return; // No change needed
    }
    
    window.auditTrailEnhanced.currentQuery.page = newPage;
    await loadAuditTrailData();
}

/**
 * Change page size
 */
async function changePageSize() {
    const select = document.getElementById('audit-page-size');
    if (select) {
        window.auditTrailEnhanced.currentQuery.page_size = parseInt(select.value);
        window.auditTrailEnhanced.currentQuery.page = 1; // Reset to first page
        await loadAuditTrailData();
    }
}

/**
 * Update pagination controls
 */
function updatePaginationControls(data) {
    const pageInfo = document.getElementById('audit-page-info');
    const currentPageInput = document.getElementById('audit-current-page');
    const totalPagesSpan = document.getElementById('audit-total-pages');
    const prevBtn = document.getElementById('audit-prev-page');
    const nextBtn = document.getElementById('audit-next-page');
    
    if (pageInfo) {
        pageInfo.textContent = `Showing ${data.entries.length} of ${data.total_count} entries`;
    }
    
    if (currentPageInput) {
        currentPageInput.value = data.page;
        currentPageInput.max = data.total_pages;
    }
    
    if (totalPagesSpan) {
        totalPagesSpan.textContent = data.total_pages;
    }
    
    if (prevBtn) {
        prevBtn.disabled = data.page <= 1;
    }
    
    if (nextBtn) {
        nextBtn.disabled = data.page >= data.total_pages;
    }
}

/**
 * Update filter summary
 */
function updateFilterSummary(data) {
    const summary = document.getElementById('filter-summary');
    if (summary && data.filters_applied) {
        const filters = data.filters_applied;
        const filterParts = [];
        
        if (filters.ticker) filterParts.push(`Ticker: ${filters.ticker}`);
        if (filters.status) filterParts.push(`Status: ${filters.status}`);
        if (filters.location) filterParts.push(`Location: ${filters.location}`);
        if (filters.error_only) filterParts.push('Errors only');
        if (filters.start_time) filterParts.push(`From: ${new Date(filters.start_time).toLocaleDateString()}`);
        if (filters.end_time) filterParts.push(`To: ${new Date(filters.end_time).toLocaleDateString()}`);
        
        if (filterParts.length > 0) {
            summary.textContent = `Filters: ${filterParts.join(', ')}`;
            summary.style.display = 'block';
        } else {
            summary.style.display = 'none';
        }
    }
}

/**
 * Load audit analytics
 */
async function loadAuditAnalytics() {
    try {
        const response = await fetch('/admin/audit/analytics');
        if (!response.ok) {
            throw new Error(`HTTP ${response.status}: ${response.statusText}`);
        }
        
        const analytics = await response.json();
        window.auditTrailEnhanced.analytics = analytics;
        
        updateAnalyticsDisplay(analytics);
        
    } catch (error) {
        console.error('Error loading audit analytics:', error);
        showErrorMessage(`Failed to load analytics: ${error.message}`);
    }
}

/**
 * Update analytics display
 */
function updateAnalyticsDisplay(analytics) {
    // Update overview metrics
    updateAnalyticsOverview(analytics.overview);
    
    // Update charts with analytics data
    updateAnalyticsCharts(analytics);
    
    // Update error analysis
    updateErrorAnalysis(analytics.error_analysis);
}

/**
 * Export audit data
 */
async function exportAuditData(format) {
    try {
        const query = buildQueryFromFilters();
        const queryParams = new URLSearchParams({
            format: format,
            include_events: query.include_events ? 'true' : 'false'
        });
        
        if (query.start_time) queryParams.append('start_date', query.start_time);
        if (query.end_time) queryParams.append('end_date', query.end_time);
        
        const response = await fetch(`/admin/audit/export?${queryParams}`);
        
        if (!response.ok) {
            throw new Error(`HTTP ${response.status}: ${response.statusText}`);
        }
        
        if (format === 'csv') {
            // Download CSV file
            const blob = await response.blob();
            const url = window.URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = `audit_trail_${new Date().toISOString().split('T')[0]}.csv`;
            document.body.appendChild(a);
            a.click();
            window.URL.revokeObjectURL(url);
            document.body.removeChild(a);
        } else {
            // Download JSON file
            const data = await response.json();
            const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
            const url = window.URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = `audit_trail_${new Date().toISOString().split('T')[0]}.json`;
            document.body.appendChild(a);
            a.click();
            window.URL.revokeObjectURL(url);
            document.body.removeChild(a);
        }
        
        showSuccessMessage(`Audit data exported successfully as ${format.toUpperCase()}`);
        
    } catch (error) {
        console.error('Error exporting audit data:', error);
        showErrorMessage(`Failed to export data: ${error.message}`);
    }
}

/**
 * View detailed signal information
 */
async function viewSignalDetails(signalId) {
    try {
        const response = await fetch(`/admin/signal/${signalId}`);
        if (!response.ok) {
            throw new Error(`HTTP ${response.status}: ${response.statusText}`);
        }
        
        const data = await response.json();
        showSignalDetailsModal(data);
        
    } catch (error) {
        console.error('Error loading signal details:', error);
        showErrorMessage(`Failed to load signal details: ${error.message}`);
    }
}

/**
 * Add tag to signal
 */
async function addSignalTag(signalId) {
    const tag = prompt('Enter tag for this signal:');
    if (!tag || !tag.trim()) {
        return;
    }
    
    try {
        const response = await fetch(`/admin/audit/signal/${signalId}/tag`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({ tag: tag.trim() })
        });
        
        if (!response.ok) {
            throw new Error(`HTTP ${response.status}: ${response.statusText}`);
        }
        
        showSuccessMessage(`Tag "${tag}" added to signal`);
        await loadAuditTrailData(); // Refresh to show new tag
        
    } catch (error) {
        console.error('Error adding signal tag:', error);
        showErrorMessage(`Failed to add tag: ${error.message}`);
    }
}

/**
 * Show loading indicator
 */
function showLoadingIndicator(show) {
    const indicator = document.getElementById('audit-loading-indicator');
    if (indicator) {
        indicator.style.display = show ? 'block' : 'none';
    }
}

/**
 * Get status color class for styling
 */
function getStatusColorClass(status) {
    const statusColors = {
        'received': 'bg-blue-600',
        'processing': 'bg-yellow-600',
        'approved': 'bg-green-600',
        'rejected': 'bg-red-600',
        'forwarded_success': 'bg-green-700',
        'forwarded_timeout': 'bg-orange-600',
        'forwarded_http_error': 'bg-red-700',
        'forwarded_generic_error': 'bg-red-800',
        'discarded': 'bg-gray-600'
    };
    
    return statusColors[status] || 'bg-slate-600';
}

// Initialize on page load
document.addEventListener('DOMContentLoaded', function() {
    // Wait a bit for other initialization to complete
    setTimeout(initializeEnhancedAuditTrail, 1000);
});
