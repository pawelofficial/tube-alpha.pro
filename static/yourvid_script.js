



// Function to validate YouTube URL
function isValidYouTubeUrl(url) {
    const patterns = [
        /^(https?:\/\/)?(www\.)?(youtube\.com\/(watch\?v=|embed\/|v\/)|youtu\.be\/|m\.youtube\.com\/watch\?v=)[\w-]{11}(?:\S+)?$/,
        /^(https?:\/\/)?(www\.)?youtube\.com\/shorts\/[\w-]{11}(?:\S+)?$/
    ];
    
    return patterns.some(pattern => pattern.test(url));
}

// Function to extract video ID from YouTube URL
function extractVideoId(url) {
    const patterns = [
        /(?:youtube\.com\/watch\?v=|youtu\.be\/|youtube\.com\/embed\/|youtube\.com\/v\/|youtube\.com\/shorts\/)([^#\&\?]*).*/,
    ];
    
    for (let pattern of patterns) {
        const match = url.match(pattern);
        if (match && match[1]) {
            return match[1];
        }
    }
    return null;
}

// Function to render tiles visualization
function renderTiles(tiles) {
    const scoreClass = (x) => x > 0.05 ? 'green' : (x < -0.05 ? 'red' : 'gray');
    const scoreLabel = (x) => x > 0.05 ? 'Bullish' : (x < -0.05 ? 'Bearish' : 'Neutral');
    const fmt = (x) => (Math.round(x * 100) / 100).toFixed(2);

    const grid = document.getElementById('asset-grid');
    grid.innerHTML = tiles.map(t => {
        const cls = scoreClass(t.avg);
        const sentiments = (t.sentiments || []).map(s => `<span class="tag">${s}</span>`).join('');
        const vids = (t.videos || []).map(v => `<code>${v}</code>`).join(' ');
        const quotes = (t.quotes || []).map(q => `<p>"${q}"</p>`).join('');
        
        return `
            <div class="asset-card b-${cls}" data-asset="${t.asset}">
                <div class="asset-head">
                    <div class="asset-left">
                        <div class="asset-name">${t.asset}</div>
                        <div class="asset-meta">
                            <span class="badge t-${cls}">${scoreLabel(t.avg)} <span class="card-avg">(${fmt(t.avg)})</span></span>
                            ${sentiments}
                        </div>
                    </div>
                </div>
                <div class="asset-details">
                    <div class="quotes">
                        <div class="asset-meta" style="margin-bottom:.4rem;">
                            Source: ${vids || '—'}  
                        </div>
                        <div class="quotes">
                            ${quotes || '<p>No quotes</p>'}
                        </div>
                    </div>
                </div>
            </div>
        `;
    }).join('');

    // Toggle open/close on card click
    grid.addEventListener('click', (e) => {
        const card = e.target.closest('.asset-card');
        if (!card) return;
        card.classList.toggle('open');
    });
}

async function submitText() {
    const textInput = document.getElementById('textInput');
    const submitBtn = document.getElementById('submitBtn');
    const loadingIndicator = document.getElementById('loadingIndicator');
    const resultDisplay = document.getElementById('resultDisplay');
    const tilesVisualization = document.getElementById('tiles-visualization');
    
    // Get the input value
    const inputValue = textInput.value.trim();
    
    // Function to show validation errors
    function showError(message) {
        // Show error in result display
        resultDisplay.style.display = 'block';
        tilesVisualization.style.display = 'none';
        resultDisplay.innerHTML = `
            <div class="error-card">
                <div class="result-header">
                    <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="#ef4444" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                        <circle cx="12" cy="12" r="10"></circle>
                        <line x1="12" y1="8" x2="12" y2="12"></line>
                        <line x1="12" y1="16" x2="12.01" y2="16"></line>
                    </svg>
                    <h4 style="color: #ef4444;">Invalid Input</h4>
                </div>
                <p class="error-text">${message}</p>
            </div>
            <!-- Tiles Visualization -->
            <div id="tiles-visualization" style="margin-top: 30px; display: none;">
                <h3 style="margin-bottom: 20px; color: #2c3e50;">Analysis Results</h3>
                <div id="asset-grid" class="asset-grid"></div>
            </div>
        `;
        
        // Shake animation
        textInput.style.animation = 'shake 0.5s';
        setTimeout(() => {
            textInput.style.animation = '';
        }, 500);
        textInput.focus();
    }
    
    // Validate input
    if (!inputValue) {
        showError('Please enter a YouTube URL');
        return;
    }
    
    // Validate YouTube URL
    if (!isValidYouTubeUrl(inputValue)) {
        showError('Please enter a valid YouTube URL (e.g., https://youtube.com/watch?v=...)');
        return;
    }
    
    // Extract video ID
    const videoId = extractVideoId(inputValue);
    if (!videoId) {
        showError('Could not extract video ID from URL');
        return;
    }
    
    // Show loading, hide result, disable button
    loadingIndicator.style.display = 'flex';
    resultDisplay.style.display = 'none';
    tilesVisualization.style.display = 'none';
    submitBtn.disabled = true;
    textInput.disabled = true;
    
    try {
        const response = await fetch('/api/v1/videos/process', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ url: inputValue, video_id: videoId })
        });

        let data;
        try {
            data = await response.json();
        } catch (_) {
            throw new Error(`Server error (HTTP ${response.status})`);
        }

        if (!response.ok) {
            throw new Error(data.detail || `HTTP ${response.status}`);
        }

        if (data.success) {
            const desc = data.summary || data.message || '';
            resultDisplay.innerHTML = `
                <div class="result-card">
                    <div class="result-header">
                        <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="#10b981" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                            <path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"></path>
                            <polyline points="22 4 12 14.01 9 11.01"></polyline>
                        </svg>
                        <h4>Done!</h4>
                    </div>
                    <p class="result-text">${desc}</p>
                </div>
                <div id="tiles-visualization" style="margin-top:30px;display:none;">
                    <h3 style="margin-bottom:20px;color:#2c3e50;">Analysis Results</h3>
                    <div id="asset-grid" class="asset-grid"></div>
                </div>
            `;
            if (data.tiles && data.tiles.length > 0) {
                document.getElementById('tiles-visualization').style.display = 'block';
                renderTiles(data.tiles);
            }
        } else {
            const msg = data.message || data.summary || 'Processing failed.';
            resultDisplay.innerHTML = `
                <div class="error-card">
                    <div class="result-header">
                        <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="#ef4444" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                            <circle cx="12" cy="12" r="10"></circle>
                            <line x1="12" y1="8" x2="12" y2="12"></line>
                            <line x1="12" y1="16" x2="12.01" y2="16"></line>
                        </svg>
                        <h4 style="color:#ef4444;">Error</h4>
                    </div>
                    <p class="error-text">${msg}</p>
                </div>
            `;
        }

        resultDisplay.style.display = 'block';
        resultDisplay.style.animation = 'fadeIn 0.5s';

    } catch (error) {
        console.error('Error:', error);
        resultDisplay.innerHTML = `
            <div class="error-card">
                <div class="result-header">
                    <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="#ef4444" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                        <circle cx="12" cy="12" r="10"></circle>
                        <line x1="12" y1="8" x2="12" y2="12"></line>
                        <line x1="12" y1="16" x2="12.01" y2="16"></line>
                    </svg>
                    <h4 style="color:#ef4444;">Error</h4>
                </div>
                <p class="error-text">${error.message || 'An unexpected error occurred.'}</p>
            </div>
        `;
        resultDisplay.style.display = 'block';
    } finally {
        // Hide loading and re-enable inputs
        loadingIndicator.style.display = 'none';
        submitBtn.disabled = false;
        textInput.disabled = false;
    }
}
