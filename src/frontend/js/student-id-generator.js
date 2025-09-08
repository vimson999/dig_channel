/**
 * 日本学生证生成器 JavaScript
 * 处理表单交互、图片上传、实时预览和学生证生成
 */

class StudentIdGenerator {
    constructor() {
        this.uploadedPhoto = null;
        this.studentData = {};
        this.init();
    }

    init() {
        this.setupEventListeners();
        this.setupPhotoUpload();
        this.setDefaultDates();
        this.updatePreview();
    }

    setupEventListeners() {
        // 表单输入监听
        const inputs = [
            'schoolName', 'studentNameKana', 'studentName', 'studentId',
            'grade', 'department', 'validFrom', 'validTo'
        ];
        
        inputs.forEach(id => {
            const element = document.getElementById(id);
            if (element) {
                element.addEventListener('input', () => this.updatePreview());
                element.addEventListener('change', () => this.updatePreview());
            }
        });

        // 生成按钮
        document.getElementById('generateButton').addEventListener('click', () => this.generateStudentId());
        
        // 背面生成按钮
        document.getElementById('generateBackButton').addEventListener('click', () => this.generateStudentIdBack());
        
        // 下载按钮
        document.getElementById('downloadButton').addEventListener('click', () => this.downloadStudentId());
        
        // 打印按钮
        document.getElementById('printButton').addEventListener('click', () => this.printStudentId());
    }

    setupPhotoUpload() {
        const uploadArea = document.getElementById('photoUploadArea');
        const photoInput = document.getElementById('photoInput');
        const photoPreview = document.getElementById('photoPreview');
        const previewImage = document.getElementById('previewImage');
        const removePhotoBtn = document.getElementById('removePhoto');

        // 点击上传区域
        uploadArea.addEventListener('click', () => photoInput.click());

        // 文件选择
        photoInput.addEventListener('change', (e) => this.handlePhotoUpload(e.target.files[0]));

        // 拖拽上传
        uploadArea.addEventListener('dragover', (e) => {
            e.preventDefault();
            uploadArea.classList.add('border-primary-400', 'bg-primary-50');
        });

        uploadArea.addEventListener('dragleave', (e) => {
            e.preventDefault();
            uploadArea.classList.remove('border-primary-400', 'bg-primary-50');
        });

        uploadArea.addEventListener('drop', (e) => {
            e.preventDefault();
            uploadArea.classList.remove('border-primary-400', 'bg-primary-50');
            const file = e.dataTransfer.files[0];
            if (file && file.type.startsWith('image/')) {
                this.handlePhotoUpload(file);
            }
        });

        // 删除照片
        removePhotoBtn.addEventListener('click', () => this.removePhoto());
    }

    handlePhotoUpload(file) {
        if (!file || !file.type.startsWith('image/')) {
            this.showToast('请选择有效的图片文件', 'error');
            return;
        }

        if (file.size > 5 * 1024 * 1024) {
            this.showToast('图片文件大小不能超过5MB', 'error');
            return;
        }

        const reader = new FileReader();
        reader.onload = (e) => {
            this.uploadedPhoto = e.target.result;
            document.getElementById('previewImage').src = e.target.result;
            document.getElementById('photoPreview').classList.remove('hidden');
            this.updatePreviewPhoto();
            this.showToast('照片上传成功', 'success');
        };
        reader.readAsDataURL(file);
    }

    removePhoto() {
        this.uploadedPhoto = null;
        document.getElementById('photoPreview').classList.add('hidden');
        document.getElementById('previewImage').src = '';
        document.getElementById('photoInput').value = '';
        this.updatePreviewPhoto();
    }

    setDefaultDates() {
        const today = new Date();
        const nextYear = new Date(today.getFullYear() + 1, today.getMonth(), today.getDate());
        
        document.getElementById('validFrom').value = today.toISOString().split('T')[0];
        document.getElementById('validTo').value = nextYear.toISOString().split('T')[0];
    }

