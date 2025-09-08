"""
学生证生成器API
处理日本学生证的生成请求
"""

import os
import io
import base64
from datetime import datetime
from flask import Blueprint, request, jsonify, send_file
from PIL import Image, ImageDraw, ImageFont
import logging

# 创建蓝图
student_id_bp = Blueprint('student_id', __name__, url_prefix='/api')

logger = logging.getLogger(__name__)

class StudentIdGenerator:
    """日本学生证生成器"""
    
    def __init__(self):
        # 学生证尺寸 (像素) - 基于标准信用卡尺寸 54mm x 86mm，300DPI
        self.width = 340  # 像素
        self.height = 215  # 像素
        
        # 颜色定义
        self.colors = {
            'navy': (0, 51, 102),      # 海军蓝
            'red': (220, 20, 60),      # 深红色  
            'gold': (255, 215, 0),     # 金色
            'cream': (250, 247, 240),  # 奶白色
            'white': (255, 255, 255),  # 白色
            'black': (0, 0, 0),        # 黑色
            'gray': (128, 128, 128),   # 灰色
            'light_gray': (211, 211, 211),  # 浅灰色
        }
        
        # 尝试加载字体
        self.fonts = self.load_fonts()

    def load_fonts(self):
        """加载字体文件"""
        fonts = {}
        
        # 默认字体大小
        font_sizes = {
            'large': 16,
            'medium': 12,
            'small': 10,
            'tiny': 8
        }
        
        # 尝试加载系统字体
        font_paths = [
            # macOS 系统字体
            '/System/Library/Fonts/Arial.ttf',
            '/System/Library/Fonts/Helvetica.ttc',
            # Windows 系统字体
            'C:/Windows/Fonts/arial.ttf',
            'C:/Windows/Fonts/calibri.ttf',
            # Linux 字体
            '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',
            '/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf'
        ]
        
        loaded_font = None
        for font_path in font_paths:
            if os.path.exists(font_path):
                try:
                    loaded_font = font_path
                    break
                except Exception as e:
                    logger.warning(f"无法加载字体 {font_path}: {e}")
                    continue
        
        # 为不同大小创建字体对象
        for size_name, size in font_sizes.items():
            try:
                if loaded_font:
                    fonts[size_name] = ImageFont.truetype(loaded_font, size)
                else:
                    fonts[size_name] = ImageFont.load_default()
                    logger.warning(f"使用默认字体代替 {size_name}")
            except Exception as e:
                fonts[size_name] = ImageFont.load_default()
                logger.warning(f"字体加载失败，使用默认字体: {e}")
        
        return fonts

    def create_student_id_image(self, student_data, photo_data=None):
        """
        生成学生证图片
        
        Args:
            student_data: 学生信息字典
            photo_data: 学生照片的base64数据或PIL Image对象
            
        Returns:
            PIL Image对象
        """
        # 创建画布
        img = Image.new('RGB', (self.width, self.height), self.colors['cream'])
        draw = ImageDraw.Draw(img)
        
        # 绘制边框
        self.draw_border(draw)
        
        # 绘制顶部标题栏（包含学校名称）
        self.draw_header(draw, student_data.get('schoolName', '学校名'))
        
        # 处理并绘制照片
        photo_img = self.process_photo(photo_data)
        if photo_img:
            self.draw_photo(img, photo_img)
        else:
            self.draw_photo_placeholder(draw)
        
        # 绘制学生信息
        self.draw_student_info(draw, student_data)
        
        # 绘制有效期框
        self.draw_validity_period(draw, student_data)
        
        # 绘制装饰元素
        self.draw_decorations(draw)
        
        return img

    def draw_border(self, draw):
        """绘制边框"""
        # 主边框
        draw.rectangle([0, 0, self.width-1, self.height-1], 
                      outline=self.colors['navy'], width=2)
        
        # 内边框
        draw.rectangle([3, 3, self.width-4, self.height-4], 
                      outline=self.colors['navy'], width=1)

    def draw_header(self, draw, school_name="学校名"):
        """绘制顶部标题栏"""
        # 标题栏背景
        header_height = 32
        draw.rectangle([5, 5, self.width-6, header_height], 
                      fill=self.colors['navy'])
        
        # 左侧：学生証标识
        title_text = "学生証"
        draw.text((12, 12), title_text, 
                 fill=self.colors['white'], font=self.fonts['small'])
        
        # 右侧：学校名称（截断以适应顶栏宽度）
        max_school_name_length = 18  # 根据顶栏宽度调整
        truncated_school_name = self.truncate_text(school_name, max_school_name_length)
        
        # 计算学校名称位置（右对齐）
        school_bbox = draw.textbbox((0, 0), truncated_school_name, font=self.fonts['small'])
        school_width = school_bbox[2] - school_bbox[0]
        school_x = self.width - school_width - 12
        
        draw.text((school_x, 12), truncated_school_name, 
                 fill=self.colors['white'], font=self.fonts['small'])


    def process_photo(self, photo_data):
        """处理照片数据"""
        if not photo_data:
            return None
            
        try:
            if isinstance(photo_data, str):
                # base64数据
                if photo_data.startswith('data:image'):
                    photo_data = photo_data.split(',')[1]
                
                photo_bytes = base64.b64decode(photo_data)
                photo_img = Image.open(io.BytesIO(photo_bytes))
            else:
                # PIL Image对象或文件对象
                photo_img = Image.open(photo_data) if not isinstance(photo_data, Image.Image) else photo_data
            
            # 转换为RGB模式
            if photo_img.mode != 'RGB':
                photo_img = photo_img.convert('RGB')
            
            # 调整大小 - 学生证照片区域
            photo_width, photo_height = 90, 120
            photo_img = photo_img.resize((photo_width, photo_height), Image.Resampling.LANCZOS)
            
            return photo_img
            
        except Exception as e:
            logger.error(f"照片处理失败: {e}")
            return None

    def draw_photo(self, img, photo_img):
        """绘制学生照片"""
        photo_x, photo_y = 15, 65
        img.paste(photo_img, (photo_x, photo_y))
        
        # 照片边框
        draw = ImageDraw.Draw(img)
        draw.rectangle([photo_x-1, photo_y-1, photo_x+90, photo_y+120], 
                      outline=self.colors['gray'], width=1)

    def draw_photo_placeholder(self, draw):
        """绘制照片占位符"""
        photo_x, photo_y = 15, 65
        photo_width, photo_height = 90, 120
        
        # 占位符背景
        draw.rectangle([photo_x, photo_y, photo_x+photo_width, photo_y+photo_height], 
                      fill=self.colors['light_gray'], outline=self.colors['gray'])
        
        # 占位符文字
        placeholder_text = "写真"
        text_bbox = draw.textbbox((0, 0), placeholder_text, font=self.fonts['small'])
        text_width = text_bbox[2] - text_bbox[0]
        text_height = text_bbox[3] - text_bbox[1]
        
        text_x = photo_x + (photo_width - text_width) // 2
        text_y = photo_y + (photo_height - text_height) // 2
        
        draw.text((text_x, text_y), placeholder_text, 
                 fill=self.colors['gray'], font=self.fonts['small'])

    def draw_student_info(self, draw, student_data):
        """绘制学生信息"""
        info_x = 120  # 信息区域起始x坐标
        info_y = 50   # 信息区域起始y坐标，由于顶栏移除了学校名称，可以调高
        line_height = 14  # 行高
        
        # 主要信息字段
        info_fields = [
            ('学籍:', student_data.get('studentId', '-')),
            ('かな:', self.truncate_text(student_data.get('studentNameKana', '-'), 12)),
            ('氏名:', self.truncate_text(student_data.get('studentName', '-'), 10)),
            ('学年:', student_data.get('grade', '-')),
        ]
        
        current_y = info_y
        
        for label, value in info_fields:
            # 绘制标签
            draw.text((info_x, current_y), label, 
                     fill=self.colors['gray'], font=self.fonts['tiny'])
            
            # 绘制值
            label_bbox = draw.textbbox((0, 0), label, font=self.fonts['tiny'])
            label_width = label_bbox[2] - label_bbox[0]
            
            draw.text((info_x + label_width + 3, current_y), value, 
                     fill=self.colors['black'], font=self.fonts['tiny'])
            
            current_y += line_height
        
        # 学部信息（如果有）
        department = student_data.get('department', '')
        if department:
            department = self.truncate_text(department, 8)
            draw.text((info_x, current_y), f"学部: {department}", 
                     fill=self.colors['gray'], font=self.fonts['tiny'])
            current_y += line_height

    def draw_validity_period(self, draw, student_data):
        """绘制通栏样式的有效期 - 完全模仿顶部通栏"""
        # 底部通栏完全按照顶部通栏的方式和参数
        # 顶部: [5, 5, width-6, 32]
        # 底部: [5, height-37, width-6, height-5] 让它们完全对称
        
        validity_start_y = self.height - 37  # 32px高度 + 5px边距 = 37
        validity_end_y = self.height - 5     # 距离底边5px，与顶部对称
        
        # 与顶部通栏使用完全相同的绘制方式
        draw.rectangle([5, validity_start_y, self.width-6, validity_end_y], 
                      fill=self.colors['navy'])
        
        # 有效期日期
        valid_from = student_data.get('validFrom', '-')
        valid_to = student_data.get('validTo', '-')
        
        # 格式化日期显示
        if valid_from != '-' and '.' in valid_from:
            valid_from = valid_from[2:]  # 去掉年份前两位 (24.04.01)
        if valid_to != '-' and '.' in valid_to:
            valid_to = valid_to[2:]  # 去掉年份前两位 (25.03.31)
        
        # 左侧显示"有効期間"标题 - 与顶部文字位置完全对称
        title_x = 12  # 与顶部 (12, 12) 的x坐标一致
        title_y = validity_start_y + 7  # 相对通栏顶部的偏移与顶部一致
        draw.text((title_x, title_y), "有効期間", 
                 fill=self.colors['white'], font=self.fonts['small'])
        
        # 右侧显示日期范围 - 与顶部学校名称位置对称
        validity_text = f"{valid_from} ～ {valid_to}"
        
        # 计算日期文字右对齐位置，与顶部学校名称完全对称
        text_bbox = draw.textbbox((0, 0), validity_text, font=self.fonts['small'])
        text_width = text_bbox[2] - text_bbox[0]
        text_x = self.width - text_width - 12  # 与顶部右对齐方式一致
        text_y = validity_start_y + 7  # 与左侧标题同一行
        
        draw.text((text_x, text_y), validity_text, 
                 fill=self.colors['white'], font=self.fonts['small'])

    def truncate_text(self, text, max_length):
        """截断文本以适应布局"""
        if len(text) <= max_length:
            return text
        return text[:max_length-1] + '…'

    def draw_decorations(self, draw):
        """绘制装饰元素"""
        # 由于有效期通栏占据了底部，移除底部装饰
        # 可以在其他位置添加小装饰元素
        
        # 右上角小金色圆点（不与顶栏冲突的位置）
        circle_x, circle_y = self.width - 15, 45
        circle_radius = 3
        
        draw.ellipse([circle_x - circle_radius, circle_y - circle_radius, 
                     circle_x + circle_radius, circle_y + circle_radius], 
                    fill=self.colors['gold'], outline=self.colors['gold'])

    def create_student_id_back_image(self, student_data):
        """
        生成学生证背面图片
        
        Args:
            student_data: 学生信息字典
            
        Returns:
            PIL Image对象
        """
        # 创建画布
        img = Image.new('RGB', (self.width, self.height), self.colors['cream'])
        draw = ImageDraw.Draw(img)
        
        # 绘制边框
        self.draw_border(draw)
        
        # 绘制顶部标题栏
        self.draw_back_header(draw)
        
        # 绘制学校信息区
        self.draw_school_info(draw, student_data)
        
        # 绘制注意事项
        self.draw_guidelines(draw)
        
        # 绘制底部信息栏
        self.draw_back_footer(draw, student_data)
        
        # 绘制装饰元素
        self.draw_back_decorations(draw)
        
        return img

    def draw_back_header(self, draw):
        """绘制背面顶部标题栏"""
        # 标题栏背景
        header_height = 32
        draw.rectangle([5, 5, self.width-6, header_height], 
                      fill=self.colors['navy'])
        
        # 中央标题
        title_text = "学生証（裏面）"
        title_bbox = draw.textbbox((0, 0), title_text, font=self.fonts['small'])
        title_width = title_bbox[2] - title_bbox[0]
        title_x = (self.width - title_width) // 2
        
        draw.text((title_x, 12), title_text, 
                 fill=self.colors['white'], font=self.fonts['small'])

    def draw_school_info(self, draw, student_data):
        """绘制学校信息区域"""
        school_name = student_data.get('schoolName', '学校名')
        
        # 学校信息框
        info_box_y = 50
        info_box_height = 60
        
        # 背景框
        draw.rectangle([15, info_box_y, self.width-16, info_box_y + info_box_height], 
                      fill=self.colors['white'], outline=self.colors['navy'], width=1)
        
        # 学校名称
        school_text = f"発行: {school_name}"
        draw.text((20, info_box_y + 8), school_text, 
                 fill=self.colors['navy'], font=self.fonts['small'])
        
        # 学生部门
        dept_text = "学生部 学籍課"
        draw.text((20, info_box_y + 25), dept_text, 
                 fill=self.colors['gray'], font=self.fonts['tiny'])
        
        # 联系信息
        contact_text = "お問い合わせ: 学生課窓口"
        draw.text((20, info_box_y + 40), contact_text, 
                 fill=self.colors['gray'], font=self.fonts['tiny'])

    def draw_guidelines(self, draw):
        """绘制注意事项"""
        guidelines_y = 125
        
        # 注意事项标题
        guidelines_title = "注意事項"
        draw.text((15, guidelines_y), guidelines_title, 
                 fill=self.colors['navy'], font=self.fonts['small'])
        
        # 注意事项内容
        guidelines = [
            "• この学生証は常時携帯してください",
            "• 紛失した場合は直ちに届け出てください", 
            "• 他人への貸与は禁止されています",
            "• 有効期限を確認してください",
            "• 破損した場合は再発行が必要です"
        ]
        
        current_y = guidelines_y + 18
        for guideline in guidelines:
            # 截断过长的文本以适应卡片宽度
            truncated_guideline = self.truncate_text(guideline, 32)
            draw.text((15, current_y), truncated_guideline, 
                     fill=self.colors['black'], font=self.fonts['tiny'])
            current_y += 12

    def draw_back_footer(self, draw, student_data):
        """绘制底部信息栏"""
        # 底部通栏
        footer_box_x = 5
        footer_box_y = self.height - 25
        footer_box_width = self.width - 10
        footer_box_height = 18
        
        # 绘制底部通栏背景
        draw.rectangle([footer_box_x, footer_box_y, 
                       footer_box_x + footer_box_width, footer_box_y + footer_box_height], 
                      fill=self.colors['navy'])
        
        # 发行日期
        current_date = datetime.now().strftime('%Y.%m.%d')
        issue_text = f"発行日: {current_date}"
        
        draw.text((footer_box_x + 12, footer_box_y + 4), issue_text, 
                 fill=self.colors['white'], font=self.fonts['tiny'])
        
        # 右侧版本信息
        version_text = "Ver.2024"
        version_bbox = draw.textbbox((0, 0), version_text, font=self.fonts['tiny'])
        version_width = version_bbox[2] - version_bbox[0]
        version_x = footer_box_x + footer_box_width - version_width - 12
        
        draw.text((version_x, footer_box_y + 4), version_text, 
                 fill=self.colors['white'], font=self.fonts['tiny'])

    def draw_back_decorations(self, draw):
        """绘制背面装饰元素"""
        # 左上角小装饰
        circle_x, circle_y = 25, 45
        circle_radius = 2
        
        draw.ellipse([circle_x - circle_radius, circle_y - circle_radius, 
                     circle_x + circle_radius, circle_y + circle_radius], 
                    fill=self.colors['gold'])
        
        # 右下角小装饰线
        line_start_x, line_start_y = self.width - 30, self.height - 45
        line_end_x, line_end_y = self.width - 15, self.height - 45
        
        draw.line([line_start_x, line_start_y, line_end_x, line_end_y], 
                  fill=self.colors['gold'], width=1)

