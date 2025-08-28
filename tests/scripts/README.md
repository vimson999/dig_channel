# Utility Scripts

数据维护、检查和修复工具脚本集合。

## 📋 脚本说明

### 数据检查工具
- **`check_data_completeness.py`** - 检查SQLite和PostgreSQL数据完整性对比
- **`detailed_date_check.py`** - 详细的日期数据检查和分析

### 数据同步工具
- **`sync_missing_channel_data.py`** - 同步缺失的频道数据记录
- **`sync_missing_data.py`** - 通用缺失数据同步工具

### 环境和配置
- **`fix_env.py`** - 修复环境变量配置，确保使用PostgreSQL
- **`verify_fix.sh`** - 验证修复结果的Shell脚本

### 测试工具
- **`test_insert.py`** - 测试数据插入功能的验证脚本

## 🚀 使用方法

### 数据完整性检查
```bash
# 检查数据完整性
python check_data_completeness.py

# 详细日期检查
python detailed_date_check.py
```

### 数据同步
```bash
# 同步缺失的频道数据
python sync_missing_channel_data.py

# 通用数据同步
python sync_missing_data.py
```

### 环境修复
```bash
# 修复环境配置
python fix_env.py

# 验证修复结果
bash verify_fix.sh
```

### 功能测试
```bash
# 测试数据插入
python test_insert.py
```

## 📊 脚本特点

- **独立运行**: 每个脚本都可以独立执行
- **详细输出**: 提供详细的执行日志和结果报告
- **错误处理**: 包含完善的异常处理机制
- **安全检查**: 操作前进行必要的验证

## ⚠️ 使用注意

1. **生产环境**: 在生产环境使用前请先在测试环境验证
2. **数据备份**: 执行数据修改操作前请备份数据
3. **权限检查**: 确保有足够的数据库操作权限
4. **配置验证**: 检查数据库连接配置是否正确