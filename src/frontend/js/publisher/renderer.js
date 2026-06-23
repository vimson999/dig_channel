
const FONT_STACK = '"PingFang SC", "PingFang TC", "Source Han Sans SC", "Source Han Sans CN", "Microsoft YaHei", sans-serif';

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
        'police_yellow': { bg: 'rgba(255, 204, 0, 0.95)', text: '#000000', action: '#000000', border: 'rgba(0,0,0,0.2)' },
        'institutional_blue': { bg: 'rgba(30, 64, 175, 0.85)', text: 'rgba(255,255,255,0.95)', action: '#ffffff', border: 'rgba(255,255,255,0.2)' },
        'investment_gold': { bg: 'rgba(212, 175, 55, 0.85)', text: 'rgba(25, 30, 40, 0.95)', action: '#000000', border: 'rgba(0,0,0,0.1)' }
    },

    /**
     * 核心：绘制封面预览
     */
    drawCover(ctx, canvas, images, config) {
        if (!ctx) return;
        const w = canvas.width, h = canvas.height;
        const mode = config.mode || 'preset';
        const isScreenshot = (mode === 'screenshot');
        const isPodcast = (mode === 'podcast');
        const isScreenshotStyle = isScreenshot || isPodcast;

        ctx.clearRect(0, 0, w, h);
        
        // 1. 背景渲染
        if (isScreenshot) {
            const screenshotH = Math.round(h * 0.41);
            if (images.custom.src && images.custom.complete && images.custom.naturalWidth > 0) {
                ctx.save();
                ctx.beginPath(); ctx.rect(0, 0, w, screenshotH); ctx.clip();
                const trans = config.transform || { x: 0, y: 0, scale: 1 };
                this.drawImageCover(ctx, images.custom, 0, 0, w, screenshotH, trans.x, trans.y, trans.scale, { anchorY: 0, quality: 'high' });
                const topShade = ctx.createLinearGradient(0, screenshotH - 90, 0, screenshotH);
                topShade.addColorStop(0, 'rgba(255,255,255,0)');
                topShade.addColorStop(1, 'rgba(255,255,255,0.22)');
                ctx.fillStyle = topShade;
                ctx.fillRect(0, Math.max(0, screenshotH - 90), w, 90);
                ctx.restore();
            } else {
                // 截图模式占位：深蓝色顶部 + 提示文字
                ctx.fillStyle = '#f8fafc'; ctx.fillRect(0, 0, w, screenshotH);
                ctx.fillStyle = 'rgba(100, 116, 139, 0.42)'; ctx.textAlign = 'center'; ctx.font = `bold 40px ${FONT_STACK}`;
                ctx.fillText('等待上传研报截图...', w/2, screenshotH * 0.5);
                ctx.textAlign = 'left';
            }
            const titleBg = ctx.createLinearGradient(0, screenshotH, 0, h);
            titleBg.addColorStop(0, '#082136');
            titleBg.addColorStop(0.55, '#061827');
            titleBg.addColorStop(1, '#03111D');
            ctx.fillStyle = titleBg;
            ctx.fillRect(0, screenshotH, w, h - screenshotH);

            const titleGlow = ctx.createRadialGradient(w * 0.24, screenshotH + 185, 0, w * 0.24, screenshotH + 185, w * 0.58);
            titleGlow.addColorStop(0, 'rgba(229, 200, 75, 0.075)');
            titleGlow.addColorStop(1, 'rgba(229, 200, 75, 0)');
            ctx.fillStyle = titleGlow;
            ctx.fillRect(0, screenshotH, w, h - screenshotH);

            ctx.fillStyle = '#DCC457';
            ctx.fillRect(0, screenshotH, w, 4);
            const splitShadow = ctx.createLinearGradient(0, screenshotH, 0, screenshotH + 70);
            splitShadow.addColorStop(0, 'rgba(0,0,0,0.26)');
            splitShadow.addColorStop(1, 'rgba(0,0,0,0)');
            ctx.fillStyle = splitShadow;
            ctx.fillRect(0, screenshotH, w, 70);
        } else if (isPodcast) {
            if (images.custom.src && images.custom.complete && images.custom.naturalWidth > 0) {
                const trans = config.transform || { x: 0, y: 0, scale: 1 };
                this.drawImageCover(ctx, images.custom, 0, 0, w, h, trans.x, trans.y, trans.scale);
                // 播客模式全屏遮罩：左侧深色渐变 (更深一点以增加对比度)
                const maskGradient = ctx.createLinearGradient(0, 0, w * 0.8, 0);
                maskGradient.addColorStop(0, 'rgba(10, 10, 10, 0.92)');
                maskGradient.addColorStop(0.4, 'rgba(10, 10, 10, 0.6)');
                maskGradient.addColorStop(1, 'rgba(10, 10, 10, 0)');
                ctx.fillStyle = maskGradient; ctx.fillRect(0, 0, w, h);
            } else {
                // 【重构】：径向渐变背景 (营造空间深度)
                // 中心点位于标题附近的 (w/2, h*0.4)，扩散半径设为宽度的 80% 以确保过渡平滑
                const bgGrad = ctx.createRadialGradient(w/2, h * 0.4, 0, w/2, h * 0.4, w * 0.8);
                bgGrad.addColorStop(0, '#1C1916'); // 中心：温润的极暗金灰
                bgGrad.addColorStop(1, '#0A0A0A'); // 边缘：深邃近黑
                ctx.fillStyle = bgGrad;
                ctx.fillRect(0, 0, w, h);
            }
        } else if (images.bg.complete && images.bg.naturalWidth > 0) {
            this.drawImageCover(ctx, images.bg, 0, 0, w, h);
            const opacity = config.overlayOpacity || 0.5; // 使用动态透明度
            const maskGradient = ctx.createLinearGradient(0, 0, w * 0.6, 0);
            maskGradient.addColorStop(0, `rgba(0, 15, 30, ${opacity})`); maskGradient.addColorStop(1, `rgba(0, 15, 30, 0)`);
            ctx.fillStyle = maskGradient; ctx.fillRect(0, 0, w, h);
        } else { ctx.fillStyle = this.themes.bg; ctx.fillRect(0, 0, w, h); }

        if (isScreenshot) {
            this._drawScreenshotCoverContent(ctx, w, h, config);
            return;
        }

        const leftMargin = 120;
        const centerX = w / 2; // 定义中轴线

        // 2. 【重构】：播客模式标签“皇冠化” (顶级金融徽章质感)
        if (isPodcast) {
            const tagTop = 180; 
            const barHeight = 90; 
            const tagPadding = 50;
            ctx.save();
            ctx.font = `bold 54px ${FONT_STACK}`; 
            const tagText = '内资机构 · 闭门会';
            const textWidth = ctx.measureText(tagText).width;
            const tagWidth = textWidth + tagPadding * 2;
            const tagX = centerX - tagWidth / 2; 

            // --- 1. 强化外部晕影 (专属圈层氛围) ---
            ctx.shadowColor = 'rgba(212, 175, 55, 0.3)';
            ctx.shadowBlur = 15;
            ctx.shadowOffsetX = 0;
            ctx.shadowOffsetY = 0;
            
            // --- 2. 标签表面质感升级 (金属线性渐变) ---
            const tagGrad = ctx.createLinearGradient(tagX, tagTop, tagX + tagWidth, tagTop + barHeight);
            tagGrad.addColorStop(0, '#F0C420'); // 左上：亮金色
            tagGrad.addColorStop(1, '#D4AF37'); // 右下：标准机构金
            
            ctx.fillStyle = tagGrad;
            ctx.beginPath();
            ctx.roundRect(tagX, tagTop, tagWidth, barHeight, 12);
            ctx.fill();

            // --- 3. 微小表面光泽扫掠 (精致感增强) ---
            ctx.shadowColor = 'transparent'; // 关闭阴影避免污染细线
            ctx.strokeStyle = 'rgba(255, 255, 255, 0.15)';
            ctx.lineWidth = 0.5;
            ctx.beginPath();
            ctx.moveTo(tagX + 5, tagTop + barHeight * 0.25);
            ctx.lineTo(tagX + tagWidth - 5, tagTop + barHeight * 0.25);
            ctx.stroke();

            // --- 4. 文本颜色微调 (深金色提升层次) ---
            ctx.fillStyle = '#705010'; 
            ctx.textAlign = 'center';
            ctx.textBaseline = 'middle';
            ctx.fillText(tagText, centerX, tagTop + barHeight / 2 + 4);
            ctx.restore();
        }

        // 重新分配 Y 轴空间：播客模式主标题从 380px 开始
        let currentY = isScreenshot ? 520 : (isPodcast ? 380 : 180);
        
        // 2. 标签渲染
        if (!isScreenshotStyle) {
            ctx.font = `30px ${FONT_STACK}`;
            const tm = ctx.measureText(config.tagText);
            ctx.save(); ctx.beginPath(); ctx.roundRect(leftMargin - 24, currentY - 12, tm.width + 48, 30 + 24, 4); ctx.fillStyle = "#FFFFFF"; ctx.fill(); ctx.restore();
            ctx.fillStyle = '#333'; ctx.textBaseline = 'top'; ctx.fillText(config.tagText, leftMargin, currentY); 
            currentY += 130;
        }

        // 3. 主副标题渲染
        const mainFontSize = 110;
        ctx.font = `${mainFontSize}px ${FONT_STACK}`;
        
        // 【隔离逻辑】：仅播客模式使用机构金，截图模式保留原样 #FFD700
        const mainColor = isPodcast ? '#D4AF37' : (isScreenshot ? '#FFD700' : this.themes.main);
        
        ctx.textAlign = 'left';
        ctx.textBaseline = 'top';

        config.mainTitle.split('\n').forEach(l => {
            if (!l.trim()) return;
            // 【居中优化】：播客模式采用居中对齐
            const targetX = isPodcast ? centerX : leftMargin;
            const textAlign = isPodcast ? 'center' : 'left';
            ctx.textAlign = textAlign;

            if (isScreenshotStyle) { 
                ctx.fillStyle = mainColor; 
                ctx.fillText(l, targetX, currentY); 
            }
            else { 
                this.drawHaloText(ctx, l, targetX, currentY, mainColor, this.themes.stroke, mainFontSize, "normal"); 
            }
            // 【行距优化】：播客模式拉大行距至 1.55x，提升呼吸感
            currentY += mainFontSize * (isPodcast ? 1.55 : 1.3); 
        });

        const subTitleLines = String(config.subTitle || '').split('\n').map(l => l.trim()).filter(Boolean);
        if (subTitleLines.length) {
            // 【段距优化】：播客模式显著增加主副标题间距
            currentY += (isPodcast ? 60 : 15); 
            const targetX = isPodcast ? centerX : leftMargin;
            const textAlign = isPodcast ? 'center' : 'left';
            ctx.textAlign = textAlign;

            const subColor = isScreenshotStyle ? '#FFFFFF' : this.themes.sub;
            const subFontSize = 56;
            ctx.font = `${subFontSize}px ${FONT_STACK}`;
            subTitleLines.forEach(line => {
                if (isScreenshotStyle) {
                    ctx.fillStyle = subColor;
                    ctx.fillText(line, targetX, currentY);
                }
                else {
                    this.drawHaloText(ctx, line, targetX, currentY, subColor, this.themes.stroke, subFontSize, "normal");
                }
                currentY += subFontSize * 1.25;
            });
        }

        // 4. 页脚日期
        const footerY = h - (isPodcast ? 80 : 100);
        const footerColor = isScreenshotStyle ? 'rgba(255, 255, 255, 0.8)' : this.themes.date;
        ctx.save(); 
        
        if (isPodcast) {
            // 【页脚居中】：播客模式页脚整体居中
            ctx.fillStyle = footerColor;
            ctx.font = `600 32px ${FONT_STACK}`;
            ctx.textAlign = 'center';
            
            const iconSize = 28;
            const dateText = config.dateText;
            const textWidth = ctx.measureText(dateText).width;
            const totalFooterW = iconSize + 15 + textWidth;
            const footerStartX = centerX - totalFooterW / 2;

            this._drawCalendarIcon(ctx, footerStartX, footerY - 33, iconSize);
            ctx.fillText(dateText, footerStartX + iconSize + 15 + textWidth / 2, footerY - 32);
        } else {
            ctx.textAlign = 'left';
            ctx.beginPath(); ctx.moveTo(leftMargin, footerY - 47); ctx.lineTo(leftMargin + 60, footerY - 47); ctx.strokeStyle = footerColor; ctx.lineWidth = 2; ctx.stroke();
            ctx.font = `600 32px ${FONT_STACK}`; ctx.fillStyle = footerColor; ctx.fillText(config.dateText, leftMargin, footerY - 32); 
        }
        ctx.restore();
    },

    /**
     * 内部工具：绘制极简日历图标
     */
    _drawCalendarIcon(ctx, x, y, size) {
        ctx.save();
        ctx.strokeStyle = ctx.fillStyle;
        ctx.lineWidth = 2;
        ctx.strokeRect(x, y + size*0.2, size, size * 0.8);
        ctx.beginPath();
        ctx.moveTo(x, y + size*0.45); ctx.lineTo(x + size, y + size*0.45);
        ctx.moveTo(x + size*0.25, y); ctx.lineTo(x + size*0.25, y + size*0.3);
        ctx.moveTo(x + size*0.75, y); ctx.lineTo(x + size*0.75, y + size*0.3);
        ctx.stroke();
        ctx.restore();
    },

    _drawScreenshotCoverContent(ctx, w, h, config) {
        const splitY = Math.round(h * 0.41);
        const left = 120;
        const right = 180;
        const maxTextW = w - left - right;
        let currentY = splitY + 74;

        ctx.save();
        ctx.textAlign = 'left';
        ctx.textBaseline = 'top';

        ctx.fillStyle = '#DCC457';
        ctx.fillRect(left, currentY - 30, 88, 4);

        const titleText = String(config.mainTitle || '').trim() || '研报核心观点解读';
        const fittedTitle = this._fitWrappedLines(ctx, titleText, maxTextW, {
            weight: 900,
            baseSize: 86,
            minSize: 60,
            maxLines: 3,
            lineHeight: 1.20,
        });

        this._drawTitleLinesWithAccent(ctx, fittedTitle.lines, left, currentY, fittedTitle.fontSize, fittedTitle.lineHeight);
        currentY += fittedTitle.lines.length * fittedTitle.lineHeight + 38;

        const subText = String(config.subTitle || '').trim();
        if (subText) {
            const fittedSub = this._fitWrappedLines(ctx, subText, maxTextW, {
                weight: 600,
                baseSize: 52,
                minSize: 40,
                maxLines: 2,
                lineHeight: 1.30,
            });
            ctx.font = `600 ${fittedSub.fontSize}px ${FONT_STACK}`;
            ctx.fillStyle = 'rgba(255,255,255,0.92)';
            fittedSub.lines.forEach((line) => {
                ctx.fillText(line, left, currentY);
                currentY += fittedSub.lineHeight;
            });
        }

        const footerY = h - 102;
        ctx.fillStyle = 'rgba(255,255,255,0.82)';
        ctx.fillRect(left, footerY - 44, 64, 2);
        ctx.font = `550 34px ${FONT_STACK}`;
        ctx.fillText(config.dateText || '研报播客', left, footerY - 30);
        ctx.restore();
    },

    _fitWrappedLines(ctx, text, maxWidth, options) {
        const weight = options.weight || 700;
        const maxLines = options.maxLines || 3;
        let fontSize = options.baseSize || 72;
        const minSize = options.minSize || 42;
        let lines = [];

        while (fontSize >= minSize) {
            ctx.font = `${weight} ${fontSize}px ${FONT_STACK}`;
            lines = this._wrapText(ctx, text, maxWidth).filter(line => line.trim());
            if (lines.length <= maxLines) break;
            fontSize -= 4;
        }

        ctx.font = `${weight} ${fontSize}px ${FONT_STACK}`;
        lines = this._wrapText(ctx, text, maxWidth).filter(line => line.trim());
        if (lines.length > maxLines) {
            lines = lines.slice(0, maxLines);
            lines[lines.length - 1] = this._truncateLineToWidth(ctx, lines[lines.length - 1], maxWidth);
        }

        return {
            fontSize,
            lines,
            lineHeight: fontSize * (options.lineHeight || 1.28),
        };
    },

    _truncateLineToWidth(ctx, text, maxWidth) {
        let value = String(text || '').trim();
        if (!value || ctx.measureText(value).width <= maxWidth) return value;
        while (value.length > 1 && ctx.measureText(`${value}...`).width > maxWidth) {
            value = value.slice(0, -1);
        }
        return `${value}...`;
    },

    _drawTitleLinesWithAccent(ctx, lines, x, y, fontSize, lineHeight) {
        ctx.font = `900 ${fontSize}px ${FONT_STACK}`;
        ctx.shadowColor = 'rgba(0,0,0,0.22)';
        ctx.shadowBlur = 8;
        ctx.shadowOffsetY = 2;
        lines.forEach((line, index) => {
            const text = String(line || '').trim();
            if (!text) return;

            const separatorIndex = text.search(/[:：]/);
            if (index === 0 && separatorIndex > 0 && separatorIndex < 8) {
                const prefix = text.slice(0, separatorIndex + 1);
                const rest = text.slice(separatorIndex + 1);
                ctx.fillStyle = '#E5C84B';
                ctx.fillText(prefix, x, y);
                ctx.fillStyle = '#F8FAFC';
                ctx.fillText(rest, x + ctx.measureText(prefix).width + 8, y);
            } else {
                ctx.fillStyle = '#F8FAFC';
                ctx.fillText(text, x, y);
            }
            y += lineHeight;
        });
        ctx.shadowColor = 'transparent';
        ctx.shadowBlur = 0;
        ctx.shadowOffsetY = 0;
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
            ctx.textAlign = 'center'; ctx.font = 'bold 30px ${FONT_STACK}';
            ctx.fillText('等待上传研报截图...', w / 2, splitY / 2);
        }

        // 4. 文字排版规则
        const leftPadding = 80;
        const maxTextW = w * 0.82 - leftPadding; // 安全区控制
        let currentY = splitY + 120;

        // 主标题绘制 (亮黄色 #FFD700)
        let mainFs = 95;
        if (config.mainTitle.length > 25) mainFs = 82;
        ctx.font = `900 ${mainFs}px ${FONT_STACK}`;
        ctx.fillStyle = '#FFD700';
        ctx.textAlign = 'left';
        ctx.textBaseline = 'top';

        const mainLines = this._wrapText(ctx, config.mainTitle, maxTextW).slice(0, 3);
        mainLines.forEach(line => {
            ctx.fillText(line, leftPadding, currentY);
            currentY += mainFs * 1.35;
        });

        // 副标题绘制 (纯白 #FFFFFF)
        currentY += 40;
        const subFs = 55;
        ctx.font = `500 ${subFs}px ${FONT_STACK}`;
        ctx.fillStyle = '#FFFFFF';
        const subLines = this._wrapText(ctx, config.subTitle, maxTextW).slice(0, 1);
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
        ctx.font = '500 38px ${FONT_STACK}';
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
        lines = this._normalizeCtaLines(lines);
        const barHeight = 140; 
        const barY = (position === 'bottom_bar') ? (h - barHeight) : 0;
        ctx.fillStyle = s.bg; ctx.fillRect(0, barY, w, barHeight);
        ctx.strokeStyle = s.border; ctx.lineWidth = 2; ctx.beginPath();
        if (position === 'bottom_bar') { ctx.moveTo(0, barY); ctx.lineTo(w, barY); }
        else { ctx.moveTo(0, barHeight); ctx.lineTo(w, barHeight); }
        ctx.stroke();

        const hMargin = 100; const centerY = barY + (barHeight / 2);
        ctx.textAlign = 'left'; ctx.textBaseline = 'middle';
        ctx.font = `500 42px ${FONT_STACK}`; ctx.fillStyle = s.text;
        ctx.fillText(lines[0], hMargin, centerY);

        ctx.textAlign = 'right'; ctx.font = `600 48px ${FONT_STACK}`; ctx.fillStyle = s.action;
        const actionTxt = lines[1] || lines[0];
        ctx.fillText(actionTxt, w - hMargin, centerY);
    },

    _renderFloatingCard(ctx, w, h, lines, position, s) {
        lines = this._normalizeCtaLines(lines);
        const scale = Math.min(w / 1920, h / 1080);
        const paddingH = 60 * scale;
        const paddingV = 54 * scale;
        const lineSpacing = 22 * scale;
        const maxTextW = Math.min(760 * scale, w * 0.42);
        const metrics = lines.map((line, i) => {
            const baseFs = i === 0 ? 42 * scale : 34 * scale;
            let fs = baseFs;
            const fw = '700';
            const color = i === 0 ? s.text : s.action;
            ctx.font = `${fw} ${fs}px ${FONT_STACK}`;
            while (fs > 24 * scale && ctx.measureText(line).width > maxTextW) {
                fs -= 2 * scale;
                ctx.font = `${fw} ${fs}px ${FONT_STACK}`;
            }
            ctx.font = `${fw} ${fs}px ${FONT_STACK}`;
            return { text: line, width: ctx.measureText(line).width, height: fs, fw, color };
        });

        const maxW = Math.max(...metrics.map(m => m.width), 520 * scale);
        const totalTextH = metrics.reduce((sum, m) => sum + m.height, 0) + (metrics.length - 1) * lineSpacing;
        const boxW = maxW + paddingH * 2;
        const boxH = totalTextH + paddingV * 2;
        const safeR = 120 * scale, safeB = 220 * scale;
        
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
            ctx.font = `${m.fw} ${m.height}px ${FONT_STACK}`; ctx.fillStyle = m.color;
            ctx.fillText(m.text, bx + boxW / 2, currY); currY += m.height + lineSpacing;
        });
    },

    _normalizeCtaLines(lines) {
        const clean = lines.map(line => String(line || '').trim()).filter(Boolean);
        if (clean.length <= 3) return clean;
        return [clean[0], clean[1], clean.slice(2).join('')];
    },

    /**
     * 工具：Halo 文字
     */
    drawHaloText(ctx, text, x, y, color, strokeColor, fontSize, fontWeight = "900") {
        ctx.save(); ctx.font = `${fontWeight} ${fontSize}px ${FONT_STACK}`; ctx.textBaseline = 'top';
        ctx.strokeStyle = strokeColor; ctx.lineWidth = fontSize * 0.04; ctx.lineJoin = 'round'; ctx.strokeText(text, x, y);
        ctx.fillStyle = color; ctx.fillText(text, x, y); ctx.restore();
    },

    /**
     * 工具：图片 Cover 填充 (支持平移和缩放)
     */
    drawImageCover(ctx, img, x, y, w, h, offsetX = 0, offsetY = 0, scale = 1, options = {}) {
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
        const anchorY = Number.isFinite(Number(options.anchorY)) ? Number(options.anchorY) : 0.5;
        const baseY = y - Math.max(0, sh - h) * Math.min(1, Math.max(0, anchorY));
        const finalX = rx + (rw - sw) / 2 + offsetX;
        const finalY = (options.anchorY === undefined ? ry + (rh - sh) / 2 : baseY) + offsetY;
        
        ctx.imageSmoothingEnabled = true;
        ctx.imageSmoothingQuality = options.quality || 'medium';
        ctx.drawImage(img, finalX, finalY, sw, sh);
    }
};

window.CanvasRenderer = CanvasRenderer;