    updatePreview() {
        // 收集表单数据
        this.studentData = {
            schoolName: document.getElementById('schoolName').value || '学校名を入力してください',
            studentNameKana: document.getElementById('studentNameKana').value || '-',
            studentName: document.getElementById('studentName').value || '-',
            studentId: document.getElementById('studentId').value || '-',
            grade: document.getElementById('grade').value || '-',
            department: document.getElementById('department').value || '-',
            validFrom: this.formatDate(document.getElementById('validFrom').value),
            validTo: this.formatDate(document.getElementById('validTo').value)
        };

        // 更新预览
        document.getElementById('previewSchoolName').textContent = this.studentData.schoolName;
        document.getElementById('previewStudentId').textContent = this.studentData.studentId;
        document.getElementById('previewNameKana').textContent = this.studentData.studentNameKana;
        document.getElementById('previewName').textContent = this.studentData.studentName;
        document.getElementById('previewGrade').textContent = this.studentData.grade;
        document.getElementById('previewDepartment').textContent = this.studentData.department;
        document.getElementById('previewValidFrom').textContent = this.studentData.validFrom;
        document.getElementById('previewValidTo').textContent = this.studentData.validTo;
        
        // 更新背面预览
        this.updateBackPreview();

        // 检查是否可以生成
        this.checkCanGenerate();
    }

    updatePreviewPhoto() {
        const previewPhoto = document.getElementById('previewPhoto');
        const photoPlaceholder = document.getElementById('previewPhotoPlaceholder');
        
        if (this.uploadedPhoto) {
            previewPhoto.src = this.uploadedPhoto;
            previewPhoto.classList.remove('hidden');
            photoPlaceholder.style.display = 'none';
        } else {
            previewPhoto.classList.add('hidden');
            photoPlaceholder.style.display = 'flex';
        }
    }

    updateBackPreview() {
        // 更新背面预览中的学校名称
        const backSchoolName = document.getElementById('backPreviewSchoolName');
        if (backSchoolName && this.studentData && this.studentData.schoolName) {
            backSchoolName.textContent = this.studentData.schoolName;
        }
    }

    formatDate(dateString) {
        if (!dateString) return '-';
        const date = new Date(dateString);
        const year = date.getFullYear();
        const month = String(date.getMonth() + 1).padStart(2, '0');
        const day = String(date.getDate()).padStart(2, '0');
        return `${year}.${month}.${day}`;
    }

    checkCanGenerate() {
        const required = ['schoolName', 'studentName', 'studentNameKana', 'validFrom', 'validTo'];
        const canGenerate = required.every(field => 
            document.getElementById(field).value.trim() !== ''
        ) && this.uploadedPhoto;

        const generateBtn = document.getElementById('generateButton');
        generateBtn.disabled = !canGenerate;
        
        if (canGenerate) {
            generateBtn.classList.remove('opacity-50', 'cursor-not-allowed');
        } else {
            generateBtn.classList.add('opacity-50', 'cursor-not-allowed');
        }
    }

    async generateStudentId() {
        if (!this.validateForm()) {
            return;
        }

        const generateBtn = document.getElementById('generateButton');
        const originalText = generateBtn.innerHTML;
        
        try {
            // 显示生成中状态
            generateBtn.innerHTML = '<i class="fas fa-spinner fa-spin mr-2"></i>生成中...';
            generateBtn.disabled = true;

            // 更新学生数据（确保使用最新的表单值）
            this.updatePreview();

            // 准备数据
            const formData = new FormData();
            
            // 添加学生信息
            Object.keys(this.studentData).forEach(key => {
                formData.append(key, this.studentData[key]);
            });

            // 添加照片（转换为blob）
            if (this.uploadedPhoto) {
                const blob = await this.dataURLToBlob(this.uploadedPhoto);
                formData.append('photo', blob, 'student-photo.jpg');
            }

            // 发送到后端生成
            const response = await fetch('/api/generate-student-id', {
                method: 'POST',
                body: formData
            });

            if (!response.ok) {
                throw new Error(`HTTP error! status: ${response.status}`);
            }

            const result = await response.blob();
            
            // 创建下载链接
            const url = window.URL.createObjectURL(result);
            this.generatedImageUrl = url;
            this.generatedBlob = result; // 保存blob用于下载
            
            // 启用下载和打印按钮
            document.getElementById('downloadButton').disabled = false;
            document.getElementById('printButton').disabled = false;
            
            this.showToast('学生证生成成功！', 'success');

        } catch (error) {
            console.error('生成学生证失败:', error);
            this.showToast('生成失败，请稍后重试', 'error');
        } finally {
            // 恢复按钮状态
            generateBtn.innerHTML = originalText;
            generateBtn.disabled = false;
        }
    }

