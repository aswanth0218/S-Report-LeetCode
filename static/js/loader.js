/**
 * S-REPORT global page loader and Excel download animation
 */
(function () {
    const loader = document.getElementById('pageLoader');
    const loaderText = document.getElementById('pageLoaderText');
    const loaderSub = document.getElementById('pageLoaderSub');
    const pageBanner = document.getElementById('downloadPageBanner');
    const pageBannerText = document.getElementById('downloadPageBannerText');
    const topProgress = document.getElementById('topPageProgress');

    // Excel Download Animation Elements
    const excelOverlay = document.getElementById('excelDownloadOverlay');
    const excelModal = document.getElementById('excelDownloadModal');
    const excelTitle = document.getElementById('excelLoaderTitle');
    const excelSub = document.getElementById('excelLoaderSub');
    const excelFilename = document.getElementById('excelLoaderFilename');
    const excelProgressBar = document.getElementById('excelProgressBar');

    let excelTimer1 = null;
    let excelTimer2 = null;
    let excelTimer3 = null;
    let excelTimer4 = null;

    function startTopProgress() {
        if (!topProgress) return;
        topProgress.classList.add('active');
        topProgress.style.width = '30%';
        topProgress.style.opacity = '1';
        setTimeout(function () {
            if (topProgress.classList.contains('active')) {
                topProgress.style.width = '70%';
            }
        }, 200);
    }

    function completeTopProgress() {
        if (!topProgress) return;
        topProgress.style.width = '100%';
        setTimeout(function () {
            topProgress.style.opacity = '0';
            setTimeout(function () {
                topProgress.classList.remove('active');
                topProgress.style.width = '0';
            }, 300);
        }, 150);
    }

    function showPageLoader(message, subtext, useAnimatedDots) {
        startTopProgress();
        if (!loader) return;
        if (loaderText && message) {
            if (useAnimatedDots) {
                loaderText.innerHTML = message + '<span class="loader-dots"></span>';
            } else {
                loaderText.textContent = message;
            }
        }
        if (loaderSub && subtext) loaderSub.textContent = subtext;
        loader.classList.remove('d-none');
        loader.setAttribute('aria-hidden', 'false');
        document.body.classList.add('page-loading');
    }

    function hidePageLoader() {
        completeTopProgress();
        if (!loader) return;
        loader.classList.add('d-none');
        loader.setAttribute('aria-hidden', 'true');
        document.body.classList.remove('page-loading');
    }

    function hideExcelDownloadAnimation() {
        if (excelTimer1) clearTimeout(excelTimer1);
        if (excelTimer2) clearTimeout(excelTimer2);
        if (excelTimer3) clearTimeout(excelTimer3);
        if (excelTimer4) clearTimeout(excelTimer4);
        if (excelOverlay) {
            excelOverlay.classList.add('d-none');
            excelOverlay.setAttribute('aria-hidden', 'true');
        }
        completeTopProgress();
    }

    function showExcelDownloadAnimation(opts) {
        opts = opts || {};
        const filename = opts.filename || 'Report.xlsx';
        const message = opts.message || 'Generating Excel File...';
        const subtext = opts.subtext || 'Formatting rows and preparing workbook...';
        const success = opts.success || 'Excel file downloaded successfully!';

        if (excelTimer1) clearTimeout(excelTimer1);
        if (excelTimer2) clearTimeout(excelTimer2);
        if (excelTimer3) clearTimeout(excelTimer3);
        if (excelTimer4) clearTimeout(excelTimer4);

        startTopProgress();

        if (excelOverlay) {
            if (excelModal) excelModal.classList.remove('is-success');
            if (excelTitle) excelTitle.textContent = message;
            if (excelSub) excelSub.textContent = subtext;
            if (excelFilename) excelFilename.textContent = filename;
            if (excelProgressBar) excelProgressBar.style.width = '15%';

            excelOverlay.classList.remove('d-none');
            excelOverlay.setAttribute('aria-hidden', 'false');

            // Animation sequence:
            excelTimer1 = setTimeout(function () {
                if (excelProgressBar) excelProgressBar.style.width = '55%';
                if (excelSub) excelSub.textContent = 'Applying formatting and styles...';
            }, 350);

            excelTimer2 = setTimeout(function () {
                if (excelProgressBar) excelProgressBar.style.width = '88%';
                if (excelSub) excelSub.textContent = 'Finalizing workbook...';
            }, 800);

            excelTimer3 = setTimeout(function () {
                if (excelProgressBar) excelProgressBar.style.width = '100%';
                if (excelModal) excelModal.classList.add('is-success');
                if (excelTitle) excelTitle.textContent = 'Excel Ready & Downloaded!';
                if (excelSub) excelSub.textContent = success;
                showDownloadSuccess(success, filename);
                completeTopProgress();
            }, 1450);

            excelTimer4 = setTimeout(function () {
                if (excelOverlay) {
                    excelOverlay.classList.add('d-none');
                    excelOverlay.setAttribute('aria-hidden', 'true');
                }
            }, 3100);
        } else {
            showDownloadSuccess(success, filename);
        }
    }

    function inferExcelInfo(el, fallbackName) {
        const href = (el.getAttribute('href') || el.getAttribute('action') || '').toLowerCase();
        let filename = el.dataset.loaderFilename || fallbackName || '';
        let message = el.dataset.loaderMessage || 'Generating Excel File...';
        let subtext = el.dataset.loaderSub || 'Formatting tables and building workbook...';
        let success = el.dataset.loaderSuccess || 'Excel file downloaded successfully!';

        if (!filename) {
            if (href.includes('export-department-report') || href.includes('department-report')) {
                filename = 'S-Report.xlsx';
                success = 'S-Report exported successfully!';
            } else if (href.includes('weekly-contest') || href.includes('export-weekly')) {
                filename = 'Weekly-Contest-Details.xlsx';
                success = 'Weekly contest details exported successfully!';
            } else if (href.includes('missing-data')) {
                filename = 'Missing-Data-Issues.xlsx';
                success = 'Missing data report exported successfully!';
            } else if (href.includes('sample-input')) {
                filename = 'LeetCode_Students_Sample.xlsx';
                success = 'Sample input template downloaded successfully!';
            } else if (href.includes('download-report')) {
                filename = 'S-Report.xlsx';
            } else {
                filename = 'Report.xlsx';
            }
        }
        return { filename: filename, message: message, subtext: subtext, success: success };
    }

    function isExcelTarget(el) {
        if (!el) return false;
        if (el.dataset.reportDownload !== undefined) return true;
        const href = (el.getAttribute('href') || el.getAttribute('action') || '').toLowerCase();
        return (
            href.includes('.xlsx') ||
            href.includes('export-department-report') ||
            href.includes('export-weekly-contest-details') ||
            href.includes('download/missing-data') ||
            href.includes('download_missing_data') ||
            href.includes('download-report') ||
            href.includes('sample-input')
        );
    }

    function showDownloadSuccess(message, filename) {
        const text = message || 'Report downloaded successfully!';
        const fileLine = filename ? 'Saved as ' + filename : '';

        if (pageBanner && pageBannerText) {
            pageBannerText.textContent = fileLine ? text + ' — ' + fileLine : text;
            pageBanner.classList.remove('d-none');
            pageBanner.classList.add('show');
        }
    }

    function prepareFormForSubmit(form) {
        if (form.id === 'sReportForm') {
            const custom = form.querySelector('#contestDateCustom');
            const select = form.querySelector('#contestDateSelect');
            if (select && custom && select.value) {
                custom.value = '';
            }
        }
    }

    window.showPageLoader = showPageLoader;
    window.hidePageLoader = hidePageLoader;
    window.showExcelDownloadAnimation = showExcelDownloadAnimation;
    window.hideExcelDownloadAnimation = hideExcelDownloadAnimation;
    window.showDownloadSuccess = showDownloadSuccess;
    window.startTopProgress = startTopProgress;
    window.completeTopProgress = completeTopProgress;

    document.addEventListener('DOMContentLoaded', function () {
        hidePageLoader();
        if (excelOverlay) {
            excelOverlay.addEventListener('click', function (e) {
                if (e.target === excelOverlay) {
                    hideExcelDownloadAnimation();
                }
            });
        }
        const params = new URLSearchParams(window.location.search);
        const downloaded = params.get('downloaded');
        if (downloaded) {
            showDownloadSuccess('Report downloaded successfully!', downloaded);
        }
    });

    // Intercept clicks on links
    document.addEventListener('click', function (event) {
        const link = event.target.closest('a');
        if (!link) return;
        const href = link.getAttribute('href');
        if (!href || href.startsWith('#') || href.startsWith('javascript:') || link.target === '_blank') {
            if (link && isExcelTarget(link)) {
                const info = inferExcelInfo(link);
                showExcelDownloadAnimation(info);
            }
            return;
        }

        if (isExcelTarget(link)) {
            const info = inferExcelInfo(link);
            showExcelDownloadAnimation(info);
            return;
        }

        startTopProgress();
    }, true);

    // Intercept form submissions
    document.addEventListener('submit', function (event) {
        const form = event.target;
        if (!(form instanceof HTMLFormElement)) return;

        if (isExcelTarget(form)) {
            prepareFormForSubmit(form);
            const info = inferExcelInfo(form);
            showExcelDownloadAnimation(info);
            return;
        }

        if (form.dataset.noLoader !== undefined) return;

        const message = form.dataset.loaderMessage || 'Loading...';
        const subtext = form.dataset.loaderSub || 'Please wait while we process your request';
        showPageLoader(message, subtext);
    }, true);

    window.addEventListener('pageshow', function (event) {
        if (event.persisted) {
            hidePageLoader();
            hideExcelDownloadAnimation();
        }
    });
})();
