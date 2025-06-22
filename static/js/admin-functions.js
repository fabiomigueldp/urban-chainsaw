/**
 * Admin Panel Functions
 * Complementary functions for real-time WebSocket updates
 * 
 * This file contains all the missing functions that are called by the WebSocket handler
 * in admin.html but were not properly implemented. These functions handle real-time
 * updates of the admin interface.
 */

// Utility Functions
function safeUpdateTextContent(element, text) {
    if (element && typeof element.textContent !== 'undefined') {
        // Fix: Properly handle 0 values - only show N/A for null/undefined
        element.textContent = (text !== null && text !== undefined) ? text : 'N/A';
    }
}

// Alias for backward compatibility
const updateTextContent = safeUpdateTextContent;

function safeUpdateInnerHTML(element, html) {
    if (element && typeof element.innerHTML !== 'undefined') {
        element.innerHTML = html || '';
    }
}

function formatBytes(bytes) {
    if (bytes === 0) return '0 Bytes';
    const k = 1024;
    const sizes = ['Bytes', 'KB', 'MB', 'GB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
}

function formatPercentage(value) {
    return (parseFloat(value) || 0).toFixed(1) + '%';
}

function formatDuration(seconds) {
    const hours = Math.floor(seconds / 3600);
    const minutes = Math.floor((seconds % 3600) / 60);
    const secs = seconds % 60;
    
    if (hours > 0) {
        return `${hours}h ${minutes}m ${secs}s`;
    } else if (minutes > 0) {
        return `${minutes}m ${secs}s`;
    } else {
        return `${secs}s`;
    }
}

function formatTimestamp(timestamp) {
    if (!timestamp) return 'N/A';
    
    try {
        const date = new Date(timestamp);
        return date.toLocaleString('pt-BR', {
            year: 'numeric',
            month: '2-digit',
            day: '2-digit',
            hour: '2-digit',
            minute: '2-digit',
            second: '2-digit'
        });
    } catch (error) {
        console.error('Error formatting timestamp:', error);
        return timestamp.toString();
    }
}

// Main Update Functions

/**
 * Updates metrics display with real-time data
 * @param {Object} data - Metrics data from WebSocket
 */
function updateMetrics(data) {
    console.log('Updating metrics:', data);
    
    if (!data) return;
    
    try {
        // Update signal metrics - using correct IDs from HTML
        updateTextContent(document.getElementById('signals-received'), data.signals_processed || data.signals_received || 0);
        updateTextContent(document.getElementById('signals-approved'), data.signals_approved || 0);
        updateTextContent(document.getElementById('signals-rejected'), data.signals_rejected || 0);
        updateTextContent(document.getElementById('signals-forwarded-success'), data.signals_forwarded_success || data.forwarded_success || 0);
        updateTextContent(document.getElementById('signals-forwarded-error'), data.signals_forwarded_error || data.forwarded_error || 0);
          // Calculate and update success rate - CORRECTED: (Forwarded Success / Approved) * 100
        const forwardedSuccess = (data.signals_forwarded_success || data.forwarded_success || 0);
        const approved = (data.signals_approved || 0);
        const successRate = approved > 0 ? (forwardedSuccess / approved * 100).toFixed(1) : '0.0';
        updateTextContent(document.getElementById('success-rate'), `${successRate}%`);
        
        // Update queue sizes - using correct IDs
        updateTextContent(document.getElementById('processing-queue-size'), data.processing_queue_size || 0);
        updateTextContent(document.getElementById('approved-queue-size'), data.approved_queue_size || 0);
        updateTextContent(document.getElementById('forwarding-workers-active'), data.forwarding_workers_active || 0);
        
        // Update metrics start time
        if (data.metrics_start_time) {
            updateTextContent(document.getElementById('metrics-start-time'), formatTimestamp(data.metrics_start_time));
        }
        
    } catch (error) {
        console.error('Error updating metrics:', error);
    }
}

/**
 * Updates overview section with system status
 * @param {Object} data - System overview data
 */
function updateOverview(data) {
    console.log('Updating overview:', data);
    
    if (!data) return;
    
    try {
        // Update engine status - using correct IDs from HTML
        if (data.engine_status) {
            updateTextContent(document.getElementById('overview-engine-status-text'), data.engine_status);
            
            // Update status dot
            const statusDot = document.getElementById('overview-engine-status-dot');
            if (statusDot) {
                statusDot.className = 'status-dot ' + 
                    (data.engine_status === 'running' ? 'green' : 
                     data.engine_status === 'paused' ? 'yellow' : 'red');
            }
        }
        
        // Update last update status - using correct IDs
        if (data.last_update_status) {
            updateTextContent(document.getElementById('overview-last-update-status'), data.last_update_status);
        }
        
        // Update timestamps - using correct IDs
        if (data.last_successful_update) {
            updateTextContent(document.getElementById('overview-last-successful-update'), formatTimestamp(data.last_successful_update));
        }
        
        if (data.last_failed_update) {
            updateTextContent(document.getElementById('overview-last-failed-update'), formatTimestamp(data.last_failed_update));
        }
        
        if (data.last_update_duration) {
            updateTextContent(document.getElementById('overview-last-update-duration'), `${data.last_update_duration}s`);
        }
        
        // Update number of tickers - using correct ID
        if (data.num_tickers !== undefined) {
            updateTextContent(document.getElementById('overview-num-tickers'), data.num_tickers);
        }
        
        // Update WebSocket status - using correct ID
        if (data.websocket_status) {
            updateTextContent(document.getElementById('overview-websocket-status'), data.websocket_status);
        }
        
        // Update Elite auth status - using correct ID
        if (data.elite_auth_status) {
            updateTextContent(document.getElementById('overview-elite-auth-status'), data.elite_auth_status);
        }
        
    } catch (error) {
        console.error('Error updating overview:', error);
    }
}

/**
 * Adds a new audit entry to the audit log
 * @param {Object} entry - Audit entry data
 */
function addAuditEntry(entry) {
    console.log('Adding audit entry:', entry);
    
    if (!entry) return;
    
    try {
        // Use correct ID from HTML - audit-log-container
        const container = document.getElementById('audit-log-container');
        if (!container) {
            console.warn('Audit log container not found');
            return;
        }
          // Hide empty message if it exists - using correct ID
        const emptyMessage = document.getElementById('audit-log-empty-message');
        if (emptyMessage) {
            emptyMessage.style.display = 'none';
        }
        
        // ENHANCED: Check if entry with same signal_id already exists
        const existingEntry = container.querySelector(`[data-signal-id="${entry.signal_id}"]`);
        if (existingEntry) {
            // FIXED: Check if this is actually a newer update
            const existingData = JSON.parse(existingEntry.getAttribute('data-entry') || '{}');
            const existingTimestamp = new Date(existingData.updated_at || existingData.timestamp || 0).getTime();
            const newTimestamp = new Date(entry.updated_at || entry.timestamp || 0).getTime();
            
            if (newTimestamp > existingTimestamp) {
                console.log(`🔄 Updating existing entry for signal ${entry.signal_id?.slice(0, 8)}... (newer timestamp)`);
                updateExistingAuditEntry(existingEntry, entry);
            } else {
                console.log(`⏭️ Skipping older update for signal ${entry.signal_id?.slice(0, 8)}...`);
            }
            return;
        }
        
        // This is a new entry - make sure to initialize it in the tracker if it has a signal_id
        if (entry.signal_id) {
            // Initialize tracker for new signal (this handles the case where we first see a signal)
            if (!window.signalStatusTracker) {
                window.signalStatusTracker = {};
            }
            // CRITICAL FIX: Add new signal to tracker immediately
            window.signalStatusTracker[entry.signal_id] = entry.status;
            console.log(`🆕 New signal detected: ${entry.signal_id.slice(0, 8)}... with status ${entry.status} - ADDED TO TRACKER`);
        }

        // Check if this is a manual admin order (define manualClass early)
        const isManualOrder = (
            entry.worker_id && (
                entry.worker_id.includes('ADMIN-MANUAL') || 
                entry.worker_id.includes('ADMIN-SELL-ALL') || 
                entry.worker_id.includes('ADMIN-QUEUE')
            )
        ) || (
            entry.details && entry.details.includes('📋 MANUAL')
        );
        
        let manualClass = '';
        if (isManualOrder) {
            manualClass = 'border-blue-500/30 bg-slate-800/80'; // Slight highlight for manual orders
        }

        // Create entry element
        const entryElement = document.createElement('div');
        entryElement.className = `audit-entry bg-slate-800 rounded-lg p-3 border border-slate-700 mb-2 ${manualClass}`;
        entryElement.setAttribute('data-entry', JSON.stringify(entry));
        entryElement.setAttribute('data-signal-id', entry.signal_id || '');
          // Create entry content
        const timestamp = formatTimestamp(entry.timestamp);
        const status = entry.status || 'unknown';
        const statusDisplay = entry.status_display || status;
        const ticker = entry.ticker || entry.normalised_ticker || 'N/A';
        const action = entry.action || 'N/A';
        const details = entry.details || '';
        const signalId = entry.signal_id || 'N/A';
        const eventsCount = entry.events_count || 0;
        const location = entry.location || 'unknown';
        
        // Extract original signal data for display
        console.log('🔍 DEBUG: Processing entry for signal data extraction:', {
            signal_id: entry.signal_id?.slice(0, 8) + '...',
            has_original_signal: !!entry.original_signal,
            original_signal_keys: entry.original_signal ? Object.keys(entry.original_signal) : [],
            entry_keys: Object.keys(entry)
        });
        
        let signalData = {};
        if (entry.original_signal && typeof entry.original_signal === 'object') {
            signalData = entry.original_signal;
            console.log('🔍 DEBUG: Extracted signal data from original_signal:', signalData);
        } else {
            // Fallback: try to extract from entry itself
            signalData = {
                side: entry.side,
                action: entry.action,
                price: entry.price,
                time: entry.time,
                volume: entry.volume,
                quantity: entry.quantity,
                shares: entry.shares,
                size: entry.size
            };
            console.log('🔍 DEBUG: Extracted signal data from entry fields:', signalData);
        }
        
        // Format signal data for display
        const signalSide = signalData.side || signalData.action || 'N/A';
        const signalPrice = signalData.price ? `$${parseFloat(signalData.price).toFixed(2)}` : 'N/A';
        const signalTime = signalData.time ? formatTimestamp(signalData.time) : 'N/A';
        const signalVolume = signalData.volume || signalData.quantity || signalData.shares || signalData.size || 'N/A';
        
        console.log('🔍 DEBUG: Formatted signal display data:', {
            side: signalSide,
            price: signalPrice,
            time: signalTime,
            volume: signalVolume
        });
          // Determine status color based on the new status system
        let statusColor = 'text-slate-400';
        let statusBadge = statusDisplay;
        let locationBadge = location.replace('_', ' ').toUpperCase();
        
        if (status.includes('success') || status.includes('approved') || status === 'forwarded_success') {
            statusColor = 'text-green-400';        } else if (status.includes('error') || status.includes('failed') || status.includes('rejected') || status.includes('timeout')) {
            statusColor = 'text-red-400';
        } else if (status.includes('processing') || status.includes('forwarding') || status.includes('queued')) {
            statusColor = 'text-yellow-400';
        }
        
        // Add manual order indicator (isManualOrder and manualClass already defined above)
        let manualBadge = '';
        if (isManualOrder) {
            manualBadge = '<span class="text-xs text-blue-300 bg-blue-900 px-2 py-1 rounded font-semibold">📋 MANUAL</span>';
        }

        // Determine if this signal allows trading actions
        // Show buttons for any signal with a valid ticker, regardless of status
        const allowActions = (
            ticker && 
            ticker !== 'N/A' && 
            ticker.trim() !== ''
        );entryElement.innerHTML = `
            <div class="flex justify-between items-start mb-2">
                <div class="flex items-center space-x-3">
                    <span class="text-xs text-slate-500">${timestamp}</span>
                    <span class="text-sm font-mono text-teal-400 font-semibold">${ticker}</span>
                    <span class="text-xs font-mono text-blue-300 bg-blue-900/30 px-2 py-1 rounded">${signalId.slice(0, 8)}...</span>
                    <span class="text-sm ${statusColor} font-semibold px-2 py-1 rounded bg-opacity-20 ${status.includes('approved') || status.includes('success') ? 'bg-green-500' : status.includes('rejected') || status.includes('error') ? 'bg-red-500' : 'bg-yellow-500'}">${statusBadge}</span>
                    <span class="text-xs text-slate-400 bg-slate-700 px-2 py-1 rounded">${locationBadge}</span>
                    ${manualBadge}
                </div>
                <div class="flex items-center space-x-2">
                    <div class="text-xs text-slate-400">
                        <span>${eventsCount} events</span>
                        <span class="ml-2">${action}</span>
                    </div>                    ${allowActions ? `
                        <div class="flex gap-1 ml-3">
                            <button 
                                class="sell-signal-btn bg-slate-600 hover:bg-red-600 text-slate-300 hover:text-white text-xs px-2 py-1 rounded transition-all duration-200 opacity-70 hover:opacity-100" 
                                title="Sell ${ticker} immediately" 
                                data-ticker="${ticker}"
                                data-signal-id="${signalId}"
                            >
                                ↗ Sell
                            </button>
                            <button 
                                class="queue-signal-btn bg-slate-600 hover:bg-orange-600 text-slate-300 hover:text-white text-xs px-2 py-1 rounded transition-all duration-200 opacity-70 hover:opacity-100" 
                                title="Add ${ticker} to sell all accumulator" 
                                data-ticker="${ticker}"
                                data-signal-id="${signalId}"
                            >
                                + Queue
                            </button>
                        </div>
                    ` : ''}
                </div>
            </div>
            <div class="flex items-center space-x-4 text-xs text-slate-400 mb-2">
                <span class="font-semibold ${signalSide === 'BUY' || signalSide === 'buy' ? 'text-green-400' : signalSide === 'SELL' || signalSide === 'sell' ? 'text-red-400' : 'text-slate-400'}">${signalSide}</span>
                <span>Price: <span class="text-slate-300">${signalPrice}</span></span>
                <span>Volume: <span class="text-slate-300">${signalVolume}</span></span>
                <span>Signal Time: <span class="text-slate-300">${signalTime}</span></span>
            </div>
            ${details ? `<div class="text-sm text-slate-300 mt-2">${details}</div>` : ''}
            ${entry.events && entry.events.length > 0 ? `
                <div class="mt-2 text-xs text-slate-400">
                    <span class="font-medium">Journey:</span> 
                    ${entry.events.map(e => e.event_type).join(' → ')}
                </div>
            ` : ''}
        `;// Add to beginning of list (most recent first)
        container.insertBefore(entryElement, container.firstChild);
        
        // Add event listeners for action buttons if they exist
        if (allowActions) {
            const sellBtn = entryElement.querySelector('.sell-signal-btn');
            const queueBtn = entryElement.querySelector('.queue-signal-btn');
              if (sellBtn) {
                sellBtn.addEventListener('click', async (e) => {
                    e.stopPropagation();
                    const ticker = e.target.getAttribute('data-ticker');
                    const signalId = e.target.getAttribute('data-signal-id');
                    
                    // Disable button and show loading
                    const originalText = e.target.textContent;
                    e.target.disabled = true;
                    e.target.textContent = '⏳';
                    e.target.classList.add('opacity-50', 'cursor-not-allowed');
                    
                    console.log(`Sell action triggered for ${ticker} (signal: ${signalId.slice(0, 8)}...)`);
                    
                    try {
                        await sellIndividualTicker(ticker);
                    } finally {
                        // Re-enable button after action completes
                        setTimeout(() => {
                            e.target.disabled = false;
                            e.target.textContent = originalText;
                            e.target.classList.remove('opacity-50', 'cursor-not-allowed');
                        }, 2000);
                    }
                });
            }
            
            if (queueBtn) {
                queueBtn.addEventListener('click', async (e) => {
                    e.stopPropagation();
                    const ticker = e.target.getAttribute('data-ticker');
                    const signalId = e.target.getAttribute('data-signal-id');
                    
                    // Disable button and show loading
                    const originalText = e.target.textContent;
                    e.target.disabled = true;
                    e.target.textContent = '⏳';
                    e.target.classList.add('opacity-50', 'cursor-not-allowed');
                    
                    console.log(`Queue action triggered for ${ticker} (signal: ${signalId.slice(0, 8)}...)`);
                    
                    try {
                        await addToSellAllList(ticker);
                    } finally {
                        // Re-enable button after action completes
                        setTimeout(() => {
                            e.target.disabled = false;
                            e.target.textContent = originalText;
                            e.target.classList.remove('opacity-50', 'cursor-not-allowed');
                        }, 2000);
                    }
                });
            }
        }        
        // Removed artificial limit - keep all entries
        // The backend already limits to 5000 entries via deque maxlen
        // Let's allow the full audit trail to be displayed
        
        // Update statistics and charts
        updateAuditStatistics();
        updateChartsWithEntry(entry);
        
    } catch (error) {
        console.error('Error adding audit entry:', error);
    }
}

/**
 * Updates an existing audit entry with new information
 * @param {Element} entryElement - Existing DOM element
 * @param {Object} entry - Updated entry data
 */
function updateExistingAuditEntry(entryElement, entry) {
    console.log('Updating existing audit entry:', entry.signal_id);
    
    try {
        // Update the data attribute
        entryElement.setAttribute('data-entry', JSON.stringify(entry));
          // Update the visual content
        const timestamp = formatTimestamp(entry.timestamp);
        const status = entry.status || 'unknown';
        const statusDisplay = entry.status_display || status;
        const ticker = entry.ticker || entry.normalised_ticker || 'N/A';
        const action = entry.action || 'N/A';
        const details = entry.details || '';
        const signalId = entry.signal_id || 'N/A';
        const eventsCount = entry.events_count || 0;
        const location = entry.location || 'unknown';
        
        // Extract original signal data for display
        console.log('🔍 DEBUG: Processing updated entry for signal data extraction:', {
            signal_id: entry.signal_id?.slice(0, 8) + '...',
            has_original_signal: !!entry.original_signal,
            original_signal_keys: entry.original_signal ? Object.keys(entry.original_signal) : [],
            entry_keys: Object.keys(entry)
        });
        
        let signalData = {};
        if (entry.original_signal && typeof entry.original_signal === 'object') {
            signalData = entry.original_signal;
            console.log('🔍 DEBUG: Extracted signal data from original_signal (update):', signalData);
        } else {
            // Fallback: try to extract from entry itself
            signalData = {
                side: entry.side,
                action: entry.action,
                price: entry.price,
                time: entry.time,
                volume: entry.volume,
                quantity: entry.quantity,
                shares: entry.shares,
                size: entry.size
            };
            console.log('🔍 DEBUG: Extracted signal data from entry fields (update):', signalData);
        }
        
        // Format signal data for display
        const signalSide = signalData.side || signalData.action || 'N/A';
        const signalPrice = signalData.price ? `$${parseFloat(signalData.price).toFixed(2)}` : 'N/A';
        const signalTime = signalData.time ? formatTimestamp(signalData.time) : 'N/A';
        const signalVolume = signalData.volume || signalData.quantity || signalData.shares || signalData.size || 'N/A';
        
        console.log('🔍 DEBUG: Formatted signal display data (update):', {
            side: signalSide,
            price: signalPrice,
            time: signalTime,
            volume: signalVolume
        });
          // Determine status color
        let statusColor = 'text-slate-400';
        let locationBadge = location.replace('_', ' ').toUpperCase();
        
        if (status.includes('success') || status.includes('approved') || status === 'forwarded_success') {
            statusColor = 'text-green-400';
        } else if (status.includes('error') || status.includes('failed') || status.includes('rejected') || status.includes('timeout')) {
            statusColor = 'text-red-400';        } else if (status.includes('processing') || status.includes('forwarding') || status.includes('queued')) {
            statusColor = 'text-yellow-400';
        }
        
        // Check if this is a manual admin order
        const isManualOrder = (
            entry.worker_id && (
                entry.worker_id.includes('ADMIN-MANUAL') || 
                entry.worker_id.includes('ADMIN-SELL-ALL') || 
                entry.worker_id.includes('ADMIN-QUEUE')
            )
        ) || (
            details && details.includes('📋 MANUAL')
        );
          // Add manual order indicator
        let manualBadge = '';
        if (isManualOrder) {
            manualBadge = '<span class="text-xs text-blue-300 bg-blue-900 px-2 py-1 rounded font-semibold">📋 MANUAL</span>';
        }

        // Update element class to highlight manual orders
        const baseClasses = 'audit-entry bg-slate-800 rounded-lg p-3 border border-slate-700 mb-2';
        const manualClass = isManualOrder ? 'border-blue-500/30 bg-slate-800/80' : '';
        entryElement.className = `${baseClasses} ${manualClass}`;
        
        // Determine if this signal allows trading actions
        // Show buttons for any signal with a valid ticker, regardless of status
        const allowActions = (
            ticker && 
            ticker !== 'N/A' && 
            ticker.trim() !== ''
        );        entryElement.innerHTML = `
            <div class="flex justify-between items-start mb-2">
                <div class="flex items-center space-x-3">
                    <span class="text-xs text-slate-500">${timestamp}</span>
                    <span class="text-sm font-mono text-teal-400 font-semibold">${ticker}</span>
                    <span class="text-xs font-mono text-blue-300 bg-blue-900/30 px-2 py-1 rounded">${signalId.slice(0, 8)}...</span>
                    <span class="text-sm ${statusColor} font-semibold px-2 py-1 rounded bg-opacity-20 ${status.includes('approved') || status.includes('success') ? 'bg-green-500' : status.includes('rejected') || status.includes('error') ? 'bg-red-500' : 'bg-yellow-500'}">${statusDisplay}</span>
                    <span class="text-xs text-slate-400 bg-slate-700 px-2 py-1 rounded">${locationBadge}</span>
                    ${manualBadge}
                    <span class="text-xs text-orange-400">UPDATED</span>
                </div>
                <div class="flex items-center space-x-2">
                    <div class="text-xs text-slate-400">
                        <span>${eventsCount} events</span>
                        <span class="ml-2">${action}</span>
                    </div>
                    ${allowActions ? `
                        <div class="flex gap-1 ml-3">
                            <button 
                                class="sell-signal-btn bg-slate-600 hover:bg-red-600 text-slate-300 hover:text-white text-xs px-2 py-1 rounded transition-all duration-200 opacity-70 hover:opacity-100" 
                                title="Sell ${ticker} immediately" 
                                data-ticker="${ticker}"
                                data-signal-id="${signalId}"
                            >
                                ↗ Sell
                            </button>
                            <button 
                                class="queue-signal-btn bg-slate-600 hover:bg-orange-600 text-slate-300 hover:text-white text-xs px-2 py-1 rounded transition-all duration-200 opacity-70 hover:opacity-100" 
                                title="Add ${ticker} to sell all accumulator" 
                                data-ticker="${ticker}"
                                data-signal-id="${signalId}"
                            >
                                + Queue
                            </button>
                        </div>
                    ` : ''}
                </div>
            </div>
            <div class="flex items-center space-x-4 text-xs text-slate-400 mb-2">
                <span class="font-semibold ${signalSide === 'BUY' || signalSide === 'buy' ? 'text-green-400' : signalSide === 'SELL' || signalSide === 'sell' ? 'text-red-400' : 'text-slate-400'}">${signalSide}</span>
                <span>Price: <span class="text-slate-300">${signalPrice}</span></span>
                <span>Volume: <span class="text-slate-300">${signalVolume}</span></span>
                <span>Signal Time: <span class="text-slate-300">${signalTime}</span></span>
            </div>
            ${details ? `<div class="text-sm text-slate-300 mt-2">${details}</div>` : ''}
            ${entry.events && entry.events.length > 0 ? `
                <div class="mt-2 text-xs text-slate-400">
                    <span class="font-medium">Journey:</span> 
                    ${entry.events.map(e => e.event_type).join(' → ')}
                </div>
            ` : ''}        `;
        
        // Add event listeners for action buttons if they exist
        if (allowActions) {
            const sellBtn = entryElement.querySelector('.sell-signal-btn');
            const queueBtn = entryElement.querySelector('.queue-signal-btn');
            
            if (sellBtn) {
                sellBtn.addEventListener('click', async (e) => {
                    e.stopPropagation();
                    const ticker = e.target.getAttribute('data-ticker');
                    const signalId = e.target.getAttribute('data-signal-id');
                    
                    // Disable button and show loading
                    const originalText = e.target.textContent;
                    e.target.disabled = true;
                    e.target.textContent = '⏳';
                    e.target.classList.add('opacity-50', 'cursor-not-allowed');
                    
                    console.log(`Sell action triggered for ${ticker} (signal: ${signalId.slice(0, 8)}...)`);
                    
                    try {
                        await sellIndividualTicker(ticker);
                    } finally {
                        // Re-enable button after action completes
                        setTimeout(() => {
                            e.target.disabled = false;
                            e.target.textContent = originalText;
                            e.target.classList.remove('opacity-50', 'cursor-not-allowed');
                        }, 2000);
                    }
                });
            }
            
            if (queueBtn) {
                queueBtn.addEventListener('click', async (e) => {
                    e.stopPropagation();
                    const ticker = e.target.getAttribute('data-ticker');
                    const signalId = e.target.getAttribute('data-signal-id');
                    
                    // Disable button and show loading
                    const originalText = e.target.textContent;
                    e.target.disabled = true;
                    e.target.textContent = '⏳';
                    e.target.classList.add('opacity-50', 'cursor-not-allowed');
                    
                    console.log(`Queue action triggered for ${ticker} (signal: ${signalId.slice(0, 8)}...)`);
                    
                    try {
                        await addToSellAllList(ticker);
                    } finally {
                        // Re-enable button after action completes
                        setTimeout(() => {
                            e.target.disabled = false;
                            e.target.textContent = originalText;
                            e.target.classList.remove('opacity-50', 'cursor-not-allowed');
                        }, 2000);
                    }
                });
            }
        }
          // Move updated entry to top
        const container = entryElement.parentNode;
        container.insertBefore(entryElement, container.firstChild);
        
        // Update statistics and charts
        updateAuditStatistics();
        updateChartsWithEntry(entry);
        
    } catch (error) {
        console.error('Error updating audit entry:', error);
    }
}

/**
 * Updates system information display
 * @param {Object} data - System info data
 */
function updateSystemInfo(data) {
    if (!data) return;
    
    console.log('Updating system info with data:', data);
    
    try {
        // Update Finviz elite info - using correct IDs
        if (data.is_elite_enabled !== undefined || data.finviz_elite_enabled !== undefined) {
            updateTextContent(document.getElementById('finviz-elite-enabled'), 
                (data.is_elite_enabled || data.finviz_elite_enabled) ? 'Yes' : 'No');
        }
        
        if (data.elite_session_valid !== undefined || data.auth_session_valid !== undefined) {
            updateTextContent(document.getElementById('auth-session-valid'), 
                (data.elite_session_valid || data.auth_session_valid) ? 'Yes' : 'No');
        }
        
        // Update rate limiting info - using correct IDs
        if (data.max_requests_per_min !== undefined) {
            updateTextContent(document.getElementById('max-requests-per-min'), data.max_requests_per_min);
        }
        
        if (data.max_concurrency !== undefined) {
            updateTextContent(document.getElementById('max-concurrency'), data.max_concurrency);
        }
          if (data.rows_per_page !== undefined || data.tickers_per_page !== undefined) {
            updateTextContent(document.getElementById('rows-per-page'), 
                data.rows_per_page !== undefined ? data.rows_per_page : data.tickers_per_page);
        }
          if (data.rate_limit_tokens !== undefined || data.rate_limit_tokens_available !== undefined) {
            updateTextContent(document.getElementById('rate-limit-tokens-live'), 
                data.rate_limit_tokens !== undefined ? data.rate_limit_tokens : data.rate_limit_tokens_available);
        }
        
        if (data.concurrency_slots !== undefined || data.concurrency_slots_available !== undefined) {
            updateTextContent(document.getElementById('concurrency-slots-available'), 
                data.concurrency_slots !== undefined ? data.concurrency_slots : data.concurrency_slots_available);
        }
          // Update webhook rate limiter system info - using correct IDs
        if (data.webhook_rate_limiter_status !== undefined || data.webhook_rl_status !== undefined || data.webhook_rate_limiter_enabled !== undefined) {
            const status = data.webhook_rate_limiter_status !== undefined ? data.webhook_rate_limiter_status : 
                          data.webhook_rl_status !== undefined ? data.webhook_rl_status :
                          (data.webhook_rate_limiter_enabled ? 'Enabled' : 'Disabled');
            updateTextContent(document.getElementById('webhook-rl-status-live'), status);
        }
        
        if (data.webhook_tokens !== undefined || data.webhook_rl_tokens !== undefined || data.webhook_tokens_available !== undefined) {
            const tokens = data.webhook_tokens !== undefined ? data.webhook_tokens :
                          data.webhook_rl_tokens !== undefined ? data.webhook_rl_tokens :
                          data.webhook_tokens_available;
            updateTextContent(document.getElementById('webhook-rl-tokens-live'), tokens);
        }
        
        if (data.webhook_max_per_min !== undefined || data.webhook_rl_max !== undefined || data.webhook_max_requests_per_minute !== undefined) {
            const maxPerMin = data.webhook_max_per_min !== undefined ? data.webhook_max_per_min :
                             data.webhook_rl_max !== undefined ? data.webhook_rl_max :
                             data.webhook_max_requests_per_minute;
            updateTextContent(document.getElementById('webhook-rl-max-live'), maxPerMin);
        }
        
    } catch (error) {
        console.error('Error updating system info:', error);
    }
}

/**
 * Updates webhook rate limiter status
 * @param {Object} data - Webhook rate limiter data
 */
function updateWebhookRateLimiter(data) {
    if (!data) return;
    
    console.log('Updating webhook rate limiter with data:', data);
    
    try {
        // Update main status indicator
        const statusElement = document.getElementById('webhook-rate-limiter-status');
        const statusIndicator = document.getElementById('webhook-status-indicator');
        const statusDescription = document.getElementById('webhook-status-description');
        
        if (data.status !== undefined || data.enabled !== undefined) {
            const isEnabled = data.enabled !== undefined ? data.enabled : (data.status === 'Enabled');
            const statusText = isEnabled ? 'Enabled' : 'Disabled';
            
            // Update main status badge
            if (statusElement) {
                safeUpdateTextContent(statusElement, statusText);
                statusElement.className = `px-3 py-1 text-xs font-semibold rounded-full ${
                    isEnabled ? 'bg-green-600 text-green-100' : 'bg-red-600 text-red-100'
                }`;
            }
            
            // Update status indicator
            if (statusIndicator) {
                safeUpdateTextContent(statusIndicator, statusText);
                statusIndicator.className = `px-2 py-1 text-xs rounded-full ${
                    isEnabled ? 'bg-green-600 text-green-100' : 'bg-red-600 text-red-100'
                }`;
            }
            
            // Update description
            if (statusDescription) {
                const description = isEnabled 
                    ? 'Rate limiter is active, controlling webhook sending rate to protect destination'
                    : 'Rate limiter is disabled, webhooks will be sent immediately without rate control';
                safeUpdateTextContent(statusDescription, description);
            }
            
            // Update button states
            const pauseBtn = document.getElementById('webhook-pause-btn');
            const resumeBtn = document.getElementById('webhook-resume-btn');
            
            if (pauseBtn) {
                pauseBtn.disabled = !isEnabled;
                if (isEnabled) {
                    pauseBtn.classList.remove('opacity-50');
                } else {
                    pauseBtn.classList.add('opacity-50');
                }
            }
            
            if (resumeBtn) {
                resumeBtn.disabled = isEnabled;
                if (!isEnabled) {
                    resumeBtn.classList.remove('opacity-50');
                } else {
                    resumeBtn.classList.add('opacity-50');
                }
            }
        }
        
        // Update metrics with proper null/undefined handling
        if (data.tokens_available !== undefined) {
            const element = document.getElementById('webhook-tokens-available');
            if (element) {
                safeUpdateTextContent(element, data.tokens_available);
            }
        }
        
        if (data.max_req_per_min !== undefined) {
            const element = document.getElementById('webhook-max-req-per-min');
            if (element) {
                safeUpdateTextContent(element, data.max_req_per_min);
            }
        }
        
        if (data.requests_made_this_minute !== undefined || data.requests_this_minute !== undefined) {
            const element = document.getElementById('webhook-requests-this-minute');
            if (element) {
                const value = data.requests_made_this_minute !== undefined 
                    ? data.requests_made_this_minute 
                    : data.requests_this_minute;
                safeUpdateTextContent(element, value);
            }
        }
        
        if (data.total_requests_limited !== undefined || data.total_limited !== undefined) {
            const element = document.getElementById('webhook-total-limited');
            if (element) {
                const value = data.total_requests_limited !== undefined 
                    ? data.total_requests_limited 
                    : data.total_limited;
                safeUpdateTextContent(element, value);
            }
        }
        
        // Update enabled checkbox state
        if (data.enabled !== undefined) {
            const checkbox = document.getElementById('webhook-enabled-checkbox');
            if (checkbox) {
                checkbox.checked = data.enabled;
                
                // Update toggle visual state
                const toggle = checkbox.parentElement.querySelector('.relative');
                const dot = toggle?.querySelector('.dot');
                if (toggle && dot) {
                    if (data.enabled) {
                        toggle.classList.add('bg-purple-600');
                        toggle.classList.remove('bg-slate-600');
                        dot.style.transform = 'translateX(24px)';
                    } else {
                        toggle.classList.remove('bg-purple-600');
                        toggle.classList.add('bg-slate-600');
                        dot.style.transform = 'translateX(0px)';
                    }
                }
            }
        }
        
    } catch (error) {
        console.error('Error updating webhook rate limiter:', error);
    }
}

/**
 * Updates ticker list display
 * @param {Array} tickers - Array of ticker data
 */
function updateTickerList(tickers) {
    if (!tickers || !Array.isArray(tickers)) {
        console.warn('Invalid tickers data provided to updateTickerList');
        return;
    }
    
    console.log('Updating ticker list with', tickers.length, 'tickers');
    
    try {
        // Update ticker count - using correct ID
        const tickerCount = tickers.length;
        const countElement = document.getElementById('ticker-count');
        if (countElement) {
            updateTextContent(countElement, tickerCount);
        }
        
        // Update ticker list container - using correct ID
        const listContainer = document.getElementById('tickers-list-container');
        if (listContainer) {
            // Clear current list
            listContainer.innerHTML = '';
            
            if (tickers.length === 0) {
                // Show no tickers message
                const emptyMessage = document.createElement('div');
                emptyMessage.className = 'bg-yellow-600 text-yellow-900 font-semibold text-center py-3 px-4 rounded-md shadow-md text-sm col-span-full flex items-center justify-center';
                emptyMessage.innerHTML = `
                    <svg xmlns="http://www.w3.org/2000/svg" class="h-5 w-5 mr-2 shrink-0" viewBox="0 0 20 20" fill="currentColor">
                        <path fill-rule="evenodd" d="M8.257 3.099c.765-1.36 2.722-1.36 3.486 0l5.58 9.92c.75 1.334-.216 3.004-1.742 3.004H4.42c-1.526 0-2.492-1.67-1.742-3.004l5.58-9.92zM10 10a1 1 0 00-1 1v2a1 1 0 102 0v-2a1 1 0 00-1-1zm0 5a1 1 0 100-2 1 1 0 000 2z" clip-rule="evenodd" />
                    </svg>
                    No tickers available. Finviz may be temporarily blocking requests or none found.
                `;
                listContainer.appendChild(emptyMessage);            } else {
                // Add each ticker as a simple display item without action buttons
                tickers.forEach(ticker => {
                    const tickerElement = document.createElement('div');
                    tickerElement.className = 'ticker-item bg-slate-700 text-slate-200 px-3 py-2 rounded text-sm font-mono text-center hover:bg-slate-600 transition-colors border border-slate-600';
                    
                    tickerElement.innerHTML = `
                        <div class="font-semibold text-teal-400">${ticker}</div>
                        <div class="text-xs text-slate-400 mt-1">Top-N Approved</div>
                    `;
                    
                    listContainer.appendChild(tickerElement);
                });
            }
        }
        
    } catch (error) {
        console.error('Error updating ticker list:', error);
    }
}

/**
 * Updates sell all list display
 * @param {Array} sellAllList - List of tickers for sell all
 */
function updateSellAllList(sellAllList) {
    if (!Array.isArray(sellAllList)) {
        console.warn('Invalid sell all list data provided');
        return;
    }
    
    console.log('Updating sell all list with', sellAllList.length, 'items');
    
    try {
        // Use correct ID from HTML - sell-all-list-container
        const container = document.getElementById('sell-all-list-container');
        if (!container) {
            console.warn('Sell all list container not found');
            return;
        }
        
        // Clear current list
        container.innerHTML = '';
        
        if (sellAllList.length === 0) {
            // Show empty message
            const emptyMessage = document.createElement('p');
            emptyMessage.className = 'text-slate-400 text-sm italic';
            emptyMessage.textContent = 'Tickers added for \'Sell All\' will appear here...';
            container.appendChild(emptyMessage);
        } else {
            // Add each ticker
            sellAllList.forEach(ticker => {
                const tickerElement = document.createElement('span');
                tickerElement.className = 'ticker-item bg-red-700 text-red-100 px-3 py-1 rounded text-sm font-mono mr-2 mb-2 inline-block';
                tickerElement.textContent = ticker;
                container.appendChild(tickerElement);
            });
        }        
    } catch (error) {
        console.error('Error updating sell all list:', error);
    }
}

/**
 * Updates configuration display
 * @param {Object} data - Configuration data
 */
function updateConfigDisplay(data) {
    if (!data) return;
    
    console.log('Updating config display with data:', data);
    
    try {
        // Update Finviz URL - using correct ID
        if (data.current_finviz_url) {
            updateTextContent(document.getElementById('finviz-url'), data.current_finviz_url);
        }
        
        // Update Top-N - using correct ID
        if (data.current_top_n !== undefined) {
            updateTextContent(document.getElementById('top-n'), data.current_top_n);
        }
        
        // Update refresh interval - using correct ID  
        if (data.current_refresh_sec !== undefined) {
            updateTextContent(document.getElementById('current-refresh-sec'), data.current_refresh_sec);
        }
        
        // Update destination webhook URL - using correct ID
        if (data.dest_webhook_url) {
            updateTextContent(document.getElementById('dest-webhook-url'), data.dest_webhook_url);
        }
        
    } catch (error) {
        console.error('Error updating config display:', error);
    }
}

/**
 * Loads and displays audit entries
 * @param {Array} entries - Array of audit entries
 */
function loadAuditEntries(entries) {
    if (!Array.isArray(entries)) {
        console.warn('Invalid audit entries data provided:', typeof entries, entries);
        return;
    }
    
    console.log('Loading', entries.length, 'audit entries');
    
    try {
        // Use correct ID from HTML - audit-log-container
        const container = document.getElementById('audit-log-container');
        if (!container) {
            console.error('Audit log container not found');
            return;
        }
        
        // Clear container but preserve the empty message element
        const emptyMessage = document.getElementById('audit-log-empty-message');
        const children = Array.from(container.children);
        children.forEach(child => {
            if (child.id !== 'audit-log-empty-message') {
                child.remove();
            }
        });
        
        if (entries.length === 0) {
            // Show empty message
            if (emptyMessage) {
                emptyMessage.style.display = 'block';
                emptyMessage.textContent = 'No audit logs available for the selected period.';
            }
            console.log('No audit entries to display');
        } else {
            // Hide empty message
            if (emptyMessage) {
                emptyMessage.style.display = 'none';
            }
              console.log('Sample audit entry:', entries[0]);
            
            // Use lazy loading system for better performance with large datasets
            console.log(`Preparing to load ${entries.length} audit entries with lazy loading`);
            initializeLazyLoadingAuditEntries(entries);
        }
        
        // Update statistics and charts after loading entries
        updateAuditStatistics();
        
        // Clean up old signals from status tracker
        cleanupStatusTracker();
        
        // Update charts with loaded data
        if (window.updateAuditCharts) {
            window.updateAuditCharts();
        }
        
    } catch (error) {
        console.error('Error loading audit entries:', error);
    }
}

/**
 * Updates audit statistics based on current entries
 */
function updateAuditStatistics() {
    try {
        const auditContainer = document.getElementById('audit-log-container');
        const entries = auditContainer?.querySelectorAll('.audit-entry') || [];
        
        const totalCountElement = document.getElementById('audit-total-count');
        const approvedCountElement = document.getElementById('audit-approved-count');
        const rejectedCountElement = document.getElementById('audit-rejected-count');
        const forwardedCountElement = document.getElementById('audit-forwarded-count');
          let totalCount = 0;
        let approvedCount = 0;
        let rejectedCount = 0;
        let forwardedCount = 0;
        
        // ENHANCED: Use signal status tracker as source of truth when available
        if (window.signalStatusTracker && Object.keys(window.signalStatusTracker).length > 0) {
            // Count from tracker for more accurate results
            const statusCounts = {};
            Object.values(window.signalStatusTracker).forEach(status => {
                statusCounts[status] = (statusCounts[status] || 0) + 1;
            });
            
            // Map to display categories
            approvedCount = (statusCounts['approved'] || 0) + 
                          (statusCounts['queued_forwarding'] || 0) + 
                          (statusCounts['forwarding'] || 0);
            
            rejectedCount = statusCounts['rejected'] || 0;
            
            forwardedCount = (statusCounts['forwarded_success'] || 0);
            
            totalCount = Object.keys(window.signalStatusTracker).length;
            
            console.log('📊 Statistics calculated from signal tracker:', {
                total: totalCount, approved: approvedCount, rejected: rejectedCount, forwarded: forwardedCount
            });
        } else {
            // Fallback: count from visible entries
            entries.forEach(entry => {
                if (entry.style.display !== 'none') { // Only count visible entries
                    totalCount++;
                    
                    try {
                        const entryData = JSON.parse(entry.getAttribute('data-entry') || '{}');
                        const status = (entryData.status || '').toLowerCase();
                        
                        // FIXED: Consistent status mapping
                        if (status === 'approved' || status === 'queued_forwarding' || status === 'forwarding') {
                            approvedCount++;
                        } else if (status === 'rejected') {
                            rejectedCount++;
                        } else if (status === 'forwarded_success') {
                            forwardedCount++;
                        }
                    } catch (parseError) {
                        console.warn('Error parsing entry data for statistics:', parseError);
                    }
                }
            });
            
            console.log('📊 Statistics calculated from DOM entries:', {
                total: totalCount, approved: approvedCount, rejected: rejectedCount, forwarded: forwardedCount
            });
        }
        
        // Update display
        if (totalCountElement) totalCountElement.textContent = totalCount;
        if (approvedCountElement) approvedCountElement.textContent = approvedCount;
        if (rejectedCountElement) rejectedCountElement.textContent = rejectedCount;  
        if (forwardedCountElement) forwardedCountElement.textContent = forwardedCount;
        
        console.log('Updated audit statistics:', { totalCount, approvedCount, rejectedCount, forwardedCount });
        
    } catch (error) {
        console.error('Error updating audit statistics:', error);
    }
}

// Action Functions

/**
 * Executes sell all tickers command
 */
/**
 * Sell all tickers in the accumulator
 */
async function sellAllTickers() {
    try {
        // Get admin token (with caching)
        const token = getAdminToken('sell all tickers in accumulator');
        if (!token) return;
        
        console.log('Selling all tickers in accumulator');
        
        const response = await fetch('/admin/order/sell-all', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ 
                token: token
            }),
        });
        
        const result = await response.json().catch(() => ({}));
        
        if (response.ok) {
            const message = result.message || 'All sell orders processed successfully!';
            showSuccessMessage(message);
            console.log('✅ Successfully processed sell all orders');
            
            // Show details if available
            if (result.processed_tickers && result.processed_tickers.length > 0) {
                console.log(`Processed tickers: ${result.processed_tickers.join(', ')}`);
            }
            if (result.failed_tickers && result.failed_tickers.length > 0) {
                console.warn(`Failed tickers:`, result.failed_tickers);
            }
        } else {
            const errorDetail = result.detail || `Server responded with status ${response.status}`;
            // If token is invalid, clear cache
            if (response.status === 403) {
                adminTokenCache.token = null;
                adminTokenCache.expiry = 0;
            }
            showErrorMessage(`Error selling all tickers: ${errorDetail}`);
            console.error('❌ Failed to sell all tickers:', errorDetail);
        }
        
    } catch (error) {
        const errorMsg = `Client-side error selling all tickers: ${error.message}`;
        showErrorMessage(errorMsg);
        console.error(errorMsg, error);
    }
}

