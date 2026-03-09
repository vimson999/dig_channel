/**
 * 听见资本 - 全能封面工作台 (3合1) 核心逻辑
 * 集成至 YouTube Analytics 项目
 */

document.addEventListener('DOMContentLoaded', () => {
    // 初始化 Canvas 和 上下文
    const canvas = document.getElementById('thumbnailCanvas');
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    
    // UI 元素引用
    const inputs = {
        format: document.getElementById('formatSelect'),
        showGuides: document.getElementById('showGuides'),
        inst: document.getElementById('instSelect'),
        mainTitle: document.getElementById('mainTitle'),
        subTitle: document.getElementById('subTitle'),
        emotions: document.querySelectorAll('input[name="emotion"]'),
        upload: document.getElementById('imageUpload'),
        clearImg: document.getElementById('clearImageBtn')
    };

    const uiElements = {
        wrapper: document.getElementById('previewWrapper'),
        container: document.getElementById('canvasContainer'),
        header: document.getElementById('previewHeader'),
        title: document.getElementById('previewTitle'),
        size: document.getElementById('previewSize'),
        btnText: document.getElementById('downloadBtnText')
    };

    let reportImg = null;
    
    // 拖拽/缩放状态
    let imgOffsetX = 0;
    let imgOffsetY = 0;
    let imgScale = 1.0;
    let isDragging = false;
    let dragStartX = 0;
    let dragStartY = 0;
    let initialPinchDistance = null;
    let initialScale = 1.0;

    const themeColors = {
        macro: { main: '#FACC15', glow: 'rgba(250, 204, 21, 0.4)' },
        bull: { main: '#4ADE80', glow: 'rgba(74, 222, 128, 0.4)' },
        bear: { main: '#EF4444', glow: 'rgba(239, 68, 68, 0.4)' }
    };

    const layouts = {
        horizontal: { w: 1920, h: 1080, aspectClass: 'max-w-5xl aspect-video', title: '横版预览区', size: '1920 x 1080 (16:9)', btn: '下载横版封面 (1920x1080)' },
        vertical: { w: 1080, h: 1920, aspectClass: 'max-w-[42vh] aspect-[9/16]', title: '竖版预览区', size: '1080 x 1920 (9:16)', btn: '下载竖版封面 (1080x1920)' },
        wx_cover: { w: 1080, h: 1440, aspectClass: 'max-w-[45vh] aspect-[3/4]', title: '视频号封面预览区', size: '1080 x 1440 (3:4)', btn: '下载视频号封面 (1080x1440)' },
        podcast: { w: 1400, h: 1400, aspectClass: 'max-w-[50vh] aspect-square', title: '播客封面预览区', size: '1400 x 1400 (1:1)', btn: '下载播客封面 (1400x1400)' },
        video_bg: { w: 1080, h: 1920, aspectClass: 'max-w-[42vh] aspect-[9/16]', title: '视频号/抖音内容背景板', size: '1080 x 1920 (9:16)', btn: '下载视频背景板 (1080x1920)' },
        video_bg_audio: { w: 1080, h: 1920, aspectClass: 'max-w-[42vh] aspect-[9/16]', title: '纯音频视频背景板', size: '1080 x 1920 (9:16)', btn: '下载音频背景板 (1080x1920)' }
    };

    function getCurrentFormat() { return inputs.format.value; }
    function getCurrentColor() { return themeColors[Array.from(inputs.emotions).find(r => r.checked).value]; }

    // 事件绑定
    if (inputs.format) inputs.format.addEventListener('change', updateLayoutAndDraw);
    if (inputs.showGuides) inputs.showGuides.addEventListener('change', draw);
    if (inputs.inst) inputs.inst.addEventListener('change', draw);
    if (inputs.mainTitle) inputs.mainTitle.addEventListener('input', draw);
    if (inputs.subTitle) inputs.subTitle.addEventListener('input', draw);
    inputs.emotions.forEach(radio => radio.addEventListener('change', draw));

    if (inputs.upload) {
        inputs.upload.addEventListener('change', function(e) {
            const file = e.target.files[0];
            if (file) {
                const reader = new FileReader();
                reader.onload = function(event) {
                    const img = new Image();
                    img.onload = function() {
                        reportImg = img;
                        imgOffsetX = 0;
                        imgOffsetY = 0;
                        imgScale = 1.0;
                        inputs.clearImg.classList.remove('hidden');
                        canvas.style.cursor = 'grab';
                        draw();
                    }
                    img.src = event.target.result;
                }
                reader.readAsDataURL(file);
            }
        });
    }

    if (inputs.clearImg) {
        inputs.clearImg.addEventListener('click', () => {
            reportImg = null;
            imgOffsetX = 0;
            imgOffsetY = 0;
            imgScale = 1.0;
            inputs.upload.value = '';
            inputs.clearImg.classList.add('hidden');
            canvas.style.cursor = 'default';
            draw();
        });
    }

    // Canvas 交互逻辑
    function getCanvasPos(e) {
        const rect = canvas.getBoundingClientRect();
        const scaleX = canvas.width / rect.width;
        const scaleY = canvas.height / rect.height;
        let clientX = e.clientX;
        let clientY = e.clientY;
        if (e.touches && e.touches.length > 0) {
            clientX = e.touches[0].clientX;
            clientY = e.touches[0].clientY;
        }
        return {
            x: (clientX - rect.left) * scaleX,
            y: (clientY - rect.top) * scaleY
        };
    }

    function startDrag(e) {
        if (!reportImg) return;
        if (e.touches && e.touches.length === 2) {
            initialPinchDistance = Math.hypot(e.touches[0].clientX - e.touches[1].clientX, e.touches[0].clientY - e.touches[1].clientY);
            initialScale = imgScale;
            isDragging = false;
            return;
        }
        isDragging = true;
        const pos = getCanvasPos(e);
        dragStartX = pos.x - imgOffsetX;
        dragStartY = pos.y - imgOffsetY;
        canvas.style.cursor = 'grabbing';
    }

    function onDrag(e) {
        if (!reportImg) return;
        if (e.touches && e.touches.length === 2 && initialPinchDistance) {
            const currentDistance = Math.hypot(e.touches[0].clientX - e.touches[1].clientX, e.touches[0].clientY - e.touches[1].clientY);
            imgScale = initialScale * (currentDistance / initialPinchDistance);
            imgScale = Math.max(0.1, Math.min(imgScale, 5.0));
            draw();
            if (e.cancelable) e.preventDefault();
            return;
        }
        if (!isDragging) return;
        const pos = getCanvasPos(e);
        imgOffsetX = pos.x - dragStartX;
        imgOffsetY = pos.y - dragStartY;
        draw();
        if (e.cancelable) e.preventDefault();
    }

    function endDrag(e) {
        if (e.touches && e.touches.length < 2) initialPinchDistance = null;
        if (!isDragging) return;
        isDragging = false;
        if (reportImg) canvas.style.cursor = 'grab';
    }

    canvas.addEventListener('mousedown', startDrag);
    canvas.addEventListener('mousemove', onDrag);
    canvas.addEventListener('mouseup', endDrag);
    canvas.addEventListener('mouseleave', endDrag);
    canvas.addEventListener('touchstart', startDrag, { passive: false });
    canvas.addEventListener('touchmove', onDrag, { passive: false });
    canvas.addEventListener('touchend', endDrag);
    canvas.addEventListener('wheel', (e) => {
        if (!reportImg) return;
        e.preventDefault();
        const zoomSensitivity = 0.0015;
        imgScale += e.deltaY > 0 ? -zoomSensitivity * Math.abs(e.deltaY) : zoomSensitivity * Math.abs(e.deltaY);
        imgScale = Math.max(0.1, Math.min(imgScale, 5.0));
        draw();
    }, { passive: false });

    // 辅助函数
    function wrapText(context, text, x, y, maxWidth, lineHeight) {
        let line = '';
        let currentY = y;
        for(let n = 0; n < text.length; n++) {
            let testLine = line + text[n];
            if(text[n] === '\n') {
                context.fillText(line, x, currentY);
                line = '';
                currentY += lineHeight;
                continue;
            }
            let metrics = context.measureText(testLine);
            let testWidth = metrics.width;
            if (testWidth > maxWidth && n > 0) {
                context.fillText(line, x, currentY);
                line = text[n];
                currentY += lineHeight;
            } else {
                line = testLine;
            }
        }
        context.fillText(line, x, currentY);
        return currentY + lineHeight;
    }

    function roundRect(ctx, x, y, width, height, radius) {
        ctx.beginPath();
        ctx.moveTo(x + radius, y);
        ctx.lineTo(x + width - radius, y);
        ctx.quadraticCurveTo(x + width, y, x + width, y + radius);
        ctx.lineTo(x + width, y + height - radius);
        ctx.quadraticCurveTo(x + width, y + height, x + width - radius, y + height);
        ctx.lineTo(x + radius, y + height);
        ctx.quadraticCurveTo(x, y + height, x, y + height - radius);
        ctx.lineTo(x, y + radius);
        ctx.quadraticCurveTo(x, y, x + radius, y);
        ctx.closePath();
    }

    function updateLayoutAndDraw() {
        const currentFormat = getCurrentFormat();
        const layout = layouts[currentFormat];
        imgOffsetX = 0;
        imgOffsetY = 0;
        imgScale = 1.0;
        
        // 更新容器类名
        uiElements.container.className = `canvas-container w-full ${layout.aspectClass}`;
        
        // 健壮地提取 max-width 类名并应用给 header，确保 header 宽度与 canvas 对齐
        const maxWidthClass = layout.aspectClass.split(' ').find(c => c.startsWith('max-w-')) || '';
        uiElements.header.className = `flex justify-between items-end mb-4 w-full ${maxWidthClass}`;
        
        uiElements.title.innerText = layout.title;
        uiElements.size.innerText = layout.size;
        uiElements.btnText.innerText = layout.btn;
        canvas.width = layout.w;
        canvas.height = layout.h;
        draw();
    }

    function draw() {
        const format = getCurrentFormat();
        const w = canvas.width;
        const h = canvas.height;
        const colors = getCurrentColor();

        const bgGradient = ctx.createLinearGradient(0, 0, w, h);
        bgGradient.addColorStop(0, '#0a1128'); 
        bgGradient.addColorStop(1, '#050814'); 
        ctx.fillStyle = bgGradient;
        ctx.fillRect(0, 0, w, h);

        if (reportImg) {
            let targetWidth, targetHeight, drawX, drawY;
            const imgRatio = reportImg.width / reportImg.height;

            if (format === 'horizontal') {
                targetWidth = 1200; targetHeight = 1080;
                const targetX = 720;
                const targetRatio = targetWidth / targetHeight;
                if (imgRatio > targetRatio) {
                    let dw = reportImg.width * (targetHeight / reportImg.height);
                    let sDw = dw * imgScale; let sDh = targetHeight * imgScale;
                    drawX = targetX - (sDw - targetWidth) / 2 + imgOffsetX; 
                    drawY = 0 - (sDh - targetHeight) / 2 + imgOffsetY;
                    ctx.drawImage(reportImg, drawX, drawY, sDw, sDh);
                } else {
                    let dh = reportImg.height * (targetWidth / reportImg.width);
                    let sDw = targetWidth * imgScale; let sDh = dh * imgScale;
                    drawX = targetX - (sDw - targetWidth) / 2 + imgOffsetX; 
                    drawY = -(sDh - targetHeight) / 2 + imgOffsetY;
                    ctx.drawImage(reportImg, drawX, drawY, sDw, sDh);
                }
                const mask = ctx.createLinearGradient(400, 0, 1200, 0);
                mask.addColorStop(0, '#0a1128');
                mask.addColorStop(0.5, 'rgba(10, 17, 40, 0.9)'); 
                mask.addColorStop(1, 'rgba(10, 17, 40, 0)');
                ctx.fillStyle = mask; ctx.fillRect(0, 0, w, h);
            } else if (format === 'vertical') {
                targetWidth = w; targetHeight = 1200;
                const targetRatio = targetWidth / targetHeight;
                if (imgRatio > targetRatio) {
                    let dw = reportImg.width * (targetHeight / reportImg.height);
                    let sDw = dw * imgScale; let sDh = targetHeight * imgScale;
                    drawX = -(sDw - targetWidth) / 2 + imgOffsetX; 
                    drawY = 0 - (sDh - targetHeight) / 2 + imgOffsetY;
                    ctx.drawImage(reportImg, drawX, drawY, sDw, sDh);
                } else {
                    let dh = reportImg.height * (targetWidth / reportImg.width);
                    let sDw = targetWidth * imgScale; let sDh = dh * imgScale;
                    drawX = 0 - (sDw - targetWidth) / 2 + imgOffsetX; 
                    drawY = 0 - (sDh - targetHeight) / 2 + imgOffsetY;
                    ctx.drawImage(reportImg, drawX, drawY, sDw, sDh);
                }
                const mask = ctx.createLinearGradient(0, 400, 0, 1200);
                mask.addColorStop(0, 'rgba(10, 17, 40, 0)');
                mask.addColorStop(0.7, 'rgba(10, 17, 40, 0.9)'); 
                mask.addColorStop(1, '#0a1128');
                ctx.fillStyle = mask; ctx.fillRect(0, 0, w, h);
            } else if (format === 'wx_cover') {
                targetWidth = w; targetHeight = 900;
                const targetRatio = targetWidth / targetHeight;
                if (imgRatio > targetRatio) {
                    let dw = reportImg.width * (targetHeight / reportImg.height);
                    let sDw = dw * imgScale; let sDh = targetHeight * imgScale;
                    drawX = -(sDw - targetWidth) / 2 + imgOffsetX; 
                    drawY = 0 - (sDh - targetHeight) / 2 + imgOffsetY;
                    ctx.drawImage(reportImg, drawX, drawY, sDw, sDh);
                } else {
                    let dh = reportImg.height * (targetWidth / reportImg.width);
                    let sDw = targetWidth * imgScale; let sDh = dh * imgScale;
                    drawX = 0 - (sDw - targetWidth) / 2 + imgOffsetX; 
                    drawY = 0 - (sDh - targetHeight) / 2 + imgOffsetY;
                    ctx.drawImage(reportImg, drawX, drawY, sDw, sDh);
                }
                const mask = ctx.createLinearGradient(0, 300, 0, 900);
                mask.addColorStop(0, 'rgba(10, 17, 40, 0)');
                mask.addColorStop(0.7, 'rgba(10, 17, 40, 0.9)'); 
                mask.addColorStop(1, '#0a1128');
                ctx.fillStyle = mask; ctx.fillRect(0, 0, w, h);
            } else if (format === 'podcast') {
                targetWidth = w; targetHeight = 900;
                const targetRatio = targetWidth / targetHeight;
                if (imgRatio > targetRatio) {
                    let dw = reportImg.width * (targetHeight / reportImg.height);
                    let sDw = dw * imgScale; let sDh = targetHeight * imgScale;
                    drawX = -(sDw - targetWidth) / 2 + imgOffsetX; 
                    drawY = 0 - (sDh - targetHeight) / 2 + imgOffsetY;
                    ctx.drawImage(reportImg, drawX, drawY, sDw, sDh);
                } else {
                    let dh = reportImg.height * (targetWidth / reportImg.width);
                    let sDw = targetWidth * imgScale; let sDh = dh * imgScale;
                    drawX = 0 - (sDw - targetWidth) / 2 + imgOffsetX; 
                    drawY = 0 - (sDh - targetHeight) / 2 + imgOffsetY;
                    ctx.drawImage(reportImg, drawX, drawY, sDw, sDh);
                }
                const mask = ctx.createLinearGradient(0, 200, 0, 900);
                mask.addColorStop(0, 'rgba(10, 17, 40, 0)');
                mask.addColorStop(0.6, 'rgba(10, 17, 40, 0.9)'); 
                mask.addColorStop(1, '#0a1128');
                ctx.fillStyle = mask; ctx.fillRect(0, 0, w, h);
            } else if (format === 'video_bg') {
                ctx.globalAlpha = 0.15; 
                let dw = reportImg.width * (h / reportImg.height);
                let sDw = dw * imgScale; let sDh = h * imgScale;
                ctx.drawImage(reportImg, -(sDw - w) / 2 + imgOffsetX, 0 - (sDh - h) / 2 + imgOffsetY, sDw, sDh);
                ctx.globalAlpha = 1.0;
            } else if (format === 'video_bg_audio') {
                ctx.globalAlpha = 0.15; 
                let dwBg = reportImg.width * (h / reportImg.height);
                let sDwBg = dwBg * imgScale; let sDhBg = h * imgScale;
                ctx.drawImage(reportImg, -(sDwBg - w) / 2 + imgOffsetX, 0 - (sDhBg - h) / 2 + imgOffsetY, sDwBg, sDhBg);
                ctx.globalAlpha = 1.0;
            }
        } else {
            ctx.strokeStyle = 'rgba(255, 255, 255, 0.03)';
            ctx.lineWidth = 2;
            for(let i=0; i<w; i+=80) { ctx.beginPath(); ctx.moveTo(i, 0); ctx.lineTo(i, h); ctx.stroke(); }
            for(let j=0; j<h; j+=80) { ctx.beginPath(); ctx.moveTo(0, j); ctx.lineTo(w, j); ctx.stroke(); }
        }

        const instText = inputs.inst.value;
        const mainText = inputs.mainTitle.value || "请输入主标题";
        const subText = inputs.subTitle.value;

        if (format === 'horizontal') {
            const margin = 120;
            ctx.font = 'bold 40px "Microsoft YaHei", sans-serif'; ctx.fillStyle = colors.main;
            ctx.fillRect(margin, 160, 12, 45); ctx.fillStyle = '#e2e8f0';
            ctx.fillText(instText + " · 研报解构", margin + 30, 195);
            ctx.font = '900 130px "Microsoft YaHei", "PingFang SC", sans-serif'; ctx.fillStyle = colors.main;
            ctx.shadowColor = colors.glow; ctx.shadowBlur = 30; ctx.shadowOffsetY = 10;
            let nextY = wrapText(ctx, mainText, margin, 400, 1200, 150);
            ctx.shadowBlur = 0; ctx.shadowOffsetY = 0;
            if (subText) {
                ctx.font = 'bold 70px "Microsoft YaHei", sans-serif'; ctx.fillStyle = '#ffffff';
                ctx.shadowColor = 'rgba(0,0,0,0.8)'; ctx.shadowBlur = 10; ctx.shadowOffsetY = 5;
                wrapText(ctx, subText, margin, nextY + 60, 1200, 90);
                ctx.shadowBlur = 0; ctx.shadowOffsetY = 0;
            }
            const by = 960; ctx.fillStyle = colors.main;
            ctx.fillRect(margin, by-15, 6, 15); ctx.fillRect(margin+12, by-30, 6, 30); ctx.fillRect(margin+24, by-20, 6, 20); ctx.fillRect(margin+36, by-35, 6, 35); ctx.fillRect(margin+48, by-10, 6, 10);
            ctx.font = 'bold 45px "Microsoft YaHei", sans-serif'; ctx.fillStyle = '#94a3b8'; ctx.fillText("听见资本", margin + 70, by);
        } else if (format === 'vertical') {
            const margin = 80; const maxW = w - margin * 2;
            ctx.font = 'bold 36px "Microsoft YaHei", sans-serif'; ctx.fillStyle = colors.main;
            ctx.fillRect(margin, 1000, 10, 40); ctx.fillStyle = '#e2e8f0';
            ctx.fillText(instText + " · 研报解构", margin + 25, 1032);
            ctx.font = '900 110px "Microsoft YaHei", "PingFang SC", sans-serif'; ctx.fillStyle = colors.main;
            ctx.shadowColor = colors.glow; ctx.shadowBlur = 30; ctx.shadowOffsetY = 10;
            let nextY = wrapText(ctx, mainText, margin, 1170, maxW, 130);
            ctx.shadowBlur = 0; ctx.shadowOffsetY = 0;
            if (subText) {
                ctx.font = 'bold 65px "Microsoft YaHei", sans-serif'; ctx.fillStyle = '#ffffff';
                ctx.shadowColor = 'rgba(0,0,0,0.8)'; ctx.shadowBlur = 10; ctx.shadowOffsetY = 5;
                wrapText(ctx, subText, margin, nextY + 60, maxW, 85);
                ctx.shadowBlur = 0; ctx.shadowOffsetY = 0;
            }
            const by = 1750; ctx.fillStyle = colors.main;
            ctx.fillRect(margin, by-15, 6, 15); ctx.fillRect(margin+12, by-30, 6, 30); ctx.fillRect(margin+24, by-20, 6, 20); ctx.fillRect(margin+36, by-35, 6, 35); ctx.fillRect(margin+48, by-10, 6, 10);
            ctx.font = 'bold 40px "Microsoft YaHei", sans-serif'; ctx.fillStyle = '#64748b'; ctx.fillText("听见资本", margin + 70, by);
        } else if (format === 'wx_cover') {
            const margin = 80; const maxW = w - margin * 2;
            ctx.font = 'bold 36px "Microsoft YaHei", sans-serif'; ctx.fillStyle = colors.main;
            ctx.fillRect(margin, 700, 10, 40); ctx.fillStyle = '#e2e8f0';
            ctx.fillText(instText + " · 研报解构", margin + 25, 732);
            ctx.font = '900 110px "Microsoft YaHei", "PingFang SC", sans-serif'; ctx.fillStyle = colors.main;
            ctx.shadowColor = colors.glow; ctx.shadowBlur = 30; ctx.shadowOffsetY = 10;
            let nextY = wrapText(ctx, mainText, margin, 870, maxW, 130);
            ctx.shadowBlur = 0; ctx.shadowOffsetY = 0;
            if (subText) {
                ctx.font = 'bold 65px "Microsoft YaHei", sans-serif'; ctx.fillStyle = '#ffffff';
                ctx.shadowColor = 'rgba(0,0,0,0.8)'; ctx.shadowBlur = 10; ctx.shadowOffsetY = 5;
                wrapText(ctx, subText, margin, nextY + 60, maxW, 85);
                ctx.shadowBlur = 0; ctx.shadowOffsetY = 0;
            }
            const by = 1300; ctx.fillStyle = colors.main;
            ctx.fillRect(margin, by-15, 6, 15); ctx.fillRect(margin+12, by-30, 6, 30); ctx.fillRect(margin+24, by-20, 6, 20); ctx.fillRect(margin+36, by-35, 6, 35); ctx.fillRect(margin+48, by-10, 6, 10);
            ctx.font = 'bold 40px "Microsoft YaHei", sans-serif'; ctx.fillStyle = '#64748b'; ctx.fillText("听见资本", margin + 70, by);
        } else if (format === 'podcast') {
            const margin = 100; const maxW = w - margin * 2;

            ctx.font = 'bold 45px "Microsoft YaHei", sans-serif'; ctx.fillStyle = colors.main;
            ctx.fillRect(margin, 630, 12, 50); ctx.fillStyle = '#e2e8f0';
            ctx.fillText(instText + " · 研报解构", margin + 35, 672);

            ctx.font = '900 140px "Microsoft YaHei", "PingFang SC", sans-serif'; ctx.fillStyle = colors.main;
            ctx.shadowColor = colors.glow; ctx.shadowBlur = 30; ctx.shadowOffsetY = 10;
            let nextY = wrapText(ctx, mainText, margin, 850, maxW, 160);
            ctx.shadowBlur = 0; ctx.shadowOffsetY = 0;

            if (subText) {
                ctx.font = 'bold 80px "Microsoft YaHei", sans-serif'; ctx.fillStyle = '#ffffff';
                ctx.shadowColor = 'rgba(0,0,0,0.8)'; ctx.shadowBlur = 10; ctx.shadowOffsetY = 5;
                wrapText(ctx, subText, margin, nextY + 60, maxW, 100);
                ctx.shadowBlur = 0; ctx.shadowOffsetY = 0;
            }

                const by = 1340; ctx.fillStyle = colors.main;            ctx.fillRect(margin, by-20, 8, 20); ctx.fillRect(margin+16, by-40, 8, 40); ctx.fillRect(margin+32, by-25, 8, 25); ctx.fillRect(margin+48, by-45, 8, 45); ctx.fillRect(margin+64, by-15, 8, 15);
            ctx.font = 'bold 55px "Microsoft YaHei", sans-serif'; ctx.fillStyle = '#64748b'; ctx.fillText("听见资本", margin + 95, by);
        } else if (format === 'video_bg') {
            const margin = 80; const maxW = w - margin * 2;
            ctx.font = 'bold 36px "Microsoft YaHei", sans-serif'; ctx.fillStyle = colors.main;
            ctx.fillRect(margin, 120, 10, 40); ctx.fillStyle = '#e2e8f0';
            ctx.fillText(instText + " · 研报解构", margin + 25, 152);
            ctx.font = '900 90px "Microsoft YaHei", "PingFang SC", sans-serif'; ctx.fillStyle = colors.main;
            ctx.shadowColor = colors.glow; ctx.shadowBlur = 30; ctx.shadowOffsetY = 10;
            let nextY = wrapText(ctx, mainText, margin, 260, maxW, 110);
            ctx.shadowBlur = 0; ctx.shadowOffsetY = 0;
            if (subText) {
                ctx.font = 'bold 55px "Microsoft YaHei", sans-serif'; ctx.fillStyle = '#ffffff';
                wrapText(ctx, subText, margin, nextY + 40, maxW, 75);
            }
            const videoH = w * (9/16); 
            const videoY = 620; 
            ctx.fillStyle = '#000000'; 
            ctx.fillRect(0, videoY, w, videoH);
            if (inputs.showGuides.checked) {
                ctx.setLineDash([15, 15]);
                ctx.strokeStyle = 'rgba(255,255,255,0.2)';
                ctx.lineWidth = 4;
                ctx.strokeRect(10, videoY + 10, w - 20, videoH - 20);
                ctx.setLineDash([]);
                ctx.font = 'bold 36px "Microsoft YaHei"'; ctx.fillStyle = '#4b5563';
                ctx.textAlign = 'center';
                ctx.fillText('剪映剪辑时，将 16:9 视频放置于此黑框内', w/2, videoY + videoH/2);
                ctx.textAlign = 'left';
                ctx.textAlign = 'center';
                ctx.font = '30px "Microsoft YaHei"'; ctx.fillStyle = 'rgba(255,255,255,0.15)';
                ctx.fillText('--- 字幕安全区 ---', w/2, 1450);
                ctx.textAlign = 'left';
            }
            const by = 1750; ctx.fillStyle = colors.main;
            ctx.fillRect(margin, by-15, 6, 15); ctx.fillRect(margin+12, by-30, 6, 30); ctx.fillRect(margin+24, by-20, 6, 20); ctx.fillRect(margin+36, by-35, 6, 35); ctx.fillRect(margin+48, by-10, 6, 10);
            ctx.font = 'bold 40px "Microsoft YaHei", sans-serif'; ctx.fillStyle = '#64748b'; ctx.fillText("听见资本", margin + 70, by);
        } else if (format === 'video_bg_audio') {
            const margin = 80; const maxW = w - margin * 2;
            ctx.font = 'bold 36px "Microsoft YaHei", sans-serif'; ctx.fillStyle = colors.main;
            ctx.fillRect(margin, 120, 10, 40); ctx.fillStyle = '#e2e8f0';
            ctx.fillText(instText + " · 研报解构", margin + 25, 152);
            ctx.font = '900 90px "Microsoft YaHei", "PingFang SC", sans-serif'; ctx.fillStyle = colors.main;
            ctx.shadowColor = colors.glow; ctx.shadowBlur = 30; ctx.shadowOffsetY = 10;
            let nextY = wrapText(ctx, mainText, margin, 260, maxW, 110);
            ctx.shadowBlur = 0; ctx.shadowOffsetY = 0;
            if (subText) {
                ctx.font = 'bold 55px "Microsoft YaHei", sans-serif'; ctx.fillStyle = '#ffffff';
                nextY = wrapText(ctx, subText, margin, nextY + 40, maxW, 75);
            }
            const boxY = Math.max(nextY + 80, 550); 
            const boxH = 550;
            if (reportImg) {
                ctx.shadowColor = 'rgba(0,0,0,0.5)'; ctx.shadowBlur = 20; ctx.shadowOffsetY = 10;
                ctx.fillStyle = 'rgba(255,255,255,0.05)';
                roundRect(ctx, margin, boxY, maxW, boxH, 20);
                ctx.fill();
                ctx.shadowBlur = 0; ctx.shadowOffsetY = 0; 
                ctx.lineWidth = 4; ctx.strokeStyle = 'rgba(255,255,255,0.1)';
                ctx.stroke();
                ctx.save();
                roundRect(ctx, margin, boxY, maxW, boxH, 20);
                ctx.clip();
                const imgRatio = reportImg.width / reportImg.height;
                const targetRatio = maxW / boxH;
                let dw, dh, dx, dy;
                if (imgRatio > targetRatio) {
                    dh = boxH; dw = reportImg.width * (boxH / reportImg.height);
                    let sDw = dw * imgScale; let sDh = dh * imgScale;
                    dx = margin - (sDw - maxW)/2 + imgOffsetX; 
                    dy = boxY - (sDh - boxH)/2 + imgOffsetY;
                    ctx.drawImage(reportImg, dx, dy, sDw, sDh);
                } else {
                    dw = maxW; dh = reportImg.height * (maxW / reportImg.width);
                    let sDw = dw * imgScale; let sDh = dh * imgScale;
                    dx = margin - (sDw - maxW)/2 + imgOffsetX; 
                    dy = boxY - (sDh - boxH)/2 + imgOffsetY;
                    ctx.drawImage(reportImg, dx, dy, sDw, sDh);
                }
                ctx.restore();
            }
            if (inputs.showGuides.checked) {
                ctx.textAlign = 'center';
                ctx.font = 'bold 36px "Microsoft YaHei"'; ctx.fillStyle = colors.main;
                ctx.fillText('〰️ 剪映中在此处添加【音频波形】 〰️', w/2, boxY + boxH + 120);
                ctx.font = '30px "Microsoft YaHei"'; ctx.fillStyle = 'rgba(255,255,255,0.15)';
                ctx.fillText('--- 字幕安全区 ---', w/2, boxY + boxH + 260);
                ctx.textAlign = 'left';
            }
            const by = 1750; ctx.fillStyle = colors.main;
            ctx.fillRect(margin, by-15, 6, 15); ctx.fillRect(margin+12, by-30, 6, 30); ctx.fillRect(margin+24, by-20, 6, 20); ctx.fillRect(margin+36, by-35, 6, 35); ctx.fillRect(margin+48, by-10, 6, 10);
            ctx.font = 'bold 40px "Microsoft YaHei", sans-serif'; ctx.fillStyle = '#64748b'; ctx.fillText("听见资本", margin + 70, by);
        }
    }

    // 绑定到 window 用于 HTML 按钮调用
    window.downloadThumbnail = function() {
        const currentFormat = getCurrentFormat();
        let formatName = '横版';
        if (currentFormat === 'vertical') formatName = '竖版';
        if (currentFormat === 'wx_cover') formatName = '视频号封面';
        if (currentFormat === 'podcast') formatName = '播客';
        if (currentFormat === 'video_bg') formatName = '视频背景';
        if (currentFormat === 'video_bg_audio') formatName = '音频背景';
        const link = document.createElement('a');
        const titleStr = inputs.mainTitle.value.replace(/\s+/g, '_').substring(0, 8).replace(/\n/g, '');
        link.download = `听见资本_${formatName}_${titleStr}.png`;
        link.href = canvas.toDataURL('image/png', 1.0);
        link.click();
    };

    // 初始运行
    updateLayoutAndDraw();
});
