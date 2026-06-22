# 变声器完整链路与依赖分类架构图

为了确保变声器 (RVC) 模块能在后续云端化分离中完美运行，我们将变声器所需的底层库、模型、音频管道进行如下详细分类：

## 1. 深度学习计算与框架层 (Deep Learning Backend)
这部分是体积最大的核心计算依赖（约2.5GB），完全可以做成绿色的 `AI_Env.zip`：
- **`torch` (PyTorch)**: 提供张量计算、模型前向推理，以及 CUDA 加速。
- **`torchaudio`**: PyTorch 音频处理附属库。
- **`torchvision`**: PyTorch 视觉附属库（部分 RVC 旧依赖可能间接引用）。
- **`fairseq`**: Facebook 开源的序列模型库，用于加载 Hubert 特征提取模型。（注：由于 Fairseq 较老，依赖旧版 dataclasses 及 PyTorch 旧版特性，已在代码中通过 Monkey Patch 解决）。
- **`faiss-cpu` / `faiss-gpu`**: 向量索引检索库，用于 RVC 音色融合计算（Index Rate）。

## 2. 音频处理与特征工程层 (Audio Processing)
负责语音格式的转换、重采样及数学矩阵计算（约200MB）：
- **`librosa`**: 用于音频时频分析、变调操作。
- **`soundfile` / `audioread`**: 底层音频文件读写，支持浮点数 WAV 的高速存取。
- **`scipy` / `numpy`**: 用于信号处理滤波器（如 RMS 音量计算、平滑等）与数组运算。
- **`ffmpeg-python` / `ffmpeg`**: RVC 内部重采样或部分输入解码需要调用本地的 FFmpeg。

## 3. 硬件交互与音频流捕获层 (Hardware Streaming)
这部分保留在主程序中，负责捕获麦克风和输出到扬声器：
- **`sounddevice`**: 极低延迟的 CFFI 音频库，直接对接 Windows WASAPI / DirectSound。
- **Ring Buffer (环形缓冲区)**: 在 `engine.py` 内自主实现的队列缓冲，解决流式音频处理时的卡顿与撕裂问题。

## 4. 算法调度层与模型资产 (Algorithm & Assets)
负责串联上述所有组件：
- **`rvc_python`**: 对官方 RVC WebUI 进行剥离封装的推理库。包含 `vc_single` 单音频推理逻辑。
- **`hubert_base.pt`**: 公共的底层语音特征提取模型（存放于 AI 环境内）。
- **`rmvpe.pt`**: RVC 最先进的 F0 音高预测提取模型（存放于 AI 环境内）。
- **角色音色模型库**: `.pth` (发音器权重) 与 `.index` (音色特征索引)，存放在 `AppData/Roaming/CoreCommander/models`。

---

## 测试链路 (The Execution Pipeline)

1. **麦克风输入** -> `sounddevice` 以 16000Hz 捕获音频块 (Chunk)。
2. **静音检测** -> 计算均方根 (RMS)，低于阈值 (0.001) 直接返回静音块，跳过处理以节省 CPU/GPU。
3. **缓存拼接** -> 将前一块、当前块、后一块拼接，避免切片边缘的“爆音”（音频撕裂）。
4. **硬盘中转** -> 目前通过 `soundfile.write` 存入临时 WAV 文件（为了兼容 RVC ffmpeg 接口）。
5. **RVC 推理** -> `rvc_python` 读取临时 WAV -> `Hubert` 提取特征 -> `RMVPE` 提取音高 -> `.pth` 生成变换后的音频数组。
6. **重采样适配** -> PyTorch `torchaudio.functional.resample` 将生成音频 (40kHz/48kHz) 转换回输出扬声器所需频率。
7. **扬声器输出** -> `sounddevice` 播放音频块，清空临时文件。