@student_id_bp.route('/generate-student-id', methods=['POST'])
def generate_student_id():
    """生成学生证API端点"""
    try:
        # 获取表单数据
        form_data = request.form.to_dict()
        
        # 验证必需字段
        required_fields = ['schoolName', 'studentName', 'studentNameKana', 'validFrom', 'validTo']
        for field in required_fields:
            if not form_data.get(field):
                return jsonify({'error': f'缺少必需字段: {field}'}), 400
        
        # 获取照片文件
        photo_file = request.files.get('photo')
        photo_data = None
        
        if photo_file:
            # 验证文件类型
            if not photo_file.content_type.startswith('image/'):
                return jsonify({'error': '照片文件格式不正确'}), 400
            
            photo_data = photo_file
        
        # 创建生成器实例
        generator = StudentIdGenerator()
        
        # 生成学生证图片
        student_id_image = generator.create_student_id_image(form_data, photo_data)
        
        # 将图片保存到内存
        img_buffer = io.BytesIO()
        student_id_image.save(img_buffer, format='PNG', quality=95)
        img_buffer.seek(0)
        
        # 生成文件名
        student_name = form_data.get('studentName', 'student')
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        filename = f'student_id_{student_name}_{timestamp}.png'
        
        logger.info(f"成功生成学生证: {filename}")
        
        return send_file(
            img_buffer,
            mimetype='image/png',
            as_attachment=True,
            download_name=filename
        )
        
    except Exception as e:
        logger.error(f"生成学生证失败: {str(e)}")
        return jsonify({'error': '生成失败，请稍后重试'}), 500

