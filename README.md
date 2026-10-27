# 实测 TSS 波形重绘

直接读取 `.tss` 内的 WFM 采样值，以示波器风格绘制 PNG 和可编辑 SVG。全部计算在本机完成。每次输出 JSON 记录时间范围、各通道档位/位置/偏移、物理单位、采样间隔和源文件 SHA-256。

交付目录：`/Volumes/1TBWDBlue/DCAC/dspace/scope_waveform_renderer`

## 原图对比与示例

- `results/comparison.png`：左侧原示波器截图，右侧从采样值重绘，均为 5 ms/div。
- `results/window_examples.png`：同一窗口、中心向后移动 15 ms、1 ms/div 放大、20 μs/div 放大。
- `results/redraw_5ms.png` / `.svg`：单独的重绘图。
- `reference/original_scope.png`：对照用完整原始截图。
- `display_config.json`：可直接修改的通道配置。
- `verification/results.json`：图像位置核对与参数功能验证。

源文件为 `Tek-scope/clean-channel-resaved-rerun-20261012/tss/200-120-dy-keep180-power_000.tss`。对照图是同一 TSS 在此前导出的真实截图，并非源 TSS 内嵌的初始画面：后续导出调整过电流档位和各通道位置，记录见 `reference/export_plan.json`。重绘使用这些已记录的显示设置；波形不使用截图像素。

## 可调参数

| 参数 | 含义 | 示例 |
| --- | --- | --- |
| `--source` | 输入 TSS；省略时使用本次固定 180° 示例 | `/完整路径/data.tss` |
| `--center-s` | 窗口中心，相对记录第一个采样点，单位 s | `2.5006864442` |
| `--time-div-ms` | 水平时基 ms/div，整屏 10 格 | `5`、`1`、`0.02` |
| `--scale` | 各通道垂直档位，数据物理单位/div，可重复 | `CH1=2` 表示 2 A/div |
| `--offset` | 各通道物理偏移，使用数据物理单位，可重复；正值使波形下移 | `CH1=1` 表示偏移 1 A |
| `--position` | 各通道屏幕参考位置，单位 div，可重复；正值向上 | `CH1=1.96` |
| `--channels` | 显示哪些通道以及绘制次序 | `1,2,5,6,7,8` |
| `--display-config` | 从 JSON 读取通道配置；以其中通道作为显示通道 | `display_config.json` |
| `--output` / `--name` | 输出目录 / 文件名前缀 | `results/custom` / `step1` |

档位与偏移的单位来自 WFM：电流为 A，电压为 V，功率为 W。例如 `CH5=2000` 对应 2 kW/div；`CH8=0.5` 对应 500 mA/div。这些参数只改变显示映射，不改变原始测量值或原始文件。

显示纵坐标的定义为：

```text
display_div = (measured_value - offset) / scale + position
```

标签放在 `position` 指定的参考行；有物理偏移时，这一行对应的测量值为 `offset`。优先级：源 TSS 显示设置 → 默认示例的已记录导出设置（仅省略 `--source` 时）→ JSON 配置 → 命令行覆盖。

## 固定 10×10 网格与裁剪（2026-10-27）

底层 `configure_scope_axes()` 固定生成横向、纵向各 10 div 的基准窗：横向 0–10 div，纵向 −5–5 div。
`--crop-x-div LEFT RIGHT` 和 `--crop-y-div BOTTOM TOP` 只裁剪该窗口；不重新生成十格，不改变时间/幅值单位每格的比例。
`--save-full` 同时输出 `_full10x10.svg/.png`，便于检查完整八通道和裁剪前的坐标。

例如保留横向 8 div、纵向 5 div：

```sh
./render.sh --channels 1,2,3,4,5,6,7,8 \
  --crop-x-div 1 9 --crop-y-div -3 2 --save-full \
  --output results/cropped --name eight_channels
```

