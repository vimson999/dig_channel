
/**
 * PublisherService - 负责所有的 API 交互和数据管理
 */
const PublisherService = {
    API_BASE: 'http://localhost:5001/api/v1',

    async fetchPresets() {
        const res = await fetch(`${this.API_BASE}/thumbnail/presets`);
        return await res.json();
    },

    async checkBiliStatus() {
        const res = await fetch(`${this.API_BASE}/bilibili/status`);
        return await res.json();
    },

    async fetchBiliSeasons() {
        const res = await fetch(`${this.API_BASE}/bilibili/seasons`);
        return await res.json();
    },

    async uploadFile(file) {
        const fd = new FormData();
        fd.append('file', file);
        const res = await fetch(`${this.API_BASE}/resources/upload`, { method: 'POST', body: fd });
        return await res.json();
    },

    async submitTask(payload) {
        const res = await fetch(`${this.API_BASE}/publisher/submit`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        return await res.json();
    },

    async getTaskStatus(taskId) {
        const res = await fetch(`${this.API_BASE}/publisher/task/${taskId}`);
        return await res.json();
    },

    async fetchTasks() {
        const res = await fetch(`${this.API_BASE}/publisher/tasks`);
        return await res.json();
    },

    async retryTask(taskId) {
        const res = await fetch(`${this.API_BASE}/publisher/retry/${taskId}`, { method: 'POST' });
        return await res.json();
    },

    async postToYouTube(taskId) {
        const res = await fetch(`${this.API_BASE}/publisher/post-youtube/${taskId}`, { method: 'POST' });
        return await res.json();
    }
};

window.PublisherService = PublisherService;
