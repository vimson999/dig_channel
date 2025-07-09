#!/bin/bash

# Claude MCP 服务器安装脚本
# 这个脚本会安装常用的 MCP 服务器工具

echo "开始安装 Claude MCP 服务器..."

# 1. Context7（官方文档查询）
echo "安装 Context7 - 官方文档查询工具..."
claude mcp add context7 -s user -- npx -y @upstash/context7-mcp

# 2. Sequential Thinking（多步骤逻辑推理）
echo "安装 Sequential Thinking - 多步骤逻辑推理工具..."
claude mcp add sequential-thinking -s user -- npx -y @modelcontextprotocol/server-sequential-thinking

# 3. Magic（UI 组件生成）
echo "安装 Magic - UI 组件生成工具..."
claude mcp add magic -s user -- npx -y @21st-dev/magic-mcp

# 4. Puppeteer（网页自动化 / E2E 测试）
echo "安装 Puppeteer - 网页自动化/E2E 测试工具..."
claude mcp add puppeteer -s user -- npx -y @modelcontextprotocol/server-puppeteer

# 列出已安装的 MCP 工具以确认安装成功
echo "安装完成！以下是已安装的 MCP 服务器:"
claude mcp list

echo "所有 MCP 服务器安装完成！"