/**
 * Clear the sell all accumulator
 */
async function clearAccumulator() {
    try {
        // Get admin token (with caching)
        const token = getAdminToken('clear sell all accumulator');
        if (!token) return;
        
        console.log('Clearing sell all accumulator');
        
        const response = await fetch('/admin/order/clear-accumulator', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ 
                token: token
            }),
        });
        
        const result = await response.json().catch(() => ({}));
        
        if (response.ok) {
            const message = result.message || 'Sell all accumulator cleared successfully!';
            showSuccessMessage(message);
            console.log('✅ Successfully cleared accumulator');
        } else {
            const errorDetail = result.detail || `Server responded with status ${response.status}`;
            // If token is invalid, clear cache
            if (response.status === 403) {
                adminTokenCache.token = null;
                adminTokenCache.expiry = 0;
            }
            showErrorMessage(`Error clearing accumulator: ${errorDetail}`);
            console.error('❌ Failed to clear accumulator:', errorDetail);
        }
        
    } catch (error) {
        const errorMsg = `Client-side error clearing accumulator: ${error.message}`;
        showErrorMessage(errorMsg);
        console.error(errorMsg, error);
    }
}

/**
 * Copies current URL to clipboard
 */
async function copyCurrentUrl() {
    // Use correct ID from HTML - finviz-url
    const currentUrlElement = document.getElementById('finviz-url');
    const currentUrl = currentUrlElement?.textContent || '';
    
    if (!currentUrl || currentUrl === 'N/A') {
        showStatusMessage(document.getElementById('status-message-box'), 'No URL to copy', 'error');
        return;
    }
    
    try {
        await navigator.clipboard.writeText(currentUrl);
        
        // Visual feedback - using correct ID
        const copyButton = document.getElementById('copy-url-btn');
        if (copyButton) {
            const originalContent = copyButton.innerHTML;
            copyButton.innerHTML = '✓ Copied!';
            copyButton.className = copyButton.className.replace('bg-slate-600', 'bg-green-600');
            
            setTimeout(() => {
                copyButton.innerHTML = originalContent;
                copyButton.className = copyButton.className.replace('bg-green-600', 'bg-slate-600');
            }, 2000);
        }
        
        console.log('URL copied to clipboard:', currentUrl);
    } catch (error) {
        console.error('Failed to copy URL:', error);
        showStatusMessage(document.getElementById('status-message-box'), 'Failed to copy URL to clipboard', 'error');
    }
}