    validateForm() {
        const requiredFields = [
            { id: 'schoolName', name: '学校名' },
            { id: 'studentName', name: '学生姓名(汉字)' },
            { id: 'studentNameKana', name: '学生姓名(假名)' },
            { id: 'validFrom', name: '有效期开始' },
            { id: 'validTo', name: '有效期结束' }
        ];

        for (const field of requiredFields) {
            const value = document.getElementById(field.id).value.trim();
            if (!value) {
                this.showToast(`请填写${field.name}`, 'error');
                document.getElementById(field.id).focus();
                return false;
            }
        }

        if (!this.uploadedPhoto) {
            this.showToast('请上传学生照片', 'error');
            return false;
        }

        // 验证日期
        const validFrom = new Date(document.getElementById('validFrom').value);
        const validTo = new Date(document.getElementById('validTo').value);
        
        if (validTo <= validFrom) {
            this.showToast('结束日期必须晚于开始日期', 'error');
            return false;
        }

        return true;
    }

    async dataURLToBlob(dataURL) {
        const response = await fetch(dataURL);
        return response.blob();
    }

    downloadStudentId() {
        if (!this.generatedImageUrl || !this.generatedBlob) {
            this.showToast('请先生成学生证', 'error');
            return;
        }

        try {
            const link = document.createElement('a');
            link.href = this.generatedImageUrl;
            
            // 使用学生姓名生成文件名，如果没有则使用默认名称
            const studentName = this.studentData.studentName || 'student';
            const timestamp = new Date().toISOString().slice(0, 10); // YYYY-MM-DD格式
            link.download = `学生证_${studentName}_${timestamp}.png`;
            
            document.body.appendChild(link);
            link.click();
            document.body.removeChild(link);
            
            this.showToast('学生证下载成功', 'success');
        } catch (error) {
            console.error('下载失败:', error);
            this.showToast('下载失败，请稍后重试', 'error');
        }
    }

    printStudentId() {
        if (!this.generatedImageUrl) {
            this.showToast('请先生成学生证', 'error');
            return;
        }

        const printWindow = window.open('', '_blank');
        printWindow.document.write(`
            <!DOCTYPE html>
            <html>
            <head>
                <title>学生证打印</title>
                <style>
                    body { 
                        margin: 0; 
                        padding: 20px; 
                        display: flex; 
                        justify-content: center; 
                        align-items: center; 
                        min-height: 100vh;
                        background: white;
                    }
                    img { 
                        max-width: 100%; 
                        height: auto; 
                        box-shadow: 0 4px 8px rgba(0,0,0,0.1);
                    }
                    @media print {
                        body { padding: 0; }
                        img { box-shadow: none; }
                    }
                </style>
            </head>
            <body>
                <img src="${this.generatedImageUrl}" alt="学生证">
            </body>
            </html>
        `);
        
        printWindow.document.close();
        printWindow.focus();
        
        // 等待图片加载后打印
        printWindow.onload = () => {
            setTimeout(() => {
                printWindow.print();
                printWindow.close();
            }, 500);
        };
    }

