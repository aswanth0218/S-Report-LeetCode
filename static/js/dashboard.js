/**
 * S-REPORT Dashboard Charts — problems solved by department
 */

document.addEventListener('DOMContentLoaded', function () {
    const stats = window.dashboardStats || {};
    const deptColors = {
        CSE: 'rgba(13, 110, 253, 0.85)',
        'AI&DS': 'rgba(111, 66, 193, 0.85)',
        IT: 'rgba(25, 135, 84, 0.85)',
        EEE: 'rgba(255, 193, 7, 0.85)',
        ECE: 'rgba(23, 162, 184, 0.85)',
    };
    const highlightColor = 'rgba(220, 53, 69, 0.9)';

    function colorsForLabels(labels, highlightFirst) {
        return labels.map(function (label, index) {
            if (highlightFirst && index === 0) {
                return highlightColor;
            }
            return deptColors[label] || 'rgba(108, 117, 125, 0.85)';
        });
    }

    const growthCtx = document.getElementById('deptSolvedGrowthChart');
    if (growthCtx) {
        const growthLabels = stats.solved_growth_labels || [];
        const growthValues = stats.solved_growth_values || [];
        new Chart(growthCtx, {
            type: 'bar',
            data: {
                labels: growthLabels,
                datasets: [{
                    label: 'Avg Problems Solved / Student',
                    data: growthValues,
                    backgroundColor: colorsForLabels(growthLabels, true),
                    borderWidth: 1,
                }],
            },
            options: {
                indexAxis: 'y',
                responsive: true,
                plugins: {
                    legend: { display: false },
                    tooltip: {
                        callbacks: {
                            label: function (ctx) {
                                return 'Avg solved: ' + ctx.raw + ' per student';
                            },
                        },
                    },
                },
                scales: {
                    x: {
                        beginAtZero: true,
                        title: { display: true, text: 'Average problems solved' },
                    },
                },
            },
        });
    }

    const totalCtx = document.getElementById('deptSolvedTotalChart');
    if (totalCtx) {
        const totalLabels = stats.solved_total_labels || [];
        const totalValues = stats.solved_total_values || [];
        new Chart(totalCtx, {
            type: 'doughnut',
            data: {
                labels: totalLabels,
                datasets: [{
                    label: 'Total Problems Solved',
                    data: totalValues,
                    backgroundColor: colorsForLabels(totalLabels, false),
                    borderWidth: 2,
                }],
            },
            options: {
                responsive: true,
                plugins: {
                    legend: { position: 'bottom' },
                    tooltip: {
                        callbacks: {
                            label: function (ctx) {
                                const total = totalValues.reduce(function (a, b) { return a + b; }, 0);
                                const pct = total ? Math.round((ctx.raw / total) * 100) : 0;
                                return ctx.label + ': ' + ctx.raw + ' solved (' + pct + '%)';
                            },
                        },
                    },
                },
            },
        });
    }
});
