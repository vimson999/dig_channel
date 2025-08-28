# Database Scripts

数据库相关的初始化、迁移和管理脚本。

## 📋 文件说明

### SQL文件
- **`create_tables.sql`** - 原始数据库表结构定义
- **`create_tables_fixed.sql`** - 修复后的表结构定义（推荐使用）

### 初始化脚本
- **`init_direct.py`** - 直接执行完整SQL文件初始化数据库
- **`init_postgres.py`** - PostgreSQL专用初始化脚本
- **`init_simple.py`** - 简化版数据库初始化脚本

### 迁移脚本
- **`migrate_data.py`** - SQLite到PostgreSQL完整数据迁移工具

## 🚀 使用方法

### 初始化新数据库
```bash
# 推荐方式：使用修复后的SQL文件
python init_direct.py

# 或者使用PostgreSQL专用脚本
python init_postgres.py
```

### 数据迁移
```bash
# 从SQLite迁移到PostgreSQL
python migrate_data.py
```

## ⚠️ 注意事项

1. **备份数据**: 在运行迁移脚本前请备份现有数据
2. **配置检查**: 确保数据库连接配置正确
3. **权限验证**: 确保数据库用户有足够的操作权限
4. **版本兼容**: 使用`create_tables_fixed.sql`获得最佳兼容性