    showToast(message, type = 'info') {
        // 创建toast容器（如果不存在）
        let toastContainer = document.getElementById('toast-container');
        if (!toastContainer) {
            toastContainer = document.createElement('div');
            toastContainer.id = 'toast-container';
            toastContainer.className = 'fixed top-4 right-4 z-50 space-y-2';
            document.body.appendChild(toastContainer);
        }

        // 创建toast元素
        const toast = document.createElement('div');
        const bgColor = {
            success: 'bg-green-500',
            error: 'bg-red-500',
            info: 'bg-blue-500',
            warning: 'bg-yellow-500'
        }[type] || 'bg-blue-500';

        const icon = {
            success: 'fas fa-check-circle',
            error: 'fas fa-exclamation-circle',
            info: 'fas fa-info-circle',
            warning: 'fas fa-exclamation-triangle'
        }[type] || 'fas fa-info-circle';

        toast.className = `${bgColor} text-white px-4 py-3 rounded-lg shadow-lg flex items-center gap-3 transform translate-x-full transition-transform duration-300`;
        toast.innerHTML = `
            <i class="${icon}"></i>
            <span>${message}</span>
            <button class="ml-2 hover:bg-white/20 rounded p-1" onclick="this.parentElement.remove()">
                <i class="fas fa-times text-sm"></i>
            </button>
        `;

        toastContainer.appendChild(toast);

        // 显示动画
        setTimeout(() => {
            toast.classList.remove('translate-x-full');
        }, 100);

        // 自动移除
        setTimeout(() => {
            toast.classList.add('translate-x-full');
            setTimeout(() => {
                if (toast.parentElement) {
                    toast.remove();
                }
            }, 300);
        }, 5000);
    }

    // 随机填充表单
    randomFillForm() {
        const schools = [
            '東京大学', '京都大学', '大阪大学', '早稲田大学', '慶應義塾大学',
            '東京工業大学', '筑波大学', '名古屋大学', '九州大学', '北海道大学',
            '一橋大学', '神戸大学', '横浜国立大学', '千葉大学', '広島大学'
        ];

        const lastNames = [
            '田中', '佐藤', '鈴木', '高橋', '渡辺', '伊藤', '山本', '中村', 
            '小林', '加藤', '吉田', '山田', '佐々木', '山口', '松本', '井上',
            '木村', '林', '斉藤', '清水'
        ];

        const firstNames = [
            '太郎', '花子', '次郎', '美咲', '健太', '由美', '大輔', '麻衣',
            '智也', '愛子', '翔太', '恵美', '拓海', '友香', '裕介', '真理'
        ];

        const lastNamesKana = [
            'たなか', 'さとう', 'すずき', 'たかはし', 'わたなべ', 'いとう', 'やまもと', 'なかむら',
            'こばやし', 'かとう', 'よしだ', 'やまだ', 'ささき', 'やまぐち', 'まつもと', 'いのうえ',
            'きむら', 'はやし', 'さいとう', 'しみず'
        ];

        const firstNamesKana = [
            'たろう', 'はなこ', 'じろう', 'みさき', 'けんた', 'ゆみ', 'だいすけ', 'まい',
            'ともや', 'あいこ', 'しょうた', 'えみ', 'たくみ', 'ともか', 'ゆうすけ', 'まり'
        ];

        const grades = ['1年生', '2年生', '3年生', '4年生', '大学院生'];
        
        const departments = [
            '法学部', '経済学部', '文学部', '教育学部', '理学部', '工学部',
            '医学部', '農学部', '薬学部', '商学部', '社会学部', '国際関係学部',
            '情報学部', '環境学部', '心理学部', '建築学部'
        ];

        // 随机选择数据
        const randomSchool = schools[Math.floor(Math.random() * schools.length)];
        const randomLastName = lastNames[Math.floor(Math.random() * lastNames.length)];
        const randomFirstName = firstNames[Math.floor(Math.random() * firstNames.length)];
        const randomLastNameKana = lastNamesKana[Math.floor(Math.random() * lastNamesKana.length)];
        const randomFirstNameKana = firstNamesKana[Math.floor(Math.random() * firstNamesKana.length)];
        const randomGrade = grades[Math.floor(Math.random() * grades.length)];
        const randomDepartment = departments[Math.floor(Math.random() * departments.length)];

        // 生成随机学号
        const currentYear = new Date().getFullYear();
        const randomStudentId = `${currentYear}${String(Math.floor(Math.random() * 90000) + 10000)}`;

        // 填充表单
        document.getElementById('schoolName').value = randomSchool;
        document.getElementById('studentName').value = `${randomLastName} ${randomFirstName}`;
        document.getElementById('studentNameKana').value = `${randomLastNameKana} ${randomFirstNameKana}`;
        document.getElementById('studentId').value = randomStudentId;
        document.getElementById('grade').value = randomGrade;
        document.getElementById('department').value = randomDepartment;

        // 根据数据类型设置有效期
        if (this.studentData.schoolName === '石川県立看護大学') {
            // 使用指定的测试数据日期
            document.getElementById('validFrom').value = '2023-04-01';
            document.getElementById('validTo').value = '2027-03-31';
        } else {
            // 设置有效期（当前年度的学年）
            const startDate = new Date(currentYear, 3, 1); // 4月1日
            const endDate = new Date(currentYear + 1, 2, 31); // 次年3月31日
            
            document.getElementById('validFrom').value = startDate.toISOString().split('T')[0];
            document.getElementById('validTo').value = endDate.toISOString().split('T')[0];
        }

        // 创建随机头像照片
        this.generateRandomPhoto();

        // 更新预览
        this.updatePreview();

        // 显示提示
        this.showToast('ランダムデータを入力しました！', 'success');
    }