/**
 * Autofills URL field with current URL
 */
function autofillCurrentUrl() {
    // Use correct ID from HTML - finviz-url
    const currentUrlElement = document.getElementById('finviz-url');
    const currentUrl = currentUrlElement?.textContent || '';
    
    if (!currentUrl || currentUrl === 'N/A') {
        showStatusMessage(document.getElementById('status-message-box'), 'No URL to autofill', 'error');
        return;
    }
    
    const newUrlInput = document.getElementById('new-finviz-url');
    if (newUrlInput) {
        newUrlInput.value = currentUrl;
        console.log('URL autofilled:', currentUrl);        
        // Visual feedback
        newUrlInput.focus();
        newUrlInput.select();
    }
}

/**
 * Applies filters to audit log
 */
function applyAuditFilters() {
    console.log('Applying audit filters...');
    
    try {
        // Get filter values
        const searchTickerInput = document.getElementById('audit-search-ticker');
        const statusFilterSelect = document.getElementById('audit-status-filter');
        const locationFilterSelect = document.getElementById('audit-location-filter');
        const startDateInput = document.getElementById('audit-filter-start');
        const endDateInput = document.getElementById('audit-filter-end');
        
        const searchTicker = searchTickerInput?.value?.trim().toLowerCase() || '';
        const statusFilter = statusFilterSelect?.value || 'all';
        const locationFilter = locationFilterSelect?.value || 'all';
        const startDate = startDateInput?.value || '';
        const endDate = endDateInput?.value || '';
        
        // Get all entries - use correct ID
        const auditContainer = document.getElementById('audit-log-container');
        const entries = auditContainer?.querySelectorAll('.audit-entry') || [];
        
        console.log('Filtering', entries.length, 'entries with:', { 
            searchTicker, statusFilter, locationFilter, startDate, endDate 
        });
        
        let visibleCount = 0;
        
        entries.forEach(entry => {
            try {
                const entryData = JSON.parse(entry.getAttribute('data-entry') || '{}');
                const ticker = (entryData.ticker || entryData.normalised_ticker || '').toLowerCase();
                const signalId = (entryData.signal_id || '').toLowerCase();
                const status = (entryData.status || '').toLowerCase();
                const location = (entryData.location || '').toLowerCase();
                const entryTimestamp = entryData.timestamp || entryData.updated_at;
                
                let shouldShow = true;
                
                // Filter by ticker or signal ID
                if (searchTicker) {
                    if (!ticker.includes(searchTicker) && !signalId.includes(searchTicker)) {
                        shouldShow = false;
                    }
                }
                
                // Filter by status
                if (statusFilter !== 'all' && !status.includes(statusFilter.toLowerCase())) {
                    shouldShow = false;
                }
                
                // Filter by location
                if (locationFilter !== 'all' && !location.includes(locationFilter.toLowerCase())) {
                    shouldShow = false;
                }
                
                // Filter by date range
                if (entryTimestamp && (startDate || endDate)) {
                    try {
                        const entryDate = new Date(entryTimestamp);
                        
                        if (startDate) {
                            const startDateTime = new Date(startDate);
                            if (entryDate < startDateTime) {
                                shouldShow = false;
                            }
                        }
                        
                        if (endDate) {
                            const endDateTime = new Date(endDate);
                            if (entryDate > endDateTime) {
                                shouldShow = false;
                            }
                        }
                    } catch (dateError) {
                        console.warn('Error parsing date for entry:', entryTimestamp, dateError);
                    }
                }
                
                // Show/hide entry
                entry.style.display = shouldShow ? 'block' : 'none';
                if (shouldShow) visibleCount++;
                
            } catch (parseError) {
                console.warn('Error parsing entry data:', parseError);
                entry.style.display = 'none';
            }
        });
        
        console.log('Showing', visibleCount, 'entries after filtering');
        
        // Show/hide empty message based on filtered results - use correct ID
        const emptyMessage = document.getElementById('audit-log-empty-message');
        const hasVisibleEntries = visibleCount > 0;
        if (emptyMessage) {
            if (hasVisibleEntries) {
                emptyMessage.style.display = 'none';
            } else {
                emptyMessage.style.display = 'block';
                emptyMessage.textContent = 'No audit entries match the current filters.';
            }
        }
        
        // Update statistics after applying filters
        updateAuditStatistics();
        
    } catch (error) {
        console.error('Error applying audit filters:', error);
    }
}

