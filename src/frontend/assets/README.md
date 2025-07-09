# Frontend Assets Directory

这个目录存放前端项目的静态资源文件。

## 目录结构

```
assets/
├── README.md           # 本说明文件
├── fonts/              # 字体文件
│   └── __init__.py
└── images/             # 图像资源
    ├── __init__.py
    ├── favicon.ico     # 网站图标
    ├── favicon-16x16.png
    ├── favicon-32x32.png
    ├── apple-touch-icon.png
    ├── android-chrome-192x192.png
    └── android-chrome-512x512.png
```

## 文件说明

### 🖼️ 图标文件 (images/)
- **favicon.ico** - 标准网站图标
- **favicon-16x16.png** - 16x16像素PNG图标
- **favicon-32x32.png** - 32x32像素PNG图标
- **apple-touch-icon.png** - 苹果设备主屏幕图标 (180x180)
- **android-chrome-192x192.png** - Android Chrome图标 (192x192)
- **android-chrome-512x512.png** - Android Chrome图标 (512x512)

### 🔤 字体文件 (fonts/)
- 暂无自定义字体文件
- 目前使用系统字体和Google Fonts

## 使用说明

### 在HTML中引用图标
```html
<!-- 标准图标 -->
<link rel="icon" type="image/x-icon" href="/assets/images/favicon.ico">
<link rel="icon" type="image/png" sizes="16x16" href="/assets/images/favicon-16x16.png">
<link rel="icon" type="image/png" sizes="32x32" href="/assets/images/favicon-32x32.png">
<link rel="apple-touch-icon" sizes="180x180" href="/assets/images/apple-touch-icon.png">
```

### 在Web Manifest中引用
```json
{
  "icons": [
    {
      "src": "/assets/images/favicon-16x16.png",
      "sizes": "16x16",
      "type": "image/png"
    }
  ]
}
```

## 添加新资源

### 添加图片
1. 将图片文件放入 `images/` 目录
2. 使用相对路径引用: `/assets/images/your-image.png`

### 添加字体
1. 将字体文件放入 `fonts/` 目录
2. 在CSS中使用 `@font-face` 引用
3. 使用相对路径: `/assets/fonts/your-font.woff2`

## 注意事项

1. **路径引用**: 所有静态资源都应该使用以 `/assets/` 开头的绝对路径
2. **文件命名**: 使用小写字母和连字符，避免空格和特殊字符
3. **图片优化**: 添加图片前请确保已经优化过大小和格式
4. **兼容性**: 图标文件应该支持主流浏览器和设备

## 文件大小建议

- **favicon.ico**: < 10KB
- **PNG图标**: < 20KB
- **一般图片**: < 200KB
- **字体文件**: < 100KB per font

## 版本控制

- 所有静态资源都应该纳入版本控制
- 修改资源文件时请更新相关的引用
- 删除不再使用的资源文件