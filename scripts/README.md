# Scripts Directory

这个目录包含了项目中使用的各种脚本文件，已按功能分类整理。

## 开发服务器脚本

### 🚀 启动脚本
- **`start-dev.sh`** - 启动开发服务器（后端Flask + 前端静态服务）
- **`start-all-dev.sh`** - 启动完整开发环境（包含调度器）
- **`start_scheduler.sh`** - 单独启动调度器服务
- **`dev-server.sh`** - 简单的开发服务器启动脚本

### 🛑 停止脚本
- **`stop-dev.sh`** - 停止开发服务器

## 工具脚本

### 📦 安装脚本
- **`install_mcp_servers.sh`** - 安装 Claude MCP 服务器工具

### 🔧 数据库维护脚本
- **`add_channel_sort_field.py`** - 添加频道排序字段
- **`fix_channel_ids.py`** - 修复频道ID问题
- **`fix_imported_channels.py`** - 修复导入的频道数据
- **`import_channels.py`** - 导入频道数据

## 使用说明

### 快速开发环境启动
```bash
# 启动基础开发服务器
./scripts/start-dev.sh

# 启动完整开发环境（包含调度器）
./scripts/start-all-dev.sh

# 停止开发服务器
./scripts/stop-dev.sh
```

### 工具脚本使用
```bash
# 安装 MCP 服务器
./scripts/install_mcp_servers.sh

# 运行数据库维护脚本（需要激活虚拟环境）
source venv/bin/activate
python scripts/import_channels.py
```

## 注意事项

1. 所有 shell 脚本都已设置为可执行权限
2. Python 脚本需要在激活虚拟环境后运行
3. 开发服务器脚本会自动检查端口占用情况
4. 调度器脚本会在后台运行，注意进程管理

## 目录结构
```
scripts/
├── README.md                    # 本说明文件
├── dev-server.sh               # 开发服务器
├── start-dev.sh                # 启动开发环境
├── start-all-dev.sh            # 启动完整开发环境
├── start_scheduler.sh          # 启动调度器
├── stop-dev.sh                 # 停止开发服务器
├── install_mcp_servers.sh      # 安装MCP服务器
├── add_channel_sort_field.py   # 添加频道排序字段
├── fix_channel_ids.py          # 修复频道ID
├── fix_imported_channels.py    # 修复导入频道
└── import_channels.py          # 导入频道数据
```