/**
 * Clears all audit filters and shows all entries
 */
function clearAuditFilters() {
    console.log('Clearing audit filters...');
    
    try {
        // Clear filter inputs
        const searchTickerInput = document.getElementById('audit-search-ticker');
        const statusFilterSelect = document.getElementById('audit-status-filter');
        const locationFilterSelect = document.getElementById('audit-location-filter');
        const startDateInput = document.getElementById('audit-filter-start');
        const endDateInput = document.getElementById('audit-filter-end');
        
        if (searchTickerInput) searchTickerInput.value = '';
        if (statusFilterSelect) statusFilterSelect.value = 'all';
        if (locationFilterSelect) locationFilterSelect.value = 'all';
        if (startDateInput) startDateInput.value = '';
        if (endDateInput) endDateInput.value = '';
        
        // Show all entries
        const auditContainer = document.getElementById('audit-log-container');
        const entries = auditContainer?.querySelectorAll('.audit-entry') || [];
        
        let visibleCount = 0;
        entries.forEach(entry => {
            entry.style.display = 'block';
            visibleCount++;
        });
        
        console.log('Showing all', visibleCount, 'entries after clearing filters');
        
        // Update empty message
        const emptyMessage = document.getElementById('audit-log-empty-message');
        if (emptyMessage) {
            emptyMessage.style.display = visibleCount > 0 ? 'none' : 'block';
            emptyMessage.textContent = 'No audit logs available for the selected period.';
        }
        
        // Update statistics after applying filters
        updateAuditStatistics();
        
    } catch (error) {
        console.error('Error clearing audit filters:', error);
    }
}