    // 生成随机头像照片
    generateRandomPhoto() {
        const canvas = document.createElement('canvas');
        canvas.width = 300;
        canvas.height = 400;
        const ctx = canvas.getContext('2d');

        // 随机背景色
        const bgColors = [
            '#e3f2fd', '#f3e5f5', '#e8f5e8', '#fff3e0', 
            '#fce4ec', '#e1f5fe', '#f1f8e9', '#fff8e1'
        ];
        const bgColor = bgColors[Math.floor(Math.random() * bgColors.length)];
        ctx.fillStyle = bgColor;
        ctx.fillRect(0, 0, 300, 400);

        // 绘制简单的人像轮廓
        ctx.fillStyle = '#90a4ae';
        
        // 头部
        ctx.beginPath();
        ctx.arc(150, 130, 50, 0, Math.PI * 2);
        ctx.fill();

        // 身体
        ctx.fillRect(120, 180, 60, 80);

        // 添加文字
        ctx.fillStyle = '#424242';
        ctx.font = '16px Arial';
        ctx.textAlign = 'center';
        ctx.fillText('サンプル写真', 150, 320);

        // 转换为blob并设置照片
        canvas.toBlob((blob) => {
            const reader = new FileReader();
            reader.onload = (e) => {
                this.uploadedPhoto = e.target.result;
                document.getElementById('previewImage').src = e.target.result;
                document.getElementById('photoPreview').classList.remove('hidden');
                this.updatePreviewPhoto();
            };
            reader.readAsDataURL(blob);
        });
    }

    // 清空表单
    clearForm() {
        document.getElementById('studentForm').reset();
        this.removePhoto();
        this.setDefaultDates();
        this.updatePreview();
        this.showToast('フォームをクリアしました', 'info');
    }

