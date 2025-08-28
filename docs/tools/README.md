# 工具集合

本目录包含项目相关的各种工具和实用脚本。

## 📁 目录结构

```
tools/
├── README.md                   # 本文档
└── colab/                      # Google Colab 相关工具
    ├── ai_clip_creator.py      # AI驱动的短视频切片工具
    ├── video_processor.py      # 视频处理工具
    └── youtube_automation_manager.py # YouTube自动化管理工具
```

## 🛠️ 工具说明

### 📹 Colab工具集 (colab/)

#### `ai_clip_creator.py` - AI短视频切片生成器
功能强大的自动化脚本，用于从长视频中智能提取精华片段并制作带双语字幕的短视频。

**主要功能**：
- 自动下载YouTube视频
- 使用Whisper模型生成原文字幕
- 调用DeepSeek API翻译成中文
- AI智能推荐爆款片段
- 自动切割和烧录字幕

**使用环境**: Google Colab

#### `video_processor.py` - 视频处理工具
视频后处理和格式转换工具。

#### `youtube_automation_manager.py` - YouTube自动化管理
YouTube频道和视频的自动化管理工具。

## 🚀 使用方法

### 环境要求
- Python 3.8+
- 相关依赖包（见各脚本文件开头注释）

### 在Google Colab中使用
1. 上传脚本文件到Colab
2. 安装所需依赖包
3. 设置API密钥和配置
4. 运行相应的功能模块

### 本地使用
```bash
# 安装依赖
pip install -r requirements/base.txt

# 运行工具（以AI切片工具为例）
python tools/colab/ai_clip_creator.py
```

## ⚠️ 注意事项

1. **API密钥**: 确保设置了正确的API密钥
2. **依赖安装**: 每个工具可能需要不同的依赖包
3. **使用限制**: 注意各API的使用限制和费用
4. **版权问题**: 使用时请遵守相关版权法律法规

## 🔧 贡献指南

如果您有新的工具脚本想要添加：

1. 在对应的子目录中添加脚本
2. 在脚本开头添加详细的文档说明
3. 更新本README文件
4. 提交Pull Request

## 📝 更新记录

- **2025-08-28**: 从docs/colab移动工具脚本到此目录
- **2025-08-28**: 创建工具目录结构和说明文档