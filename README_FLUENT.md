# Core Commander v8.1 - Fluent Design Edition

## 🎨 重构说明

本版本将原有的 PyQt6 UI 完全重构为 **PySide6 + QFluentWidgets**，采用 **Windows 11 Fluent Design** 风格。

## ✨ 主要特性

### 1. **现代化UI设计**
- ✅ 使用 QFluentWidgets 组件库
- ✅ Windows 11 Fluent Design 风格
- ✅ 圆角、阴影、动效统一
- ✅ 自动跟随系统深色/浅色模式

### 2. **左侧导航栏**
- 🏠 **主页** - 核心优化功能
- ⚙️ **功能设置** - 系统配置选项
- ℹ️ **关于** - 应用信息

### 3. **卡片式布局**
- 📦 SimpleCardWidget - 基础信息卡片
- 📦 ElevatedCardWidget - 高级功能卡片（带阴影）
- 📦 所有内容模块化，清晰易读

### 4. **Fluent 组件**
- 🔘 ToggleButton - 核心选择按钮
- 📝 LineEdit - 输入框
- 📋 ComboBox - 下拉选择
- ☑️ CheckBox - 复选框
- 🔢 SpinBox - 数字输入
- 🔔 InfoBar - 通知提示
- 💬 MessageBox - 对话框

## 🚀 运行方式

### 方法1：直接运行新版本
```bash
python main_fluent.py
```

### 方法2：对比运行（可选）
```bash
# 旧版本（PyQt6）
python main.py

# 新版本（PySide6 + Fluent）
python main_fluent.py
```

## 📦 依赖库

已安装的依赖：
- ✅ PySide6 (6.10.1)
- ✅ PySide6-Fluent-Widgets (1.11.0)
- ✅ psutil
- ✅ pywin32

## 🎯 核心功能保持不变

### 后端逻辑 100% 保留：
- ✅ 硬件拓扑识别引擎
- ✅ 双模内存清理（狂暴/护航）
- ✅ CPU 核心绑定
- ✅ 进程隔离
- ✅ 守护进程
- ✅ 电源计划切换
- ✅ PID 智能追踪

## 🎨 UI 对比

### 旧版本 (PyQt6)
- 传统深色主题
- 自定义样式表
- 固定颜色方案

### 新版本 (PySide6 + Fluent)
- ✨ Windows 11 Fluent Design
- ✨ 亚克力/云母效果（组件库内置）
- ✨ 自动主题切换
- ✨ 流畅动画效果
- ✨ 现代化卡片布局
- ✨ 左侧导航栏

## 📝 代码结构

```
main_fluent.py
├── 1. 硬件拓扑引擎 (保持不变)
├── 2. 系统服务 & 双模清理 (保持不变)
├── 3. 进程选择对话框 (Fluent 风格)
├── 4. 核心按钮组件 (ToggleButton)
├── 5. 主页界面 (HomePage)
├── 6. 设置页面 (SettingsPage)
├── 7. 关于页面 (AboutPage)
├── 8. 主窗口 (FluentWindow)
└── 9. 程序入口
```

## 🔧 主要改动

### UI 框架替换
```python
# 旧版本
from PyQt6.QtWidgets import *
from PyQt6.QtCore import pyqtSignal

# 新版本
from PySide6.QtWidgets import *
from PySide6.QtCore import Signal
from qfluentwidgets import *
```

### 信号机制
```python
# PyQt6
pyqtSignal(bool, str)

# PySide6
Signal(bool, str)
```

### 窗口基类
```python
# 旧版本
class MainWindow(QMainWindow)

# 新版本
class MainWindow(FluentWindow)  # 自带导航栏
```

## 🎯 使用建议

1. **首次运行**：以管理员身份运行
2. **主题切换**：自动跟随系统（Win11 设置 > 个性化 > 颜色）
3. **导航使用**：点击左侧图标切换页面
4. **卡片交互**：所有功能模块化在卡片中

## 📸 界面预览

### 主页
- CPU 信息卡片
- 进程选择卡片
- 首选核心配置
- 核心选择网格
- 内存清理控制
- 应用优化按钮

### 功能设置
- 后台进程隔离开关
- 配置守护进程开关
- 开机自动启动开关

### 关于
- 应用版本信息
- 作者信息

## ⚠️ 注意事项

1. **权限要求**：必须以管理员身份运行
2. **兼容性**：Windows 10/11
3. **配置文件**：新版本使用独立配置（FluentConfigs）
4. **旧版本**：main.py 保持不变，可随时切换

## 🎉 完成状态

- ✅ 依赖安装完成
- ✅ UI 完全重构
- ✅ 后端逻辑保持不变
- ✅ Fluent Design 风格实现
- ✅ 左侧导航栏
- ✅ 卡片式布局
- ✅ 主题自动切换
- ✅ 所有组件使用 QFluentWidgets

## 📞 技术支持

作者：B站 _可燃垃圾

---

**享受全新的 Fluent Design 体验！** 🚀
