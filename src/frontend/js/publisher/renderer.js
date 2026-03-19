
/**
 * CanvasRenderer - 专门负责发布工作台的视觉渲染
 */
const CanvasRenderer = {
    themes: {
        main: '#002D72',
        sub: '#FFD700',
        tag: '#002D72',
        date: '#ffffff',
        stroke: 'rgba(255,255,255,1.0)',
        bg: '#f0f4f8'
    },

    ctaStyles: {
        'premium_dark': { bg: 'rgba(25, 28, 36, 0.85)', text: 'rgba(255,255,255,0.95)', action: '#ffffff', border: 'rgba(255,255,255,0.15)' },
        'nomura_red': { bg: 'rgba(128, 20, 30, 0.88)', text: 'rgba(255,255,255,0.95)', action: '#ffffff', border: 'rgba(255,255,255,0.2)' },
        'institutional_blue': { bg: 'rgba(30, 64, 175, 0.85)', text: 'rgba(255,255,255,0.95)', action: '#ffffff', border: 'rgba(255,255,255,0.2)' },
        'investment_gold': { bg: 'rgba(212, 175, 55, 0.85)', text: 'rgba(25, 30, 40, 0.95)', action: '#000000', border: 'rgba(0,0,0,0.1)' }
    },

    /**
     * 核心：绘制封面预览
     */
    drawCover(ctx, canvas, images, config) {
        if (!ctx) return;
        const w = canvas.width, h = canvas.height;
        const isScreenshot = (config.mode === 'screenshot');
        ctx.clearRect(0, 0, w, h);
        
        // 1. 背景渲染
        if (isScreenshot) {
            if (images.custom.src && images.custom.complete && images.custom.naturalWidth > 0) {
                ctx.save();
                ctx.beginPath(); ctx.rect(0, 0, w, h * 0.45); ctx.clip();
                const trans = config.transform || { x: 0, y: 0, scale: 1 };
                this.drawImageCover(ctx, images.custom, 0, 0, w, h * 0.45, trans.x, trans.y, trans.scale);
                ctx.restore();
            } else {
                // 截图模式占位：深蓝色顶部 + 提示文字
                ctx.fillStyle = '#002140'; ctx.fillRect(0, 0, w, h * 0.45);
                ctx.fillStyle = 'rgba(255, 255, 255, 0.2)'; ctx.textAlign = 'center'; ctx.font = 'bold 40px sans-serif';
                ctx.fillText('等待上传研报截图...', w/2, h * 0.22);
                ctx.textAlign = 'left'; // 恢复对齐
            }
            ctx.fillStyle = '#001529'; ctx.fillRect(0, h * 0.45, w, h * 0.55);
        } else if (images.bg.complete && images.bg.naturalWidth > 0) {
            this.drawImageCover(ctx, images.bg, 0, 0, w, h);
            const opacity = config.overlayOpacity || 0.5; // 使用动态透明度
            const maskGradient = ctx.createLinearGradient(0, 0, w * 0.6, 0);
            maskGradient.addColorStop(0, `rgba(0, 15, 30, ${opacity})`); maskGradient.addColorStop(1, `rgba(0, 15, 30, 0)`);
            ctx.fillStyle = maskGradient; ctx.fillRect(0, 0, w, h);
        } else { ctx.fillStyle = this.themes.bg; ctx.fillRect(0, 0, w, h); }

        const leftMargin = 120;
        let currentY = isScreenshot ? 520 : 180;
        
        // 2. 标签渲染
        if (!isScreenshot) {
            ctx.font = `900 30px sans-serif`;
            const tm = ctx.measureText(config.tagText);
            ctx.save(); ctx.beginPath(); ctx.roundRect(leftMargin - 24, currentY - 12, tm.width + 48, 30 + 24, 4); ctx.fillStyle = "#FFFFFF"; ctx.fill(); ctx.restore();
            ctx.fillStyle = '#333'; ctx.textBaseline = 'top'; ctx.fillText(config.tagText, leftMargin, currentY); 
            currentY += 130;
        }

        // 3. 主副标题渲染
        const mainFontSize = 110;
        ctx.font = `900 ${mainFontSize}px sans-serif`;
        const mainColor = isScreenshot ? '#FFD700' : this.themes.main;
        config.mainTitle.split('\n').forEach(l => {
            if (!l.trim()) return;
            if (isScreenshot) { ctx.fillStyle = mainColor; ctx.textBaseline = 'top'; ctx.fillText(l, leftMargin, currentY); }
            else { this.drawHaloText(ctx, l, leftMargin, currentY, mainColor, this.themes.stroke, mainFontSize); }
            currentY += mainFontSize * 1.3; 
        });

        if (config.subTitle) {
            const subColor = isScreenshot ? '#FFFFFF' : this.themes.sub;
            if (isScreenshot) { ctx.font = `700 56px sans-serif`; ctx.fillStyle = subColor; ctx.fillText(config.subTitle, leftMargin, currentY + 15); }
            else { this.drawHaloText(ctx, config.subTitle, leftMargin, currentY + 15, subColor, this.themes.stroke, 56, "700"); }
        }

        // 4. 页脚日期
        const footerY = h - 100;
        const footerColor = isScreenshot ? 'rgba(200, 200, 200, 0.8)' : this.themes.date;
        ctx.save(); ctx.beginPath(); ctx.moveTo(leftMargin, footerY - 47); ctx.lineTo(leftMargin + 60, footerY - 47); ctx.strokeStyle = footerColor; ctx.lineWidth = 2; ctx.stroke();
        ctx.font = `600 32px sans-serif`; ctx.fillStyle = footerColor; ctx.fillText(config.dateText, leftMargin, footerY - 32); ctx.restore();
    },

    /**
     * 核心：绘制 9:16 竖版封面 (PRD 实现)
     */
    drawVerticalCover(ctx, canvas, images, config) {
        if (!ctx) return;
        const w = canvas.width, h = canvas.height;
        ctx.clearRect(0, 0, w, h);

        // 1. 区域划分
        const splitY = 700;
        
        // 2. 背景绘制
        ctx.fillStyle = '#FFFFFF';
        ctx.fillRect(0, 0, w, splitY);
        
        // --- 新增：阴影过渡 (纸张悬浮感) ---
        const shadowH = 60;
        const gradShadow = ctx.createLinearGradient(0, splitY - shadowH, 0, splitY);
        gradShadow.addColorStop(0, 'rgba(0,0,0,0)');
        gradShadow.addColorStop(1, 'rgba(0,0,0,0.08)');
        ctx.fillStyle = gradShadow;
        ctx.fillRect(0, splitY - shadowH, w, shadowH);

        ctx.fillStyle = '#051C33';
        ctx.fillRect(0, splitY, w, h - splitY);

        // 3. 截图区域渲染 (Object-fit: contain 逻辑)
        if (images.v_screenshot && images.v_screenshot.complete && images.v_screenshot.naturalWidth) {
            const img = images.v_screenshot;
            const scale = Math.min(w / img.naturalWidth, splitY / img.naturalHeight);
            const nw = img.naturalWidth * scale;
            const nh = img.naturalHeight * scale;
            const nx = (w - nw) / 2;
            const ny = (splitY - nh) / 2;
            ctx.drawImage(img, nx, ny, nw, nh);
            
            // 细微分割线
            ctx.strokeStyle = 'rgba(0,0,0,0.05)';
            ctx.lineWidth = 1;
            ctx.beginPath(); ctx.moveTo(0, splitY); ctx.lineTo(w, splitY); ctx.stroke();
        } else {
            ctx.fillStyle = '#F8F9FA';
            ctx.fillRect(40, 40, w - 80, splitY - 80);
            ctx.fillStyle = '#DEE2E6';
            ctx.textAlign = 'center'; ctx.font = 'bold 30px sans-serif';
            ctx.fillText('等待上传研报截图...', w / 2, splitY / 2);
        }

        // 4. 文字排版规则
        const leftPadding = 80;
        const maxTextW = w * 0.82 - leftPadding; // 安全区控制
        let currentY = splitY + 120;

        // 主标题绘制 (亮黄色 #FFD700)
        let mainFs = 95;
        if (config.mainTitle.length > 25) mainFs = 82;
        ctx.font = `900 ${mainFs}px sans-serif`;
        ctx.fillStyle = '#FFD700';
        ctx.textAlign = 'left';
        ctx.textBaseline = 'top';

        const mainLines = this._wrapText(ctx, config.mainTitle, maxTextW).slice(0, 4);
        mainLines.forEach(line => {
            ctx.fillText(line, leftPadding, currentY);
            currentY += mainFs * 1.35;
        });

        // 副标题绘制 (纯白 #FFFFFF)
        currentY += 40;
        const subFs = 55;
        ctx.font = `500 ${subFs}px sans-serif`;
        ctx.fillStyle = '#FFFFFF';
        const subLines = this._wrapText(ctx, config.subTitle, maxTextW).slice(0, 3);
        subLines.forEach(line => {
            ctx.fillText(line, leftPadding, currentY);
            currentY += subFs * 1.5;
        });

        // 5. 底部品牌标识 (上移至离底部 400px，避开 UI 遮挡)
        const brandY = h - 400; 
        ctx.fillStyle = 'rgba(255, 255, 255, 0.6)';
        // 精致短横线 (位于文字上方 15px)
        ctx.fillRect(leftPadding, brandY - 45, 100, 2);
        // 品牌文字
        ctx.font = '500 38px sans-serif';
        ctx.fillText(config.dateText, leftPadding, brandY - 30);
    },

    /**
     * 工具：文字自动换行 (改进版：支持词组保护和手动换行)
     */
    _wrapText(ctx, text, maxWidth) {
        const lines = [];
        const paragraphs = text.split('\n');
        
        paragraphs.forEach(p => {
            // 如果是空行，也保留一个空位
            if (p.trim() === '') {
                lines.push('');
                return;
            }
            
            // 改进：使用更细粒度的分词（正则匹配中文字符或连续的英文/数字）
            const tokens = p.match(/[\u4e00-\u9fa5]|([a-zA-Z0-9\.]+)|[^\u4e00-\u9fa5]/g) || [];
            let currentLine = '';
            
            tokens.forEach(token => {
                const testLine = currentLine + token;
                const metrics = ctx.measureText(testLine);
                if (metrics.width > maxWidth && currentLine.length > 0) {
                    lines.push(currentLine);
                    currentLine = token;
                } else {
                    currentLine = testLine;
                }
            });
            lines.push(currentLine);
        });
        return lines;
    },

    /**
     * 核心：绘制视频正文 CTA
     */
    drawVideoPreview(ctx, canvas, image, config) {
        if (!ctx) return;
        const w = canvas.width, h = canvas.height;
        ctx.clearRect(0, 0, w, h);
        
        if (image.src && image.complete) {
            const trans = config.transform || { x: 0, y: 0, scale: 1 };
            this.drawImageCover(ctx, image, 0, 0, w, h, trans.x, trans.y, trans.scale);
            if (config.enabled && config.text) {
                ctx.save();
                const rawLines = config.text.split('\n').filter(l => l.trim());
                if (rawLines.length === 0) { ctx.restore(); return; }

                const s = this.ctaStyles[config.style] || this.ctaStyles.premium_dark;
                const isFullBar = config.position.includes('bar');

                if (isFullBar) {
                    this._renderFullBar(ctx, w, h, rawLines, config.position, s);
                } else {
                    this._renderFloatingCard(ctx, w, h, rawLines, config.position, s);
                }
                ctx.restore();
            }
        } else {
            ctx.fillStyle = '#1e293b'; ctx.fillRect(0, 0, w, h);
        }
    },

    _renderFullBar(ctx, w, h, lines, position, s) {
        const barHeight = 140; 
        const barY = (position === 'bottom_bar') ? (h - barHeight) : 0;
        ctx.fillStyle = s.bg; ctx.fillRect(0, barY, w, barHeight);
        ctx.strokeStyle = s.border; ctx.lineWidth = 2; ctx.beginPath();
        if (position === 'bottom_bar') { ctx.moveTo(0, barY); ctx.lineTo(w, barY); }
        else { ctx.moveTo(0, barHeight); ctx.lineTo(w, barHeight); }
        ctx.stroke();

        const hMargin = 100; const centerY = barY + (barHeight / 2);
        ctx.textAlign = 'left'; ctx.textBaseline = 'middle';
        ctx.font = `500 42px sans-serif`; ctx.fillStyle = s.text;
        ctx.fillText(lines[0], hMargin, centerY);

        ctx.textAlign = 'right'; ctx.font = `600 48px sans-serif`; ctx.fillStyle = s.action;
        const actionTxt = lines[1] || lines[0];
        const displayTxt = (actionTxt.startsWith('↓') || actionTxt.includes('PDF')) ? actionTxt : "↓ " + actionTxt;
        ctx.fillText(displayTxt, w - hMargin, centerY);
    },

    _renderFloatingCard(ctx, w, h, lines, position, s) {
        const metrics = lines.map((line, i) => {
            let fs = 32, fw = '400', color = s.text;
            if (i === 0) { fs = 48; fw = '500'; } 
            else if (i === lines.length - 1 && lines.length > 1) { 
                fs = 48; fw = '600'; color = s.action; 
            } 
            ctx.font = `${fw} ${fs}px sans-serif`;
            return { text: line, width: ctx.measureText(line).width, height: fs, fw, color };
        });

        const maxW = Math.max(...metrics.map(m => m.width));
        const lineSpacing = 25; const totalTextH = metrics.reduce((sum, m) => sum + m.height, 0) + (metrics.length - 1) * lineSpacing;
        const paddingH = 80, paddingV = 60;
        const boxW = maxW + paddingH * 2; const boxH = totalTextH + paddingV * 2;
        const safeR = 120, safeB = 220; 
        
        let bx = w - boxW - safeR;
        let by = (position === 'right_center') ? (h - boxH) / 2 : (h - boxH - safeB);

        // 双层阴影系统
        ctx.shadowColor = 'rgba(0,0,0,0.4)'; ctx.shadowBlur = 60; ctx.shadowOffsetY = 25;
        ctx.fillStyle = s.bg; ctx.beginPath(); ctx.roundRect(bx, by, boxW, boxH, 32); ctx.fill();
        ctx.shadowColor = 'rgba(0,0,0,0.2)'; ctx.shadowBlur = 10; ctx.shadowOffsetY = 5;
        ctx.strokeStyle = 'rgba(255,255,255,0.15)'; ctx.lineWidth = 2; ctx.stroke();
        
        ctx.shadowColor = 'transparent'; ctx.textAlign = 'center'; ctx.textBaseline = 'top';
        let currY = by + paddingV;
        metrics.forEach((m, i) => {
            ctx.font = `${m.fw} ${m.height}px sans-serif`; ctx.fillStyle = m.color;
            let txt = m.text;
            if (i === metrics.length - 1 && lines.length > 1 && !txt.startsWith('↓') && !txt.includes('PDF')) txt = "↓ " + txt;
            ctx.fillText(txt, bx + boxW / 2, currY); currY += m.height + lineSpacing;
        });
    },

    /**
     * 工具：Halo 文字
     */
    drawHaloText(ctx, text, x, y, color, strokeColor, fontSize, fontWeight = "900") {
        ctx.save(); ctx.font = `${fontWeight} ${fontSize}px sans-serif`; ctx.textBaseline = 'top';
        ctx.strokeStyle = strokeColor; ctx.lineWidth = fontSize * 0.04; ctx.lineJoin = 'round'; ctx.strokeText(text, x, y);
        ctx.fillStyle = color; ctx.fillText(text, x, y); ctx.restore();
    },

    /**
     * 工具：图片 Cover 填充 (支持平移和缩放)
     */
    drawImageCover(ctx, img, x, y, w, h, offsetX = 0, offsetY = 0, scale = 1) {
        if (!img.naturalWidth) return;
        const r = img.naturalWidth / img.naturalHeight, cr = w / h;
        let rw, rh, rx, ry;
        if (r > cr) { 
            rh = h; rw = h * r; rx = x - (rw - w) / 2; ry = y; 
        } else { 
            rw = w; rh = w / r; rx = x; ry = y; 
        }
        
        // 应用自定义变换 (相对于填充后的尺寸)
        // 缩放围绕中心进行
        const sw = rw * scale;
        const sh = rh * scale;
        const finalX = rx + (rw - sw) / 2 + offsetX;
        const finalY = ry + (rh - sh) / 2 + offsetY;
        
        ctx.drawImage(img, finalX, finalY, sw, sh);
    }
};

window.CanvasRenderer = CanvasRenderer;