/**
 * Updates audit charts with current audit log data
 */
window.updateAuditCharts = function updateAuditCharts() {
    try {
        const auditContainer = document.getElementById('audit-log-container');
        const entries = auditContainer?.querySelectorAll('.audit-entry') || [];
        
        // Clear the status tracker and rebuild it from current entries
        window.signalStatusTracker = {};
        
        // Initialize counters for pie chart (status distribution)
        const statusCounts = {
            forwarded_success: 0,
            approved: 0,
            rejected: 0,
            forwarding_http_error: 0,
            forwarding_timeout_error: 0,
            processing: 0,
            discarded: 0
        };
        
        // Initialize data for line chart (signals over time)
        const timeData = [];
        
        entries.forEach(entry => {
            if (entry.style.display !== 'none') { // Only count visible entries
                try {
                    const entryData = JSON.parse(entry.getAttribute('data-entry') || '{}');
                    const status = (entryData.status || '').toLowerCase();
                    const timestamp = entryData.timestamp;
                    const signalId = entryData.signal_id;
                    
                    // Track the current status for this signal
                    if (signalId) {
                        window.signalStatusTracker[signalId] = status;
                    }                      // Count status for pie chart - FIXED: Added all missing status mappings
                    if (status === 'forwarded_success') {
                        statusCounts.forwarded_success++;
                    } else if (status === 'approved' || status === 'queued_forwarding' || status === 'forwarding') {
                        statusCounts.approved++; // FIXED: All approved states go to approved count
                    } else if (status === 'rejected') {
                        statusCounts.rejected++;
                    } else if (status === 'forwarded_http_error' || status === 'forwarding_http_error') {
                        statusCounts.forwarding_http_error++;
                    } else if (status === 'forwarded_timeout' || status === 'forwarded_timeout_error' || status === 'forwarding_timeout_error') {
                        statusCounts.forwarding_timeout_error++;
                    } else if (status.includes('processing') || status === 'queued_processing') {
                        statusCounts.processing++; // FIXED: Removed forwarding states from processing
                    } else if (status === 'received') {
                        // Received signals are essentially "in processing" from UI perspective
                        statusCounts.processing++;
                    } else if (status === 'forwarded_generic_error') {
                        // Generic errors count as HTTP errors for chart simplicity
                        statusCounts.forwarding_http_error++;
                    } else if (status === 'discarded') {
                        statusCounts.discarded++;
                    } else {
                        // Log unknown status and categorize as discarded
                        console.warn(`⚠️ Unknown signal status encountered: "${status}" - categorizing as discarded`);
                        statusCounts.discarded++;
                    }
                    
                    // Collect data for line chart
                    if (timestamp) {
                        timeData.push({
                            timestamp: new Date(timestamp),
                            status: status,
                            entryData: entryData
                        });
                    }
                } catch (parseError) {
                    console.warn('Error parsing entry data for charts:', parseError);
                }
            }
        });
        
        // Update pie chart if it exists
        if (window.statusPieChartInstance) {
            updateStatusPieChart(statusCounts);
        }
        
        // Update line chart if it exists  
        if (window.signalsOverTimeChartInstance) {
            updateSignalsOverTimeChart(timeData);
        }
        
        console.log('Updated audit charts with status counts:', statusCounts);
        console.log('Signal status tracker has', Object.keys(window.signalStatusTracker).length, 'signals');
        
    } catch (error) {
        console.error('Error updating audit charts:', error);
    }
}

/**
 * Updates the status pie chart with new data
 * @param {Object} statusCounts - Object with status counts
 */
function updateStatusPieChart(statusCounts) {
    try {
        const chart = window.statusPieChartInstance;
        if (!chart) {
            console.warn('Status pie chart instance not found');
            return;
        }
        
        // Update data values
        chart.data.datasets[0].data = [
            statusCounts.forwarded_success || 0,
            statusCounts.approved || 0, 
            statusCounts.rejected || 0,
            statusCounts.forwarding_http_error || 0,
            statusCounts.forwarding_timeout_error || 0,
            statusCounts.processing || 0,
            statusCounts.discarded || 0
        ];
        
        // Update the chart
        chart.update('none'); // Use 'none' animation for better performance
        
    } catch (error) {
        console.error('Error updating status pie chart:', error);
    }
}

/**
 * Updates the signals over time line chart with new data
 * @param {Array} timeData - Array of time-series data points
 */
function updateSignalsOverTimeChart(timeData) {
    try {
        const chart = window.signalsOverTimeChartInstance;
        if (!chart) {
            console.warn('Signals over time chart instance not found');
            return;
        }
        
        // Sort data by timestamp
        timeData.sort((a, b) => a.timestamp - b.timestamp);
        
        // Group data by time intervals (e.g., every 5 minutes)
        const groupedData = groupDataByTimeInterval(timeData, 5 * 60 * 1000); // 5 minutes in ms
        
        // Prepare chart data
        const labels = [];
        const totalSignals = [];
        const successfulSignals = [];
        const rejectedSignals = [];
        const errorSignals = [];
        
        Object.keys(groupedData).forEach(timeKey => {
            const group = groupedData[timeKey];
            const timestamp = new Date(parseInt(timeKey));
            
            labels.push(timestamp.toLocaleTimeString('pt-BR', { 
                hour: '2-digit', 
                minute: '2-digit' 
            }));
            
            totalSignals.push(group.total);
            successfulSignals.push(group.success);
            rejectedSignals.push(group.rejected);
            errorSignals.push(group.errors);
        });
        
        // Keep only last 20 data points for performance
        if (labels.length > 20) {
            const keepCount = 20;
            labels.splice(0, labels.length - keepCount);
            totalSignals.splice(0, totalSignals.length - keepCount);
            successfulSignals.splice(0, successfulSignals.length - keepCount);
            rejectedSignals.splice(0, rejectedSignals.length - keepCount);
            errorSignals.splice(0, errorSignals.length - keepCount);
        }
        
        // Update chart data
        chart.data.labels = labels;
        chart.data.datasets[0].data = totalSignals;
        chart.data.datasets[1].data = successfulSignals;
        chart.data.datasets[2].data = rejectedSignals;
        chart.data.datasets[3].data = errorSignals;
        
        // Update the chart
        chart.update('none'); // Use 'none' animation for better performance
        
    } catch (error) {
        console.error('Error updating signals over time chart:', error);
    }
}

/**
 * Groups time-series data by time intervals
 * @param {Array} timeData - Array of data points with timestamps
 * @param {number} intervalMs - Interval in milliseconds
 * @returns {Object} Grouped data by time intervals
 */
function groupDataByTimeInterval(timeData, intervalMs) {
    const grouped = {};
    
    timeData.forEach(item => {
        // Round timestamp to interval boundary
        const intervalStart = Math.floor(item.timestamp.getTime() / intervalMs) * intervalMs;
        
        if (!grouped[intervalStart]) {
            grouped[intervalStart] = {
                total: 0,
                success: 0,
                rejected: 0,
                errors: 0
            };
        }
        
        grouped[intervalStart].total++;
        
        const status = item.status.toLowerCase();
        if (status === 'forwarded_success') {
            grouped[intervalStart].success++;
        } else if (status === 'rejected') {
            grouped[intervalStart].rejected++;
        } else if (status.includes('error') || status.includes('timeout')) {
            grouped[intervalStart].errors++;
        }
    });
    
    return grouped;
}

// Global object to track signal statuses for proper chart updates
if (!window.signalStatusTracker) {
    window.signalStatusTracker = {};
    console.log('🔧 Initialized signal status tracker');
}

/**
 * Updates charts when a new audit entry is added or updated
 * @param {Object} entry - The audit entry data
 */
function updateChartsWithEntry(entry) {
    if (!entry) return;
    
    console.log('Updating charts with entry:', entry.signal_id);
    
    try {
        // Update pie chart if it exists
        if (window.statusPieChartInstance) {
            updatePieChartWithEntry(entry);
        }
        
        // Update line chart if it exists
        if (window.signalsOverTimeChartInstance) {
            updateLineChartWithEntry(entry);
        }
        
    } catch (error) {
        console.error('Error updating charts with entry:', error);
    }
}

/**
 * Updates pie chart with a single entry
 * @param {Object} entry - The audit entry data
 */
function updatePieChartWithEntry(entry) {
    try {
        const chart = window.statusPieChartInstance;
        const signalId = entry.signal_id;
        const currentStatus = (entry.status || '').toLowerCase();
        
        if (!chart) {
            console.warn('Status pie chart instance not found');
            return;
        }
        
        if (!signalId) {
            console.warn('Entry missing signal_id, cannot track status changes');
            return;
        }
        
        console.log(`🔄 Updating chart for signal ${signalId.slice(0, 8)}... (${currentStatus})`);
          // Get previous status for this signal
        const previousStatus = window.signalStatusTracker[signalId];
        console.log(`📊 Previous status: ${previousStatus || 'none'}, Current status: ${currentStatus}`);
        
        // CRITICAL DEBUG: Log tracker state
        console.log(`🎯 Tracker state for ${signalId.slice(0, 8)}...:`);
        console.log(`   Has previous status: ${!!previousStatus}`);
        console.log(`   Previous was processing: ${previousStatus && previousStatus.includes('processing')}`);
        console.log(`   Current is processing: ${currentStatus.includes('processing')}`);
        console.log(`   Should decrement processing: ${previousStatus && previousStatus.includes('processing') && !currentStatus.includes('processing')}`);        // Map status to chart data index - FIXED: Added all missing status mappings
        function getStatusIndex(status) {
            if (status === 'forwarded_success') return 0;
            if (status === 'approved' || status === 'queued_forwarding' || status === 'forwarding') return 1; // FIXED: Approved (Pending)
            if (status === 'rejected') return 2;
            if (status === 'forwarded_http_error' || status === 'forwarding_http_error' || status === 'forwarded_generic_error') return 3;
            if (status === 'forwarded_timeout' || status === 'forwarded_timeout_error' || status === 'forwarding_timeout_error') return 4;
            if (status.includes('processing') || status === 'queued_processing' || status === 'received') return 5; // FIXED: Removed forwarding states
            if (status === 'discarded') return 6;
            
            // Log unknown status for debugging
            console.warn(`⚠️ Unknown signal status in getStatusIndex: "${status}" - categorizing as discarded`);
            return 6; // discarded/unknown
        }
        
        // Get current chart data for logging
        const currentData = [...chart.data.datasets[0].data];
        console.log('📈 Chart data before update:', {
            'Successfully Forwarded': currentData[0],
            'Approved (Pending)': currentData[1],
            'Rejected': currentData[2],
            'HTTP Errors': currentData[3],
            'Timeout Errors': currentData[4],
            'Processing': currentData[5],
            'Discarded': currentData[6]
        });
          // If this signal had a previous status, decrement that counter
        if (previousStatus && previousStatus !== currentStatus) {
            console.log(`🔄 DECREMENT LOGIC: Previous "${previousStatus}" !== Current "${currentStatus}"`);
            const previousIndex = getStatusIndex(previousStatus);
            console.log(`📍 Previous status index: ${previousIndex}`);
            if (previousIndex >= 0 && previousIndex < chart.data.datasets[0].data.length) {
                const oldValue = chart.data.datasets[0].data[previousIndex];
                chart.data.datasets[0].data[previousIndex] = Math.max(0, oldValue - 1);
                console.log(`➖ Decremented ${previousStatus} count from ${oldValue} to ${chart.data.datasets[0].data[previousIndex]} (index ${previousIndex})`);
                
                // CRITICAL DEBUG: Specific processing decrement logging
                if (previousIndex === 5) { // Processing index
                    console.log(`🚨 PROCESSING DECREMENTED: ${oldValue} → ${chart.data.datasets[0].data[previousIndex]}`);
                }
            } else {
                console.warn(`⚠️ Invalid previous index ${previousIndex} for status "${previousStatus}"`);
            }
        } else {
            console.log(`ℹ️ No decrement needed: Previous="${previousStatus}", Current="${currentStatus}"`);
        }
        
        // Increment current status counter
        const currentIndex = getStatusIndex(currentStatus);
        if (currentIndex >= 0 && currentIndex < chart.data.datasets[0].data.length) {
            const oldValue = chart.data.datasets[0].data[currentIndex];
            chart.data.datasets[0].data[currentIndex]++;
            console.log(`➕ Incremented ${currentStatus} count from ${oldValue} to ${chart.data.datasets[0].data[currentIndex]} (index ${currentIndex})`);
        }
        
        // Update the status tracker
        window.signalStatusTracker[signalId] = currentStatus;
        console.log(`💾 Updated tracker for ${signalId.slice(0, 8)}... -> ${currentStatus}`);
        
        // Log final chart data
        const finalData = [...chart.data.datasets[0].data];
        console.log('📊 Chart data after update:', {
            'Successfully Forwarded': finalData[0],
            'Approved (Pending)': finalData[1],
            'Rejected': finalData[2],
            'HTTP Errors': finalData[3],
            'Timeout Errors': finalData[4],
            'Processing': finalData[5],
            'Discarded': finalData[6]
        });
        
        // Update the chart
        chart.update('none');        // Update the chart
        chart.update('none');
        
    } catch (error) {
        console.error('Error updating pie chart with entry:', error);
    }
}