    // 填充指定的测试数据
    fillTestData() {
        // 填充您提供的具体测试数据
        document.getElementById('schoolName').value = '石川県立看護大学';
        document.getElementById('studentName').value = '山田 さくら';
        document.getElementById('studentNameKana').value = 'やまだ さくら';
        document.getElementById('studentId').value = 'N2023-0456';
        document.getElementById('grade').value = '2年生';
        document.getElementById('department').value = '看護学部 看護学科';
        document.getElementById('validFrom').value = '2023-04-01';
        document.getElementById('validTo').value = '2027-03-31';
        
        // 生成测试照片
        this.generateRandomPhoto();
        
        // 更新预览
        this.updatePreview();
        
        // 显示提示
        this.showToast('石川県立看護大学のテストデータを入力しました！', 'success');
    }

    async generateStudentIdBack() {
        if (!this.validateFormBasic()) {
            return;
        }

        const generateBtn = document.getElementById('generateBackButton');
        const originalText = generateBtn.innerHTML;
        
        try {
            // 显示生成中状态
            generateBtn.innerHTML = '<i class="fas fa-spinner fa-spin mr-2"></i>生成中...';
            generateBtn.disabled = true;

            // 更新学生数据（确保使用最新的表单值）
            this.updatePreview();

            // 准备数据
            const formData = new FormData();
            
            // 添加学生信息
            Object.keys(this.studentData).forEach(key => {
                formData.append(key, this.studentData[key]);
            });

            // 发送到后端生成背面
            const response = await fetch('/api/generate-student-id-back', {
                method: 'POST',
                body: formData
            });

            if (!response.ok) {
                throw new Error(`HTTP error! status: ${response.status}`);
            }

            const result = await response.blob();
            
            // 创建下载链接并自动下载
            const url = window.URL.createObjectURL(result);
            const link = document.createElement('a');
            link.href = url;
            
            // 使用学生姓名生成文件名
            const studentName = this.studentData.studentName || 'student';
            const timestamp = new Date().toISOString().slice(0, 10); // YYYY-MM-DD格式
            link.download = `学生証背面_${studentName}_${timestamp}.png`;
            
            document.body.appendChild(link);
            link.click();
            document.body.removeChild(link);
            
            // 清理URL对象
            window.URL.revokeObjectURL(url);
            
            this.showToast('学生証の裏面生成・ダウンロード成功！', 'success');
        } catch (error) {
            console.error('生成学生证背面失败:', error);
            this.showToast('裏面生成に失敗しました', 'error');
        } finally {
            // 恢复按钮状态
            generateBtn.innerHTML = originalText;
            generateBtn.disabled = false;
        }
    }

    validateFormBasic() {
        const requiredFields = [
            { id: 'schoolName', name: '学校名' }
        ];

        for (const field of requiredFields) {
            const value = document.getElementById(field.id).value.trim();
            if (!value) {
                this.showToast(`请填写${field.name}`, 'error');
                document.getElementById(field.id).focus();
                return false;
            }
        }
        
        return true;
    }
}

// 添加全局函数供HTML调用
window.randomFillForm = function() {
    const generator = window.studentIdGenerator || new StudentIdGenerator();
    generator.randomFillForm();
};

window.clearForm = function() {
    const generator = window.studentIdGenerator || new StudentIdGenerator();
    generator.clearForm();
};

window.fillTestData = function() {
    const generator = window.studentIdGenerator || new StudentIdGenerator();
    generator.fillTestData();
};

// 页面加载完成后初始化
document.addEventListener('DOMContentLoaded', () => {
    new StudentIdGenerator();
});

// 导出到全局作用域，供HTML调用
window.generateStudentId = function() {
    const generator = window.studentIdGenerator || new StudentIdGenerator();
    generator.generateStudentId();
};

window.downloadStudentId = function() {
    const generator = window.studentIdGenerator || new StudentIdGenerator();
    generator.downloadStudentId();
};

window.printStudentId = function() {
    const generator = window.studentIdGenerator || new StudentIdGenerator();
    generator.printStudentId();
};

window.generateStudentIdBack = function() {
    const generator = window.studentIdGenerator || new StudentIdGenerator();
    generator.generateStudentIdBack();
};