@student_id_bp.route('/generate-student-id-back', methods=['POST'])
def generate_student_id_back():
    """生成学生证背面API端点"""
    try:
        # 获取表单数据
        form_data = request.form.to_dict()
        
        # 验证必需字段
        required_fields = ['schoolName']
        for field in required_fields:
            if not form_data.get(field):
                return jsonify({'error': f'缺少必需字段: {field}'}), 400
        
        # 创建生成器实例
        generator = StudentIdGenerator()
        
        # 生成学生证背面图片
        student_id_back_image = generator.create_student_id_back_image(form_data)
        
        # 将图片保存到内存
        img_buffer = io.BytesIO()
        student_id_back_image.save(img_buffer, format='PNG', quality=95)
        img_buffer.seek(0)
        
        # 生成文件名
        student_name = form_data.get('studentName', 'student')
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        filename = f'student_id_back_{student_name}_{timestamp}.png'
        
        logger.info(f"成功生成学生证背面: {filename}")
        
        return send_file(
            img_buffer,
            mimetype='image/png',
            as_attachment=True,
            download_name=filename
        )
        
    except Exception as e:
        logger.error(f"生成学生证背面失败: {str(e)}")
        return jsonify({'error': '生成背面失败，请稍后重试'}), 500

@student_id_bp.route('/student-id-preview', methods=['POST'])
def preview_student_id():
    """生成学生证预览（用于前端实时预览）"""
    try:
        data = request.get_json()
        
        # 创建简单的预览数据
        preview_data = {
            'schoolName': data.get('schoolName', '学校名を入力してください'),
            'studentName': data.get('studentName', '-'),
            'studentNameKana': data.get('studentNameKana', '-'),
            'studentId': data.get('studentId', '-'),
            'grade': data.get('grade', '-'),
            'department': data.get('department', '-'),
            'validFrom': data.get('validFrom', '-'),
            'validTo': data.get('validTo', '-')
        }
        
        return jsonify({
            'status': 'success',
            'preview': preview_data
        })
        
    except Exception as e:
        logger.error(f"预览生成失败: {str(e)}")
        return jsonify({'error': '预览生成失败'}), 500