/**
 * Updates line chart with a single entry
 * @param {Object} entry - The audit entry data  
 */
function updateLineChartWithEntry(entry) {
    try {
        const chart = window.signalsOverTimeChartInstance;
        const timestamp = new Date(entry.timestamp || Date.now());
        const status = (entry.status || '').toLowerCase();
        
        // Add new data point
        const timeLabel = timestamp.toLocaleTimeString('pt-BR', { 
            hour: '2-digit', 
            minute: '2-digit' 
        });
        
        chart.data.labels.push(timeLabel);
        
        // Update datasets
        const lastTotal = chart.data.datasets[0].data.slice(-1)[0] || 0;
        const lastSuccess = chart.data.datasets[1].data.slice(-1)[0] || 0;
        const lastRejected = chart.data.datasets[2].data.slice(-1)[0] || 0;
        const lastErrors = chart.data.datasets[3].data.slice(-1)[0] || 0;
        
        chart.data.datasets[0].data.push(lastTotal + 1); // Total always increases
        
        if (status === 'forwarded_success') {
            chart.data.datasets[1].data.push(lastSuccess + 1);
            chart.data.datasets[2].data.push(lastRejected);
            chart.data.datasets[3].data.push(lastErrors);
        } else if (status === 'rejected') {
            chart.data.datasets[1].data.push(lastSuccess);
            chart.data.datasets[2].data.push(lastRejected + 1);
            chart.data.datasets[3].data.push(lastErrors);
        } else if (status.includes('error') || status.includes('timeout')) {
            chart.data.datasets[1].data.push(lastSuccess);
            chart.data.datasets[2].data.push(lastRejected);
            chart.data.datasets[3].data.push(lastErrors + 1);
        } else {
            // Keep previous values for other statuses
            chart.data.datasets[1].data.push(lastSuccess);
            chart.data.datasets[2].data.push(lastRejected);
            chart.data.datasets[3].data.push(lastErrors);
        }
        
        // Keep only last 20 points for performance
        if (chart.data.labels.length > 20) {
            chart.data.labels.shift();
            chart.data.datasets.forEach(dataset => dataset.data.shift());
        }
        
        chart.update('none');
        
    } catch (error) {
        console.error('Error updating line chart with entry:', error);
    }
}

/**
 * Enhanced version of loadAuditEntries that also updates charts
 * This function wraps the original loadAuditEntries and adds chart updates
 */
function loadAuditEntriesWithCharts(auditEntries) {
    // Call the original loadAuditEntries function directly
    if (!Array.isArray(auditEntries)) {
        console.warn('Invalid audit entries data provided:', typeof auditEntries, auditEntries);
        return;
    }
    
    console.log('Loading', auditEntries.length, 'audit entries with chart updates');
    
    try {
        // Use correct ID from HTML - audit-log-container
        const container = document.getElementById('audit-log-container');
        if (!container) {
            console.error('Audit log container not found');
            return;
        }
        
        // Clear container but preserve the empty message element
        const emptyMessage = document.getElementById('audit-log-empty-message');
        const children = Array.from(container.children);
        children.forEach(child => {
            if (child.id !== 'audit-log-empty-message') {
                child.remove();
            }
        });
        
        if (auditEntries.length === 0) {
            // Show empty message
            if (emptyMessage) {
                emptyMessage.style.display = 'block';
                emptyMessage.textContent = 'No audit logs available for the selected period.';
            }
            console.log('No audit entries to display');
        } else {
            // Hide empty message
            if (emptyMessage) {
                emptyMessage.style.display = 'none';
            }
              console.log('Sample audit entry:', auditEntries[0]);
            
            // Use lazy loading system for better performance with large datasets
            console.log(`Preparing to load ${auditEntries.length} audit entries with lazy loading and charts`);
            initializeLazyLoadingAuditEntries(auditEntries);
        }
        
        // Update statistics and charts after loading entries
        updateAuditStatistics();
        
        // Update charts with loaded data
        if (window.updateAuditCharts) {
            window.updateAuditCharts();
        }
        
    } catch (error) {
        console.error('Error loading audit entries:', error);
    }
}

// Make the enhanced function globally available
window.loadAuditEntriesWithCharts = loadAuditEntriesWithCharts;

/**
 * Initialize charts with current audit data when page loads
 */
window.initializeChartsWithData = function initializeChartsWithData() {
    // Wait a bit for the charts to be initialized
    setTimeout(() => {
        if (window.updateAuditCharts) {
            window.updateAuditCharts();
        }
    }, 500);
};

// Periodic cleanup of status tracker (every 5 minutes)
setInterval(cleanupStatusTracker, 5 * 60 * 1000);

// Make updateChartsWithEntry globally available
window.updateChartsWithEntry = updateChartsWithEntry;

/**
 * Cleans up old signals from the status tracker
 * This should be called periodically to prevent memory leaks
 */
function cleanupStatusTracker() {
    try {
        const auditContainer = document.getElementById('audit-log-container');
        const entries = auditContainer?.querySelectorAll('.audit-entry') || [];
        
        // Get all currently visible signal IDs
        const currentSignalIds = new Set();
        entries.forEach(entry => {
            if (entry.style.display !== 'none') {
                try {
                    const entryData = JSON.parse(entry.getAttribute('data-entry') || '{}');
                    if (entryData.signal_id) {
                        currentSignalIds.add(entryData.signal_id);
                    }
                } catch (e) {
                    // Ignore parse errors
                }
            }
        });
        
        // Remove signals from tracker that are no longer visible
        const trackerKeys = Object.keys(window.signalStatusTracker);
        let cleanedCount = 0;
        
        trackerKeys.forEach(signalId => {
            if (!currentSignalIds.has(signalId)) {
                delete window.signalStatusTracker[signalId];
                cleanedCount++;
            }
        });
        
        if (cleanedCount > 0) {
            console.log(`Cleaned up ${cleanedCount} old signals from status tracker`);
        }
        
    } catch (error) {
        console.error('Error cleaning up status tracker:', error);
    }
}

/**
 * Debug function to check the current state of the signal status tracker
 * Can be called from browser console: checkSignalTracker()
 */
window.checkSignalTracker = function() {
    console.log('🔍 Signal Status Tracker Debug Info');
    console.log('===================================');
    
    if (!window.signalStatusTracker) {
        console.log('❌ Tracker not initialized');
        return;
    }
    
    const trackerKeys = Object.keys(window.signalStatusTracker);
    console.log(`📊 Tracking ${trackerKeys.length} signals`);
    
    if (trackerKeys.length > 0) {
        console.log('📋 Current signals and their status:');
        trackerKeys.forEach(signalId => {
            const status = window.signalStatusTracker[signalId];
            console.log(`  ${signalId.slice(0, 8)}... -> ${status}`);
        });
        
        // Count signals by status
        const statusCounts = {};
        trackerKeys.forEach(signalId => {
            const status = window.signalStatusTracker[signalId];
            statusCounts[status] = (statusCounts[status] || 0) + 1;
        });
        
        console.log('📈 Status distribution in tracker:');
        Object.entries(statusCounts).forEach(([status, count]) => {
            console.log(`  ${status}: ${count}`);
        });
    }
    
    // Show current chart data
    if (window.statusPieChartInstance) {
        const chartData = window.statusPieChartInstance.data.datasets[0].data;
        console.log('📊 Current chart data:');
        console.log('  Successfully Forwarded:', chartData[0]);
        console.log('  Approved (Pending):', chartData[1]);
        console.log('  Rejected:', chartData[2]);
        console.log('  HTTP Errors:', chartData[3]);
        console.log('  Timeout Errors:', chartData[4]);
        console.log('  Processing:', chartData[5]);
        console.log('  Discarded:', chartData[6]);
    } else {
        console.log('📊 Chart instance not found');
    }
    
    // Show visible entries
    const container = document.getElementById('audit-log-container');
    if (container) {
        const entries = container.querySelectorAll('.audit-entry');
        console.log(`👁️ ${entries.length} visible entries in audit log`);
        
        const visibleSignals = {};
        entries.forEach(entry => {
            try {
                const entryData = JSON.parse(entry.getAttribute('data-entry') || '{}');
                if (entryData.signal_id) {
                    visibleSignals[entryData.signal_id] = entryData.status;
                }
            } catch (e) {
                // ignore
            }
        });
        
        console.log(`📋 ${Object.keys(visibleSignals).length} unique signals visible`);
        
        // Check for discrepancies
        const visibleSignalIds = Object.keys(visibleSignals);
        const trackedSignalIds = Object.keys(window.signalStatusTracker);
        
        const onlyInVisible = visibleSignalIds.filter(id => !trackedSignalIds.includes(id));
        const onlyInTracker = trackedSignalIds.filter(id => !visibleSignalIds.includes(id));
        
        if (onlyInVisible.length > 0) {
            console.log('⚠️ Signals visible but not tracked:', onlyInVisible.map(id => id.slice(0, 8) + '...'));
        }
        
        if (onlyInTracker.length > 0) {
            console.log('⚠️ Signals tracked but not visible:', onlyInTracker.map(id => id.slice(0, 8) + '...'));
        }
        
        if (onlyInVisible.length === 0 && onlyInTracker.length === 0) {
            console.log('✅ Tracker and visible entries are in sync');
        }
    }
};

console.log('Admin functions loaded successfully');

/**
 * Sell individual ticker immediately
 * @param {string} ticker - Ticker symbol to sell
 */
async function sellIndividualTicker(ticker) {
    try {
        // Get admin token (with caching)
        const token = getAdminToken(`sell ${ticker} immediately`);
        if (!token) return;
        
        console.log(`Selling individual ticker: ${ticker}`);
        
        const response = await fetch('/admin/order/sell-individual', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ 
                token: token,
                ticker: ticker
            }),
        });
        
        const result = await response.json().catch(() => ({}));
        
        if (response.ok) {
            const message = result.message || `Sell order for ${ticker} executed successfully!`;
            showSuccessMessage(message);
            console.log(`✅ Successfully sold ${ticker}`);
        } else {
            const errorDetail = result.detail || `Server responded with status ${response.status}`;
            // If token is invalid, clear cache
            if (response.status === 403) {
                adminTokenCache.token = null;
                adminTokenCache.expiry = 0;
            }
            showErrorMessage(`Error selling ${ticker}: ${errorDetail}`);
            console.error(`❌ Failed to sell ${ticker}:`, errorDetail);
        }
        
    } catch (error) {
        const errorMsg = `Client-side error selling ${ticker}: ${error.message}`;
        showErrorMessage(errorMsg);
        console.error(errorMsg, error);
    }
}

/**
 * Add ticker to sell all accumulator list
 * @param {string} ticker - Ticker symbol to add to accumulator
 */
async function addToSellAllList(ticker) {
    try {
        // Get admin token (with caching)
        const token = getAdminToken(`add ${ticker} to sell all accumulator`);
        if (!token) return;
        
        console.log(`Adding ${ticker} to sell all accumulator`);
        
        const response = await fetch('/admin/order/add-to-accumulator', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ 
                token: token,
                ticker: ticker
            }),
        });
        
        const result = await response.json().catch(() => ({}));
        
        if (response.ok) {
            const message = result.message || `${ticker} added to sell all accumulator!`;
            showSuccessMessage(message);
            console.log(`✅ Successfully added ${ticker} to accumulator`);
        } else {
            const errorDetail = result.detail || `Server responded with status ${response.status}`;
            // If token is invalid, clear cache
            if (response.status === 403) {
                adminTokenCache.token = null;
                adminTokenCache.expiry = 0;
            }
            showErrorMessage(`Error adding ${ticker} to accumulator: ${errorDetail}`);
            console.error(`❌ Failed to add ${ticker} to accumulator:`, errorDetail);
        }
        
    } catch (error) {
        const errorMsg = `Client-side error adding ${ticker} to accumulator: ${error.message}`;
        showErrorMessage(errorMsg);
        console.error(errorMsg, error);
    }
}

