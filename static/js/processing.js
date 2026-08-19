/**
 * S-REPORT Processing Page — polls backend for upload progress
 */

(function () {
    const pollInterval = 800;

    function updateUI(data) {
        const stageText = document.getElementById('stageText');
        const progressBar = document.getElementById('progressBar');
        const percentText = document.getElementById('percentText');
        const fetchCount = document.getElementById('fetchCount');
        const currentUser = document.getElementById('currentUser');
        const cacheInfo = document.getElementById('cacheInfo');
        const cacheText = document.getElementById('cacheText');
        const errorBox = document.getElementById('errorBox');
        const errorText = document.getElementById('errorText');
        const completeBox = document.getElementById('completeBox');
        const statusIcon = document.getElementById('statusIcon');

        if (stageText && data.stage) {
            stageText.textContent = data.stage;
        }

        const pct = data.percent || 0;
        if (progressBar) {
            progressBar.style.width = pct + '%';
        }
        if (percentText) {
            percentText.textContent = pct + '%';
        }

        if (fetchCount && data.total) {
            fetchCount.textContent = (data.done || 0) + ' / ' + data.total;
        }

        if (currentUser && data.current_username) {
            const source = data.current_source === 'cached' ? '(cached)' : '';
            currentUser.textContent = 'Current: ' + data.current_username + ' ' + source;
        }

        if (cacheInfo && data.cached_profiles !== undefined && data.to_fetch !== undefined) {
            cacheInfo.classList.remove('d-none');
            cacheText.textContent =
                data.cached_profiles + ' cached (instant), ' + data.to_fetch + ' to fetch from LeetCode';
        }

        if (data.status === 'error') {
            if (errorBox) errorBox.classList.remove('d-none');
            if (errorText) errorText.textContent = data.message || 'Unknown error';
            if (statusIcon) statusIcon.innerHTML = '<i class="bi bi-x-circle-fill text-danger display-4"></i>';
            return true;
        }

        if (data.status === 'complete') {
            if (completeBox) completeBox.classList.remove('d-none');
            if (statusIcon) statusIcon.innerHTML = '<i class="bi bi-check-circle-fill text-success display-4"></i>';
            if (progressBar) {
                progressBar.classList.remove('progress-bar-animated');
                progressBar.classList.add('bg-success');
            }
            setTimeout(function () {
                window.location.href = '/';
            }, 1500);
            return true;
        }

        return false;
    }

    function poll() {
        fetch('/api/process-status')
            .then(function (r) { return r.json(); })
            .then(function (data) {
                const done = updateUI(data);
                if (!done) {
                    setTimeout(poll, pollInterval);
                }
            })
            .catch(function () {
                setTimeout(poll, pollInterval * 2);
            });
    }

    poll();
})();
