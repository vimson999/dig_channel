import re

# 1. Restore renderer.js
with open('src/frontend/js/publisher/renderer.js', 'r', encoding='utf-8') as f:
    renderer_content = f.read()

renderer_content = re.sub(
    r'    drawCover\(ctx, canvas, images, config\).*?    },(?=\n\n    /\*\*\n     \* 核心：绘制 9:16)',
    r'''    drawCover(ctx, canvas, images, config) {
        if (!ctx) return;
        const w = canvas.width, h = canvas.height;
        const mode = config.mode || 'preset';
        const isScreenshot = (mode === 'screenshot');
        const isPodcast = (mode === 'podcast');
        const isScreenshotStyle = isScreenshot || isPodcast;

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
                ctx.fillStyle = '#002140'; ctx.fillRect(0, 0, w, h * 0.45);
                ctx.fillStyle = 'rgba(255, 255, 255, 0.2)'; ctx.textAlign = 'center'; ctx.font = `bold 40px ${FONT_STACK}`;
                ctx.fillText('等待上传研报截图...', w/2, h * 0.22);
                ctx.textAlign = 'left';
            }
            ctx.fillStyle = '#001529'; ctx.fillRect(0, h * 0.45, w, h * 0.55);
        } else if (isPodcast) {
            if (images.custom.src && images.custom.complete && images.custom.naturalWidth > 0) {
                const trans = config.transform || { x: 0, y: 0, scale: 1 };
                this.drawImageCover(ctx, images.custom, 0, 0, w, h, trans.x, trans.y, trans.scale);
                // 播客模式全屏遮罩：左侧深色渐变
                const maskGradient = ctx.createLinearGradient(0, 0, w * 0.7, 0);
                maskGradient.addColorStop(0, 'rgba(0, 0, 0, 0.85)');
                maskGradient.addColorStop(0.5, 'rgba(0, 0, 0, 0.5)');
                maskGradient.addColorStop(1, 'rgba(0, 0, 0, 0)');
                ctx.fillStyle = maskGradient; ctx.fillRect(0, 0, w, h);
            } else {
                ctx.fillStyle = '#1a1a1a'; ctx.fillRect(0, 0, w, h);
            }
        } else if (images.bg.complete && images.bg.naturalWidth > 0) {
            this.drawImageCover(ctx, images.bg, 0, 0, w, h);
            const opacity = config.overlayOpacity || 0.5;
            const maskGradient = ctx.createLinearGradient(0, 0, w * 0.6, 0);
            maskGradient.addColorStop(0, `rgba(0, 15, 30, ${opacity})`); maskGradient.addColorStop(1, `rgba(0, 15, 30, 0)`);
            ctx.fillStyle = maskGradient; ctx.fillRect(0, 0, w, h);
        } else { ctx.fillStyle = this.themes.bg; ctx.fillRect(0, 0, w, h); }

        const leftMargin = 150;
        let currentY = isScreenshot ? 520 : (isPodcast ? 350 : 180);
        
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
        const mainColor = isScreenshotStyle ? '#FFD700' : this.themes.main;
        
        // --- 标题逻辑优化：支持竖线 | 分割，或 \n 分割 ---
        let titleLines = [];
        if (config.mainTitle.includes('|')) {
            titleLines = config.mainTitle.split('|').map(s => s.trim());
        } else {
            titleLines = config.mainTitle.split('\n').map(s => s.trim());
        }
        
        titleLines.forEach((l, idx) => {
            if (!l) return;
            // 如果是使用 | 分割的多行，第二行可以稍微小一点
            let lineSize = mainFontSize;
            if (isPodcast && titleLines.length > 1 && idx > 0) {
                lineSize = mainFontSize * 0.8;
                ctx.font = `${lineSize}px ${FONT_STACK}`;
            } else {
                ctx.font = `${mainFontSize}px ${FONT_STACK}`;
            }
            
            if (isScreenshotStyle) { 
                ctx.fillStyle = mainColor; ctx.textBaseline = 'top'; ctx.fillText(l, leftMargin, currentY); 
            } else { 
                this.drawHaloText(ctx, l, leftMargin, currentY, mainColor, this.themes.stroke, lineSize, "normal"); 
            }
            currentY += lineSize * 1.3; 
        });

        if (config.subTitle) {
            const subColor = isScreenshotStyle ? '#FFFFFF' : this.themes.sub;
            if (isScreenshotStyle) { ctx.font = `56px ${FONT_STACK}`; ctx.fillStyle = subColor; ctx.fillText(config.subTitle, leftMargin, currentY + 15); }
            else { this.drawHaloText(ctx, config.subTitle, leftMargin, currentY + 15, subColor, this.themes.stroke, 56, "normal"); }
        }

        // 4. 页脚日期
        const footerY = h - 100;
        const footerColor = isScreenshotStyle ? 'rgba(200, 200, 200, 0.8)' : this.themes.date;
        ctx.save(); ctx.beginPath(); ctx.moveTo(leftMargin, footerY - 47); ctx.lineTo(leftMargin + 60, footerY - 47); ctx.strokeStyle = footerColor; ctx.lineWidth = 2; ctx.stroke();
        ctx.font = `600 32px ${FONT_STACK}`; ctx.fillStyle = footerColor; ctx.fillText(config.dateText, leftMargin, footerY - 32); ctx.restore();
    }''',
    renderer_content,
    flags=re.DOTALL
)