/**
 * Show success message to user
 * @param {string} message - Success message to display
 */
function showSuccessMessage(message) {
    // Try to find a status message container
    const statusContainer = document.getElementById('sell-order-status-message') || 
                           document.getElementById('status-message-box') ||
                           document.getElementById('general-status-message');
    
    if (statusContainer) {
        statusContainer.textContent = message;
        statusContainer.className = 'mt-3 p-3 rounded-md text-sm bg-green-600 text-green-100';
        statusContainer.style.display = 'block';
        
        // Hide after 5 seconds
        setTimeout(() => {
            statusContainer.style.display = 'none';
        }, 5000);
    } else {
        // Fallback to alert
        alert(message);
    }
}

/**
 * Show error message to user
 * @param {string} message - Error message to display
 */
function showErrorMessage(message) {
    // Try to find a status message container
    const statusContainer = document.getElementById('sell-order-status-message') || 
                           document.getElementById('status-message-box') ||
                           document.getElementById('general-status-message');
    
    if (statusContainer) {
        statusContainer.textContent = message;
        statusContainer.className = 'mt-3 p-3 rounded-md text-sm bg-red-600 text-red-100';
        statusContainer.style.display = 'block';
        
        // Hide after 7 seconds for errors
        setTimeout(() => {
            statusContainer.style.display = 'none';
        }, 7000);
    } else {
        // Fallback to alert
        alert(message);
    }
}

// Global token cache for admin actions (expires after 5 minutes)
let adminTokenCache = {
    token: null,
    expiry: 0
};

/**
 * Get admin token with caching
 * @param {string} action - Description of the action requiring token
 * @returns {string|null} - Admin token or null if cancelled
 */
function getAdminToken(action) {
    const now = Date.now();
    
    // Check if cached token is still valid (5 minutes)
    if (adminTokenCache.token && now < adminTokenCache.expiry) {
        return adminTokenCache.token;
    }
    
    // Request new token
    const token = prompt(`Enter admin token to ${action}:`);
    if (token) {
        // Cache token for 5 minutes
        adminTokenCache.token = token;
        adminTokenCache.expiry = now + (5 * 60 * 1000);
    }
    
    return token;
}

// Initialize event listeners when DOM is loaded
document.addEventListener('DOMContentLoaded', function() {
    // Sell All button
    const sellAllBtn = document.getElementById('sell-all-btn');
    if (sellAllBtn) {
        sellAllBtn.addEventListener('click', async function(e) {
            e.preventDefault();
            
            // Disable button and show loading
            const originalText = this.textContent;
            this.disabled = true;
            this.textContent = 'Processing...';
            this.classList.add('opacity-50', 'cursor-not-allowed');
            
            try {
                await sellAllTickers();
            } finally {
                // Re-enable button after action completes
                setTimeout(() => {
                    this.disabled = false;
                    this.textContent = originalText;
                    this.classList.remove('opacity-50', 'cursor-not-allowed');
                }, 2000);
            }
        });
    }
    
    // Clear Accumulator button
    const clearAccumulatorBtn = document.getElementById('clear-accumulator-btn');
    if (clearAccumulatorBtn) {
        clearAccumulatorBtn.addEventListener('click', async function(e) {
            e.preventDefault();
            
            // Confirm action
            const confirmed = confirm('Are you sure you want to clear the sell all accumulator? This action cannot be undone.');
            if (!confirmed) return;
            
            // Disable button and show loading
            const originalText = this.textContent;
            this.disabled = true;
            this.textContent = 'Clearing...';
            this.classList.add('opacity-50', 'cursor-not-allowed');
            
            try {
                await clearAccumulator();
            } finally {
                // Re-enable button after action completes
                setTimeout(() => {
                    this.disabled = false;
                    this.textContent = originalText;
                    this.classList.remove('opacity-50', 'cursor-not-allowed');
                }, 2000);
            }        });
    }
    
    // Show All Entries button
    const showAllBtn = document.getElementById('show-all-entries-btn');
    if (showAllBtn) {
        showAllBtn.addEventListener('click', function(e) {
            e.preventDefault();
            
            // Disable button and show loading
            const originalText = this.textContent;
            this.textContent = 'Loading...';
            this.disabled = true;
            
            // Load all remaining entries
            loadAllRemainingEntries();
            
            // Re-enable button (though it will be hidden)
            setTimeout(() => {
                this.textContent = originalText;
                this.disabled = false;
            }, 1000);
        });
    }
    
    // Lazy loading initialization - for audit entries
    const initialAuditEntries = []; // This should be populated with the initial set of audit entries
    initializeLazyLoadingAuditEntries(initialAuditEntries);
});

// === ENHANCED AUDIT TRAIL DEBUGGING AND MONITORING ===

/**
 * Comprehensive audit trail system health check
 * Call this function from browser console: auditTrailHealthCheck()
 */
window.auditTrailHealthCheck = function() {
    console.log('🏥 AUDIT TRAIL SYSTEM HEALTH CHECK');
    console.log('=' .repeat(50));
    
    // 1. Check signal status tracker
    const trackerSize = window.signalStatusTracker ? Object.keys(window.signalStatusTracker).length : 0;
    console.log(`📊 Signal Status Tracker: ${trackerSize} signals`);
    
    // 2. Check DOM entries
    const container = document.getElementById('audit-log-container');
    const domEntries = container ? container.querySelectorAll('.audit-entry').length : 0;
    console.log(`🎭 DOM Entries: ${domEntries} visible entries`);
    
    // 3. Check lazy loading state
    console.log(`📚 Lazy Loading: ${displayedEntriesCount}/${auditEntriesPool.length} entries loaded`);
    
    // 4. Check for inconsistencies
    const inconsistencies = [];
    
    if (trackerSize > 0 && domEntries === 0) {
        inconsistencies.push('❌ Tracker has data but DOM is empty');
    }
    
    if (domEntries > 0 && trackerSize === 0) {
        inconsistencies.push('❌ DOM has entries but tracker is empty');
    }
    
    if (Math.abs(trackerSize - domEntries) > 10) {
        inconsistencies.push(`⚠️ Large discrepancy: Tracker(${trackerSize}) vs DOM(${domEntries})`);
    }
    
    // 5. Check chart consistency
    const chart = window.statusPieChartInstance;
    if (chart) {
        const chartData = chart.data.datasets[0].data;
        const chartTotal = chartData.reduce((sum, val) => sum + (val || 0), 0);
        console.log(`📈 Chart Total: ${chartTotal} signals`);
        
        if (Math.abs(chartTotal - trackerSize) > 5) {
            inconsistencies.push(`⚠️ Chart inconsistency: Chart(${chartTotal}) vs Tracker(${trackerSize})`);
        }
    }
    
    // 6. Report health status
    if (inconsistencies.length === 0) {
        console.log('✅ SYSTEM HEALTHY - No inconsistencies detected');
    } else {
        console.log('🚨 ISSUES DETECTED:');
        inconsistencies.forEach(issue => console.log(`   ${issue}`));
    }
    
    // 7. Performance metrics
    const performanceMetrics = {
        total_signals_tracked: trackerSize,
        dom_entries_rendered: domEntries,
        lazy_loading_progress: `${displayedEntriesCount}/${auditEntriesPool.length}`,
        loading_state: isLoadingMoreEntries ? 'LOADING' : 'IDLE'
    };
    
    console.log('📊 Performance Metrics:', performanceMetrics);
    
    return {
        healthy: inconsistencies.length === 0,
        issues: inconsistencies,
        metrics: performanceMetrics
    };
};

/**
 * Force refresh audit trail data
 * Call this function to reload all audit data: refreshAuditTrail()
 */
window.refreshAuditTrail = function() {
    console.log('🔄 REFRESHING AUDIT TRAIL...');
    
    // Clear current state
    window.signalStatusTracker = {};
    auditEntriesPool = [];
    displayedEntriesCount = 0;
    
    // Clear DOM
    const container = document.getElementById('audit-log-container');
    if (container) {
        container.innerHTML = '<div id="audit-log-empty-message" class="text-slate-400 text-center py-8">Loading audit logs...</div>';
    }
    
    // Request fresh data
    if (socket && socket.readyState === WebSocket.OPEN) {
        socket.send(JSON.stringify({
            event: 'request_audit_log',
            data: {}
        }));
        console.log('✅ Audit log refresh requested via WebSocket');
    } else {
        console.log('❌ WebSocket not available for refresh');
    }
};

/**
 * Debug specific signal by ID
 */
window.debugSignal = function(signalId) {
    console.log(`🔍 DEBUGGING SIGNAL: ${signalId}`);
    console.log('-'.repeat(40));
    
    // Check in tracker
    const trackerStatus = window.signalStatusTracker?.[signalId] || 'NOT_FOUND';
    console.log(`📊 Tracker Status: ${trackerStatus}`);
    
    // Check in DOM
    const domEntry = document.querySelector(`[data-signal-id="${signalId}"]`);
    if (domEntry) {
        const entryData = JSON.parse(domEntry.getAttribute('data-entry') || '{}');
        console.log(`🎭 DOM Entry:`, entryData);
        console.log(`👁️ DOM Visible: ${domEntry.style.display !== 'none'}`);
    } else {
        console.log(`🎭 DOM Entry: NOT_FOUND`);
    }
    
    // Check in lazy loading pool
    const poolEntry = auditEntriesPool.find(entry => entry.signal_id === signalId);
    if (poolEntry) {
        console.log(`📚 Pool Entry:`, poolEntry);
    } else {
        console.log(`📚 Pool Entry: NOT_FOUND`);
    }
};

// Auto-run health check every 5 minutes in development
if (window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1') {
    setInterval(() => {
        const health = window.auditTrailHealthCheck();
        if (!health.healthy) {
            console.warn('🚨 Audit trail health check failed - consider running refreshAuditTrail()');
        }
    }, 5 * 60 * 1000);
}

// === LAZY LOADING SYSTEM FOR AUDIT ENTRIES ===

// Global variables for lazy loading (implicitly declared)
if (typeof auditEntriesPool === 'undefined') {
    auditEntriesPool = [];
}
if (typeof displayedEntriesCount === 'undefined') {
    displayedEntriesCount = 0;
}
if (typeof isLoadingMoreEntries === 'undefined') {
    isLoadingMoreEntries = false;
}

// Lazy loading configuration
const LAZY_LOADING_BATCH_SIZE = 50; // Load 50 entries at a time
const LAZY_LOADING_TRIGGER_THRESHOLD = 200; // Trigger when scrolled to within 200px of bottom

/**
 * Initialize lazy loading for audit entries
 * @param {Array} entries - All audit entries to be lazily loaded
 */
function initializeLazyLoadingAuditEntries(entries) {
    if (!Array.isArray(entries)) {
        console.error('Invalid entries provided to lazy loading system:', entries);
        return;
    }

    console.log(`🚀 Initializing lazy loading system with ${entries.length} entries`);
    
    // Store all entries in the pool
    auditEntriesPool = [...entries];
    displayedEntriesCount = 0;
    isLoadingMoreEntries = false;

    // Clear existing entries from DOM
    const container = document.getElementById('audit-log-container');
    if (!container) {
        console.error('Audit log container not found for lazy loading');
        return;
    }

    // Clear container but preserve empty message
    const emptyMessage = document.getElementById('audit-log-empty-message');
    const children = Array.from(container.children);
    children.forEach(child => {
        if (child.id !== 'audit-log-empty-message') {
            child.remove();
        }
    });

    // Load initial batch
    loadMoreAuditEntries(true);

    // Set up scroll listener for infinite loading
    setupLazyLoadingScrollListener(container);

    // Show/hide "Show All" button based on remaining entries
    updateShowAllButton();
}

/**
 * Load more audit entries (called by scroll trigger or show all button)
 * @param {boolean} isInitial - Whether this is the initial load
 */
