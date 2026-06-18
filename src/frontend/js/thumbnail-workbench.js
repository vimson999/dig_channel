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
        footerText: document.getElementById('footerText'),
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
        pro: { main: '#BFA15F', glow: 'rgba(191, 161, 95, 0.4)' },
        macro: { main: '#FACC15', glow: 'rgba(250, 204, 21, 0.4)' },
        bull: { main: '#4ADE80', glow: 'rgba(74, 222, 128, 0.4)' },
        bear: { main: '#EF4444', glow: 'rgba(239, 68, 68, 0.4)' }
    };

    const layouts = {
        horizontal: { w: 1920, h: 1080, aspectClass: 'max-w-5xl aspect-video', title: '横版预览区', size: '1920 x 1080 (16:9)', btn: '下载横版封面 (1920x1080)' },
        horizontal_reverse: { w: 1920, h: 1080, aspectClass: 'max-w-5xl aspect-video', title: '横版反向预览区', size: '1920 x 1080 (16:9)', btn: '下载横版反向封面 (1920x1080)' },
        horizontal_v2: { w: 1920, h: 1080, aspectClass: 'max-w-5xl aspect-video', title: '横版 V2 霓虹预览区', size: '1920 x 1080 (16:9)', btn: '下载横版 V2 封面 (1920x1080)' },
        horizontal_stack: { w: 1920, h: 1080, aspectClass: 'max-w-5xl aspect-video', title: '横版上下分层预览', size: '1920 x 1080 (16:9)', btn: '下载横版上下封面 (1920x1080)' },
        horizontal_diagonal: { w: 1920, h: 1080, aspectClass: 'max-w-5xl aspect-video', title: '横版斜线对角预览', size: '1920 x 1080 (16:9)', btn: '下载横版斜线封面 (1920x1080)' },
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
    if (inputs.footerText) inputs.footerText.addEventListener('input', draw);
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
        if (!layout) {
            console.warn('⚠️ [WORKBENCH] 未知版式:', currentFormat);
            return;
        }
        imgOffsetX = 0;
        imgOffsetY = 0;
        imgScale = 1.0;
        
        // 更新容器类名
        uiElements.container.className = `canvas-container w-full ${layout.aspectClass}`;
        
        // 健壮地提取 max-width 类名并应用给 header，确保 header 宽度与 canvas 对齐
        const maxWidthClass = (layout.aspectClass || '').split(' ').find(c => c.startsWith('max-w-')) || '';
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
            } else if (format === 'horizontal_reverse') {
                // 1. 背景研报区：保持高清晰度 (98% 不透明度)，还原白底研报质感
                ctx.save();
                ctx.globalAlpha = 0.98;
                // 采用高度优先的填充逻辑，并靠左侧放置
                let dh = h; 
                let dw = reportImg.width * (h / reportImg.height);
                ctx.drawImage(reportImg, imgOffsetX, imgOffsetY, dw * imgScale, dh * imgScale);
                ctx.restore();

                // 2. 线性渐变羽化 (严格遵循 40% 开始过渡)
                const morganBlue = '#001529';
                const maskStart = w * 0.4;  // 768px (40%)
                const maskEnd = w * 0.55;   // 1056px (55% 处达到完全不透明)
                
                const grad = ctx.createLinearGradient(maskStart, 0, maskEnd, 0);
                grad.addColorStop(0, 'rgba(0, 21, 41, 0)'); // 40% 处完全露出研报
                grad.addColorStop(1, morganBlue);           // 55% 处完全遮挡
                
                ctx.fillStyle = grad;
                ctx.fillRect(maskStart, 0, maskEnd - maskStart, h);
                ctx.fillStyle = morganBlue;
                ctx.fillRect(maskEnd, 0, w - maskEnd, h);
            } else if (format === 'horizontal_v2') {
                const splitPos = w * 0.48;
                const neonColor = '#00FF66';
                const darkBg = '#050B14';

                // 1. 绘制研报底图 (回归全屏贴边)
                ctx.save();
                
                // 仅保留左侧微圆角 (16px)
                const r = 16;
                ctx.beginPath();
                ctx.moveTo(r, 0); ctx.lineTo(splitPos, 0);
                ctx.lineTo(splitPos, h); ctx.lineTo(r, h);
                ctx.quadraticCurveTo(0, h, 0, h - r); 
                ctx.lineTo(0, r); ctx.quadraticCurveTo(0, 0, r, 0);
                ctx.clip();

                // 2. 绘制图片 (基础清晰度)
                ctx.filter = 'brightness(0.95) contrast(1.02)';
                let dh = h; 
                let dw = reportImg.width * (h / reportImg.height);
                const zoom = 1.05;
                ctx.drawImage(reportImg, imgOffsetX, imgOffsetY, dw * imgScale * zoom, dh * imgScale * zoom);
                ctx.filter = 'none';

                // 3. 模拟顶部柔和暗角 (代替硬黑条)
                const topShadow = ctx.createLinearGradient(0, 0, 0, 120);
                topShadow.addColorStop(0, 'rgba(0, 0, 0, 0.75)');
                topShadow.addColorStop(1, 'transparent');
                ctx.fillStyle = topShadow;
                ctx.fillRect(0, 0, splitPos, 120);

                // 4. 核心：用【环境色渐变叠加】实现完美隐形 (彻底废弃 Mask)
                const blendOverlay = ctx.createLinearGradient(splitPos * 0.4, 0, splitPos, 0);
                blendOverlay.addColorStop(0, 'rgba(5, 11, 20, 0)'); // 透明
                blendOverlay.addColorStop(0.7, 'rgba(5, 11, 20, 0.95)'); // 平滑过渡
                blendOverlay.addColorStop(1, darkBg); // 严格对齐底色
                ctx.fillStyle = blendOverlay;
                ctx.fillRect(0, 0, splitPos + 2, h); // 稍微多出2px压缝
                
                ctx.restore(); // 退出裁剪与状态栈

                // 8. 绘制右侧氛围环境光
                ctx.save();
                ctx.fillStyle = darkBg;
                ctx.fillRect(splitPos, 0, w - splitPos, h);
                const radialGrad = ctx.createRadialGradient(
                    splitPos + (w - splitPos) * 0.35, h * 0.5, 0, 
                    splitPos + (w - splitPos) * 0.35, h * 0.5, h * 0.8
                );
                radialGrad.addColorStop(0, 'rgba(0, 255, 102, 0.15)');
                radialGrad.addColorStop(0.65, 'transparent');
                ctx.fillStyle = radialGrad;
                ctx.fillRect(splitPos, 0, w - splitPos, h);
                ctx.restore();

                // 9. 霓虹分割线
                ctx.save();
                const dividerH = h * 0.75;
                const dividerY = (h - dividerH) / 2;
                const dividerGrad = ctx.createLinearGradient(0, dividerY, 0, dividerY + dividerH);
                dividerGrad.addColorStop(0, 'transparent');
                dividerGrad.addColorStop(0.15, neonColor);
                dividerGrad.addColorStop(0.85, neonColor);
                dividerGrad.addColorStop(1, 'transparent');
                ctx.shadowColor = neonColor;
                ctx.shadowBlur = 12;
                ctx.fillStyle = dividerGrad;
                ctx.fillRect(splitPos - 2, dividerY, 4, dividerH);
                ctx.shadowBlur = 25;
                ctx.shadowColor = 'rgba(0, 255, 102, 0.45)';
                ctx.fillRect(splitPos - 2, dividerY, 4, dividerH);
                ctx.restore();
            } else if (format === 'horizontal_stack') {                targetWidth = 1920; targetHeight = 1080;
                let dw = reportImg.width * (targetHeight / reportImg.height);
                let sDw = dw * imgScale; let sDh = targetHeight * imgScale;
                drawX = 0 - (sDw - targetWidth) / 2 + imgOffsetX; 
                drawY = 0 - (sDh - targetHeight) / 2 + imgOffsetY;
                ctx.drawImage(reportImg, drawX, drawY, sDw, sDh);
                const mask = ctx.createLinearGradient(0, 400, 0, 1080);
                mask.addColorStop(0, 'rgba(10, 17, 40, 0)');
                mask.addColorStop(0.6, 'rgba(10, 17, 40, 0.9)'); 
                mask.addColorStop(1, '#0a1128');
                ctx.fillStyle = mask; ctx.fillRect(0, 0, w, h);
            } else if (format === 'horizontal_diagonal') {
                targetWidth = 1600; targetHeight = 1080;
                let dw = reportImg.width * (targetHeight / reportImg.height);
                let sDw = dw * imgScale; let sDh = targetHeight * imgScale;
                drawX = 600 - (sDw - targetWidth) / 2 + imgOffsetX; 
                drawY = 0 - (sDh - targetHeight) / 2 + imgOffsetY;
                ctx.drawImage(reportImg, drawX, drawY, sDw, sDh);
                ctx.beginPath();
                ctx.moveTo(0, 0);
                ctx.lineTo(1100, 0);
                ctx.lineTo(700, 1080);
                ctx.lineTo(0, 1080);
                ctx.closePath();
                const mask = ctx.createLinearGradient(0, 0, 1100, 0);
                mask.addColorStop(0, '#0a1128');
                mask.addColorStop(0.7, '#0a1128');
                mask.addColorStop(1, 'rgba(10, 17, 40, 0)');
                ctx.fillStyle = mask;
                ctx.fill();
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
        const footerText = inputs.footerText ? inputs.footerText.value : "听见资本";

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
            ctx.font = 'bold 45px "Microsoft YaHei", sans-serif'; ctx.fillStyle = '#94a3b8'; ctx.fillText(footerText, margin + 70, by);
        } else if (format === 'horizontal_reverse') {
            const margin = 1100; // 严格起始于 55% 的深色区
            
            // 机构标识
            ctx.font = 'bold 36px "Microsoft YaHei", sans-serif'; 
            ctx.fillStyle = colors.main;
            ctx.fillRect(margin, 160, 10, 45); 
            ctx.fillStyle = 'rgba(255, 255, 255, 0.9)';
            ctx.fillText(instText + " · 研报解构", margin + 30, 195);
            
            // 视觉对比：主标题大字 (135px 900)，副标题小字 (60px)
            ctx.font = '900 135px "Microsoft YaHei", "PingFang SC", sans-serif'; 
            ctx.fillStyle = colors.main;
            ctx.shadowBlur = 0; 
            let nextY = wrapText(ctx, mainText, margin, 400, 720, 155);
            
            if (subText) {
                // 副标题缩小一点 (60px) 制造强烈的主次感
                ctx.font = 'bold 60px "Microsoft YaHei", sans-serif'; 
                ctx.fillStyle = '#FFFFFF';
                wrapText(ctx, subText, margin, nextY + 40, 720, 85);
            }
            
            // 水印：移至左侧与标题对齐 (建立统一垂直轴线)
            const footerX = margin; 
            const footerY = 960; 
            
            ctx.fillStyle = 'rgba(255, 255, 255, 0.5)'; // 半透明白色
            ctx.font = 'bold 45px "Microsoft YaHei", sans-serif';
            ctx.fillText(footerText, footerX + 70, footerY);
            
            ctx.fillStyle = colors.main;
            ctx.fillRect(footerX, footerY-15, 6, 15); ctx.fillRect(footerX+12, footerY-30, 6, 30); ctx.fillRect(footerX+24, footerY-20, 6, 20); 
        } else if (format === 'horizontal_v2') {
            const margin = 1020; // 稍微左移，增加排版空间
            const neonColor = '#00FF66'; 
            
            // 1. 顶部机构标识
            ctx.font = 'bold 48px "Microsoft YaHei", sans-serif'; 
            ctx.fillStyle = '#C8FFD8'; 
            ctx.shadowColor = neonColor;
            ctx.shadowBlur = 10;
            ctx.fillText(instText + " · 研报解构", margin, 180);
            ctx.shadowBlur = 0;
            
            // 2. 主标题：多重发光效果 (158px 核心，提供 880px 宽度)
            ctx.font = '900 158px "Microsoft YaHei", "PingFang SC", sans-serif'; 
            const mainTextColor = '#C8FFD8'; 
            const maxTextWidth = 880; 
            
            ctx.save();
            ctx.shadowColor = 'rgba(0, 204, 82, 0.45)'; 
            ctx.shadowBlur = 50;
            ctx.fillStyle = mainTextColor;
            wrapText(ctx, mainText, margin, 420, maxTextWidth, 185);
            
            ctx.shadowColor = neonColor;
            ctx.shadowBlur = 25;
            wrapText(ctx, mainText, margin, 420, maxTextWidth, 185);
            
            ctx.shadowBlur = 10;
            let nextY = wrapText(ctx, mainText, margin, 420, maxTextWidth, 185);
            ctx.restore();
            
            // 3. 副标题 (85px)
            if (subText) {
                ctx.font = 'bold 85px "Microsoft YaHei", sans-serif'; 
                ctx.fillStyle = '#AACCBB'; 
                ctx.shadowColor = 'rgba(0, 255, 102, 0.35)';
                ctx.shadowBlur = 15;
                wrapText(ctx, subText, margin, nextY + 75, maxTextWidth, 105);
                ctx.shadowBlur = 0;
            }
            
            // 4. 左下角 Logo (极具质感的毛玻璃效果)
            const logoX = 75;
            const logoY = 960;
            ctx.save();
            // 模拟 backdrop-filter: blur + 边框高亮
            ctx.fillStyle = 'rgba(255, 255, 255, 0.18)'; 
            roundRect(ctx, logoX, logoY - 55, 310, 100, 12);
            ctx.fill();
            ctx.strokeStyle = 'rgba(255, 255, 255, 0.4)';
            ctx.lineWidth = 2.5;
            ctx.stroke();
            
            ctx.fillStyle = '#FFFFFF';
            ctx.font = 'bold 45px "Microsoft YaHei", sans-serif';
            ctx.fillRect(logoX + 25, logoY-10, 6, 20); 
            ctx.fillRect(logoX + 37, logoY-20, 6, 40); 
            ctx.fillRect(logoX + 49, logoY-15, 6, 30);
            ctx.fillText(footerText, logoX + 85, logoY + 15);
            ctx.restore();
        } else if (format === 'horizontal_stack') {
            const margin = 120;
            ctx.font = 'bold 40px "Microsoft YaHei", sans-serif'; ctx.fillStyle = colors.main;
            ctx.fillRect(margin, 580, 12, 45); ctx.fillStyle = '#e2e8f0';
            ctx.fillText(instText + " · 研报解构", margin + 30, 615);
            ctx.font = '900 130px "Microsoft YaHei", "PingFang SC", sans-serif'; ctx.fillStyle = colors.main;
            ctx.shadowColor = colors.glow; ctx.shadowBlur = 30; ctx.shadowOffsetY = 10;
            let nextY = wrapText(ctx, mainText, margin, 780, 1680, 150);
            ctx.shadowBlur = 0; ctx.shadowOffsetY = 0;
            if (subText) {
                ctx.font = 'bold 70px "Microsoft YaHei", sans-serif'; ctx.fillStyle = '#ffffff';
                ctx.shadowColor = 'rgba(0,0,0,0.8)'; ctx.shadowBlur = 10; ctx.shadowOffsetY = 5;
                wrapText(ctx, subText, margin, nextY + 40, 1680, 90);
                ctx.shadowBlur = 0; ctx.shadowOffsetY = 0;
            }
            const by = 1020; ctx.fillStyle = colors.main;
            ctx.fillRect(margin, by-15, 6, 15); ctx.fillRect(margin+12, by-30, 6, 30); ctx.fillRect(margin+24, by-20, 6, 20); ctx.fillRect(margin+36, by-35, 6, 35); ctx.fillRect(margin+48, by-10, 6, 10);
            ctx.font = 'bold 40px "Microsoft YaHei", sans-serif'; ctx.fillStyle = '#94a3b8'; ctx.fillText(footerText, margin + 70, by);
        } else if (format === 'horizontal_diagonal') {
            const margin = 100;
            ctx.font = 'bold 40px "Microsoft YaHei", sans-serif'; ctx.fillStyle = colors.main;
            ctx.fillRect(margin, 160, 12, 45); ctx.fillStyle = '#e2e8f0';
            ctx.fillText(instText + " · 研报解构", margin + 30, 195);
            ctx.font = '900 120px "Microsoft YaHei", "PingFang SC", sans-serif'; ctx.fillStyle = colors.main;
            ctx.shadowColor = colors.glow; ctx.shadowBlur = 30; ctx.shadowOffsetY = 10;
            let nextY = wrapText(ctx, mainText, margin, 400, 750, 140);
            ctx.shadowBlur = 0; ctx.shadowOffsetY = 0;
            if (subText) {
                ctx.font = 'bold 65px "Microsoft YaHei", sans-serif'; ctx.fillStyle = '#ffffff';
                ctx.shadowColor = 'rgba(0,0,0,0.8)'; ctx.shadowBlur = 10; ctx.shadowOffsetY = 5;
                wrapText(ctx, subText, margin, nextY + 60, 700, 85);
                ctx.shadowBlur = 0; ctx.shadowOffsetY = 0;
            }
            const by = 960; ctx.fillStyle = colors.main;
            ctx.fillRect(margin, by-15, 6, 15); ctx.fillRect(margin+12, by-30, 6, 30); ctx.fillRect(margin+24, by-20, 6, 20); ctx.fillRect(margin+36, by-35, 6, 35); ctx.fillRect(margin+48, by-10, 6, 10);
            ctx.font = 'bold 45px "Microsoft YaHei", sans-serif'; ctx.fillStyle = '#94a3b8'; ctx.fillText(footerText, margin + 70, by);
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
            ctx.font = 'bold 40px "Microsoft YaHei", sans-serif'; ctx.fillStyle = '#64748b'; ctx.fillText(footerText, margin + 70, by);
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
            ctx.font = 'bold 40px "Microsoft YaHei", sans-serif'; ctx.fillStyle = '#64748b'; ctx.fillText(footerText, margin + 70, by);
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
            const by = 1340; ctx.fillStyle = colors.main; ctx.fillRect(margin, by-20, 8, 20); ctx.fillRect(margin+16, by-40, 8, 40); ctx.fillRect(margin+32, by-25, 8, 25); ctx.fillRect(margin+48, by-45, 8, 45); ctx.fillRect(margin+64, by-15, 8, 15);
            ctx.font = 'bold 55px "Microsoft YaHei", sans-serif'; ctx.fillStyle = '#64748b'; ctx.fillText(footerText, margin + 95, by);
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
            const videoH = w * (9/16); const videoY = 620; 
            ctx.fillStyle = '#000000'; ctx.fillRect(0, videoY, w, videoH);
            if (inputs.showGuides.checked) {
                ctx.setLineDash([15, 15]); ctx.strokeStyle = 'rgba(255,255,255,0.2)'; ctx.lineWidth = 4;
                ctx.strokeRect(10, videoY + 10, w - 20, videoH - 20); ctx.setLineDash([]);
                ctx.font = 'bold 36px "Microsoft YaHei"'; ctx.fillStyle = '#4b5563'; ctx.textAlign = 'center';
                ctx.fillText('剪映剪辑时，将 16:9 视频放置于此黑框内', w/2, videoY + videoH/2);
                ctx.textAlign = 'left'; ctx.font = '30px "Microsoft YaHei"'; ctx.fillStyle = 'rgba(255,255,255,0.15)';
                ctx.textAlign = 'center'; ctx.fillText('--- 字幕安全区 ---', w/2, 1450); ctx.textAlign = 'left';
            }
            const by = 1750; ctx.fillStyle = colors.main;
            ctx.fillRect(margin, by-15, 6, 15); ctx.fillRect(margin+12, by-30, 6, 30); ctx.fillRect(margin+24, by-20, 6, 20); ctx.fillRect(margin+36, by-35, 6, 35); ctx.fillRect(margin+48, by-10, 6, 10);
            ctx.font = 'bold 40px "Microsoft YaHei", sans-serif'; ctx.fillStyle = '#64748b'; ctx.fillText(footerText, margin + 70, by);
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
            const boxY = Math.max(nextY + 80, 550); const boxH = 550;
            if (reportImg) {
                ctx.shadowColor = 'rgba(0,0,0,0.5)'; ctx.shadowBlur = 20; ctx.shadowOffsetY = 10;
                ctx.fillStyle = 'rgba(255,255,255,0.05)'; roundRect(ctx, margin, boxY, maxW, boxH, 20); ctx.fill();
                ctx.shadowBlur = 0; ctx.shadowOffsetY = 0; ctx.lineWidth = 4; ctx.strokeStyle = 'rgba(255,255,255,0.1)'; ctx.stroke();
                ctx.save(); roundRect(ctx, margin, boxY, maxW, boxH, 20); ctx.clip();
                const imgRatio = reportImg.width / reportImg.height; const targetRatio = maxW / boxH;
                let dw, dh, dx, dy;
                if (imgRatio > targetRatio) {
                    dh = boxH; dw = reportImg.width * (boxH / reportImg.height);
                    let sDw = dw * imgScale; let sDh = dh * imgScale;
                    dx = margin - (sDw - maxW)/2 + imgOffsetX; dy = boxY - (sDh - boxH)/2 + imgOffsetY;
                    ctx.drawImage(reportImg, dx, dy, sDw, sDh);
                } else {
                    dw = maxW; dh = reportImg.height * (maxW / reportImg.width);
                    let sDw = dw * imgScale; let sDh = dh * imgScale;
                    dx = margin - (sDw - maxW)/2 + imgOffsetX; dy = boxY - (sDh - boxH)/2 + imgOffsetY;
                    ctx.drawImage(reportImg, dx, dy, sDw, sDh);
                }
                ctx.restore();
            }
            if (inputs.showGuides.checked) {
                ctx.textAlign = 'center'; ctx.font = 'bold 36px "Microsoft YaHei"'; ctx.fillStyle = colors.main;
                ctx.fillText('〰️ 剪映中在此处添加【音频波形】 〰️', w/2, boxY + boxH + 120);
                ctx.font = '30px "Microsoft YaHei"'; ctx.fillStyle = 'rgba(255,255,255,0.15)';
                ctx.fillText('--- 字幕安全区 ---', w/2, boxY + boxH + 260); ctx.textAlign = 'left';
            }
            const by = 1750; ctx.fillStyle = colors.main;
            ctx.fillRect(margin, by-15, 6, 15); ctx.fillRect(margin+12, by-30, 6, 30); ctx.fillRect(margin+24, by-20, 6, 20); ctx.fillRect(margin+36, by-35, 6, 35); ctx.fillRect(margin+48, by-10, 6, 10);
            ctx.font = 'bold 40px "Microsoft YaHei", sans-serif'; ctx.fillStyle = '#64748b'; ctx.fillText(footerText, margin + 70, by);
        }
    }

    function loadWorkflowImage(dataUrl) {
        return new Promise((resolve, reject) => {
            if (!dataUrl) {
                resolve(null);
                return;
            }
            const img = new Image();
            img.onload = () => resolve(img);
            img.onerror = reject;
            img.src = dataUrl;
        });
    }

    function waitWorkflowFonts() {
        if (!document.fonts || !document.fonts.ready) return Promise.resolve();
        return Promise.race([
            document.fonts.ready.catch(() => {}),
            new Promise(resolve => setTimeout(resolve, 2000))
        ]);
    }

    window.renderWorkflowThumbnail = async function(payload = {}) {
        if (payload.format && layouts[payload.format]) {
            inputs.format.value = payload.format;
            updateLayoutAndDraw();
        }
        if (typeof payload.showGuides === 'boolean') inputs.showGuides.checked = payload.showGuides;
        if (payload.inst) inputs.inst.value = payload.inst;
        if (payload.mainTitle !== undefined) inputs.mainTitle.value = payload.mainTitle || '';
        if (payload.subTitle !== undefined) inputs.subTitle.value = payload.subTitle || '';
        if (payload.footerText !== undefined) inputs.footerText.value = payload.footerText || '';
        if (payload.emotion) {
            inputs.emotions.forEach(radio => {
                radio.checked = radio.value === payload.emotion;
            });
        }
        if (payload.imageDataUrl) {
            reportImg = await loadWorkflowImage(payload.imageDataUrl);
            imgOffsetX = Number(payload.imageOffsetX || 0);
            imgOffsetY = Number(payload.imageOffsetY || 0);
            imgScale = Number(payload.imageScale || 1);
            if (inputs.clearImg) inputs.clearImg.classList.remove('hidden');
            canvas.style.cursor = 'grab';
        } else if (payload.clearImage) {
            reportImg = null;
            imgOffsetX = 0;
            imgOffsetY = 0;
            imgScale = 1.0;
            if (inputs.upload) inputs.upload.value = '';
            if (inputs.clearImg) inputs.clearImg.classList.add('hidden');
            canvas.style.cursor = 'default';
        }
        await waitWorkflowFonts();
        draw();
        return {
            dataUrl: canvas.toDataURL(payload.mimeType || 'image/png', payload.quality || 1.0),
            width: canvas.width,
            height: canvas.height
        };
    };

    window.__workflowThumbnailReady = true;

    // 绑定到 window 用于 HTML 按钮调用
    window.downloadThumbnail = function() {
        const currentFormat = getCurrentFormat();
        const footerText = (inputs.footerText && inputs.footerText.value) ? inputs.footerText.value : '听见资本';
        let formatName = '横版';
        if (currentFormat === 'horizontal_reverse') formatName = '横版(反向)';
        if (currentFormat === 'horizontal_v2') formatName = '横版(V2霓虹)';
        if (currentFormat === 'horizontal_stack') formatName = '横版(上下)';
        if (currentFormat === 'horizontal_diagonal') formatName = '横版(斜线)';
        if (currentFormat === 'vertical') formatName = '竖版';
        if (currentFormat === 'wx_cover') formatName = '视频号封面';
        if (currentFormat === 'podcast') formatName = '播客';
        if (currentFormat === 'video_bg') formatName = '视频背景';
        if (currentFormat === 'video_bg_audio') formatName = '音频背景';
        const link = document.createElement('a');
        const titleStr = inputs.mainTitle.value.replace(/\s+/g, '_').substring(0, 8).replace(/\n/g, '');
        link.download = `${footerText}_${formatName}_${titleStr}.png`;
        link.href = canvas.toDataURL('image/png', 1.0);
        link.click();
    };

    // 初始运行
    updateLayoutAndDraw();
});