renderer_content = re.sub(
    r'    drawVerticalCover\(ctx, canvas, images, config\).*?    },(?=\n\n    /\*\*\n     \* 绘制 Apex)',
    r'''    drawVerticalCover(ctx, canvas, images, config) {
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
            ctx.textAlign = 'center'; ctx.font = `bold 30px ${FONT_STACK}`;
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

        const mainLines = this._wrapText(ctx, config.mainTitle, maxTextW).slice(0, 4);
        mainLines.forEach(line => {
            ctx.fillText(line, leftPadding, currentY);
            currentY += mainFs * 1.35;
        });

        // 副标题绘制 (纯白 #FFFFFF)
        currentY += 40;
        const subFs = 55;
        ctx.font = `500 ${subFs}px ${FONT_STACK}`;
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
        ctx.font = `500 38px ${FONT_STACK}`;
        ctx.fillText(config.dateText, leftPadding, brandY - 30);
    }''',
    renderer_content,
    flags=re.DOTALL
)

# Remove drawApexBadge
renderer_content = re.sub(r'    /\*\*\n     \* 绘制 Apex.*?\n    },(?=\n\n    /\*\*\n     \* 工具)', '', renderer_content, flags=re.DOTALL)

with open('src/frontend/js/publisher/renderer.js', 'w', encoding='utf-8') as f:
    f.write(renderer_content)


# 2. Restore thumbnail_service.py
with open('src/backend/services/thumbnail_service.py', 'r', encoding='utf-8') as f:
    py_content = f.read()

# Remove _draw_apex_badge
py_content = re.sub(r'    def _draw_apex_badge\(self, draw, x, y, radius\):.*?    def generate_thumbnail', '    def generate_thumbnail', py_content, flags=re.DOTALL)

