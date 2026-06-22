# Core Commander - Windows System & Game Low-Level Optimization Console

[![Platform](https://img.shields.io/badge/Platform-Windows%2010%20%2F%2011-0078D4?style=for-the-badge&logo=windows11&logoColor=white)](https://microsoft.com/windows)
[![Python](https://img.shields.io/badge/Python-3.8%2B-3776AB?style=for-the-badge&logo=python&logoColor=white)](https://python.org)
[![UI Framework](https://img.shields.io/badge/UI%20Framework-PySide6%20%2B%20QFluentWidgets-30ba78?style=for-the-badge&logo=qt&logoColor=white)](https://github.com/zhiyiYo/PySide6-Fluent-Widgets)
[![License](https://img.shields.io/badge/License-Proprietary-red?style=for-the-badge&logo=github&logoColor=white)](#)

**Core Commander** is a professional-grade system and game low-level performance optimization tool designed specifically for the Windows platform. Built on the modern **Windows 11 Fluent Design** visual system, it unlocks hardware capabilities by managing the system kernel scheduler, processor affinity masks, a dual-mode memory cleaning engine, and dozens of registry and service-level tweaks. It is designed to provide gamers and professional creators with ultra-low latency and maximum system responsiveness.

---

## UI Screenshot Preview

### 1. Home Dashboard
*Provides hardware topology overview, target process binding, preferred logical processor routing, instant physical memory cleanup, and the strategy application scheduling center.*
![Home Dashboard](./screenshots/home_light.png)

### 2. Basic Tuning Configuration (General)
*General OS options including Windows visual effect reduction, transparency toggle, widget disable, and startup configuration.*
![General Configuration](./screenshots/general_light.png)

### 3. CPU Scheduling & Core Latency (CPU)
*Advanced CPU tuning: core parking controls, EPP (Energy Performance Preference), disabling High Precision Event Timer (HPET), and win32 priority separation.*
![CPU Scheduling](./screenshots/cpu_light.png)

### 4. GPU & Rendering Pipeline (GPU)
*Graphics driver tweaks, DirectX pipeline controls, GPU preemption toggle, MSI (Message Signaled Interrupts) utility integration, and NVIDIA Profiler configuration.*
![GPU & Rendering](./screenshots/gpu_light.png)

### 5. Memory Management & Storage Optimization (Memory)
*Dual-mode physical memory working set / standby list cleanup, NVMe read/write optimization, and memory compression controls.*
![Memory & Storage](./screenshots/memory_light.png)

### 6. Peripherals & Input Latency (Peripherals)
*Hardware-level keyboard and mouse buffer queue sizes, custom repeat delay rates, USB low-latency routing, and dynamic lighting controls.*
![Peripherals & Latency](./screenshots/peripheral_light.png)

### 7. Network Stack & DNS Path Tuning (Network)
*Network throttling indexing, DNS client priorities, TCP/IP stack optimization, and TCP BBR congestion control activation.*
![Network Tuning](./screenshots/network_light.png)

### 8. System Services & Privacy Restriction (Privacy)
*Disabling telemetry, diagnostics, useless Windows background services, and controlling Windows Defender / SmartScreen to eliminate micro-stutters.*
![Services & Privacy](./screenshots/privacy_light.png)

### 9. Startup Items Manager (Startup)
*Scans, enables, or disables startup applications and services, keeping the boot process clean.*
![Startup Manager](./screenshots/startup_light.png)

### 10. Advanced Diagnostics & External Toolset (Tools)
*Interactive call support for premium standalone utilities (Dism++, HiBit Uninstaller, BoosterX, O&O ShutUp10, WPD, AutoGpuAffinity) and system registry backups.*
![Advanced Tools](./screenshots/tools_light.png)

### 11. About Console
*Software version information, configuration logs, and licensing.*
![About](./screenshots/about_light.png)

---

## Key Features

### 1. Hardware Topology Awareness & CPU Affinity Routing
- **Architectural Sensing**: Recognizes heterogeneous CPU designs (Intel hybrid P-Cores / E-Cores) and AMD chiplet designs.
- **Affinity Mask Locking**: Restricts target games or creative apps to specific cores, shielding them from background scheduling interrupts.
- **Preferred Core Binding**: Routes primary rendering or game physics threads to the two highest-priority physical P-Cores to maximize single-thread IPC throughput.

### 2. Intelligent Dual-Mode Memory Reclamation Engine
- **Working Set Trimming**: Releases physical memory occupied by inactive processes.
- **Standby List Flushing**: Clears cached memory standby lists to completely eliminate periodic micro-stutters during heavy gaming.
- **Scheduled Background Cleanup**: Quietly runs memory cleanup at user-defined intervals (in minutes) to maintain a lean system state.

### 3. Elevated QoS & Priority Orchestration
- **Active Process Guardian**: A background watchdog automatically tracks target game PIDs and instantly applies high CPU priorities and affinity masks upon launch.
- **Windows QoS Policies**: Configures game-level Quality of Service bandwidth and processor priority scheduling inside the Windows kernel.

### 4. Deep Kernel & Hardware Tuning
- **Input Latency Reduction**: Minimizes USB interrupt latency and adjusts mouse/keyboard driver input buffers for instantaneous input response.
- **Power Management Configuration**: Deploys an optimized Ultimate Power Plan and configures PCIe ASPM states.

---

## Quick Start

### Prerequisites
This utility requires **Administrator Privileges** to access low-level hardware interfaces and modify system registry keys.

1. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
   *Or install manually:*
   ```bash
   pip install PySide6 qfluentwidgets psutil pywin32
   ```

2. Run the application:
   Right-click `run.bat` and select **"Run as Administrator"**, or execute in an elevated terminal:
   ```bash
   python main.py
   ```

---

## Architecture & Rebuilding
- **Modern Fluent Design**: Built on `PySide6` and `QFluentWidgets`, aligning perfectly with Windows 11 visuals (including rounded corners, smooth drop shadows, acrylic/mica transparency, and fluid animations).
- **Theme Adaptability**: Seamlessly switches between light and dark modes according to system settings.
- **Decoupled Architecture**: Standardized separation between system worker calls (`core/`), visual render layout (`ui/`), and user configuration (`config/`).

---

## Disclaimer
1. **Elevated Privileges**: Low-level kernel optimizations, process watchdog monitoring, and memory standby list flushing require administrator execution to function.
2. **System Modifications**: Certain options write to deep system registries (e.g., disabling HPET, VBS, or driver blocklists). Review description cards carefully and create a system restore point prior to deploying configurations.

---

## Support & Author
- **Developer**: `_可燃垃圾` (Bilibili)