整屏和裁剪后的 SVG/PNG 保持相同的物理 mm/div；上述裁剪尺寸为整屏的 80%×50%。
各通道仍使用独立的 scale、offset、position；`--channels` 明确选择需要的绘制层。
`--gain CH8=10 --scale CH8=20` 可应用已确认的探头倍率修正，公式相应为
`display_div = (measured_value * gain - offset) / scale + position`；原始 WFM 样本不变。
未指定 gain 时为 1，旧配置文件和调用方式仍可用。

通道徽标使用截图参考的箭头轮廓、浅色填充和逐通道配色：C1 浅红底/红字、C2 蓝底/白字，其他通道浅色底/深色字。
文字为常规 `DejaVu Sans Mono` 窄小等宽字，不使用论文的 Times New Roman 字体或粗体。
共享参考行的标识自动上下分开并用短线连接真实参考位置；波形位置不动。
论文图调用同一组网格/徽标函数；为印刷可读性独立指定徽标的物理尺寸。
这是可编辑的矢量重绘，未声称示波器专有字体或抗锯齿逐像素相同。

`verification/results.json` 包含整屏 11×11 条主刻度线、8×5 裁剪、探头 gain、位移/档位和源文件不变检查。

## 使用方法

重新生成原图对比和四个时间窗口：

```sh
/Volumes/1TBWDBlue/DCAC/dspace/scope_waveform_renderer/render.sh --demo
```

调整默认示例的时间中心、时基、C1/C2 的档位与位置，并为 C1 设置物理偏移：

```sh
/Volumes/1TBWDBlue/DCAC/dspace/scope_waveform_renderer/render.sh \
  --center-s 2.499 --time-div-ms 1 \
  --scale CH1=1 --scale CH2=0.5 \
  --offset CH1=3 --position CH1=1.96 --position CH2=0.96 \
  --output /Volumes/1TBWDBlue/DCAC/dspace/scope_waveform_renderer/results/custom \
  --name adjusted
```

也可以修改 `display_config.json` 中的 `scale`、`offset`、`position`，通过 `--display-config` 载入；可移除不想显示的通道。自选 TSS 时默认读取它自己的显示设置，若要沿用本次布局，请显式传入配置：

```sh
/Volumes/1TBWDBlue/DCAC/dspace/scope_waveform_renderer/render.sh \
  --source /完整路径/data.tss \
  --display-config /Volumes/1TBWDBlue/DCAC/dspace/scope_waveform_renderer/display_config.json \
  --center-s 3.05 --time-div-ms 5 \
  --output /Volumes/1TBWDBlue/DCAC/dspace/scope_waveform_renderer/results/another_case \
  --name step
```

`--demo` 固定使用四个演示时间窗口；自选中心和时基请使用普通模式。相同输出目录、相同文件名的再次运行会更新生成文件，源 TSS 不会被写入。

## 验证与限制

运行 `scopeData_FFT/.venv/bin/python scope_waveform_renderer/verify_rendering.py` 可以重复检查原截图与重绘的彩色曲线中心位置，并独立验证 position +1 div、offset +1000 W、垂直档位减半的实际图像变化。

本例 125 kSa/s，采样间隔 8 μs。20 μs/div 的全屏仅覆盖 200 μs，约 25 个采样间隔：放大不能增加采集时不存在的细节。绘图使用保存的原始采样点作线性连接，保留全部点，不进行滤波、拟合或平滑。

屏幕像素绘制、线条粗细、抗锯齿和标签字体与示波器存在小差异。仅验证了本项目现有的普通单帧 WFM#003 记录；FastFrame 显式拒绝，其他仪器或格式需要先验证。图像中心位置误差仅是显示对齐指标，不代表测量误差。

`render.sh` 默认复用相邻 `scopeData_FFT/.venv`；也可设置 `SCOPE_PYTHON` 指向装有 `requirements.txt` 中依赖的 Python。TSS/WFM 读取器已随包保存在 `vendor/`，来源和哈希见 `vendor/PROVENANCE.json`。