py_content = re.sub(
    r'    def generate_thumbnail\(self, main_title: str.*?return output_path\n        except Exception as e:\n            logger\.error\(f"Thumbnail generation failed: \{e\}", exc_info=True\)\n            raise e',
    r'''    def generate_thumbnail(self, main_title: str, sub_title: str = "", date_text: str = "", tag_text: str = "",
                          bg_preset_index: int = 0, layout: str = 'left', output_filename: str = None,
                          custom_bg_path: str = None, overlay_opacity: float = 0.5, transform: dict = None,
                          mode: str = 'preset') -> str:
        try:
            if bg_preset_index < 0 or bg_preset_index >= len(BACKGROUNDS): bg_preset_index = 0
            bg_config = BACKGROUNDS[bg_preset_index]
            theme = PRESETS.get(bg_config['config']['scheme'], PRESETS['morgan'])
            
            is_screenshot = (mode == 'screenshot')
            is_podcast = (mode == 'podcast')
            is_custom_bg = bool(custom_bg_path and os.path.exists(custom_bg_path))
            
            # 判断是否应用特定视觉标识 (VI)
            is_screenshot_style = is_screenshot or is_podcast

            if is_screenshot_style and is_custom_bg:
                # 实现与前端一致的 drawImageCover 逻辑 (带变换)
                img = Image.new('RGBA', (1920, 1080), (0, 21, 41, 255)) 
                screenshot = Image.open(custom_bg_path).convert('RGBA')
                
                target_w, target_h = 1920, (486 if is_screenshot else 1080)
                sw_orig, sh_orig = screenshot.size
                r = sw_orig / sh_orig
                cr = target_w / target_h
                
                if r > cr:
                    rh = target_h
                    rw = int(target_h * r)
                else:
                    rw = target_w
                    rh = int(target_w / r)
                
                # 应用变换
                trans = transform or {'x': 0, 'y': 0, 'scale': 1.0}
                scale = float(trans.get('scale', 1.0))
                offset_x = float(trans.get('x', 0))
                offset_y = float(trans.get('y', 0))
                
                sw = int(rw * scale)
                sh = int(rh * scale)
                
                rx = (target_w - rw) // 2
                ry = 0 # 顶部对齐
                
                final_x = rx + (rw - sw) // 2 + int(offset_x)
                final_y = ry + (rh - sh) // 2 + int(offset_y)
                
                screenshot_resized = screenshot.resize((sw, sh), Image.Resampling.LANCZOS)
                
                if is_screenshot:
                    # 截图模式：裁剪到顶部 45% 区域，底部为深蓝色
                    paste_layer = Image.new('RGBA', (1920, 1080), (0, 0, 0, 0))
                    paste_layer.paste(screenshot_resized, (final_x, final_y))
                    mask = Image.new('L', (1920, 1080), 0)
                    mask_draw = ImageDraw.Draw(mask)
                    mask_draw.rectangle([0, 0, 1920, 486], fill=255)
                    img.paste(paste_layer, (0, 0), mask)
                    
                    # 底部背景深蓝色
                    draw_bg = ImageDraw.Draw(img)
                    draw_bg.rectangle([0, 486, 1920, 1080], fill=(0, 21, 41, 255))
                else:
                    # 播客模式：全屏图片 + 遮罩
                    img.paste(screenshot_resized, (final_x, final_y))
                    # 播客全屏遮罩：左侧深色渐变
                    mask_layer = Image.new('L', (1920, 1080), 0)
                    mask_draw = ImageDraw.Draw(mask_layer)
                    grad_w = int(1920 * 0.7)
                    for x in range(grad_w):
                        ratio = x / grad_w
                        if ratio < 0.5:
                            alpha_ratio = 0.85 - (ratio / 0.5) * 0.35
                        else:
                            alpha_ratio = 0.5 - ((ratio - 0.5) / 0.5) * 0.5
                        alpha = int(255 * max(0, alpha_ratio))
                        mask_draw.line((x, 0, x, 1080), fill=alpha)
                    img.paste(Image.new('RGBA', (1920, 1080), (0, 0, 0, 255)), (0, 0), mask_layer)
            else:
                # 预设模式或未上传截图
                bg_path_default = os.path.join(self.asset_dir, 'default', bg_config['file'])
                bg_path_root = os.path.join(self.asset_dir, bg_config['file'])
                bg_path = bg_path_default if os.path.exists(bg_path_default) else (bg_path_root if os.path.exists(bg_path_root) else None)
                
                if bg_path:
                    img = Image.open(bg_path).convert('RGBA')
                else:
                    img = Image.new('RGB', (1920, 1080), theme['bg']).convert('RGBA')
                
                img = ImageOps.fit(img, (1920, 1080), method=Image.Resampling.LANCZOS)
                blur_amount = bg_config['config'].get('blur', 0)
                if blur_amount > 0: img = img.filter(ImageFilter.GaussianBlur(radius=blur_amount))
                
                mask_alpha = bg_config['config'].get('mask', 40) / 100.0
                if mask_alpha > 0:
                    mask_layer = Image.new('L', (1920, 1080), 0)
                    mask_draw = ImageDraw.Draw(mask_layer)
                    base_color = (0, 0, 0) if theme['isDark'] else (0, 15, 30)
                    grad_w = int(1920 * 0.7)
                    for x in range(grad_w):
                        alpha = int(255 * mask_alpha * (1 - x / grad_w))
                        line_x = x if layout == 'left' else (1919 - x)
                        mask_draw.line((line_x, 0, line_x, 1080), fill=alpha)
                    img.paste(Image.new('RGBA', (1920, 1080), base_color + (255,)), (0, 0), mask_layer)

            draw = ImageDraw.Draw(img)
            base_x = 150
            # 垂直重心位置：截图模式 520, 播客模式 350, 预设模式 180
            current_y = 520 if is_screenshot else (350 if is_podcast else 180)
            
            try:
                main_size = 110
                font_main = ImageFont.truetype(self.font_path, main_size)
                font_sub = ImageFont.truetype(self.font_path, 56)
                font_date = ImageFont.truetype(self.font_path, 32)
                font_tag = ImageFont.truetype(self.font_path, 30)
            except: font_main = font_sub = font_date = font_tag = ImageFont.load_default()

            if tag_text and not is_screenshot_style:
                l, t, r, b = draw.textbbox((0, 0), tag_text, font=font_tag)
                draw.rounded_rectangle((base_x-24, current_y-12, base_x+(r-l)+24, current_y+(b-t)+12), radius=4, fill='white')
                draw.text((base_x, current_y), tag_text, font=font_tag, fill='#333333')
                current_y += 130

            if main_title:
                main_color = '#FFD700' if is_screenshot_style else theme['main']
                stroke_color = None if is_screenshot_style else self._parse_rgba(theme['stroke'])
                
                # --- 标题逻辑优化：支持竖线 | 分割，或 \n 分割 ---
                title_lines = []
                if '|' in main_title:
                    title_lines = [s.strip() for s in main_title.split('|')]
                else:
                    title_lines = [s.strip() for s in main_title.split('\n')]
                    
                for idx, line in enumerate(title_lines):
                    if not line: continue
                    
                    line_size = main_size
                    if is_podcast and len(title_lines) > 1 and idx > 0:
                        line_size = int(main_size * 0.8)
                        try:
                            current_font = ImageFont.truetype(self.font_path, line_size)
                        except:
                            current_font = font_main
                    else:
                        current_font = font_main
                        
                    self._draw_halo_text(draw, line, (base_x, current_y), current_font, main_color, stroke_color, line_size * 0.04)
                    current_y += int(line_size * 1.3)
                current_y += 15

            if sub_title:
                sub_color = '#FFFFFF' if is_screenshot_style else theme['sub']
                self._draw_halo_text(draw, sub_title, (base_x, current_y), font_sub, sub_color, None if is_screenshot_style else self._parse_rgba(theme['stroke']), 56 * 0.04)

            # 落款回归左下角
            if date_text:
                date_y = 1080 - 100
                f_color = (200, 200, 200, 200) if is_screenshot_style else theme['date']
                draw.line((base_x, date_y - 47, base_x + 60, date_y - 47), fill=f_color, width=2)
                self._draw_halo_text(draw, date_text, (base_x, date_y-32), font_date, f_color, None, 0)

            if not output_filename:
                import uuid
                output_filename = f"thumbnail_{uuid.uuid4().hex[:8]}.jpg"
            if not output_filename.lower().endswith('.jpg'): output_filename += '.jpg'
            output_path = os.path.join(self.output_dir, output_filename)
            img.convert('RGB').save(output_path, quality=95)
            logger.info(f"Thumbnail generated: {output_path}")
            return output_path
        except Exception as e:
            logger.error(f"Thumbnail generation failed: {e}", exc_info=True)
            raise e''',
    py_content,
    flags=re.DOTALL
)

with open('src/backend/services/thumbnail_service.py', 'w', encoding='utf-8') as f:
    f.write(py_content)