function loadMoreAuditEntries(isInitial = false) {
    if (isLoadingMoreEntries && !isInitial) {
        return; // Prevent concurrent loading
    }

    const remainingEntries = auditEntriesPool.length - displayedEntriesCount;
    if (remainingEntries <= 0) {
        console.log('📚 No more entries to load');
        updateShowAllButton();
        return;
    }

    isLoadingMoreEntries = true;
    const batchSize = LAZY_LOADING_BATCH_SIZE;
    const startIndex = displayedEntriesCount;
    const endIndex = Math.min(startIndex + batchSize, auditEntriesPool.length);
    
    console.log(`📖 Loading entries ${startIndex + 1} to ${endIndex} of ${auditEntriesPool.length}`);

    const container = document.getElementById('audit-log-container');
    if (!container) {
        console.error('Audit log container not found');
        isLoadingMoreEntries = false;
        return;
    }

    // Get batch of entries to load
    const batchEntries = auditEntriesPool.slice(startIndex, endIndex);
    
    // Hide empty message if showing entries
    const emptyMessage = document.getElementById('audit-log-empty-message');
    if (emptyMessage && batchEntries.length > 0) {
        emptyMessage.style.display = 'none';
    }

    // Add each entry to DOM
    batchEntries.forEach(entry => {
        addSingleAuditEntryToDOM(entry, container);
    });

    // Update counters
    displayedEntriesCount = endIndex;
    isLoadingMoreEntries = false;

    console.log(`✅ Loaded batch: ${displayedEntriesCount}/${auditEntriesPool.length} entries now displayed`);

    // Update show all button visibility
    updateShowAllButton();

    // Update scroll info
    updateScrollInfo();
}

/**
 * Add a single audit entry to DOM (helper function for lazy loading)
 * @param {Object} entry - Audit entry data
 * @param {Element} container - Container element
 */
function addSingleAuditEntryToDOM(entry, container) {
    // Create entry element using the same logic as addAuditEntry but without the container search
    const timestamp = formatTimestamp(entry.timestamp);
    const status = entry.status || 'unknown';
    const statusDisplay = entry.status_display || status;
    const ticker = entry.ticker || entry.normalised_ticker || 'N/A';
    const action = entry.action || 'N/A';
    const details = entry.details || '';
    const signalId = entry.signal_id || 'N/A';
    const eventsCount = entry.events_count || 0;
    const location = entry.location || 'unknown';

    // Update signal tracker
    if (entry.signal_id) {
        if (!window.signalStatusTracker) {
            window.signalStatusTracker = {};
        }
        window.signalStatusTracker[entry.signal_id] = entry.status;
    }

    // Check if manual order
    const isManualOrder = (
        entry.worker_id && (
            entry.worker_id.includes('ADMIN-MANUAL') || 
            entry.worker_id.includes('ADMIN-SELL-ALL') || 
            entry.worker_id.includes('ADMIN-QUEUE')
        )
    ) || (
        details && details.includes('📋 MANUAL')
    );

    let manualClass = '';
    if (isManualOrder) {
        manualClass = 'border-blue-500/30 bg-slate-800/80';
    }

    // Extract signal data for display
    let signalData = {};
    if (entry.original_signal && typeof entry.original_signal === 'object') {
        signalData = entry.original_signal;
    } else {
        signalData = {
            side: entry.side,
            action: entry.action,
            price: entry.price,
            time: entry.time,
            volume: entry.volume,
            quantity: entry.quantity,
            shares: entry.shares,
            size: entry.size
        };
    }

    const signalSide = signalData.side || signalData.action || 'N/A';
    const signalPrice = signalData.price ? `$${parseFloat(signalData.price).toFixed(2)}` : 'N/A';
    const signalTime = signalData.time ? formatTimestamp(signalData.time) : 'N/A';
    const signalVolume = signalData.volume || signalData.quantity || signalData.shares || signalData.size || 'N/A';

    // Status colors
    let statusColor = 'text-slate-400';
    let locationBadge = location.replace('_', ' ').toUpperCase();
    
    if (status.includes('success') || status.includes('approved') || status === 'forwarded_success') {
        statusColor = 'text-green-400';
    } else if (status.includes('error') || status.includes('failed') || status.includes('rejected') || status.includes('timeout')) {
        statusColor = 'text-red-400';
    } else if (status.includes('processing') || status.includes('forwarding') || status.includes('queued')) {
        statusColor = 'text-yellow-400';
    }

    let manualBadge = '';
    if (isManualOrder) {
        manualBadge = '<span class="text-xs text-blue-300 bg-blue-900 px-2 py-1 rounded font-semibold">📋 MANUAL</span>';
    }

    const allowActions = (ticker && ticker !== 'N/A' && ticker.trim() !== '');

    // Create entry element
    const entryElement = document.createElement('div');
    entryElement.className = `audit-entry bg-slate-800 rounded-lg p-3 border border-slate-700 mb-2 ${manualClass}`;
    entryElement.setAttribute('data-entry', JSON.stringify(entry));
    entryElement.setAttribute('data-signal-id', entry.signal_id || '');

    entryElement.innerHTML = `
        <div class="flex justify-between items-start mb-2">
            <div class="flex items-center space-x-3">
                <span class="text-xs text-slate-500">${timestamp}</span>
                <span class="text-sm font-mono text-teal-400 font-semibold">${ticker}</span>
                <span class="text-xs font-mono text-blue-300 bg-blue-900/30 px-2 py-1 rounded">${signalId.slice(0, 8)}...</span>
                <span class="text-sm ${statusColor} font-semibold px-2 py-1 rounded bg-opacity-20 ${status.includes('approved') || status.includes('success') ? 'bg-green-500' : status.includes('rejected') || status.includes('error') ? 'bg-red-500' : 'bg-yellow-500'}">${statusDisplay}</span>
                <span class="text-xs text-slate-400 bg-slate-700 px-2 py-1 rounded">${locationBadge}</span>
                ${manualBadge}
            </div>
            <div class="flex items-center space-x-2">
                <div class="text-xs text-slate-400">
                    <span>${eventsCount} events</span>
                    <span class="ml-2">${action}</span>
                </div>
                ${allowActions ? `
                    <div class="flex gap-1 ml-3">
                        <button 
                            class="sell-signal-btn bg-slate-600 hover:bg-red-600 text-slate-300 hover:text-white text-xs px-2 py-1 rounded transition-all duration-200 opacity-70 hover:opacity-100" 
                            title="Sell ${ticker} immediately" 
                            data-ticker="${ticker}"
                            data-signal-id="${signalId}"
                        >
                            ↗ Sell
                        </button>
                        <button 
                            class="queue-signal-btn bg-slate-600 hover:bg-orange-600 text-slate-300 hover:text-white text-xs px-2 py-1 rounded transition-all duration-200 opacity-70 hover:opacity-100" 
                            title="Add ${ticker} to sell all accumulator" 
                            data-ticker="${ticker}"
                            data-signal-id="${signalId}"
                        >
                            + Queue
                        </button>
                    </div>
                ` : ''}
            </div>
        </div>
        <div class="flex items-center space-x-4 text-xs text-slate-400 mb-2">
            <span class="font-semibold ${signalSide === 'BUY' || signalSide === 'buy' ? 'text-green-400' : signalSide === 'SELL' || signalSide === 'sell' ? 'text-red-400' : 'text-slate-400'}">${signalSide}</span>
            <span>Price: <span class="text-slate-300">${signalPrice}</span></span>
            <span>Volume: <span class="text-slate-300">${signalVolume}</span></span>
            <span>Signal Time: <span class="text-slate-300">${signalTime}</span></span>
        </div>
        ${details ? `<div class="text-sm text-slate-300 mt-2">${details}</div>` : ''}
        ${entry.events && entry.events.length > 0 ? `
            <div class="mt-2 text-xs text-slate-400">
                <span class="font-medium">Journey:</span> 
                ${entry.events.map(e => e.event_type).join(' → ')}
            </div>
        ` : ''}        `;

    // Add event listeners for action buttons if they exist
    if (allowActions) {
        const sellBtn = entryElement.querySelector('.sell-signal-btn');
        const queueBtn = entryElement.querySelector('.queue-signal-btn');
        
        if (sellBtn) {
            sellBtn.addEventListener('click', async (e) => {
                e.stopPropagation();
                const ticker = e.target.getAttribute('data-ticker');
                const signalId = e.target.getAttribute('data-signal-id');
                
                const originalText = e.target.textContent;
                e.target.disabled = true;
                e.target.textContent = '⏳';
                e.target.classList.add('opacity-50', 'cursor-not-allowed');
                
                console.log(`Sell action triggered for ${ticker} (signal: ${signalId.slice(0, 8)}...)`);
                
                try {
                    await sellIndividualTicker(ticker);
                } finally {
                    setTimeout(() => {
                        e.target.disabled = false;
                        e.target.textContent = originalText;
                        e.target.classList.remove('opacity-50', 'cursor-not-allowed');
                    }, 2000);
                }
            });
        }
        
        if (queueBtn) {
            queueBtn.addEventListener('click', async (e) => {
                e.stopPropagation();
                const ticker = e.target.getAttribute('data-ticker');
                const signalId = e.target.getAttribute('data-signal-id');
                
                const originalText = e.target.textContent;
                e.target.disabled = true;
                e.target.textContent = '⏳';
                e.target.classList.add('opacity-50', 'cursor-not-allowed');
                
                console.log(`Queue action triggered for ${ticker} (signal: ${signalId.slice(0, 8)}...)`);
                
                try {
                    await addToSellAllList(ticker);
                } finally {
                    setTimeout(() => {
                        e.target.disabled = false;
                        e.target.textContent = originalText;
                        e.target.classList.remove('opacity-50', 'cursor-not-allowed');
                    }, 2000);
                }
            });
        }
    }

    // Append to container
    container.appendChild(entryElement);
}

/**
 * Set up scroll listener for lazy loading
 * @param {Element} container - Container element to monitor
 */
function setupLazyLoadingScrollListener(container) {
    // Remove any existing scroll listeners first
    container.removeEventListener('scroll', handleLazyLoadingScroll);
    
    // Add new scroll listener
    container.addEventListener('scroll', handleLazyLoadingScroll);
}

/**
 * Handle scroll events for lazy loading
 */
function handleLazyLoadingScroll() {
    const container = document.getElementById('audit-log-container');
    if (!container || isLoadingMoreEntries) return;

    const scrollTop = container.scrollTop;
    const scrollHeight = container.scrollHeight;
    const clientHeight = container.clientHeight;
    
    // Check if user has scrolled close to the bottom
    if (scrollTop + clientHeight >= scrollHeight - LAZY_LOADING_TRIGGER_THRESHOLD) {
        const remainingEntries = auditEntriesPool.length - displayedEntriesCount;
        if (remainingEntries > 0) {
            console.log(`📜 Scroll trigger activated, loading more entries...`);
            loadMoreAuditEntries();
        }
    }
}

/**
 * Load all remaining entries at once (for "Show All" button)
 */
function loadAllRemainingEntries() {
    const remainingEntries = auditEntriesPool.length - displayedEntriesCount;
    if (remainingEntries <= 0) {
        console.log('📚 No more entries to load');
        return;
    }

    console.log(`🚀 Loading ALL remaining ${remainingEntries} entries...`);
    
    const container = document.getElementById('audit-log-container');
    if (!container) {
        console.error('Audit log container not found');
        return;
    }

    // Load all remaining entries
    const startIndex = displayedEntriesCount;
    const endIndex = auditEntriesPool.length;
    const allRemainingEntries = auditEntriesPool.slice(startIndex, endIndex);
    
    // Add all entries to DOM
    allRemainingEntries.forEach(entry => {
        addSingleAuditEntryToDOM(entry, container);
    });

    // Update counter
    displayedEntriesCount = auditEntriesPool.length;
    
    console.log(`✅ Loaded ALL entries: ${displayedEntriesCount}/${auditEntriesPool.length} entries now displayed`);
    
    // Update UI elements
    updateShowAllButton();
    updateScrollInfo();
    updateAuditStatistics();
}

/**
 * Update show all button visibility and text
 */
function updateShowAllButton() {
    const showAllBtn = document.getElementById('show-all-entries-btn');
    if (!showAllBtn) return;

    const remainingEntries = auditEntriesPool.length - displayedEntriesCount;
    
    if (remainingEntries > 0) {
        showAllBtn.style.display = 'inline-block';
        showAllBtn.classList.remove('hidden');
        showAllBtn.textContent = `Show All ${remainingEntries} Remaining`;
    } else {
        showAllBtn.style.display = 'none';
        showAllBtn.classList.add('hidden');
    }
}

/**
 * Update scroll info text
 */
function updateScrollInfo() {
    const scrollInfo = document.getElementById('audit-scroll-info');
    const entriesInfo = document.getElementById('audit-entries-info');
    
    if (entriesInfo) {
        const remainingEntries = auditEntriesPool.length - displayedEntriesCount;
        if (remainingEntries > 0) {
            entriesInfo.textContent = `Showing ${displayedEntriesCount} of ${auditEntriesPool.length} entries`;
        } else {
            entriesInfo.textContent = `Showing all ${auditEntriesPool.length} entries`;
        }
    }
    
    if (scrollInfo) {
        const remainingEntries = auditEntriesPool.length - displayedEntriesCount;
        if (remainingEntries > 0) {
            scrollInfo.style.display = 'inline';
            scrollInfo.classList.remove('hidden');
            scrollInfo.textContent = 'Scroll down to load more entries';
        } else {
            scrollInfo.style.display = 'none';
            scrollInfo.classList.add('hidden');
        }
    }
}

// === END LAZY LOADING SYSTEM ===
