# Tests Directory Structure

这个目录包含了项目的所有测试和相关工具脚本，按功能进行了合理的分类组织。

## 📁 目录结构

```
tests/
├── README.md                   # 本文档
├── __init__.py
├── unit/                       # 单元测试
├── integration/                # 集成测试
├── e2e/                        # 端到端测试
├── manual/                     # 手工验证脚本
│   ├── api/                    # API/前端联调脚本
│   └── final_test_all_fixes.py
├── debug/                      # 线上快速调试脚本
├── tts/                        # TTS 功能专项测试
├── celery/                     # Celery 调度测试
├── fixtures/                   # 测试数据和配置
├── database/                   # 数据库相关脚本
└── scripts/                    # 工具和维护脚本
```

## 📝 各目录说明

### 🧪 unit/ - 单元测试
包含所有单元测试文件，测试独立的函数和类：
- `test_api.py` - API接口单元测试
- `test_youtube_api.py` - YouTube API相关测试

### 🔗 integration/ - 集成测试
包含测试多个组件协作的集成测试

### 🌐 e2e/ - 端到端测试
包含完整业务流程的端到端测试

### 🧭 manual/ - 手工验证
用于人工验证和复现问题的脚本：
- `api/` 下的脚本覆盖带认证接口、前端修复验证等场景
- `final_test_all_fixes.py` 汇总关键修复项的人工检查

### 🛠️ debug/ - 调试脚本
快速定位线上问题的诊断脚本，例如频道数据对齐、Reddit采集器等。

### 🔊 tts/ - 语音合成专项
覆盖TTS服务端、模型切换、多说话人对话等专项测试脚本。

### ⚙️ celery/ - 调度测试
`test_celery_full.py` 用于验证Celery任务的全流程运行。

### 📊 fixtures/ - 测试数据和配置
包含测试所需的固定数据、配置文件和模拟数据

### 🗄️ database/ - 数据库相关脚本
包含数据库初始化、迁移和管理脚本：
- `create_tables.sql` - 数据库表结构SQL
- `create_tables_fixed.sql` - 修复后的表结构SQL
- `init_direct.py` - 直接执行SQL初始化脚本
- `init_postgres.py` - PostgreSQL初始化脚本
- `init_simple.py` - 简单初始化脚本
- `migrate_data.py` - 数据迁移脚本（SQLite到PostgreSQL）

### 🔧 scripts/ - 工具和维护脚本
包含数据维护、检查和修复工具：
- `check_data_completeness.py` - 数据完整性检查工具
- `detailed_date_check.py` - 详细日期数据检查
- `sync_missing_channel_data.py` - 同步缺失的频道数据
- `sync_missing_data.py` - 同步缺失数据通用工具
- `fix_env.py` - 环境配置修复工具
- `test_insert.py` - 数据插入测试脚本
- `verify_fix.sh` - 修复验证脚本

## 🚀 使用指南

### 运行测试
```bash
# 运行所有单元测试
python -m pytest tests/unit/

# 运行特定测试文件
python -m pytest tests/unit/test_api.py

# 运行集成测试
python -m pytest tests/integration/

# 运行端到端测试
python -m pytest tests/e2e/
```

### 数据库操作
```bash
# 初始化数据库
python tests/database/init_direct.py

# 迁移数据
python tests/database/migrate_data.py

# 检查数据完整性
python tests/scripts/check_data_completeness.py
```

### 维护脚本
```bash
# 修复环境配置
python tests/scripts/fix_env.py

# 同步缺失数据
python tests/scripts/sync_missing_data.py

# 验证修复结果
bash tests/scripts/verify_fix.sh
```

> 提示：前端导航相关的手工测试页面已放在 `src/frontend/tests/` 目录，可直接通过本地静态服务打开。

## 📋 最佳实践

1. **测试分类**: 严格按照功能将测试分类到对应目录
2. **命名规范**: 测试文件以`test_`开头，脚本文件使用描述性名称
3. **文档完善**: 每个脚本都包含详细的文档说明
4. **独立性**: 确保测试和脚本可以独立运行
5. **版本控制**: 重要的数据库变更脚本要保留历史版本

## 🔄 更新日志

- 2025-08-28: 重新组织目录结构，按功能分类文件
- 2025-09-XX: 整理手工验证与调试脚本，前端测试页面迁移至 `src/frontend/tests/`
