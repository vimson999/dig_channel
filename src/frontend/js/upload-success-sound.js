// 播放平台上传完成提示音。
// 音频由前端静态服务器提供，避免把本地文件路径暴露给浏览器。
(() => {
    const soundPath = 'assets/sounds/xm4012.mp3';
    let player = null;
    let pending = false;
    let lastPlayedAt = 0;

    const getPlayer = () => {
        if (player) return player;
        const baseUrl = document.baseURI || window.location.href;
        player = new Audio(new URL(soundPath, baseUrl).toString());
        player.preload = 'auto';
        player.volume = 1;
        return player;
    };

    const play = () => {
        const now = Date.now();
        if (now - lastPlayedAt < 1000) return Promise.resolve(false);
        lastPlayedAt = now;

        const media = getPlayer();
        try {
            media.currentTime = 0;
        } catch (_) {
            // A not-yet-loaded media element may reject currentTime; play() can
            // still start it and the browser will seek from the beginning.
        }

        let result;
        try {
            result = media.play();
        } catch (_) {
            lastPlayedAt = 0;
            pending = true;
            return Promise.resolve(false);
        }
        if (!result || typeof result.then !== 'function') {
            pending = false;
            return Promise.resolve(true);
        }
        return result.then(() => {
            pending = false;
            return true;
        }).catch(() => {
            // Browsers may block a delayed play() until the next user gesture.
            lastPlayedAt = 0;
            pending = true;
            return false;
        });
    };

    const retryAfterInteraction = () => {
        if (!pending) return;
        pending = false;
        void play();
    };

    document.addEventListener('pointerdown', retryAfterInteraction, { passive: true });
    document.addEventListener('keydown', retryAfterInteraction, { passive: true });

    window.UploadSuccessSound = Object.freeze({
        source: soundPath,
        play,
    });
})();
